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

Before every non-dirty access the checkout is made identical to the remote
branch (fetch + hard reset + clean); it is only recloned when it is broken.

Remote outages: when the remote cannot be reached (or answers 5xx, e.g. 503
in maintenance), the local checkout is kept and reads are answered from it,
flagged as stale (see app.lib.staleness). Writes, and reads without any
checkout yet, fail with 503 (RepoUnavailable / RepoMaintenance). After a
failure the remote is left alone for REMOTE_BACKOFF_SECONDS.

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
import errno
import logging
import os
import time
import re

from aioshutil import rmtree
from anyio import Path, open_file, to_thread

from app.lib import git
from app.lib import j2
from app.lib import specs as _specs
from app.lib import staleness
from app.model.err import RepoClientError
from app.model.err import RepoConflict
from app.model.err import RepoPushRejected
from app.model.err import RepoMaintenance
from app.model.err import RepoUnavailable
from app.model.err import RepoError
from app.model.err import RepoNotFound
from app.model.err import RepoTimeoutError
from app.model.err import RepoSpecsError
from app.model.out import Diff
from app.model.out import User
from app.model.plg import CommitMsg
from app.model.plg import IRepo
from app.model.plg import IRepoSession
from app.model.plg import RepoState
from app.model.plg import IRepoUntyped


logger = logging.getLogger(__name__)
_CONN = _specs.get_repo_connection()
URL = _CONN.get("url", "")
BRANCH = _CONN.get("branch", "main")
KEY_FILE = _CONN.get("ssh_key_file", "/home/yac/.ssh/id_rsa")
KNOWN_HOSTS = _CONN.get("ssh_known_hosts_file", "/home/yac/.ssh/known_hosts")
DIRTY_MAX = int(_CONN.get("dirty_max_age", 0))

# After a failed remote operation, reads do not touch the remote again for
# this long: they answer from the local checkout right away (flagged as
# stale, see app.lib.staleness) or, without a checkout, fail fast with 503.
# Writes always retry, they cannot proceed without the remote. Keeps an
# outage from turning every read into a wait on a dead connection.
REMOTE_BACKOFF_SECONDS = 10
# Value of `GitRepo._reader_count` while a writer holds the lock.
_WRITER_ACTIVE = -1


# git's output for a push the remote refused because its branch has moved on.
_PUSH_REJECTED_RE = re.compile(r"\[rejected\]|non-fast-forward|fetch first")


def _patch(old: str | None, new: str, path_old: str, path_new: str) -> str:
    """The unified diff of a file; `old` is None for a file that did not exist."""
    return "\n".join(
        unified_diff(
            [] if old is None else old.split("\n"),
            new.split("\n"),
            fromfile=f"a/{path_old}",
            tofile=f"b/{path_new}",
            lineterm="",
        )
    )


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


