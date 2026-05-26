"""
Redis-backed repo plugin. Layered on top of `git_direct`: one pod pulls
from the remote per TTL window and publishes a snapshot of the working
tree into Redis; every other read across every pod is served from Redis
with no git I/O.

All YAC pods see the same state. Writes still go through `git_direct`
(pull-modify-push) and rebuild the snapshot on scope exit so the next
read sees the new commit immediately. External commits (pushed to git
outside YAC) are picked up after `max_age_seconds`.

Redis key layout:

  latest                 -> current snapshot's git hash
  synced                 -> unix timestamp of the last successful pull
  pull_lock              -> cross-pod stampede mutex (NX + EX)
  ready:{hash}           -> "1" once the snapshot is fully populated
  paths:{hash}           -> SET of repo-relative paths in the snapshot
  data:{hash}:{path}     -> "f:<content>" for files, "l:<rel_target>" for
                            symlinks (resolved at snapshot time)

The connection config is read from `repo.connection` in the specs file
at process startup; changes require a pod restart.

  redis_url:        URL of the Redis instance (or sentinel://... for HA).
                    default: '' -> required!
  max_age_seconds:  How long a snapshot may serve reads before a refresh
                    pull is triggered. Stale-but-ready snapshots are
                    served while the refresh runs.
                    default: 300
  grace_seconds:    How long old snapshot keys are kept after a swap so
                    in-flight readers can finish.
                    default: 60
  pull_lock_ttl:    TTL of the cross-pod stampede mutex. Must comfortably
                    exceed the worst-case git pull time.
                    default: 120

Details: same as the `git_direct` plugin (path templates per type).
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
import asyncio
import logging
import re
import time

from anyio import Path, open_file
import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.lib import plugin
from app.lib import specs as _specs
from app.model.err import RepoError
from app.model.err import RepoNotFound
from app.model.out import Diff
from app.model.out import User
from app.model.plg import IRepo
from app.model.plg import IRepoSession
from app.model.plg import IRepoUntyped


logger = logging.getLogger(__name__)
_CONN = _specs.get_repo_connection()
REDIS_URL = _CONN.get("redis_url", "")
MAX_AGE = int(_CONN.get("max_age_seconds", 300))
GRACE = int(_CONN.get("grace_seconds", 60))
PULL_LOCK_TTL = int(_CONN.get("pull_lock_ttl", 120))

_KEY_LATEST = "latest"
_KEY_SYNCED = "synced"
_KEY_PULL_LOCK = "pull_lock"

# `git_direct` is reused as the source-of-truth working tree on whichever
# pod ends up doing the pull. Resolved lazily because importing this
# module shouldn't force `git_direct` to import before its config is
# needed.
_git_direct = None


def _gd():
    global _git_direct
    if _git_direct is None:
        _git_direct = plugin.get_module("repo", "git_direct").handler
    return _git_direct


# Reuse git_direct's path helpers — keeps the rendering rules identical
# between the two plugins, including the glob cache.
def _gd_module():
    return plugin.get_module("repo", "git_direct")


async def _render_glob(type_name: str, details: dict) -> str:
    # pylint: disable=protected-access
    return await _gd_module()._render_glob(type_name, details)


async def _render_path(type_name: str, name: str, details: dict) -> str:
    # pylint: disable=protected-access
    return await _gd_module()._render_path(type_name, name, details)


class _GitRedisSession(IRepoSession):
    """
    Read-only session backed by a pinned Redis snapshot. Writes raise —
    the writer scope hands callers `git_direct`'s session directly, so a
    Redis-backed session is never used for writes.
    """

    def __init__(
        self,
        handler: "GitRedisRepo",
        client: "redis_async.Redis",
        snapshot_hash: str,
        details: dict,
    ) -> None:
        self._h = handler
        self._r = client
        self._hash = snapshot_hash
        self._details = dict(details)

    async def get_hash(self) -> str:
        return self._hash

    async def list(self, type: str) -> list[str]:
        glob_pat = await _render_glob(type, self._details)
        start, end = glob_pat.split("*", maxsplit=1)
        pattern = re.compile(rf"^{re.escape(start)}(.+){re.escape(end)}$")
        try:
            paths = await self._r.smembers(f"paths:{self._hash}")
        except RedisError as error:
            raise RepoError(f"Redis list failed for type {type}") from error
        names = []
        for p in paths:
            match = pattern.match(p)
            if match:
                names.append(match.group(1))
        return sorted(names)

    async def exists(self, type: str, name: str) -> bool:
        key = await self._data_key(type, name)
        try:
            return (await self._r.exists(key)) > 0
        except RedisError as error:
            raise RepoError(f"Redis exists failed for {type}/{name}") from error

    async def is_link(self, type: str, name: str) -> bool:
        value = await self._raw(type, name)
        return value is not None and value.startswith("l:")

    async def get_link(self, type: str, name: str) -> str:
        value = await self._raw(type, name)
        if value is None or not value.startswith("l:"):
            raise RepoError(f"File {name} is not a link")
        target_rel = value[2:]
        glob_pat = await _render_glob(type, self._details)
        start, end = glob_pat.split("*", maxsplit=1)
        match = re.match(rf"^{re.escape(start)}(.+){re.escape(end)}$", target_rel)
        if not match:
            raise RepoError(
                f"Link {name} target {target_rel} doesn't match type glob"
            )
        return match.group(1)

    async def get(self, type: str, name: str) -> str:
        value = await self._raw(type, name)
        if value is None:
            raise RepoNotFound(f"The file {name} does not exist")
        if value.startswith("f:"):
            return value[2:]
        if value.startswith("l:"):
            target_key = f"data:{self._hash}:{value[2:]}"
            try:
                target_value = await self._r.get(target_key)
            except RedisError as error:
                raise RepoError(
                    f"Redis follow-link failed for {type}/{name}"
                ) from error
            if target_value is None or not target_value.startswith("f:"):
                raise RepoNotFound(
                    f"Link target {value[2:]} for {name} not found in snapshot"
                )
            return target_value[2:]
        raise RepoError(f"Unrecognised value prefix for {type}/{name}")

    async def _data_key(self, type: str, name: str) -> str:
        rel = await _render_path(type, name, self._details)
        return f"data:{self._hash}:{rel}"

    async def _raw(self, type: str, name: str) -> str | None:
        key = await self._data_key(type, name)
        try:
            return await self._r.get(key)
        except RedisError as error:
            raise RepoError(f"Redis get failed for {type}/{name}") from error

    def _no_write(self) -> None:
        raise RepoError("Write operation outside writer scope")

    async def write(
        self, type: str, name: str, content_old: str, content_new: str, msg: str
    ) -> Diff:
        del type, name, content_old, content_new, msg
        self._no_write()

    async def write_rename(
        self,
        type: str,
        name_old: str,
        name_new: str,
        content_old: str,
        content_new: str,
        msg: str,
    ) -> Diff:
        del type, name_old, name_new, content_old, content_new, msg
        self._no_write()

    async def copy(self, type: str, name_dest: str, name_src: str, msg: str) -> Diff:
        del type, name_dest, name_src, msg
        self._no_write()

    async def link(self, type: str, name_link: str, name_src: str, msg: str) -> Diff:
        del type, name_link, name_src, msg
        self._no_write()

    async def delete(self, type: str, name: str, msg: str) -> None:
        del type, name, msg
        self._no_write()


class _GitRedisUntyped(IRepoUntyped):
    """
    Per-scope view yielded by `GitRedisRepo.reader`. Pins the snapshot
    hash chosen at scope entry; `session(details)` returns a read-only
    Redis-backed session bound to that hash.
    """

    def __init__(
        self,
        handler: "GitRedisRepo",
        client: "redis_async.Redis",
        snapshot_hash: str,
    ) -> None:
        self._h = handler
        self._r = client
        self._hash = snapshot_hash

    async def get_hash(self) -> str:
        return self._hash

    def session(self, details: dict) -> _GitRedisSession:
        return _GitRedisSession(self._h, self._r, self._hash, details)


class GitRedisRepo(IRepo):
    """
    Process-singleton handler. Owns the Redis connection pool and the
    cleanup-task registry. Writes delegate fully to `git_direct`; the
    snapshot is rebuilt after each writer scope.
    """

    def __init__(self) -> None:
        self._client: "redis_async.Redis | None" = None
        # In-flight grace-period cleanup tasks. Held only to keep
        # references alive (asyncio garbage-collects orphaned tasks).
        self._cleanups: set[asyncio.Task] = set()

    def _redis(self) -> "redis_async.Redis":
        if self._client is None:
            if not REDIS_URL:
                raise RepoError(
                    "git_redis: repo.connection.redis_url is not configured"
                )
            self._client = redis_async.from_url(
                REDIS_URL, decode_responses=True
            )
        return self._client

    @asynccontextmanager
    async def reader(
        self, user: User | None, *, dirty: bool = False
    ) -> AsyncGenerator[IRepoUntyped, None]:
        try:
            client = self._redis()
            snapshot_hash = await self._ensure_snapshot(user, client, dirty=dirty)
        except (RedisError, OSError, RepoError) as error:
            logger.warning(
                "git_redis: snapshot unavailable, falling back to git_direct (%s)",
                error,
            )
            async with _gd().reader(user, dirty=dirty) as rpo:
                yield rpo
            return
        yield _GitRedisUntyped(self, client, snapshot_hash)

    @asynccontextmanager
    async def writer(
        self, user: User | None
    ) -> AsyncGenerator[IRepoUntyped, None]:
        async with _gd().writer(user) as rpo:
            yield rpo
            # Snapshot rebuild is best-effort: a failure here leaves
            # Redis stale (next read will refresh via the normal TTL
            # path) but does NOT undo the successful push.
            try:
                client = self._redis()
                await self._rebuild_snapshot(client)
            except (RedisError, OSError, RepoError) as error:
                logger.warning(
                    "git_redis: snapshot refresh after write failed (%s)", error
                )

    async def _ensure_snapshot(
        self,
        user: User | None,
        client: "redis_async.Redis",
        *,
        dirty: bool,
    ) -> str:
        snapshot_hash = await self._usable_snapshot(client, dirty=dirty)
        if snapshot_hash is not None:
            return snapshot_hash

        claimed = await client.set(
            _KEY_PULL_LOCK, "1", nx=True, ex=PULL_LOCK_TTL
        )
        if claimed:
            try:
                async with _gd().writer(user):
                    return await self._rebuild_snapshot(client)
            finally:
                try:
                    await client.delete(_KEY_PULL_LOCK)
                except RedisError:
                    pass  # TTL will reap it

        # Another pod is refreshing — wait briefly for the new snapshot.
        deadline = time.monotonic() + PULL_LOCK_TTL
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            snapshot_hash = await self._usable_snapshot(client, dirty=dirty)
            if snapshot_hash is not None:
                return snapshot_hash
        raise RepoError("git_redis: snapshot refresh timed out")

    async def _usable_snapshot(
        self, client: "redis_async.Redis", *, dirty: bool
    ) -> str | None:
        latest = await client.get(_KEY_LATEST)
        if not latest:
            return None
        ready = await client.get(f"ready:{latest}")
        if not ready:
            return None
        if dirty:
            return latest
        synced = await client.get(_KEY_SYNCED)
        if synced and float(synced) + MAX_AGE > time.time():
            return latest
        return None

    async def _rebuild_snapshot(self, client: "redis_async.Redis") -> str:
        # Must be called inside a `git_direct` reader OR writer scope so
        # the on-disk working tree is fresh.
        repo_path = _gd().path
        new_hash = await self._read_git_head(repo_path)
        old_hash = await client.get(_KEY_LATEST)

        # Fast path: HEAD unchanged AND prior snapshot still valid. Just
        # bump `synced` so readers stop refreshing. Avoids the
        # delete-then-rewrite window on the in-use snapshot.
        if old_hash == new_hash and await client.get(f"ready:{new_hash}"):
            await client.set(_KEY_SYNCED, str(time.time()))
            return new_hash

        entries = await self._collect_entries(repo_path)

        pipe = client.pipeline(transaction=False)
        # Clear any partial snapshot left from a previous failed attempt.
        pipe.delete(f"ready:{new_hash}", f"paths:{new_hash}")
        for rel, value in entries:
            pipe.set(f"data:{new_hash}:{rel}", value)
        if entries:
            pipe.sadd(f"paths:{new_hash}", *[rel for rel, _ in entries])
        pipe.set(f"ready:{new_hash}", "1")
        pipe.set(_KEY_LATEST, new_hash)
        pipe.set(_KEY_SYNCED, str(time.time()))
        await pipe.execute()

        if old_hash and old_hash != new_hash:
            task = asyncio.create_task(
                self._cleanup_after(client, old_hash, GRACE)
            )
            self._cleanups.add(task)
            task.add_done_callback(self._cleanups.discard)

        logger.info(
            "git_redis: published snapshot %s (%d entries)",
            new_hash[:8],
            len(entries),
        )
        return new_hash

    @staticmethod
    async def _read_git_head(repo_path: str) -> str:
        # Avoid spawning git just to read HEAD: refs/heads/<branch> or
        # packed-refs is enough for the snapshot key. Fall back to the
        # full git module if the cheap path fails.
        head_file = Path(f"{repo_path}/.git/HEAD")
        try:
            head = (await head_file.read_text(encoding="utf-8")).strip()
            if head.startswith("ref: "):
                ref_path = Path(f"{repo_path}/.git/{head[5:]}")
                if await ref_path.exists():
                    return (await ref_path.read_text(encoding="utf-8")).strip()
            else:
                return head
        except OSError:
            pass
        # Fall back to git plumbing via the existing helper.
        from app.lib import git  # local import to avoid cycles at module load

        gr = git.Repo(path=repo_path, env={"LANG": "C"})
        return await gr.get_hash()

    @staticmethod
    async def _collect_entries(repo_path: str) -> list[tuple[str, str]]:
        base = await Path(repo_path).resolve()
        base_str = str(base)
        prefix_len = len(base_str) + 1
        git_dir = f"{base_str}/.git"
        entries: list[tuple[str, str]] = []
        async for candidate in base.rglob("*"):
            cstr = str(candidate)
            if cstr == git_dir or cstr.startswith(git_dir + "/"):
                continue
            try:
                is_link = await candidate.is_symlink()
                if is_link:
                    target = await candidate.resolve()
                    target_str = str(target)
                    if not (
                        target_str == base_str
                        or target_str.startswith(base_str + "/")
                    ):
                        logger.warning(
                            "git_redis: skipping out-of-repo symlink %s -> %s",
                            cstr,
                            target_str,
                        )
                        continue
                    rel = cstr[prefix_len:]
                    rel_target = target_str[prefix_len:] if target_str != base_str else ""
                    entries.append((rel, f"l:{rel_target}"))
                    continue
                if not await candidate.is_file():
                    continue
                rel = cstr[prefix_len:]
                try:
                    async with await open_file(cstr, "r", encoding="utf-8") as f:
                        content = await f.read()
                except UnicodeDecodeError:
                    logger.warning(
                        "git_redis: skipping non-UTF-8 file %s", cstr
                    )
                    continue
                entries.append((rel, f"f:{content}"))
            except OSError as error:
                logger.warning(
                    "git_redis: could not snapshot %s (%s)", cstr, error
                )
        return entries

    async def _cleanup_after(
        self, client: "redis_async.Redis", snapshot_hash: str, delay: int
    ) -> None:
        try:
            await asyncio.sleep(delay)
            batch: list[str] = []
            async for key in client.scan_iter(
                match=f"data:{snapshot_hash}:*", count=500
            ):
                batch.append(key)
                if len(batch) >= 500:
                    await client.delete(*batch)
                    batch = []
            if batch:
                await client.delete(*batch)
            await client.delete(
                f"paths:{snapshot_hash}", f"ready:{snapshot_hash}"
            )
            logger.info(
                "git_redis: cleaned up old snapshot %s", snapshot_hash[:8]
            )
        except (RedisError, OSError) as error:
            logger.warning(
                "git_redis: cleanup of snapshot %s failed (%s)",
                snapshot_hash[:8],
                error,
            )
        except asyncio.CancelledError:
            raise


handler = GitRedisRepo()
