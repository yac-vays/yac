"""
git_direct behaviour while the remote is unavailable: the local checkout is
kept and serves reads (flagged stale), writes and checkout-less reads fail
with 503, the remote is not retried within the backoff window, and
everything recovers once the remote is back.

Reuses the bare-remote + working-clone fixture of lib_git_direct; the
remote failure itself is injected by patching `git.Repo.pull/clone/push`.
"""

import time

import pytest

import app.plugin.repo.git_direct as gd
from app.lib import git
from app.lib import staleness
from app.model.err import RepoMaintenance
from app.model.err import RepoUnavailable

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)


def _remote_down(monkeypatch, *, maintenance: bool = False) -> dict:
    """Make every network git command fail as an unreachable remote."""
    calls = {"pull": 0, "clone": 0, "push": 0}

    def failing(name):
        async def run(self, *args, **kwargs):
            del self, args, kwargs
            calls[name] += 1
            raise git.GitRemoteError(
                "Command git failed with: fatal: unable to access 'https://x/':"
                + (
                    " The requested URL returned error: 503"
                    if maintenance
                    else " Could not resolve host: git.example.com"
                ),
                returncode=128,
                maintenance=maintenance,
            )

        return run

    for name in calls:
        monkeypatch.setattr(git.Repo, name, failing(name))
    return calls


async def _names(handler) -> list[str]:
    async with handler.reader(USER) as untyped:
        return await untyped.session(DETAILS).list("host")


async def test_read_serves_last_known_state(repo, monkeypatch):
    calls = _remote_down(monkeypatch)
    with staleness.request_scope() as state:
        assert await _names(repo) == ["a", "b", "link"]
        assert state.stale is True
    assert calls["pull"] == 1
    assert calls["clone"] == 0  # the checkout was NOT thrown away
    st = repo.state()
    assert st.failed is not None
    assert "Could not resolve host" in st.error
    assert st.maintenance is False


async def test_read_outside_backoff_retries_the_remote(repo, monkeypatch):
    calls = _remote_down(monkeypatch)
    await _names(repo)
    await _names(repo)
    assert calls["pull"] == 1  # second read within the backoff: no retry
    repo._remote_failed = time.time() - gd.REMOTE_BACKOFF_SECONDS - 1
    with staleness.request_scope() as state:
        await _names(repo)
        assert state.stale is True
    assert calls["pull"] == 2


async def test_write_fails_with_503_and_keeps_checkout(repo, monkeypatch):
    _remote_down(monkeypatch)
    with pytest.raises(RepoUnavailable) as excinfo:
        async with repo.writer(USER):
            pass
    assert not isinstance(excinfo.value, RepoMaintenance)
    assert excinfo.value.code == 503
    # the git output (which may contain the remote URL) is not the message
    assert "git.example.com" not in str(excinfo.value)
    assert await _names(repo) == ["a", "b", "link"]


async def test_maintenance_503_is_reported_as_such(repo, monkeypatch):
    _remote_down(monkeypatch, maintenance=True)
    with pytest.raises(RepoMaintenance):
        async with repo.writer(USER):
            pass
    assert repo.state().maintenance is True


async def test_without_checkout_reads_fail_and_backoff_applies(repo, tmp_path, monkeypatch):
    calls = _remote_down(monkeypatch, maintenance=True)
    fresh = gd.GitRepo()
    fresh.path = str(tmp_path / "never-cloned")
    fresh._loaded = False
    with pytest.raises(RepoMaintenance):
        await _names(fresh)
    assert calls["clone"] == 1
    with pytest.raises(RepoMaintenance):
        await _names(fresh)
    assert calls["clone"] == 1  # within the backoff: failed fast, no retry
    fresh._remote_failed = time.time() - gd.REMOTE_BACKOFF_SECONDS - 1
    with pytest.raises(RepoMaintenance):
        await _names(fresh)
    assert calls["clone"] == 2


async def test_recovery_clears_the_failure_state(repo, monkeypatch):
    original_pull = git.Repo.pull
    _remote_down(monkeypatch)
    await _names(repo)
    assert repo.state().failed is not None

    monkeypatch.setattr(git.Repo, "pull", original_pull)
    repo._remote_failed = time.time() - gd.REMOTE_BACKOFF_SECONDS - 1
    with staleness.request_scope() as state:
        assert await _names(repo) == ["a", "b", "link"]
        assert state.stale is False
    st = repo.state()
    assert st.failed is None and st.error is None
    assert st.synced is not None


async def test_push_failure_is_503_and_leaves_a_clean_tree(repo, monkeypatch):
    original = {name: getattr(git.Repo, name) for name in ("pull", "clone")}
    _remote_down(monkeypatch)
    # the pull before the write must succeed, only the push hits the outage
    for name, method in original.items():
        monkeypatch.setattr(git.Repo, name, method)
    with pytest.raises(RepoUnavailable):
        async with repo.writer(USER) as untyped:
            s = untyped.session(DETAILS)
            await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", "bump")
    gr = gd._make_git_repo(repo.path, USER)
    assert await gr.is_dirty() is False
    async with repo.reader(USER, dirty=True) as untyped:
        assert await untyped.session(DETAILS).get("host", "a") == "cpu: 4\n"
