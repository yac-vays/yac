"""The kinds of a request's entity are mutually exclusive."""

import pytest

from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x1", "copy": "web01", "link": "web01"},
        {"name": "x1", "link": "web01", "yaml": "owner: a\n"},
        {"name": "x1", "yaml": "owner: a\n", "unknown": 1},
    ],
)
async def test_mixed_or_unknown_entity_fields_are_rejected(client, login, repo_session, body):  # noqa: F811
    login("alice")
    r = await client.post("/entity/host", json=body)
    assert r.status_code == 422, r.text
    assert "x1" not in repo_session.files and "x1" not in repo_session.links


async def test_each_kind_alone_is_accepted(client, login, repo_session):  # noqa: F811
    login("alice")
    for body in (
        {"name": "x1", "yaml": "owner: a\n"},
        {"name": "x2", "copy": "web01"},
        {"name": "x3", "link": "web01"},
    ):
        r = await client.post("/entity/host", json=body)
        assert r.status_code == 201, r.text
