"""
Raises: [app.lib.j2.J2Error, app.model.err.RequestError]
"""

import json
import re

import jinja2

from app.lib import plugin
from app.model.err import RequestError


class J2Error(Exception):
    def __init__(self, msg: str, *, loc: str = "#"):
        super().__init__(msg)
        self.loc = loc


async def render(
    o, props: dict, *, skip: list[str] = [], strict: bool = True, loc: str = "#"
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
    nonstr = re.match(r"^(\{\{|\{%).+(\}\}|%\})$", s) and allow_nonstr
    j2 = jinja2.Environment(
        enable_async=True,
        loader=jinja2.BaseLoader(),
        undefined=jinja2.StrictUndefined if strict else jinja2.DebugUndefined,
        trim_blocks=not nonstr,
        finalize=json.dumps if nonstr else None,
    )
    j2.globals.update(plugin.get_functions("j2_functions"))
    j2.filters.update(plugin.get_functions("j2_filters"))
    j2.tests.update(plugin.get_functions("j2_tests"))
    try:
        result = await j2.from_string(s).render_async({"loc": loc} | props)
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
    d, props, *, skip: list[str], strict: bool = True, loc: str = "#"
):
    r = {}
    for k, v in d.items():
        k_loc = f"{loc}/{k}"
        if k in skip:
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
    l, props, *, skip: list[str], strict: bool = True, loc: str = "#"
):
    r = []
    for v in l:
        v_loc = f"{loc}/{len(r)}"
        if isinstance(v, dict):
            r.append(await __render_dict(v, props, skip=skip, strict=strict, loc=v_loc))
        elif isinstance(v, list):
            r.append(await __render_list(v, props, skip=skip, strict=strict, loc=v_loc))
        elif isinstance(v, str):
            r.append(await render_str(v, props, strict=strict, loc=v_loc))
        else:
            r.append(v)
    return r
