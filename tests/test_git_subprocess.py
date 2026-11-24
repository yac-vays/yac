"""
app.lib.git stops a git command that timed out or whose request was
cancelled together with its helper processes, and reaps it.
"""

import asyncio
import os
import time

import pytest

from app.lib import git


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def slow_repo(tmp_path, monkeypatch):
    """A repo whose `git fetch` spawns a helper that hangs and records its pid."""
    pidfile = tmp_path / "helper.pid"
    helper = tmp_path / "ssh"
    helper.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 60\n")
    helper.chmod(0o755)
    path = tmp_path / "work"
    path.mkdir()
    os.system(f"git -C {path} init -q && git -C {path} remote add origin ssh://h/r.git")
    monkeypatch.setattr(git, "_TERMINATE_GRACE", 0.2)
    repo = git.Repo(
        path=str(path), env={"LANG": "C", "GIT_SSH": str(helper), "PATH": os.environ["PATH"]}
    )
    return repo, pidfile


async def _helper_pid(pidfile) -> int:
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            return int(pidfile.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("helper did not start")


async def test_timeout_stops_the_helper_too(slow_repo):
    repo, pidfile = slow_repo
    with pytest.raises(git.GitTimeoutError):
        await repo.fetch(timeout=1)
    assert not _alive(await _helper_pid(pidfile))


async def test_cancellation_stops_git(slow_repo):
    repo, pidfile = slow_repo
    task = asyncio.create_task(repo.fetch(timeout=30))
    pid = await _helper_pid(pidfile)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    deadline = time.monotonic() + 2
    while _alive(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not _alive(pid)


async def test_missing_git_directory_is_a_git_error(tmp_path):
    with pytest.raises(git.GitError):
        await git.Repo(path=str(tmp_path / "nope"), env={"LANG": "C"}).get_hash()
