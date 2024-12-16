"""
Raises: [app.lib.j2.J2Error]
"""

import json
import re

import jinja2

from app.lib import plugin


class J2Error(Exception):
    loc = "#"


def render(
    o, props: dict = {}, *, strict: bool = True
):  # pylint: disable=dangerous-default-value
    if isinstance(o, dict):
        return __render_dict(o, props, strict=strict)
    if isinstance(o, list):
        return __render_list(o, props, strict=strict)
    if isinstance(o, str):
        return render_str(o, props, strict=strict)
    return o


def render_file(path, file, props, *, strict: bool = True):
    j2 = jinja2.Environment(
        loader=jinja2.FileSystemLoader(path),
        undefined=jinja2.StrictUndefined if strict else jinja2.DebugUndefined,
        trim_blocks=True,
    )
    j2.globals.update(plugin.get_functions("j2_functions"))
    j2.filters.update(plugin.get_functions("j2_filters"))
    j2.tests.update(plugin.get_functions("j2_tests"))
    try:
        result = j2.get_template(file).render(props)
    except (jinja2.exceptions.TemplateError, Exception) as error:
        # Must expect any Exception from plugins!
        raise J2Error(f"Templating file {path}/{file} failed with: {error}") from error
    return result


def render_test(
    test_str: str, props: dict = {}
) -> bool:  # pylint: disable=dangerous-default-value
    return bool(render_str(f"{{{{ {test_str} }}}}", props, allow_nonstr=True))


def render_print(
    print_str: str, props: dict = {}, *, strict: bool = True
) -> str:  # pylint: disable=dangerous-default-value
    return str(
        render_str(f"{{{{ {print_str} }}}}", props, allow_nonstr=False, strict=strict)
    )


def render_str(s, props, *, allow_nonstr: bool = True, strict: bool = True):
    nonstr = re.match(r"^\{\{.+\}\}$", s) and allow_nonstr
    j2 = jinja2.Environment(
        loader=jinja2.BaseLoader(),
        undefined=jinja2.StrictUndefined if strict else jinja2.DebugUndefined,
        trim_blocks=not nonstr,
        finalize=json.dumps if nonstr else None,
    )
    j2.globals.update(plugin.get_functions("j2_functions"))
    j2.filters.update(plugin.get_functions("j2_filters"))
    j2.tests.update(plugin.get_functions("j2_tests"))
    try:
        result = j2.from_string(s).render(props)
    except (jinja2.exceptions.UndefinedError, Exception) as error:
        # Must expect any Exception from plugins!
        raise J2Error(f'Templating str "{s}" failed with: {error}') from error
    if nonstr:
        try:
            return json.loads(result)
        except ValueError as error:
            raise J2Error(
                f'Templating str "{s}" caused a value error: {error}'
            ) from error
    else:
        return result


def __render_dict(d, props, *, strict: bool = True):
    r = {}
    for k, v in d.items():
        try:
            if isinstance(v, dict):
                r.update({k: __render_dict(v, props, strict=strict)})
            elif isinstance(v, list):
                r.update({k: __render_list(v, props, strict=strict)})
            elif isinstance(v, str):
                r.update({k: render_str(v, props, strict=strict)})
            else:
                r.update({k: v})
        except J2Error as error:
            error.loc = f"{error.loc}/{k}"
            raise error
    return r


def __render_list(l, props, *, strict: bool = True):
    r = []
    for v in l:
        try:
            if isinstance(v, dict):
                r.append(__render_dict(v, props, strict=strict))
            elif isinstance(v, list):
                r.append(__render_list(v, props, strict=strict))
            elif isinstance(v, str):
                r.append(render_str(v, props, strict=strict))
            else:
                r.append(v)
        except J2Error as error:
            error.loc = f"{error.loc}/{v}"
            raise error
    return r
