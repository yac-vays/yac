"""
`app.lib.auth.get_current_user` against the REAL authlib / joserfc
verification (tests/lib_auth.py and tests/test_auth_access_token.py stub it
out). No network: the provider metadata and its JWKS are preloaded into the
authlib client.
"""

import base64
import hmac
import json
import time

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.lib import auth
from app.lib import specs
from app.model.err import AuthError
from app.model.spc import Auth
from app.model.spc import AuthOIDC
from app.model.spc import AuthOIDCAccessTokens

ISS = "https://idp.example"
CLIENT = "vays"
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})
HEADER = {"alg": "RS256", "kid": "k1"}


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _claims(**over) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISS,
        "sub": "s1",
        "aud": CLIENT,
        "iat": now,
        "exp": now + 300,
        "nonce": "n",
        "name": "Mallory",
        "email": "mallory@example.com",
        "groups": ["admins"],
    }
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def _unsigned(claims: dict) -> str:
    return _b64({"alg": "none"}) + "." + _b64(claims) + "."


@pytest.fixture
def idp(monkeypatch):
    """`idp(algs, access=...)`: a provider advertising the algorithms `algs`."""

    def _install(algs=None, access=None):
        monkeypatch.setattr(
            specs,
            "AUTH",
            Auth(
                oidc=AuthOIDC(
                    client_ids=[CLIENT],
                    access_tokens=access or AuthOIDCAccessTokens(),
                )
            ),
        )
        metadata = {
            "issuer": ISS,
            "jwks": {"keys": [KEY.as_dict(private=False)]},
            "jwks_uri": f"{ISS}/jwks",
            "_loaded_at": time.time(),
        }
        if algs is not None:
            metadata["id_token_signing_alg_values_supported"] = algs
        monkeypatch.setattr(auth.authlib_oauth.oidc, "server_metadata", metadata)

    return _install


@pytest.mark.parametrize("algs", [None, ["RS256"], ["RS256", "none", "HS256"]])
async def test_signed_id_token_is_accepted(idp, algs):
    idp(algs)
    user = await auth.get_current_user("Bearer " + jwt.encode(HEADER, _claims(), KEY))
    assert user.name == "s1"
    assert user.token["groups"] == ["admins"]


@pytest.mark.parametrize(
    "over",
    [
        {"exp": 1},
        {"iss": "https://evil.example"},
        {"aud": "other-client"},
        {"nonce": None},
        {"iat": None},
    ],
)
async def test_id_token_with_bad_claims_is_rejected(idp, over):
    idp(["RS256"])
    token = jwt.encode(HEADER, _claims(**over), KEY)
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + token)


async def test_id_token_with_tampered_payload_is_rejected(idp):
    idp(["RS256"])
    header, _, signature = jwt.encode(HEADER, _claims(), KEY).split(".")
    forged = f"{header}.{_b64(_claims(sub='admin'))}.{signature}"
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + forged)


@pytest.mark.parametrize("algs", [None, ["RS256"], ["RS256", "none"], ["none"]])
async def test_unsigned_id_token_is_rejected_whatever_the_provider_advertises(
    idp, algs
):
    """
    The provider's `id_token_signing_alg_values_supported` is not trusted to
    be sane: with `none` in it, authlib would accept a token without any
    signature -- i.e. any identity and any groups, from anybody.
    """
    idp(algs)
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + _unsigned(_claims()))


async def test_id_token_signed_with_the_public_key_as_secret_is_rejected(idp):
    """Key confusion: HS256 with the (public) RSA key as the shared secret."""
    idp(["RS256", "HS256"])
    signed = f"{_b64({'alg': 'HS256', 'kid': 'k1'})}.{_b64(_claims())}"
    signature = hmac.new(KEY.as_pem(private=False), signed.encode(), "sha256").digest()
    forged = f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + forged)


async def test_provider_with_only_refused_algorithms_fails_closed(idp):
    idp(["HS256", "none"])
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + jwt.encode(HEADER, _claims(), KEY))


async def test_access_token_is_verified(idp):
    idp(["RS256"], access=AuthOIDCAccessTokens(audiences=["api"], subjects=["robot"]))
    good = {"iss": ISS, "aud": "api", "sub": "robot", "exp": int(time.time()) + 60}

    user = await auth.get_current_user("Bearer " + jwt.encode(HEADER, good, KEY))
    assert user.name == "robot"

    for bad in ({"sub": "someone"}, {"iss": "https://evil.example"}, {"exp": 1}):
        with pytest.raises(AuthError):
            await auth.get_current_user(
                "Bearer " + jwt.encode(HEADER, good | bad, KEY)
            )
    with pytest.raises(AuthError):
        await auth.get_current_user("Bearer " + _unsigned(good))
