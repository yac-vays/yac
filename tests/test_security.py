"""
Regression tests for the findings of the security audit of 2026-11-24
(numbers as in the audit report):

  #1  /validate handed the stored data / YAML of any entity to any
      authenticated user (no "see" needed)
  #2  rename / copy / link evaluated the roles for the NEW name only, so a
      role scoped on `name` let its holder move, copy or link foreign entities
      into that scope
  #3  an edit that changes nothing needed no permission at all -- and an
      empty PATCH still rewrote (re-serialized) the stored YAML
  #4  PATCH validated the patch against the client-supplied `yaml_base` but
      wrote it onto the stored YAML

All of them drive the real app through the harness of tests/test_routers.py
(users and specs: tests/fixtures/routers.yml).
"""

import copy

import pytest

from app.lib import specs as specs_lib

# pylint: disable=unused-import
from tests.test_routers import RAW_SPECS
from tests.test_routers import TOP_SECRET
from tests.test_routers import WEB01_YAML
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


def _edit_body(name: str = "web01", data: dict | None = None) -> dict:
    return {
        "operation": "edit",
        "type": "host",
        "name": name,
        "entity": {"name": name, "data": data or {}},
    }


def _add_roles(monkeypatch, *roles: dict) -> None:
    raw = copy.deepcopy(RAW_SPECS)
    raw["roles"].extend(roles)
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)


#
# 1) /validate must not disclose entities the user may not see
#


@pytest.mark.parametrize(
    "body",
    [
        _edit_body(),
        _edit_body(data={"owner": "dave"}),
        {"operation": "read", "type": "host", "name": "web01"},
        {"operation": "delete", "type": "host", "name": "web01"},
        {
            "operation": "create",
            "type": "host",
            "entity": {"name": "web09", "copy": "web01"},
        },
    ],
    ids=["edit-noop", "edit", "read", "delete", "copy"],
)
async def test_validate_discloses_nothing_without_see(client, login, body):
    login("dave")  # no role at all
    resp = await client.post("/validate", json=body)
    assert resp.status_code == 200
    result = resp.json()

    # Nothing of the stored entity, in whatever form (data, YAML, consts
    # echoed into the schema, error messages).
    for marker in (TOP_SECRET, "keep-me", "10.0.0.1", "alice"):
        assert marker not in resp.text
    assert result["schemas"]["data"] == {}
    assert result["schemas"]["yaml"] == ""
    assert result["schemas"]["json_schema"] == {}
    assert result["usages"] == []
    # ... and the request is reported as not allowed.
    assert result["request"]["valid"] is False
    assert "permission" in result["request"]["message"]


async def test_validate_read_only_user_still_gets_the_document(client, login):
    """Control: "see" is what unlocks the data (carol is read-only)."""
    login("carol")
    resp = await client.post(
        "/validate", json={"operation": "read", "type": "host", "name": "web01"}
    )
    result = resp.json()
    assert result["request"]["valid"] is True
    assert result["schemas"]["data"]["top_secret"] == TOP_SECRET

    # She may look at the edit schema too, but not edit.
    resp = await client.post("/validate", json=_edit_body())
    result = resp.json()
    assert result["schemas"]["data"]["top_secret"] == TOP_SECRET
    assert result["request"]["valid"] is False
    assert '"edt"' in result["request"]["message"]


#
# 2) rename / copy / link need rights on the SOURCE entity
#

# mallory: everything, but only inside her own name scope ("mine-*") -- the
# shape of a role like "hosts whose name resolves into my subnets".
MALLORY = {
    "host:all:add+rnm+cpy+lnk+edt+del": (
        "user.name == 'mallory' and name.startswith('mine-')"
    )
}
# olga: rights that follow the DATA (the owner), plus creating anything.
OLGA_OWNER = {"host:all:rnm+cpy+lnk+edt": "old.data.owner == user.name"}
OLGA_ADD = {"host:all:add": "user.name == 'olga'"}
# nora: may create and rename everything, but not edit.
NORA = {"host:all:add+rnm": "user.name == 'nora'"}


async def test_rename_of_foreign_entity_into_own_scope_is_forbidden(
    client, login, repo_session, monkeypatch
):
    _add_roles(monkeypatch, MALLORY)
    login("mallory")
    assert (await client.get("/entity/host/web01")).status_code == 403

    # blind (PATCH needs no yaml_old) ...
    resp = await client.patch(
        "/entity/host/web01", json={"name": "mine-x", "data": {}}
    )
    assert resp.status_code == 403
    # ... and with the content, were it known
    resp = await client.put(
        "/entity/host/web01",
        json={"name": "mine-x", "yaml_old": WEB01_YAML, "yaml_new": WEB01_YAML},
    )
    assert resp.status_code == 403

    assert repo_session.files["web01"] == WEB01_YAML
    assert "mine-x" not in repo_session.files

    # /validate agrees and discloses nothing
    body = _edit_body()
    body["entity"]["name"] = "mine-x"
    resp = await client.post("/validate", json=body)
    assert resp.json()["request"]["valid"] is False
    assert TOP_SECRET not in resp.text


async def test_copy_and_link_of_foreign_entity_into_own_scope_are_forbidden(
    client, login, repo_session, monkeypatch
):
    _add_roles(monkeypatch, MALLORY)
    login("mallory")

    resp = await client.post("/entity/host", json={"name": "mine-y", "copy": "web01"})
    assert resp.status_code == 403
    resp = await client.post("/entity/host", json={"name": "mine-z", "link": "web01"})
    assert resp.status_code == 403

    assert "mine-y" not in repo_session.files
    assert "mine-z" not in repo_session.links


