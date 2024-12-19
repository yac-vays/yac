from abc import abstractmethod
from contextlib import asynccontextmanager
from typing import Self, AsyncGenerator


from app.model.inp import OperationRequest
from app.model.out import Diff
from app.model.out import Log
from app.model.out import User
from app.model.int import Entity
from app.model.spc import Specs
from app.model.err import PluginError


class IAction:

    @abstractmethod
    async def run(self, *, details: dict, props: dict) -> None: ...


class ILog:

    @abstractmethod
    async def get(
        self,
        facility: str,
        problem: bool,
        progress: bool,
        *,
        details: dict,
        props: dict,
    ) -> list[Log]: ...


class ISortable:

    @abstractmethod
    def order(self) -> tuple[bool, int]: ...


class IJsonSchema(ISortable):

    @abstractmethod
    def order(self) -> tuple[bool, int]:
        """
        The boolean indicates if the process function should run post order
        (when walking back up the schema tree = true) or pre order (when
        walking down the schema tree = false). The integer indicates the order
        number (lower number run earlier).
        """
        ...

    @abstractmethod
    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        The first tuple element of the return value is the (modified)
        json_schema (only the current subschema we're working on) and the
        second one is a (across plugins) global context dict that can be used
        to store data while traversing the subschema tree.
        """
        ...


class IUiSchema(ISortable):

    @abstractmethod
    def order(self) -> tuple[bool, int]:
        """
        The boolean indicates if the process function should run post order
        (when walking back up the schema tree = true) or pre order (when
        walking down the schema tree = false). The integer indicates the order
        number (lower number run earlier).
        """
        ...

    @abstractmethod
    async def process(
        self, loc: str, json_schema: dict, ui_schema: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        The first tuple element of the return value is the (modified)
        json_schema (only the current subschema we're working on) and the
        second one is the (full, updated) ui_schema.
        """
        ...


class IValidator(ISortable):

    @abstractmethod
    def order(self) -> tuple[bool, int]:
        """
        The boolean indicates if the test should skip if entities are only listed
        (true) or if it should run always (false). The integer indicates the order
        number (lower number run earlier). All test_always functions always run
        before the test_nolist functions.
        """
        ...

    async def test_always(self, op: OperationRequest, spec: Specs) -> None:
        """
        This method is executed if order returns false (so it runs on every operation).
        """
        raise PluginError(
            f"Method test_always is not implement in {self.__class__.__name__}"
        )

    async def test_nolist(
        self, op: OperationRequest, spec: Specs, old: Entity, new: Entity
    ) -> None:
        """
        This method is executed if order returns true (so it runs on every operation
        except for listing entities).
        """
        raise PluginError(
            f"Method test_nolist is not implement in {self.__class__.__name__}"
        )


class IRepo:

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
