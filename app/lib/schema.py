"""
Raises: [app.model.err.SchemaSpecsError]
"""

import logging
from typing import Any

import jsonschema

from app.lib import j2
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


def get(
    op: inp.OperationRequest,
    schema_spec: spc.Schema,
    request_spec: spc.Request,
    old_data: dict,
    old_perms: list[str],
    new_data: dict,
) -> out.Schema:
    schema_props = props.get_schema(op, request_spec, old_data, old_perms, new_data)

    if (
        schema_props["operation"] == "create"
        and "add" not in schema_props["old"]["perms"]
    ):
        # We need to inject the add permission on create to allow having a complete schema
        # for the VAYS user. The add permission is also validated in
        # plugin/validators/perms.py as a whole, so this should be safe.
        schema_props["old"]["perms"].append("add")

    try:
        json_schema = j2.render(dict(schema_spec), schema_props)
    except j2.J2Error as error:
        raise SchemaSpecsError(f"{error.loc}: {error}") from error

    json_schema, ui_schema, _ = handle_schema("#", json_schema, {}, {}, schema_props)

    # convert trivial cases into real schemas
    if json_schema is None or (isinstance(json_schema, bool) and not json_schema):
        json_schema = {"not": {}}
    elif isinstance(json_schema, bool) and json_schema:
        json_schema = {}

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
        # TODO remove this line if line above works: except jsonschema.exceptions.ValidationError as error:
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


def handle_schema(
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

    for plug in plugin.get_modules_sorted("json_schema", late=False):
        logger.debug(
            f"Early json_schema plugin {plug.__name__} processing schema at {loc}"
        )
        json, cx = plug.process(loc, json, cx, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    for plug in plugin.get_modules_sorted("ui_schema", late=False):
        logger.debug(
            f"Early ui_schema plugin {plug.__name__} processing schema at {loc}"
        )
        json, ui = plug.process(loc, json, ui, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    # subschemas

    for k in SUBSCHEMAS:
        if k in json:
            s, ui, cx = handle_schema(f"{loc}/{k}", json[k], ui, cx, p)
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
                s, ui, cx = handle_schema(f"{loc}/{k}/{key}", json[k][key], ui, cx, p)
                if s is None:
                    json[k].pop(key)
                else:
                    json[k][key] = s

    # arrays of subschemas

    for k in SUBSCHEMA_ARRAYS:
        if k in json:
            if not isinstance(json[k], list):
                raise SchemaSpecsError(f"{loc}/{k} is not an array (of schemas)")
            for i, val in enumerate(json[k]):
                s, ui, cx = handle_schema(f"{loc}/{k}/{str(i)}", val, ui, cx, p)
                if s is None:
                    json[k].pop(i)
                else:
                    json[k][i] = s

    # post_order plugins

    for plug in plugin.get_modules_sorted("json_schema", late=True):
        logger.debug(
            f"Late json_schema plugin {plug.__name__} processing schema at {loc}"
        )
        json, cx = plug.process(loc, json, cx, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    for plug in plugin.get_modules_sorted("ui_schema", late=True):
        logger.debug(
            f"Late ui_schema plugin {plug.__name__} processing schema at {loc}"
        )
        json, ui = plug.process(loc, json, ui, p)
        if isinstance(json, bool) or json is None:
            return json, ui, cx

    return json, ui, cx
