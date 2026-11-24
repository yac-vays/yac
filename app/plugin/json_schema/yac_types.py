from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema


class YacTypes(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return False, 20

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Removes subschemas whose yac_types list does not contain the current
        type.
        """
        if "yac_types" not in json_schema:
            return json_schema, context

        try:
            assert bool(json_schema["yac_types"])
            assert isinstance(json_schema["yac_types"], list)
            assert all(isinstance(s, str) for s in json_schema["yac_types"])
        except AssertionError as error:
            raise SchemaSpecsError(
                f"{loc}/yac_types is not an array of strings"
            ) from error

        if props["type"] not in json_schema["yac_types"]:
            return None, context

        json_schema.pop("yac_types")
        return json_schema, context


processor = YacTypes()
