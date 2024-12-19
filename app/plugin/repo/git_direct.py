"""
When using this plugin, every (worker) process will have its own repo copy in
`/repo/{pid}` and every non-dirty access will cause an exclusive lock on the
worker-repository to allow updating it before reading/writing. To optimize,
mount a tmpfs at `/repo`, so all the data is always in memory.

Details:

  file: The path for YAML files of this entity type.
        type: format-string (with {name} as entity name)
        default: null -> required!
        example: path/to/{name}/yac_managed.yml

Environment variables:

  YAC_REPO__URL:                  The HTTPS or SSH URL to the git repo.
                                  default: '' -> required!
  YAC_REPO__BRANCH:               The branch to work on.
                                  default: 'main'
  YAC_REPO__SSH_KEY_FILE:         Path to the private key file.
                                  default: '/root/.ssh/id_rsa'
  YAC_REPO__SSH_KNOWN_HOSTS_FILE: Path to the known hosts file.
                                  default: '/root/.ssh/known_hosts'
  YAC_REPO__DIRTY_MAX_AGE:        Acceptable age (in minutes) of the last git
                                  fetch where a dirty read will not update the
                                  data again.
                                  default: '0'
"""

from contextlib import asynccontextmanager
from difflib import unified_diff
from os import getpid
from os.path import dirname
from typing import Self, AsyncGenerator
import asyncio
import logging
import time

from aioshutil import rmtree
from anyio import Path, open_file
from parse import parse

from app import consts
from app.lib import git
from app.model.err import RepoClientError
from app.model.err import RepoConflict
from app.model.err import RepoError
from app.model.err import RepoNotFound
from app.model.err import RepoTimeoutError
from app.model.err import RepoSpecsError
from app.model.out import Diff
from app.model.out import User
from app.model.plg import IRepo


logger = logging.getLogger(__name__)
URL = consts.ENV.repo.get("url", "")
BRANCH = consts.ENV.repo.get("branch", "main")
KEY_FILE = consts.ENV.repo.get("ssh_key_file", "/root/.ssh/id_rsa")
KNOWN_HOSTS = consts.ENV.repo.get("ssh_known_hosts_file", "/root/.ssh/known_hosts")
DIRTY_MAX = int(consts.ENV.repo.get("dirty_max_age", "0"))


