from app.model.err import SchemaSpecsError


def order() -> tuple[bool, int]:
    return False, 0


def process(
    loc: str, json_schema: dict, context: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Removes subschemas where yac_changable is false if the operations is change.
    (If inside object properties, yac_optional.py takes care of cleaning up the required
    list.)
    """
    if "yac_changable" not in json_schema:
        return json_schema, context

    if props["operation"] != "change":
        json_schema.pop("yac_changable")
        return json_schema, context

    if not isinstance(json_schema["yac_changable"], bool):
        raise SchemaSpecsError(f"{loc}/yac_changable is not a boolean")

    if not json_schema["yac_changable"]:
        return None, context

    json_schema.pop("yac_changable")
    return json_schema, context
