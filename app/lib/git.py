"""
A library to run non-blocking (async) git commands and kill them after a timeout.

Raises: [app.lib.git.GitError, app.lib.git.GitTimeoutError, app.lib.git.GitRemoteError]
"""

import asyncio
import logging
import re

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


class GitRemoteError(GitError):
    """
    A network operation (clone / pull / push) failed because the remote could
    not be reached or answered with a server error -- as opposed to a failure
    on our side (rejected push, bad credentials, corrupt checkout), which
    stays a plain GitError. `maintenance` is set when the server answered
    HTTP 503, the status git servers return while in maintenance.

    Only HTTP(S) remotes expose the server's status code; over SSH a
    maintenance window looks like any other failure to read from the remote.
    """

    def __init__(
        self, message: str, *, returncode: int | None = None, maintenance: bool = False
    ) -> None:
        super().__init__(message, returncode=returncode)
        self.maintenance = maintenance


# Patterns (in git's stderr, LANG=C) that identify a remote that is down or
# unreachable rather than a problem with our request. Kept deliberately
# narrow: an authentication failure or a rejected push must not look like an
# outage.
_MAINTENANCE_RE = re.compile(r"The requested URL returned error: 503\b")
_UNAVAILABLE_RE = re.compile(
    r"The requested URL returned error: 5\d\d\b"
    r"|Could not resolve host"
    r"|Failed to connect to"
    r"|Connection refused"
    r"|Connection timed out"
    r"|Connection reset by peer"
    r"|Network is unreachable"
    r"|Empty reply from server"
    r"|Recv failure"
    r"|ssh: connect to host .* port \d+:"
)

# Credentials embedded in remote URLs (https://user:token@host/...) show up
# verbatim in git's error output; strip them before the text reaches logs or
# exceptions.
_URL_CREDENTIALS_RE = re.compile(r"(://)[^/@\s]+@")


def redact_credentials(text: str) -> str:
    return _URL_CREDENTIALS_RE.sub(r"\1***@", text)


def classify_remote_failure(error: GitError) -> GitError:
    """
    Turn a GitError from a network command into a GitRemoteError if its
    output shows the remote was unreachable or answered 5xx; otherwise
    return the error unchanged. Timeouts are left alone: callers treat them
    specially (a timed-out push may well have landed).
    """
    if isinstance(error, (GitRemoteError, GitTimeoutError)):
        return error
    message = str(error)
    if _UNAVAILABLE_RE.search(message) is None:
        return error
    return GitRemoteError(
        message,
        returncode=error.returncode,
        maintenance=_MAINTENANCE_RE.search(message) is not None,
    )


# Network operations (clone / pull / push) share a generous timeout: a slow
# remote must not look like a failure. This matters twice for pull, which the
# git_direct plugin answers with a full reclone, and for push, whose
# server-side ref update usually completes even when the client is killed on
# timeout (the user then sees an error for a commit that did land). Local
# commands keep their short timeouts.
NETWORK_TIMEOUT = 30


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
                redact_credentials(
                    f"Command git {' '.join(args)} failed with: {stderr.decode()}"
                ),
                returncode=proc.returncode,
            )
        return stdout.decode()

    async def __run_remote(self, *args: str, timeout: int) -> str:
        """
        Like `__run`, for commands that talk to the remote: a failure caused
        by the remote being down surfaces as GitRemoteError.
        """
        try:
            return await self.__run(*args, timeout=timeout)
        except GitError as error:
            classified = classify_remote_failure(error)
            if classified is error:
                raise
            raise classified from error

    async def load(self) -> None:
        try:
            await self.__run("rev-parse", timeout=2)
        except FileNotFoundError as error:
            raise GitError(f"Directory {self.path} does not exist") from error
        self.loaded = True

    async def clone(
        self,
        url: str,
        *,
        depth: int = 1,
        branch: str = "main",
        timeout: int = NETWORK_TIMEOUT,
    ) -> None:
        try:
            await Path(self.path).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise GitError(f"Unable to create {self.path}: {error}") from error
        await self.__run_remote(
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

    async def pull(self, timeout: int = NETWORK_TIMEOUT) -> None:
        await self.__run_remote("pull", timeout=timeout)

    async def add(self, files: list[str]) -> None:
        await self.__run("add", *files, timeout=3)

    async def commit(self, msg: str) -> None:
        await self.__run("commit", "-m", msg, timeout=3)

    async def push(self, timeout: int = NETWORK_TIMEOUT) -> None:
        await self.__run_remote("push", timeout=timeout)

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

    async def get_hash(self, ref: str = "HEAD") -> str:
        return (await self.__run("rev-parse", ref, timeout=3)).strip()

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
