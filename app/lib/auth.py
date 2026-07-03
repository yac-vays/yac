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
from joserfc.errors import JoseError
from starlette.requests import Request

from app.lib import specs
from app.model.err import AuthError
from app.model.out import User

logger = logging.getLogger(__name__)


authlib_oauth = OAuth()
authlib_oauth.register(
    name="oidc",
    server_metadata_url=specs.AUTH.oidc.url,
    client_kwargs={"scope": "openid"},
)

fastapi_oauth2 = OpenIdConnect(
    openIdConnectUrl=specs.AUTH.oidc.url,
    scheme_name="OpenID Connect",
)


async def get_current_user(token: Annotated[str, Depends(fastapi_oauth2)]) -> User:
    try:
        user = await authlib_oauth.oidc.parse_id_token(  # type: ignore
            token={"id_token": token[7:] if token[:7].lower() == "bearer " else token},
            # Nonce is validated client-side in openid-client's
            # authorizationCodeGrant (loginProcess.ts: expectedNonce). The
            # backend re-validates only signature, iss, aud, exp; it has no
            # session state to bind a nonce to.
            nonce=None,
        )
        aud = user["aud"]
        aud_set = set(aud) if isinstance(aud, list) else {aud}
        accepted = set(specs.AUTH.oidc.client_ids)
        if not aud_set.intersection(accepted):
            raise AuthlibBaseError(f'"{aud}" is not an accepted client_id')
    except (AttributeError, AuthlibBaseError, JoseError) as error:
        # Since authlib 1.6 the JOSE layer is joserfc, whose errors (expired
        # token, bad signature, malformed JWT, invalid claims) surface
        # unwrapped and do NOT inherit from AuthlibBaseError.
        raise AuthError(
            f"Supplied authentication could not be validated ({error})"
        ) from error

    # The `.format(**user)` templates raise KeyError on a missing claim and
    # IndexError/ValueError on a malformed template; all of those must become
    # a clean AuthError instead of a 500.
    try:
        full_name = specs.AUTH.oidc.jwt.full_name.format(**user)
        if len(full_name) <= 0:
            raise KeyError("Empty string")
    except (KeyError, IndexError, ValueError):
        try:
            full_name = specs.AUTH.oidc.jwt.full_name_fallback.format(**user)
        except (KeyError, IndexError, ValueError) as error:
            raise AuthError(
                f"Could not extract the full name from the token ({error})"
            ) from error

    try:
        email = specs.AUTH.oidc.jwt.email.format(**user)
        if len(email) <= 0:
            raise KeyError("Empty string")
    except (KeyError, IndexError, ValueError):
        try:
            email = specs.AUTH.oidc.jwt.email_fallback.format(**user)
        except (KeyError, IndexError, ValueError) as error:
            raise AuthError(
                f"Could not extract the email from the token ({error})"
            ) from error

    try:
        name = specs.AUTH.oidc.jwt.name.format(**user)
    except (KeyError, IndexError, ValueError) as error:
        raise AuthError(
            f"Could not extract the user name from the token ({error})"
        ) from error

    return User(
        name=name,
        full_name=full_name,
        email=email,
        token=user,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
