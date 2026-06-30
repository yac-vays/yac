"""
Tests for the built-in `json_schema` processor plugins that shape the generated
schema. These run per (sub)schema location via `lib.schema.handle_schema`; here
they are exercised directly (one `processor.process(loc, schema, ctx, props)`
call) since each is a pure transform of the schema dict.
"""

from app.plugin.json_schema.add_consts import processor as add_consts
from app.plugin.json_schema.additional_properties import processor as additional_properties
from app.plugin.json_schema.required_defaults import processor as required_defaults
from app.plugin.json_schema.yac_editable import processor as yac_editable
from app.plugin.json_schema.yac_if_cleanup import processor as yac_if_cleanup
from app.plugin.json_schema.yac_optional import processor as yac_optional


async def _run(processor, schema, props, ctx=None):
    out, _ = await processor.process("#", schema, ctx or {}, props)
    return out


async def _process(processor, schema, props, ctx=None):
    """Like `_run` but returns the raw (schema-or-None, ctx) so a removed
    subschema (None) can be asserted."""
    return await processor.process("#", schema, ctx or {}, props)


# ----- required_defaults -----

async def test_required_defaults_materialises_object_and_array():
    # A required object (with properties) / array without an explicit default get
    # default {} / [] so VAYS can cascade nested defaults and the required-but-
    # missing error is not invisible. (Regression for the yac_if subschema case.)
    schema = {
        "type": "object",
        "required": ["obj", "arr", "flag"],
        "properties": {
            "obj": {"type": "object", "properties": {"x": {"type": "string"}}},
            "arr": {"type": "array", "items": {}},
            "flag": {"type": "boolean"},
        },
    }
    out = await _run(required_defaults, schema, {"operation": "edit"})
    assert out["properties"]["obj"]["default"] == {}
    assert out["properties"]["arr"]["default"] == []
    assert out["properties"]["flag"]["default"] is False


async def test_required_defaults_const_gets_const_value():
    schema = {
        "type": "object",
        "required": ["k"],
        "properties": {"k": {"const": "fixed"}},
    }
    out = await _run(required_defaults, schema, {"operation": "edit"})
    assert out["properties"]["k"]["default"] == "fixed"


async def test_required_defaults_skips_optional_and_existing_default():
    schema = {
        "type": "object",
        "required": ["a"],  # b not required
        "properties": {
            "a": {"type": "object", "properties": {}, "default": {"keep": 1}},
            "b": {"type": "object", "properties": {}},
        },
    }
    out = await _run(required_defaults, schema, {"operation": "edit"})
    assert out["properties"]["a"]["default"] == {"keep": 1}  # untouched
    assert "default" not in out["properties"]["b"]  # not required -> no default


async def test_required_defaults_noop_on_read():
    # On read the schema is for display; injecting synthetic defaults would
    # misrepresent what is stored.
    schema = {
        "type": "object",
        "required": ["obj"],
        "properties": {"obj": {"type": "object", "properties": {}}},
    }
    out = await _run(required_defaults, schema, {"operation": "read"})
    assert "default" not in out["properties"]["obj"]


# ----- additional_properties -----

async def test_additional_properties_defaults_to_false():
    out = await _run(additional_properties, {"type": "object", "properties": {}}, {})
    assert out["additionalProperties"] is False


async def test_additional_properties_respects_explicit():
    out = await _run(
        additional_properties, {"type": "object", "additionalProperties": True}, {}
    )
    assert out["additionalProperties"] is True


# ----- yac_optional -----

async def test_yac_optional_builds_required_list():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string", "yac_optional": True},
        },
    }
    out = await _run(yac_optional, schema, {})
    assert out["required"] == ["a"]  # b is optional
    # the marker keyword is consumed
    assert "yac_optional" not in out["properties"]["b"]


# ----- add_consts -----

async def test_add_consts_preserves_existing_data_as_const():
    # A key present in the committed entity but not defined by the schema is
    # surfaced as a read-only const (on edit), so it is preserved, not dropped.
    schema = {"type": "object", "properties": {"known": {"type": "string"}}}
    props = {
        "operation": "edit",
        "old": {"data": {"known": "v", "extra": "keep"}},
        "user": {"perms": []},
    }
    out = await _run(add_consts, schema, props)
    assert out["properties"]["extra"]["const"] == "keep"


async def test_add_consts_noop_on_create():
    schema = {"type": "object", "properties": {"known": {"type": "string"}}}
    props = {"operation": "create", "old": {"data": {}}, "user": {"perms": []}}
    out = await _run(add_consts, schema, props)
    assert "extra" not in out["properties"]


# ----- yac_editable -----

async def test_yac_editable_removes_unchangable_subschema_on_change():
    schema, _ = await _process(
        yac_editable, {"type": "object", "yac_editable": False}, {"operation": "edit"}
    )
    assert schema is None  # removed -> field cannot be modified

    # editable=True (or non-edit op) keeps the schema and drops the marker.
    out = await _run(
        yac_editable, {"type": "object", "yac_editable": True, "x": 1}, {"operation": "edit"}
    )
    assert "yac_editable" not in out and out["x"] == 1
    out = await _run(
        yac_editable, {"type": "object", "yac_editable": False, "x": 1}, {"operation": "create"}
    )
    assert "yac_editable" not in out  # not enforced outside edit


# ----- yac_if_cleanup -----

async def test_yac_if_cleanup_drops_false_subschema():
    # yac_if left at False (condition unmet) -> the subschema is removed.
    schema, _ = await _process(yac_if_cleanup, {"type": "object", "yac_if": False}, {})
    assert schema is None
    # no yac_if marker -> untouched.
    out = await _run(yac_if_cleanup, {"type": "object", "x": 1}, {})
    assert out == {"type": "object", "x": 1}
