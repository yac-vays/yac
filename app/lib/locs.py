"""
This library handles loc (location) strings for representing a position in data or
json_schema objects. These strings are used as a reference in ui_schema.

We differentiate between data_loc and schema_loc here although they are basically
the same, because we can use schema_locs to filter data_locs.

Raises: []
"""

import re
import os
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

# The same keywords in different categories:


def get(
    data: Any, add: Callable[[Any], bool], loc: str = "#", res: list[str] | None = None
) -> list[str]:
    """
    Generates loc strings for all given data that the add() function returns
    True for.

    If flat is False, it will also add dicts and lists to the data (instad of only
    basic types).
    """
    if res is None:
        res = []

    if add(data):
        res.append(loc)

    if isinstance(data, dict):
        for key, value in data.items():
            get(value, add, f"{loc}/{key}", res)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            get(item, add, f"{loc}/{index}", res)

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
            d = d.get(key, None)
        elif isinstance(d, list):
            d = d[int(key)] if int(key) < len(d) else None
        else:
            d = None
    return d


def on_schema_lvl(schema_loc: list, index: int) -> bool:
    """
    Tests if we are on schema level with the index (assuming that top-level
    is always schema level).
    """
    if index == 0:
        return True
    if index >= 1 and schema_loc[index - 1] in SUBSCHEMAS:
        return True
    if index >= 2 and schema_loc[index - 2] in SUBSCHEMA_OBJECTS + SUBSCHEMA_ARRAYS:
        return True
    return False


def to_regex(schema_loc: str, recursive: bool) -> str:
    """
    Will remove all keywords from the loc that are irrelevant for data handling
    purposes. Then it converts everything to a regular expression to match
    only data_locs that are referenced by the schema_loc.

    If recursive is True, it will also match to all locs below that schema_loc.
    """
    keys = schema_loc.split("/")
    if keys and keys[0] == "#":
        keys = keys[1:]

    recursion = "(/.+)*" if recursive else ""
    root = f"^\\#{recursion}$"

    if len(keys) == 0:
        return root

    # keywords we cannot provide data for
    for key in [
        "if",
        "not",
        "propertyNames",
        "$defs",
        "const",
    ]:
        for i, k in enumerate(keys):
            if k == key and on_schema_lvl(keys, i):
                return root

    # keywords we can skip/ignore for data purposes
    for key in ["else", "then", "contentSchema"]:
        for i, k in enumerate(keys):
            if k == key and on_schema_lvl(keys, i):
                del keys[i]

    # keywords we can ignore the keyword and the next one for data purposes
    for key in ["oneOf", "allOf", "anyOf", "dependentSchemas"]:
        for i, k in enumerate(keys):
            if k == key and on_schema_lvl(keys, i):
                del keys[i + 1]
                del keys[i]

    if len(keys) == 0:
        return root

    res = []
    i = 0
    while i < len(keys):
        if keys[i] in ["properties", "prefixItems"]:
            res.append(re.escape(keys[i + 1]))
            i = i + 2
        elif keys[i] == "patternProperties":
            res.append(keys[i + 1])
            i = i + 2
        elif keys[i] in ["items", "contains"]:
            res.append(r"\d+")
            i = i + 1
        else:
            if i + 1 != len(keys):
                logger.error(
                    f'Unknown keyword "{keys[i]}" in json_schema: {schema_loc}'
                )
            i = i + 1

    return f'^\\#/{"/".join(res)}{recursion}$'


def reduce(schema_loc: str, data_locs: list[str], recursive: bool = False) -> list[str]:
    """
    Will only return the data_locs that refer to data that is defined by the
    schema_loc.

    If recursive is True, it will return all data_locs below the schema_loc,
    otherwise it will only return the exact result(s).
    """
    reg = to_regex(schema_loc, recursive)
    schema = re.compile(reg)
    logger.debug(f"Using regex to reduce data to schema loc: {reg}")
    return [d for d in data_locs if schema.match(d)]


def get_most_specific(loc: str, loc_list: list[str]) -> str | None:
    """
    Returns the (first) loc from the list that has the biggest common prefix
    with the given loc or None if there is no match at all.
    """
    prefix = 0
    result = None
    for l in loc_list:
        if not loc.startswith(l):
            continue
        pf = len(os.path.commonpath([loc, l]))
        if prefix < pf:
            prefix = pf
            result = l
    return result


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



def specification(key: str, schema: dict | bool | None) -> str | None:
    """
    How a property (key) is defined in a json_schema specifying an object:

    - `DEFINED` if a live subschema for the key exists,
    - the removal reason (see consts.REMOVED, e.g. "if", "perms") if the only
      subschema(s) found were removed by the json_schema plugins,
    - `None` if the key is not defined at all.

    This is a bit fuzzy and we'll never be able to tell for 100% due to the
    design of json_schema. To keep the complexity low, we only consider some
    keywords (see below or in docs for description).
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

    if key in schema.get("properties", {}):
        sub = schema["properties"][key]
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
