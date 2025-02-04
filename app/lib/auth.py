"""
Raises: [app.model.err.AuthError]
"""

import logging
from typing import Optional
from typing_extensions import Annotated

from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, Cookie
from fastapi.security.open_id_connect_url import OpenIdConnect
from starlette.requests import Request

from app import consts
from app.model.err import AuthError
from app.model.out import User

logger = logging.getLogger(__name__)


class OpenIdConnectOptional(OpenIdConnect):
    """
    Overwrite FastAPIs implementation to never raise but return None instead.
    This is required to allow both, header or cookie authentication!
    """

    async def __call__(self, request: Request) -> Optional[str]:
        authorization = request.headers.get("Authorization")
        if not authorization:
            return None
        return authorization


authlib_oauth = OAuth()
authlib_oauth.register(
    name="oidc",
    server_metadata_url=consts.ENV.oidc_url,
    client_kwargs={"scope": "openid"},
)

fastapi_oauth2 = OpenIdConnectOptional(
    openIdConnectUrl=consts.ENV.oidc_url,
    scheme_name="OpenID Connect",
)


async def get_current_user(
    header_token: Annotated[str | None, Depends(fastapi_oauth2)] = None,
    cookie_token: Annotated[
        str | None, Cookie(alias="token", include_in_schema=False)
    ] = None,
) -> User:
    if header_token:
        return await verify_token(header_token)
    if cookie_token:
        return await verify_token(cookie_token)

    raise AuthError("Authentication through header or cookie required!")


async def get_token(token: Annotated[str, Depends(fastapi_oauth2)]) -> tuple[str, User]:
    user = await verify_token(token)
    return token, user


async def verify_token(token: str) -> User:
    try:
        user = await authlib_oauth.oidc.parse_id_token(  # type: ignore
            token={"id_token": token[7:] if token[:7] == "Bearer " else token},
            nonce=None,  # can be ignored because we're using PKCE
        )
        if user["aud"] not in consts.ENV.oidc_client_ids.split(","):
            raise AuthlibBaseError(f'"{user["aud"]}" is not an accepted client_id')
    except (AttributeError, AuthlibBaseError) as error:
        raise AuthError(
            f"Supplied authentication could not be validated ({error})"
        ) from error

    try:
        full_name = consts.ENV.oidc_jwt_full_name.format(**user)
        if len(full_name) <= 0:
            raise KeyError("Empty string")
    except KeyError:
        full_name = consts.ENV.oidc_jwt_full_name_fallback.format(**user)

    try:
        email = consts.ENV.oidc_jwt_email.format(**user)
        if len(email) <= 0:
            raise KeyError("Empty string")
    except KeyError:
        email = consts.ENV.oidc_jwt_email_fallback.format(**user)

    return User(
        name=consts.ENV.oidc_jwt_name.format(**user),
        full_name=full_name,
        email=email,
        token=user,
    )
