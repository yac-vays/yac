"""
Async LRU+TTL caching keyed by an explicit `key_fn`.

The codebase needs to cache async functions whose arguments include
non-hashable objects (Pydantic models, IRepo handlers, dicts). Rather than
serialising the entire call (which is expensive on hits and risks aliasing
mutable returns), the caller declares which arguments contribute to identity
via a small projection function.

Use `copy_result=True` for cached values that callers may mutate.
"""

import asyncio
import copy
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable


def stable_key(value: Any) -> Any:
    """
    Convert a (possibly nested) dict/list into a hashable, order-insensitive
    structure that can be used as part of a cache key.
    """
    if isinstance(value, dict):
        return tuple(sorted((k, stable_key(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(stable_key(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(stable_key(v) for v in value))
    return value


def keyed_alru_cache(
    key_fn: Callable[..., Any],
    *,
    maxsize: int = 1024,
    ttl: float | None = None,
    copy_result: bool = False,
):
    """
    Decorator that LRU+TTL-caches an async function using `key_fn(*args, **kwargs)`
    to derive a hashable cache key.

    - `maxsize`: maximum number of entries; least-recently-used are evicted.
    - `ttl`: optional time-to-live in seconds; expired entries are recomputed.
    - `copy_result`: deep-copy on hit so callers cannot corrupt cached entries.

    Concurrent misses on the same key are single-flighted: the first caller
    runs the computation, all others await its result (exceptions propagate
    to every waiter and never poison the in-flight map).
    """

    def decorator(func):
        cache: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        inflight: dict[Any, asyncio.Future] = {}
        lock = asyncio.Lock()

        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            now = time.monotonic()

            async with lock:
                hit = cache.get(key)
                if hit is not None:
                    expires, value = hit
                    if ttl is None or expires > now:
                        cache.move_to_end(key)
                        return copy.deepcopy(value) if copy_result else value
                    cache.pop(key, None)
                fut = inflight.get(key)
                if fut is None:
                    fut = asyncio.get_running_loop().create_future()
                    inflight[key] = fut
                    leader = True
                else:
                    leader = False

            if not leader:
                # Awaiting the future re-raises the leader's exception (if
                # any) in every waiter without cancelling the computation.
                value = await fut
                return copy.deepcopy(value) if copy_result else value

            try:
                value = await func(*args, **kwargs)
            except BaseException as error:
                async with lock:
                    inflight.pop(key, None)
                fut.set_exception(error)
                # Mark the exception as retrieved so a leader without waiters
                # does not trigger "exception was never retrieved" warnings.
                fut.exception()
                raise

            async with lock:
                expires = (now + ttl) if ttl is not None else float("inf")
                cache[key] = (expires, value)
                cache.move_to_end(key)
                while len(cache) > maxsize:
                    cache.popitem(last=False)
                inflight.pop(key, None)
            fut.set_result(value)

            return copy.deepcopy(value) if copy_result else value

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        wrapper.cache_size = lambda: len(cache)  # type: ignore[attr-defined]
        return wrapper

    return decorator
