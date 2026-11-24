"""
yac_perms / yac_editable on properties of objects that are not plainly
`type: object` (finding YV-19 of the review of 2026-11-24): for
`type: [object, "null"]` or no type at all, nothing closed or pinned the
object, so the restricted property was writable for everybody with "edt".

Harness: tests/test_routers.py.
"""

import copy

import pytest

from app.lib import specs as specs_lib
from app.lib import yaml as yaml_lib

# pylint: disable=unused-import
from tests.test_routers import RAW_SPECS
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


def _specs(monkeypatch, mutate) -> None:
    raw = copy.deepcopy(RAW_SPECS)
    mutate(raw)
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)


def _networking_type(obj_type):
    def mutate(raw):
        net = raw["schema"]["properties"]["networking"]
        if obj_type is None:
            net.pop("type")
        else:
            net["type"] = obj_type

    return mutate


@pytest.mark.parametrize("obj_type", ["object", ["object", "null"], ["null", "object"]])
async def test_yac_perms_hold_in_optional_objects(
    client, login, repo_session, monkeypatch, obj_type
):
    _specs(monkeypatch, _networking_type(obj_type))
    login("bob")  # edt+cln, but not the perm of networking.vlan_secret
    before = repo_session.files["web01"]

    for patch in (
        {"networking": {"vlan_secret": "CHANGED-BY-BOB"}},
        {"networking": {"vlan_secret": yaml_lib.UNSET}},
        {"networking": {"invented": 1}},
    ):
        resp = await client.patch(
            "/entity/host/web01", json={"name": "web01", "data": patch}
        )
        assert resp.status_code == 400, (patch, resp.text)
    assert repo_session.files["web01"] == before


async def test_yac_perms_in_an_untyped_object_are_a_specs_error(
    client, login, repo_session, monkeypatch
):
    """Not enforceable (nothing closes or pins the object): refuse to guess."""
    _specs(monkeypatch, _networking_type(None))
    login("bob")
    before = repo_session.files["web01"]
    resp = await client.patch(
        "/entity/host/web01",
        json={"name": "web01", "data": {"networking": {"vlan_secret": "CHANGED"}}},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["title"] == "Error in Specification"
    assert repo_session.files["web01"] == before
