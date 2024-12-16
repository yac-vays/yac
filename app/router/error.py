import logging

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.model.err import YACError
from app.lib import hacks
from app import consts

logger = logging.getLogger(__name__)


async def handle_yac(request: Request, exc: YACError) -> JSONResponse:
    if exc.code == 500:
        logger.error(f"{exc.title}: {exc}")

    message = exc.default_message
    if exc.code not in [401, 500] or consts.ENV.debug_mode:
        message = str(exc)

    return hacks.add_cors_headers_to_response(
        request,
        JSONResponse(
            status_code=exc.code,
            content=jsonable_encoder({"title": exc.title, "message": message}),
        ),
    )


async def handle_all(request: Request, exc: Exception) -> JSONResponse:
    # TODO own stacktrace log call (to allow for different log-formats later)
    return hacks.add_cors_headers_to_response(
        request,
        JSONResponse(
            status_code=500,
            content=jsonable_encoder(
                {"title": YACError.title, "message": YACError.default_message}
            ),
        ),
    )
