"""
Tests for the `git_redis` repo plugin's `_rebuild_snapshot` rollback guard.

Two pods race: pod A pushes and publishes snapshot H1 while pod B is still
rebuilding from the slightly older HEAD H0. Without the guard, B would SET
`latest` back to H0 (rolling back A's publish) and schedule the H1 snapshot
keys for deletion. The guard must keep the newer published snapshot when the
local HEAD is its ancestor, and publish as before in every other case.

Redis and git are faked: a minimal in-memory client stands in for Redis and
the ancestry check / HEAD read / tree scan are monkeypatched, so no network,
git repo, or Redis server is required.
"""

import asyncio

import pytest

import app.plugin.repo.git_redis as grs
from app.lib import git


#
# Fakes
#


class FakePipeline:
    """Records commands and applies them to the FakeRedis on execute()."""

    def __init__(self, fake: "FakeRedis") -> None:
        self._fake = fake
        self._ops = []

    def delete(self, *keys):
        self._ops.append(("delete", keys))

    def set(self, key, value):
        self._ops.append(("set", (key, value)))

    def sadd(self, key, *values):
        self._ops.append(("sadd", (key, values)))

    async def execute(self):
        for op, args in self._ops:
            if op == "delete":
                for key in args:
                    self._fake.store.pop(key, None)
                    self._fake.sets.pop(key, None)
            elif op == "set":
                self._fake.store[args[0]] = args[1]
                self._fake.set_calls.append(args)
            elif op == "sadd":
                self._fake.sets.setdefault(args[0], set()).update(args[1])
        self._ops = []


class FakeRedis:
    """The minimal subset of redis.asyncio used by `_rebuild_snapshot`."""

    def __init__(self, store=None) -> None:
        self.store = dict(store or {})
        self.sets = {}
        self.set_calls = []  # every SET, direct or via pipeline

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.set_calls.append((key, value))
        return True

    def pipeline(self, transaction=False):
        del transaction
        return FakePipeline(self)


class FakeGitDirect:
    path = "/nonexistent-test-repo"


#
# Fixture: a handler whose git plumbing is fully faked out
#


@pytest.fixture
def handler(monkeypatch):
    """
    GitRedisRepo with `_gd()` pointing at a dummy path, HEAD pinned to "H0"
    and a one-file tree. The ancestry answer is injected per test via
    `_set_ancestry` (or left real for the git-failure fallback test).
    """
    monkeypatch.setattr(grs, "_git_direct", FakeGitDirect())

    async def fake_head(repo_path):
        del repo_path
        return "H0"

    async def fake_entries(repo_path):
        del repo_path
        return [("hosts/a.yml", "f:cpu: 4\n")]

    monkeypatch.setattr(grs.GitRedisRepo, "_read_git_head", staticmethod(fake_head))
    monkeypatch.setattr(
        grs.GitRedisRepo, "_collect_entries", staticmethod(fake_entries)
    )
    return grs.GitRedisRepo()


def _set_ancestry(monkeypatch, answer: bool) -> list:
    """Pin the (git-backed) ancestry check to a fixed answer; returns the
    recorded calls so tests can assert whether/how it was consulted."""
    calls = []

    async def fake_check(repo_path, new_hash, old_hash):
        calls.append((repo_path, new_hash, old_hash))
        return answer

    monkeypatch.setattr(
        grs.GitRedisRepo, "_published_is_newer", staticmethod(fake_check)
    )
    return calls


async def _drain_cleanups(handler) -> None:
    """Cancel the grace-period cleanup tasks so nothing outlives the test."""
    tasks = list(handler._cleanups)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


#
# Guard behavior
#


async def test_newer_published_snapshot_is_kept(handler, monkeypatch):
    """(a) `latest` (H1) is strictly newer than the local HEAD (H0): do not
    publish, do not schedule any cleanup, return the existing snapshot."""
    calls = _set_ancestry(monkeypatch, True)
    client = FakeRedis(store={"latest": "H1", "ready:H1": "1"})

    result = await handler._rebuild_snapshot(client)

    assert result == "H1"
    assert client.store["latest"] == "H1"  # not rolled back to H0
    assert client.set_calls == []  # no SET at all (latest/ready/synced/data)
    assert handler._cleanups == set()  # H1's keys NOT scheduled for deletion
    assert calls == [(FakeGitDirect.path, "H0", "H1")]


async def test_older_published_snapshot_is_replaced(handler, monkeypatch):
    """(b) the local HEAD (H0) is the newer commit: publish as before and
    schedule the grace-period cleanup of the replaced snapshot."""
    _set_ancestry(monkeypatch, False)
    client = FakeRedis(store={"latest": "OLD", "ready:OLD": "1"})

    result = await handler._rebuild_snapshot(client)

    assert result == "H0"
    assert client.store["latest"] == "H0"
    assert client.store["ready:H0"] == "1"
    assert client.store["data:H0:hosts/a.yml"] == "f:cpu: 4\n"
    assert client.sets["paths:H0"] == {"hosts/a.yml"}
    assert len(handler._cleanups) == 1  # OLD scheduled for cleanup
    await _drain_cleanups(handler)


async def test_partial_published_snapshot_is_replaced(handler, monkeypatch):
    """A `latest` without its `ready` key is a partial/failed snapshot: the
    guard must not keep it (the ancestry check is not even consulted)."""
    calls = _set_ancestry(monkeypatch, True)
    client = FakeRedis(store={"latest": "H1"})  # no ready:H1

    result = await handler._rebuild_snapshot(client)

    assert result == "H0"
    assert client.store["latest"] == "H0"
    assert calls == []
    await _drain_cleanups(handler)


async def test_unchanged_head_fast_path_untouched(handler, monkeypatch):
    """HEAD unchanged + snapshot ready: only `synced` is bumped (fast path
    from before the guard, must still short-circuit first)."""
    calls = _set_ancestry(monkeypatch, True)
    client = FakeRedis(store={"latest": "H0", "ready:H0": "1"})

    result = await handler._rebuild_snapshot(client)

    assert result == "H0"
    assert calls == []
    assert [key for key, _ in client.set_calls] == ["synced"]


async def test_undecidable_ancestry_falls_back_to_publish(handler, monkeypatch):
    """The published hash is unknown to the local clone (git errors out):
    the real `_published_is_newer` logs a warning and returns False, so the
    rebuild publishes the local HEAD as before (a wrong-direction publish is
    corrected by the next refresh)."""

    class FailingRepo:
        def __init__(self, path, env):
            del path, env

        async def is_ancestor(self, ancestor, descendant):
            del ancestor, descendant
            raise git.GitError(
                "fatal: Not a valid commit name H1", returncode=128
            )

    monkeypatch.setattr(git, "Repo", FailingRepo)
    client = FakeRedis(store={"latest": "H1", "ready:H1": "1"})

    result = await handler._rebuild_snapshot(client)

    assert result == "H0"  # published despite the newer-looking `latest`
    assert client.store["latest"] == "H0"
    await _drain_cleanups(handler)
