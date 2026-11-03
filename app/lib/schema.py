"""
Raises: [app.model.err.SchemaSpecsError]
"""

import asyncio
import inspect
import logging
import math
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

import jsonschema

from app.consts import INJECTED
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


_FLOAT_MULTIPLE_OF = jsonschema.Draft7Validator.VALIDATORS["multipleOf"]


def _decimal_multiple_of(validator, d_b, instance, schema):
    """
    `multipleOf` in decimal (as the numbers are written in YAML/JSON) instead
    of binary floating point: 0.035 IS a multiple of 0.005, although
    0.035 / 0.005 is 7.000000000000001 in floats (which jsonschema rejects).
    `str()` of a float is its shortest round-trip repr, i.e. what was written.
    inf/nan are never a multiple (jsonschema itself raises on them); a quotient
    beyond the decimal precision falls back to jsonschema's float check.
    """
    if not validator.is_type(instance, "number"):
        return
    if isinstance(instance, float) and not math.isfinite(instance):
        yield jsonschema.ValidationError(f"{instance!r} is not a multiple of {d_b}")
        return
    try:
        failed = Decimal(str(instance)) % Decimal(str(d_b)) != 0
    except (InvalidOperation, ValueError):
        yield from _FLOAT_MULTIPLE_OF(validator, d_b, instance, schema)
        return
    if failed:
        yield jsonschema.ValidationError(f"{instance!r} is not a multiple of {d_b}")


Draft7Validator = jsonschema.validators.extend(
    jsonschema.Draft7Validator, {"multipleOf": _decimal_multiple_of}
)


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

    json_schema, ui_schema, context = await handle_schema(
        "#", json_schema, {}, {}, schema_props
    )
    _strip_injected(json_schema)

    # convert trivial cases into real schemas
    if json_schema is None:
        json_schema = {"not": {}}
    elif isinstance(json_schema, bool):
        json_schema = {} if json_schema else {"not": {}}

    validator = await _validator(json_schema, new_data)

    try:
        validator.validate(new_data)
        excess = _shared_consts_excess(context, new_data)
        if excess is not None:
            return out.Schema(
                json_schema=json_schema,
                ui_schema=ui_schema,
                data=new_data,
                valid=False,
                message=excess[0],
                validator="enum",
                json_schema_loc=excess[1],
                data_loc=excess[2],
            )
        return out.Schema(
            json_schema=json_schema,
            ui_schema=ui_schema,
            data=new_data,
            valid=True,
        )
    except jsonschema.ValidationError as error:
        message, data_loc = _describe(error, context)
        return out.Schema(
            json_schema=json_schema,
            ui_schema=ui_schema,
            data=new_data,
            valid=False,
            message=message,
            validator=str(error.validator),
            json_schema_loc="/".join(["#"] + [str(i) for i in list(error.schema_path)]),
            data_loc=data_loc,
        )


def _strip_injected(json_schema: Any) -> None:
    """Drops the consts.INJECTED markers once the ui_schema is complete."""
    if isinstance(json_schema, dict):
        json_schema.pop(INJECTED, None)
        for key, value in json_schema.items():
            # data, not schemas
            if key not in ("enum", "const", "default", "examples"):
                _strip_injected(value)
    elif isinstance(json_schema, list):
        for value in json_schema:
            _strip_injected(value)


def _shared_consts_excess(context: dict, data: Any) -> tuple[str, str, str] | None:
    """
    The `enum` add_consts.py injects for a key without a (live) subschema in
    a shared subschema (e.g. array items) only limits the values to the
    stored ones. This adds the rest: no value may be used more often than
    before (so values can be kept, moved or removed, but not duplicated).
    Returns (message, json_schema_loc, data_loc) of the first excess use.
    """
    for schema_loc, stored in context.get("add_consts_shared", {}).items():
        parent_loc, key = schema_loc.rsplit("/properties/", 1)
        left = Counter(locs.value_key(v) for v in stored)
        objs = locs.reduce(parent_loc, locs.get(data, lambda d: isinstance(d, dict)))
        for data_loc in objs:
            obj = locs.extract(data_loc, data)
            if not isinstance(obj, dict) or key not in obj:
                continue
            value = locs.value_key(obj[key])
            if left[value] <= 0:
                return (
                    f"{obj[key]!r} may not be used more often than before at"
                    f" '{key}' (the stored values can only be kept or removed)",
                    f"{schema_loc}/enum",
                    f"{data_loc}/{key}",
                )
            left[value] -= 1
    return None


