# pylint: disable=unused-argument


def order() -> tuple[bool, int]:
    return False, 0


async def process(
    loc: str, json_schema: dict, context: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Default additionalProperties in objects to false instead of true to have a
    more secure default schema behaviour.
    """
    if json_schema.get("type", "") == "object":
        if "additionalProperties" not in json_schema:
            json_schema["additionalProperties"] = False
    return json_schema, context
