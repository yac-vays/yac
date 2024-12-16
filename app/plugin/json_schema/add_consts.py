import logging

from app.lib import locs

logger = logging.getLogger(__name__)


def order() -> tuple[bool, int]:
    # Must run before yac_optional.py to to have it added to the required list
    return True, 90


def process(
    loc: str, json_schema: dict, context: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Adds existing data to the schema as consts if not defined in the schema.

    This will only add data on the object property level, so lists are either
    considered as defined or they are added as a single constant.
    """
    if props["operation"] != "change":
        return json_schema, context

    if json_schema.get("type", "") != "object":
        return json_schema, context

    if "add_consts" not in context:
        context["add_consts"] = locs.get(
            props["old"]["data"], lambda d: isinstance(d, dict)
        )

    for data_loc in locs.reduce(loc, context["add_consts"], recursive=False):
        data = locs.extract(data_loc, props["old"]["data"])
        if isinstance(data, dict):
            for key in data.keys():
                if locs.is_specified(key, json_schema):
                    logger.debug(
                        f"Not adding data {data_loc}/{key} to schema {loc}/properties/{key} "
                        "due to existing subschema"
                    )
                else:
                    json_schema["properties"][key] = {
                        "const": data[key],
                        "yac_optional": "cln" in props["old"]["perms"],
                    }

    return json_schema, context
