"""
Tests for the `git_redis` repo plugin: which snapshot gets published when
pods race (rollback guard, compare-and-set on `latest`), how replaced and
abandoned snapshots expire, which snapshot a read is served from while
another pod refreshes or the remote is down, and the read-only session.

Redis and git are faked: an in-memory client stands in for Redis (see
`FakeRedis`) and the HEAD read / tree scan / ancestry check are
monkeypatched, so no network, git repo or Redis server is required.
"""

from contextlib import asynccontextmanager
import asyncio
import time

import pytest
from redis.exceptions import LockNotOwnedError, ResponseError, WatchError

import app.plugin.repo.git_redis as grs
from app.lib import staleness
from app.model.err import RepoError, RepoSpecsError, RepoUnavailable
from app.model.plg import RepoState


# Commit hashes (`latest` is only accepted if it looks like one).
H0 = "0" * 40
H1 = "1" * 40
OLD = "a" * 40
GONE = "d" * 40


#
# Fakes
#


class FakePipeline:
    """Queues commands; with a transaction, EXEC fails if a watched key changed."""

    def __init__(self, fake: "FakeRedis", transaction: bool) -> None:
        self._fake = fake
        self._transaction = transaction
        self._ops = []
        self._watched = None
        self._queueing = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._watched = None

    async def watch(self, *keys):
        self._watched = {k: self._fake.versions.get(k, 0) for k in keys}
        self._queueing = False

    def multi(self):
        self._queueing = True

    def __getattr__(self, name):
        command = getattr(self._fake, name)

        def queue(*args, **kwargs):
            if not self._queueing:
                return command(*args, **kwargs)  # immediate, awaited by caller
            self._ops.append((command, args, kwargs))
            return self

        return queue

    async def execute(self):
        if self._fake.before_exec is not None:
            hook, self._fake.before_exec = self._fake.before_exec, None
            await hook()
        if self._watched is not None:
            for key, version in self._watched.items():
                if self._fake.versions.get(key, 0) != version:
                    self._ops = []
                    raise WatchError("Watched variable changed.")
        results = [await command(*a, **kw) for command, a, kw in self._ops]
        self._ops = []
        return results


class FakeLock:
    def __init__(self, fake: "FakeRedis", name: str) -> None:
        self._fake = fake
        self._name = name
        self._token = object()

    async def acquire(self):
        if self._name in self._fake.locks:
            return False
        self._fake.locks[self._name] = self._token
        return True

    async def release(self):
        if self._fake.locks.get(self._name) is not self._token:
            raise LockNotOwnedError("Cannot release a lock that's no longer owned")
        del self._fake.locks[self._name]


