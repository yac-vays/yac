import logging

from app.lib import locs
from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


class YacOptional(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return True, 110

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
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
        del props
        if json_schema.get("type", "") != "object":
            return json_schema, context

        # A subschema that only tests the data keeps its `required` as written.
        shape = not locs.conditional(loc)
        if "required" in json_schema:
            if shape:
                logger.warning(
                    f"Explicit definition of {loc}/required, better use yac_optional"
                    " instead"
                )
        elif shape:
            json_schema["required"] = []

        for key, prop in json_schema.get("properties", {}).items():
            optional = False
            if isinstance(prop, dict) and "yac_optional" in prop:
                optional = prop.pop("yac_optional")
                if not isinstance(optional, bool):
                    raise SchemaSpecsError(
                        f"{loc}/properties/{key}/yac_optional is not a boolean"
                    )
            if not shape:
                continue
            if key not in json_schema["required"] and not optional:
                json_schema["required"].append(key)
            if key in json_schema["required"] and optional:
                json_schema["required"].remove(key)

        return json_schema, context


processor = YacOptional()
