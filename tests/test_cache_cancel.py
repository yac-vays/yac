"""A cancelled waiter must not disturb the other callers of a shared miss."""

import asyncio

import pytest

from app.lib.cache import keyed_alru_cache


async def test_cancelled_waiter_does_not_cancel_the_computation():
    release = asyncio.Event()
    calls = 0

    @keyed_alru_cache(key_fn=lambda key: key)
    async def slow(key):
        nonlocal calls
        calls += 1
        await release.wait()
        return f"value-{key}"

    leader = asyncio.create_task(slow("k"))
    await asyncio.sleep(0)
    waiter_a = asyncio.create_task(slow("k"))
    waiter_b = asyncio.create_task(slow("k"))
    await asyncio.sleep(0)

    waiter_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_a

    release.set()
    assert await leader == "value-k"
    assert await waiter_b == "value-k"
    assert calls == 1
