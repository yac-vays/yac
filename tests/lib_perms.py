"""
Tests for `lib.perms` -- the permission resolution that is YAC's trust boundary.

Covers the permission-expansion table and the role-matching pipeline
(`get_active_role_set` prefilter + `get_from_roles`) for user-only and
entity-data-dependent role tests. Roles are `type:set:perm -> CEL/Jinja test`.
"""

from app.lib import perms
from app.model.inp import OperationRequest
from app.model.out import User
from app.model.spc import Request, Role, Sets, Specs

# Module-level helper (name-mangling does not apply at module scope; access by
# its literal name).
_expand = getattr(perms, "__expand_perms")


def _op(user="alice", name="e1", type_name="host", operation="read"):
    return OperationRequest(
        request_headers={},
        request_ip="1.2.3.4",
        user=User(name=user, email="a@example.com", full_name="A"),
        operation=operation,
        type=type_name,
        name=name,
        actions=[],
        entity=None,
    )


def _specs(*role_maps, sets=None):
    return Specs.model_construct(
        roles=[Role(**m) for m in role_maps],
        sets=Sets(**(sets or {})),
        request=Request.model_construct(headers={}),
        context={},
    )


# ----- permission expansion -----

def test_expand_perms_groups():
    assert _expand(["edt"]) == ["edt", "see"]            # edt implies see
    assert _expand(["cln"]) == ["cln", "see"]
    assert "adm" in _expand(["adm"]) and "del" in _expand(["adm"])
    assert _expand(["all"]) == [
        "act", "add", "cln", "cpy", "del", "edt", "lnk", "rnm", "see"
    ]


def test_expand_perms_plus_split_and_dedup():
    # `+`-joined perms expand and the result is a sorted unique set.
    assert _expand(["see+del"]) == ["del", "see"]
    assert _expand(["see", "see", "edt"]) == ["edt", "see"]
    # An unknown perm passes through unchanged.
    assert _expand(["xyz"]) == ["xyz"]


# ----- role matching -----

async def test_user_only_role_match_and_miss():
    specs = _specs({"host:all:edt": "user.name == 'alice'"})
    assert await perms.get_from_roles(_op("alice"), specs, {}) == ["edt", "see"]
    assert await perms.get_from_roles(_op("bob"), specs, {}) == []


async def test_role_for_other_type_is_ignored():
    specs = _specs({"other:all:edt": "true"})
    assert await perms.get_from_roles(_op("alice", type_name="host"), specs, {}) == []


async def test_entity_data_dependent_role():
    specs = _specs({"host:all:del": "old.data.owner == user.name"})
    assert await perms.get_from_roles(_op("alice"), specs, {"owner": "alice"}) == ["del"]
    assert await perms.get_from_roles(_op("alice"), specs, {"owner": "bob"}) == []


async def test_set_scoped_role():
    # The role only applies inside a named set; the set test is user-only here.
    specs = _specs(
        {"host:mine:edt": "true"},
        sets={"host": {"mine": "user.name == 'alice'"}},
    )
    assert await perms.get_from_roles(_op("alice"), specs, {}) == ["edt", "see"]
    assert await perms.get_from_roles(_op("bob"), specs, {}) == []


async def test_prefilter_drops_nonmatching_user_only_roles():
    specs = _specs(
        {"host:all:edt": "user.name == 'alice'"},   # matches
        {"host:all:del": "user.name == 'carol'"},   # filtered out at prefilter
    )
    active = await perms.get_active_role_set(_op("alice"), specs)
    perms_granted = {ar.perm for ar in active}
    assert perms_granted == {"edt"}


async def test_admin_shortcut_drops_redundant_conditional_roles():
    # An unconditional `all` role already covers everything a conditional role
    # could add, so the conditional role is dropped from per-entity evaluation.
    specs = _specs(
        {"host:all:all": "true"},
        {"host:all:edt": "old.data.owner == user.name"},
    )
    active = await perms.get_active_role_set(_op("alice"), specs)
    assert all(ar.set_test is None and ar.role_test is None for ar in active)


async def test_sets_of_a_type_named_like_a_pydantic_attribute():
    # `copy`, `json`, ... are attributes of every pydantic model.
    for type_name in ("copy", "json", "dict", "schema"):
        specs = _specs(
            {f"{type_name}:mine:edt": "true"},
            sets={type_name: {"mine": "user.name == 'alice'"}},
        )
        assert await perms.get_from_roles(_op("alice", type_name=type_name), specs, {}) == ["edt", "see"]
        assert await perms.get_from_roles(_op("bob", type_name=type_name), specs, {}) == []


async def test_sets_of_a_type_without_sets_never_match():
    for sets in ({}, {"host": None}):
        specs = _specs({"host:mine:edt": "true"}, sets=sets)
        assert await perms.get_from_roles(_op("alice"), specs, {}) == []


