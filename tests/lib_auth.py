"""
Tests for `lib.auth.get_current_user` -- the backend half of the OIDC trust
boundary. It re-validates a bearer id-token (signature/iss/aud/exp are done by
authlib; this code adds the `aud` membership check) and projects the JWT claims
into a `User` via the configurable `jwt.*` format strings.

The actual JWT signature verification lives in authlib, so `parse_id_token` is
replaced with a fake that yields a claims dict. That lets us drive the parts YAC
owns: the audience check, the `Bearer ` prefix stripping, and the
full_name/email primary-vs-fallback template logic.
"""

import pytest

from app.lib import auth
from app.lib import specs
from app.model.err import AuthError
from app.model.spc import Auth, AuthOIDC, AuthOIDCJWT

from authlib.common.errors import AuthlibBaseError


def _auth(client_ids=("client-a",), **jwt):
    return Auth(oidc=AuthOIDC(client_ids=list(client_ids), jwt=AuthOIDCJWT(**jwt)))


@pytest.fixture
def fake_oidc(monkeypatch):
    """
    Replace AUTH config + `parse_id_token`. `_install(claims, auth=...)` makes
    the fake return `claims` (or raise it, if it's an exception) and records the
    token dict it was handed.
    """
    captured = {}

    def _install(claims_or_exc, auth_cfg=None):
        monkeypatch.setattr(specs, "AUTH", auth_cfg or _auth())

        async def _parse(token, nonce=None):
            captured["token"] = token
            captured["nonce"] = nonce
            if isinstance(claims_or_exc, Exception):
                raise claims_or_exc
            return claims_or_exc

        monkeypatch.setattr(auth.authlib_oauth.oidc, "parse_id_token", _parse)
        return captured

    return _install


# ----- audience check -----

async def test_valid_token_maps_claims_to_user(fake_oidc):
    fake_oidc({
        "sub": "u73", "name": "John Doe", "email": "john@example.com",
        "aud": "client-a",
    })
    user = await auth.get_current_user("Bearer abc.def.ghi")
    assert user.name == "u73"
    assert user.full_name == "John Doe"
    assert user.email == "john@example.com"
    assert user.token["sub"] == "u73"


async def test_aud_as_list_intersects_accepted(fake_oidc):
    fake_oidc({"sub": "u", "name": "N", "email": "e@x", "aud": ["other", "client-a"]})
    user = await auth.get_current_user("tok")
    assert user.name == "u"


async def test_aud_not_accepted_raises(fake_oidc):
    fake_oidc({"sub": "u", "name": "N", "email": "e@x", "aud": "someone-else"})
    with pytest.raises(AuthError):
        await auth.get_current_user("tok")


async def test_parse_failure_becomes_auth_error(fake_oidc):
    fake_oidc(AuthlibBaseError("bad signature"))
    with pytest.raises(AuthError):
        await auth.get_current_user("tok")


# ----- bearer prefix stripping -----

async def test_bearer_prefix_is_stripped(fake_oidc):
    cap = fake_oidc({"sub": "u", "name": "N", "email": "e@x", "aud": "client-a"})
    await auth.get_current_user("Bearer the-jwt")
    assert cap["token"] == {"id_token": "the-jwt"}
    # nonce is intentionally not validated server-side
    assert cap["nonce"] is None


async def test_raw_token_without_prefix_passes_through(fake_oidc):
    cap = fake_oidc({"sub": "u", "name": "N", "email": "e@x", "aud": "client-a"})
    await auth.get_current_user("raw-jwt-no-prefix")
    assert cap["token"] == {"id_token": "raw-jwt-no-prefix"}


async def test_bearer_prefix_is_case_insensitive(fake_oidc):
    cap = fake_oidc({"sub": "u", "name": "N", "email": "e@x", "aud": "client-a"})
    await auth.get_current_user("bEaReR x")
    assert cap["token"] == {"id_token": "x"}


# ----- full_name / email fallbacks -----

async def test_full_name_falls_back_when_primary_claim_missing(fake_oidc):
    # default full_name="{name}"; missing -> full_name_fallback "{given_name} {family_name}"
    fake_oidc({
        "sub": "u", "aud": "client-a",
        "given_name": "Jane", "family_name": "Roe", "email": "j@x.com",
    })
    user = await auth.get_current_user("tok")
    assert user.full_name == "Jane Roe"


async def test_full_name_falls_back_when_primary_is_empty(fake_oidc):
    # name present but empty -> still falls back
    fake_oidc({
        "sub": "u", "aud": "client-a", "name": "",
        "given_name": "Jane", "family_name": "Roe", "email": "j@x.com",
    })
    user = await auth.get_current_user("tok")
    assert user.full_name == "Jane Roe"


async def test_email_falls_back_to_sub_localhost(fake_oidc):
    # default email="{email}"; missing -> email_fallback "{sub}@localhost"
    fake_oidc({"sub": "user99", "aud": "client-a", "name": "N"})
    user = await auth.get_current_user("tok")
    assert user.email == "user99@localhost"


async def test_custom_jwt_templates(fake_oidc):
    cfg = _auth(name="{preferred_username}", email="{mail}")
    fake_oidc(
        {"sub": "u", "aud": "client-a", "preferred_username": "alice",
         "mail": "alice@eth.ch", "name": "Alice"},
        auth_cfg=cfg,
    )
    user = await auth.get_current_user("tok")
    assert user.name == "alice" and user.email == "alice@eth.ch"
