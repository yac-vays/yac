"""
Bounds on what a request can make YAC do, and what /validate tells users
an operation is refused to (findings YV-05 and YV-15 of the review of
2026-11-24):

- there was no bound on the size of a request, and documents were parsed
  (and schemas generated) before the permission check of a write;
- /validate told users without any role which entities exist and how much
  of a limit the entities of others use.

Harness: tests/test_routers.py.
"""

import copy

import pytest

from app import consts
from app.lib import bodylimit
from app.lib import schema as schema_lib
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


def _quota_specs(raw: dict) -> None:
    """A quota summed over all entities, capped at 100."""
    raw["types"][0]["limits"] = [
        {"title": "Quota", "value": "old.data.quota | default(1)", "max": "100"}
    ]
    raw["schema"]["properties"]["quota"] = {"type": "integer", "minimum": 1}


def _create(name: str, yaml: str) -> dict:
    return {
        "operation": "create",
        "type": "host",
        "entity": {"name": name, "yaml": yaml},
    }


async def test_oversized_yaml_is_refused(client, login):
    login("erin")
    doc = "owner: erin\n" + "#" * consts.ENV.max_yaml_size
    for body in (
        _create("big", doc),
        {
            "operation": "edit",
            "type": "host",
            "name": "web02",
            "entity": {"name": "web02", "yaml_old": doc, "yaml_new": "owner: bob\n"},
        },
        {
            "operation": "edit",
            "type": "host",
            "name": "web02",
            "entity": {"name": "web02", "data": {}, "yaml_base": doc},
        },
    ):
        resp = await client.post("/validate", json=body)
        assert resp.status_code == 422, resp.text

    resp = await client.post("/entity/host", json={"name": "big", "yaml": doc})
    assert resp.status_code == 422, resp.text


async def test_oversized_body_is_refused(client, login):
    login("erin")
    body = {
        "operation": "edit",
        "type": "host",
        "name": "web02",
        "entity": {"name": "web02", "data": {"x": "y" * bodylimit.MAX_BODY_SIZE}},
    }
    resp = await client.post("/validate", json=body)
    assert resp.status_code == 413, resp.text
    assert resp.json()["title"] == "Request Too Large"


async def test_oversized_body_without_content_length_is_refused(client, login):
    login("erin")

    async def chunks():
        yield b'{"operation": "edit", "type": "host", "name": "web02", "entity":'
        yield b' {"name": "web02", "data": {"x": "'
        for _ in range(bodylimit.MAX_BODY_SIZE // 65536 + 2):
            yield b"y" * 65536
        yield b'"}}}'

    resp = await client.post(
        "/validate", content=chunks(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413, resp.text


async def test_body_within_the_limit_passes(client, login):
    login("erin")
    doc = "owner: erin\n# " + "x" * (consts.ENV.max_yaml_size - 20) + "\n"
    resp = await client.post("/validate", json=_create("fits", doc))
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"method": "POST", "url": "/entity/host", "json": {"name": "n1", "yaml": "a: [\n"}},
        {
            "method": "PATCH",
            "url": "/entity/host/web01",
            "json": {"name": "web01", "data": {"owner": "dave"}},
        },
        {
            "method": "PUT",
            "url": "/entity/host/web01",
            "json": {"name": "web01", "yaml_old": "a: 1\n", "yaml_new": "a: [\n"},
        },
    ],
)
async def test_forbidden_write_is_refused_before_any_schema_work(
    client, login, monkeypatch, request_kwargs
):
    """
    A write the user may not do anyway must not make YAC generate the schema
    (which runs the specs' functions and format checks) -- nor report what
    is wrong with the YAML first.
    """

    async def _no_schema(*args, **kwargs):
        raise AssertionError("schema generated for a forbidden write")

    monkeypatch.setattr(schema_lib, "get", _no_schema)
    login("dave")  # no role at all
    resp = await client.request(**request_kwargs)
    assert resp.status_code == 403, resp.text

async def test_validate_does_not_tell_which_entities_exist(client, login):
    login("dave")  # no role at all
    answers = []
    for operation in ("read", "edit", "delete"):
        for name in ("web01", "does-not-exist"):
            body = {"operation": operation, "type": "host", "name": name}
            if operation == "edit":
                body["entity"] = {"name": name, "data": {}}
            resp = await client.post("/validate", json=body)
            assert resp.status_code == 200, resp.text
            answers.append(resp.json())
        assert answers[-2] == answers[-1], operation
        assert answers[-1]["schemas"]["json_schema"] == {}


async def test_validate_still_tells_readers_that_an_entity_is_missing(client, login):
    login("erin")  # sees every host
    body = {"operation": "read", "type": "host", "name": "does-not-exist"}
    result = (await client.post("/validate", json=body)).json()
    assert "does not exist" in result["request"]["message"]


async def test_validate_discloses_no_limit_usage_when_forbidden(
    client, login, repo_session, monkeypatch
):
    def mutate(raw):
        _quota_specs(raw)
        raw["types"][0]["limits"][0]["scope"] = "old.data.owner == new.data.owner"

    _specs(monkeypatch, mutate)
    repo_session.files.update(
        {"q1": "owner: alice\nquota: 70\n", "q2": "owner: bob\nquota: 5\n"}
    )
    probe = _create("probe", "owner: alice\nquota: 1\n")

    login("dave")  # no role at all
    result = (await client.post("/validate", json=probe)).json()
    assert result["request"]["valid"] is False
    assert result["usages"] == []
    # The schema of the type is no secret (the create form is rendered from
    # it before the name -- and with it the right to create -- is settled).
    assert result["schemas"]["json_schema"].get("properties")

    login("erin")  # may create
    result = (await client.post("/validate", json=probe)).json()
    assert result["usages"] and result["usages"][0]["used"] >= 71
