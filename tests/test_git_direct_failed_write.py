"""
git_direct: a write that fails after it has touched the working tree for a
reason git is not involved in (disk full, cancelled request) must leave the
tree as the commit it is at. Dirty readers do not sync before they read, and
the entity cache is keyed by the HEAD hash: both would serve the damage.
"""

import asyncio
import errno
import os

import pytest

import app.plugin.repo.git_direct as gd
from app.lib import git
from app.lib import repo as repo_lib
from app.model.err import RepoError
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)


@pytest.fixture
def dirty(repo, monkeypatch):  # noqa: F811
    """Dirty reads that do not sync (and so would not heal anything)."""
    monkeypatch.setattr(gd, "DIRTY_MAX", 1)

    async def no_sync(user):
        raise AssertionError("dirty read synced")

    async def read(name: str) -> str | None:
        with monkeypatch.context() as patch:
            patch.setattr(repo, "_refresh_for_read", no_sync)
            async with repo.reader(USER, dirty=True) as untyped:
                s = untyped.session(DETAILS)
                if not await s.exists("host", name):
                    return None
                return await s.get("host", name)

    return read


def _disk_full(patch):
    real_fdopen = os.fdopen

    class Full:
        def __init__(self, file):
            self._file = file

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._file.close()

        def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

    # the file is truncated by then
    patch.setattr(gd.os, "fdopen", lambda fd, *a, **kw: Full(real_fdopen(fd, *a, **kw)))


async def test_failed_file_write_does_not_leave_a_truncated_file(
    repo, dirty, monkeypatch  # noqa: F811
):
    async with repo.reader(USER):  # FETCH_HEAD is fresh
        pass
    head = await repo._get_hash()

    with monkeypatch.context() as patch, pytest.raises(RepoError):
        _disk_full(patch)
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write(
                "host", "a", "cpu: 4\n", "cpu: 16\n", CommitMsg()
            )

    assert await dirty("a") == "cpu: 4\n"
    assert await repo._get_hash() == head
    assert repo._loaded is True

    # ... and so does the entity cache for that commit
    lookup = getattr(repo_lib, "__lookup_entities")
    async with repo.reader(USER, dirty=True) as untyped:
        old, _ = await lookup(head, "host", "a", None, True, untyped.session(DETAILS))
    assert old.yaml == "cpu: 4\n"


async def test_failed_rename_does_not_leave_the_new_file(
    repo, dirty, monkeypatch  # noqa: F811
):
    async with repo.reader(USER):
        pass

    real_unlink = gd.Path.unlink

    async def unlink(self, *args, **kwargs):
        if str(self).endswith("/hosts/a.yml"):
            raise OSError(errno.EIO, "Input/output error")
        await real_unlink(self, *args, **kwargs)

    with monkeypatch.context() as patch, pytest.raises(RepoError):
        patch.setattr(gd.Path, "unlink", unlink)
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write_rename(
                "host", "a", "z", "cpu: 4\n", "cpu: 5\n", CommitMsg()
            )

    assert await dirty("z") is None
    assert await dirty("a") == "cpu: 4\n"


async def test_cancelled_write_is_rolled_back_before_the_lock_is_released(
    repo, dirty, monkeypatch  # noqa: F811
):
    async with repo.reader(USER):
        pass
    head = await repo._get_hash()
    pushing = asyncio.Event()

    async def push(self, *args, **kwargs):
        pushing.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(git.Repo, "push", push)

    async def write():
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write(
                "host", "a", "cpu: 4\n", "cpu: 16\n", CommitMsg()
            )

    task = asyncio.create_task(write())
    await pushing.wait()  # file written and committed locally
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The cleanup was complete when the writer scope ended.
    assert repo._tree_touched is False
    assert await dirty("a") == "cpu: 4\n"
    assert await repo._get_hash() == head  # the unpushed commit is gone, too


async def test_cancellation_during_the_cleanup_does_not_cut_it_short(
    repo, monkeypatch  # noqa: F811
):
    cleaning = asyncio.Event()
    done = []
    cleanup = repo._cleanup

    async def slow_cleanup(user):
        cleaning.set()
        await asyncio.sleep(0.1)
        await cleanup(user)
        done.append(1)

    monkeypatch.setattr(repo, "_cleanup", slow_cleanup)
    _disk_full(monkeypatch)

    async def write():
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write(
                "host", "a", "cpu: 4\n", "cpu: 16\n", CommitMsg()
            )

    task = asyncio.create_task(write())
    await cleaning.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done == [1]
    assert repo._reader_count == 0
    with open(f"{repo.path}/hosts/a.yml", encoding="utf-8") as file:
        assert file.read() == "cpu: 4\n"


async def test_rejected_write_cleans_up_only_once(repo, monkeypatch):  # noqa: F811
    """Git failures are cleaned up where they happen (as before)."""
    calls = []
    cleanup = repo._cleanup

    async def counting_cleanup(user):
        calls.append(1)
        await cleanup(user)

    async def push(self, *args, **kwargs):
        raise git.GitError("! [rejected] main -> main (fetch first)", returncode=1)

    monkeypatch.setattr(repo, "_cleanup", counting_cleanup)
    monkeypatch.setattr(git.Repo, "push", push)
    with pytest.raises(RepoError):
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write(
                "host", "a", "cpu: 4\n", "cpu: 16\n", CommitMsg()
            )
    assert calls == [1]


async def test_conflict_before_any_change_needs_no_cleanup(repo, monkeypatch):  # noqa: F811
    async def no_cleanup(user):
        raise AssertionError("cleanup without a change")

    monkeypatch.setattr(repo, "_cleanup", no_cleanup)
    with pytest.raises(RepoError):
        async with repo.writer(USER) as untyped:
            await untyped.session(DETAILS).write(
                "host", "a", "STALE", "cpu: 16\n", CommitMsg()
            )
