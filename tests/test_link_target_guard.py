"""
An entity that other entities link to can neither be renamed nor deleted:
/validate reports it, the write endpoints refuse before any hook runs and
git_direct refuses as the last line of defence.
"""

import pytest

from app.lib import plugin
from app.lib import specs as specs_lib
from app.model.err import RepoClientError
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401
from tests.test_routers import WEB01_YAML
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


@pytest.fixture
def linked(repo_session):  # noqa: F811
    repo_session.links["alias"] = "web01"
    return repo_session


@pytest.fixture
def before_hooks(monkeypatch, repo_session):  # noqa: F811
    calls = []

    class _Rec:
        async def run(self, *, details, props):
            calls.append(props)

    specs_lib._RAW_DATA["types"][0]["actions"] = [
        {
            "name": "notify",
            "title": "Notify",
            "plugin": "exec",
            "force": True,
            "details": {"command": ["true"]},
            "hooks": ["delete:before", "edit:before"],
        }
    ]
    monkeypatch.setattr(plugin.get_module("action", "exec"), "action", _Rec())
    return calls


async def test_validate_delete_of_link_target_is_invalid(client, login, linked):  # noqa: F811
    login("alice")
    r = await client.post(
        "/validate", json={"operation": "delete", "type": "host", "name": "web01"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["request"]["valid"] is False
    assert "linked" in r.json()["request"]["message"]


async def test_delete_of_link_target_runs_no_hook(client, login, linked, before_hooks):  # noqa: F811
    login("alice")
    r = await client.delete("/entity/host/web01")
    assert r.status_code == 400, r.text
    assert before_hooks == []
    assert "web01" in linked.files


async def test_rename_of_link_target_is_refused(client, login, linked, before_hooks):  # noqa: F811
    login("alice")
    r = await client.put(
        "/entity/host/web01",
        json={"name": "web01b", "yaml_old": WEB01_YAML, "yaml_new": WEB01_YAML},
    )
    assert r.status_code == 400, r.text
    assert before_hooks == []
    assert "web01" in linked.files


async def test_unlinked_entity_can_still_be_deleted(client, login, linked):  # noqa: F811
    login("alice")
    r = await client.delete("/entity/host/web02")
    assert r.status_code == 204, r.text


async def test_git_direct_refuses_to_rename_a_link_target(repo):  # noqa: F811
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        assert await s.is_link_target("host", "a") is True
        assert await s.is_link_target("host", "b") is False
        with pytest.raises(RepoClientError):
            await s.write_rename(
                "host", "a", "a2", await s.get("host", "a"), "x: 1\n", CommitMsg()
            )


async def test_git_redis_finds_links_to_an_entity():
    import app.plugin.repo.git_redis as grs

    snapshot = {
        "": "",  # the marker of a complete snapshot
        "hosts/a.yml": "f:x: 1\n",
        "hosts/b.yml": "f:x: 2\n",
        "hosts/link.yml": "l:hosts/a.yml",
    }

    class _Redis:
        async def hkeys(self, key):
            assert key == "snap:H"
            return list(snapshot)

        async def hmget(self, key, fields):
            assert key == "snap:H"
            return [snapshot.get(f) for f in fields]

    s = grs._GitRedisSession(None, _Redis(), "H", DETAILS)
    assert await s.is_link_target("host", "a") is True
    assert await s.is_link_target("host", "b") is False
