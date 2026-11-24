"""
git_direct keeps its checkout identical to the remote branch (fetch + hard
reset + clean) and only replaces it -- via a clone into a temporary directory
-- when the checkout itself is broken.
"""

import os
import subprocess

import pytest

import app.plugin.repo.git_direct as gd
from app.lib import git
from app.model.err import RepoError
from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, _git, repo  # noqa: F401  (fixture)


def _log(cwd) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout.splitlines()


async def test_leftovers_of_a_crashed_write_are_dropped(repo, tmp_path):
    work = repo.path
    # what a process killed between commit and push leaves behind ...
    with open(f"{work}/hosts/a.yml", "w", encoding="utf-8") as f:
        f.write("cpu: 99\n")
    _git(work, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qam", "orphan")
    # ... or between the file write and the commit
    with open(f"{work}/hosts/b.yml", "w", encoding="utf-8") as f:
        f.write("cpu: 77\n")
    with open(f"{work}/hosts/junk.yml", "w", encoding="utf-8") as f:
        f.write("x: 1\n")
    with open(f"{work}/.git/index.lock", "w", encoding="utf-8"):
        pass

    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        assert await s.list("host") == ["a", "b", "link"]
        assert await s.get("host", "a") == "cpu: 4\n"
        assert await s.get("host", "b") == "cpu: 8\n"
        await s.write("host", "c", "", "cpu: 1\n", CommitMsg())

    assert _log(tmp_path / "remote.git") == ["[YAC] Created hosts/c.yml", "init"]


async def test_refused_fetch_keeps_the_checkout(repo, monkeypatch):
    calls = {"clone": 0}

    async def refused(self, *args, **kwargs):
        del self, args, kwargs
        raise git.GitError("fatal: Authentication failed", returncode=128)

    async def clone(self, *args, **kwargs):
        del self, args, kwargs
        calls["clone"] += 1

    monkeypatch.setattr(git.Repo, "fetch", refused)
    monkeypatch.setattr(git.Repo, "clone", clone)
    with pytest.raises(RepoError):
        async with repo.reader(USER):
            pass
    assert calls["clone"] == 0
    assert os.path.exists(f"{repo.path}/hosts/a.yml")


async def test_broken_checkout_is_recloned(repo):
    os.remove(f"{repo.path}/.git/HEAD")
    async with repo.reader(USER) as untyped:
        assert await untyped.session(DETAILS).list("host") == ["a", "b", "link"]
    assert not os.path.exists(f"{repo.path}.new")


async def test_failed_clone_leaves_no_half_checkout(repo, tmp_path, monkeypatch):
    fresh = gd.GitRepo()
    fresh.path = str(tmp_path / "fresh")
    fresh._loaded = False
    monkeypatch.setattr(gd, "URL", str(tmp_path / "no-such-remote.git"))
    with pytest.raises(RepoError):
        async with fresh.reader(USER):
            pass
    assert not os.path.exists(fresh.path)
    assert not os.path.exists(f"{fresh.path}.new")
    assert fresh._loaded is False


async def test_interrupted_clone_does_not_load(tmp_path):
    # `git init` leaves what a clone killed early does: a .git without a commit
    path = tmp_path / "half"
    path.mkdir()
    _git(path, "init", "-q")
    with pytest.raises(git.GitError):
        await git.Repo(path=str(path), env={"LANG": "C"}).load()


async def test_subdirectory_of_a_checkout_does_not_load(repo):
    with pytest.raises(git.GitError):
        await git.Repo(path=f"{repo.path}/hosts", env={"LANG": "C"}).load()
