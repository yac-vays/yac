from fastapi import APIRouter, Response, status

from app.model.err import http_responses
from app.model.inp import Token
from app.model.inp import User as InpUser
from app.model.out import User as OutUser
from app.consts import ENV

router = APIRouter()


@router.post(
    "/token",
    summary="Set the token cookie",
    status_code=status.HTTP_200_OK,
    responses=http_responses(),
)
async def login(token: Token, response: Response) -> OutUser:
    response.set_cookie(
        "token",
        token[0],
        secure=True,
        httponly=True,
        samesite="lax",
        max_age=86400,
        domain=ENV.cookie_domain,
    )
    return token[1]


@router.delete(
    "/token",
    summary="Unset the token cookie",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=http_responses(),
)
async def logout(response: Response) -> None:
    response.delete_cookie("token")
    return


@router.get(
    "/token",
    summary="Test the token for validity and get its content",
    responses=http_responses(),
)
async def me(user: InpUser) -> OutUser:
    """
    Will validate the OpenID Connect ID Token and return some user data.
    """
    return user
