"""
Raises: [app.model.err.AuthError]

Two bearer-token flavors are accepted:

- OIDC **id-tokens** (the default): issued to an interactive user via one
  of the accepted `auth.oidc.client_ids` — this is what VAYS sends.
- OIDC **JWT access tokens** (RFC 9068, opt-in): enabled by configuring
  `auth.oidc.access_tokens.audiences`. Meant for machine clients that
  obtain tokens via the OAuth2 `client_credentials` grant. Validation is
  fully local and stateless (signature via the provider's cached JWKS,
  plus `iss`/`exp`/`aud` checks) — no IdP round-trip per request. Opaque
  (non-JWT) access tokens are not supported: they would require token
  introspection, i.e. an IdP call per request.

Routing between the two paths is decided on the *unverified* `aud` claim:
a token whose `aud` intersects `access_tokens.audiences` takes the
access-token path, everything else the id-token path. The peek is for
routing only — each path fully validates the token afterwards.
"""

import base64
import json
import logging
from typing_extensions import Annotated

from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends
from fastapi.security.open_id_connect_url import OpenIdConnect
from joserfc import jwt as jose_jwt
from joserfc.errors import InvalidKeyIdError, JoseError, MissingClaimError
from joserfc.jwk import KeySet

from app.lib import specs
from app.model.err import AuthError
from app.model.out import User
from app.model.spc import AuthOIDCJWT

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


def _aud_set(aud) -> set:
    """Normalize an `aud` claim (string or list) into a set of strings."""
    if isinstance(aud, str):
        return {aud}
    if isinstance(aud, list):
        return {a for a in aud if isinstance(a, str)}
    return set()


def _unverified_part(token: str, index: int) -> dict:
    """
    Best-effort decode of a JWT part (0 = header, 1 = payload) WITHOUT
    verification, used only to route the token to the right validation path
    and to improve rejection messages. Never trust the result.
    """
    try:
        part = token.split(".")[index]
        decoded = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _unverified_claims(token: str) -> dict:
    return _unverified_part(token, 1)


def _is_access_token(token: str) -> bool:
    audiences = specs.AUTH.oidc.access_tokens.audiences
    if not audiences:
        return False
    aud = _unverified_claims(token).get("aud", "")
    return bool(_aud_set(aud).intersection(audiences))


def _reject_unrouted_access_token(token: str) -> None:
    """
    A token with the RFC 9068 header `typ: at+jwt` is definitely an access
    token, never an id-token; if it did not match the access-token routing it
    could only go on to fail id-token validation with a cryptic complaint
    about the missing `nonce` claim, so reject it with the real reason here.
    The unverified header/claims are only used to phrase the rejection.
    """
    if str(_unverified_part(token, 0).get("typ", "")).lower() not in (
        "at+jwt",
        "application/at+jwt",
    ):
        return
    if not specs.AUTH.oidc.access_tokens.audiences:
        raise AuthError(
            "Access tokens are not enabled on this server"
            " (no auth.oidc.access_tokens.audiences configured)"
        )
    aud = _unverified_claims(token).get("aud", "")
    raise AuthError(f'The access token audience "{aud}" is not accepted')


def _extract_name(jwt_cfg: AuthOIDCJWT, claims: dict) -> str:
    try:
        return jwt_cfg.name.format(**claims)
    except (KeyError, IndexError, ValueError) as error:
        raise AuthError(
            f"Could not extract the user name from the token ({error})"
        ) from error


def _extract_full_name(jwt_cfg: AuthOIDCJWT, claims: dict) -> str:
    # The `.format(**claims)` templates raise KeyError on a missing claim
    # and IndexError/ValueError on a malformed template; all of those must
    # become a clean AuthError instead of a 500.
    try:
        full_name = jwt_cfg.full_name.format(**claims)
        if len(full_name) <= 0:
            raise KeyError("Empty string")
        return full_name
    except (KeyError, IndexError, ValueError):
        try:
            return jwt_cfg.full_name_fallback.format(**claims)
        except (KeyError, IndexError, ValueError) as error:
            raise AuthError(
                f"Could not extract the full name from the token ({error})"
            ) from error


def _extract_email(jwt_cfg: AuthOIDCJWT, claims: dict) -> str:
    try:
        email = jwt_cfg.email.format(**claims)
        if len(email) <= 0:
            raise KeyError("Empty string")
        return email
    except (KeyError, IndexError, ValueError):
        try:
            return jwt_cfg.email_fallback.format(**claims)
        except (KeyError, IndexError, ValueError) as error:
            raise AuthError(
                f"Could not extract the email from the token ({error})"
            ) from error


