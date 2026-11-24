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
