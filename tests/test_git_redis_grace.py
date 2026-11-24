"""
git_redis: a request pins `snap:<hash>` for all its Redis calls. With
`grace_seconds: 0` a publish deleted that snapshot under the request, which
then took everything for absent (empty lists, 404s). The grace has a minimum
now, and a session whose snapshot is gone nevertheless raises instead.
"""

import pytest

import app.plugin.repo.git_redis as grs
from app.model.err import RepoError
from app.model.err import RepoNotFound

from tests.lib_git_redis import FakeRedis

DETAILS = {"host": "hosts/{{ name }}.yml"}
SNAPSHOT = {
    "": "",
    "hosts/a.yml": "f:cpu: 4\n",
    "hosts/link.yml": "l:hosts/a.yml",
}


def test_grace_seconds_has_a_minimum():
    assert grs._check_grace(0) == grs.MIN_GRACE
    assert grs._check_grace(-5) == grs.MIN_GRACE
    assert grs._check_grace(grs.MIN_GRACE) == grs.MIN_GRACE
    assert grs._check_grace(300) == 300
    assert grs.GRACE >= grs.MIN_GRACE


async def test_session_notices_that_its_snapshot_is_gone():
    client = FakeRedis(snapshots={"snap:h1": SNAPSHOT})
    pinned = grs._GitRedisUntyped(grs.GitRedisRepo(), client, "h1").session(DETAILS)
    assert await pinned.list("host") == ["a", "link"]

    await client.delete("snap:h1")  # what EXPIRE does in the end

    for read in (
        pinned.list("host"),
        pinned.exists("host", "a"),
        pinned.get("host", "a"),
        pinned.get_resolved("host", "a"),
        pinned.is_link("host", "link"),
        pinned.is_link_target("host", "a"),
    ):
        with pytest.raises(RepoError, match="expired") as excinfo:
            await read
        assert not isinstance(excinfo.value, RepoNotFound)


async def test_link_target_vanishing_with_the_snapshot_is_no_dangling_link():
    client = FakeRedis(snapshots={"snap:h1": SNAPSHOT})
    pinned = grs._GitRedisSession(None, client, "h1", DETAILS)
    real_hget = client.hget

    async def hget(key, field):
        value = await real_hget(key, field)
        if field == "hosts/link.yml":
            await client.delete(key)  # expires between the two lookups
        return value

    client.hget = hget
    with pytest.raises(RepoError, match="expired") as excinfo:
        await pinned.get("host", "link")
    assert not isinstance(excinfo.value, RepoNotFound)


async def test_absent_in_an_existing_snapshot_is_still_absent():
    client = FakeRedis(snapshots={"snap:h1": SNAPSHOT})
    pinned = grs._GitRedisSession(None, client, "h1", DETAILS)
    assert await pinned.exists("host", "zzz") is False
    assert await pinned.is_link("host", "zzz") is False
    with pytest.raises(RepoNotFound):
        await pinned.get("host", "zzz")
