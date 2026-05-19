"""
The specs file is loaded once at module import. Updating it requires
a process restart. The repo plugin and its connection config also live
inside the specs file (`repo.plugin`, `repo.connection`) and are read
from the raw, untemplated YAML at import time.

Raises: [app.model.err.SpecsError]
"""

import asyncio
import copy
import hashlib
import logging
import re
import sys
from typing import Any
from os.path import dirname, abspath, realpath

from pydantic import ValidationError

from app import consts
from app.version import VERSION
from app.lib import j2
from app.lib import props
from app.lib import yaml
from app.lib.cache import keyed_alru_cache, stable_key
from app.model.err import SpecsError
from app.model.spc import Auth
from app.model.spc import Request
from app.model.spc import Specs
from app.model.inp import OperationRequest

logger = logging.getLogger(__name__)


# JWT claims that change on every token rotation but never affect spec
# rendering decisions. Excluding them lets the specs cache (and downstream
# perms cache) hit across token refreshes within a single user session.
# If a spec template legitimately needs a volatile claim, the TTL on the
# cache (1h) limits staleness anyway.
_VOLATILE_TOKEN_CLAIMS = frozenset(
    {
        "iat",
        "exp",
        "nbf",
        "jti",
        "auth_time",
        "sid",
        "session_state",
        "nonce",
        "at_hash",
        "c_hash",
    }
)


def _stable_token(token: dict | None) -> dict:
    if not token:
        return {}
    return {k: v for k, v in token.items() if k not in _VOLATILE_TOKEN_CLAIMS}


def _op_signature(op: OperationRequest) -> tuple:
    # Fields that influence specs rendering (request templating uses
    # request_headers; types/repo blocks use user + request).
    user = dict(op.user)
    user["token"] = _stable_token(user.get("token"))
    return (
        op.type_name,
        op.operation,
        tuple(op.actions),
        op.request_ip,
        stable_key(op.request_headers),
        stable_key(user),
    )


