from typing import Callable

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from app.lib import specs


def get_openapi_schema_with_oidc_idtoken(app: FastAPI) -> Callable:
    """
    Makes Swagger UI send the id_token instead of the access_token.
    Hack from: https://github.com/fastapi/fastapi/discussions/8557
    """
    default = app.openapi

    def get_schema():
        schema = default()  # generated once, cached on the app
        schema["components"]["securitySchemes"]["OpenID Connect"][
            "x-tokenName"
        ] = "id_token"
        return schema

    return get_schema


def add_cors_headers_to_response(
    request: Request, response: JSONResponse
) -> JSONResponse:
    """
    The handler of unexpected exceptions (500) runs outside the CORS
    middleware, so its response gets no CORS headers on its own.
    Hack from: https://github.com/fastapi/fastapi/discussions/8027
    """
    response.headers.update(
        {
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true",
        }
    )

    origin = request.headers.get("origin")
    if origin in specs.AUTH.cors.origins:
        response.headers.update(
            {
                "Access-Control-Allow-Origin": origin,
            }
        )

    return response
