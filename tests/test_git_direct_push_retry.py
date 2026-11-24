"""
A push rejected because another instance pushed first is retried once on
top of the new remote state; a real conflict stays a conflict.
"""

import pytest

from app.model.err import RepoConflict
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, _git, repo  # noqa: F401  (fixture)


def _push_elsewhere(tmp_path, name: str, content: str) -> None:
    seed = tmp_path / "seed"
    _git(seed, "pull", "-q", "origin", "main")
    (seed / "hosts" / f"{name}.yml").write_text(content)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", f"other {name}")
    _git(seed, "push", "-q", "origin", "main")


async def test_unrelated_concurrent_push_is_retried(repo, tmp_path):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        _push_elsewhere(tmp_path, "other", "cpu: 2\n")
        diff = await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())
        assert diff.name == "a"
        assert await s.get("host", "a") == "cpu: 5\n"
        assert await s.get("host", "other") == "cpu: 2\n"


async def test_conflicting_concurrent_push_is_a_conflict(repo, tmp_path):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        _push_elsewhere(tmp_path, "a", "cpu: 64\n")
        with pytest.raises(RepoConflict):
            await s.write("host", "a", "cpu: 4\n", "cpu: 5\n", CommitMsg())
        assert await s.get("host", "a") == "cpu: 64\n"
