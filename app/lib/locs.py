"""
This library handles loc (location) strings for representing a position in data or
json_schema objects. These strings are used as a reference in ui_schema.

We differentiate between data_loc and schema_loc here although they are basically
the same, because we can use schema_locs to filter data_locs.

Raises: []
"""

import re
import json
import logging
from typing import Any, Callable

from app import consts

logger = logging.getLogger(__name__)

SUBSCHEMAS = [
    "if",
    "else",
    "then",
    "not",
    "propertyNames",
    "contains",
    "items",
    "contentSchema",
]
SUBSCHEMA_OBJECTS = ["$defs", "properties", "patternProperties", "dependentSchemas"]
SUBSCHEMA_ARRAYS = ["oneOf", "allOf", "anyOf", "prefixItems"]


def collect(
    data: Any, add: Callable[[Any], bool], loc: str = "#", res: list[str] | None = None
) -> list[str]:
    """
    Generates loc strings for all given data that the add() function returns
    True for.
    """
    if res is None:
        res = []

    if add(data):
        res.append(loc)

    if isinstance(data, dict):
        for key, value in data.items():
            collect(value, add, f"{loc}/{key}", res)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            collect(item, add, f"{loc}/{index}", res)

    return res


def extract(data_loc: str, data: Any) -> Any | None:
    """
    Extracts and returns the data from the data object that is referenced by
    the data-loc string (or None if not found).
    """
    keys = data_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]

    d = data
    for key in keys:
        if isinstance(d, dict):
            if key in d:
                d = d[key]
            else:
                # locs are strings, YAML keys need not be (`80: http`)
                d = next((v for k, v in d.items() if str(k) == key), None)
        elif isinstance(d, list):
            is_index = key.isascii() and key.isdigit() and int(key) < len(d)
            d = d[int(key)] if is_index else None
        else:
            d = None
    return d


def on_schema_lvl(schema_loc: list, index: int) -> bool:
    """
    Tests if the segment at index is a schema keyword (and not the name of a
    property or the index of a subschema), walking down from the top level,
    which is always schema level.
    """
    i = 0
    while i < index:
        i += 2 if schema_loc[i] in SUBSCHEMA_OBJECTS + SUBSCHEMA_ARRAYS else 1
    return i == index


def to_data_path(schema_loc: str) -> list[tuple[str, str]] | None:
    """
    Converts a schema_loc into the path of the data it describes: one
    (kind, arg) per data loc segment below `#`, where kind is "key" (the
    segment equals arg), "pattern" (the regex arg is found in the segment, as
    for `patternProperties`) or "index" (any list index). Keywords that are
    irrelevant for data handling (then, oneOf/<n>, ...) are skipped.

    Returns None if there is no data behind the schema_loc (e.g. below `if`
    or `not`), which only refers to the document root.
    """
    keys = schema_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]

    path: list[tuple[str, str]] = []
    i = 0
    while i < len(keys):
        key = keys[i]
        has_arg = i + 1 < len(keys)
        # keywords we cannot provide data for
        if key in ("if", "not", "propertyNames", "$defs", "const"):
            return None
        # keywords we can skip (they apply to the same data)
        if key in ("else", "then", "contentSchema"):
            i += 1
        elif key in ("oneOf", "allOf", "anyOf", "dependentSchemas"):
            i += 2
        elif key in ("properties", "prefixItems") and has_arg:
            path.append(("key", keys[i + 1]))
            i += 2
        elif key == "patternProperties" and has_arg:
            path.append(("pattern", keys[i + 1]))
            i += 2
        elif key in ("items", "contains"):
            path.append(("index", ""))
            i += 1
        else:
            if has_arg:
                logger.error(f'Unknown keyword "{key}" in json_schema: {schema_loc}')
            i += 1
    return path


def _segment_matches(kind: str, arg: str, segment: str) -> bool:
    if kind == "key":
        return segment == arg
    if kind == "index":
        return segment.isascii() and segment.isdigit()
    try:
        return re.search(arg, segment) is not None
    except re.error as error:
        logger.error(f'Invalid patternProperties pattern "{arg}": {error}')
        return False


def filter_by_schema_loc(
    schema_loc: str, data_locs: list[str], recursive: bool = False
) -> list[str]:
    """
    Will only return the data_locs that refer to data that is defined by the
    schema_loc.

    If recursive is True, it will return all data_locs below the schema_loc,
    otherwise it will only return the exact result(s).
    """
    path = to_data_path(schema_loc)

    def matches(data_loc: str) -> bool:
        segments = data_loc.split("/")
        if segments[0] != "#":
            return False
        segments = segments[1:]
        if path is None:
            return recursive or not segments
        if len(segments) < len(path) or (not recursive and len(segments) > len(path)):
            return False
        return all(
            _segment_matches(kind, arg, segment)
            for (kind, arg), segment in zip(path, segments)
        )

    return [d for d in data_locs if matches(d)]


