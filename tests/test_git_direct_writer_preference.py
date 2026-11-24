"""
git_direct: dirty readers (`/validate`, `/logs`) do not queue for the writer
lock. Overlapping ones kept the reader count above zero for as long as they
came, and with it a writer (and every synced read behind it) waiting. Now no
new reader is let in while a writer waits for the readers to leave.
"""

import asyncio

import pytest

import app.plugin.repo.git_direct as gd

from tests.lib_git_direct import USER, repo  # noqa: F401  (fixture)


@pytest.fixture
def handler(repo, monkeypatch):  # noqa: F811
    """Dirty reads and writes without any git work."""
    monkeypatch.setattr(gd, "DIRTY_MAX", 1)

    async def not_outdated():
        return False

    async def no_pull(user):
        del user

    monkeypatch.setattr(repo, "_is_outdated", not_outdated)
    monkeypatch.setattr(repo, "_pull", no_pull)
    return repo


async def test_overlapping_dirty_readers_do_not_starve_a_writer(handler):
    events = []
    stop = asyncio.Event()

    async def read(i):
        async with handler.reader(USER, dirty=True):
            events.append(f"r{i}")
            await asyncio.sleep(0.02)

    async def readers():
        i = 0
        tasks = []
        while not stop.is_set():  # always at least one reader in its scope
            tasks.append(asyncio.create_task(read(i)))
            i += 1
            await asyncio.sleep(0.01)
        await asyncio.gather(*tasks)

    async def write():
        async with handler.writer(USER):
            events.append("w")

    stream = asyncio.create_task(readers())
    await asyncio.sleep(0.03)
    try:
        await asyncio.wait_for(write(), timeout=2)
    finally:
        stop.set()
        await stream

    assert "w" in events
    # readers that came while the writer waited were let in after it
    assert events.index("w") < len(events) - 1


async def test_readers_wait_for_a_pending_writer(handler):
    order = []
    first_in = asyncio.Event()
    release = asyncio.Event()

    async def first_reader():
        async with handler.reader(USER, dirty=True):
            first_in.set()
            await release.wait()
            order.append("r1")

    async def write():
        async with handler.writer(USER):
            order.append("w")

    async def late_reader():
        async with handler.reader(USER, dirty=True):
            order.append("r2")

    r1 = asyncio.create_task(first_reader())
    await first_in.wait()
    w = asyncio.create_task(write())
    await asyncio.sleep(0.01)
    assert handler._writer_waiting is True
    r2 = asyncio.create_task(late_reader())
    await asyncio.sleep(0.01)
    assert order == []  # r2 does not slip in beside r1

    release.set()
    await asyncio.gather(r1, w, r2)
    assert order == ["r1", "w", "r2"]
    assert handler._reader_count == 0 and handler._writer_waiting is False


async def test_cancelled_waiting_writer_lets_the_readers_in(handler):
    first_in = asyncio.Event()
    release = asyncio.Event()
    late_in = asyncio.Event()

    async def first_reader():
        async with handler.reader(USER, dirty=True):
            first_in.set()
            await release.wait()

    async def write():
        async with handler.writer(USER):
            raise AssertionError("writer got the lock")

    async def late_reader():
        async with handler.reader(USER, dirty=True):
            late_in.set()

    r1 = asyncio.create_task(first_reader())
    await first_in.wait()
    w = asyncio.create_task(write())
    await asyncio.sleep(0.01)
    r2 = asyncio.create_task(late_reader())
    await asyncio.sleep(0.01)
    assert not late_in.is_set()

    w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w
    # r1 still holds its scope: nobody else would wake the late reader up
    await asyncio.wait_for(late_in.wait(), timeout=2)
    release.set()
    await asyncio.gather(r1, r2)
    assert handler._reader_count == 0 and handler._writer_waiting is False
