"""
A write whose push is rejected because another instance pushed first is
repeated in a new writer scope: limits are measured again on the new state.
"""

import copy
from contextlib import asynccontextmanager

from app.lib import repo as repo_lib
from app.lib import specs as specs_lib
from app.model.err import RepoPushRejected

from tests.test_routers import RAW_SPECS
from tests.test_routers import FakeRepoHandler
from tests.test_routers import _Untyped
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


class RejectingRepoHandler(FakeRepoHandler):
    """The first write is rejected; `other` is what the other instance pushed."""

    def __init__(self, sess, other: dict[str, str], rejections: int = 1):
        super().__init__(sess)
        self.other = other
        self.rejections = rejections
        self.scopes = 0

    @asynccontextmanager
    async def writer(self, user=None):
        self.scopes += 1
        sess = self._sess
        if self.scopes <= self.rejections:
            real_write = sess.write

            async def write(*_a):
                sess.write = real_write
                sess.files.update(self.other)  # seen by the next sync
                raise RepoPushRejected("The repository has changed in the meantime")

            sess.write = write
        yield _Untyped(sess)


def _specs_with_limit(cap: str) -> dict:
    raw = copy.deepcopy(RAW_SPECS)
    raw["types"][0]["limits"] = [{"title": "hosts", "max": cap}]
    return raw


async def _create(client):  # noqa: F811
    return await client.post(
        "/entity/host", json={"name": "new01", "yaml": "owner: alice\n"}
    )


async def test_unrelated_push_is_retried(monkeypatch, client, login, repo_session):  # noqa: F811
    handler = RejectingRepoHandler(repo_session, {})
    monkeypatch.setattr(repo_lib, "handler", handler)

    login("alice")
    resp = await _create(client)
    assert resp.status_code == 201, resp.text
    assert handler.scopes == 2
    assert "new01" in repo_session.files


async def test_retry_enforces_limits_again(monkeypatch, client, login, repo_session):  # noqa: F811
    """Cap 3, two entities: the other instance took the last slot."""
    monkeypatch.setattr(specs_lib, "_RAW_DATA", _specs_with_limit("3"))
    handler = RejectingRepoHandler(repo_session, {"sniped": "owner: mallory\n"})
    monkeypatch.setattr(repo_lib, "handler", handler)

    login("alice")
    resp = await _create(client)
    assert resp.status_code == 400, resp.text
    assert 'Limit "hosts" reached: 4/3' in resp.json()["message"]
    assert "new01" not in repo_session.files


async def test_second_rejection_is_a_conflict(monkeypatch, client, login, repo_session):  # noqa: F811
    handler = RejectingRepoHandler(repo_session, {}, rejections=2)
    monkeypatch.setattr(repo_lib, "handler", handler)

    login("alice")
    resp = await _create(client)
    assert resp.status_code == 409, resp.text
    assert handler.scopes == 2
    assert "new01" not in repo_session.files
