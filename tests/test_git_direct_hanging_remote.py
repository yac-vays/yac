"""
git_direct with a remote that does not answer: the readers that queued for
the lock while the first one waited for its fetch to time out must not each
run (and wait for) a fetch of their own, one after the other. They get the
last known state as soon as the failure is known.
"""

import asyncio

from app.lib import git
from app.lib import staleness

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)

HANG = 0.2  # stands in for git.NETWORK_TIMEOUT


def _hanging_remote(monkeypatch) -> dict:
    calls = {"fetch": 0}

    async def fetch(self, *args, **kwargs):
        del self, args, kwargs
        calls["fetch"] += 1
        await asyncio.sleep(HANG)
        raise git.GitTimeoutError("Timeout of 30 seconds exceeded")

    monkeypatch.setattr(git.Repo, "fetch", fetch)
    return calls


async def _read(handler) -> tuple[list[str], bool]:
    with staleness.request_scope() as state:
        async with handler.reader(USER) as untyped:
            return await untyped.session(DETAILS).list("host"), state.stale


async def test_queued_readers_share_the_failed_sync(repo, monkeypatch):  # noqa: F811
    calls = _hanging_remote(monkeypatch)

    loop = asyncio.get_running_loop()
    start = loop.time()
    results = await asyncio.gather(*(_read(repo) for _ in range(6)))

    assert calls["fetch"] == 1
    assert loop.time() - start < 3 * HANG
    assert results == [(["a", "b", "link"], True)] * 6


async def test_reader_queued_before_the_backoff_ended_does_not_fetch(
    repo, monkeypatch  # noqa: F811
):
    """Also when it only gets the lock after the backoff window."""
    import app.plugin.repo.git_direct as gd

    calls = _hanging_remote(monkeypatch)
    monkeypatch.setattr(gd, "REMOTE_BACKOFF_SECONDS", 0)

    results = await asyncio.gather(*(_read(repo) for _ in range(3)))

    assert calls["fetch"] == 1
    assert results == [(["a", "b", "link"], True)] * 3


async def test_writer_still_tries_the_remote(repo, monkeypatch):  # noqa: F811
    import pytest
    from app.model.err import RepoUnavailable

    calls = _hanging_remote(monkeypatch)
    await _read(repo)
    with pytest.raises(RepoUnavailable):
        async with repo.writer(USER):
            pass
    assert calls["fetch"] == 2