def _load_text_sync(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return file.read()
    except OSError as error:
        logger.critical(f"Could not read specs from file at {path}: {error}")
        sys.exit(1)


def _process_includes_sync(data: Any, base_path: str) -> Any:
    if isinstance(data, dict):
        if "yac_include" in data:
            includes = data.pop("yac_include")
            if isinstance(includes, str):
                includes = [includes]

            base_real = realpath(abspath(base_path))

            for inc_file in includes:
                inc_file_path = f"{base_path}/{inc_file}"
                inc_real = realpath(abspath(inc_file_path))

                if inc_real != base_real and not inc_real.startswith(base_real + "/"):
                    logger.critical(
                        f"Included specs file at {inc_file_path} is outside of"
                        f" the specs base directory {base_path}"
                    )
                    sys.exit(1)

                try:
                    with open(inc_real, "r", encoding="utf-8") as file:
                        content = file.read()
                except OSError as error:
                    logger.critical(
                        f"Could not read included specs file at"
                        f" {inc_file_path}: {error}"
                    )
                    sys.exit(1)

                try:
                    inc_data = yaml.load_as_dict(content, strict=False)
                except yaml.YAMLError as error:
                    logger.critical(
                        f"In YAML syntax of {inc_file_path}: {error}"
                    )
                    sys.exit(1)

                inc_data = _process_includes_sync(inc_data, dirname(inc_file_path))

                if isinstance(inc_data, dict):
                    for key, value in inc_data.items():
                        if key not in data:
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
            data[key] = _process_includes_sync(value, base_path)

    elif isinstance(data, list):
        return [_process_includes_sync(item, base_path) for item in data]

    return data


def _load_raw_data() -> tuple[dict, str]:
    """
    Load the static specs file and process `yac_include`. The returned
    dict has not been j2-rendered yet (rendering happens per request in
    `read()`). The hash is a stable digest of the post-include text.
    """
    text = _load_text_sync(consts.ENV.specs)
    try:
        data = yaml.load_as_dict(text, strict=False)
    except yaml.YAMLError as error:
        logger.critical(f"In YAML syntax of {consts.ENV.specs}: {error}")
        sys.exit(1)

    if not isinstance(data, dict):
        logger.critical(f"Specs file at {consts.ENV.specs} must be an object")
        sys.exit(1)

    data = _process_includes_sync(data, dirname(consts.ENV.specs))
    data_hash = hashlib.sha1(repr(stable_key(data)).encode("utf-8")).hexdigest()
    return data, data_hash


# Loaded once at process startup. Subsequent requests reuse this raw dict.
_RAW_DATA, _RAW_HASH = _load_raw_data()


def _render_at_startup(
    block: Any, props_: dict, section: str, *, skip: str | None = None
) -> Any:
    """
    Render `block` once at startup. Fails fast on template errors — the
    section is static and the pod should not come up with a broken config.
    """
    if not block:
        return block
    try:
        return asyncio.run(j2.render(block, props_, skip=skip))
    except j2.J2Error as error:
        logger.critical(f"In {section} at {error.loc}: {error}")
        sys.exit(1)


# `repo.details` is rendered per-call inside the plugin (it carries
# `{{ name }}` and plugin-specific vars); everything else in `repo`
# uses the same env-only scope as `props.get_repo()`.
_STATIC_REPO: dict = _render_at_startup(
    _RAW_DATA.get("repo", {}) or {},
    props.get_repo(),
    section=f"repo section of {consts.ENV.specs}",
    skip=r"^#/details$",
)
_STATIC_AUTH_DATA: dict = _render_at_startup(
    _RAW_DATA.get("auth", {}) or {},
    props.get_auth(),
    section=f"auth section of {consts.ENV.specs}",
)


def get_repo_plugin() -> str:
    """Name of the repo plugin selected in the specs file."""
    return _STATIC_REPO.get("plugin", "git_direct")


def get_repo_connection() -> dict:
    """Static connection config for the selected repo plugin."""
    return _STATIC_REPO.get("connection", {}) or {}


def _load_auth() -> Auth:
    try:
        return Auth.model_validate(_STATIC_AUTH_DATA)
    except ValidationError as error:
        logger.critical(f"In auth section of {consts.ENV.specs}: {error}")
        sys.exit(1)


# Authentication / CORS config — static for the lifetime of the process.
# Validated once at import (fail-fast on a malformed `auth` block) so all
# subsequent reads are plain attribute accesses.
AUTH: Auth = _load_auth()


async def read(op: OperationRequest) -> Specs:
    op_sig = _op_signature(op)
    return await __parse_cached(op, op_sig)


@keyed_alru_cache(
    key_fn=lambda op, op_sig: op_sig,
    maxsize=128,
    ttl=3600,
)
async def __parse_cached(op: OperationRequest, op_sig: tuple) -> Specs:
    # The cached Specs is treated as read-only by all callers; do not mutate.
    s = await __parse(op)
    # Stamp signature so downstream caches (perms etc.) can key on it.
    s._signature = f"{_RAW_HASH}:{hash(op_sig)}"
    return s


async def __parse(op: OperationRequest) -> Specs:
    # Deep-copy so subsequent rendering/validation cannot mutate the shared
    # `_RAW_DATA` (which is reused across all requests).
    data = copy.deepcopy(_RAW_DATA)

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

    # `repo` and `auth` are rendered once at module-import with env-only
    # props (see `_STATIC_REPO` / `_STATIC_AUTH_DATA`). Reuse the rendered
    # versions here so the parsed `Specs` is consistent with what the repo
    # plugin and the global `AUTH` constant actually use. `repo.details`
    # remains raw — it carries `{{ name }}` templates that are rendered
    # per-call inside the plugin.
    data["repo"] = copy.deepcopy(_STATIC_REPO)
    data["auth"] = copy.deepcopy(_STATIC_AUTH_DATA)

    try:
        s = Specs.model_validate(data)
    except ValidationError as error:
        raise SpecsError(str(error)) from error

    if s.version is None or not re.match(rf"^{s.version}\.[0-9]+(rc[0-9]+)?$", VERSION):
        raise SpecsError(
            f"In version: {s.version} is not compatibale with YAC {VERSION}"
        )

    return s
