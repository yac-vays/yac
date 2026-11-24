"""Every error answer has the `{title, message}` body."""

import pytest

from app.model.err import RepoConflict
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401
from tests.test_routers import WEB01_YAML
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


async def test_request_validation_error_uses_the_error_format(client, login):  # noqa: F811
    login("alice")
    r = await client.post("/entity/host", json={"name": "bad name!", "yaml": 1})
    assert r.status_code == 422
    body = r.json()
    assert set(body) == {"title", "message"}
    assert body["title"] == "Invalid Request"
    assert isinstance(body["message"], str) and body["message"]


async def test_missing_token_uses_the_error_format(client):  # noqa: F811
    r = await client.get("/entity/host")
    assert r.status_code == 401
    assert set(r.json()) == {"title", "message"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_validate_read_without_name_has_a_clear_message(client, login):  # noqa: F811
    login("alice")
    r = await client.post("/validate", json={"operation": "read", "type": "host"})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["message"] == "The name of the entity is missing"


async def test_rename_onto_existing_name_is_a_conflict(client, login):  # noqa: F811
    login("alice")
    r = await client.put(
        "/entity/host/web01",
        json={"name": "web02", "yaml_old": WEB01_YAML, "yaml_new": WEB01_YAML},
    )
    assert r.status_code == 409, r.text


async def test_git_direct_reports_an_existing_target_as_conflict(repo):  # noqa: F811
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoConflict):
            await s.copy("host", "b", "a", CommitMsg())
        with pytest.raises(RepoConflict):
            await s.link("host", "b", "a", CommitMsg())
