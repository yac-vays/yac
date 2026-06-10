"""
Raises: [app.model.err.SchemaSpecsError]
"""

import logging
import re
from typing import Any

import jsonschema

from app.lib import j2
from app.lib import locs
from app.lib import plugin
from app.lib import props
from app.lib.locs import SUBSCHEMAS
from app.lib.locs import SUBSCHEMA_ARRAYS
from app.lib.locs import SUBSCHEMA_OBJECTS
from app.model import inp
from app.model import out
from app.model import spc
from app.model.err import SchemaSpecsError

logger = logging.getLogger(__name__)


def find_removed_violation(
    removed: list[re.Pattern], old_data: dict, new_data: dict
) -> str | None:
    """
    Permission-removed subschemas (recorded by plugin/json_schema/yac_perms.py
    as compiled data-loc regexes in the shared plugin context under
    `yac_perms_removed`) cannot be enforced *inside* the schema without
    leaking the stored value (a `const` would echo it back). So the write is
    enforced here, outside the schema: any difference between the stored and
    the incoming data at a removed data path — changing, deleting or newly
    setting a value — is a permission violation.

    Returns the first violating data loc, or None if the data at all removed
    paths is unchanged.
    """
    if not removed:
        return None

    data_locs = set(locs.get(old_data, lambda d: True))
    data_locs.update(locs.get(new_data, lambda d: True))

    for data_loc in sorted(data_locs):
        if not any(r.match(data_loc) for r in removed):
            continue
        if locs.extract(data_loc, old_data) != locs.extract(data_loc, new_data):
            return data_loc
    return None


async def get(
    op: inp.OperationRequest,
    schema_spec: spc.Schema,
    request_spec: spc.Request,
    context: dict,
    old_data: dict,
    perms: list[str],
    new_data: dict,
) -> out.Schema:
    schema_props = props.get_schema(
        op, request_spec, old_data, perms, new_data, context
    )

    try:
        json_schema = await j2.render(
            dict(schema_spec),
            schema_props,
            skip=rf"^.*/(({'|'.join(SUBSCHEMAS)})|({'|'.join(SUBSCHEMA_ARRAYS)})/\d+|({'|'.join(SUBSCHEMA_OBJECTS)})/[^/]+)/description$",
        )
    except j2.J2Error as error:
        raise SchemaSpecsError(f"{error.loc}: {error}") from error

    json_schema, ui_schema, cx = await handle_schema(
        "#", json_schema, {}, {}, schema_props
    )

    # convert trivial cases into real schemas
    if json_schema is None:
        json_schema = {"not": {}}
    elif isinstance(json_schema, bool):
        json_schema = {} if json_schema else {"not": {}}

    # Enforce write protection for permission-removed subschemas (which can
    # no longer enforce anything themselves). Checked before the json_schema
    # validation so the permission error takes precedence over generic
    # validation errors and never echoes the protected value.
    violation = find_removed_violation(
        cx.get("yac_perms_removed", []), old_data or {}, new_data or {}
    )
    if violation is not None:
        return out.Schema(
            json_schema=json_schema,
            ui_schema=ui_schema,
            data=new_data,
            valid=False,
            message=(
                f"You don't have the permissions to set, change or delete the"
                f" value at {violation}."
            ),
            validator="yac_perms",
            json_schema_loc="#",
            data_loc=violation,
        )

    format_checker = jsonschema.FormatChecker()
    for name, funct in plugin.get_functions("schema_formats").items():
        logger.debug(f"Adding {name} format_checker {type(funct)}")
        format_checker.checks(name)(funct)

    validator = jsonschema.Draft7Validator(json_schema, format_checker=format_checker)

    try:
        validator.validate(new_data)
        return out.Schema(
            json_schema=json_schema,
            ui_schema=ui_schema,
            data=new_data,
            valid=True,
        )
    except jsonschema.ValidationError as error:
        return out.Schema(
            json_schema=json_schema,
            ui_schema=ui_schema,
            data=new_data,
            valid=False,
            message=error.message,
            validator=str(error.validator),
            json_schema_loc="/".join(["#"] + [str(i) for i in list(error.schema_path)]),
            data_loc="/".join(["#"] + [str(i) for i in list(error.path)]),
        )


async def handle_schema(
    loc: str, json_schema: dict | bool | Any, ui_schema: dict, context: dict, prop: dict
) -> tuple[dict | bool | None, dict, dict]:

    # pre-tests

    if isinstance(json_schema, bool):
        return json_schema, ui_schema, context
    if not isinstance(json_schema, dict):
        raise SchemaSpecsError(f"{loc} is not a schema (object or bool)")

    cx = context
    json = json_schema.copy()
    ui = ui_schema.copy()
    p = prop.copy()

    # pre_order plugins

    for plug in plugin.get_sorted("json_schema", "processor", late=False):
        logger.debug(
            f"Early json_schema plugin {plug.__class__.__name__} processing schema at"
            f" {loc}"
        )
        json, cx = await plug.process(loc, json, cx, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    for plug in plugin.get_sorted("ui_schema", "processor", late=False):
        logger.debug(
            f"Early ui_schema plugin {plug.__class__.__name__} processing schema at"
            f" {loc}"
        )
        json, ui = await plug.process(loc, json, ui, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    # subschemas

    for k in SUBSCHEMAS:
        if k in json:
            s, ui, cx = await handle_schema(f"{loc}/{k}", json[k], ui, cx, p)
            if s is None:
                json.pop(k)
            else:
                json[k] = s

    # objects of subschemas

    for k in SUBSCHEMA_OBJECTS:
        if k in json:
            if not isinstance(json[k], dict):
                raise SchemaSpecsError(f"{loc}/{k} is not an object (of schemas)")
            for key in list(json[k].keys()):
                s, ui, cx = await handle_schema(
                    f"{loc}/{k}/{key}", json[k][key], ui, cx, p
                )
                if s is None:
                    json[k].pop(key)
                else:
                    json[k][key] = s

    # arrays of subschemas

    for k in SUBSCHEMA_ARRAYS:
        if k in json:
            if not isinstance(json[k], list):
                raise SchemaSpecsError(f"{loc}/{k} is not an array (of schemas)")
            new_array = []
            for i, val in enumerate(json[k]):
                s, ui, cx = await handle_schema(f"{loc}/{k}/{str(i)}", val, ui, cx, p)
                if s is not None:
                    new_array.append(s)
            json[k] = new_array

    # post_order plugins

    for plug in plugin.get_sorted("json_schema", "processor", late=True):
        logger.debug(
            f"Late json_schema plugin {plug.__class__.__name__} processing schema at"
            f" {loc}"
        )
        json, cx = await plug.process(loc, json, cx, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    for plug in plugin.get_sorted("ui_schema", "processor", late=True):
        logger.debug(
            f"Late ui_schema plugin {plug.__class__.__name__} processing schema at"
            f" {loc}"
        )
        json, ui = await plug.process(loc, json, ui, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    return json, ui, cx
