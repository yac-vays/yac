"""
Stored keys without a (live) subschema in a *shared* subschema (e.g. array
items): add_consts injects an optional `enum` of all stored values
(instead of a const of the first object's value, which made every other object
invalid), and lib.schema rejects using a value more often than before.
"""

import pytest

from app.consts import INJECTED
from app.consts import REMOVED
from app.lib import locs
from app.lib import schema
from app.model.inp import OperationRequest
from app.model.out import User
from app.model.spc import Request, Schema
from app.plugin.json_schema.add_consts import processor as add_consts

ITEMS_LOC = "#/properties/users/items"

OLD = {
    "users": [
        {"name": "a", "legacy": "old-a"},
        {"name": "b", "legacy": "old-b"},
        {"name": "c"},
    ]
}


def _items_schema(**legacy):
    props = {"name": {"type": "string"}}
    if legacy:
        props["legacy"] = legacy
    return {"type": "object", "properties": props}


def _props(perms=("edt",), old=None):
    return {"operation": "edit", "old": {"data": old or OLD}, "user": {"perms": list(perms)}}


async def _get(spec, new_data, old_data=None, perms=("see", "edt")):
    return await schema.get(
        OperationRequest(
            request_headers={}, request_ip="1.2.3.4",
            user=User(name="alice", email="a@x.com", full_name="A"),
            operation="edit", type="host", name="e1", actions=[], entity=None,
        ),
        Schema.model_validate(spec), Request(), {}, old_data or OLD, list(perms), new_data,
    )


def _users_spec(**legacy):
    return {
        "type": "object",
        "properties": {"users": {"type": "array", "items": _items_schema(**legacy)}},
    }


def _users(*legacies):
    return {
        "users": [
            {"name": str(i)} | ({} if legacy is None else {"legacy": legacy})
            for i, legacy in enumerate(legacies)
        ]
    }


# ----- locs.shared -----


def test_shared_locs():
    assert locs.shared("#/properties/users/items")
    assert locs.shared("#/properties/users/items/properties/x")
    assert locs.shared("#/properties/m/patternProperties/^a$")
    assert locs.shared("#/properties/l/contains")
    assert not locs.shared("#")
    assert not locs.shared("#/properties/items")
    assert not locs.shared("#/properties/a/properties/items/properties/b")


# ----- add_consts -----


async def test_unknown_key_in_items_gets_optional_enum_of_all_values():
    out, ctx = await add_consts.process(ITEMS_LOC, _items_schema(), {}, _props())
    assert out["properties"]["legacy"] == {
        "enum": ["old-a", "old-b"], "yac_optional": True, INJECTED: True,
    }
    assert ctx["add_consts_shared"][f"{ITEMS_LOC}/properties/legacy"] == ["old-a", "old-b"]


async def test_enum_keeps_values_python_equates():
    old = {"users": [{"x": 1}, {"x": True}, {"x": 1}]}
    out, _ = await add_consts.process(ITEMS_LOC, _items_schema(), {}, _props(old=old))
    assert out["properties"]["x"]["enum"] == [1, True]


async def test_removed_key_in_items_is_injected_too():
    schema_ = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "legacy": {REMOVED: "editable", "not": True}},
    }
    out, ctx = await add_consts.process(ITEMS_LOC, schema_, {}, _props())
    assert out["properties"]["legacy"] == {
        "enum": ["old-a", "old-b"], "yac_optional": True, INJECTED: True,
    }
    assert ctx["add_consts_state"][f"{ITEMS_LOC}/properties/legacy"] == "editable"


async def test_unshared_object_still_gets_a_const():
    schema_ = {"type": "object", "properties": {"guarded": {REMOVED: "perms", "not": True}}}
    props = {"operation": "edit", "old": {"data": {"guarded": "s"}}, "user": {"perms": ["edt"]}}
    out, ctx = await add_consts.process("#", schema_, {}, props)
    assert out["properties"]["guarded"] == {"const": "s"}
    assert "add_consts_shared" not in ctx


# ----- lib.schema: end to end -----


@pytest.mark.parametrize(
    "legacies",
    [
        ("old-a", "old-b", None),  # unchanged
        ("old-b", "old-a", None),  # swapped / reordered
        ("old-a", None, "old-b"),  # moved to another item
        ("old-a",),  # items removed
        (None, None, None, None),  # removed from the items, new item
    ],
)
async def test_stored_values_can_be_kept_moved_or_removed(legacies):
    out = await _get(_users_spec(), _users(*legacies))
    assert out.valid, out.message


async def test_stored_value_cannot_be_duplicated():
    out = await _get(_users_spec(), _users("old-a", "old-b", "old-a"))
    assert not out.valid
    assert out.data_loc == "#/users/2/legacy"
    assert out.json_schema_loc == f"{ITEMS_LOC}/properties/legacy/enum"
    assert "'old-a'" in out.message


async def test_stored_value_used_twice_before_may_stay_twice():
    old = _users("same", "same")
    assert (await _get(_users_spec(), _users("same", "same"), old)).valid
    assert not (await _get(_users_spec(), _users("same", "same", "same"), old)).valid


async def test_new_value_is_rejected_by_the_enum():
    out = await _get(_users_spec(), _users("old-a", "new"))
    assert not out.valid
    assert out.data_loc == "#/users/1/legacy"


def _rows(out, path=(0, 0)):
    ctrl = out.ui_schema["elements"][path[0]]["elements"][path[1]]
    return [e["scope"] for e in ctrl["options"]["details"]["elements"]]


@pytest.mark.parametrize("legacy", [None, {"type": "string", "yac_editable": False}])
async def test_injected_key_in_items_is_no_part_of_the_form(legacy):
    # Unknown (legacy None) or not editable: like outside of arrays, the
    # stored value is kept but not shown, even without any vays_options.
    spec = _users_spec(**(legacy or {}))
    spec["properties"]["users"]["vays_category"] = "General"
    out = await _get(spec, _users("old-b", None, "old-a"))
    assert out.valid, out.message
    assert _rows(out) == ["#/properties/name"]
    items = out.json_schema["properties"]["users"]["items"]
    assert items["properties"]["legacy"] == {"enum": ["old-a", "old-b"]}
    assert "legacy" not in items["required"]
    assert not (await _get(spec, _users("x"))).valid


async def test_injected_key_in_items_of_a_nested_array():
    spec = {
        "type": "object",
        "properties": {
            "cfg": {
                "type": "object",
                "vays_category": "General",
                "properties": {"users": {"type": "array", "items": _items_schema()}},
            }
        },
    }
    old = {"cfg": OLD}
    out = await _get(spec, old, old)
    assert out.valid, out.message
    users = out.ui_schema["elements"][0]["elements"][0]["options"]["detail"]["elements"][0]
    assert [e["scope"] for e in users["options"]["details"]["elements"]] == ["#/properties/name"]
    assert INJECTED not in str(out.json_schema)


async def test_nested_arrays_count_over_all_objects():
    spec = {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"users": {"type": "array", "items": _items_schema()}},
                },
            }
        },
    }
    old = {"groups": [{"users": [{"name": "a", "legacy": "x"}]}, {"users": [{"name": "b"}]}]}
    moved = {"groups": [{"users": [{"name": "a"}]}, {"users": [{"name": "b", "legacy": "x"}]}]}
    assert (await _get(spec, moved, old)).valid
    dup = {"groups": [{"users": [{"name": "a", "legacy": "x"}]}, {"users": [{"name": "b", "legacy": "x"}]}]}
    out = await _get(spec, dup, old)
    assert not out.valid and out.data_loc == "#/groups/1/users/0/legacy"
