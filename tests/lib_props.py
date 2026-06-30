"""
Tests for `lib.props` -- the pure builders that assemble the Jinja2 variable
dicts (`old`, `new`, `name`, `user`, `request`, ...) handed to specs templates
for each kind of evaluation (roles, schema, action, log, limits, namegen).

The interesting logic is the request-header filtering (`yac-<key>` lookup,
pattern matching, defaults) and the `name` precedence (new entity name beats
the path name) -- the rest is plain field plumbing, covered lightly to guard
the keys downstream templates rely on.
"""

from app.lib import props
from app.model.inp import OperationRequest, NewEntity
from app.model.out import User
from app.model.spc import Request


def _op(name="e1", entity=None, headers=None, operation="read", actions=None):
    return OperationRequest(
        request_headers=headers or {},
        request_ip="10.0.0.1",
        user=User(name="alice", email="a@example.com", full_name="A"),
        operation=operation,
        type="host",
        name=name,
        actions=actions or [],
        entity=entity,
    )


# ----- request header filtering -----

def test_headers_pattern_match_default_and_casefold():
    spec = Request(headers={"team": {"pattern": "^[a-z]+$", "default": "none"}})
    # header arrives lowercased + dash-joined as `yac-team`
    op = _op(headers={"yac-team": "infra"})
    got = props.get_types(op, spec)["request"]["headers"]
    assert got["team"] == "infra"

    # value violating the pattern falls back to default
    op = _op(headers={"yac-team": "Team-7"})
    got = props.get_types(op, spec)["request"]["headers"]
    assert got["team"] == "none"

    # absent header -> default
    got = props.get_types(_op(), spec)["request"]["headers"]
    assert got["team"] == "none"


def test_headers_underscore_key_maps_to_dashed_lookup():
    spec = Request(headers={"api_key": {"pattern": "^.+$", "default": ""}})
    op = _op(headers={"yac-api-key": "secret"})
    assert props.get_types(op, spec)["request"]["headers"]["api_key"] == "secret"


def test_headers_missing_pattern_defaults_to_empty_match_only():
    # No `pattern` key -> default pattern "^$", so only the empty string passes;
    # any real value is rejected and the (missing) default "" is used.
    spec = Request(headers={"x": {}})
    op = _op(headers={"yac-x": "anything"})
    assert props.get_types(op, spec)["request"]["headers"]["x"] == ""


# ----- name precedence -----

def test_get_action_name_prefers_new_entity_name():
    op = _op(name="old-name", entity=NewEntity(name="new-name", yaml=""))
    d = props.get_action(op, Request())
    assert d["name"] == "new-name"
    assert d["old"]["name"] == "old-name"
    assert d["new"]["name"] == "new-name"


def test_get_action_name_falls_back_to_path_name():
    op = _op(name="old-name", entity=None)
    d = props.get_action(op, Request())
    assert d["name"] == "old-name"
    assert d["new"]["name"] is None


def test_get_action_entity_without_name_uses_path_name():
    # NewEntity with name=None -> `op.entity.name` is falsy, so `name` is op.name
    op = _op(name="path-name", entity=NewEntity(name=None, yaml=""))
    assert props.get_action(op, Request())["name"] == "path-name"


# ----- log / roles / schema shape -----

def test_get_log_uses_path_name_only():
    op = _op(name="host7")
    d = props.get_log(op, Request())
    assert d["name"] == "host7" and d["old"]["name"] == "host7"


def test_get_roles_threads_old_data_and_request_fields():
    op = _op(name="h", operation="edit", actions=["a1"])
    d = props.get_roles(op, Request(), {"owner": "alice"})
    assert d["old"]["data"] == {"owner": "alice"}
    assert d["operation"] == "edit" and d["actions"] == ["a1"]
    assert d["type"] == "host" and d["request"]["ip"] == "10.0.0.1"
    assert d["user"]["name"] == "alice"


def test_get_roles_none_old_data_becomes_empty_dict():
    d = props.get_roles(_op(), Request(), None)
    assert d["old"]["data"] == {}


def test_get_schema_attaches_perms_to_user():
    op = _op(name="h", entity=NewEntity(name="h2", yaml=""))
    d = props.get_schema(op, Request(), {"a": 1}, ["see", "edt"], {"b": 2}, {"ctx": 1})
    assert d["user"]["perms"] == ["see", "edt"]
    assert d["old"]["data"] == {"a": 1} and d["new"]["data"] == {"b": 2}
    assert d["context"] == {"ctx": 1}
    assert d["name"] == "h2"


def test_get_namegen_carries_list_and_new_data():
    d = props.get_namegen(_op(), Request(), ["a", "b"], {"k": "v"})
    assert d["old"]["list"] == ["a", "b"] and d["new"]["data"] == {"k": "v"}


def test_get_limits_base_includes_context_no_entity_fields():
    d = props.get_limits_base(_op(operation="create"), Request(), {"c": 1})
    assert d["context"] == {"c": 1} and d["operation"] == "create"
    # the per-entity `old`/`new`/`name` keys are added by the caller, not here
    assert "old" not in d and "new" not in d and "name" not in d
