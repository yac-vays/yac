"""
Raises: [app.lib.j2.J2Error, app.model.err.RequestError]
"""

import json
import re
from functools import lru_cache

import jinja2
from jinja2.sandbox import SandboxedEnvironment

from app.consts import OMIT
from app.lib import plugin
from app.model.err import RequestError


class J2Error(Exception):
    def __init__(self, msg: str, *, loc: str = "#"):
        super().__init__(msg)
        self.loc = loc


# A SandboxedEnvironment is built once per (strict, nonstr) combination and
# the plugin globals/filters/tests are registered once on first use. Compiled
# templates are LRU-cached by source per environment.

_ENV_VARIANTS: dict[tuple[bool, bool], SandboxedEnvironment] = {}
_envs_initialized = False


def _build_env(strict: bool, nonstr: bool) -> SandboxedEnvironment:
    return SandboxedEnvironment(
        enable_async=True,
        loader=jinja2.BaseLoader(),
        undefined=jinja2.StrictUndefined if strict else jinja2.DebugUndefined,
        trim_blocks=not nonstr,
        finalize=json.dumps if nonstr else None,
    )


def _ensure_envs_initialized() -> None:
    global _envs_initialized
    if _envs_initialized:
        return
    j2_functions = plugin.get_functions("j2_functions")
    j2_filters = plugin.get_functions("j2_filters")
    j2_tests = plugin.get_functions("j2_tests")
    for strict in (True, False):
        for nonstr in (True, False):
            env = _build_env(strict, nonstr)
            env.globals.update(j2_functions)
            env.globals["omit"] = OMIT
            env.filters.update(j2_filters)
            env.tests.update(j2_tests)
            _ENV_VARIANTS[(strict, nonstr)] = env
    _envs_initialized = True


def _get_env(strict: bool, nonstr: bool) -> SandboxedEnvironment:
    if not _envs_initialized:
        _ensure_envs_initialized()
    return _ENV_VARIANTS[(strict, nonstr)]


@lru_cache(maxsize=10000)
def _get_template(strict: bool, nonstr: bool, source: str):
    return _get_env(strict, nonstr).from_string(source)


# Sync variant for startup-time rendering of small env-only blocks (auth,
# repo.plugin, repo.connection). Intentionally minimal: no plugin
# functions/filters/tests are registered — the only thing beyond plain
# Jinja is `omit`. This keeps the early surface free of async code so the
# FastAPI app can be constructed before its lifespan runs.

_SYNC_ENV_VARIANTS: dict[bool, SandboxedEnvironment] = {}


def _build_sync_env(nonstr: bool) -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        loader=jinja2.BaseLoader(),
        undefined=jinja2.StrictUndefined,
        trim_blocks=not nonstr,
        finalize=json.dumps if nonstr else None,
    )
    env.globals["omit"] = OMIT
    return env


def _get_sync_env(nonstr: bool) -> SandboxedEnvironment:
    if nonstr not in _SYNC_ENV_VARIANTS:
        _SYNC_ENV_VARIANTS[nonstr] = _build_sync_env(nonstr)
    return _SYNC_ENV_VARIANTS[nonstr]


@lru_cache(maxsize=1000)
def _get_sync_template(nonstr: bool, source: str):
    return _get_sync_env(nonstr).from_string(source)


async def render(
    o, props: dict, *, skip: str | None = None, strict: bool = True, loc: str = "#"
):
    if isinstance(o, dict):
        return await __render_dict(o, props, skip=skip, strict=strict, loc=loc)
    if isinstance(o, list):
        return await __render_list(o, props, skip=skip, strict=strict, loc=loc)
    if isinstance(o, str):
        return await render_str(o, props, strict=strict, loc=loc)
    return o


async def render_test(test_str: str, props: dict) -> bool:
    return bool(await render_str(f"{{{{ {test_str} }}}}", props, allow_nonstr=True))


async def render_print(print_str: str, props: dict, *, strict: bool = True) -> str:
    return str(
        await render_str(
            f"{{{{ {print_str} }}}}", props, allow_nonstr=False, strict=strict
        )
    )


