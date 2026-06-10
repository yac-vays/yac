"""
Tests for `lib.hacks` -- two small FastAPI/Starlette work-arounds:

* `get_openapi_schema_with_oidc_idtoken` injects `x-tokenName: id_token` into
  the OpenID Connect security scheme (so Swagger sends the id-token) and caches
  the generated schema on the app.
* `add_cors_headers_to_response` re-adds CORS headers to error responses, only
  echoing the `Origin` back when it is an allowed origin.
"""

import pytest
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from fastapi.security.open_id_connect_url import OpenIdConnect
from starlette.requests import Request

from app.lib import hacks
from app.lib import specs
from app.model.spc import Auth, AuthCORS


def _request(origin=None):
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})


# ----- openapi id-token hack -----

def _app_with_oidc():
    app = FastAPI()
    oidc = OpenIdConnect(openIdConnectUrl="https://localhost/.well-known/openid-configuration",
                         scheme_name="OpenID Connect")

    @app.get("/thing")
    async def _thing(token: str = Depends(oidc)):  # noqa: ANN001
        return {}

    return app


def test_openapi_injects_token_name_and_caches():
    app = _app_with_oidc()
    get_schema = hacks.get_openapi_schema_with_oidc_idtoken(app, "desc")

    schema = get_schema()
    scheme = schema["components"]["securitySchemes"]["OpenID Connect"]
    assert scheme["x-tokenName"] == "id_token"

    # second call returns the cached object (same identity)
    assert get_schema() is schema
    assert app.openapi_schema is schema


# ----- CORS header hack -----

@pytest.fixture
def allow_localhost(monkeypatch):
    monkeypatch.setattr(
        specs, "AUTH",
        Auth(cors=AuthCORS(origins=["https://allowed.example"])),
    )


def test_cors_always_sets_method_and_header_wildcards(allow_localhost):
    resp = hacks.add_cors_headers_to_response(_request(), JSONResponse({}))
    assert resp.headers["Access-Control-Allow-Methods"] == "*"
    assert resp.headers["Access-Control-Allow-Headers"] == "*"
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_echoes_allowed_origin(allow_localhost):
    resp = hacks.add_cors_headers_to_response(
        _request("https://allowed.example"), JSONResponse({})
    )
    assert resp.headers["Access-Control-Allow-Origin"] == "https://allowed.example"


def test_cors_omits_disallowed_origin(allow_localhost):
    resp = hacks.add_cors_headers_to_response(
        _request("https://evil.example"), JSONResponse({})
    )
    assert "Access-Control-Allow-Origin" not in resp.headers
