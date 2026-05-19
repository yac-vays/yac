"""
Tests for app.lib.cache (the keyed_alru_cache decorator and stable_key helper).

Run via tests/main.py — uses bare asserts, no test framework.
"""

import asyncio
import time

from app.lib.cache import keyed_alru_cache, stable_key


def _stable_key_tests() -> None:
    # Same dict, different insertion order -> same key
    assert stable_key({"a": 1, "b": 2}) == stable_key({"b": 2, "a": 1})
    # Nested dicts are sorted recursively
    assert stable_key({"x": {"b": 2, "a": 1}, "y": [3, 1, 2]}) == stable_key(
        {"y": [3, 1, 2], "x": {"a": 1, "b": 2}}
    )
    # Lists preserve order (semantically meaningful)
    assert stable_key([1, 2, 3]) != stable_key([3, 2, 1])
    # Sets are order-insensitive
    assert stable_key({1, 2, 3}) == stable_key({3, 2, 1})
    # Tuples are treated like lists
    assert stable_key((1, 2)) == stable_key([1, 2])
    # Scalars round-trip
    assert stable_key("foo") == "foo"
    assert stable_key(42) == 42
    assert stable_key(None) is None
    # Result is hashable (key requirement)
    hash(stable_key({"a": [1, 2], "b": {"c": 3}}))


def _basic_hit_miss() -> None:
    calls = {"n": 0}

    @keyed_alru_cache(lambda x, y: (x,), maxsize=10)
    async def f(x, y):
        calls["n"] += 1
        return [x, y]

    async def run() -> None:
        # First call: miss
        a = await f(1, "alpha")
        assert a == [1, "alpha"]
        assert calls["n"] == 1
        # Same key (x=1), different y -> hit, returns cached value
        b = await f(1, "beta")
        assert b == [1, "alpha"]
        assert calls["n"] == 1
        # Different key
        c = await f(2, "gamma")
        assert c == [2, "gamma"]
        assert calls["n"] == 2

    asyncio.run(run())


def _lru_eviction_order() -> None:
    calls = {"n": 0}

    @keyed_alru_cache(lambda k: (k,), maxsize=2)
    async def f(k):
        calls["n"] += 1
        return k

    async def run() -> None:
        await f("a")          # MISS  cache: [a]            n=1
        await f("b")          # MISS  cache: [a, b]         n=2
        await f("a")          # HIT   cache: [b, a]         n=2  (a moved to MRU)
        await f("c")          # MISS  cache: [a, c]         n=3  (b evicted as LRU)
        assert calls["n"] == 3
        await f("b")          # MISS  cache: [c, b]         n=4  (a evicted, b inserted)
        assert calls["n"] == 4
        # a was the LRU before f("b"), so it has been evicted now too
        await f("a")          # MISS  n=5
        assert calls["n"] == 5
        # c is still in cache (was MRU at the time of f("b"))? Let's check:
        # Before f("a"): cache: [c, b]. f("a") MISS -> insert -> [c, b, a] -> evict c -> [b, a]
        # So c is now evicted; b is still cached.
        await f("b")          # HIT
        assert calls["n"] == 5

    asyncio.run(run())


def _ttl_expiry() -> None:
    calls = {"n": 0}

    @keyed_alru_cache(lambda k: (k,), maxsize=10, ttl=0.05)
    async def f(k):
        calls["n"] += 1
        return k

    async def run() -> None:
        await f("a")
        await f("a")
        assert calls["n"] == 1
        # Wait past the TTL
        await asyncio.sleep(0.07)
        await f("a")
        assert calls["n"] == 2

    asyncio.run(run())


def _copy_result_isolation() -> None:
    @keyed_alru_cache(lambda k: (k,), copy_result=True)
    async def f(k):
        return {"k": k, "list": [1, 2, 3]}

    async def run() -> None:
        a = await f("x")
        a["list"].append(99)
        a["new"] = True
        b = await f("x")
        # Cached value must be untouched by the caller's mutation
        assert b == {"k": "x", "list": [1, 2, 3]}, b
        assert "new" not in b

    asyncio.run(run())


def _copy_result_off_shares_reference() -> None:
    @keyed_alru_cache(lambda k: (k,), copy_result=False)
    async def f(k):
        return {"items": []}

    async def run() -> None:
        a = await f("x")
        a["items"].append("polluted")
        b = await f("x")
        # Without copy_result, mutation IS visible. This documents the
        # contract: only opt into copy_result when callers may mutate.
        assert b is a
        assert b["items"] == ["polluted"]

    asyncio.run(run())


def _cache_clear_and_size() -> None:
    @keyed_alru_cache(lambda k: (k,), maxsize=10)
    async def f(k):
        return k

    async def run() -> None:
        await f("a")
        await f("b")
        await f("c")
        assert f.cache_size() == 3
        f.cache_clear()
        assert f.cache_size() == 0

    asyncio.run(run())


def _unhashable_args_pass_through() -> None:
    """The whole point of keyed_alru_cache: unhashable args are fine, the
    key_fn projects to something hashable."""
    calls = {"n": 0}

    @keyed_alru_cache(lambda hash_, model: (hash_,), maxsize=10)
    async def f(hash_, model):
        calls["n"] += 1
        return model["data"]

    async def run() -> None:
        m1 = {"data": "first"}
        m2 = {"data": "second"}
        a = await f("h1", m1)
        b = await f("h1", m2)  # same hash key -> hit; m2 ignored for caching
        assert a == "first" and b == "first"
        assert calls["n"] == 1

    asyncio.run(run())


def test() -> None:
    _stable_key_tests()
    _basic_hit_miss()
    _lru_eviction_order()
    _ttl_expiry()
    _copy_result_isolation()
    _copy_result_off_shares_reference()
    _cache_clear_and_size()
    _unhashable_args_pass_through()
