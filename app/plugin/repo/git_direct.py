"""
When using this plugin, every (worker) process has its own repo copy in
`/repo/{pid}` and every non-dirty access acquires an exclusive lock on the
worker-repository to allow updating it before reading/writing. To optimize,
mount a tmpfs at `/repo`, so all the data is always in memory.

Per request, a `_GitRepoSession` is yielded that captures the user and
`details` (entity-type → path-template) for that scope. The handler holds
only the on-disk path and the cross-scope locks; user/details/git-env are
never shared between concurrent requests.

The connection config is read from `repo.connection` in the specs file at
process startup; changes require a pod restart.

  url:                  The HTTPS or SSH URL to the git repo.
                        default: '' -> required!
  branch:               The branch to work on.
                        default: 'main'
  ssh_key_file:         Path to the private key file.
                        default: '/home/yac/.ssh/id_rsa'
  ssh_known_hosts_file: Path to the known hosts file.
                        default: '/home/yac/.ssh/known_hosts'
  dirty_max_age:        Acceptable age (in minutes) of the last git fetch
                        where a dirty read will not update the data again.
                        default: 0

Details:

  <type-name>: The path for YAML files of this entity type.
               type: string (must contain "name" as j2 var and must not contain "*")
               default: "" -> required!
               example: path/to/{{ name }}/yac_data.yml
"""

from contextlib import asynccontextmanager
from difflib import unified_diff
from os import getpid
from os.path import dirname
from typing import AsyncGenerator
import asyncio
import logging
import os
import time
import re

from aioshutil import rmtree
from anyio import Path, open_file

from app.lib import git
from app.lib import j2
from app.lib import specs as _specs
from app.model.err import RepoClientError
from app.model.err import RepoConflict
from app.model.err import RepoError
from app.model.err import RepoNotFound
from app.model.err import RepoTimeoutError
from app.model.err import RepoSpecsError
from app.model.out import Diff
from app.model.out import User
from app.model.plg import IRepo
from app.model.plg import IRepoSession
from app.model.plg import IRepoUntyped


logger = logging.getLogger(__name__)
_CONN = _specs.get_repo_connection()
URL = _CONN.get("url", "")
BRANCH = _CONN.get("branch", "main")
KEY_FILE = _CONN.get("ssh_key_file", "/home/yac/.ssh/id_rsa")
KNOWN_HOSTS = _CONN.get("ssh_known_hosts_file", "/home/yac/.ssh/known_hosts")
DIRTY_MAX = int(_CONN.get("dirty_max_age", 0))


# Module-level cache of rendered globs/paths keyed by (type, template). Path
# templates are stable across the lifetime of a process (they come from specs),
# so this cache never needs explicit invalidation. The j2 layer also caches
# compiled templates, so misses are cheap.
_GLOB_CACHE: dict[tuple[str, str], str] = {}


def _path_template(type_name: str, details: dict) -> str:
    template = details.get(type_name)
    if not isinstance(template, str) or not template:
        raise RepoSpecsError(
            f"No path template configured for type '{type_name}' in repo.details"
        )
    return template


async def _render_glob(type_name: str, details: dict) -> str:
    template = _path_template(type_name, details)
    key = (type_name, template)
    cached = _GLOB_CACHE.get(key)
    if cached is not None:
        return cached
    rendered = await j2.render_str(template, {"name": "*"})
    if "*" not in rendered or rendered.count("*") > 1:
        raise RepoSpecsError(
            f"Path template for type '{type_name}' must contain exactly one"
            f" '*' after rendering with name='*' (got '{rendered}')"
        )
    _GLOB_CACHE[key] = rendered
    return rendered


async def _render_path(type_name: str, name: str, details: dict) -> str:
    template = _path_template(type_name, details)
    return await j2.render_str(template, {"name": name})


def _make_git_repo(path: str, user: User | None) -> git.Repo:
    user_name = user.full_name if user is not None else "Unknown"
    user_email = user.email if user is not None else "<>"
    return git.Repo(
        path=path,
        env={
            "EMAIL": user_email,
            "GIT_AUTHOR_EMAIL": user_email,
            "GIT_AUTHOR_NAME": f"{user_name} (via YAC)",
            "GIT_COMMITTER_EMAIL": user_email,
            "GIT_COMMITTER_NAME": f"{user_name} (via YAC)",
            "GIT_SSH_COMMAND": (
                f"ssh -o UserKnownHostsFile={KNOWN_HOSTS} -i {KEY_FILE}"
            ),
            "LANG": "C",
        },
    )


