"""
git_redis: `latest` comes from Redis and ends up in key names and as an
argument of `git merge-base`. Anything that is no commit hash is treated
like a missing `latest`.
"""

import time

import pytest

import app.plugin.repo.git_redis as grs
from app.model.err import RepoUnavailable

from tests.lib_git_redis import FakeRedis, H0, SNAPSHOT, _set_relation
from tests.lib_git_redis import gd, handler  # noqa: F401  (fixtures)

BAD = ["--output=/tmp/x", "H1", "abc123", "0" * 39, "0" * 65, "A" * 40, "0" * 40 + "\n"]


def test_hash_pattern():
    assert grs._is_hash("0123456789abcdef" * 2 + "01234567")  # SHA-1
    assert grs._is_hash("0123456789abcdef" * 4)  # SHA-256
    for value in [None, "", *BAD]:
        assert not grs._is_hash(value)


@pytest.mark.parametrize("latest", BAD)
async def test_invalid_latest_is_no_published_snapshot(handler, latest):  # noqa: F811
    client = FakeRedis(
        store={"latest": latest, "synced": str(time.time())},
        snapshots={f"snap:{latest}": SNAPSHOT},
    )
    assert await handler._published(client, dirty=False) == (None, False)
    with pytest.raises(RepoUnavailable):
        await handler._stale_snapshot(client, RepoUnavailable("down"))


@pytest.mark.parametrize("latest", BAD)
async def test_invalid_latest_never_reaches_git_and_is_replaced(
    handler, monkeypatch, latest  # noqa: F811
):
    calls = _set_relation(monkeypatch, "newer")
    client = FakeRedis(
        store={"latest": latest}, snapshots={f"snap:{latest}": SNAPSHOT}
    )

    assert await handler._publish_snapshot(client, None) == H0
    assert calls == []
    assert client.store["latest"] == H0
    assert client.ttl == {}  # no EXPIRE on a key named by it either
