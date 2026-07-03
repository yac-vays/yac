"""
Tests for `lib.limits.measure`.

Covers the two limits edge cases that were security/UX holes:
  - a *symlink* to the entity being edited must be counted with the incoming
    data (it will hold that data once committed), not its stale on-disk copy;
  - a *schema-invalid* value (e.g. a non-number) must not abort the limit, so
    the validate endpoint can still report the real schema error.

`YAC_SPECS` is pointed at a minimal specs file by `conftest.py` so importing the
repo/limits layer does not exit at import time.
"""

import pytest

from app.lib import limits
from app.model.err import RequestError
from app.model.inp import NewEntity, OperationRequest, UpdateEntity
from app.model.int import Entity
from app.model.out import User
from app.model.spc import Request, Specs, Type, TypeLimit


def _specs_with(lim: TypeLimit) -> Specs:
    typ = Type.model_construct(limits=[lim])
    return Specs.model_construct(
        types=[typ], type=typ, request=Request.model_construct(headers={}), context={}
    )


def _edit_op(data: dict) -> OperationRequest:
    return OperationRequest(
        request_headers={},
        request_ip="",
        user=User(name="u", email="u@example.com", full_name="U"),
        operation="edit",
        type="host",
        name="A",
        actions=[],
        entity=UpdateEntity(name="A", data=data),
    )


# A=4, C=2 are files; B is a symlink to A.
def _repo(fake_repo):
    return fake_repo(files={"A": "cpus: 4", "C": "cpus: 2"}, links={"B": "A"})


_CPUS_LIMIT = dict(
    title="cpus", on=["edit", "create"], scope="true",
    value="old.data.cpus | default(0)", max="100",
)


async def test_symlink_to_edited_entity_counts_incoming_data(fake_repo):
    old = Entity(name="A", exists=True, data={"cpus": 4})
    lim = TypeLimit.model_construct(**_CPUS_LIMIT)
    usages = await limits.measure(
        "h", _repo(fake_repo), _edit_op({"cpus": 40}), _specs_with(lim), old, {"cpus": 40}
    )
    # A=40 (incoming) + B=40 (symlink -> A, the *new* value) + C=2 = 82.
    # The bug counted B with its stale on-disk copy (4) -> 46, bypassing the cap.
    assert usages[0].used == 82, usages[0].used


async def test_non_number_data_does_not_abort_measure(fake_repo):
    old = Entity(name="A", exists=True, data={"cpus": 4})
    lim = TypeLimit.model_construct(**_CPUS_LIMIT)
    # Incoming cpus is a (schema-invalid) string: the value cannot be coerced to
    # a number, but measure must not raise -- the contribution falls back to 0.
    usages = await limits.measure(
        "h", _repo(fake_repo), _edit_op({"cpus": "nope"}),
        _specs_with(lim), old, {"cpus": "nope"},
    )
    # incoming A -> 0, B mirrors A's new data -> 0, C -> 2; the point is no raise.
    assert usages[0].used == 2, usages[0].used


async def test_name_only_limit_unaffected(fake_repo):
    old = Entity(name="A", exists=True, data={"cpus": 4})
    # value/scope never read old.data -> fast name-only path, no data load.
    lim = TypeLimit.model_construct(
        title="count", on=["edit"], scope="true", value="1", max="100"
    )
    usages = await limits.measure(
        "h", _repo(fake_repo), _edit_op({"cpus": 40}), _specs_with(lim), old, {"cpus": 40}
    )
    assert usages[0].used == 3, usages[0].used  # A + B + C, one each


async def test_over_cap_reports_not_ok(fake_repo):
    old = Entity(name="A", exists=True, data={"cpus": 4})
    lim = TypeLimit.model_construct(
        title="cpus", on=["edit"], scope="true",
        value="old.data.cpus | default(0)", max="50",
    )
    usages = await limits.measure(
        "h", _repo(fake_repo), _edit_op({"cpus": 40}), _specs_with(lim), old, {"cpus": 40}
    )
    # 40 + 40 + 2 = 82 > 50
    assert usages[0].used == 82 and usages[0].max == 50 and usages[0].ok is False


def _create_op(yaml_text: str) -> OperationRequest:
    return OperationRequest(
        request_headers={}, request_ip="",
        user=User(name="u", email="u@example.com", full_name="U"),
        operation="create", type="host", name=None, actions=[],
        entity=NewEntity(name="new", yaml=yaml_text),
    )


async def test_scope_filters_out_of_scope_entities(fake_repo):
    rpo = fake_repo(files={"A": "kind: vm", "B": "kind: bare", "C": "kind: vm"})
    lim = TypeLimit.model_construct(
        title="vms", on=["create"], scope="old.data.kind == 'vm'", value="1", max="10"
    )
    # incoming vm + existing vms (A, C); the bare B is out of scope.
    usages = await limits.measure(
        "h", rpo, _create_op("kind: vm"), _specs_with(lim),
        Entity(name=None, exists=False), {"kind": "vm"},
    )
    assert usages[0].used == 3
    # an out-of-scope incoming entity does not count itself.
    usages = await limits.measure(
        "h", rpo, _create_op("kind: bare"), _specs_with(lim),
        Entity(name=None, exists=False), {"kind": "bare"},
    )
    assert usages[0].used == 2  # only existing A, C


async def test_enforce_raises_when_writer_scope_is_at_cap(fake_repo):
    """
    `enforce` is the writer-scope TOCTOU re-check: given a session whose
    entity count already consumed the cap (as if a concurrent create landed
    after the reader-scope measurement), it must raise the same RequestError
    the validator raises, so the HTTP response shape is unchanged.
    """
    rpo = fake_repo(files={"A": "x: 1", "B": "x: 1", "C": "x: 1"})
    lim = TypeLimit.model_construct(
        title="hosts", on=["create"], scope="true", value="1", max="3"
    )
    with pytest.raises(RequestError, match='Limit "hosts" reached: 4/3'):
        await limits.enforce(
            rpo, _create_op("x: 1"), _specs_with(lim),
            Entity(name=None, exists=False), {"x": 1},
        )


async def test_enforce_passes_below_cap(fake_repo):
    """Control: with a free slot left, `enforce` returns without raising."""
    rpo = fake_repo(files={"A": "x: 1", "B": "x: 1"})
    lim = TypeLimit.model_construct(
        title="hosts", on=["create"], scope="true", value="1", max="3"
    )
    await limits.enforce(
        rpo, _create_op("x: 1"), _specs_with(lim),
        Entity(name=None, exists=False), {"x": 1},
    )


async def test_limit_not_applicable_to_operation(fake_repo):
    # A limit that only applies on `create` yields no usage on an `edit`.
    rpo = fake_repo(files={"A": "cpus: 4"})
    lim = TypeLimit.model_construct(
        title="cpus", on=["create"], scope="true", value="old.data.cpus | default(0)", max="100"
    )
    usages = await limits.measure(
        "h", rpo, _edit_op({"cpus": 4}), _specs_with(lim),
        Entity(name="A", exists=True, data={"cpus": 4}), {"cpus": 4},
    )
    assert usages == []
