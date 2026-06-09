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
