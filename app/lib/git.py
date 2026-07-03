"""
A library to run non-blocking (async) git commands and kill them after a timeout.

Raises: [app.lib.git.GitError, app.lib.git.GitTimeoutError]
"""

import asyncio
import logging

from anyio import Path

logger = logging.getLogger(__name__)


class GitError(Exception):
    """
    Raised for any failing git invocation. `returncode` carries the exit
    status when the failure was a non-zero exit (None for other failures,
    e.g. timeouts), so callers can tell apart commands that answer via the
    exit status (like `merge-base --is-ancestor`) from real errors.
    """

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class GitTimeoutError(GitError):
    pass


class Repo:

    def __init__(self, path: str, env: dict[str, str]) -> None:
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
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # it finished in the meantime!
            raise GitTimeoutError(f"Timeout of {timeout} seconds exceeded") from error
        except Exception as error:
            raise GitError(f"Git command failed with: {error}") from error

        if proc.returncode != 0:
            raise GitError(
                f"Command git {' '.join(args)} failed with: {stderr.decode()}",
                returncode=proc.returncode,
            )
        return stdout.decode()

    async def load(self) -> None:
        try:
            await self.__run("rev-parse", timeout=2)
        except FileNotFoundError as error:
            raise GitError(f"Directory {self.path} does not exist") from error
        self.loaded = True

    async def clone(
        self, url: str, *, depth: int = 1, branch: str = "main", timeout: int = 30
    ) -> None:
        try:
            await Path(self.path).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise GitError(f"Unable to create {self.path}: {error}") from error
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
        args = ["reset", branch]
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

    async def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """
        Whether commit `ancestor` is an ancestor of (or equal to) commit
        `descendant`. `git merge-base --is-ancestor` answers via its exit
        status: 0 means yes, 1 means no, anything else (e.g. a commit
        unknown to the local clone) is a real error and raises GitError.
        """
        try:
            await self.__run(
                "merge-base", "--is-ancestor", ancestor, descendant, timeout=3
            )
        except GitError as error:
            if error.returncode == 1:
                return False
            raise
        return True

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
