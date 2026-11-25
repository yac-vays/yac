"""
Tests for the repo helpers: `get_resolved` / `get_all` (the backward-compatible
defaults on `IRepoSession`, which report a symlink's target alongside its
content) and what `lib.repo` builds on them.
"""

import pytest

from app.lib import repo
from app.model.inp import OperationRequest, CopyEntity, LinkEntity, NewEntity
from app.model.int import Entity
from app.model.out import TypeOption, User
from app.model.err import RepoError, RepoNotFound, RepoClientError, RepoSpecsError
from app.model.plg import IRepoSession
from app.model.spc import Type, Specs, Request, Role, Sets
from app import consts


def _op(operation="read", name="a", entity=None, type_name="host"):
    return OperationRequest(
        request_headers={}, request_ip="1.2.3.4",
        user=User(name="alice", email="a@x.com", full_name="A"),
        operation=operation, type=type_name, name=name, actions=[], entity=entity,
    )


def _specs(*role_maps, name_gen="uuid()", with_type=True):
    typ = Type.model_construct(
        name="host", name_pattern=consts.NAME_PATTERN, name_generator=name_gen,
        options=[],
    ) if with_type else None
    return Specs.model_construct(
        types=[], type=typ, roles=[Role(**m) for m in role_maps],
        sets=Sets(), request=Request(), context={},
    )


# ----- to_detailed_entity (pure projection) -----

def test_to_detailed_entity_basic_fields():
    de = repo.to_detailed_entity(
        Entity(name="e1", exists=True, yaml="a: 1\n", data={"a": 1}, is_link=False),
        ["see", "edt"],
        "hash1",
        Type.model_construct(options=[]),
    )
    assert de.name == "e1" and de.perms == ["see", "edt"] and de.hash == "hash1"
    assert de.data == {"a": 1} and de.yaml == "a: 1\n" and de.link is None


def test_to_detailed_entity_materialises_options_with_defaults():
    typ = Type.model_construct(options=[
        TypeOption(name="os", title="OS"),                       # present in data
        TypeOption(name="env", title="Env", default="prod"),     # falls back to default
        TypeOption(name="absent", title="Absent"),               # no value, no default -> skipped
    ])
    de = repo.to_detailed_entity(
        Entity(name="e1", exists=True, yaml="", data={"os": "linux"}, is_link=False),
        ["see"], "h", typ,
    )
    assert de.options == {"os": "linux", "env": "prod"}
    assert "absent" not in de.options


def test_to_detailed_entity_reports_link():
    de = repo.to_detailed_entity(
        Entity(name="lnk", exists=True, yaml="", data={}, is_link=True, link="target"),
        ["see"], "h", Type.model_construct(options=[]),
    )
    assert de.link == "target"


async def test_get_resolved_plain_file(fake_repo):
    rpo = fake_repo(files={"A": "x: 1"})
    content, target = await rpo.get_resolved("host", "A")
    assert content == "x: 1"
    assert target is None


async def test_get_resolved_link_reports_target_and_content(fake_repo):
    rpo = fake_repo(files={"A": "x: 1"}, links={"B": "A"})
    content, target = await rpo.get_resolved("host", "B")
    assert content == "x: 1"   # symlink is followed
    assert target == "A"       # ... and the target name is reported


# ----- get_all (the per-entity default on `IRepoSession`) -----

async def test_get_all_reads_every_entity_with_its_link_target(fake_repo):
    rpo = fake_repo(files={"A": "x: 1", "C": "x: 3"}, links={"B": "A"})
    assert await rpo.get_all("host") == {
        "A": ("x: 1", None),
        "B": ("x: 1", "A"),
        "C": ("x: 3", None),
    }


async def test_get_all_leaves_out_what_cannot_be_read(fake_repo):
    class _Dangling(fake_repo):
        async def get(self, type, name):
            if name == "B":
                raise RepoNotFound("The link target of B does not exist")
            return await super().get(type, name)

    rpo = _Dangling(files={"A": "x: 1"}, links={"B": "gone"})
    assert await rpo.get_all("host") == {"A": ("x: 1", None)}


# ----- read_type (one bulk read per type and repo state) -----

async def test_read_type_is_sorted_and_read_once_per_repo_state(fake_repo):
    class _Counting(fake_repo):
        reads = 0

        async def get_all(self, type):
            _Counting.reads += 1
            return dict(reversed((await super().get_all(type)).items()))

    rpo = _Counting(files={"b": "x: 2", "a": "x: 1"}, links={"c": "a"})
    stored = await repo.read_type("h-read-type-1", "host", rpo)
    assert list(stored) == ["a", "b", "c"]
    assert stored["c"] == ("x: 1", "a")
    assert await repo.read_type("h-read-type-1", "host", rpo) is stored
    assert _Counting.reads == 1
    # another repo state (or type) is read on its own
    await repo.read_type("h-read-type-2", "host", rpo)
    await repo.read_type("h-read-type-2", "vm", rpo)
    assert _Counting.reads == 3


