"""
Tests for `lib.schema` -- the engine that renders the specs' JSON-Schema, walks
it through the json_schema/ui_schema processor plugins (`handle_schema`), and
validates the entity data against the result (`get`).

`handle_schema` is the structural recursion (bool/None handling, subschema
descent) and is exercised directly for the parts it owns. `get` is exercised as
an integration test over the real built-in plugins, asserting the contract the
routers/UI rely on: the validity verdict, the failure-location reporting, the
trivial-schema normalisation, and that the schema_formats plugins are wired into
the validator.
"""

import pytest

from app.lib import schema
from app.model.inp import OperationRequest
from app.model.out import User
from app.model.spc import Schema, Request
from app.model.err import SchemaSpecsError


def _op(operation="create", name="e1", entity=None):
    return OperationRequest(
        request_headers={}, request_ip="1.2.3.4",
        user=User(name="alice", email="a@x.com", full_name="A"),
        operation=operation, type="host", name=name, actions=[], entity=entity,
    )


async def _get(spec_dict, *, new_data, perms=("see", "edt"), operation="create",
               old_data=None):
    return await schema.get(
        _op(operation=operation), Schema.model_validate(spec_dict), Request(),
        {}, old_data or {}, list(perms), new_data,
    )


# ----- get: validity verdict + reporting -----

async def test_get_valid_data_builds_schema_and_ui():
    out = await _get(
        {"type": "object",
         "properties": {"owner": {"type": "string", "vays_category": "Main"}},
         "required": ["owner"]},
        new_data={"owner": "bob"},
    )
    assert out.valid is True
    # additionalProperties:false is injected by the plugin
    assert out.json_schema["additionalProperties"] is False
    # the ui_schema is the Categorization tree, control under the named category
    cat = out.ui_schema["elements"][0]
    assert cat["label"] == "Main"
    assert cat["elements"][0]["scope"] == "#/properties/owner"


async def test_get_invalid_data_reports_message_and_locations():
    out = await _get(
        {"type": "object",
         "properties": {"owner": {"type": "string", "vays_category": "M"}}},
        new_data={"owner": 5},
    )
    assert out.valid is False
    assert out.validator == "type"
    assert "not of type 'string'" in out.message
    assert out.json_schema_loc == "#/properties/owner/type"
    assert out.data_loc == "#/owner"


async def test_get_oneOf_subschema_is_preserved_and_validated():
    out = await _get(
        {"type": "object",
         "properties": {"a": {"oneOf": [{"const": "x"}, {"const": "y"}],
                              "vays_category": "X"}}},
        new_data={"a": "x"},
    )
    assert out.valid is True
    assert out.json_schema["properties"]["a"]["oneOf"] == [
        {"const": "x"}, {"const": "y"}
    ]


# ----- get: plugin-driven structural changes -----

async def test_get_unchangable_property_removed_on_change():
    out = await _get(
        {"type": "object",
         "properties": {
             "keep": {"type": "string", "vays_category": "X"},
             "drop": {"type": "string", "yac_changable": False, "vays_category": "X"},
         }},
        new_data={}, operation="change", perms=("edt",),
    )
    props = out.json_schema["properties"]
    assert "keep" in props and "drop" not in props


async def test_get_toplevel_removed_without_perms_becomes_not_schema():
    # On change a user lacking edt/add gets the whole schema removed; `get`
    # normalises the None into the always-failing `{"not": {}}`.
    out = await _get(
        {"type": "object", "properties": {"a": {"type": "string"}}},
        new_data={}, operation="change", perms=(),
    )
    assert out.json_schema == {"not": {}}
    assert out.valid is False


# ----- get: schema_formats plugin wiring -----

async def test_get_format_checker_uses_schema_formats_plugin():
    spec = {"type": "object",
            "properties": {"key": {"type": "string", "format": "ssh_key",
                                   "vays_category": "X"}}}
    bad = await _get(spec, new_data={"key": "definitely-not-an-ssh-key"})
    assert bad.valid is False and bad.validator == "format"


# ----- handle_schema: structural recursion it owns -----

async def test_handle_schema_bool_passthrough():
    # a boolean schema is returned untouched (no plugin descent)
    out, ui, ctx = await schema.handle_schema("#", True, {}, {}, {"operation": "read"})
    assert out is True and ui == {} and ctx == {}
    out, _, _ = await schema.handle_schema("#", False, {}, {}, {"operation": "read"})
    assert out is False


async def test_handle_schema_rejects_non_schema():
    with pytest.raises(SchemaSpecsError):
        await schema.handle_schema("#", 123, {}, {}, {"operation": "read"})


async def test_handle_schema_array_subschema_must_be_list():
    # `oneOf` present but not a list -> structural error
    with pytest.raises(SchemaSpecsError):
        await schema.handle_schema(
            "#", {"type": "object", "oneOf": {"not": "a list"}}, {}, {},
            {"operation": "read"},
        )


async def test_handle_schema_object_subschema_must_be_object():
    with pytest.raises(SchemaSpecsError):
        await schema.handle_schema(
            "#", {"type": "object", "properties": ["not", "an", "object"]}, {}, {},
            {"operation": "read"},
        )
