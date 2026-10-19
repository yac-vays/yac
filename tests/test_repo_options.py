"""
Type `options` (the extra list columns): a plain name reads a top-level key,
a data-loc (`#/a/b`) reads a value below the top level.
"""

from app.lib.repo import to_detailed_entity
from app.model.int import Entity
from app.model.out import TypeOption
from app.model.spc import Type

DATA = {
    "os": "deb12",
    "nothing": None,
    "spec": {"users": ["alice", "bob"], "description": "Hello", "ports": [80, 443]},
}


def _options(*opts: TypeOption, data=None) -> dict:
    typ = Type(name="t", title="T", options=list(opts))
    entity = Entity(name="e1", data=DATA if data is None else data)
    return to_detailed_entity(entity, ["see"], "hash", typ).options


def test_plain_name_reads_top_level_key():
    assert _options(TypeOption(name="os", title="OS")) == {"os": "deb12"}


def test_plain_name_is_not_split_on_dots():
    # A dotted name stays a (missing) top-level key, it is no path.
    assert _options(TypeOption(name="spec.users", title="Users")) == {}


def test_plain_name_keeps_explicit_null():
    # unchanged behaviour: a present key wins over the default, even if null
    assert _options(TypeOption(name="nothing", title="N", default="x")) == {"nothing": None}


def test_loc_reads_nested_values():
    assert _options(
        TypeOption(name="#/spec/users", title="Users"),
        TypeOption(name="#/spec/description", title="Description"),
        TypeOption(name="#/spec/ports/1", title="Second Port"),
    ) == {
        "#/spec/users": ["alice", "bob"],
        "#/spec/description": "Hello",
        "#/spec/ports/1": 443,
    }


def test_loc_missing_value_uses_default_or_is_omitted():
    assert _options(
        TypeOption(name="#/spec/missing", title="Missing"),
        TypeOption(name="#/nope/deeper", title="Deeper", default="-"),
        TypeOption(name="#/spec/ports/7", title="Out Of Range"),
        TypeOption(name="#/spec/users/x", title="Bad Index"),
    ) == {"#/nope/deeper": "-"}


def test_loc_without_data():
    assert _options(TypeOption(name="#/spec/users", title="Users"), data={}) == {}
