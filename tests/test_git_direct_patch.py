"""The patch returned by a git_direct write."""

from app.model.plg import CommitMsg

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401


async def test_patch_of_a_created_file_only_adds_lines(repo):  # noqa: F811
    diff = await repo._write(USER, DETAILS, "host", "new1", "", "a: 1\n", CommitMsg())
    body = diff.patch.split("\n")[3:]
    assert body == ["+a: 1", "+"]


async def test_patch_of_an_edit_shows_both_sides(repo):  # noqa: F811
    await repo._write(USER, DETAILS, "host", "new2", "", "a: 1\n", CommitMsg())
    diff = await repo._write(
        USER, DETAILS, "host", "new2", "a: 1\n", "a: 2\n", CommitMsg()
    )
    assert "-a: 1" in diff.patch and "+a: 2" in diff.patch
