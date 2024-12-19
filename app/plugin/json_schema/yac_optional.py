import logging

from app.model.err import SchemaSpecsError

logger = logging.getLogger(__name__)

# pylint: disable=unused-argument


def order() -> tuple[bool, int]:
    return True, 100


async def process(
    loc: str, json_schema: dict, context: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Removes required entries in objects if they are not in properties (because
    other plugins might have deleted them).
    Then it builds the required list according to the object schema properties
    with respect to the yac_optional in the property-subschemas (which defaults
    to false if not defined explicitly).

    **Note** that the usage of yac_optional is only supported for object
    property definitions with the properties schema-keyword!
    """
    if json_schema.get("type", "") != "object":
        return json_schema, context

    if "required" in json_schema:
        logger.warning(
            f"Explicit definition of {loc}/required, better use yac_optional instead"
        )
    else:
        json_schema["required"] = []

    for key in json_schema.get("properties", {}).keys():
        optional = False
        if "yac_optional" in json_schema["properties"][key]:
            optional = json_schema["properties"][key]["yac_optional"]
            if isinstance(optional, bool):
                json_schema["properties"][key].pop("yac_optional")
            else:
                raise SchemaSpecsError(
                    f"{loc}/properties/{key}/yac_optional is not a boolean"
                )

        if key not in json_schema["required"] and not optional:
            json_schema["required"].append(key)
        if key in json_schema["required"] and optional:
            json_schema["required"].remove(key)

    return json_schema, context
