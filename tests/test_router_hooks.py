"""
Action hooks of the write endpoints: the props they get and what a failing
hook does to the request.
"""

import pytest

from app.lib import plugin
from app.lib import specs as specs_lib

from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


class _Recorder:
    def __init__(self):
        self.props = []

    async def run(self, *, details, props):
        self.props.append(props)


@pytest.fixture
def hooks(monkeypatch, repo_session):  # noqa: F811
    """Forced action on every create/edit/delete hook, recording its props."""
    host = specs_lib._RAW_DATA["types"][0]
    host["name_generated"] = "optional"
    host["name_generator"] = "'gen-1'"
    host["actions"] = [
        {
            "name": "notify",
            "title": "Notify",
            "plugin": "exec",
            "force": True,
            "details": {"command": ["true"]},
            "hooks": [
                "create:before",
                "create:after",
                "edit:after",
                "delete:after",
            ],
        }
    ]
    rec = _Recorder()
    monkeypatch.setattr(plugin.get_module("action", "exec"), "action", rec)
    return rec


async def test_create_after_hook_gets_generated_name(client, login, hooks):  # noqa: F811
    login("alice")
    r = await client.post(
        "/entity/host", json={"name": None, "yaml": "owner: alice\n"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "gen-1"
    before, after = hooks.props
    assert before["name"] is None
    assert after["name"] == "gen-1"
    assert after["new"]["name"] == "gen-1"


async def test_copy_hook_gets_source_as_old_name(client, login, hooks):  # noqa: F811
    login("alice")
    r = await client.post("/entity/host", json={"name": "web09", "copy": "web01"})
    assert r.status_code == 201, r.text
    after = hooks.props[-1]
    assert after["old"]["name"] == "web01"
    assert after["name"] == "web09"


class _Failing:
    def __init__(self, error):
        self.error = error

    async def run(self, *, details, props):
        if props["operation"] in ("create", "edit", "delete"):
            raise self.error


@pytest.mark.parametrize("client_error", [False, True])
async def test_failing_after_hook_still_reports_the_write(
    client, login, hooks, repo_session, monkeypatch, client_error  # noqa: F811
):
    from app.model.err import ActionClientError, ActionError

    specs_lib._RAW_DATA["types"][0]["actions"][0]["hooks"] = [
        "create:after",
        "edit:after",
        "delete:after",
    ]
    error = ActionClientError("bad\ninput") if client_error else ActionError("boom")
    monkeypatch.setattr(
        plugin.get_module("action", "exec"), "action", _Failing(error)
    )
    login("alice")

    r = await client.post("/entity/host", json={"name": "web09", "yaml": "owner: a\n"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "web09"
    warning = r.headers["YAC-Warning"]
    assert warning.startswith("Action notify failed")
    # only client errors carry their message
    assert ("bad input" in warning) is client_error
    assert "boom" not in warning
    assert "web09" in repo_session.files

    r = await client.put(
        "/entity/host/web09",
        json={"name": "web09", "yaml_old": "owner: a\n", "yaml_new": "owner: b\n"},
    )
    assert r.status_code == 200, r.text
    assert "YAC-Warning" in r.headers

    r = await client.delete("/entity/host/web09")
    assert r.status_code == 204, r.text
    assert "YAC-Warning" in r.headers
    assert "web09" not in repo_session.files


def test_warning_header_is_exposed_to_browsers():
    from app.main import yac

    cors = next(m for m in yac.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert "YAC-Warning" in cors.kwargs["expose_headers"]


async def test_successful_write_has_no_warning(client, login, hooks):  # noqa: F811
    login("alice")
    r = await client.post("/entity/host", json={"name": "web10", "yaml": "owner: a\n"})
    assert r.status_code == 201
    assert "YAC-Warning" not in r.headers


async def test_action_failure_names_type_and_entity(client, login, repo_session, monkeypatch):  # noqa: F811
    from app.lib import action as action_lib
    from app.model.err import ActionError

    monkeypatch.setattr(
        plugin.get_module("action", "exec"), "action", _Failing(ActionError("boom"))
    )
    login("alice")
    seen = {}
    orig = action_lib.run

    async def spy(hook, op, s):
        try:
            await orig(hook, op, s)
        except ActionError as error:
            seen["msg"] = str(error)
            raise

    monkeypatch.setattr(action_lib, "run", spy)
    specs_lib._RAW_DATA["types"][0]["actions"][0]["hooks"] = ["edit:before"]
    specs_lib._RAW_DATA["types"][0]["actions"][0]["force"] = True
    r = await client.patch(
        "/entity/host/web02", json={"name": "web02", "data": {"owner": "zoe"}}
    )
    assert r.status_code == 500
    assert seen["msg"] == "Action deploy for host web02 failed with: boom"