class _GitRepoUntyped(IRepoUntyped):
    """
    Per-scope view of the repository before any entity-type details are
    known. Returned by `GitRepo.reader` / `GitRepo.writer`. Use `session()`
    to obtain a typed `_GitRepoSession` once `details` are known.
    """

    def __init__(
        self, handler: "GitRepo", user: User | None, *, writing: bool
    ) -> None:
        self._h = handler
        self._user = user
        self._writing = writing

    async def get_hash(self) -> str:
        return await self._h._get_hash()

    def session(self, details: dict) -> "_GitRepoSession":
        return _GitRepoSession(
            self._h, self._user, details, writing=self._writing
        )


class _GitRepoSession(IRepoSession):
    """
    Typed view that carries the user and details for one reader or writer
    scope. Created via `_GitRepoUntyped.session(details)`. Write methods
    raise when the underlying scope is a reader.
    """

    def __init__(
        self,
        handler: "GitRepo",
        user: User | None,
        details: dict,
        *,
        writing: bool,
    ) -> None:
        self._h = handler
        self._user = user
        self._details = dict(details)
        self._writing = writing

    async def get_hash(self) -> str:
        return await self._h._get_hash()

    async def list(self, type: str) -> list[str]:
        return await self._h._list(type, self._details)

    async def exists(self, type: str, name: str) -> bool:
        return await self._h._exists(type, name, self._details)

    async def is_link(self, type: str, name: str) -> bool:
        return await self._h._is_link(type, name, self._details)

    async def get_link(self, type: str, name: str) -> str:
        return await self._h._get_link(type, name, self._details)

    async def get(self, type: str, name: str) -> str:
        return await self._h._get(type, name, self._details)

    def _require_writing(self) -> None:
        if not self._writing:
            raise RepoError("Write operation outside writer scope")

    async def write(
        self, type: str, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        self._require_writing()
        return await self._h._write(
            self._user, self._details, type, name, content_old, content_new, msg
        )

    async def write_rename(
        self,
        type: str,
        name_old: str,
        name_new: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff:
        self._require_writing()
        return await self._h._write_rename(
            self._user,
            self._details,
            type,
            name_old,
            name_new,
            content_old,
            content_new,
            msg,
        )

    async def copy(
        self, type: str, name_dest: str, name_src: str, msg: str
    ) -> Diff:
        self._require_writing()
        return await self._h._copy(
            self._user, self._details, type, name_dest, name_src, msg
        )

    async def link(
        self, type: str, name_link: str, name_src: str, msg: str
    ) -> Diff:
        self._require_writing()
        return await self._h._link(
            self._user, self._details, type, name_link, name_src, msg
        )

    async def delete(self, type: str, name: str, content_old: str, msg: str) -> None:
        self._require_writing()
        await self._h._delete(self._user, self._details, type, name, content_old, msg)


class GitRepo(IRepo):
    """
    Process-singleton: owns the on-disk repo path and the cross-scope locks.
    Holds NO per-request state — `details`, `user`, and the per-call git.Repo
    travel with each reader/writer scope via `_GitRepoSession`.
    """

    def __init__(self) -> None:
        self.path: str = f"/repo/{getpid()}"
        # Whether the on-disk repo has been load()-checked or cloned at least once.
        self._loaded: bool = False
        # Reader/writer locking: positive count = readers, -1 = writer active.
        self._reader_count: int = 0
        self._reader_update_lock: asyncio.Lock = asyncio.Lock()
        self._no_readers: asyncio.Condition = asyncio.Condition(
            self._reader_update_lock
        )
        self._writer_lock: asyncio.Lock = asyncio.Lock()

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, dirty: bool = False
    ) -> AsyncGenerator[_GitRepoUntyped, None]:
        logger.debug(f"Acquiring git reader lock for {self.path}...")
        if not dirty or await self._is_outdated():
            logger.debug(
                f"Upgrading lock to git writer lock to pull repo at {self.path}!"
            )
            async with self.writer(user):
                pass

        async with self._reader_update_lock:
            while self._reader_count == -1:
                await self._no_readers.wait()
            logger.debug(f"... git reader lock for {self.path} acquired!")
            self._reader_count += 1

        try:
            yield _GitRepoUntyped(self, user, writing=False)
        except git.GitTimeoutError as error:
            raise RepoTimeoutError(str(error)) from error
        finally:
            async with self._reader_update_lock:
                logger.debug(f"Releasing git reader lock for {self.path}!")
                self._reader_count -= 1
                if self._reader_count == 0:
                    self._no_readers.notify_all()

    @asynccontextmanager
    async def writer(
        self, user: User | None
    ) -> AsyncGenerator[_GitRepoUntyped, None]:
        logger.debug(f"Acquiring git writer lock for {self.path}...")
        async with self._writer_lock:
            async with self._reader_update_lock:
                while self._reader_count != 0:
                    await self._no_readers.wait()
                logger.debug(f"... git writer lock for {self.path} acquired!")
                self._reader_count = -1

            try:
                await self._pull(user)
                try:
                    yield _GitRepoUntyped(self, user, writing=True)
                except git.GitTimeoutError as error:
                    raise RepoTimeoutError(str(error)) from error
            finally:
                async with self._reader_update_lock:
                    self._reader_count = 0
                    self._no_readers.notify_all()

    # ------------------------------------------------------------------ #
    # On-disk operations. Each method takes the parameters it needs; no
    # state is read off `self` other than the on-disk path and the loaded
    # flag.
    # ------------------------------------------------------------------ #

    async def _is_outdated(self) -> bool:
        gr = _make_git_repo(self.path, None)
        try:
            last_fetch = await gr.get_fetch_time()
        except git.GitError as error:
            logger.error(str(error))
            return True
        return (time.time() - last_fetch) > 60 * DIRTY_MAX

    async def _pull(self, user: User | None) -> None:
        gr = _make_git_repo(self.path, user)
        try:
            if not self._loaded:
                await gr.load()
                self._loaded = True
            logger.debug(f"Pulling git repo at {self.path}")
            await gr.pull()
        except git.GitError:
            try:
                await rmtree(self.path)
            except FileNotFoundError:
                pass  # it may not be there yet
            except OSError as error:
                raise RepoError(f"Cannot delete {self.path}") from error
            logger.info(f"Cloning git repo to {self.path}")
            try:
                await gr.clone(URL, branch=BRANCH)
                self._loaded = True
            except git.GitError as error:
                raise RepoError(
                    f"Cannot clone repo to {self.path}: {error}"
                ) from error

    async def _push(self, user: User | None, files: list[str], msg: str) -> None:
        gr = _make_git_repo(self.path, user)
        try:
            await gr.add(files)
            await gr.commit(f"[YAC] {msg}")
            logger.debug(f"Pushing new git commit from {self.path} to remote")
            await gr.push()
        except git.GitError as error:
            # Very unlikely scenario where someone pushes from a different
            # instance or directly to the repo in the millisecond between
            # pull and push. If this occurs more often than expected
            # (~ never), we can implement a retry mechanism here.
            await self._cleanup(user)
            raise RepoError(
                f"Unable to commit and push changes from {self.path}"
            ) from error
        await self._cleanup(user)

    async def _cleanup(self, user: User | None) -> None:
        gr = _make_git_repo(self.path, user)
        if not await gr.is_dirty():
            return
        try:
            logger.debug(f"Cleaning git repo at {self.path}")
            await gr.reset(f"origin/{BRANCH}", hard=True)
            await gr.clean(recursive=True, force=True)
            assert not await gr.is_dirty()
        except (git.GitError, AssertionError):
            await self._pull(user)

    @staticmethod
    def _make_relative(path: str, path2: str) -> str:
        # Use os.path.relpath: returns a path from path2's directory to path,
        # correctly handling component boundaries.
        return os.path.relpath(path, os.path.dirname(path2))

    async def _assert_inside_repo(self, file: str) -> None:
        base = str(await Path(self.path).resolve())
        # Resolve the parent of the target (the target itself may not exist yet
        # for write/create operations) and assert it stays under base.
        try:
            parent = str(await Path(dirname(file) or self.path).resolve())
        except OSError as error:
            raise RepoError(f"Could not resolve path {file}") from error
        if parent != base and not parent.startswith(base + "/"):
            # Keep the path detail server-side; the client message must not
            # leak filesystem paths.
            logger.warning(f"Resolved path escapes the repository: {file}")
            raise RepoClientError("The resolved file path escapes the repository")

    async def _read_file(self, file: str, *, what: str, absolute: bool = True) -> str:
        """
        `what` describes the entity (type/name) for the client-facing
        not-found message; the filesystem path is only logged server-side.
        """
        absfile = file if absolute else f"{self.path}/{file}"
        try:
            async with await open_file(absfile, "r", encoding="utf-8") as f:
                logger.debug(f"Reading file {absfile}")
                return await f.read()
        except FileNotFoundError as error:
            logger.info(f"File {absfile} does not exist ({what})")
            raise RepoNotFound(f"The {what} does not exist") from error
        except OSError as error:
            raise RepoError(f"Could not read file {absfile}") from error

    async def _file_path(self, type_name: str, name: str, details: dict) -> str:
        return f"{self.path}/{await _render_path(type_name, name, details)}"

    async def _has_link(self, type_name: str, name: str, details: dict) -> bool:
        """
        Return True iff any other entity of `type_name` is a symlink whose
        resolved target is the entity (`type_name`, `name`).

        Looks across the whole type's glob (not just the immediate dirname)
        so it works for both flat and nested path templates.
        """
        target_abs = await self._file_path(type_name, name, details)
        try:
            target_resolved = await Path(target_abs).resolve()
        except OSError as error:
            raise RepoError(f"Could not resolve {target_abs}") from error
        glob_pat = await _render_glob(type_name, details)
        try:
            async for candidate in Path(self.path).glob(glob_pat):
                if str(candidate) == target_abs:
                    continue  # don't match self
                if not await candidate.is_symlink():
                    continue
                if await candidate.resolve() == target_resolved:
                    return True
        except OSError as error:
            raise RepoError(
                f"Could not scan for links of {type_name} {name}"
            ) from error
        return False

    # ----- read ops -----

    async def _get_hash(self) -> str:
        gr = _make_git_repo(self.path, None)
        return await gr.get_hash()

    async def _list(self, type_name: str, details: dict) -> list[str]:
        glob_pat = await _render_glob(type_name, details)
        start, end = glob_pat.split("*", maxsplit=1)
        pattern = re.compile(
            rf"^{re.escape(self.path)}/{re.escape(start)}(.+){re.escape(end)}$"
        )
        try:
            return sorted(
                [
                    pattern.findall(str(fn))[0]
                    async for fn in Path(self.path).glob(glob_pat)
                ]
            )
        except (OSError, AttributeError, KeyError, IndexError) as error:
            raise RepoError(
                f"Could not list files at {self.path}/{glob_pat}"
            ) from error

    async def _exists(self, type_name: str, name: str, details: dict) -> bool:
        path = await self._file_path(type_name, name, details)
        try:
            return await Path(path).exists()
        except OSError as error:
            raise RepoError(f"Could not read file {path}") from error

    async def _is_link(self, type_name: str, name: str, details: dict) -> bool:
        path = await self._file_path(type_name, name, details)
        try:
            return await Path(path).is_symlink()
        except OSError as error:
            raise RepoError(f"Could not read file {path}") from error

    async def _get_link(self, type_name: str, name: str, details: dict) -> str:
        if not await self._is_link(type_name, name, details):
            raise RepoError(f"File {name} is not a link")

        base = str(await Path(self.path).resolve())
        src = "/".join([base, await _render_path(type_name, name, details)])
        dest = str(await Path(src).resolve())

        if not dest.startswith(base):
            raise RepoError(f"Link {src} has an illegal destination: {dest}")

        glob_pat = await _render_glob(type_name, details)
        link_rel = dest[(len(base) + 1) :]
        start, end = glob_pat.split("*", maxsplit=1)
        try:
            return re.findall(
                rf"^{re.escape(start)}(.+){re.escape(end)}$", link_rel
            )[0]
        except (AttributeError, KeyError, IndexError) as error:
            raise RepoError(
                f"Link {src} has an illegal destination: {dest}"
            ) from error

    async def _get(self, type_name: str, name: str, details: dict) -> str:
        path = await self._file_path(type_name, name, details)
        return await self._read_file(
            path, what=f"{type_name} {name}", absolute=True
        )

    # ----- write ops -----

    async def _write(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff:
        path_rel = await _render_path(type_name, name, details)
        file_abs = f"{self.path}/{path_rel}"
        await self._assert_inside_repo(file_abs)

        if await self._exists(type_name, name, details):
            content = await self._get(type_name, name, details)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if content == content_new:
                raise RepoClientError("Cannot write without changing anything")
            if await self._is_link(type_name, name, details):
                raise RepoClientError("Modifying links is not allowed")
        elif len(content_old) > 0:
            raise RepoConflict("The file has been deleted in the meantime")

        try:
            async with await open_file(file_abs, "w+", encoding="utf-8") as f:
                logger.debug(f"Writing file {file_abs}")
                await f.write(content_new)
        except OSError as error:
            raise RepoError(f"Could not write file {file_abs}") from error

        await self._push(user, [file_abs], msg)
        patch = "\n".join(
            unified_diff(
                content_old.split("\n"),
                content_new.split("\n"),
                fromfile=f"a/{path_rel}",
                tofile=f"b/{path_rel}",
                lineterm="",
            )
        )

        return Diff(name=name, hash=await self._get_hash(), patch=patch)

    async def _write_rename(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name_old: str,
        name_new: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff:
        path_old = await _render_path(type_name, name_old, details)
        path_new = await _render_path(type_name, name_new, details)
        file_old = f"{self.path}/{path_old}"
        file_new = f"{self.path}/{path_new}"
        await self._assert_inside_repo(file_old)
        await self._assert_inside_repo(file_new)

        if name_old == name_new:
            raise RepoClientError("Cannot rename without chaning the name")
        if await self._exists(type_name, name_old, details):
            content = await self._get(type_name, name_old, details)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if await self._is_link(type_name, name_old, details):
                raise RepoClientError("Modifying links is not allowed")
        else:
            raise RepoConflict("The file has been deleted in the meantime")
        if await self._exists(type_name, name_new, details):
            raise RepoClientError("The file already exists")

        try:
            async with await open_file(file_new, "w+", encoding="utf-8") as f:
                logger.debug(f"Writing file {file_new}")
                await f.write(content_new)
        except OSError as error:
            raise RepoError(f"Could not write file {file_new}") from error

        try:
            await Path(file_old).unlink()
        except OSError as error:
            raise RepoError(f"Could not delete file {file_old}") from error

        await self._push(user, [file_old, file_new], msg)
        patch = "\n".join(
            unified_diff(
                content_old.split("\n"),
                content_new.split("\n"),
                fromfile=f"a/{path_old}",
                tofile=f"b/{path_new}",
                lineterm="",
            )
        )

        return Diff(name=name_new, hash=await self._get_hash(), patch=patch)

    async def _copy(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name_dest: str,
        name_src: str,
        msg: str,
    ) -> Diff:
        if await self._exists(type_name, name_dest, details):
            raise RepoClientError("The file already exists")

        path_dest = await _render_path(type_name, name_dest, details)
        file_src = await self._file_path(type_name, name_src, details)
        file_dest = f"{self.path}/{path_dest}"
        await self._assert_inside_repo(file_src)
        await self._assert_inside_repo(file_dest)

        content = await self._read_file(
            file_src, what=f"{type_name} {name_src}", absolute=True
        )

        try:
            async with await open_file(file_dest, "w+", encoding="utf-8") as f:
                logger.debug(f"Writing file {file_dest}")
                await f.write(content)
        except OSError as error:
            raise RepoError(f"Could not create file {file_dest}") from error

        await self._push(user, [file_dest], msg)
        patch = "\n".join(
            unified_diff(
                [],
                content.split("\n"),
                fromfile=f"a/{path_dest}",
                tofile=f"b/{path_dest}",
                lineterm="",
            )
        )

        return Diff(name=name_dest, hash=await self._get_hash(), patch=patch)

    async def _link(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name_link: str,
        name_src: str,
        msg: str,
    ) -> Diff:
        if not await self._exists(type_name, name_src, details):
            raise RepoNotFound("The file does not exist")

        path_link = await _render_path(type_name, name_link, details)
        link = f"{self.path}/{path_link}"
        src = await self._file_path(type_name, name_src, details)
        await self._assert_inside_repo(link)
        await self._assert_inside_repo(src)

        try:
            await Path(link).symlink_to(self._make_relative(src, link))
        except FileExistsError as error:
            raise RepoClientError("The file already exists") from error
        except OSError as error:
            raise RepoError(f"Could not create symlink {link}") from error

        await self._push(user, [link], msg)
        patch = "\n".join(
            unified_diff(
                [],
                name_src.split("\n"),
                fromfile=f"a/{path_link}",
                tofile=f"b/{path_link}",
                lineterm="",
            )
        )

        return Diff(name=name_link, hash=await self._get_hash(), patch=patch)

    async def _delete(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name: str,
        content_old: str,
        msg: str,
    ) -> None:
        if not await self._exists(type_name, name, details):
            raise RepoNotFound("The file does not exist")
        if await self._has_link(type_name, name, details):
            raise RepoClientError("The file must not be deleted because it is linked")

        # Optimistic pin, mirroring `_write`: the caller validated the delete
        # (perms from `old.data`, templated delete hooks) against `content_old`
        # under a READER scope; this writer scope pulled in the meantime. If
        # the entity changed, that authorization is stale — conflict instead
        # of acting on it.
        content = await self._get(type_name, name, details)
        if content != content_old:
            raise RepoConflict("The data has changed in the meantime")

        file = await self._file_path(type_name, name, details)
        await self._assert_inside_repo(file)
        try:
            await Path(file).unlink()
        except OSError as error:
            raise RepoError(f"Could not delete file {file}") from error

        await self._push(user, [file], msg)


handler = GitRepo()
