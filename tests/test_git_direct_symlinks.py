"""
Symlinks pushed directly to the data repository must not make git_direct
write or read outside of its checkout.
"""

import os

import pytest

from app.model.err import RepoClientError, RepoError
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, _git, repo  # noqa: F401  (fixture)


def _push_link(tmp_path, name: str, target: str) -> None:
    seed = tmp_path / "seed"
    os.symlink(target, seed / "hosts" / f"{name}.yml")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", f"link {name}")
    _git(seed, "push", "-q", "origin", "main")


async def test_create_does_not_write_through_a_dangling_link(repo, tmp_path):
    outside = tmp_path / "outside.yml"
    _push_link(tmp_path, "evil", str(outside))
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        assert await s.exists("host", "evil") is False
        with pytest.raises(RepoClientError):
            await s.write("host", "evil", "", "cpu: 1\n", CommitMsg())
        with pytest.raises(RepoClientError):
            await s.copy("host", "evil", "a", "cpu: 4\n", CommitMsg())
    assert not outside.exists()


async def test_copy_does_not_read_outside_the_repo(repo, tmp_path):
    secret = tmp_path / "secret.yml"
    secret.write_text("token: s3cr3t\n")
    _push_link(tmp_path, "leak", str(secret))
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoError):
            await s.copy("host", "stolen", "leak", "token: s3cr3t\n", CommitMsg())
        assert await s.exists("host", "stolen") is False


async def test_copy_of_an_entity_link_still_works(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        await s.copy("host", "c", "link", "cpu: 4\n", CommitMsg())
        assert await s.get("host", "c") == "cpu: 4\n"
