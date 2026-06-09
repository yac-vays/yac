"""
Tests for `lib.limits.measure`, focusing on the symlink edge case: when an
entity is edited, any other entity that is a *symlink* to it will hold the new
data once the edit is committed, so it must be counted with the incoming data --
not its stale on-disk copy. Otherwise a data-dependent limit can be bypassed by
putting the value in the link target.

Standalone (not wired into `tests/main.py`): importing `app.lib.limits` pulls in
`app.lib.repo`, which loads the repo plugin from the specs at import time, so a
minimal `YAC_SPECS` is provided before the import. Run with `PYTHONPATH=.`.
"""

import asyncio
import os
import tempfile

# Minimal specs so importing the repo layer (which initialises the repo plugin
# from the specs at import time) succeeds without a real /yac.yml.
_SPECS = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
_SPECS.write(
    'repo:\n  connection: ""\n  plugin: git_direct\n'
    "auth: {}\ntypes: []\nroles: []\nschema: {}\n"
    "sets: {}\ncontext: {}\nrequest: {}\nversion: \"\"\n"
)
_SPECS.close()
os.environ["YAC_SPECS"] = _SPECS.name

from app.lib import limits
from app.model.inp import OperationRequest, UpdateEntity
from app.model.int import Entity
from app.model.out import User
from app.model.plg import IRepoSession
from app.model.spc import Request, Specs, Type, TypeLimit


class _FakeSession(IRepoSession):
    """In-memory repo: A and C are files, B is a symlink to A."""

    def __init__(self):
        self.files = {"A": "cpus: 4", "C": "cpus: 2"}
        self.links = {"B": "A"}

    async def get_hash(self):
        return "h"

    async def list(self, type):
        return ["A", "B", "C"]

    async def exists(self, type, name):
        return name in self.files or name in self.links

    async def is_link(self, type, name):
        return name in self.links

    async def get_link(self, type, name):
        return self.links[name]

    async def get(self, type, name):
        return self.files[self.links.get(name, name)]

    async def write(self, *a): ...
    async def write_rename(self, *a): ...
    async def copy(self, *a): ...
    async def link(self, *a): ...
    async def delete(self, *a): ...


def _specs_with(lim: TypeLimit) -> Specs:
    typ = Type.model_construct(limits=[lim])
    return Specs.model_construct(
        types=[typ], type=typ, request=Request.model_construct(headers={}), context={}
    )


def _change_op(new_cpus: int) -> OperationRequest:
    return OperationRequest(
        request_headers={},
        request_ip="",
        user=User(name="u", email="u@example.com", full_name="U"),
        operation="change",
        type="host",
        name="A",
        actions=[],
        entity=UpdateEntity(name="A", data={"cpus": new_cpus}),
    )


def test_symlink_to_edited_entity_counts_incoming_data():
    rpo = _FakeSession()
    old = Entity(name="A", exists=True, data={"cpus": 4})
    # value reads old.data -> needs_data path. Edit A: cpus 4 -> 40.
    lim = TypeLimit.model_construct(
        title="cpus", on=["change", "create"], scope="true",
        value="old.data.cpus | default(0)", max="100",
    )
    usages = asyncio.run(
        limits.measure("h", rpo, _change_op(40), _specs_with(lim), old, {"cpus": 40})
    )
    # A=40 (incoming) + B=40 (symlink -> A, the new value!) + C=2 = 82.
    # The bug counted B with its stale on-disk copy (4) -> 46, bypassing the cap.
    assert usages[0].used == 82, usages[0].used


def test_non_number_data_does_not_abort_measure():
    # The incoming entity's `cpus` is a (schema-invalid) string. The limit value
    # `old.data.cpus` cannot be coerced to a number, but measure must not raise:
    # the contribution falls back to 0 so the validate endpoint can report the
    # real schema error instead of failing on the limit.
    rpo = _FakeSession()
    old = Entity(name="A", exists=True, data={"cpus": 4})
    lim = TypeLimit.model_construct(
        title="cpus", on=["change", "create"], scope="true",
        value="old.data.cpus | default(0)", max="100",
    )
    op = OperationRequest(
        request_headers={}, request_ip="",
        user=User(name="u", email="u@example.com", full_name="U"),
        operation="change", type="host", name="A", actions=[],
        entity=UpdateEntity(name="A", data={"cpus": "not-a-number"}),
    )
    usages = asyncio.run(
        limits.measure("h", rpo, op, _specs_with(lim), old, {"cpus": "not-a-number"})
    )
    # incoming A contributes 0 (bad data), B mirrors A's new data -> also 0,
    # C is a valid 2. The point is simply that it did not raise.
    assert usages[0].used == 2, usages[0].used


def test_name_only_limit_unaffected():
    rpo = _FakeSession()
    old = Entity(name="A", exists=True, data={"cpus": 4})
    # value/scope never read old.data -> fast (name-only) path, no link handling.
    lim = TypeLimit.model_construct(
        title="count", on=["change"], scope="true", value="1", max="100"
    )
    usages = asyncio.run(
        limits.measure("h", rpo, _change_op(40), _specs_with(lim), old, {"cpus": 40})
    )
    assert usages[0].used == 3, usages[0].used  # A + B + C, one each


def test():
    test_symlink_to_edited_entity_counts_incoming_data()
    test_non_number_data_does_not_abort_measure()
    test_name_only_limit_unaffected()


if __name__ == "__main__":
    test()
    print("lib_limits.test() PASSED")
