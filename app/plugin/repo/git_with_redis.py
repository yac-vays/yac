from contextlib import asynccontextmanager
from typing import Self, AsyncGenerator

from app.model.out import Diff
from app.model.out import User
from app.model.rpo import Repo


class GitRedisRepo(Repo):

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, details: dict, dirty: bool = False
    ) -> AsyncGenerator[Self, None]:
        # TODO
        yield self
        # TODO

    @asynccontextmanager
    async def writer(
        self, user: User | None, *, details: dict
    ) -> AsyncGenerator[Self, None]:
        # TODO
        yield self
        # TODO

    def update_details(self, details: dict) -> None:
        self.fpath = details.get("file", "")  # TODO

    async def get_hash(self) -> str:
        return ""  # TODO

    async def list(self) -> list[str]:
        return []  # TODO

    async def exists(self, name: str) -> bool:
        return True  # TODO

    async def is_link(self, name: str) -> bool:
        return False  # TODO

    async def get_link(self, name: str) -> str:
        return ""  # TODO

    async def get_specs(self, name: str) -> str:
        return ""  # TODO

    async def get(self, name: str) -> str:
        return ""  # TODO

    async def write(
        self, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        return Diff()  # TODO

    async def write_rename(
        self, name_old: str, name_new: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        return Diff()  # TODO

    async def copy(self, name_dest: str, name_src: str, msg: str) -> Diff:
        return Diff()  # TODO

    async def link(self, name_link: str, name_src: str, msg: str) -> Diff:
        return Diff()  # TODO

    async def delete(self, name: str, msg: str) -> None:
        pass  # TODO


handler = GitRedisRepo()
