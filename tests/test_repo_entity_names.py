"""
Which files of the data repository are entities of a type: the path template
with exactly one path segment in place of the name -- the same in git_direct
and git_redis.
"""

import os

import app.plugin.repo.git_direct as gd
import app.plugin.repo.git_redis as grs

from tests.lib_git_direct import DETAILS, USER, repo  # noqa: F401  (fixture)


def test_name_from_path():
    assert gd._name_from_path("hosts/*.yml", "hosts/a.yml") == "a"
    assert gd._name_from_path("hosts/*.yml", "hosts/sub/b.yml") is None
    assert gd._name_from_path("hosts/*.yml", "hosts/.yml") is None
    assert gd._name_from_path("hosts/*.yml", "other/a.yml") is None
    assert gd._name_from_path("*.yml", "a.yml") == "a"
    assert gd._name_from_path("*.yml", "hosts/a.yml") is None
    assert gd._name_from_path("path/*/yac_data.yml", "path/web01/yac_data.yml") == "web01"


async def test_git_direct_list_ignores_a_file_without_name(repo):
    with open(f"{repo.path}/hosts/.yml", "w", encoding="utf-8"):
        pass
    os.makedirs(f"{repo.path}/hosts/sub")
    with open(f"{repo.path}/hosts/sub/c.yml", "w", encoding="utf-8"):
        pass
    assert await repo._list("host", DETAILS) == ["a", "b", "link"]


async def test_git_redis_list_ignores_subdirectories():
    class _Redis:
        async def hkeys(self, key):
            del key
            return ["", "hosts/a.yml", "hosts/sub/b.yml", "hosts/.yml", "README.md"]

    s = grs._GitRedisSession(None, _Redis(), "H", DETAILS)
    assert await s.list("host") == ["a"]
    top = grs._GitRedisSession(None, _Redis(), "H", {"doc": "{{ name }}.md"})
    assert await top.list("doc") == ["README"]
