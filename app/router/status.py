import asyncio
import time

from fastapi import APIRouter, status, Request

from app.lib import repo
from app.lib import specs
from app.lib.auth import CurrentUser
from app.version import VERSION
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.out import Status
from app.model.out import Meta
from app.model.out import User

router = APIRouter()

_STATUS_TTL_SECONDS = 10
_status_cache: dict = {"hash": None, "expires_at": 0.0}
_status_cache_lock = asyncio.Lock()


@router.get(
    "/meta",
    summary="Meta data",
    responses=http_responses(),
)
async def get_meta() -> Meta:
    """
    Will return some meta data.
    """
    return Meta(
        version=VERSION,
        oidc_url=specs.AUTH.oidc.url,
        oidc_client_ids=specs.AUTH.oidc.client_ids,
    )


@router.get(
    "/health",
    summary="Test if the application is running",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=http_responses(),
)
async def get_health() -> None:
    """
    Will check if the API is working.
    """


@router.get(
    "/status",
    summary="Test if the application is ready and get the status",
    responses=http_responses(),
)
async def get_status(request: Request) -> Status:
    """
    Will check if the API is working, if the specs file can be read and parsed
    and if the repository is accessible and in a clean state.

    It will then return some status information.
    """

    now = time.monotonic()
    if _status_cache["hash"] is not None and now < _status_cache["expires_at"]:
        return Status(hash=_status_cache["hash"])

    async with _status_cache_lock:
        now = time.monotonic()
        if _status_cache["hash"] is not None and now < _status_cache["expires_at"]:
            return Status(hash=_status_cache["hash"])

        op = OperationRequest(
            request_headers=dict(request.headers),
            request_ip=request.client.host if request.client else "",
            user=User(
                name="dummy-status-user",
                email="invalid",
                full_name="Dummy Status User",
            ),
            operation="read",
            type="does-not-exist",
            name=None,
            actions=[],
            entity=None,
        )

        _ = await specs.read(op)
        async with repo.handler.reader(None) as raw:
            h = await raw.get_hash()

        _status_cache["hash"] = h
        _status_cache["expires_at"] = time.monotonic() + _STATUS_TTL_SECONDS
        return Status(hash=h)


@router.get(
    "/me",
    summary="Test the token for validity and get its content",
    responses=http_responses(),
)
async def me(user: CurrentUser) -> User:
    """
    Will validate the OpenID Connect ID Token and return some user data.
    """
    return user
