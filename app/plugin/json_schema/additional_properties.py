from app.model.plg import IJsonSchema


class AdditionalProperties(IJsonSchema):

    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Default additionalProperties in objects to false instead of true to have a
        more secure default schema behaviour.
        """
        if json_schema.get("type", "") == "object":
            if "additionalProperties" not in json_schema:
                json_schema["additionalProperties"] = False
        return json_schema, context


processor = AdditionalProperties()