def _name_from_path(glob_pat: str, path_rel: str) -> str | None:
    """
    The entity name of a repo-relative path that matches the type's glob
    (None if it does not). A name never spans directories.
    """
    start, end = glob_pat.split("*", maxsplit=1)
    match = re.fullmatch(
        rf"{re.escape(start)}([^/]+){re.escape(end)}", path_rel, flags=re.DOTALL
    )
    return match.group(1) if match else None


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

    async def is_link_target(self, type_name: str, name: str) -> bool:
        # pylint: disable-next=protected-access
        return await self._h._is_link_target(type_name, name, self._details)

    def _require_writing(self) -> None:
        if not self._writing:
            raise RepoError("Write operation outside writer scope")

    async def write(
        self, type: str, name: str, content_old: str, content_new: str, msg: CommitMsg
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
        msg: CommitMsg,
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
        self, type: str, name_dest: str, name_src: str, content_src: str, msg: CommitMsg
    ) -> Diff:
        self._require_writing()
        return await self._h._copy(
            self._user, self._details, type, name_dest, name_src, content_src, msg
        )

    async def link(
        self, type: str, name_link: str, name_src: str, content_src: str, msg: CommitMsg
    ) -> Diff:
        self._require_writing()
        return await self._h._link(
            self._user, self._details, type, name_link, name_src, content_src, msg
        )

    async def delete(
        self, type: str, name: str, content_old: str, msg: CommitMsg
    ) -> None:
        self._require_writing()
        await self._h._delete(
            self._user, self._details, type, name, content_old, msg
        )


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
        # Reader/writer locking: number of readers, or _WRITER_ACTIVE.
        self._reader_count: int = 0
        self._reader_update_lock: asyncio.Lock = asyncio.Lock()
        self._lock_changed: asyncio.Condition = asyncio.Condition(
            self._reader_update_lock
        )
        self._writer_lock: asyncio.Lock = asyncio.Lock()
        # A writer waits for the readers to leave: no new ones are let in.
        self._writer_waiting: bool = False
        # Whether a write has touched the working tree and not pushed it yet.
        self._tree_touched: bool = False
        # Monotonic start time of the last successful sync with the remote.
        self._sync_started: float | None = None
        # Monotonic time of the last failed remote operation.
        self._remote_failed_at: float | None = None
        # Remote-connection diagnostics, see `state()`.
        self._synced: float | None = None
        self._remote_error: str | None = None
        self._remote_failed: float | None = None
        self._remote_maintenance: bool = False

    def state(self) -> RepoState:
        return RepoState(
            synced=self._synced,
            error=self._remote_error,
            failed=self._remote_failed,
            maintenance=self._remote_maintenance,
        )

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, dirty: bool = False
    ) -> AsyncGenerator[_GitRepoUntyped, None]:
        logger.debug(f"Acquiring git reader lock for {self.path}...")
        if not dirty or not self._loaded or await self._is_outdated():
            await self._refresh_for_read(user)

        async with self._reader_update_lock:
            # Writer preference: dirty readers do not queue for the writer
            # lock, so a steady stream of them would keep a writer waiting
            # forever if they were still let in.
            while self._reader_count == _WRITER_ACTIVE or self._writer_waiting:
                await self._lock_changed.wait()
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
                    self._lock_changed.notify_all()

    @asynccontextmanager
    async def writer(
        self, user: User | None, *, synced_since: float | None = None
    ) -> AsyncGenerator[_GitRepoUntyped, None]:
        """
        Starts with a sync, unless one that started after `synced_since`
        (monotonic time) has already succeeded or failed on the remote:
        concurrent readers waiting for the lock then share one sync (or its
        failure, RepoUnavailable).
        """
        logger.debug(f"Acquiring git writer lock for {self.path}...")
        async with self._writer_lock:
            async with self._reader_update_lock:
                self._writer_waiting = True
                try:
                    while self._reader_count != 0:
                        await self._lock_changed.wait()
                except BaseException:
                    # Cancelled while waiting: let the held back readers in.
                    self._lock_changed.notify_all()
                    raise
                finally:
                    self._writer_waiting = False
                logger.debug(f"... git writer lock for {self.path} acquired!")
                self._reader_count = _WRITER_ACTIVE

            try:
                if (
                    synced_since is not None
                    and self._loaded
                    and self._remote_failed_since(synced_since)
                ):
                    # The sync this reader queued behind has failed on the
                    # remote: another attempt per waiting reader would only
                    # make each of them wait for the same timeout in turn.
                    raise self._unavailable()
                if (
                    synced_since is None
                    or not self._loaded
                    or self._sync_started is None
                    or self._sync_started < synced_since
                ):
                    started = time.monotonic()
                    await self._pull(user)
                    self._sync_started = started
                try:
                    yield _GitRepoUntyped(self, user, writing=True)
                except git.GitTimeoutError as error:
                    raise RepoTimeoutError(str(error)) from error
                finally:
                    if self._tree_touched:
                        # The write failed (or was cancelled) half-way.
                        # Dirty readers and the entity cache (keyed by the
                        # HEAD hash) rely on the tree being that commit, so
                        # restore it before anybody else gets the lock.
                        await self._finish(self._cleanup(user))
            finally:
                async with self._reader_update_lock:
                    self._reader_count = 0
                    self._lock_changed.notify_all()

    # ------------------------------------------------------------------ #
    # On-disk operations. Each method takes the parameters it needs; no
    # state is read off `self` other than the on-disk path and the loaded
    # flag.
    # ------------------------------------------------------------------ #

    async def _refresh_for_read(self, user: User | None) -> None:
        """
        Bring the checkout up to date before a read. If the remote is
        unavailable but a checkout exists, the read goes ahead on the last
        known state (marked stale for the response); only without any
        checkout does the outage surface as an error.
        """
        if self._loaded and self._in_backoff():
            staleness.mark_stale(self._synced)
            return
        logger.debug(f"Upgrading lock to git writer lock to pull repo at {self.path}!")
        asked = time.monotonic()
        try:
            async with self.writer(user, synced_since=asked):
                pass
        except RepoUnavailable:
            if not self._loaded:
                raise
            logger.warning(f"Serving last known state from {self.path}")
            staleness.mark_stale(self._synced)

    def _in_backoff(self) -> bool:
        return (
            self._remote_failed is not None
            and time.time() - self._remote_failed < REMOTE_BACKOFF_SECONDS
        )

    def _remote_failed_since(self, asked: float) -> bool:
        """
        Whether the remote is in backoff or has failed since `asked`
        (monotonic time).
        """
        return self._in_backoff() or (
            self._remote_failed_at is not None and self._remote_failed_at >= asked
        )

    @staticmethod
    async def _finish(coro) -> None:
        """
        Run `coro` to its end, even if this task is cancelled meanwhile
        (the cancellation is raised afterwards).
        """
        task = asyncio.ensure_future(coro)
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancelled = error
        if cancelled is not None:
            raise cancelled
        task.result()

    def _remote_failure(self, error: git.GitError) -> RepoUnavailable:
        """
        Record a failed remote operation and build the error to raise for
        it. The user-facing message is fixed; the git output only goes to
        the log (it may reveal the remote URL).
        """
        self._remote_error = str(error)
        self._remote_failed = time.time()
        self._remote_failed_at = time.monotonic()
        self._remote_maintenance = bool(getattr(error, "maintenance", False))
        logger.error(f"Remote git repository unavailable: {error}")
        return self._unavailable()

    def _unavailable(self) -> RepoUnavailable:
        """The error for the recorded remote failure (maintenance or not)."""
        if self._remote_maintenance:
            return RepoMaintenance(RepoMaintenance.default_message)
        return RepoUnavailable(RepoUnavailable.default_message)

    def _remote_success(self) -> None:
        self._synced = time.time()
        self._remote_error = None
        self._remote_failed = None
        self._remote_failed_at = None
        self._remote_maintenance = False

    async def _is_outdated(self) -> bool:
        gr = _make_git_repo(self.path, None)
        try:
            last_fetch = await gr.get_fetch_time()
        except git.GitError as error:
            logger.error(str(error))
            return True
        return (time.time() - last_fetch) > 60 * DIRTY_MAX

    async def _pull(self, user: User | None) -> None:
        """
        Make the checkout identical to the remote branch: fetch, then hard
        reset and clean. That never merges, so leftovers of an interrupted
        write (uncommitted files, an unpushed commit) and a rewound remote
        are healed on the way. Only a checkout that is itself broken is
        replaced by a fresh clone.
        """
        gr = _make_git_repo(self.path, user)
        if not self._loaded:
            try:
                await gr.load()
                self._loaded = True
            except git.GitError:
                await self._reclone(user)
                return
        try:
            logger.debug(f"Fetching git repo at {self.path}")
            await self._fetch(gr)
        except (git.GitRemoteError, git.GitTimeoutError) as error:
            # The remote is down or not answering: keep the checkout, it is
            # the best data we have (readers serve it, writers fail).
            raise self._remote_failure(error) from error
        except git.GitError as error:
            try:
                await gr.load()
            except git.GitError:
                logger.warning(f"Checkout {self.path} is broken ({error}), recloning")
                await self._reclone(user)
                return
            # The checkout is fine, so the fetch itself was refused (bad
            # credentials, ...): nothing a reclone could fix.
            logger.error(f"Fetch at {self.path} failed: {error}")
            raise RepoError(f"Cannot update the repo at {self.path}") from error
        try:
            await self._reset(gr)
        except git.GitError as error:
            logger.warning(f"Reset at {self.path} failed ({error}), recloning")
            await self._reclone(user)
            return
        self._remote_success()

    async def _fetch(self, gr: git.Repo) -> None:
        try:
            await gr.fetch()
        except (git.GitRemoteError, git.GitTimeoutError):
            raise
        except git.GitError as error:
            # Every process has its own checkout and this runs under its
            # writer lock: a lock file can only be left over from a killed
            # git (and would make every fetch fail, also after a restart).
            if not await to_thread.run_sync(self._remove_stale_locks):
                raise
            logger.warning(f"Removed stale git lock files at {self.path} ({error})")
            await gr.fetch()

    def _remove_stale_locks(self) -> bool:
        """Remove the `*.lock` files below `.git`; whether there were any."""
        found = False
        for directory, _, files in os.walk(f"{self.path}/.git"):
            for file in files:
                if file.endswith(".lock"):
                    try:
                        os.unlink(f"{directory}/{file}")
                        found = True
                    except OSError:
                        pass
        return found

    async def _reset(self, gr: git.Repo) -> None:
        """Drop everything that is not on the remote branch (as last fetched)."""
        # Every process has its own checkout and this runs under its writer
        # lock: an index lock can only be left over from a killed git.
        await Path(f"{self.path}/.git/index.lock").unlink(missing_ok=True)
        await gr.reset(f"origin/{BRANCH}", hard=True)
        await gr.clean(recursive=True, force=True, ignored=True)

    async def _reclone(self, user: User | None) -> None:
        """
        Replace the checkout by a fresh clone. The clone goes to a temporary
        directory and is only swapped in once complete: a failed clone leaves
        the old checkout (if any) untouched and no half-initialised one.
        """
        self._loaded = False
        if self._in_backoff():
            # No point in hammering a remote that just failed.
            raise self._unavailable()
        tmp = f"{self.path}.new"
        logger.info(f"Cloning git repo to {self.path}")
        try:
            await rmtree(tmp, ignore_errors=True)
            await _make_git_repo(tmp, user).clone(URL, branch=BRANCH)
        except (git.GitRemoteError, git.GitTimeoutError) as error:
            await rmtree(tmp, ignore_errors=True)
            raise self._remote_failure(error) from error
        except git.GitError as error:
            await rmtree(tmp, ignore_errors=True)
            raise RepoError(f"Cannot clone repo to {self.path}: {error}") from error
        try:
            await rmtree(self.path, ignore_errors=True)
            await Path(tmp).rename(self.path)
        except OSError as error:
            raise RepoError(f"Cannot replace {self.path}") from error
        self._loaded = True
        self._remote_success()

    async def _commit_and_push(
        self, user: User | None, files: list[str], subject: str, msg: CommitMsg
    ) -> None:
        """
        Commit and push `files`. The commit message is the `subject` describing
        the change (`Edited <path>`) plus the caller's `msg` (see `CommitMsg`).
        """
        gr = _make_git_repo(self.path, user)
        subject = f"[YAC] {subject}"
        if msg.note:
            subject = f"{subject} ({msg.note})"
        body = msg.body.replace("\r\n", "\n").strip()
        try:
            await gr.add(files)
            await gr.commit(f"{subject} ...\n\n{body}" if body else subject)
            logger.debug(f"Pushing new git commit from {self.path} to remote")
            await gr.push()
            self._tree_touched = False
        except git.GitRemoteError as error:
            await self._cleanup(user)
            raise self._remote_failure(error) from error
        except git.GitError as error:
            # The git output only lives in the chained exception, which the
            # error handler does not log -- so log it here. A timeout is
            # special: the push may well have reached the remote already
            # (the client process is killed, not the server-side update), so
            # the commit can be on the remote although the user got an error.
            if isinstance(error, git.GitTimeoutError):
                logger.error(
                    f"Commit/push from {self.path} timed out ({error}); the"
                    " push may have succeeded on the remote nevertheless"
                )
            else:
                logger.error(f"Commit/push from {self.path} failed: {error}")
            await self._cleanup(user)
            if _PUSH_REJECTED_RE.search(str(error)):
                raise RepoPushRejected(
                    "The repository has changed in the meantime"
                ) from error
            raise RepoError(
                f"Unable to commit and push changes from {self.path}"
            ) from error

    async def _cleanup(self, user: User | None) -> None:
        """
        Put the checkout back to the remote's state after a failed write:
        drop uncommitted changes AND any local commit that was not pushed
        (otherwise a commit the user got an error for would ride along with
        the next successful write).
        """
        self._tree_touched = False
        try:
            logger.debug(f"Cleaning git repo at {self.path}")
            await self._reset(_make_git_repo(self.path, user))
        except (git.GitError, OSError) as error:
            # Not to be served as it is: checked (and recloned) on next use.
            logger.error(f"Could not clean {self.path}: {error}")
            self._loaded = False

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

    async def _make_parents(self, file: str) -> None:
        """
        Create the missing parent directories of a file about to be written:
        git does not track empty directories, so the first entity of a type
        (or of a nested path) may have no directory to land in yet. Must be
        called after `_assert_inside_repo`, which pins the resolved parent
        inside the repository.
        """
        self._tree_touched = True
        try:
            await Path(dirname(file)).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RepoError(f"Could not create directory for {file}") from error

    async def _write_file(self, file: str, content: str) -> None:
        """Write a regular file, never through a symlink (O_NOFOLLOW)."""

        def write() -> None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
            with os.fdopen(os.open(file, flags, 0o666), "w", encoding="utf-8") as f:
                f.write(content)

        logger.debug(f"Writing file {file}")
        self._tree_touched = True
        try:
            await to_thread.run_sync(write)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise RepoClientError("Modifying links is not allowed") from error
            raise RepoError(f"Could not write file {file}") from error

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

    async def _is_link_target(self, type_name: str, name: str, details: dict) -> bool:
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
        prefix_len = len(self.path) + 1
        try:
            names = [
                _name_from_path(glob_pat, str(fn)[prefix_len:])
                async for fn in Path(self.path).glob(glob_pat)
            ]
        except OSError as error:
            raise RepoError(
                f"Could not list files at {self.path}/{glob_pat}"
            ) from error
        return sorted(name for name in names if name is not None)

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

        if not dest.startswith(base + "/"):
            raise RepoError(f"Link {src} has an illegal destination: {dest}")

        glob_pat = await _render_glob(type_name, details)
        target = _name_from_path(glob_pat, dest[(len(base) + 1) :])
        if target is None:
            raise RepoError(f"Link {src} has an illegal destination: {dest}")
        return target

    async def _get(self, type_name: str, name: str, details: dict) -> str:
        path = await self._file_path(type_name, name, details)
        # The data repository may contain symlinks (entity links, but also
        # whatever was pushed to it directly): never follow one -- of the
        # file or of a directory on the way to it -- out of the checkout.
        # (Plain realpath calls: this is the hot path of the entity list.)
        base = os.path.realpath(self.path)
        if not os.path.realpath(path).startswith(base + "/"):
            logger.warning(f"Resolved path escapes the repository: {path}")
            raise RepoError(f"The {type_name} {name} is not readable")
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
        msg: CommitMsg,
    ) -> Diff:
        path_rel = await _render_path(type_name, name, details)
        file_abs = f"{self.path}/{path_rel}"
        await self._assert_inside_repo(file_abs)

        # Also a dangling link, which does not "exist" but would be followed.
        if await self._is_link(type_name, name, details):
            raise RepoClientError("Modifying links is not allowed")
        exists = await self._exists(type_name, name, details)
        if exists:
            content = await self._get(type_name, name, details)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if content == content_new:
                raise RepoClientError("Cannot write without changing anything")
        elif len(content_old) > 0:
            raise RepoConflict("The file has been deleted in the meantime")

        await self._make_parents(file_abs)
        await self._write_file(file_abs, content_new)

        await self._commit_and_push(
            user, [file_abs], f"{'Edited' if exists else 'Created'} {path_rel}", msg
        )
        patch = _patch(
            content_old if exists else None, content_new, path_rel, path_rel
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
        msg: CommitMsg,
    ) -> Diff:
        path_old = await _render_path(type_name, name_old, details)
        path_new = await _render_path(type_name, name_new, details)
        file_old = f"{self.path}/{path_old}"
        file_new = f"{self.path}/{path_new}"
        await self._assert_inside_repo(file_old)
        await self._assert_inside_repo(file_new)

        if name_old == name_new:
            raise RepoClientError("Cannot rename without changing the name")
        if await self._exists(type_name, name_old, details):
            content = await self._get(type_name, name_old, details)
            if content != content_old:
                raise RepoConflict("The data has changed in the meantime")
            if await self._is_link(type_name, name_old, details):
                raise RepoClientError("Modifying links is not allowed")
            if await self._is_link_target(type_name, name_old, details):
                raise RepoClientError(
                    "The file must not be renamed because it is linked"
                )
        else:
            raise RepoConflict("The file has been deleted in the meantime")
        if await self._exists(type_name, name_new, details):
            raise RepoConflict("The file already exists")

        await self._make_parents(file_new)
        await self._write_file(file_new, content_new)

        self._tree_touched = True
        try:
            await Path(file_old).unlink()
        except OSError as error:
            raise RepoError(f"Could not delete file {file_old}") from error

        await self._commit_and_push(
            user,
            [file_old, file_new],
            f"Edited {path_new} (renamed from {name_old})",
            msg,
        )
        patch = _patch(content_old, content_new, path_old, path_new)

        return Diff(name=name_new, hash=await self._get_hash(), patch=patch)

    async def _copy(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name_dest: str,
        name_src: str,
        content_src: str,
        msg: CommitMsg,
    ) -> Diff:
        if await self._exists(type_name, name_dest, details):
            raise RepoConflict("The file already exists")

        path_dest = await _render_path(type_name, name_dest, details)
        file_src = await self._file_path(type_name, name_src, details)
        file_dest = f"{self.path}/{path_dest}"
        await self._assert_inside_repo(file_src)
        await self._assert_inside_repo(file_dest)

        # Optimistic pin like in `_delete`: the copy was validated (rights on
        # the source, limits) against `content_src` under a READER scope.
        content = await self._get(type_name, name_src, details)
        if content != content_src:
            raise RepoConflict("The data has changed in the meantime")

        await self._make_parents(file_dest)
        await self._write_file(file_dest, content)

        await self._commit_and_push(
            user,
            [file_dest],
            f"Created {path_dest} (copy of {name_src})",
            msg,
        )
        patch = _patch(None, content, path_dest, path_dest)

        return Diff(name=name_dest, hash=await self._get_hash(), patch=patch)

    async def _link(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name_link: str,
        name_src: str,
        content_src: str,
        msg: CommitMsg,
    ) -> Diff:
        if not await self._exists(type_name, name_src, details):
            raise RepoNotFound("The file does not exist")
        # Optimistic pin, see `_copy`.
        if await self._get(type_name, name_src, details) != content_src:
            raise RepoConflict("The data has changed in the meantime")

        path_link = await _render_path(type_name, name_link, details)
        link = f"{self.path}/{path_link}"
        src = await self._file_path(type_name, name_src, details)
        await self._assert_inside_repo(link)
        await self._assert_inside_repo(src)

        await self._make_parents(link)
        try:
            await Path(link).symlink_to(self._make_relative(src, link))
        except FileExistsError as error:
            raise RepoConflict("The file already exists") from error
        except OSError as error:
            raise RepoError(f"Could not create symlink {link}") from error

        await self._commit_and_push(
            user,
            [link],
            f"Created {path_link} (link to {name_src})",
            msg,
        )
        patch = _patch(None, name_src, path_link, path_link)

        return Diff(name=name_link, hash=await self._get_hash(), patch=patch)

    async def _delete(
        self,
        user: User | None,
        details: dict,
        type_name: str,
        name: str,
        content_old: str,
        msg: CommitMsg,
    ) -> None:
        if not await self._exists(type_name, name, details):
            raise RepoNotFound("The file does not exist")
        if await self._is_link_target(type_name, name, details):
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
        self._tree_touched = True
        try:
            await Path(file).unlink()
        except OSError as error:
            raise RepoError(f"Could not delete file {file}") from error

        await self._commit_and_push(
            user, [file], f"Deleted {os.path.relpath(file, self.path)}", msg
        )


handler = GitRepo()
