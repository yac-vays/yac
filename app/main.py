from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlparse
import logging
import os
import shutil
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import consts
from app.version import VERSION
from app.lib import repo
from app.lib import hacks
from app.lib import plugin
from app.lib import specs
from app.model.err import YACError
from app.router import arbitrary
from app.router import change
from app.router import create
from app.router import delete
from app.router import read
from app.router import status
from app.router import validate
from app.router import error

log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(
    plugin.get_module("format", consts.ENV.format_plugin).formatter
)

logging.basicConfig(level=consts.ENV.log_level.upper(), handlers=[log_handler])

logger = logging.getLogger(__name__)

#
# Startup / Shutdown
#

# TODO update docs to match the new j2-everywhere syntax (logs, actions, repo)

if consts.ENV.debug_mode:
    logger.warning(
        "DEBUG_MODE is enabled! This is DANGEROUS and will leak sensitive data"
        " to the user, so please only enable in development or testing environments!"
    )


def _cleanup_stale_repo_dirs() -> None:
    """
    Remove /repo/<pid> directories left behind by previous workers (e.g. after
    SIGKILL). The current worker keeps its own directory.
    """
    base = "/repo"
    if not os.path.isdir(base):
        return
    my_pid = str(os.getpid())
    for name in os.listdir(base):
        if name == my_pid or not name.isdigit():
            continue
        pid_path = f"/proc/{name}"
        if os.path.isdir(pid_path):
            continue  # owner still alive
        stale = os.path.join(base, name)
        try:
            shutil.rmtree(stale)
            logger.info(f"Cleaned up stale repo dir {stale}")
        except OSError as error:
            logger.warning(f"Could not clean up stale repo dir {stale}: {error}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    _cleanup_stale_repo_dirs()
    async with repo.handler.reader(None):
        pass  # only initiate repo
    yield
    # On clean shutdown, remove our own repo dir so it does not linger until
    # another worker happens to start and run the stale-dir sweep.
    own = f"/repo/{os.getpid()}"
    try:
        shutil.rmtree(own)
        logger.info(f"Cleaned up own repo dir {own}")
    except FileNotFoundError:
        pass
    except OSError as error:
        logger.warning(f"Could not clean up own repo dir {own}: {error}")


#
# API
#

DESCRIPTION = f"""

## Authentication

We are using the OpenID Connect provider
[{urlparse(specs.AUTH.oidc.url).netloc}]({specs.AUTH.oidc.url})
for authentication. You need to send a valid `id_token` via `Authorization: Bearer`
header to all API endpoints that require authentication.

Only the following `client_id`s are accepted:
`{'` `'.join(specs.AUTH.oidc.client_ids)}`

For interactive user logins, `authentication_code with PKCE` flow (with a
dummy `nonce` parameter that won't be validated) is recommended. For
automated login in scripts/software, use the `password` flow instead (therefore
you will also need the `client_secret`).

## Source, Issues and Documentation

Repository on [GitHub](https://github.com/yac-vays/yac)
"""

yac = FastAPI(
    lifespan=lifespan,
    title=consts.TITLE,
    description=DESCRIPTION,
    version=VERSION,
    root_path="" if consts.ENV.root_path == "/" else consts.ENV.root_path,
    contact=consts.CONTACT,
    license_info=consts.LICENSE,
    docs_url="/",
    redoc_url=None,
    swagger_ui_parameters={
        "displayRequestDuration": True,
        "persistAuthorization": True,
    },
    swagger_ui_init_oauth={
        "scopes": "openid",
        "clientId": specs.AUTH.oidc.client_ids[0] if specs.AUTH.oidc.client_ids else "",
        "usePkceWithAuthorizationCodeGrant": True,
        "additionalQueryStringParams": {"nonce": 0},
    },
)

yac.add_middleware(
    CORSMiddleware,
    allow_origins=specs.AUTH.cors.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yac.include_router(status.router, tags=["Status"])
yac.include_router(read.router, tags=["Entities"])
yac.include_router(create.router, tags=["Entities"])
yac.include_router(change.router, tags=["Entities"])
yac.include_router(delete.router, tags=["Entities"])
yac.include_router(arbitrary.router, tags=["Entities"])
yac.include_router(validate.router, tags=["Entities"])

yac.add_exception_handler(YACError, error.handle_yac)  # type: ignore
yac.add_exception_handler(500, error.handle_all)

yac.openapi = hacks.get_openapi_schema_with_oidc_idtoken(yac, DESCRIPTION)
