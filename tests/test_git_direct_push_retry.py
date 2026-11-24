"""
A push rejected because another instance pushed first leaves the checkout
clean and is reported as such, so the caller can repeat its writer scope.
"""

import pytest

from app.model.err import RepoConflict
from app.model.err import RepoPushRejected
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, _git, repo  # noqa: F401  (fixture)


def _push_elsewhere(tmp_path, name: str, content: str) -> None:
    seed = tmp_path / "seed"
    _git(seed, "pull", "-q", "origin", "main")
    (seed / "hosts" / f"{name}.yml").write_text(content)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", f"other {name}")
    _git(seed, "push", "-q", "origin", "main")


async def test_concurrent_push_rejects_without_leftovers(repo, tmp_path):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        _push_elsewhere(tmp_path, "other", "cpu: 2\n")
        with pytest.raises(RepoPushRejected):
            await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())
        assert await s.get("host", "a") == "cpu: 4\n"

    # the next scope syncs and the same write goes through
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())
        assert await s.get("host", "a") == "cpu: 5\n"
        assert await s.get("host", "other") == "cpu: 2\n"


async def test_conflicting_concurrent_push_is_a_conflict_when_repeated(repo, tmp_path):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        _push_elsewhere(tmp_path, "a", "cpu: 64\n")
        with pytest.raises(RepoPushRejected):
            await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())

    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoConflict) as error:
            await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())
        assert not isinstance(error.value, RepoPushRejected)
        assert await s.get("host", "a") == "cpu: 64\n"