async def test_get_entity_for_list_builds_the_entity_and_its_perms():
    op = _op()
    specs = _specs({"host:all:edt": "old.data.owner == user.name"})
    active = await repo.perms.get_active_role_set(op, specs)
    base = repo.props.get_roles_base(op, specs.request)

    old, p = await repo.get_entity_for_list("a", ("owner: alice", None), base, active)
    assert old.exists is True and old.is_link is False and old.link is None
    assert old.yaml == "owner: alice" and old.data == {"owner": "alice"}
    assert p == ["edt", "see"]

    old, p = await repo.get_entity_for_list("l", ("owner: bob", "b"), base, active)
    assert old.is_link is True and old.link == "b" and p == []

    with pytest.raises(RepoError):
        await repo.get_entity_for_list("x", ("::: not yaml :::", None), base, active)


# ----- parse_data (best-effort, content-cache backed) -----

def test_parse_data_parses_yaml():
    assert repo.parse_data("cpu: 4\nram: 8") == {"cpu": 4, "ram": 8}


def test_parse_data_empty_or_bad_yaml_is_empty_dict():
    assert repo.parse_data("") == {}
    assert repo.parse_data("::: not yaml :::") == {}


def test_parse_data_hands_out_an_own_copy():
    text = "users: [a]\nnested: {k: [1]}"
    first = repo.parse_data(text)
    first["users"].append("b")
    first["nested"]["k"].append(2)
    assert repo.parse_data(text) == {"users": ["a"], "nested": {"k": [1]}}


# ----- get_entities: name resolution + entity population -----
# Each test passes a unique `hash` so the repo-hash-keyed lookup cache never
# returns another test's entity.

async def test_get_entities_read_populates_old(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    old, new, p = await repo.get_entities(
        "h-read", rpo, _op(operation="read", name="a"), _specs()
    )
    assert old.exists is True and old.data == {"cpu": 4}
    assert old.is_link is False and new.name is None and p == []


async def test_get_entities_follows_link_metadata(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"}, links={"b": "a"})
    old, _, _ = await repo.get_entities(
        "h-link", rpo, _op(operation="read", name="b"), _specs()
    )
    assert old.is_link is True and old.link == "a"
    assert old.data == {"cpu": 4}  # link is followed for content


async def test_get_entities_copy_resolves_source_as_old(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    op = _op(operation="create", entity=CopyEntity(name="c", copy="a"))
    old, new, _ = await repo.get_entities("h-copy", rpo, op, _specs())
    assert old.name == "a" and old.exists is True   # the copy source
    assert new.name == "c"


async def test_get_entities_link_resolves_source_as_old(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    op = _op(operation="create", entity=LinkEntity(name="l", link="a"))
    old, new, _ = await repo.get_entities("h-linkop", rpo, op, _specs())
    assert old.name == "a" and new.name == "l"


async def test_get_entities_edit_uses_path_and_entity_names(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    op = _op(operation="edit", name="a", entity=NewEntity(name="a2", yaml=""))
    old, new, _ = await repo.get_entities("h-change", rpo, op, _specs())
    assert old.name == "a" and old.exists is True
    assert new.name == "a2"


async def test_get_entities_missing_old_is_unpopulated(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    old, _, _ = await repo.get_entities(
        "h-miss", rpo, _op(operation="read", name="ghost"), _specs()
    )
    assert old.exists is None and old.data is None


async def test_get_entities_returns_perms_from_roles(fake_repo):
    rpo = fake_repo(files={"a": "owner: alice"})
    specs = _specs({"host:all:edt": "old.data.owner == user.name"})
    old, _, p = await repo.get_entities(
        "h-perms", rpo, _op(operation="read", name="a"), specs
    )
    assert "edt" in p and "see" in p


async def test_get_entities_bad_yaml_raises_repo_error(fake_repo):
    rpo = fake_repo(files={"a": "::: not yaml :::"})
    with pytest.raises(RepoError):
        await repo.get_entities(
            "h-badyaml", rpo, _op(operation="read", name="a"), _specs()
        )


async def test_get_entities_no_type_skips_lookup(fake_repo):
    # type_exists False -> the entity is never read from the repo
    rpo = fake_repo(files={"a": "cpu: 4"})
    old, _, _ = await repo.get_entities(
        "h-notype", rpo, _op(operation="read", name="a"), _specs(with_type=False)
    )
    assert old.exists is None and old.data is None


# ----- gen_name -----

async def test_gen_name_renders_generator():
    name = await repo.gen_name(_op(), _specs(name_gen='"host-new"'), [], {})
    assert name == "host-new"


async def test_gen_name_rejects_invalid_pattern():
    with pytest.raises(RepoSpecsError):
        await repo.gen_name(_op(), _specs(name_gen='"bad name"'), [], {})


async def test_gen_name_rejects_duplicate():
    with pytest.raises(RepoSpecsError):
        await repo.gen_name(_op(), _specs(name_gen='"dup"'), ["dup"], {})


async def test_gen_name_requires_type():
    with pytest.raises(RepoClientError):
        await repo.gen_name(_op(), _specs(with_type=False), [], {})


async def test_gen_name_broken_generator_raises_specs_error():
    # an undefined variable in the generator expression -> J2Error -> RepoSpecsError
    with pytest.raises(RepoSpecsError):
        await repo.gen_name(_op(), _specs(name_gen="undefined_var.foo"), [], {})
