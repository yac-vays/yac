"""
Copy and link are validated (rights on the source, limits measured with its
data) in a reader scope, but act on the source as it is in the writer scope.
A source that was edited in between must answer 409 like an edit or delete
of a changed entity does, instead of copying data nobody has validated.
"""

from contextlib import asynccontextmanager
import copy

import pytest

from app.lib import repo as repo_lib
from app.lib import specs as specs_lib
from app.lib import yaml as yaml_lib
from app.model.err import RepoConflict
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)
from tests.test_routers import (  # noqa: F401  (fixtures)
    RAW_SPECS,
    FakeRepoHandler,
    _Untyped,
    client,
    login,
    repo_session,
)

#
# git_direct
#


async def test_git_direct_copy_of_a_changed_source_conflicts(repo):  # noqa: F811
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoConflict, match="changed in the meantime"):
            await s.copy("host", "c", "a", "cpu: 1\n", CommitMsg())
        assert await s.exists("host", "c") is False
        await s.copy("host", "c", "a", "cpu: 4\n", CommitMsg())
        assert await s.get("host", "c") == "cpu: 4\n"


async def test_git_direct_link_to_a_changed_source_conflicts(repo):  # noqa: F811
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoConflict, match="changed in the meantime"):
            await s.link("host", "alias", "a", "cpu: 1\n", CommitMsg())
        assert await s.exists("host", "alias") is False
        await s.link("host", "alias", "a", "cpu: 4\n", CommitMsg())
        assert await s.get_link("host", "alias") == "a"


#
# Router: a summed quota like in the kiosk s3buckets specs, capped at 100
#


@pytest.fixture
def quota(monkeypatch, repo_session):  # noqa: F811
    raw = copy.deepcopy(RAW_SPECS)
    raw["types"][0]["limits"] = [
        {"title": "Quota", "value": "old.data.quota | default(1)", "max": "100"}
    ]
    raw["schema"]["properties"]["quota"] = {"type": "integer", "minimum": 1}
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)
    repo_session.files.clear()
    repo_session.files["src"] = "owner: alice\nquota: 1\n"
    return repo_session


class RacingEditHandler(FakeRepoHandler):
    """
    A concurrent edit of `src` (quota 1 -> 60, itself within the cap) gets
    the writer lock before the copy / link does.
    """

    @asynccontextmanager
    async def writer(self, user=None):
        self._sess.files["src"] = "owner: alice\nquota: 60\n"
        self._sess.repo_hash += "x"  # a new commit
        yield _Untyped(self._sess)


def _total(session) -> int:
    names = [*session.files, *session.links]
    return sum(
        yaml_lib.load_as_dict_fast(session.files[session.links.get(n, n)])["quota"]
        for n in names
    )


@pytest.mark.parametrize("how", ["copy", "link"])
async def test_create_from_a_concurrently_edited_source_conflicts(
    client, login, quota, monkeypatch, how  # noqa: F811
):
    monkeypatch.setattr(repo_lib, "handler", RacingEditHandler(quota))
    login("alice")

    resp = await client.post("/entity/host", json={"name": "dst", how: "src"})

    assert resp.status_code == 409, resp.text
    assert "dst" not in quota.files and "dst" not in quota.links
    assert _total(quota) == 60  # not 120 with a cap of 100


@pytest.mark.parametrize("how", ["copy", "link"])
async def test_create_from_an_unchanged_source_still_works(
    client, login, quota, how  # noqa: F811
):
    login("alice")
    resp = await client.post("/entity/host", json={"name": "dst", how: "src"})
    assert resp.status_code == 201, resp.text
    assert _total(quota) == 2
