"""
Limits the size of request bodies.

Neither uvicorn nor FastAPI bound a body: it is read and parsed completely
before any validation (and before the token is even looked at by the
endpoint). The largest legitimate body is a raw-YAML edit, carrying the old
and the new document as JSON strings.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import consts

MAX_BODY_SIZE = 4 * consts.ENV.max_yaml_size + 65536


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """
    Answers 413 for a body above `max_size` bytes: right away if the
    `Content-Length` says so, else as soon as more has been received.

    Add it as the innermost middleware, so its answer passes the CORS
    middleware like any other.
    """

    def __init__(self, app: ASGIApp, max_size: int = MAX_BODY_SIZE) -> None:
        self.app = app
        self.max_size = max_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0  # the server rejects it, not our business
                if declared > self.max_size:
                    await self._reject(send)
                    return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_size:
                    exceeded = True
                    raise _TooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                # Whatever the app makes of the aborted body (FastAPI turns
                # the exception into a 400): the answer is the 413 below.
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if started:
                raise
        if exceeded and not started:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            {
                "title": "Request Too Large",
                "message": (
                    f"The request body is larger than {self.max_size} bytes."
                ),
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
