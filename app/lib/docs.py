"""
The interactive API documentation (Swagger UI) at `/`.

FastAPI's built-in docs page loads the Swagger UI script and stylesheet from a
public CDN. That makes a third party able to run script on the API origin
(where a token may be stored, see `persistAuthorization`), and the page
useless without internet access. So the assets are served by YAC itself: the
Docker image contains a pinned, checksum-verified copy of `swagger-ui-dist`
(see the Dockerfile) in `SWAGGER_UI_DIR`.

Without that directory (YAC started from a source checkout), the page falls
back to FastAPI's defaults, i.e. the CDN -- a development convenience only.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

SWAGGER_UI_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "swagger-ui"
SWAGGER_UI_FILES = ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png")

STATIC_URL = "/static/swagger-ui"
OAUTH2_REDIRECT_URL = "/docs/oauth2-redirect"


def is_bundled(directory: Path | None = None) -> bool:
    directory = SWAGGER_UI_DIR if directory is None else directory
    return all((directory / name).is_file() for name in SWAGGER_UI_FILES)


def setup(app: FastAPI, directory: Path | None = None) -> None:
    """
    Adds the docs page at `/` (plus its OAuth2 redirect page and, if bundled,
    the static assets). The app must be created with `docs_url=None`.
    """
    directory = SWAGGER_UI_DIR if directory is None else directory
    bundled = is_bundled(directory)
    if bundled:
        # A plain route with an allow-list instead of a StaticFiles mount:
        # nothing but these files is ever served, and it works the same
        # whether or not a proxy strips the root path.
        @app.get(STATIC_URL + "/{name}", include_in_schema=False)
        async def swagger_ui_asset(name: str) -> FileResponse:
            if name not in SWAGGER_UI_FILES:
                raise HTTPException(status_code=404)
            return FileResponse(
                directory / name,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    else:
        logger.warning(
            f"No Swagger UI assets in {directory}: the API docs page loads"
            " them from a public CDN (fine for development, not for production)"
        )

    @app.get("/", include_in_schema=False)
    async def swagger_ui_html(request: Request) -> HTMLResponse:
        root_path = request.scope.get("root_path", "").rstrip("/")
        assets = {}
        if bundled:
            assets = {
                "swagger_js_url": f"{root_path}{STATIC_URL}/swagger-ui-bundle.js",
                "swagger_css_url": f"{root_path}{STATIC_URL}/swagger-ui.css",
                "swagger_favicon_url": f"{root_path}{STATIC_URL}/favicon-32x32.png",
            }
        return get_swagger_ui_html(
            openapi_url=f"{root_path}{app.openapi_url}",
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=f"{root_path}{OAUTH2_REDIRECT_URL}",
            init_oauth=app.swagger_ui_init_oauth,
            swagger_ui_parameters=app.swagger_ui_parameters,
            **assets,
        )

    @app.get(OAUTH2_REDIRECT_URL, include_in_schema=False)
    async def swagger_ui_redirect() -> HTMLResponse:
        return get_swagger_ui_oauth2_redirect_html()
