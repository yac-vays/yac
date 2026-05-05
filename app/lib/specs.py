"""
Raises: [app.model.err.SpecsError]
"""

import logging
import re
from typing import Any
from os.path import dirname, abspath, realpath

from pydantic import ValidationError
from anyio import open_file

# from async_lru import alru_cache

from app import consts
from app.version import VERSION
from app.lib import j2
from app.lib import props
from app.lib import yaml
from app.model.err import RepoError
from app.model.err import SpecsError
from app.model.plg import IRepo
from app.model.spc import Request
from app.model.spc import Specs
from app.model.inp import OperationRequest

logger = logging.getLogger(__name__)


# TODO add (async) way to cache specs -> requires redesign of op
async def read(op: OperationRequest, rpo: IRepo) -> Specs:
    if in_repo():
        s = await read_from_repo(op, rpo)
    else:
        s = await read_from_file(op)

    if s.type is not None:
        await rpo.update_details(s.repo.details)

    return s


def in_repo() -> bool:
    return consts.ENV.specs.startswith(".")


async def read_from_repo(op: OperationRequest, rpo: IRepo) -> Specs:
    try:
        data = await rpo.get_specs(consts.ENV.specs)
    except RepoError as error:
        raise SpecsError(
            f"Could not read specs from repo at {consts.ENV.specs}"
        ) from error
    return await __parse(data, op)


async def read_from_file(op: OperationRequest) -> Specs:
    return await __parse(await __read_file(), op)


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


async def __process_includes(data: Any, base_path: str) -> Any:
    if isinstance(data, dict):
        if not "yac_include" in data:
            return data

        includes = data.pop("yac_include")
        if isinstance(includes, str):
            includes = [includes]

        base_real = realpath(abspath(base_path))

        for inc_file in includes:
            inc_file_path = f"{base_path}/{inc_file}"
            inc_real = realpath(abspath(inc_file_path))

            if inc_real != base_real and not inc_real.startswith(base_real + "/"):
                raise SpecsError(
                    f"Included specs file at {inc_file_path} is outside of the"
                    f" specs base directory {base_path}"
                )

            try:
                async with await open_file(
                    inc_real, mode="r", encoding="utf-8"
                ) as file:
                    logger.debug(f"Reading file {inc_real}")
                    content = await file.read()
            except OSError as error:
                raise SpecsError(
                    f"Could not read included specs file at {inc_file_path}"
                ) from error

            try:
                inc_data = yaml.load_as_dict(content, strict=False)
            except yaml.YAMLError as error:
                raise SpecsError(
                    f"In YAML syntax of {inc_file_path}: {error}"
                ) from error

            inc_data = await __process_includes(inc_data, dirname(inc_file_path))

            if isinstance(inc_data, dict):
                for key, value in inc_data.items():
                    if not key in data:
                        data[key] = value
            else:
                if len(data) > 0:
                    logger.warning(
                        f"Cannot merge {type(data)} from yac_include of"
                        f" {inc_file_path} into an object"
                    )
                    continue
                if len(includes) > 1:
                    logger.warning(
                        f"Cannot merge {type(data)} from yac_include of"
                        f" {inc_file_path} into other non-object types"
                    )
                    continue
                data = inc_data

        for key, value in list(data.items()):
            data[key] = await __process_includes(value, base_path)

    elif isinstance(data, list):
        return [await __process_includes(item, base_path) for item in data]

    return data


async def __parse(specs: str, op: OperationRequest) -> Specs:
    try:
        data = yaml.load_as_dict(specs, strict=False)
    except yaml.YAMLError as error:
        raise SpecsError(f"In YAML syntax of {consts.ENV.specs}: {error}") from error

    data = await __process_includes(data, dirname(consts.ENV.specs))

    try:
        assert isinstance(data, dict)
    except AssertionError as error:
        raise SpecsError(f"Must be an object!") from error

    try:
        data["request"] = await j2.render(
            data.get("request", {"headers": {}}), props.get_request()
        )
        request = Request.model_validate(data["request"])
    except j2.J2Error as error:
        raise SpecsError(f"In request at {error.loc}: {error}") from error
    except ValidationError as error:
        raise SpecsError(f"In request: {error}") from error

    try:
        data["types"] = await j2.render(
            data.get("types", []),
            props.get_types(op, request),
            skip=r"^#/\d+/(name_generator|(logs|actions)/\d+/details/.*)$",
        )
        data["type"] = next(
            (
                t
                for t in data["types"]
                if isinstance(t, dict) and t.get("name", "") == op.type_name
            ),
            None,
        )
    except j2.J2Error as error:
        raise SpecsError(f"In types at {error.loc}: {error}") from error

    try:
        data["repo"] = await j2.render(
            data.get("repo", {}),
            props.get_types(op, request),
            skip=r"^#/details/.*$",
        )
    except j2.J2Error as error:
        raise SpecsError(f"In repo at {error.loc}: {error}") from error

    try:
        s = Specs.model_validate(data)
    except ValidationError as error:
        raise SpecsError(str(error)) from error

    if s.version is None or not re.match(rf"^{s.version}\.[0-9]+(rc[0-9]+)?$", VERSION):
        raise SpecsError(
            f"In version: {s.version} is not compatibale with YAC {VERSION}"
        )

    return s
