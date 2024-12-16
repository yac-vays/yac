"""
A library to run non-blocking (async) git commands and kill them after a timeout.

Raises: [app.lib.git.GitError, app.lib.git.GitTimeoutError]
"""

import asyncio
import logging

from anyio import Path

logger = logging.getLogger(__name__)


class GitError(Exception):
    pass


class GitTimeoutError(GitError):
    pass


class Repo:

    def __init__(self, path: str, env: dict[str, str] = {}) -> None:
        self.loaded = False
        self.path = path
        self.env = env

    async def __run(self, *args: str, timeout: int) -> str:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            *args,
            env=self.env,
            cwd=self.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as error:
            proc.kill()
            raise GitTimeoutError(f"Timeout of {timeout} seconds exceeded") from error

        if proc.returncode != 0:
            raise GitError(
                f"Command git {' '.join(args)} failed with: {stderr.decode()}"
            )
        return stdout.decode()

    async def load(self) -> None:
        try:
            await self.__run("rev-parse", timeout=1)
        except FileNotFoundError as error:
            raise GitError(f"Directory {self.path} does not exist") from error
        self.loaded = True

    async def clone(
        self, url: str, *, depth: int = 1, branch: str = "main", timeout: int = 30
    ) -> None:
        try:
            await Path(self.path).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise GitError(f"Unable to create {self.path}: {error}")
        await self.__run(
            "clone",
            "--depth",
            str(depth),
            "--branch",
            branch,
            url,
            ".",
            timeout=timeout,
        )
        self.loaded = True

    async def pull(self, timeout: int = 5) -> None:
        await self.__run("pull", timeout=timeout)

    async def add(self, files: list[str]) -> None:
        await self.__run("add", *files, timeout=3)

    async def commit(self, msg: str) -> None:
        await self.__run("commit", "-m", msg, timeout=3)

    async def push(self, timeout: int = 5) -> None:
        await self.__run("push", timeout=timeout)

    async def is_dirty(self) -> bool:
        try:
            status = await self.__run("status", "--short", timeout=3)
            if len(status) == 0:
                return False
        except GitError:
            return True
        return True

    async def reset(self, branch: str, *, hard: bool = True) -> None:
        args = ["reset"]
        if hard:
            args.append("--hard")
        await self.__run(*args, timeout=3)

    async def clean(self, recursive: bool = True, force: bool = True) -> None:
        args = ["clean"]
        if recursive:
            args.append("-d")
        if force:
            args.append("-ff")
        await self.__run(*args, timeout=3)

    async def get_hash(self) -> str:
        return (await self.__run("rev-parse", "HEAD", timeout=3)).strip()

    async def get_fetch_time(self) -> float:
        file = f"{self.path}/.git/FETCH_HEAD"
        try:
            last_fetch = (await Path(file).stat()).st_mtime
        except FileNotFoundError:
            logger.debug(f"File {file} not found, so returning fetch time of 0")
            return 0
        except OSError as error:
            raise GitError(f"Error accessing file {file}") from error
        return last_fetch