def _is_async(funct: Any) -> bool:
    # alru_cache & co. wrap the coroutine function in a callable object
    return inspect.iscoroutinefunction(funct) or inspect.iscoroutinefunction(
        getattr(funct, "__wrapped__", None)
    )


async def _validator(json_schema: dict, data: Any) -> Any:
    """
    The validator with the schema_formats plugins as format checkers.

    jsonschema calls format checkers synchronously, but a plugin may be async
    (e.g. a lookup in an external service). Those values are resolved upfront:
    a collecting pass validates with every unresolved value counted as valid and
    records it, then all recorded values are checked concurrently. A result can
    steer the validation into other branches (anyOf, if/then/else, ...) with
    further values, so this repeats until a pass records nothing new. That
    terminates, as every round resolves at least one of the finitely many
    (format, value) pairs in the data. The returned validator only looks the
    results up.
    """
    functs = plugin.get_functions("schema_formats")
    results: dict[tuple[str, str], bool] = {}
    pending: dict[tuple[str, str], Any] = {}

    def lookup(name: str) -> Callable[[Any], bool]:
        def check(instance: Any) -> bool:
            key = (name, repr(instance))
            if key in results:
                return results[key]
            pending[key] = instance
            return True

        return check

    format_checker = jsonschema.FormatChecker()
    for name, funct in functs.items():
        logger.debug(f"Adding {name} format_checker {type(funct)}")
        format_checker.checks(name)(lookup(name) if _is_async(funct) else funct)
    validator = Draft7Validator(json_schema, format_checker=format_checker)

    if not any(_is_async(funct) for funct in functs.values()):
        return validator

    while True:
        pending.clear()
        for _ in validator.iter_errors(data):
            pass
        if not pending:
            return validator
        keys = list(pending)
        checked = await asyncio.gather(
            *(functs[key[0]](pending[key]) for key in keys)
        )
        results.update({key: bool(valid) for key, valid in zip(keys, checked)})


def _describe(error: jsonschema.ValidationError, context: dict) -> tuple[str, str]:
    """
    The (message, data_loc) to report for a validation error.

    A `required` violation is anchored on the PARENT object by jsonschema,
    which for top-level properties is the document root — useless for a UI
    that wants to mark the offending field. The missing property is derivable
    structurally (first required name absent from the instance), so point
    `data_loc` at the property itself.

    If that property is a required `const` injected by add_consts.py, the
    stored key was removed although the user may not do that; say so instead
    of the misleading "is a required property" (`add_consts_state` in the
    context records "unknown" or the removal reason, see locs.specification).
    """
    loc_path = [str(i) for i in list(error.path)]
    message = error.message
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = next(
            (p for p in error.validator_value if p not in error.instance), None
        )
        if missing is not None:
            loc_path.append(str(missing))
            schema_loc = "/".join(
                ["#"]
                + [str(i) for i in list(error.schema_path)[:-1]]
                + ["properties", str(missing)]
            )
            state = context.get("add_consts_state", {}).get(schema_loc)
            if state == "unknown":
                message = (
                    f"'{missing}' is not covered by the schema, removing it"
                    ' requires the "cln" permission'
                )
            elif state == "perms":
                message = f"'{missing}' may not be removed (missing permission)"
            elif state == "editable":
                message = f"'{missing}' may not be removed (not editable)"
    return message, "/".join(["#"] + loc_path)


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
