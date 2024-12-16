import logging

from app.model.err import SchemaSpecsError

logger = logging.getLogger(__name__)


def order() -> tuple[bool, int]:
    return False, 0


def process(
    loc: str, json_schema: dict, context: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Only allow schemas where the top-level type is objects and only the
    supported metaschema.
    """
    if loc == "#":
        if json_schema.get("type", "") != "object":
            raise SchemaSpecsError(
                "json_schema at # (top-level) must be of type object"
            )
        if "$schema" in json_schema:
            if json_schema["$schema"] != "https://json-schema.org/draft-07/schema":
                logger.warning(
                    "#/$schema has an invalid value, only draft-07 is supported"
                )
            json_schema.pop("$schema")
    return json_schema, context
