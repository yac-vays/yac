"""
Tests for the `git_direct` repo plugin against a *real* local git repository
(a bare repo acting as the remote + a working clone on disk). No network or SSH
is involved -- the remote is a `file://`-style bare repo in a tmp dir, and the
commit identity travels via the git env the plugin sets, so pushes/pulls work
entirely offline.

Covered: the path-template rendering/validation (`_render_glob` / `_path_template`),
the read operations (list/exists/is_link/get_link/get/_has_link), the full write
cycle through a writer scope (create/copy/link/rename/delete with their conflict
and "linked source" guards), the reader-scope write lockout, and the
path-escape guard.
"""

import subprocess

import pytest

import app.plugin.repo.git_direct as gd
from app.model.out import User
from app.model.err import (
    RepoClientError,
    RepoConflict,
    RepoError,
    RepoNotFound,
    RepoSpecsError,
)


DETAILS = {"host": "hosts/{{ name }}.yml"}
USER = User(name="tester", email="tester@example.com", full_name="Test Er")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """
    Build remote.git (bare) + seed it + a fresh `work` clone, and return a
    `GitRepo` handler pointed at `work`. The module-level glob cache is cleared
    so each test starts from a clean template cache.
    """
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"

    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _git(seed, "config", "user.email", "seed@example.com")
    _git(seed, "config", "user.name", "Seed")
    hosts = seed / "hosts"
    hosts.mkdir()
    (hosts / "a.yml").write_text("cpu: 4\n")
    (hosts / "b.yml").write_text("cpu: 8\n")
    (hosts / "link.yml").symlink_to("a.yml")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "init")
    _git(seed, "push", "-q", "origin", "main")

    _git(tmp_path, "clone", "-q", str(remote), str(work))

    monkeypatch.setattr(gd, "BRANCH", "main")
    monkeypatch.setattr(gd, "URL", str(remote))
    gd._GLOB_CACHE.clear()

    handler = gd.GitRepo()
    handler.path = str(work)
    handler._loaded = True
    return handler


# ----- path template rendering -----

async def test_render_glob_requires_exactly_one_star():
    gd._GLOB_CACHE.clear()
    assert await gd._render_glob("host", DETAILS) == "hosts/*.yml"

    with pytest.raises(RepoSpecsError):
        await gd._render_glob("host", {"host": "hosts/static.yml"})  # no star
    with pytest.raises(RepoSpecsError):
        await gd._render_glob("host", {"host": "{{ name }}/{{ name }}.yml"})  # two


def test_path_template_missing_or_empty_raises():
    with pytest.raises(RepoSpecsError):
        gd._path_template("absent", DETAILS)
    with pytest.raises(RepoSpecsError):
        gd._path_template("host", {"host": ""})


async def test_render_path_substitutes_name():
    assert await gd._render_path("host", "web7", DETAILS) == "hosts/web7.yml"


# ----- read operations -----

async def test_list_sorted_names(repo):
    assert await repo._list("host", DETAILS) == ["a", "b", "link"]


async def test_exists_and_get(repo):
    assert await repo._exists("host", "a", DETAILS) is True
    assert await repo._exists("host", "ghost", DETAILS) is False
    assert (await repo._get("host", "a", DETAILS)).strip() == "cpu: 4"


async def test_get_missing_raises_not_found(repo):
    with pytest.raises(RepoNotFound):
        await repo._get("host", "ghost", DETAILS)


async def test_link_introspection(repo):
    assert await repo._is_link("host", "link", DETAILS) is True
    assert await repo._is_link("host", "a", DETAILS) is False
    assert await repo._get_link("host", "link", DETAILS) == "a"
    # reading a link follows it to the target content
    assert (await repo._get("host", "link", DETAILS)).strip() == "cpu: 4"


async def test_has_link_detects_incoming_links(repo):
    # `a` is the target of `link`; `b` is referenced by nobody
    assert await repo._has_link("host", "a", DETAILS) is True
    assert await repo._has_link("host", "b", DETAILS) is False


# ----- write cycle through a writer scope -----

async def test_full_write_cycle(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)

        diff = await s.write("host", "new", "", "cpu: 1\n", "create new")
        assert diff.name == "new" and "+cpu: 1" in diff.patch
        assert await s.exists("host", "new") is True

        # conflict: supplied old content does not match what's on disk
        with pytest.raises(RepoConflict):
            await s.write("host", "new", "STALE", "cpu: 2\n", "x")

        # writing identical content is rejected as a no-op
        with pytest.raises(RepoClientError):
            await s.write("host", "new", "cpu: 1\n", "cpu: 1\n", "x")

        # copy then link onto the copy's *source*
        await s.copy("host", "clone", "new", "copy")
        assert await s.exists("host", "clone") is True
        await s.link("host", "alias", "new", "link")
        assert await s.is_link("host", "alias") is True

        # the linked source must not be deletable
        with pytest.raises(RepoClientError):
            await s.delete("host", "new", "cpu: 1\n", "del")

        # rename the clone, then delete it
        await s.write_rename(
            "host", "clone", "clone2", await s.get("host", "clone"), "cpu: 9\n", "rn"
        )
        assert await s.exists("host", "clone2") and not await s.exists("host", "clone")
        await s.delete("host", "clone2", "cpu: 9\n", "del")
        assert await s.exists("host", "clone2") is False


