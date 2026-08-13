"""
Tests for the JWT-access-token path of `lib.auth.get_current_user`
(RFC 9068 tokens from the OAuth2 `client_credentials` grant).

As in tests/lib_auth.py, the cryptographic verification is delegated to
joserfc/authlib and faked here (`_decode_access_token` is replaced by a
stub returning claims). What runs for real and is under test: the
unverified-`aud` routing between the id-token and access-token paths, the
`iss`/`exp` claims validation (joserfc's JWTClaimsRegistry), the verified
audience/subject checks, and the account/template identity mapping.
"""

import base64
import json

import joserfc.errors
import pytest

from app.lib import auth
from app.lib import specs
from app.model.err import AuthError
from app.model.spc import (
    Auth,
    AuthOIDC,
    AuthOIDCAccessTokens,
    AuthOIDCAccount,
)

FUTURE = 33279209665  # some year-3024 exp; the registry checks against real time


def _jwtish(claims: dict) -> str:
    """A JWT-*shaped* token (unsigned) — enough for the routing peek."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode())
    return f"e30.{payload.decode().rstrip('=')}.c2ln"


def _claims(**over) -> dict:
    claims = {"iss": "https://idp", "exp": FUTURE, "aud": "yac-api", "sub": "svc-backup"}
    claims.update(over)
    return claims


def _auth(**access) -> Auth:
    access.setdefault("audiences", ["yac-api"])
    return Auth(
        oidc=AuthOIDC(
            client_ids=["client-a"],
            access_tokens=AuthOIDCAccessTokens(**access),
        )
    )


@pytest.fixture
def fake_idp(monkeypatch):
    """
    `_install(claims_or_exc, auth_cfg=..., id_claims=...)` fakes the IdP:
    metadata + access-token signature check (returning/raising
    `claims_or_exc`), and `parse_id_token` (returning `id_claims`, or
    failing the test if the id-token path is entered unexpectedly).
    """
    captured = {}

    def _install(claims_or_exc=None, auth_cfg=None, id_claims=None):
        monkeypatch.setattr(specs, "AUTH", auth_cfg or _auth())

        async def _metadata():
            return {"issuer": "https://idp"}

        async def _decode(token, algorithms):
            captured["decoded"] = token
            captured["algorithms"] = algorithms
            if isinstance(claims_or_exc, Exception):
                raise claims_or_exc
            return claims_or_exc

        async def _parse(token, nonce=None):
            captured["id_token"] = token
            if id_claims is None:
                raise AssertionError("id-token path must not be taken")
            return id_claims

        monkeypatch.setattr(auth.authlib_oauth.oidc, "load_server_metadata", _metadata)
        monkeypatch.setattr(auth, "_decode_access_token", _decode)
        monkeypatch.setattr(auth.authlib_oauth.oidc, "parse_id_token", _parse)
        return captured

    return _install


# ----- routing (unverified aud peek) -----

async def test_matching_aud_routes_to_access_token_path(fake_idp):
    cap = fake_idp(_claims())
    token = _jwtish(_claims())
    user = await auth.get_current_user(f"Bearer {token}")
    assert cap["decoded"] == token  # Bearer prefix stripped
    assert user.name == "svc-backup"


async def test_non_matching_aud_routes_to_id_token_path(fake_idp):
    cap = fake_idp(
        id_claims={"sub": "u1", "name": "N", "email": "e@x", "aud": "client-a"}
    )
    user = await auth.get_current_user(_jwtish({"aud": "client-a"}))
    assert "decoded" not in cap
    assert user.name == "u1"


async def test_opaque_token_routes_to_id_token_path(fake_idp):
    # A non-JWT bearer (e.g. an opaque reference token) cannot be peeked and
    # must fall through to the id-token path (which then rejects it).
    fake_idp(id_claims={"sub": "u1", "name": "N", "email": "e@x", "aud": "client-a"})
    user = await auth.get_current_user("gmydk-not-a-jwt")
    assert user.name == "u1"


async def test_routing_disabled_without_audiences(fake_idp):
    # No `access_tokens.audiences` configured -> even a matching-looking
    # token goes to the id-token path.
    cfg = Auth(oidc=AuthOIDC(client_ids=["client-a"]))
    fake_idp(
        auth_cfg=cfg,
        id_claims={"sub": "u1", "name": "N", "email": "e@x", "aud": "client-a"},
    )
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.name == "u1"


# ----- verified claims checks -----

async def test_verified_aud_must_match_despite_peek(fake_idp):
    # The peek routed on a forged aud, but the *verified* claims say the
    # token was issued for someone else -> reject.
    fake_idp(_claims(aud="someone-else"))
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))


async def test_aud_list_intersects_audiences(fake_idp):
    fake_idp(_claims(aud=["other", "yac-api"]))
    user = await auth.get_current_user(_jwtish(_claims(aud=["other", "yac-api"])))
    assert user.name == "svc-backup"


async def test_wrong_issuer_raises(fake_idp):
    fake_idp(_claims(iss="https://evil"))
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))


async def test_expired_token_raises(fake_idp):
    fake_idp(_claims(exp=1))
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))


async def test_missing_exp_raises(fake_idp):
    claims = _claims()
    del claims["exp"]
    fake_idp(claims)
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))


async def test_bad_signature_raises(fake_idp):
    fake_idp(joserfc.errors.BadSignatureError())
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))


async def test_subject_allowlist(fake_idp):
    fake_idp(_claims(), auth_cfg=_auth(subjects=["svc-other"]))
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))

    fake_idp(_claims(), auth_cfg=_auth(subjects=["svc-backup"]))
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.name == "svc-backup"


# ----- identity mapping -----

async def test_default_templates_derive_identity_from_sub(fake_idp):
    # client_credentials tokens have no user claims: name <- {sub},
    # full_name falls back to {sub}, email falls back to {sub}@localhost.
    fake_idp(_claims())
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.name == "svc-backup"
    assert user.full_name == "svc-backup"
    assert user.email == "svc-backup@localhost"
    assert user.token["aud"] == "yac-api"


async def test_account_supplies_identity(fake_idp):
    cfg = _auth(
        accounts={
            "svc-backup": AuthOIDCAccount(
                name="backup", full_name="Backup Robot", email="backup@example.com"
            )
        }
    )
    fake_idp(_claims(), auth_cfg=cfg)
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.name == "backup"
    assert user.full_name == "Backup Robot"
    assert user.email == "backup@example.com"


async def test_partial_account_falls_back_to_templates(fake_idp):
    cfg = _auth(accounts={"svc-backup": AuthOIDCAccount(full_name="Backup Robot")})
    fake_idp(_claims(), auth_cfg=cfg)
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.name == "svc-backup"
    assert user.full_name == "Backup Robot"
    assert user.email == "svc-backup@localhost"


async def test_token_claims_win_over_default_templates(fake_idp):
    # If the IdP *does* inject user-style claims into the access token,
    # the standard templates pick them up without any account entry.
    fake_idp(_claims(name="Robby Robot", email="robby@example.com"))
    user = await auth.get_current_user(_jwtish(_claims()))
    assert user.full_name == "Robby Robot"
    assert user.email == "robby@example.com"


async def test_missing_sub_fails_identity_extraction(fake_idp):
    claims = _claims()
    del claims["sub"]
    fake_idp(claims)
    with pytest.raises(AuthError):
        await auth.get_current_user(_jwtish(_claims()))