async def render_str(
    s, props, *, allow_nonstr: bool = True, strict: bool = True, loc: str = "#"
):
    nonstr = bool(re.match(r"^(\{\{|\{%).+(\}\}|%\})$", s)) and allow_nonstr
    template = _get_template(strict, nonstr, s)
    try:
        result = await template.render_async({"loc": loc} | props)
    except RequestError as error:
        # Allow plugins to generate user errors
        raise error
    except (jinja2.exceptions.UndefinedError, Exception) as error:
        # Must expect any Exception from plugins!
        raise J2Error(f'Templating str "{s}" failed with: {error}', loc=loc) from error
    if nonstr:
        try:
            return json.loads(result)
        except ValueError as error:
            raise J2Error(
                f'Templating str "{s}" caused a value error: {error}', loc=loc
            ) from error
    else:
        return result


async def __render_dict(
    d, props, *, skip: str | None, strict: bool = True, loc: str = "#"
):
    r = {}
    for k, v in d.items():
        k_loc = f"{loc}/{k}"
        if skip and re.match(skip, k_loc):
            r.update({k: v})
        elif isinstance(v, dict):
            r.update(
                {k: await __render_dict(v, props, skip=skip, strict=strict, loc=k_loc)}
            )
        elif isinstance(v, list):
            r.update(
                {k: await __render_list(v, props, skip=skip, strict=strict, loc=k_loc)}
            )
        elif isinstance(v, str):
            r.update({k: await render_str(v, props, strict=strict, loc=k_loc)})
        else:
            r.update({k: v})
    return r


async def __render_list(
    l, props, *, skip: str | None, strict: bool = True, loc: str = "#"
):
    r = []
    for v in l:
        v_loc = f"{loc}/{len(r)}"
        if skip and re.match(skip, v_loc):
            r.append(v)
        elif isinstance(v, dict):
            r.append(await __render_dict(v, props, skip=skip, strict=strict, loc=v_loc))
        elif isinstance(v, list):
            r.append(await __render_list(v, props, skip=skip, strict=strict, loc=v_loc))
        elif isinstance(v, str):
            r.append(await render_str(v, props, strict=strict, loc=v_loc))
        else:
            r.append(v)
    return r


def render_sync(o, props: dict, *, skip: str | None = None, loc: str = "#"):
    """
    Synchronous rendering for startup-only blocks (auth, repo.plugin,
    repo.connection). Plugin filters/functions/tests are NOT available
    here — only basic Jinja plus `omit`. Always strict.
    """
    if isinstance(o, dict):
        return __render_sync_dict(o, props, skip=skip, loc=loc)
    if isinstance(o, list):
        return __render_sync_list(o, props, skip=skip, loc=loc)
    if isinstance(o, str):
        return render_sync_str(o, props, loc=loc)
    return o


def render_sync_str(s, props, *, allow_nonstr: bool = True, loc: str = "#"):
    nonstr = bool(re.match(r"^(\{\{|\{%).+(\}\}|%\})$", s)) and allow_nonstr
    template = _get_sync_template(nonstr, s)
    try:
        result = template.render({"loc": loc} | props)
    except (jinja2.exceptions.UndefinedError, Exception) as error:
        raise J2Error(f'Templating str "{s}" failed with: {error}', loc=loc) from error
    if nonstr:
        try:
            return json.loads(result)
        except ValueError as error:
            raise J2Error(
                f'Templating str "{s}" caused a value error: {error}', loc=loc
            ) from error
    return result


def __render_sync_dict(d, props, *, skip: str | None, loc: str = "#"):
    r = {}
    for k, v in d.items():
        k_loc = f"{loc}/{k}"
        if skip and re.match(skip, k_loc):
            r[k] = v
        elif isinstance(v, dict):
            r[k] = __render_sync_dict(v, props, skip=skip, loc=k_loc)
        elif isinstance(v, list):
            r[k] = __render_sync_list(v, props, skip=skip, loc=k_loc)
        elif isinstance(v, str):
            r[k] = render_sync_str(v, props, loc=k_loc)
        else:
            r[k] = v
    return r


def __render_sync_list(l, props, *, skip: str | None, loc: str = "#"):
    r = []
    for v in l:
        v_loc = f"{loc}/{len(r)}"
        if skip and re.match(skip, v_loc):
            r.append(v)
        elif isinstance(v, dict):
            r.append(__render_sync_dict(v, props, skip=skip, loc=v_loc))
        elif isinstance(v, list):
            r.append(__render_sync_list(v, props, skip=skip, loc=v_loc))
        elif isinstance(v, str):
            r.append(render_sync_str(v, props, loc=v_loc))
        else:
            r.append(v)
    return r
