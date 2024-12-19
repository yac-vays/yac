from fastapi import APIRouter, status, Request

from app.lib import repo
from app.lib import specs
from app.version import VERSION
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import User as InpUser
from app.model.out import Status
from app.model.out import Meta
from app.model.out import User as OutUser

router = APIRouter()


@router.get(
    "/meta",
    summary="Test if the application is running",
    responses=http_responses(),
)
async def get_meta() -> Meta:
    """
    Will return some meta data.
    """
    return Meta(version=VERSION)


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

    op = OperationRequest(
        _request=request,
        user=OutUser(
            name="dummy-status-user",
            email="invalid",
            full_name="Dummy Status User",
        ),
        operation="read",
        type_name="does-not-exist",
        name=None,
        actions=[],
        entity=None,
    )

    async with repo.handler.reader(None, details={}) as rpo:
        _ = await specs.read(op, rpo)
        return Status(hash=await rpo.get_hash())


@router.get(
    "/me",
    summary="Test the token for validity",
    responses=http_responses(),
)
async def me(user: InpUser) -> OutUser:
    """
    Will validate the OpenID Connect ID Token and return some user data.
    """
    return user
