"""
Tests for the repo helpers added for limit correctness: `get_resolved` (the
backward-compatible default on `IRepoSession`) and `lib.repo.load_data_resolved`,
which report a symlink's target alongside its content.
"""

from app.lib import repo
from app.model.int import Entity
from app.model.out import TypeOption
from app.model.spc import Type


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
