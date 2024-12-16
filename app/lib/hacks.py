from typing import Callable

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi

from app import consts
from app.version import VERSION


def get_openapi_schema_with_oidc_idtoken(app: FastAPI) -> Callable:
    """
    Hack from: https://github.com/fastapi/fastapi/discussions/8557
    """

    def get_schema():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=consts.TITLE,
            description=consts.DESCRIPTION,
            version=VERSION,
            contact=consts.CONTACT,
            license_info=consts.LICENSE,
            routes=app.routes,
        )
        openapi_schema["components"]["securitySchemes"]["OpenID Connect"][
            "x-tokenName"
        ] = "id_token"
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return get_schema


def add_cors_headers_to_response(
    request: Request, response: JSONResponse
) -> JSONResponse:
    """
    CORS headers are not automatically added to error handlers with FastAPI.
    Hack from: https://github.com/fastapi/fastapi/discussions/8027
    """

    response.headers.update(
        {
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Origin": consts.ENV.cors_origins,
        }
    )
    return response
