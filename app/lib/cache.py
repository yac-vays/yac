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

# Strong references to in-flight single-flight computation tasks. asyncio
# keeps only weak references to tasks, so without this a computation whose
# leader was cancelled (and thus is no longer awaiting it) could be
# garbage-collected mid-flight, leaving the waiters hanging.
_inflight_tasks: set[asyncio.Task] = set()


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
    (the leader) starts the computation, all others await its result
    (exceptions propagate to every waiter and never poison the in-flight
    map). The computation itself runs in a separate task shielded from the
    leader: a cancelled leader (e.g. its HTTP request was aborted by a
    client disconnect) re-raises its own CancelledError but does NOT abort
    the computation — the waiters are still served the computed value.
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
                # Awaiting the future re-raises the computation's exception
                # (if any) in every waiter without cancelling the computation.
                value = await fut
                return copy.deepcopy(value) if copy_result else value

            async def compute() -> None:
                """
                Run the computation and settle `fut`. Runs as its own task
                (not in the leader's coroutine) so a cancelled leader cannot
                abort it; it must therefore never raise — an unawaited task
                exception would warn at GC — so failures are delivered to the
                leader and the waiters exclusively via `fut`. Cache-publish
                and the inflight-pop also happen here, guaranteeing they run
                even when the leader is gone (inflight entries are always
                popped exactly once, before `fut` is settled).
                """
                try:
                    value = await func(*args, **kwargs)
                except BaseException as error:  # pylint: disable=broad-exception-caught
                    async with lock:
                        inflight.pop(key, None)
                    fut.set_exception(error)
                    # Mark the exception as retrieved so a leader without
                    # waiters does not trigger "exception was never
                    # retrieved" warnings.
                    fut.exception()
                    return
                async with lock:
                    expires = (now + ttl) if ttl is not None else float("inf")
                    cache[key] = (expires, value)
                    cache.move_to_end(key)
                    while len(cache) > maxsize:
                        cache.popitem(last=False)
                    inflight.pop(key, None)
                fut.set_result(value)

            task = asyncio.get_running_loop().create_task(compute())
            _inflight_tasks.add(task)
            task.add_done_callback(_inflight_tasks.discard)
            # If the leader is cancelled at this await point, the shield
            # re-raises the CancelledError in the leader only; the task
            # keeps running and settles `fut` for the waiters.
            await asyncio.shield(task)
            # `compute` never raises: by now `fut` is settled with either
            # the value or the original exception from `func`, which this
            # await re-raises in the leader (matching the waiters).
            value = await fut
            return copy.deepcopy(value) if copy_result else value

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        wrapper.cache_size = lambda: len(cache)  # type: ignore[attr-defined]
        return wrapper

    return decorator
