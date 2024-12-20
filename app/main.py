from contextlib import asynccontextmanager
from typing import AsyncIterator
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import consts
from app.version import VERSION
from app.lib import repo
from app.lib import hacks
from app.model.err import YACError
from app.router import arbitrary
from app.router import change
from app.router import create
from app.router import delete
from app.router import read
from app.router import status
from app.router import validate
from app.router import error

# TODO nice json logger
logging.basicConfig(
    level=consts.ENV.log_level.upper(),
    format="[%(asctime)-s] %(levelname)-7s %(message)s",
)

logger = logging.getLogger(__name__)

#
# Startup / Shutdown
#

# TODO update all yac specs to match the new j2-everywhere syntax (logs, actions and maybe repo)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    async with repo.handler.reader(None, details={}):
        pass  # only initiate repo
    yield
    # nothing to do on worker shutdown


#
# API
#

yac = FastAPI(
    title=consts.TITLE,
    description=consts.DESCRIPTION,
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
        "clientId": consts.ENV.oidc_client_ids.split(",", maxsplit=1)[0],
        "usePkceWithAuthorizationCodeGrant": True,
        "additionalQueryStringParams": {"nonce": 0},
    },
)

yac.add_middleware(
    CORSMiddleware,
    allow_origins=consts.ENV.cors_origins.split(","),
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

yac.openapi = hacks.get_openapi_schema_with_oidc_idtoken(yac)
