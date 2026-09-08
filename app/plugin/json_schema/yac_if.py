from app.lib import locs
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
        Removes subschemas where yac_if evaluates to false.

        The subschema is only *marked* as removed here (so add_consts.py can
        still see that the schema defines this data); the removed_cleanup
        plugin does the actual removal afterwards.
        """
        if "yac_if" not in json_schema:
            return json_schema, context

        if isinstance(json_schema["yac_if"], bool):
            condition = json_schema["yac_if"]
        elif isinstance(json_schema["yac_if"], str):
            try:
                condition = await j2.render_test(json_schema["yac_if"], props)
            except j2.J2Error as error:
                raise SchemaSpecsError(f"{loc}/yac_if: {error}") from error
        else:
            raise SchemaSpecsError(f"{loc}/yac_if is not a boolean or string")

        if not condition:
            json_schema = locs.removed(loc, "if")
        else:
            json_schema.pop("yac_if")

        return json_schema, context


processor = YacIf()
