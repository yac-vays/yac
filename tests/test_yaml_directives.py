"""
YAML the round-trip parser must not be led astray by (findings YV-04 and
L-07 of the review of 2026-11-24):

- `%YAML 1.1` documents were validated by one parser and read back by
  another (a summed limit never filled up), and the YAML version of one
  request leaked into the next one, of whoever it was;
- a deeply nested patch ended in an unhandled RecursionError -- and left
  the shared YAML dumper returning empty documents.

The router tests use the harness of tests/test_routers.py.
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


@pytest.mark.parametrize(
    "text",
    [
        "%YAML 1.1\n---\na: yes\n",
        "\ufeff%YAML 1.1\n---\na: yes\n",
        "# comment\n%YAML 1.1\n---\na: yes\n",
        "%TAG !e! tag:example.com,2000:\n---\na: 1\n",
        "a: 1\n...\n%YAML 1.1\n---\nb: yes\n",
        "a: 1\r%YAML 1.1\r---\rb: yes\r",
    ],
)
def test_yaml_directives_are_refused(text):
    with pytest.raises(yaml_lib.YAMLError):
        yaml_lib.load(text)
    with pytest.raises(yaml_lib.YAMLError):
        yaml_lib.load(text, strict=False)


@pytest.mark.parametrize(
    "text",
    [
        "a: 100%\nb: '%YAML 1.1'\n",
        "a: |\n  %YAML 1.1\n",
        'a: "x\n  %YAML 1.1"\n',
    ],
)
def test_percent_signs_in_the_data_still_load(text):
    assert yaml_lib.load(text) is not None


def test_yaml_version_of_one_document_does_not_reach_the_next():
    """
    ruamel keeps the version of the last document on the (shared) instance,
    also when it comes to refuse that document.
    """
    for y in (yaml_lib.y, yaml_lib.y_non_strict):
        y.version = (1, 1)  # as left behind by a `%YAML 1.1` document
    assert yaml_lib.load("a: yes\nb: 1:40\n") == {"a": "yes", "b": "1:40"}
    assert yaml_lib.load("a: yes\n", strict=False) == {"a": "yes"}

    yaml_lib.y.version = (1, 1)
    assert yaml_lib.dump({"a": "yes"}) == "---\na: yes\n"


async def test_yaml_directive_cannot_bypass_a_summed_limit(
    client, login, repo_session, monkeypatch
):
    """
    `quota: 1:40` in a `%YAML 1.1` document was the integer 100 for the
    validation, but the string "1:40" -- counting as 0 -- for every later
    measurement of the limit.
    """
    _specs(monkeypatch, _quota_specs)
    repo_session.files.clear()
    login("erin")

    evil = "%YAML 1.1\n---\nowner: erin\nquota: 1:40\n"
    for name in ("b1", "b2"):
        resp = await client.post("/entity/host", json={"name": name, "yaml": evil})
        assert resp.status_code == 400, resp.text
        assert "directive" in resp.json()["message"]
    assert not repo_session.files

    result = (await client.post("/validate", json=_create("b3", evil))).json()
    assert result["request"]["valid"] is False
    assert "directive" in result["request"]["message"]


async def test_yaml_of_one_user_does_not_change_the_parsing_for_the_next(
    client, login, monkeypatch
):
    _specs(
        monkeypatch,
        lambda raw: raw["schema"]["properties"].update(
            {"flag": {"type": "string", "yac_optional": True}}
        ),
    )
    victim = _create("v1", "owner: erin\nflag: yes\n")

    login("dave")  # no role at all
    await client.post("/validate", json=_create("x", "%YAML 1.1\n---\na: 1\n"))

    login("erin")
    schemas = (await client.post("/validate", json=victim)).json()["schemas"]
    assert schemas["valid"] is True, schemas
    assert schemas["data"]["flag"] == "yes"

@pytest.mark.parametrize("depth", [400, 900])
async def test_deeply_nested_patch_is_a_client_error(client, login, repo_session, depth):
    login("erin")
    data: dict | str = "x"
    for _ in range(depth):
        data = {"a": data}
    before = dict(repo_session.files)

    resp = await client.post(
        "/validate",
        json={
            "operation": "edit",
            "type": "host",
            "name": "web02",
            "entity": {"name": "web02", "data": {"nested": data}},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"]["valid"] is False

    resp = await client.patch(
        "/entity/host/web02", json={"name": "web02", "data": {"nested": data}}
    )
    assert resp.status_code == 400, resp.text
    assert repo_session.files == before

    # ... and the (shared) YAML dumper is not left broken by it: before, every
    # later patch on this worker produced an empty document.
    assert yaml_lib.update("owner: bob\n", {"os": "linux"}) == (
        "---\nowner: bob\nos: linux\n"
    )
