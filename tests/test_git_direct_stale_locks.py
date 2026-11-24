"""
git_direct: a git that is killed hard (OOM, SIGKILL) leaves its lock files
in `.git`. The checkout survives a container restart, so such a file made
every fetch fail from then on (500 for all reads, crash loop at startup).
The process owns its checkout, so it removes them and fetches again.
"""

import subprocess

import pytest

from app.lib import git
from app.model.err import RepoError

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _push_c(tmp_path):
    seed = tmp_path / "seed"
    (seed / "hosts" / "c.yml").write_text("cpu: 1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "c")
    _git(seed, "push", "-q", "origin", "main")


async def test_stale_lock_file_is_healed(repo, tmp_path):  # noqa: F811
    _push_c(tmp_path)
    # leftover of a fetch killed while updating the remote-tracking ref
    lock_file = tmp_path / "work" / ".git" / "refs/remotes/origin/main.lock"
    lock_file.write_text("")

    async with repo.reader(USER) as untyped:
        assert await untyped.session(DETAILS).list("host") == ["a", "b", "c", "link"]
    assert not lock_file.exists()


def test_all_lock_files_below_dot_git_are_removed(repo, tmp_path):  # noqa: F811
    git_dir = tmp_path / "work" / ".git"
    locks = ["refs/remotes/origin/main.lock", "packed-refs.lock", "shallow.lock"]
    for lock in locks:
        (git_dir / lock).write_text("")
    (tmp_path / "work" / "hosts" / "x.lock").write_text("")  # no git lock

    assert repo._remove_stale_locks() is True
    assert not any((git_dir / lock).exists() for lock in locks)
    assert (git_dir / "HEAD").exists()
    assert (tmp_path / "work" / "hosts" / "x.lock").exists()
    assert repo._remove_stale_locks() is False


async def test_stale_lock_file_is_healed_at_startup(repo, tmp_path):  # noqa: F811
    _push_c(tmp_path)
    (tmp_path / "work" / ".git" / "refs/remotes/origin/main.lock").write_text("")
    repo._loaded = False  # a restarted process finding its old checkout

    async with repo.reader(None) as untyped:
        assert await untyped.session(DETAILS).list("host") == ["a", "b", "c", "link"]


async def test_refused_fetch_without_lock_files_is_still_an_error(
    repo, monkeypatch  # noqa: F811
):
    calls = []

    async def fetch(self, *args, **kwargs):
        del self, args, kwargs
        calls.append(1)
        raise git.GitError("fatal: Authentication failed", returncode=128)

    monkeypatch.setattr(git.Repo, "fetch", fetch)
    with pytest.raises(RepoError, match="Cannot update the repo"):
        async with repo.reader(USER):
            pass
    assert len(calls) == 1
