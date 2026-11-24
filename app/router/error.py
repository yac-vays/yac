import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.model.err import YACError
from app.lib import hacks
from app import consts

logger = logging.getLogger(__name__)


async def handle_yac(request: Request, exc: YACError) -> JSONResponse:
    del request
    if exc.code == 500:
        logger.error(f"{exc.title}: {exc}")
    elif exc.code == 401:
        # The client only gets the default message: keep the reason here.
        logger.warning(f"{exc.title}: {exc}")

    message = exc.default_message
    if exc.code not in [401, 500] or consts.ENV.debug_mode:
        message = str(exc)

    return JSONResponse(
        status_code=exc.code,
        content=jsonable_encoder({"title": exc.title, "message": message}),
        headers={"WWW-Authenticate": "Bearer"} if exc.code == 401 else None,
    )


async def handle_invalid(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """A malformed request (422), in the format of all other errors."""
    del request
    problems = []
    for e in exc.errors():
        # e.g. ("body", "entity", "name") -> "entity.name"
        where = ".".join(str(part) for part in e.get("loc", ())[1:])
        problems.append(f"{where}: {e.get('msg')}" if where else str(e.get("msg")))
    return JSONResponse(
        status_code=422,
        content={"title": "Invalid Request", "message": "; ".join(problems)},
    )


async def handle_all(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception in request handler", exc_info=exc)
    return hacks.add_cors_headers_to_response(
        request,
        JSONResponse(
            status_code=500,
            content=jsonable_encoder(
                {"title": YACError.title, "message": YACError.default_message}
            ),
        ),
    )
