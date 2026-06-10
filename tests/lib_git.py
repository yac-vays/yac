"""
Tests for `lib.git.Repo` -- the thin async wrapper around the `git` CLI used by
the repo plugins. Runs against real local repositories (a bare remote + clones
in a tmp dir); the commit identity is supplied via the env dict the wrapper
passes to git, so no global git config / network is needed.

Covered: load (and its missing-directory error), clone, the add/commit/push +
pull round-trip, is_dirty, reset+clean recovery, get_hash, get_fetch_time
(present and absent FETCH_HEAD), and the GitError raised on a failing command.
"""

import subprocess

import pytest

from app.lib import git


ENV = {
    "EMAIL": "t@example.com",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Tester",
    "LANG": "C",
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def remote(tmp_path):
    """A bare repo seeded with one commit on `main`."""
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(bare))
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    _git(seed, "config", "user.email", "s@example.com")
    _git(seed, "config", "user.name", "Seed")
    (seed / "file.txt").write_text("one\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "init")
    _git(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def work(tmp_path, remote):
    """A working clone of the seeded remote, wrapped in a `git.Repo`."""
    path = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(remote), str(path))
    return git.Repo(path=str(path), env=ENV)


# ----- load -----

async def test_load_ok(work):
    await work.load()
    assert work.loaded is True


async def test_load_missing_directory_raises_git_error(tmp_path):
    repo = git.Repo(path=str(tmp_path / "does-not-exist"), env=ENV)
    with pytest.raises(git.GitError):
        await repo.load()


# ----- clone -----

async def test_clone_creates_working_copy(tmp_path, remote):
    dest = tmp_path / "fresh"
    repo = git.Repo(path=str(dest), env=ENV)
    await repo.clone(str(remote), branch="main")
    assert repo.loaded is True
    assert (dest / "file.txt").read_text() == "one\n"


# ----- get_hash / GitError -----

async def test_get_hash_returns_full_sha(work):
    h = await work.get_hash()
    assert len(h) == 40 and all(c in "0123456789abcdef" for c in h)


async def test_failing_command_raises_git_error(tmp_path):
    # a repo with no commits: rev-parse HEAD exits non-zero -> GitError
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q", "--initial-branch=main")
    repo = git.Repo(path=str(empty), env=ENV)
    with pytest.raises(git.GitError):
        await repo.get_hash()


# ----- is_dirty / reset / clean -----

async def test_is_dirty_tracks_worktree_state(work):
    assert await work.is_dirty() is False
    with open(f"{work.path}/file.txt", "w", encoding="utf-8") as f:
        f.write("changed\n")
    assert await work.is_dirty() is True


async def test_reset_and_clean_restore_clean_state(work):
    # tracked modification + a new untracked file
    with open(f"{work.path}/file.txt", "w", encoding="utf-8") as f:
        f.write("changed\n")
    with open(f"{work.path}/extra.txt", "w", encoding="utf-8") as f:
        f.write("junk\n")
    assert await work.is_dirty() is True

    await work.reset("origin/main", hard=True)
    await work.clean(recursive=True, force=True)
    assert await work.is_dirty() is False
    # the tracked file is back to its committed content; untracked file is gone
    with open(f"{work.path}/file.txt", encoding="utf-8") as f:
        assert f.read() == "one\n"


# ----- add / commit / push + pull round-trip -----

async def test_commit_and_push_round_trip(tmp_path, remote, work):
    with open(f"{work.path}/file.txt", "w", encoding="utf-8") as f:
        f.write("two\n")
    await work.add(["file.txt"])
    await work.commit("update")
    await work.push()

    # a second clone pulls the pushed change
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    assert (other / "file.txt").read_text() == "two\n"

    other_repo = git.Repo(path=str(other), env=ENV)
    await other_repo.pull()  # no-op, but must succeed
    assert await other_repo.get_hash() == await work.get_hash()


# ----- get_fetch_time -----

async def test_get_fetch_time_zero_without_fetch_head(tmp_path):
    empty = tmp_path / "nofetch"
    empty.mkdir()
    _git(empty, "init", "-q", "--initial-branch=main")
    repo = git.Repo(path=str(empty), env=ENV)
    assert await repo.get_fetch_time() == 0


async def test_get_fetch_time_positive_after_pull(work):
    await work.pull()
    assert await work.get_fetch_time() > 0
