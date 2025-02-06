from app.consts import OMIT
from app.model.plg import IJsonSchema


class Omit(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Removes subschemas and some schema keys if they have the value of our
        global j2 constant "omit".
        """

        for k in json_schema.keys():
            if not isinstance(json_schema[k], str):
                continue
            if json_schema[k] == OMIT:
                del json_schema[k]

        return json_schema, context


processor = Omit()
