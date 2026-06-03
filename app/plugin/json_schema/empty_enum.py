from app.model.plg import IJsonSchema

# J2-templating or perms can lead to an empty list. An empty enumeration is
# invalid schema and would crash the whole form.
#
# A required field with no valid option must stay *unsatisfiable*. So we replace
# it with an unsatisfiable schema (`not: {}`) and mark it so VAYS renders a clear
# "no option available" box (see the `unavailable` renderer).

_ENUM_KEYS = ("oneOf", "anyOf", "enum")


class EmptyEnum(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        # Post-order and after the other post-order json processors (so we also
        # catch arrays they leave empty); still before the post-order ui_schema
        # plugins (e.g. vays_category, order 150) so the `vays_options` marker
        # we set is turned into the Control's renderer option.
        return True, 200

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        if not any(
            isinstance(json_schema.get(k), list) and len(json_schema[k]) == 0
            for k in _ENUM_KEYS
        ):
            return json_schema, context

        for k in _ENUM_KEYS:
            json_schema.pop(k, None)

        json_schema.pop("default", None)
        json_schema["not"] = {}

        opt = json_schema.get("vays_options")
        if not isinstance(opt, dict):
            opt = {}
            json_schema["vays_options"] = opt
        opt["renderer"] = "unavailable"

        return json_schema, context


processor = EmptyEnum()