async def test_roles_that_cannot_add_anything_are_not_rendered(monkeypatch):
    rendered = []
    render_test = perms.j2.render_test

    async def counting(test, props):
        rendered.append(test)
        return await render_test(test, props)

    monkeypatch.setattr(perms.j2, "render_test", counting)
    specs = _specs(
        {"host:all:edt": "user.name == 'alice'"},  # without an entity test
        {"host:all:see": "old.data.a == 1"},  # see: granted by edt already
        {"host:all:del": "old.data.a == 1"},
        {"host:all:del+see": "old.data.b == 2"},  # nothing new once del matched
        {"host:all:act": "old.data.b == 2"},
    )
    active = await perms.get_active_role_set(_op(), specs)
    rendered.clear()

    p = await perms.get_from_roles(_op(), specs, {"a": 1, "b": 2}, active_roles=active)
    assert p == ["act", "del", "edt", "see"]
    assert rendered == ["old.data.a == 1", "old.data.b == 2"]

    # ... but it is, as long as it could
    rendered.clear()
    p = await perms.get_from_roles(_op(), specs, {"a": 0, "b": 2}, active_roles=active)
    assert p == ["act", "del", "edt", "see"]
    assert rendered == ["old.data.a == 1", "old.data.b == 2", "old.data.b == 2"]


async def test_a_set_is_tested_once_per_entity(monkeypatch):
    rendered = []
    render_test = perms.j2.render_test

    async def counting(test, props):
        rendered.append(test)
        return await render_test(test, props)

    monkeypatch.setattr(perms.j2, "render_test", counting)
    specs = _specs(
        {"host:labs:edt": "old.data.a == 1"},
        {"host:labs:del": "old.data.a == 2"},
        {"host:labs:act": "old.data.a == 1"},
        sets={"host": {"labs": "name.startswith('e')"}},
    )
    assert await perms.get_from_roles(_op(), specs, {"a": 1}) == ["act", "edt", "see"]
    assert rendered.count("name.startswith('e')") == 1

    rendered.clear()
    assert await perms.get_from_roles(_op(name="x1"), specs, {"a": 1}) == []
    assert rendered == ["name.startswith('e')"]


# ----- perms of the entity list are reused per caller and entity -----


def _counting_renders(monkeypatch) -> list:
    rendered = []
    render_test = perms.j2.render_test

    async def counting(test, props):
        rendered.append(test)
        return await render_test(test, props)

    monkeypatch.setattr(perms.j2, "render_test", counting)
    return rendered


async def _listed(op, specs, name, yaml_text, data):
    active = await perms.get_active_role_set(op, specs)
    return await perms.get_from_roles_for_entity(
        perms.props.get_roles_base(op, specs.request),
        active,
        name,
        data,
        signature=perms.list_signature(op, active),
        yaml_text=yaml_text,
    )


async def test_list_perms_are_reused_for_an_unchanged_entity(monkeypatch):
    specs = _specs({"host:all:edt": "old.data.owner == user.name"})
    op = _op(user="reuse-1")
    rendered = _counting_renders(monkeypatch)

    assert await _listed(op, specs, "e1", "owner: reuse-1", {"owner": "reuse-1"}) == ["edt", "see"]
    assert await _listed(op, specs, "e1", "owner: reuse-1", {"owner": "reuse-1"}) == ["edt", "see"]
    assert len(rendered) == 1

    # the entity changed, another entity, another user
    assert await _listed(op, specs, "e1", "owner: x", {"owner": "x"}) == []
    assert await _listed(op, specs, "e2", "owner: reuse-1", {"owner": "reuse-1"}) == ["edt", "see"]
    other = _op(user="reuse-2")
    assert await _listed(other, specs, "e1", "owner: reuse-1", {"owner": "reuse-1"}) == []
    assert len(rendered) == 4


async def test_list_perms_follow_the_roles_that_are_left_to_test(monkeypatch):
    """What the per-request prefilter decides is never reused."""
    op = _op(user="reuse-3")
    entity = ("e1", "owner: reuse-3", {"owner": "reuse-3"})
    owner = {"host:all:edt": "old.data.owner == user.name"}
    assert await _listed(op, _specs(owner), *entity) == ["edt", "see"]
    assert await _listed(
        op, _specs(owner, {"host:all:del": "name == 'e1'"}), *entity
    ) == ["del", "edt", "see"]
    assert await _listed(
        op, _specs(owner, {"host:all:adm": "user.name == 'reuse-3'"}), *entity
    ) == sorted(_expand(["adm"]))


async def test_list_perms_of_a_failed_test_are_not_kept(monkeypatch):
    lookups = []

    async def render_test(test, props):
        lookups.append(test)
        if len(lookups) == 1:
            raise perms.j2.J2Error("lookup timed out")
        return True

    monkeypatch.setattr(perms.j2, "render_test", render_test)
    specs = _specs({"host:all:edt": "lookup(old.data.owner)"})
    op = _op(user="reuse-4")
    assert await _listed(op, specs, "e1", "owner: a", {"owner": "a"}) == []
    assert await _listed(op, specs, "e1", "owner: a", {"owner": "a"}) == ["edt", "see"]
    assert await _listed(op, specs, "e1", "owner: a", {"owner": "a"}) == ["edt", "see"]
    assert len(lookups) == 2


async def test_list_perms_are_not_shared_without_need(monkeypatch):
    op = _op(user="reuse-5")
    unconditional = await perms.get_active_role_set(
        op, _specs({"host:all:edt": "user.name == 'reuse-5'"})
    )
    assert perms.list_signature(op, unconditional) is None

    conditional = await perms.get_active_role_set(
        op, _specs({"host:all:edt": "old.data.owner == user.name"})
    )
    assert perms.list_signature(op, conditional) is not None
    monkeypatch.setattr(perms.consts.ENV, "list_perms_ttl", 0)
    assert perms.list_signature(op, conditional) is None
