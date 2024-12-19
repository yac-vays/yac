from app.model.err import SchemaSpecsError
from app.lib import j2
from app.model.plg import IJsonSchema


class YacIf(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Removes subschemas where yac_if evaluates to false. (If inside object
        properties, yac_optional.py takes care of cleaning up the required list.)
        """
        if "yac_if" not in json_schema:
            return json_schema, context

        if not isinstance(json_schema["yac_if"], str):
            raise SchemaSpecsError(f"{loc}/yac_if is not a string")

        try:
            condition = await j2.render_test(json_schema["yac_if"], props)
        except j2.J2Error as error:
            raise SchemaSpecsError(f"{loc}/yac_if: {error}") from error

        if not condition:
            return None, context

        json_schema.pop("yac_if")
        return json_schema, context


processor = YacIf()
