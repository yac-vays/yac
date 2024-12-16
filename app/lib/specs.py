"""
Raises: [app.model.err.SpecsError]
"""

import logging
from functools import lru_cache

from pydantic import ValidationError
from anyio import Path, open_file

from app import consts
from app.lib import j2
from app.lib import props
from app.lib import yaml
from app.model.err import RepoError
from app.model.err import SpecsError
from app.model.rpo import Repo
from app.model.spc import Request
from app.model.spc import Specs
from app.model.inp import OperationRequest

logger = logging.getLogger(__name__)

# TODO add (async) way to cache specs
# TODO check major version of specs (must match!)


def in_repo() -> bool:
    return consts.ENV.specs.startswith(".")


async def read_from_repo(rpo: Repo, op: OperationRequest) -> Specs:
    try:
        data = await rpo.get_specs(consts.ENV.specs)
    except RepoError as error:
        raise SpecsError(
            f"Could not read specs from repo at {consts.ENV.specs}"
        ) from error
    return __parse(data, op)


async def read_from_file(op: OperationRequest) -> Specs:
    return __parse(await __read_file(), op)


@lru_cache(maxsize=1)
async def __read_file():
    try:
        async with await open_file(
            consts.ENV.specs, mode="r", encoding="utf-8"
        ) as file:
            logger.debug(f"Reading file {consts.ENV.specs}")
            return await file.read()
    except OSError as error:
        raise SpecsError(
            f"Could not read specs from file at {consts.ENV.specs}"
        ) from error


def __parse(specs: str, op: OperationRequest) -> Specs:
    try:
        data = yaml.load_as_dict(specs, strict=False)
    except yaml.YAMLError as error:
        raise SpecsError(f"In YAML syntax: {error}") from error

    try:
        data["request"] = j2.render(
            data.get("request", {"headers": {}}), props.get_request()
        )
        request = Request.model_validate(data["request"])
    except j2.J2Error as error:
        raise SpecsError(f"In request at {error.loc}: {error}") from error
    except ValidationError as error:
        raise SpecsError(f"In request: {error}") from error

    try:
        # TODO skip rendering log and action details to allow handling within the plugins
        data["types"] = j2.render(data.get("types", []), props.get_types(op, request))
        data["type"] = next(
            (t for t in data["types"] if t.get("name", "") == op.type_name), None
        )
    except j2.J2Error as error:
        raise SpecsError(f"In types at {error.loc}: {error}") from error

    try:
        return Specs.model_validate(data)
    except ValidationError as error:
        raise SpecsError(str(error)) from error
