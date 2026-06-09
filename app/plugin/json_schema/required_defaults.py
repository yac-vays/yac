import logging

from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


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
        - Required objects (with properties) without default get a default = {}
        - Required arrays without default get a default = []

        The empty object/array materialises the (otherwise absent) parent so that
        VAYS' client-side default insertion can cascade the nested defaults into
        it. Without this, a required-but-missing object/array error would attach
        to the absent parent, which has no rendered control and is thus invisible
        in the form (e.g. a `yac_if` subschema that just became required).
        """
        # On read the schema is for display only; injecting synthetic defaults
        # would misrepresent what is actually stored (matches yac_perms.py).
        if props["operation"] == "read":
            return json_schema, context

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
            prop = json_schema["properties"][key]
            if key in required and "default" not in prop:
                if prop.get("type", None) == "boolean":
                    prop["default"] = False
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = false to"
                        " schema"
                    )
                elif "const" in prop:
                    prop["default"] = prop["const"]
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = const value"
                        " to schema"
                    )
                elif prop.get("type", None) == "object" and "properties" in prop:
                    prop["default"] = {}
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = {{}} to schema"
                    )
                elif prop.get("type", None) == "array":
                    prop["default"] = []
                    logger.debug(
                        f"Added required {loc}/properties/{key}/default = [] to schema"
                    )

        return json_schema, context


processor = RequiredDefaults()
