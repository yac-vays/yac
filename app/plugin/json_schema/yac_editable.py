from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema


class YacEditable(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Removes subschemas where yac_editable is false if the operation is edit.
        (If inside object properties, yac_optional.py takes care of cleaning up the required
        list.)
        """
        # TODO IDEA: instead of removing: add const to the schema and update all vays renderers to make them disabled when there is a const in the subschema
        if "yac_editable" not in json_schema:
            return json_schema, context

        if props["operation"] != "edit":
            json_schema.pop("yac_editable")
            return json_schema, context

        if not isinstance(json_schema["yac_editable"], bool):
            raise SchemaSpecsError(f"{loc}/yac_editable is not a boolean")

        if not json_schema["yac_editable"]:
            return None, context

        json_schema.pop("yac_editable")
        return json_schema, context


processor = YacEditable()