async def test_rename_copy_link_inside_own_scope_still_work(
    client, login, repo_session, monkeypatch
):
    """Control: the name-scoped role keeps working where it applies to both."""
    _add_roles(monkeypatch, MALLORY)
    login("mallory")

    resp = await client.post(
        "/entity/host", json={"name": "mine-a", "yaml": "owner: mallory\n"}
    )
    assert resp.status_code == 201

    resp = await client.post("/entity/host", json={"name": "mine-b", "copy": "mine-a"})
    assert resp.status_code == 201
    resp = await client.post("/entity/host", json={"name": "mine-c", "link": "mine-a"})
    assert resp.status_code == 201
    resp = await client.patch(
        "/entity/host/mine-b", json={"name": "mine-d", "data": {}}
    )
    assert resp.status_code == 200
    assert "mine-d" in repo_session.files and "mine-b" not in repo_session.files

    # The target name counts as well: out of her scope she may not create.
    resp = await client.patch(
        "/entity/host/mine-d", json={"name": "elsewhere", "data": {}}
    )
    assert resp.status_code == 403
    assert "mine-d" in repo_session.files


async def test_data_scoped_roles_follow_the_entity_on_rename_and_copy(
    client, login, repo_session, monkeypatch
):
    """
    Control: rights derived from the stored DATA (`old.data`) apply to the
    source; the target only needs `add`.
    """
    _add_roles(monkeypatch, OLGA_OWNER, OLGA_ADD)
    repo_session.files["web05"] = "owner: olga\n"
    login("olga")

    resp = await client.post("/entity/host", json={"name": "web06", "copy": "web05"})
    assert resp.status_code == 201
    resp = await client.patch("/entity/host/web05", json={"name": "web07", "data": {}})
    assert resp.status_code == 200
    assert "web07" in repo_session.files

    # ... but not for somebody else's entity (web02 is bob's).
    resp = await client.patch("/entity/host/web02", json={"name": "web08", "data": {}})
    assert resp.status_code == 403
    resp = await client.post("/entity/host", json={"name": "web08", "copy": "web02"})
    assert resp.status_code == 403
    assert "web08" not in repo_session.files


async def test_pure_rename_needs_no_edt(client, login, repo_session, monkeypatch):
    """Control (docs: perms.md): `add` + `rnm` rename without `edt`."""
    _add_roles(monkeypatch, NORA)
    login("nora")
    resp = await client.patch("/entity/host/web02", json={"name": "web09", "data": {}})
    assert resp.status_code == 200
    assert repo_session.files["web09"] == "owner: bob\n" or "owner: bob" in (
        repo_session.files["web09"]
    )
    # with a content change it is an edit as well
    resp = await client.patch(
        "/entity/host/web09", json={"name": "web10", "data": {"owner": "nora"}}
    )
    assert resp.status_code == 403


#
# 3) an edit that changes nothing
#


async def test_noop_edit_without_any_permission_is_forbidden(
    client, login, repo_session
):
    login("dave")
    resp = await client.patch("/entity/host/web01", json={"name": "web01", "data": {}})
    assert resp.status_code == 403
    resp = await client.put(
        "/entity/host/web01",
        json={"name": "web01", "yaml_old": WEB01_YAML, "yaml_new": WEB01_YAML},
    )
    assert resp.status_code == 403
    assert repo_session.files["web01"] == WEB01_YAML


@pytest.mark.parametrize("user", ["carol", "bob", "alice"])
async def test_noop_edit_is_never_written(client, login, repo_session, user):
    """
    Neither read-only carol (403) nor those who may edit (400): an empty
    patch must not re-serialize the stored YAML into a commit.
    """
    login(user)
    expected = 403 if user == "carol" else 400
    resp = await client.patch("/entity/host/web01", json={"name": "web01", "data": {}})
    assert resp.status_code == expected
    resp = await client.put(
        "/entity/host/web01",
        json={"name": "web01", "yaml_old": WEB01_YAML, "yaml_new": WEB01_YAML},
    )
    assert resp.status_code == expected
    assert repo_session.files["web01"] == WEB01_YAML


async def test_noop_edit_validates_for_editors(client, login):
    """VAYS opens the edit form with exactly this request."""
    login("bob")
    resp = await client.post("/validate", json=_edit_body())
    result = resp.json()
    assert result["request"]["valid"] is True
    assert result["schemas"]["data"]["owner"] == "alice"


#
# 4) PATCH ignores yaml_base
#


async def test_patch_is_validated_against_the_stored_yaml(client, login, repo_session):
    stored = "owner: bob\nos: windows\n"
    repo_session.files["web02"] = stored
    login("frank")  # edt
    patch = {"linux_only": "x"}  # only defined while os is linux

    resp = await client.patch("/entity/host/web02", json={"name": "web02", "data": patch})
    assert resp.status_code == 400

    resp = await client.patch(
        "/entity/host/web02",
        json={"name": "web02", "data": patch, "yaml_base": "owner: bob\nos: linux\n"},
    )
    assert resp.status_code == 400
    assert repo_session.files["web02"] == stored

    # /validate keeps honouring it (a client validating the YAML it edits)
    body = _edit_body("web02", patch)
    body["entity"]["yaml_base"] = "owner: bob\nos: linux\n"
    resp = await client.post("/validate", json=body)
    assert resp.json()["schemas"]["valid"] is True
