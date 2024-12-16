from abc import abstractmethod
from contextlib import asynccontextmanager
from typing import Self, AsyncGenerator

from pydantic import BaseModel

from app.model.out import Diff
from app.model.out import User
from app.model.out import Permission


class Repo:

    @asynccontextmanager
    @abstractmethod
    async def reader(
        self, user: User | None, *, details: dict, dirty: bool = False
    ) -> AsyncGenerator[Self, None]: ...

    @asynccontextmanager
    @abstractmethod
    async def writer(
        self, user: User | None, *, details: dict
    ) -> AsyncGenerator[Self, None]: ...

    @abstractmethod
    def update_details(self, details: dict) -> None: ...

    @abstractmethod
    async def get_hash(self) -> str: ...

    @abstractmethod
    async def list(self) -> list[str]: ...

    @abstractmethod
    async def exists(self, name: str) -> bool: ...

    @abstractmethod
    async def is_link(self, name: str) -> bool: ...

    @abstractmethod
    async def get_link(self, name: str) -> str: ...

    @abstractmethod
    async def get_specs(self, name: str) -> str: ...

    @abstractmethod
    async def get(self, name: str) -> str: ...

    @abstractmethod
    async def write(
        self, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff: ...

    @abstractmethod
    async def write_rename(
        self, name_old: str, name_new: str, content_old: str, content_new: str, msg: str
    ) -> Diff: ...

    @abstractmethod
    async def copy(self, name_dest: str, name_src: str, msg: str) -> Diff: ...

    @abstractmethod
    async def link(self, name_link: str, name_src: str, msg: str) -> Diff: ...

    @abstractmethod
    async def delete(self, name: str, msg: str) -> None: ...


class Entity(BaseModel):
    name: None | str = None
    exists: None | bool = None
    is_link: None | bool = None
    link: None | str = None
    yaml: None | str = None
    data: None | dict = None
    perms: None | list[Permission | str] = None
