from abc import abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncGenerator


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

    @abstractmethod
    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        The first tuple element of the return value is the (modified)
        json_schema (only the current subschema we're working on) and the
        second one is a (across plugins) global context dict that can be used
        to store data while traversing the subschema tree.

        If the returned `schema` is `None`, the whole subschema will be removed
        from the parent schema.
        """


class IUiSchema(ISortable):

    @abstractmethod
    def order(self) -> tuple[bool, int]:
        """
        The boolean indicates if the process function should run post order
        (when walking back up the schema tree = true) or pre order (when
        walking down the schema tree = false). The integer indicates the order
        number (lower number run earlier).

        If the returned `schema` is `None`, the whole subschema will be removed
        from the parent schema.
        """

    @abstractmethod
    async def process(
        self, loc: str, json_schema: dict, ui_schema: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        The first tuple element of the return value is the (modified)
        json_schema (only the current subschema we're working on) and the
        second one is the (full, updated) ui_schema.
        """


class IValidator(ISortable):

    @abstractmethod
    def order(self) -> tuple[bool, int]:
        """
        The boolean indicates if the test should skip if entities are only listed
        (true) or if it should run always (false). The integer indicates the order
        number (lower number run earlier). All test_always functions always run
        before the test_nolist functions.
        """

    async def test_always(self, op: OperationRequest, spec: Specs) -> None:
        """
        This method is executed if order returns false (so it runs on every operation).
        """
        raise PluginError(
            f"Method test_always is not implement in {self.__class__.__name__}"
        )

    async def test_nolist(
        self,
        op: OperationRequest,
        spec: Specs,
        old: Entity,
        new: Entity,
        perms: list[str],
    ) -> None:
        """
        This method is executed if order returns true (so it runs on every operation
        except for listing entities).
        """
        raise PluginError(
            f"Method test_nolist is not implement in {self.__class__.__name__}"
        )


class IRepoSession:
    """
    Typed view of the repository for one request scope, given an entity-type
    → path-template mapping (`details`). Created from an `IRepoUntyped` via
    `IRepoUntyped.session(details)`; sharing the same lock scope as its parent
    untyped view (no extra git lock is acquired).

    Methods on this interface read path templates from `details`. Writes are
    only valid when the underlying scope was opened via `IRepo.writer`; on a
    reader scope they raise `RepoError`.
    """

    @abstractmethod
    async def get_hash(self) -> str: ...

    @abstractmethod
    async def list(self, type: str) -> list[str]: ...

    @abstractmethod
    async def exists(self, type: str, name: str) -> bool: ...

    @abstractmethod
    async def is_link(self, type: str, name: str) -> bool: ...

    @abstractmethod
    async def get_link(self, type: str, name: str) -> str: ...

    @abstractmethod
    async def get(self, type: str, name: str) -> str: ...

    async def get_resolved(self, type: str, name: str) -> tuple[str, str | None]:
        """
        Like `get`, but also report the link target: returns
        `(content, target_name)` where `content` is the effective YAML (a
        symlink is followed) and `target_name` is the entity this one links to,
        or `None` for a regular file.

        Used by `lib.limits` so a symlink to the entity being edited can be
        counted with the *incoming* data instead of its stale on-disk copy.
        This default is correct for any backend; cached backends should override
        it so the link status rides along with the single content fetch instead
        of costing extra round-trips.
        """
        content = await self.get(type, name)
        if await self.is_link(type, name):
            return content, await self.get_link(type, name)
        return content, None

    @abstractmethod
    async def write(
        self, type: str, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff: ...

    @abstractmethod
    async def write_rename(
        self,
        type: str,
        name_old: str,
        name_new: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff: ...

    @abstractmethod
    async def copy(
        self, type: str, name_dest: str, name_src: str, msg: str
    ) -> Diff: ...

    @abstractmethod
    async def link(
        self, type: str, name_link: str, name_src: str, msg: str
    ) -> Diff: ...

    @abstractmethod
    async def delete(self, type: str, name: str, content_old: str, msg: str) -> None:
        """
        Delete the entity, but only if its current content still equals
        `content_old` — the content the caller derived its authorization (and
        the templated delete hooks) from. A mismatch must raise `RepoConflict`,
        exactly like `write`: the permissions/roles can depend on the entity
        data (`old.data` in role conditions), so deleting a concurrently
        modified entity would act on stale authorization.
        """
        ...


class IRepoUntyped:
    """
    Per-scope view of the repository before any entity-type details are
    known. Use this view to query the current commit hash (`get_hash`).
    Once the entity-type `details` are known (typically from
    `specs.repo.details`), call `session(details)` to obtain an
    `IRepoSession` for typed entity operations. The returned session shares
    this scope's lock — no extra git lock is acquired.
    """

    @abstractmethod
    async def get_hash(self) -> str: ...

    @abstractmethod
    def session(self, details: dict) -> IRepoSession: ...


class IRepo:
    """
    Process-level handle for the repository. Owns the on-disk path and the
    cross-scope locks. Per-request state lives on the untyped view yielded by
    `reader` / `writer`; the caller derives a typed `IRepoSession` from it
    once the entity-type `details` are known (typically after parsing specs).
    """

    @asynccontextmanager
    @abstractmethod
    async def reader(
        self, user: User | None, *, dirty: bool = False
    ) -> AsyncGenerator[IRepoUntyped, None]: ...

    @asynccontextmanager
    @abstractmethod
    async def writer(
        self, user: User | None
    ) -> AsyncGenerator[IRepoUntyped, None]: ...
