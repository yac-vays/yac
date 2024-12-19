from app.lib import plugin
from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema


class Formats(IJsonSchema):

    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Only allow defined format validators in the schema. This is required in
        order to have a secure default, because json_schema treats unknown format
        validators as valid by default!
        """

        if "format" not in json_schema:
            return json_schema, context

        if json_schema["format"] in [
            "date-time",
            "date",
            "time",
            "duration",
            "email",
            "idn-email",
            "hostname",
            "idn-hostname",
            "ipv4",
            "ipv6",
            "uri",
            "uri-reference",
            "iri",
            "iri-reference",
            "uuid",
            "uri-template",
            "json-pointer",
            "relative-json-pointer",
            "regex",
        ]:
            return json_schema, context

        if json_schema["format"] in plugin.get_functions("schema_formats").keys():
            return json_schema, context

        # TODO test if it works!
        raise SchemaSpecsError(
            f'{loc}/format validator "{json_schema["format"]}" is not defined'
        )


processor = Formats()