class GitRepo(IRepo):
    def __init__(self) -> None:
        self.fpath: str = ""
        self.dirty: bool = False
        self.writing: bool = False
        self.path: str = f"/repo/{getpid()}"
        self.repo: git.Repo
        self._reader_count: int = 0  # writer = -1
        self._reader_update_lock: asyncio.Lock = asyncio.Lock()
        self._no_readers: asyncio.Condition = asyncio.Condition(
            self._reader_update_lock
        )
        self._writer_lock: asyncio.Lock = asyncio.Lock()

    def __update(self, user: User | None, details: dict, dirty: bool, writing: bool):
        user_name = user.full_name if user is not None else "Unknown"
        user_email = user.email if user is not None else "<>"
        self.dirty = dirty
        self.writing = writing
        self.repo = git.Repo(
            path=self.path,
            env={
                "EMAIL": user_email,
                "GIT_AUTHOR_EMAIL": user_email,
                "GIT_AUTHOR_NAME": f"{user_name} (via YAC)",
                "GIT_SSH_COMMAND": f"ssh -o UserKnownHostsFile={KNOWN_HOSTS} -i {KEY_FILE}",
                "LANG": "C",
            },
        )
        try:
            self.fpath.format(name="*")
        except (AttributeError, KeyError) as error:
            raise RepoSpecsError(
                "In type details: file does not contain {name}"
            ) from error

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, details: dict, dirty: bool = False
    ) -> AsyncGenerator[Self, None]:
        self.__update(user, details, dirty, writing=False)

        logger.debug(f"Aquiring git reader lock for {self.repo.path}...")
        if not self.dirty or await self.__is_outdated():
            logger.debug(
                f"Upgrading lock to git writer lock to pull repo at {self.repo.path}!"
            )
            async with self.writer(user, details=details):
                pass

        # Downgrade to a reader lock
        async with self._reader_update_lock:
            while self._reader_count == -1:
                await self._no_readers.wait()
            logger.debug(f"... git reader lock for {self.repo.path} aquired!")
            self._reader_count += 1

        try:
            yield self
        except git.GitTimeoutError as error:
            raise RepoTimeoutError(str(error)) from error
        finally:
            # Release reader lock
            async with self._reader_update_lock:
                logger.debug(f"Releasing git reader lock for {self.repo.path}!")
                self._reader_count -= 1
                if self._reader_count == 0:
                    self._no_readers.notify_all()

    @asynccontextmanager
    async def writer(
        self, user: User | None, *, details: dict
    ) -> AsyncGenerator[Self, None]:
        self.__update(user, details, dirty=False, writing=True)

        logger.debug(f"Aquiring git writer lock for {self.repo.path}...")
        async with self._writer_lock:
            async with self._reader_update_lock:
                while self._reader_count != 0:
                    await self._no_readers.wait()
                logger.debug(f"... git writer lock for {self.repo.path} aquired!")
                self._reader_count = -1  # Indicate a writer is active
            await self.__pull()

            try:
                yield self
            except git.GitTimeoutError as error:
                raise RepoTimeoutError(str(error)) from error
            finally:
                async with self._reader_update_lock:
                    self._reader_count = 0
                    self._no_readers.notify_all()

    def update_details(self, details: dict) -> None:
        self.fpath = details.get("file", "")

    async def __is_outdated(self) -> bool:
        try:
            last_fetch = await self.repo.get_fetch_time()
        except git.GitError as error:
            logger.error(str(error))
            return True

        return (time.time() - last_fetch) > 60 * DIRTY_MAX

    async def __pull(self) -> None:
        try:
            if not self.repo.loaded:
                await self.repo.load()
            logger.debug(f"Pulling git repo at {self.path}")
            await self.repo.pull()
        except git.GitError:
            try:
                await rmtree(self.path)
            except FileNotFoundError:
                pass  # it may not be there yet
            except OSError as error:
                raise RepoError(f"Cannot delete {self.path}") from error
            logger.info(f"Cloning git repo to {self.path}")
            try:
                await self.repo.clone(URL, branch=BRANCH)
            except git.GitError as error:
                raise RepoError(f"Cannot clone repo to {self.path}: {error}") from error

    async def __push(self, files: list[str], msg: str):
        try:
            await self.repo.add(files)
            await self.repo.commit(f"[YAC] {msg}")
            logger.debug(f"Pushing new git commit from {self.path} to remote")
            await self.repo.push()
        except git.GitError as error:
            # Very unlikely scenario where someone pushes from a different
            # instance or directly to the repo in the millisecond between
            # pull and push. If this occurs more often then expected (~ never)
            # we can implement a retry mechanism here.
            await self.__cleanup()
            raise RepoError(
                f"Unable to commit and push changes from {self.path}"
            ) from error
        await self.__cleanup()

    async def __cleanup(self):
        if not await self.repo.is_dirty():
            return
        try:
            logger.debug(f"Cleaning git repo at {self.path}")
            await self.repo.reset(f"origin/{BRANCH}", hard=True)
            await self.repo.clean(recursive=True, force=True)
            assert not await self.repo.is_dirty()
        except (git.GitError, AssertionError):
            # Try a complete fresh clone before giving up
            await self.__pull()

    def __make_relative(self, path: str, path2: str) -> str:
        length = 0
        for i in range(min(len(path), len(path2))):
            if path[i] == path2[i]:
                length += 1
            else:
                break

        common = path[:length]
        last_dir = common.rfind("/") + 1
        relative = path[last_dir:]
        backpath = f"../" * relative.count("/")
        return f"{backpath}{relative}"

    async def __has_link(self, name: str) -> bool:
        file_name = "/".join([self.path, self.fpath.format(name=name)])
        async for root, _, files in Path(dirname(file_name)).walk():
            for file in files:
                file_path = Path(f"{root}/{file}")
                if await file_path.is_symlink():
                    link_target = await file_path.resolve()
                    if link_target != file_path:
                        if (
                            await Path(link_target).resolve()
                            == await Path(file_name).resolve()
                        ):
                            return True
        return False

    async def __read(self, file: str, *, absolute: bool = True) -> str:
        absfile = file if absolute else f"{self.path}/{file}"
        try:
            async with await open_file(absfile, "r", encoding="utf-8") as f:
                logger.debug(f"Reading file {absfile}")
                return await f.read()
        except FileNotFoundError as error:
            raise RepoNotFound(f"The file {file} does not exist") from error
        except OSError as error:
            raise RepoError(f"Could not read file {absfile}") from error

    async def get_hash(self) -> str:
        return await self.repo.get_hash()

    async def list(self) -> list[str]:
        glob = self.fpath.format(name="*")
        try:
            return sorted(
                [
                    parse(f"{self.path}/{self.fpath}", str(fn)).named["name"]  # type: ignore
                    async for fn in Path(self.path).glob(glob)
                ]
            )
        except (OSError, AttributeError, KeyError) as error:
            raise RepoError(f"Could not list files at {self.path}/{glob}") from error

    async def exists(self, name: str) -> bool:
        file = "/".join([self.path, self.fpath.format(name=name)])
        try:
            return await Path(file).exists()
        except OSError as error:
            raise RepoError(f"Could not read file {file}") from error

    async def is_link(self, name: str) -> bool:
        file = "/".join([self.path, self.fpath.format(name=name)])
        try:
            return await Path(file).is_symlink()
        except OSError as error:
            raise RepoError(f"Could not read file {file}") from error

    async def get_link(self, name: str) -> str:
        if not await self.is_link(name):
            raise RepoError(f"File {name} is not a link")

        base = str(await Path(self.path).resolve())
        src = "/".join([base, self.fpath.format(name=name)])
        dest = str(await Path(src).resolve())

        if not dest.startswith(base):
            raise RepoError(f"Link {src} has an illegal destination: {dest}")

        link = dest[(len(base) + 1) :]
        try:
            return parse(self.fpath, link).named["name"]  # type: ignore
        except (AttributeError, KeyError) as error:
            raise RepoError(f"Link {src} has an illegal destination: {dest}") from error

    async def get_specs(self, name: str) -> str:
        return await self.__read(name, absolute=False)

    async def get(self, name: str) -> str:
        file = "/".join([self.path, self.fpath.format(name=name)])
        return await self.__read(file, absolute=True)

    async def write(
        self, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        path = self.fpath.format(name=name)
        file = f"{self.path}/{path}"

        if await self.exists(name):
            content = await self.get(name)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if content == content_new:
                raise RepoClientError("Cannot write without changing anything")
            if await self.is_link(name):
                raise RepoClientError("Modifying links is not allowed")
        elif len(content_old) > 0:
            raise RepoConflict("The file has been deleted in the meantime")

        try:
            async with await open_file(file, "w+", encoding="utf-8") as f:
                logger.debug(f"Writing file {file}")
                await f.write(content_new)
        except OSError as error:
            raise RepoError(f"Could not write file {file}") from error

        await self.__push([file], msg)
        patch = "\n".join(
            unified_diff(
                content_old.split("\n"),
                content_new.split("\n"),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

        return Diff(name=name, hash=await self.get_hash(), patch=patch)

    async def write_rename(
        self, name_old: str, name_new: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        path_old = self.fpath.format(name=name_old)
        path_new = self.fpath.format(name=name_new)
        file_old = f"{self.path}/{path_old}"
        file_new = f"{self.path}/{path_new}"

        if name_old == name_new:
            raise RepoClientError("Cannot rename without chaning the name")
        if await self.exists(name_old):
            content = await self.get(name_old)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if await self.is_link(name_old):
                raise RepoClientError("Modifying links is not allowed")
        else:
            raise RepoConflict("The file has been deleted in the meantime")
        if await self.exists(name_new):
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

        await self.__push([file_old, file_new], msg)
        patch = "\n".join(
            unified_diff(
                content_old.split("\n"),
                content_new.split("\n"),
                fromfile=f"a/{path_old}",
                tofile=f"b/{path_new}",
                lineterm="",
            )
        )

        return Diff(name=name_new, hash=await self.get_hash(), patch=patch)

    async def copy(self, name_dest: str, name_src: str, msg: str) -> Diff:
        if await self.exists(name_dest):
            raise RepoClientError("The file already exists")

        path_dest = self.fpath.format(name=name_dest)
        file_src = "/".join([self.path, self.fpath.format(name=name_src)])
        file_dest = f"{self.path}/{path_dest}"

        content = await self.__read(file_src, absolute=True)

        try:
            async with await open_file(file_dest, "w+", encoding="utf-8") as f:
                logger.debug(f"Writing file {file_dest}")
                await f.write(content)
        except OSError as error:
            raise RepoError(f"Could not create file {file_dest}") from error

        await self.__push([file_dest], msg)
        patch = "\n".join(
            unified_diff(
                [],
                content.split("\n"),
                fromfile=f"a/{path_dest}",
                tofile=f"b/{path_dest}",
                lineterm="",
            )
        )

        return Diff(name=name_dest, hash=await self.get_hash(), patch=patch)

    async def link(self, name_link: str, name_src: str, msg: str) -> Diff:
        if not await self.exists(name_src):
            raise RepoNotFound("The file does not exist")

        path_link = self.fpath.format(name=name_link)
        link = f"{self.path}/{path_link}"
        src = "/".join([self.path, self.fpath.format(name=name_src)])

        try:
            await Path(link).symlink_to(self.__make_relative(src, link))
        except FileExistsError as error:
            raise RepoClientError("The file already exists") from error
        except OSError as error:
            raise RepoError(f"Could not create symlink {link}") from error

        await self.__push([link], msg)
        patch = "\n".join(
            unified_diff(
                [],
                name_src.split("\n"),
                fromfile=f"a/{path_link}",
                tofile=f"b/{path_link}",
                lineterm="",
            )
        )

        return Diff(name=name_link, hash=await self.get_hash(), patch=patch)

    async def delete(self, name: str, msg: str) -> None:
        if not await self.exists(name):
            raise RepoNotFound("The file does not exist")
        if await self.__has_link(name):
            raise RepoClientError("The file must not be deleted because it is linked")

        file = "/".join([self.path, self.fpath.format(name=name)])
        try:
            await Path(file).unlink()
        except OSError as error:
            raise RepoError(f"Could not delete file {file}") from error

        await self.__push([file], msg)


handler = GitRepo()
