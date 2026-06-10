"""
Tests for the repo helpers added for limit correctness: `get_resolved` (the
backward-compatible default on `IRepoSession`) and `lib.repo.load_data_resolved`,
which report a symlink's target alongside its content.
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


async def test_load_data_resolved_parses_and_reports_link(fake_repo):
    rpo = fake_repo(files={"A": "cpus: 4\nram: 8"}, links={"B": "A"})
    data, target = await repo.load_data_resolved(rpo, "host", "A")
    assert data == {"cpus": 4, "ram": 8}
    assert target is None

    data, target = await repo.load_data_resolved(rpo, "host", "B")
    assert data == {"cpus": 4, "ram": 8}
    assert target == "A"


async def test_load_data_resolved_tolerates_bad_yaml(fake_repo):
    rpo = fake_repo(files={"A": "::: not yaml :::"})
    data, target = await repo.load_data_resolved(rpo, "host", "A")
    assert data == {}
    assert target is None


# ----- load_data (best-effort, content-cache backed) -----

async def test_load_data_parses_yaml(fake_repo):
    rpo = fake_repo(files={"A": "cpu: 4\nram: 8"})
    assert await repo.load_data(rpo, "host", "A") == {"cpu": 4, "ram": 8}


async def test_load_data_empty_content_is_empty_dict(fake_repo):
    rpo = fake_repo(files={"A": ""})
    assert await repo.load_data(rpo, "host", "A") == {}


async def test_load_data_bad_yaml_is_empty_dict(fake_repo):
    rpo = fake_repo(files={"A": "::: not yaml :::"})
    assert await repo.load_data(rpo, "host", "A") == {}


async def test_load_data_repo_error_is_empty_dict():
    class _Raising(IRepoSession):
        async def get_hash(self): return "h"
        async def list(self, type): return []
        async def exists(self, type, name): return False
        async def is_link(self, type, name): return False
        async def get_link(self, type, name): return ""
        async def get(self, type, name):
            raise RepoNotFound("nope")
        async def write(self, *a): ...
        async def write_rename(self, *a): ...
        async def copy(self, *a): ...
        async def link(self, *a): ...
        async def delete(self, *a): ...

    assert await repo.load_data(_Raising(), "host", "x") == {}


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


async def test_get_entities_change_uses_path_and_entity_names(fake_repo):
    rpo = fake_repo(files={"a": "cpu: 4"})
    op = _op(operation="change", name="a", entity=NewEntity(name="a2", yaml=""))
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