class FakeRedis:
    """The subset of redis.asyncio the plugin uses (strings and hashes)."""

    def __init__(self, store=None, snapshots=None) -> None:
        self.store = dict(store or {})
        self.hashes = {k: dict(v) for k, v in (snapshots or {}).items()}
        self.ttl = {}
        self.versions = {}
        self.locks = {}
        self.set_keys = []  # every SET, direct or via pipeline
        self.before_exec = None  # async hook, run once before the next EXEC

    def _touch(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value
        self.set_keys.append(key)
        self._touch(key)
        return True

    async def exists(self, key):
        return int(key in self.store or key in self.hashes)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.hashes.pop(key, None)
            self.ttl.pop(key, None)
            self._touch(key)

    async def hset(self, key, field=None, value=None, mapping=None):
        target = self.hashes.setdefault(key, {})
        if field is not None:
            target[field] = value
        target.update(mapping or {})

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hmget(self, key, fields):
        return [self.hashes.get(key, {}).get(f) for f in fields]

    async def hkeys(self, key):
        return list(self.hashes.get(key, {}))

    async def hexists(self, key, field):
        return field in self.hashes.get(key, {})

    async def expire(self, key, seconds):
        if key in self.hashes or key in self.store:
            self.ttl[key] = seconds

    async def persist(self, key):
        self.ttl.pop(key, None)

    async def rename(self, src, dst):
        if src not in self.hashes:
            raise ResponseError("no such key")
        self.hashes[dst] = self.hashes.pop(src)
        self.ttl.pop(dst, None)
        if src in self.ttl:
            self.ttl[dst] = self.ttl.pop(src)

    def pipeline(self, transaction=True):
        return FakePipeline(self, transaction)

    def lock(self, name, timeout=None, blocking=True):
        del timeout, blocking
        return FakeLock(self, name)


class FakeGitDirect:
    """Stands in for the git_direct handler: a checkout whose HEAD is `head`."""

    path = "/nonexistent-test-repo"

    def __init__(self, head=H0) -> None:
        self.head = head
        self.pulls = 0
        self.after_pull = None  # HEAD after the next sync
        self.own_state = RepoState()

    def state(self):
        return self.own_state

    async def _pull(self, user):
        del user
        self.pulls += 1
        if self.after_pull is not None:
            self.head = self.after_pull

    @asynccontextmanager
    async def writer(self, user):
        await self._pull(user)
        yield None


SNAPSHOT = {"": "", "hosts/a.yml": "f:cpu: 4\n"}


@pytest.fixture
def gd(monkeypatch):
    fake = FakeGitDirect()
    monkeypatch.setattr(grs, "_git_direct", fake)

    async def fake_head(repo_path):
        del repo_path
        return fake.head

    async def fake_entries(repo_path):
        del repo_path
        return [("hosts/a.yml", "f:cpu: 4\n")]

    monkeypatch.setattr(grs.GitRedisRepo, "_read_git_head", staticmethod(fake_head))
    monkeypatch.setattr(
        grs.GitRedisRepo, "_collect_entries", staticmethod(fake_entries)
    )
    return fake


@pytest.fixture
def handler(gd):
    del gd
    return grs.GitRedisRepo()


def _set_relation(monkeypatch, answer: str) -> list:
    """Pin how the published snapshot relates to the local HEAD."""
    calls = []

    async def fake_compare(repo_path, local, published):
        calls.append((repo_path, local, published))
        return answer

    monkeypatch.setattr(grs.GitRedisRepo, "_compare", staticmethod(fake_compare))
    return calls


def _tmp_keys(client) -> list[str]:
    return [k for k in client.hashes if ":tmp:" in k]


#
# Publishing
#


async def test_newer_published_snapshot_is_kept(handler, gd, monkeypatch):
    """This pod lags behind: the sync brings it to the published commit."""
    calls = _set_relation(monkeypatch, "newer")
    gd.after_pull = H1
    client = FakeRedis(store={"latest": H1}, snapshots={f"snap:{H1}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H1
    assert gd.pulls == 1
    assert client.store["latest"] == H1
    assert client.set_keys == ["synced"]
    assert client.ttl == {}
    assert calls == [(FakeGitDirect.path, H0, H1)]


async def test_newer_published_snapshot_of_a_rewound_remote_is_replaced(
    handler, gd, monkeypatch
):
    """
    The remote was force-pushed back from H1 to H0, but H1 still is in this
    pod's clone ("newer"): after the sync the local HEAD is what the remote
    has, so H1 must not be served any longer.
    """
    _set_relation(monkeypatch, "newer")
    client = FakeRedis(store={"latest": H1}, snapshots={f"snap:{H1}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H0
    assert gd.pulls == 1
    assert client.store["latest"] == H0
    assert client.ttl == {f"snap:{H1}": grs.GRACE}


async def test_older_published_snapshot_is_replaced_and_expires(handler, monkeypatch):
    _set_relation(monkeypatch, "other")
    client = FakeRedis(store={"latest": OLD}, snapshots={f"snap:{OLD}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H0
    assert client.store["latest"] == H0
    assert client.hashes[f"snap:{H0}"] == SNAPSHOT
    assert client.ttl == {f"snap:{OLD}": grs.GRACE}  # the new one does not expire
    assert _tmp_keys(client) == []
    assert handler._synced is not None


async def test_latest_without_its_snapshot_is_replaced(handler, monkeypatch):
    calls = _set_relation(monkeypatch, "newer")
    client = FakeRedis(store={"latest": H1})  # snap:H1 expired / never written

    assert await handler._publish_snapshot(client, None) == H0
    assert client.store["latest"] == H0
    assert calls == []


async def test_unchanged_head_only_bumps_synced(handler, monkeypatch):
    calls = _set_relation(monkeypatch, "newer")
    client = FakeRedis(store={"latest": H0}, snapshots={f"snap:{H0}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H0
    assert calls == []
    assert client.set_keys == ["synced"]


async def test_unknown_published_commit_triggers_a_sync_first(handler, gd, monkeypatch):
    """Another pod pushed and published H1 after this pod fetched H0."""
    _set_relation(monkeypatch, "unknown")
    gd.after_pull = H1
    client = FakeRedis(store={"latest": H1}, snapshots={f"snap:{H1}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H1
    assert gd.pulls == 1
    assert client.store["latest"] == H1  # not rolled back to H0
    assert client.ttl == {}


async def test_published_commit_unknown_to_the_remote_is_replaced(handler, gd, monkeypatch):
    """Still unknown after the sync (e.g. the branch was rewound): publish."""
    _set_relation(monkeypatch, "unknown")
    client = FakeRedis(store={"latest": GONE}, snapshots={f"snap:{GONE}": SNAPSHOT})

    assert await handler._publish_snapshot(client, None) == H0
    assert gd.pulls == 1
    assert client.store["latest"] == H0


async def test_publish_gives_way_to_a_concurrent_publish(handler, gd, monkeypatch):
    """`latest` moves to a newer snapshot while this pod uploads its own."""
    relations = iter(["other", "newer"])
    gd.after_pull = H1  # what the sync for the "newer" one brings

    async def fake_compare(repo_path, local, published):
        del repo_path, local, published
        return next(relations)

    monkeypatch.setattr(grs.GitRedisRepo, "_compare", staticmethod(fake_compare))
    client = FakeRedis(store={"latest": OLD}, snapshots={f"snap:{OLD}": SNAPSHOT})

    async def other_pod_publishes():
        await client.hset(f"snap:{H1}", mapping=SNAPSHOT)
        await client.set("latest", H1)

    client.before_exec = None

    original_pipeline = client.pipeline

    def pipeline(transaction=True):
        if transaction:
            client.before_exec = other_pod_publishes
        return original_pipeline(transaction)

    client.pipeline = pipeline

    assert await handler._publish_snapshot(client, None) == H1
    assert client.store["latest"] == H1
    assert f"snap:{H0}" not in client.hashes
    assert _tmp_keys(client) == []


#
# Which snapshot a read uses
#


async def test_fresh_snapshot_is_used_without_refresh(handler, gd):
    client = FakeRedis(
        store={"latest": H1, "synced": str(time.time())},
        snapshots={f"snap:{H1}": SNAPSHOT},
    )
    assert await handler._ensure_snapshot(None, client, dirty=False) == H1
    assert gd.pulls == 0


async def test_outdated_snapshot_is_refreshed(handler, gd):
    client = FakeRedis(
        store={"latest": H0, "synced": "100.0"}, snapshots={f"snap:{H0}": SNAPSHOT}
    )
    assert await handler._ensure_snapshot(None, client, dirty=False) == H0
    assert gd.pulls == 1
    assert float(client.store["synced"]) > 100.0
    assert client.locks == {}


async def test_outdated_snapshot_is_served_while_another_pod_refreshes(handler, gd):
    client = FakeRedis(
        store={"latest": H1, "synced": "100.0"}, snapshots={f"snap:{H1}": SNAPSHOT}
    )
    client.locks["pull_lock"] = object()  # held elsewhere
    with staleness.request_scope() as state:
        assert await handler._ensure_snapshot(None, client, dirty=False) == H1
        assert state.stale is False
    assert gd.pulls == 0
    assert "pull_lock" in client.locks  # the other pod's lock is left alone


async def test_first_snapshot_is_awaited(handler, gd):
    client = FakeRedis()
    client.locks["pull_lock"] = object()

    async def other_pod():
        await asyncio.sleep(0.3)
        await client.hset(f"snap:{H1}", mapping=SNAPSHOT)
        await client.set("latest", H1)
        await client.set("synced", str(time.time()))

    task = asyncio.create_task(other_pod())
    assert await handler._ensure_snapshot(None, client, dirty=False) == H1
    await task
    assert gd.pulls == 0


async def test_dirty_read_accepts_git_directs_dirty_max_age(handler, gd, monkeypatch):
    class Module:
        DIRTY_MAX = 60  # minutes

    monkeypatch.setattr(grs, "_gd_module", lambda: Module)
    age = grs.MAX_AGE + 60
    client = FakeRedis(
        store={"latest": H0, "synced": str(time.time() - age)},
        snapshots={f"snap:{H0}": SNAPSHOT},
    )
    assert await handler._ensure_snapshot(None, client, dirty=True) == H0
    assert gd.pulls == 0
    assert await handler._ensure_snapshot(None, client, dirty=False) == H0
    assert gd.pulls == 1


async def test_dirty_read_refreshes_a_snapshot_that_is_too_old(handler, gd, monkeypatch):
    class Module:
        DIRTY_MAX = 0

    monkeypatch.setattr(grs, "_gd_module", lambda: Module)
    client = FakeRedis(
        store={"latest": H0, "synced": "100.0"}, snapshots={f"snap:{H0}": SNAPSHOT}
    )
    await handler._ensure_snapshot(None, client, dirty=True)
    assert gd.pulls == 1


#
# Remote outage: serve the published snapshot as stale
#


class DownGitDirect(FakeGitDirect):
    @asynccontextmanager
    async def writer(self, user):
        del user
        raise RepoUnavailable("down")
        yield  # pylint: disable=unreachable


async def test_refresh_failure_serves_stale_snapshot(handler, monkeypatch):
    monkeypatch.setattr(grs, "_git_direct", DownGitDirect())
    client = FakeRedis(
        store={"latest": H1, "synced": "100.0"}, snapshots={f"snap:{H1}": SNAPSHOT}
    )

    with staleness.request_scope() as state:
        result = await handler._ensure_snapshot(None, client, dirty=False)
        assert state.stale is True
        assert state.synced == 100.0
    assert result == H1
    assert client.locks == {}  # released for the next attempt


async def test_refresh_failure_without_snapshot_raises(handler, monkeypatch):
    monkeypatch.setattr(grs, "_git_direct", DownGitDirect())
    client = FakeRedis()

    with pytest.raises(RepoUnavailable):
        await handler._ensure_snapshot(None, client, dirty=False)
    assert client.locks == {}


#
# State, config, session
#


async def test_state_reports_the_shared_sync_time(handler, gd):
    client = FakeRedis(
        store={"latest": H1, "synced": str(time.time())},
        snapshots={f"snap:{H1}": SNAPSHOT},
    )
    # this pod's own last attempt failed long ago, another pod synced since
    gd.own_state = RepoState(synced=50.0, error="boom", failed=60.0)
    await handler._ensure_snapshot(None, client, dirty=False)
    state = handler.state()
    assert state.synced == float(client.store["synced"])
    assert state.failed is None and state.error is None

    gd.own_state = RepoState(synced=50.0, error="boom", failed=time.time() + 5)
    state = handler.state()
    assert state.failed is not None and state.error == "boom"


def test_redis_url_scheme_is_checked():
    for url in ("", "redis://h:6379/0", "rediss://h", "unix:///run/redis.sock"):
        grs._check_url(url)
    with pytest.raises(RepoSpecsError):
        grs._check_url("sentinel://h:26379/mymaster")


async def test_session_reads_from_the_pinned_snapshot():
    details = {"host": "hosts/{{ name }}.yml"}
    client = FakeRedis(
        snapshots={
            "snap:H": {
                "": "",
                "hosts/a.yml": "f:x: 1\n",
                "hosts/b.yml": "f:x: 2\n",
                "hosts/link.yml": "l:hosts/a.yml",
                "hosts/dangling.yml": "l:hosts/gone.yml",
                "README.md": "f:hi",
            }
        }
    )
    s = grs._GitRedisSession(None, client, "H", details)
    assert await s.get_hash() == "H"
    assert await s.list("host") == ["a", "b", "dangling", "link"]
    assert await s.exists("host", "a") is True
    assert await s.exists("host", "zzz") is False
    assert await s.get("host", "a") == "x: 1\n"
    assert await s.is_link("host", "link") is True
    assert await s.is_link("host", "a") is False
    assert await s.get_link("host", "link") == "a"
    assert await s.get("host", "link") == "x: 1\n"
    assert await s.get_resolved("host", "link") == ("x: 1\n", "a")
    assert await s.get_resolved("host", "b") == ("x: 2\n", None)
    assert await s.is_link_target("host", "a") is True
    assert await s.is_link_target("host", "b") is False
    with pytest.raises(RepoError):
        await s.get("host", "dangling")
    with pytest.raises(RepoError):
        await s.get("host", "zzz")


async def test_session_reads_a_whole_type_in_bulk(monkeypatch):
    details = {"host": "hosts/{{ name }}.yml"}
    client = FakeRedis(
        snapshots={
            "snap:H": {
                "": "",
                "hosts/a.yml": "f:x: 1\n",
                "hosts/b.yml": "f:x: 2\n",
                "hosts/link.yml": "l:hosts/a.yml",
                "hosts/link2.yml": "l:hosts/a.yml",
                "hosts/dangling.yml": "l:hosts/gone.yml",
                "hosts/readme.yml": "l:README.md",
                "hosts/sub/c.yml": "f:x: 3\n",
                "README.md": "f:hi",
            }
        }
    )
    calls = []
    for command in ("hkeys", "hmget", "hget"):
        real = getattr(client, command)

        async def counting(*args, _command=command, _real=real):
            calls.append(_command)
            return await _real(*args)

        monkeypatch.setattr(client, command, counting)

    s = grs._GitRedisSession(None, client, "H", details)
    stored = await s.get_all("host")
    # one round trip each for the entities and the targets of the links
    assert calls == ["hkeys", "hmget", "hmget"]
    assert stored == {
        "a": ("x: 1\n", None),
        "b": ("x: 2\n", None),
        "link": ("x: 1\n", "a"),
        "link2": ("x: 1\n", "a"),
    }
    for name, entity in stored.items():
        assert entity == await s.get_resolved("host", name)

    # a type without entities
    assert await grs._GitRedisSession(
        None, client, "H", {"host": "vms/{{ name }}.yml"}
    ).get_all("host") == {}


async def test_bulk_read_of_an_expired_snapshot_fails():
    details = {"host": "hosts/{{ name }}.yml"}
    with pytest.raises(RepoError):
        await grs._GitRedisSession(None, FakeRedis(), "H", details).get_all("host")

    # expired between listing the entities and reading them
    client = FakeRedis(snapshots={"snap:H": {"": "", "hosts/a.yml": "f:x: 1\n"}})
    hkeys = client.hkeys

    async def expiring(key):
        paths = await hkeys(key)
        await client.delete(key)
        return paths

    client.hkeys = expiring
    with pytest.raises(RepoError):
        await grs._GitRedisSession(None, client, "H", details).get_all("host")


async def test_bulk_read_is_split_into_batches(monkeypatch):
    monkeypatch.setattr(grs, "_READ_BATCH", 2)
    files = {f"hosts/h{i}.yml": f"f:x: {i}\n" for i in range(5)}
    client = FakeRedis(snapshots={"snap:H": {"": "", **files}})
    sizes = []
    hmget = client.hmget

    async def counting(key, fields):
        sizes.append(len(fields) - 1)  # less the marker
        return await hmget(key, fields)

    client.hmget = counting
    s = grs._GitRedisSession(None, client, "H", {"host": "hosts/{{ name }}.yml"})
    assert await s.get_all("host") == {f"h{i}": (f"x: {i}\n", None) for i in range(5)}
    assert sizes == [2, 2, 1]
