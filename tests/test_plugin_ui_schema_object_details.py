"""
Tests for the `vays_object_details` ui_schema plugin: it attaches
`options.detail` to a nested object's Control so `vays_options` on the
object's properties reach the right renderers in VAYS.

The plugins are exercised directly in traversal order (post order: children
first, then `vays_category` and `vays_object_details` at the parent), like the
other schema-plugin tests.
"""

import copy

from app.plugin.ui_schema.vays_category import processor as vays_category
from app.plugin.ui_schema.vays_object_details import processor as vays_object_details

PROPS: dict = {}


def _identity_schema():
    """The motivating spec: category on the object, options on a child."""
    return {
        "title": "Identity",
        "vays_category": "NOVA",
        "type": "object",
        "properties": {
            "uuid": {
                "title": "UUID",
                "type": "string",
                "pattern": "^[0-9a-f-]+$",
                "vays_options": {
                    "renderer": "random_string",
                    "renderer_options": {"format": "uuid"},
                },
            },
            "hostname": {"title": "Hostname", "type": "string"},
        },
    }


async def _run(loc, schema, ui_schema=None):
    """Run vays_category then vays_object_details at `loc` (their run order)."""
    ui = ui_schema if ui_schema is not None else {}
    schema, ui = await vays_category.process(loc, schema, ui, PROPS)
    schema, ui = await vays_object_details.process(loc, schema, ui, PROPS)
    return schema, ui


def _find_control(ui_schema, scope):
    if ui_schema.get("type") == "Control" and ui_schema.get("scope") == scope:
        return ui_schema
    for e in ui_schema.get("elements", []):
        found = _find_control(e, scope)
        if found is not None:
            return found
    return None


async def test_object_control_gets_detail_with_property_options():
    loc = "#/properties/identity"
    schema, ui = await _run(loc, _identity_schema())

    ctrl = _find_control(ui, loc)
    assert ctrl is not None
    detail = ctrl["options"]["detail"]
    assert detail["type"] == "Group"
    assert detail["label"] == "Identity"

    # All properties are present (a supplied detail replaces generation
    # entirely), in spec order, options only where the spec set them.
    scopes = [e["scope"] for e in detail["elements"]]
    assert scopes == ["#/properties/uuid", "#/properties/hostname"]
    uuid_ctrl = detail["elements"][0]
    assert uuid_ctrl["options"] == {
        "renderer": "random_string",
        "renderer_options": {"format": "uuid"},
    }
    assert "options" not in detail["elements"][1]

    # The consumed vays_options are stripped from the delivered json_schema.
    assert "vays_options" not in schema["properties"]["uuid"]


async def test_object_without_nested_options_is_untouched():
    loc = "#/properties/identity"
    schema = _identity_schema()
    del schema["properties"]["uuid"]["vays_options"]
    _, ui = await _run(loc, schema)
    # vays_category always writes (possibly empty) options; the point here is
    # that no detail was generated.
    assert "detail" not in _find_control(ui, loc)["options"]


async def test_object_without_control_is_a_noop():
    # No vays_category => no Control at this loc (post order: the parent's
    # pass consumes the subtree later) => vays_options must survive.
    loc = "#/properties/identity"
    schema = _identity_schema()
    del schema["vays_category"]
    expected = copy.deepcopy(schema)
    schema, ui = await vays_object_details.process(loc, schema, {}, PROPS)
    assert schema == expected
    assert ui == {}


async def test_nested_object_gets_nested_detail():
    loc = "#/properties/identity"
    schema = {
        "vays_category": "NOVA",
        "type": "object",
        "properties": {
            "inner": {
                "title": "Inner",
                "type": "object",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "vays_options": {"renderer": "random_string"},
                    },
                },
            },
        },
    }
    schema, ui = await _run(loc, schema)

    inner_ctrl = _find_control(ui, loc)["options"]["detail"]["elements"][0]
    assert inner_ctrl["scope"] == "#/properties/inner"
    nested = inner_ctrl["options"]["detail"]
    assert nested["label"] == "Inner"
    assert nested["elements"][0]["options"] == {"renderer": "random_string"}
    assert "vays_options" not in schema["properties"]["inner"]["properties"]["uuid"]


async def test_nested_array_gets_row_details():
    loc = "#/properties/identity"
    schema = {
        "vays_category": "NOVA",
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {
                    "type": "string",
                    "vays_options": {"renderer": "ssh_key"},
                },
            },
        },
    }
    schema, ui = await _run(loc, schema)

    keys_ctrl = _find_control(ui, loc)["options"]["detail"]["elements"][0]
    assert keys_ctrl["options"]["details"]["elements"] == [
        {"type": "Control", "scope": "#", "options": {"renderer": "ssh_key"}}
    ]
    assert "vays_options" not in schema["properties"]["keys"]["items"]


async def test_detail_label_falls_back_to_property_name():
    loc = "#/properties/identity"
    schema = _identity_schema()
    del schema["title"]
    _, ui = await _run(loc, schema)
    assert _find_control(ui, loc)["options"]["detail"]["label"] == "identity"


async def test_object_vays_options_still_land_on_the_control():
    # vays_options on the object itself are vays_category's business and must
    # coexist with the generated detail.
    loc = "#/properties/identity"
    schema = _identity_schema()
    schema["vays_options"] = {"renderer_options": {"foo": 1}}
    _, ui = await _run(loc, schema)
    ctrl = _find_control(ui, loc)
    assert ctrl["options"]["renderer_options"] == {"foo": 1}
    assert ctrl["options"]["detail"]["type"] == "Group"


async def test_non_object_is_ignored():
    schema = {"type": "string", "vays_options": {"renderer": "random_string"}}
    expected = copy.deepcopy(schema)
    out, ui = await vays_object_details.process("#/properties/x", schema, {}, PROPS)
    assert out == expected
    assert ui == {}
