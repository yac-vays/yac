from datetime import datetime, timezone
import asyncio
import time

from fastapi import APIRouter, status, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.lib import hacks
from app.lib import repo
from app.lib import specs
from app.lib import staleness
from app.lib.auth import CurrentUser
from app.version import VERSION
from app.model.err import http_responses
from app.model.err import RepoMaintenance
from app.model.err import RepoUnavailable
from app.model.inp import OperationRequest
from app.model.out import RepoStatus
from app.model.out import Status
from app.model.out import Meta
from app.model.out import User

router = APIRouter()

_STATUS_TTL_SECONDS = 10
_status_cache: dict = {"status": None, "expires_at": 0.0}
_status_cache_lock = asyncio.Lock()


def _repo_status(*, stale: bool) -> RepoStatus:
    """
    Build the `repo` part of the status body from the plugin's diagnostics.
    The public body only says *that* the remote is unavailable (and whether
    it is maintenance); the detailed git output stays in the logs.
    """
    state = repo.handler.state()
    synced = (
        datetime.fromtimestamp(state.synced, tz=timezone.utc)
        if state.synced is not None
        else None
    )
    error: str | None = None
    if state.failed is not None:
        error = (
            RepoMaintenance.default_message
            if state.maintenance
            else RepoUnavailable.default_message
        )
    return RepoStatus(
        available=state.failed is None, stale=stale, synced=synced, error=error
    )


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
    summary="Get the status, including the data repository's availability",
    responses=http_responses(),
)
async def get_status(request: Request) -> Status:
    """
    Will check if the API is working, if the specs file can be read and parsed
    and if the repository is accessible, and return some status information.

    This is a diagnostic endpoint, not a liveness check: the remote data
    repository being down is not something a pod restart can fix. While the
    remote is unavailable but a local copy exists, this answers `200` with
    `repo.stale: true` (reads are served from the last known state). Only
    when there is no data at all does it answer `503`, with the same body.
    """

    now = time.monotonic()
    if _status_cache["status"] is not None and now < _status_cache["expires_at"]:
        return _status_cache["status"]

    async with _status_cache_lock:
        now = time.monotonic()
        if _status_cache["status"] is not None and now < _status_cache["expires_at"]:
            return _status_cache["status"]

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
        try:
            async with repo.handler.reader(None) as raw:
                h = await raw.get_hash()
        except RepoUnavailable:
            # Not cached: the next call should notice recovery right away
            # (the plugin's own backoff keeps this from hammering the remote).
            return hacks.add_cors_headers_to_response(
                request,
                JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content=jsonable_encoder(
                        Status(hash=None, repo=_repo_status(stale=False))
                    ),
                ),
            )

        stale = staleness.is_stale()
        result = Status(hash=h, repo=_repo_status(stale=stale))
        if not stale:
            _status_cache["status"] = result
            _status_cache["expires_at"] = time.monotonic() + _STATUS_TTL_SECONDS
        return result


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