async def _user_from_id_token(token: str) -> User:
    try:
        user = await authlib_oauth.oidc.parse_id_token(  # type: ignore
            token={"id_token": token},
            # Nonce is validated client-side in openid-client's
            # authorizationCodeGrant (loginProcess.ts: expectedNonce). The
            # backend re-validates only signature, iss, aud, exp; it has no
            # session state to bind a nonce to.
            nonce=None,
        )
        aud = user["aud"]
        if not _aud_set(aud).intersection(specs.AUTH.oidc.client_ids):
            raise AuthlibBaseError(f'"{aud}" is not an accepted client_id')
    except (AttributeError, AuthlibBaseError, JoseError) as error:
        # Since authlib 1.6 the JOSE layer is joserfc, whose errors (expired
        # token, bad signature, malformed JWT, invalid claims) surface
        # unwrapped and do NOT inherit from AuthlibBaseError.
        hint = ""
        # A nonce-less but otherwise valid-looking token is usually an access
        # token that missed the audience routing (some IdPs omit the RFC 9068
        # `typ` header, so _reject_unrouted_access_token cannot catch it).
        if isinstance(error, MissingClaimError) and "'nonce'" in str(error):
            hint = (
                " — id-tokens must carry a nonce; if this was meant as an"
                " access token, its aud must match one of"
                " auth.oidc.access_tokens.audiences"
            )
        raise AuthError(
            f"Supplied authentication could not be validated ({error}){hint}"
        ) from error

    jwt_cfg = specs.AUTH.oidc.jwt
    return User(
        name=_extract_name(jwt_cfg, user),
        full_name=_extract_full_name(jwt_cfg, user),
        email=_extract_email(jwt_cfg, user),
        token=user,
    )


async def _decode_access_token(token: str, algorithms: list[str]) -> dict:
    """
    Verify the JWS against the provider's (cached) JWKS and return the raw
    claims. On an unknown `kid` the JWKS is refetched once — same key-rotation
    handling as authlib's `parse_id_token`.
    """
    jwks = await authlib_oauth.oidc.fetch_jwk_set()
    try:
        decoded = jose_jwt.decode(
            token, key=KeySet.import_key_set(jwks), algorithms=algorithms
        )
    except InvalidKeyIdError:
        jwks = await authlib_oauth.oidc.fetch_jwk_set(force=True)
        decoded = jose_jwt.decode(
            token, key=KeySet.import_key_set(jwks), algorithms=algorithms
        )
    return decoded.claims


async def _user_from_access_token(token: str) -> User:
    cfg = specs.AUTH.oidc.access_tokens
    try:
        metadata = await authlib_oauth.oidc.load_server_metadata()
        claims = await _decode_access_token(token, cfg.algorithms)

        claims_options: dict = {"exp": {"essential": True}}
        if "issuer" in metadata:
            claims_options["iss"] = {"essential": True, "value": metadata["issuer"]}
        jose_jwt.JWTClaimsRegistry(leeway=120, **claims_options).validate(claims)

        aud = claims.get("aud", "")
        if not _aud_set(aud).intersection(cfg.audiences):
            raise AuthlibBaseError(f'"{aud}" is not an accepted audience')
        sub = str(claims.get("sub", ""))
        if cfg.subjects and sub not in cfg.subjects:
            raise AuthlibBaseError(f'"{sub}" is not an accepted subject')
    except (AttributeError, AuthlibBaseError, JoseError) as error:
        raise AuthError(
            f"Supplied authentication could not be validated ({error})"
        ) from error

    # A statically configured account (keyed by `sub`) supplies identity
    # fields the token itself does not carry; the format-strings are only
    # consulted for fields the account leaves unset.
    account = cfg.accounts.get(sub)
    return User(
        name=(account.name if account and account.name else _extract_name(cfg.jwt, claims)),
        full_name=(
            account.full_name
            if account and account.full_name
            else _extract_full_name(cfg.jwt, claims)
        ),
        email=(
            account.email if account and account.email else _extract_email(cfg.jwt, claims)
        ),
        token=claims,
    )


async def get_current_user(token: Annotated[str, Depends(fastapi_oauth2)]) -> User:
    token = token[7:] if token[:7].lower() == "bearer " else token
    if _is_access_token(token):
        return await _user_from_access_token(token)
    _reject_unrouted_access_token(token)
    return await _user_from_id_token(token)


CurrentUser = Annotated[User, Depends(get_current_user)]