async def test_write_to_deleted_file_conflicts(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        # claiming a non-empty old content for a file that does not exist
        with pytest.raises(RepoConflict):
            await s.write("host", "ghost", "cpu: 1\n", "cpu: 2\n", "x")


async def test_delete_with_stale_content_conflicts(repo):
    """
    The optimistic delete pin: a delete carrying content that no longer
    matches the file (the entity changed after the caller derived its
    delete authorization from it) must conflict and leave the file alone;
    with the matching content it goes through.
    """
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoConflict):
            await s.delete("host", "b", "STALE-CONTENT\n", "del")
        assert await s.exists("host", "b") is True

        await s.delete("host", "b", "cpu: 8\n", "del")
        assert await s.exists("host", "b") is False


async def test_rename_same_name_rejected(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoClientError):
            await s.write_rename(
                "host", "a", "a", await s.get("host", "a"), "cpu: 5\n", "rn"
            )


async def test_copy_onto_existing_rejected(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoClientError):
            await s.copy("host", "b", "a", "copy")  # b already exists


async def test_link_to_missing_source_rejected(repo):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoNotFound):
            await s.link("host", "alias", "ghost", "link")


async def test_writes_are_pushed_to_remote(repo, tmp_path):
    async with repo.writer(USER) as untyped:
        s = untyped.session(DETAILS)
        await s.write("host", "pushed", "", "cpu: 1\n", "create")

    # a fresh clone of the remote sees the new file -> the push really happened
    verify = tmp_path / "verify"
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(verify))
    assert (verify / "hosts" / "pushed.yml").read_text() == "cpu: 1\n"


# ----- scope + safety guards -----

async def test_reader_scope_rejects_writes(repo):
    async with repo.reader(USER) as untyped:
        s = untyped.session(DETAILS)
        with pytest.raises(RepoError):
            await s.write("host", "x", "", "y", "m")
        # but reads + hash still work in a reader scope
        assert await s.exists("host", "a") is True
        assert len(await s.get_hash()) == 40


async def test_assert_inside_repo_blocks_escape(repo):
    with pytest.raises(RepoClientError):
        await repo._assert_inside_repo(f"{repo.path}/../escape.yml")
    # a normal in-repo path is accepted (no raise)
    await repo._assert_inside_repo(f"{repo.path}/hosts/ok.yml")


# ----- clone fallback + dirty-read freshness + link errors -----

async def test_clone_fallback_when_workdir_absent(repo, tmp_path):
    # A fresh handler whose on-disk path does not exist yet: the writer's _pull
    # fails to load, deletes nothing, and clones from URL (set by the fixture).
    fresh = gd.GitRepo()
    fresh.path = str(tmp_path / "cloned-work")
    fresh._loaded = False
    async with fresh.reader(USER) as untyped:
        s = untyped.session(DETAILS)
        assert await s.list("host") == ["a", "b", "link"]


async def test_is_outdated_true_with_zero_dirty_max(repo):
    # DIRTY_MAX defaults to 0 -> any fetch age is "outdated"
    assert await repo._is_outdated() is True


async def test_is_outdated_false_within_dirty_window(repo, monkeypatch):
    # With a generous window and a just-pulled repo, the data is considered fresh
    monkeypatch.setattr(gd, "DIRTY_MAX", 60)
    async with repo.reader(USER):  # triggers a pull, writing FETCH_HEAD
        pass
    assert await repo._is_outdated() is False


async def test_get_link_on_non_link_raises(repo):
    with pytest.raises(RepoError):
        await repo._get_link("host", "a", DETAILS)  # 'a' is a regular file


async def test_get_link_illegal_destination_raises(tmp_path, monkeypatch):
    # A symlink pointing outside the repo must be rejected.
    remote = tmp_path / "r.git"
    seed = tmp_path / "s"
    work = tmp_path / "w"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _git(seed, "config", "user.email", "s@x")
    _git(seed, "config", "user.name", "S")
    hosts = seed / "hosts"
    hosts.mkdir()
    (hosts / "a.yml").write_text("cpu: 4\n")
    (hosts / "bad.yml").symlink_to("../../../etc/hostname")  # escapes the repo
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "init")
    _git(seed, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(remote), str(work))

    monkeypatch.setattr(gd, "BRANCH", "main")
    gd._GLOB_CACHE.clear()
    h = gd.GitRepo()
    h.path = str(work)
    h._loaded = True
    with pytest.raises(RepoError):
        await h._get_link("host", "bad", DETAILS)