def get_most_specific(loc: str, loc_list: list[str]) -> str | None:
    """
    Returns the longest loc from the list that is the given loc or one of its
    ancestors, or None if there is none.
    """
    candidates = [l for l in loc_list if loc == l or loc.startswith(f"{l}/")]
    return max(candidates, key=len, default=None)


DEFINED = "defined"


def removed(loc: str, reason: str) -> dict | None:
    """
    What a json_schema plugin returns for a subschema it removes: a marker
    (see consts.REMOVED) that add_consts.py on the parent object can consult
    and removed_cleanup.py drops afterwards. At the top level there is no
    parent, so the schema is removed right away (None).
    """
    if loc == "#":
        return None
    return {consts.REMOVED: reason, "not": True}


def shared(schema_loc: str) -> bool:
    """
    Whether the subschema at schema_loc may apply to more than one data
    location: it is (below) the subschema of `items`, `contains` or
    `patternProperties`, e.g. the properties of the items of an array.
    """
    keys = schema_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]
    return any(
        k in ("items", "contains", "patternProperties") and on_schema_lvl(keys, i)
        for i, k in enumerate(keys)
    )


def below_items(schema_loc: str) -> bool:
    """Whether the subschema at schema_loc is below the `items` of an array."""
    keys = schema_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]
    return any(
        k == "items" and on_schema_lvl(keys, i) for i, k in enumerate(keys[:-1])
    )


def conditional(schema_loc: str) -> bool:
    """
    Whether the subschema at schema_loc is (below) one that only tests the
    data (`if`, `not`, `contains`, `propertyNames`) instead of describing
    it. Plugins that shape a schema for the form (required, defaults,
    additionalProperties, stored values) must leave these alone: they would
    change what the test matches.
    """
    keys = schema_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]
    return any(
        k in ("if", "not", "contains", "propertyNames") and on_schema_lvl(keys, i)
        for i, k in enumerate(keys)
    )


def specification(key: str, schema: dict | bool | None) -> str | None:
    """
    How a property (key) is defined in a json_schema specifying an object:

    - `DEFINED` if a live subschema for the key exists,
    - the removal reason (see consts.REMOVED, e.g. "if", "perms") if the only
      subschema(s) found were removed by the json_schema plugins,
    - `None` if the key is not defined at all.

    This is a bit fuzzy and we'll never be able to tell for 100% due to the
    design of json_schema. To keep the complexity low, we only consider some
    keywords: `properties`, `patternProperties` (the patterns found in the
    key) and those combining subschemas (see below or in docs).
    """
    if not isinstance(schema, dict):
        # Not considering trivial schemas as a specification of the key!
        return None

    reason: str | None = None

    def consider(state: str | None) -> bool:
        nonlocal reason
        if state == DEFINED:
            return True
        if state is not None and reason is None:
            reason = state
        return False

    subs = []
    if key in schema.get("properties", {}):
        subs.append(schema["properties"][key])
    for pattern, sub in schema.get("patternProperties", {}).items():
        if _segment_matches("pattern", pattern, key):
            subs.append(sub)
    for sub in subs:
        marker = sub.get(consts.REMOVED) if isinstance(sub, dict) else None
        if consider(DEFINED if marker is None else str(marker)):
            return DEFINED

    for subschema in ["then", "else"]:
        if subschema in schema:
            if consider(specification(key, schema[subschema])):
                return DEFINED

    for subschema_list in ["oneOf", "allOf", "anyOf"]:
        if subschema_list in schema and isinstance(schema[subschema_list], list):
            for subschema in schema[subschema_list]:
                if consider(specification(key, subschema)):
                    return DEFINED

    return reason


def is_specified(key: str, schema: dict | bool | None) -> bool:
    """
    Checks if a property (key) is defined explicitly (live or removed by a
    plugin) in a json_schema specifying an object. See `specification`.
    """
    return specification(key, schema) is not None


def value_key(value: Any) -> str:
    """
    A hashable identity of a data value that tells apart what Python equates
    but JSON-Schema does not (`1` and `true`), but not what JSON-Schema
    equates (`1` and `1.0`). YAML-only types (dates) count by their string
    form.
    """
    return json.dumps(_canonical(value), sort_keys=True, default=str)


def _canonical(value: Any) -> Any:
    """Integral floats as ints (equal in JSON-Schema), all mapping keys as strings."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
