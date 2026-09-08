"""
Tests for the built-in `json_schema` processor plugins that shape the generated
schema. These run per (sub)schema location via `lib.schema.handle_schema`; here
they are exercised directly (one `processor.process(loc, schema, ctx, props)`
call) since each is a pure transform of the schema dict.
"""

from app.consts import REMOVED
from app.plugin.json_schema.add_consts import processor as add_consts
from app.plugin.json_schema.additional_properties import processor as additional_properties
from app.plugin.json_schema.required_defaults import processor as required_defaults
from app.plugin.json_schema.yac_editable import processor as yac_editable
from app.plugin.json_schema.removed_cleanup import processor as removed_cleanup
from app.plugin.json_schema.yac_optional import processor as yac_optional
from app.plugin.json_schema.yac_perms import processor as yac_perms


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


async def test_add_consts_unknown_key_is_optional_only_with_cln():
    # Not defined by the schema at all: exactly what "cln" is for.
    def schema():
        return {"type": "object", "properties": {"known": {"type": "string"}}}
    old = {"data": {"known": "v", "extra": "keep"}}
    out = await _run(add_consts, schema(), {"operation": "edit", "old": old, "user": {"perms": ["edt"]}})
    assert out["properties"]["extra"] == {"const": "keep", "yac_optional": False}
    out = await _run(add_consts, schema(), {"operation": "edit", "old": old, "user": {"perms": ["edt", "cln"]}})
    assert out["properties"]["extra"] == {"const": "keep", "yac_optional": True}


async def test_add_consts_perms_removed_key_is_always_required():
    # Defined by the schema but removed by yac_perms / yac_editable: the
    # marker is replaced by an immutable const, even for "cln" holders.
    for reason in ("perms", "editable"):
        schema = {"type": "object", "properties": {"guarded": {REMOVED: reason, "not": True}}}
        props = {
            "operation": "edit",
            "old": {"data": {"guarded": "secret"}},
            "user": {"perms": ["edt", "cln"]},
        }
        out, ctx = await _process(add_consts, schema, props)
        assert out["properties"]["guarded"] == {"const": "secret"}
        assert ctx["add_consts_state"]["#/properties/guarded"] == reason


async def test_add_consts_leaves_if_removed_key_alone():
    # Defined by the schema but its yac_if is false: the data has to go, so
    # no const is injected (removed_cleanup drops the marker afterwards).
    schema = {"type": "object", "properties": {"cond": {REMOVED: "if", "not": True}}}
    props = {"operation": "edit", "old": {"data": {"cond": "stale"}}, "user": {"perms": ["edt"]}}
    out, ctx = await _process(add_consts, schema, props)
    assert out["properties"]["cond"] == {REMOVED: "if", "not": True}
    assert "add_consts_state" not in ctx


async def test_add_consts_noop_on_create():
    schema = {"type": "object", "properties": {"known": {"type": "string"}}}
    props = {"operation": "create", "old": {"data": {}}, "user": {"perms": []}}
    out = await _run(add_consts, schema, props)
    assert "extra" not in out["properties"]


# ----- yac_editable -----

async def test_yac_editable_removes_unchangable_subschema_on_change():
    schema, _ = await yac_editable.process(
        "#/properties/x", {"type": "object", "yac_editable": False}, {}, {"operation": "edit"}
    )
    # marked as removed (dropped by removed_cleanup) -> field cannot be modified
    assert schema == {REMOVED: "editable", "not": True}
    # at the top level there is no parent object to consult the marker
    schema, _ = await _process(
        yac_editable, {"type": "object", "yac_editable": False}, {"operation": "edit"}
    )
    assert schema is None

    # editable=True (or non-edit op) keeps the schema and drops the marker.
    out = await _run(
        yac_editable, {"type": "object", "yac_editable": True, "x": 1}, {"operation": "edit"}
    )
    assert "yac_editable" not in out and out["x"] == 1
    out = await _run(
        yac_editable, {"type": "object", "yac_editable": False, "x": 1}, {"operation": "create"}
    )
    assert "yac_editable" not in out  # not enforced outside edit


# ----- yac_perms -----

def _perms_ctx():
    # process() seeds this at loc "#"; the tests below enter at deeper locs.
    return {"yac_perms": {"#": ["add", "edt"]}}


def _perms_props(perms):
    return {"operation": "edit", "user": {"perms": perms}}


async def test_yac_perms_removes_guarded_subschema_without_side_effects():
    # Missing perm -> subschema removed. Removal is the WHOLE write-side
    # enforcement: nothing may be recorded in the shared context (read
    # protection below entity level does not exist; add_consts echoes the
    # stored value back as a const, which also pins it).
    schema, ctx = await yac_perms.process(
        "#/properties/top_secret",
        {"type": "string", "yac_perms": ["secrets"]},
        _perms_ctx(),
        _perms_props(["edt"]),
    )
    assert schema == {REMOVED: "perms", "not": True}
    assert set(ctx.keys()) == {"yac_perms"}  # no removal bookkeeping


async def test_yac_perms_removed_oneof_option_has_no_field_wide_effect():
    # Regression: a perm-gated oneOf option (enum-style) is removed for a
    # user without the perm — with no side effects, so the field stays
    # writable via the remaining options (previously the removal was
    # recorded and *any* write to the field was rejected).
    schema, ctx = await yac_perms.process(
        "#/properties/os/oneOf/1",
        {"const": "special", "yac_perms": ["secrets"]},
        _perms_ctx(),
        _perms_props(["edt"]),
    )
    assert schema == {REMOVED: "perms", "not": True}
    assert set(ctx.keys()) == {"yac_perms"}


async def test_yac_perms_keeps_subschema_for_holder_and_consumes_keyword():
    schema, _ = await yac_perms.process(
        "#/properties/top_secret",
        {"type": "string", "yac_perms": ["secrets"]},
        _perms_ctx(),
        _perms_props(["edt", "secrets"]),
    )
    assert schema == {"type": "string"}


# ----- removed_cleanup -----

async def test_removed_cleanup_drops_marked_children():
    # Marked subschemas (by yac_if / yac_perms / yac_editable) are dropped by
    # the cleanup running on their PARENT: from properties, composition
    # lists and single-subschema keywords alike.
    marker = {REMOVED: "perms", "not": True}
    schema = {
        "type": "object",
        "properties": {"keep": {"type": "string"}, "drop": marker},
        "oneOf": [{"const": "a"}, marker],
        "items": marker,
        "then": {"type": "string"},
    }
    out = await _run(removed_cleanup, schema, {})
    assert out == {
        "type": "object",
        "properties": {"keep": {"type": "string"}},
        "oneOf": [{"const": "a"}],
        "then": {"type": "string"},
    }
    # a marker itself is left alone: this is its own post-order pass, only
    # the parent's pass may drop it (after add_consts consulted it there)
    out = await _run(removed_cleanup, dict(marker), {})
    assert out == marker
