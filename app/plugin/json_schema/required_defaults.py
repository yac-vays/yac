import logging

from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)

# pylint: disable=unused-argument


class RequiredDefaults(IJsonSchema):

    def order(self) -> tuple[bool, int]:
        # Must run after yac_optional.py to ensure required list is complete
        return True, 110

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        To allow VAYS to always have a valid data object, this plugin extends
        the schame the following way:
        - Required booleans without default value get a default = false
        - Required consts without default value get a default = const value
        """
        if json_schema.get("type", "") != "object":
            return json_schema, context

        required = json_schema.get("required", [])

        try:
            assert isinstance(required, list)
            assert all(isinstance(s, str) for s in required)
        except AssertionError as error:
            raise SchemaSpecsError(
                f"{loc}/required is not an array of strings"
            ) from error

        for key in json_schema.get("properties", {}).keys():
            if key in required and "default" not in json_schema["properties"][key]:
                if json_schema["properties"][key].get("type", None) == "boolean":
                    json_schema["properties"][key]["default"] = False
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = false to schema"
                    )
                elif "const" in json_schema["properties"][key]:
                    json_schema["properties"][key]["default"] = json_schema[
                        "properties"
                    ][key]["const"]
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = const value to schema"
                    )

        return json_schema, context


processor = RequiredDefaults()
