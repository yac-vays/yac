"""
Raises: [app.model.err.AuthError]
"""

from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends
from fastapi.security.open_id_connect_url import OpenIdConnect
from typing_extensions import Annotated

from app import consts
from app.model.err import AuthError
from app.model.out import User

authlib_oauth = OAuth()
authlib_oauth.register(
    name="oidc",
    server_metadata_url=consts.ENV.oidc_url,
    client_kwargs={"scope": "openid"},
)

fastapi_oauth2 = OpenIdConnect(
    openIdConnectUrl=consts.ENV.oidc_url,
    scheme_name="OpenID Connect",
)


async def get_current_user(token: Annotated[str, Depends(fastapi_oauth2)]) -> User:
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
