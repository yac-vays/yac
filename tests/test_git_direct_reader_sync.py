"""Concurrent readers of git_direct share one sync with the remote."""

import asyncio

from tests.lib_git_direct import USER, repo  # noqa: F401


async def _read(repo):  # noqa: F811
    async with repo.reader(USER) as untyped:
        return await untyped.get_hash()


async def test_concurrent_readers_share_a_sync(repo, monkeypatch):  # noqa: F811
    await _read(repo)
    pulls = 0
    pull = repo._pull

    async def counting_pull(user):
        nonlocal pulls
        pulls += 1
        await asyncio.sleep(0.05)  # let the other readers queue up
        await pull(user)

    monkeypatch.setattr(repo, "_pull", counting_pull)
    hashes = await asyncio.gather(*(_read(repo) for _ in range(5)))
    assert len(set(hashes)) == 1
    # The first sync is already running when the others arrive: they must
    # not rely on it, but share the next one.
    assert pulls == 2


async def test_a_later_reader_syncs_again(repo, monkeypatch):  # noqa: F811
    pulls = 0
    pull = repo._pull

    async def counting_pull(user):
        nonlocal pulls
        pulls += 1
        await pull(user)

    monkeypatch.setattr(repo, "_pull", counting_pull)
    await _read(repo)
    await _read(repo)
    assert pulls == 2
