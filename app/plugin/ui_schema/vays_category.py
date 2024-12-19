from app.model.err import SchemaSpecsError
from app.lib import uischema

# pylint: disable=unused-argument


def order() -> tuple[bool, int]:
    return True, 20


async def process(
    loc: str, json_schema: dict, ui_schema: dict, props: dict
) -> tuple[dict | bool | None, dict]:
    """
    Adds subschemas to the ui_schema if vays_category is defined. It respects
    vays_group and vays_options as optional.
    """
    if "vays_category" not in json_schema:
        return json_schema, ui_schema

    cat = json_schema["vays_category"]
    grp = json_schema.get("vays_group", None)
    opt = json_schema.get("vays_options", {})

    if not isinstance(cat, str):
        raise SchemaSpecsError(f"{loc}/vays_category is not a string")
    if not isinstance(grp, str) and grp is not None:
        raise SchemaSpecsError(f"{loc}/vays_group is not a string")
    if not isinstance(opt, dict):
        raise SchemaSpecsError(f"{loc}/vays_options is not an object")

    ui_schema = uischema.add_element(loc, opt, cat, grp, ui_schema)
    json_schema.pop("vays_category")
    json_schema.pop("vays_group", None)
    json_schema.pop("vays_options", None)
    return (json_schema, ui_schema)
