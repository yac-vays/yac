"""
The json_schema plugins run in a fixed order, cope with boolean subschemas
and leave subschemas that only test the data (if / not / contains /
propertyNames) as written.
"""

from app.consts import OMIT, REMOVED
from app.lib import locs, plugin, schema
from app.plugin.ui_schema.vays_category import processor as vays_category


def _props(operation="edit", perms=("see", "edt"), data=None) -> dict:
    return {
        "operation": operation,
        "type": "host",
        "user": {"name": "u", "perms": list(perms)},
        "old": {"data": data or {}},
        "new": {"data": {}},
    }


async def _generate(json_schema: dict, **kwargs) -> dict:
    out, _, _ = await schema.handle_schema("#", json_schema, {}, {}, _props(**kwargs))
    return out


def test_early_plugins_have_distinct_order_numbers():
    early = plugin.get_sorted("json_schema", "processor", late=False)
    names = [p.__class__.__name__ for p in early]
    numbers = [p.order()[1] for p in early]
    assert names[0] == "Omit"
    assert numbers == sorted(set(numbers))
    assert names.index("YacPerms") < names.index("YacIf")
    assert names.index("YacEditable") < names.index("YacIf")


async def test_omit_works_for_every_extension_keyword():
    out = await _generate(
        {
            "type": "object",
            "properties": {
                "a": {
                    "type": "string",
                    "yac_perms": OMIT,
                    "yac_types": OMIT,
                    "yac_if": OMIT,
                    "yac_editable": OMIT,
                    "format": OMIT,
                }
            },
        }
    )
    assert out["properties"]["a"] == {"type": "string"}


async def test_unwritable_value_stays_pinned_when_its_condition_fails():
    json_schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "yac_if": False, "yac_perms": ["adm"]},
            "b": {"type": "string", "yac_if": False},
        },
    }
    early = plugin.get_sorted("json_schema", "processor", late=False)
    marks = {}
    for key in ("a", "b"):
        sub = dict(json_schema["properties"][key])
        ctx = {"yac_perms": {"#": ["add", "edt"]}}
        for plug in early:
            sub, ctx = await plug.process(f"#/properties/{key}", sub, ctx, _props())
        marks[key] = sub[REMOVED]
    assert marks == {"a": "perms", "b": "if"}


async def test_boolean_property_schemas_are_accepted():
    out = await _generate(
        {"type": "object", "properties": {"any": True, "never": False}}
    )
    assert out["properties"] == {"any": True, "never": False}


async def test_condition_subschemas_are_left_as_written():
    condition = {"type": "object", "properties": {"kind": {"const": "vm"}}}
    out = await _generate(
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "object", "properties": {}},
                    "contains": {"type": "object", "properties": {"x": {"const": 1}}},
                },
            },
            "if": dict(condition),
            "then": {"type": "object", "properties": {"cpu": {"type": "integer"}}},
            "not": {"type": "object", "properties": {"kind": {"const": "none"}}},
        },
        data={"kind": "vm", "other": 1},
    )
    assert out["if"] == condition
    assert out["not"] == {"type": "object", "properties": {"kind": {"const": "none"}}}
    assert out["properties"]["tags"]["contains"] == {
        "type": "object",
        "properties": {"x": {"const": 1}},
    }
    # schemas that describe the data are still shaped
    assert out["additionalProperties"] is False
    assert "cpu" in out["then"]["required"]


def test_conditional_and_below_items_judge_by_keyword_position():
    assert locs.conditional("#/if") is True
    assert locs.conditional("#/properties/a/not/properties/b") is True
    assert locs.conditional("#/properties/if") is False
    assert locs.conditional("#/properties/not/properties/contains") is False
    assert locs.conditional("#/then/properties/a") is False
    assert locs.below_items("#/properties/a/items/properties/b") is True
    assert locs.below_items("#/properties/items/properties/b") is False
    assert locs.below_items("#/properties/a/items") is False


async def test_property_named_items_gets_its_form_controls():
    sub = {"type": "string", "vays_category": "General"}
    _, ui = await vays_category.process(
        "#/properties/items/properties/x", dict(sub), {}, {}
    )
    assert ui["elements"][0]["elements"][0]["scope"] == "#/properties/items/properties/x"
    out, ui = await vays_category.process(
        "#/properties/list/items/properties/x", dict(sub), {}, {}
    )
    assert ui == {} and "vays_category" not in out
