from contextlib import asynccontextmanager
from typing import Self, AsyncGenerator
from datetime import datetime

import redis.asyncio as redis

from app.lib import plugin
from app.model.out import Diff
from app.model.out import User
from app.model.plg import IRepo
from app.model.err import RepoError

# TODO max_age_seconds and redis_url from ENV
# TODO handle redis exceptions!

# Redis structure:
# latest: a93b983c
# synced: datetime
# data:
#   a93b983c:
#     path/to/file.yml: content
#     path/to/2nd-file.yml: 2nd \n content

# TODO use background-task to update redis after response (or better a global middleware)
# TODO make init function that is executed on fastapi startup
# TODO disconnect on shutdown!?


class GitRedisRepo(IRepo):

    def __init__(self):
        self.git_direct = plugin.get_module("repo", "git_direct").handler
        self.redis = None

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, details: dict, dirty: bool = False
    ) -> AsyncGenerator[Self, None]:
        await self.__init_redis()
        now = datetime.now().timestamp()
        if await self.redis.get("synced") > now - max_age_seconds:
            # Update the synced-timestamp early to avoid simulatneous git pulls; this way we
            # accept that synced is updated before the latest field points to the new hash!
            await self.redis.set("synced", now)
            async with self.git_direct.reader(
                user, details=details, dirty=False
            ) as rpo:
                await self.__update_redis(rpo)
        # TODO check if current entity type (or specs!!?) is in cache already, if not -> __update_redis(rpo)!!
        yield self

    @asynccontextmanager
    async def writer(
        self, user: User | None, *, details: dict
    ) -> AsyncGenerator[Self, None]:
        async with self.git_direct.writer(user, details=details) as rpo:
            yield rpo
            await self.__update_redis(rpo)

    async def __init_redis(self) -> None:
        if self.redis is not None:
            return
        self.redis = redis.from_url(redis_url)

    async def __update_redis(self, git_direct: IRepo) -> None:
        pass  # TODO use git_direct to get the data and write it into redis cache, update latest field and delete old data!

    async def get_hash(self) -> str:
        return await self.redis.get("latest")

    async def list(self, type: str) -> list[str]:
        return []  # TODO get from redis

    async def exists(self, type: str, name: str) -> bool:
        return True  # TODO get from redis

    async def is_link(self, type: str, name: str) -> bool:
        return False  # TODO get from redis

    async def get_link(self, type: str, name: str) -> str:
        return ""  # TODO get from redis

    async def get_specs(self, name: str) -> str:
        return ""  # TODO get from redis

    async def get(self, type: str, name: str) -> str:
        return ""  # TODO get from redis

    async def update_details(self, details: dict) -> None:
        pass

    async def write(
        self, type: str, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        raise RepoError("Illegal function call!")

    async def write_rename(
        self,
        type: str,
        name_old: str,
        name_new: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff:
        raise RepoError("Illegal function call!")

    async def copy(self, type: str, name_dest: str, name_src: str, msg: str) -> Diff:
        raise RepoError("Illegal function call!")

    async def link(self, type: str, name_link: str, name_src: str, msg: str) -> Diff:
        raise RepoError("Illegal function call!")

    async def delete(self, type: str, name: str, msg: str) -> None:
        raise RepoError("Illegal function call!")


handler = GitRedisRepo()
