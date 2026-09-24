"""
Per-request "stale data" marker.

When the remote git repository cannot be reached, the repo plugins keep
answering reads from the last known state instead of failing. They call
`mark_stale` while doing so; the ASGI middleware below then adds two
headers to the response so clients can tell the data may be outdated:

    Warning: 110 yac "Data repository unreachable, serving last known state"
    X-YAC-Repo-Synced: <unix timestamp of the last successful sync, if known>

(110 = "Response is Stale", RFC 7234 section 5.5.1.)

A contextvar holds a small mutable holder per request; the middleware
creates it, the plugin mutates it. This works across the task boundaries
FastAPI introduces because the child contexts share the holder object.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterator, MutableMapping

WARNING_HEADER = "Warning"
WARNING_VALUE = '110 yac "Data repository unreachable, serving last known state"'
SYNCED_HEADER = "X-YAC-Repo-Synced"
EXPOSED_HEADERS = [WARNING_HEADER, SYNCED_HEADER]


@dataclass
class _RequestState:
    stale: bool = False
    synced: float | None = None


_STATE: ContextVar[_RequestState | None] = ContextVar("yac_stale_state", default=None)


def mark_stale(synced: float | None) -> None:
    """
    Flag the current request as answered from stale data. `synced` is the
    unix timestamp of the last successful sync with the remote (None if
    unknown). A no-op outside of a request (e.g. at startup).
    """
    state = _STATE.get()
    if state is None:
        return
    state.stale = True
    if synced is not None:
        state.synced = synced


def is_stale() -> bool:
    state = _STATE.get()
    return state is not None and state.stale


@contextmanager
def request_scope() -> Iterator[_RequestState]:
    """
    Open a fresh per-request state (used by the middleware, and by tests
    that call the repo layer without going through HTTP).
    """
    state = _RequestState()
    token = _STATE.set(state)
    try:
        yield state
    finally:
        _STATE.reset(token)


Scope = MutableMapping[str, object]
Receive = Callable[[], Awaitable[MutableMapping[str, object]]]
Send = Callable[[MutableMapping[str, object]], Awaitable[None]]


class StaleResponseMiddleware:
    """
    Pure ASGI middleware (no BaseHTTPMiddleware: that one runs the app in a
    separate task whose context changes would not be visible here).
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        with request_scope() as state:

            async def send_with_headers(message: MutableMapping[str, object]) -> None:
                if message["type"] == "http.response.start" and state.stale:
                    headers = list(message.get("headers", []))  # type: ignore[arg-type]
                    headers.append(
                        (WARNING_HEADER.lower().encode(), WARNING_VALUE.encode())
                    )
                    if state.synced is not None:
                        headers.append(
                            (
                                SYNCED_HEADER.lower().encode(),
                                str(int(state.synced)).encode(),
                            )
                        )
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_with_headers)
