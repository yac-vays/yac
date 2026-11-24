"""
Redis-backed repo plugin. Layered on top of `git_direct`: one pod syncs
with the remote per TTL window and publishes a snapshot of the working
tree into Redis; every other read across every pod is served from Redis
with no git I/O.

All YAC pods see the same state. Writes still go through `git_direct`
(sync-modify-push) and publish a new snapshot on scope exit so the next
read sees the new commit immediately. External commits (pushed to git
outside YAC) are picked up after `max_age_seconds`.

Redis key layout:

  latest         -> git hash of the current snapshot
  synced         -> unix timestamp of the last successful sync
  pull_lock      -> cross-pod refresh mutex (redis-py Lock)
  snap:{hash}    -> HASH of repo-relative path -> "f:<content>" for files,
                    "l:<rel_target>" for symlinks (resolved at snapshot
                    time), plus the empty field as "complete" marker

A snapshot is built under a temporary key and renamed into place, so a
`snap:{hash}` key that exists is complete. A replaced snapshot expires
after `grace_seconds`.

The connection config is read from `repo.connection` in the specs file
at process startup; changes require a pod restart.

  redis_url:        URL of the Redis instance (redis://, rediss:// or
                    unix://).
                    default: '' -> required!
  max_age_seconds:  How long a snapshot may serve reads before a refresh
                    is triggered. While one pod refreshes, the others keep
                    serving the snapshot there is.
                    default: 300
  grace_seconds:    How long a replaced snapshot is kept so in-flight
                    readers can finish.
                    default: 60
  pull_lock_ttl:    TTL of the refresh mutex. Should exceed the time a
                    refresh takes (waiting for local writers, the git
                    sync and the upload of the snapshot); a refresh that
                    takes longer is no longer exclusive.
                    default: 120

Dirty reads accept a snapshot of the age `max_age_seconds` or, if that is
longer, git_direct's `dirty_max_age`.

Details: same as the `git_direct` plugin (path templates per type).
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlsplit
import asyncio
import logging
import time
import uuid

from anyio import Path, open_file
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from redis.exceptions import WatchError

from app.lib import git
from app.lib import plugin
from app.lib import specs as _specs
from app.lib import staleness
from app.model.err import RepoError
from app.model.err import RepoNotFound
from app.model.err import RepoSpecsError
from app.model.err import RepoUnavailable
from app.model.out import User
from app.model.plg import IRepo
from app.model.plg import ReadOnlyRepoSession
from app.model.plg import RepoState
from app.model.plg import IRepoUntyped


logger = logging.getLogger(__name__)
_CONN = _specs.get_repo_connection()
REDIS_URL = _CONN.get("redis_url", "")
MAX_AGE = int(_CONN.get("max_age_seconds", 300))
GRACE = int(_CONN.get("grace_seconds", 60))
PULL_LOCK_TTL = int(_CONN.get("pull_lock_ttl", 120))



def _check_url(url: str) -> None:
    if url and urlsplit(url).scheme not in ("redis", "rediss", "unix"):
        raise RepoSpecsError(
            "git_redis: repo.connection.redis_url must be a redis://, rediss://"
            " or unix:// URL"
        )


_check_url(REDIS_URL)

_KEY_LATEST = "latest"
_KEY_SYNCED = "synced"
_KEY_PULL_LOCK = "pull_lock"
# Field of a snapshot HASH that is no path: keeps the key of an empty
# repository's snapshot in existence.
_MARKER = ""
# An abandoned temporary snapshot (pod killed while uploading) expires.
_TMP_TTL = 600
_UPLOAD_BATCH = 500


def _snap(snapshot_hash: str) -> str:
    return f"snap:{snapshot_hash}"


class _Superseded(Exception):
    """`latest` changed while a snapshot was being built."""

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


def _name_from_path(glob_pat: str, path_rel: str) -> str | None:
    # pylint: disable=protected-access
    return _gd_module()._name_from_path(glob_pat, path_rel)


async def _render_path(type_name: str, name: str, details: dict) -> str:
    # pylint: disable=protected-access
    return await _gd_module()._render_path(type_name, name, details)


class _GitRedisSession(ReadOnlyRepoSession):
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
        self._key = _snap(snapshot_hash)
        self._details = dict(details)

    async def get_hash(self) -> str:
        return self._hash

    async def list(self, type: str) -> list[str]:
        glob_pat = await _render_glob(type, self._details)
        try:
            paths = await self._r.hkeys(self._key)
        except RedisError as error:
            raise RepoError(f"Redis list failed for type {type}") from error
        names = (_name_from_path(glob_pat, p) for p in paths)
        return sorted(name for name in names if name is not None)

    async def exists(self, type: str, name: str) -> bool:
        rel = await _render_path(type, name, self._details)
        try:
            return bool(await self._r.hexists(self._key, rel))
        except RedisError as error:
            raise RepoError(f"Redis exists failed for {type}/{name}") from error

    async def is_link(self, type: str, name: str) -> bool:
        value = await self._raw(type, name)
        return value is not None and value.startswith("l:")

    async def _target_rel_to_name(self, type: str, name: str, target_rel: str) -> str:
        glob_pat = await _render_glob(type, self._details)
        target = _name_from_path(glob_pat, target_rel)
        if target is None:
            raise RepoError(
                f"Link {name} target {target_rel} doesn't match type glob"
            )
        return target

    async def get_link(self, type: str, name: str) -> str:
        value = await self._raw(type, name)
        if value is None or not value.startswith("l:"):
            raise RepoError(f"File {name} is not a link")
        return await self._target_rel_to_name(type, name, value[2:])

    async def is_link_target(self, type_name: str, name: str) -> bool:
        target = "l:" + await _render_path(type_name, name, self._details)
        names = [n for n in await self.list(type_name) if n != name]
        if not names:
            return False
        paths = [await _render_path(type_name, n, self._details) for n in names]
        try:
            return target in await self._r.hmget(self._key, paths)
        except RedisError as error:
            raise RepoError(
                f"Redis link scan failed for {type_name}/{name}"
            ) from error

    async def _link_content(self, type: str, name: str, target_rel: str) -> str:
        try:
            target_value = await self._r.hget(self._key, target_rel)
        except RedisError as error:
            raise RepoError(f"Redis follow-link failed for {type}/{name}") from error
        if target_value is None or not target_value.startswith("f:"):
            # Keep the repo-relative path server-side only.
            logger.info(f"Link target {target_rel} for {name} not found in snapshot")
            raise RepoNotFound(f"The link target of {name} does not exist")
        return target_value[2:]

    async def get_resolved(self, type: str, name: str) -> tuple[str, str | None]:
        # The value encodes link vs file (`l:`/`f:`), so the link target
        # comes for free -- no extra round-trip vs plain `get`.
        value = await self._raw(type, name)
        if value is None:
            raise RepoNotFound(f"The file {name} does not exist")
        if value.startswith("f:"):
            return value[2:], None
        if value.startswith("l:"):
            target_name = await self._target_rel_to_name(type, name, value[2:])
            return await self._link_content(type, name, value[2:]), target_name
        raise RepoError(f"Unrecognised value prefix for {type}/{name}")

    async def get(self, type: str, name: str) -> str:
        value = await self._raw(type, name)
        if value is None:
            raise RepoNotFound(f"The file {name} does not exist")
        if value.startswith("f:"):
            return value[2:]
        if value.startswith("l:"):
            return await self._link_content(type, name, value[2:])
        raise RepoError(f"Unrecognised value prefix for {type}/{name}")

    async def _raw(self, type: str, name: str) -> str | None:
        rel = await _render_path(type, name, self._details)
        try:
            return await self._r.hget(self._key, rel)
        except RedisError as error:
            raise RepoError(f"Redis get failed for {type}/{name}") from error


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
    Process-singleton handler. Owns the Redis connection pool. Writes
    delegate fully to `git_direct`; a new snapshot is published after each
    writer scope.
    """

    def __init__(self) -> None:
        self._client: "redis_async.Redis | None" = None
        # The shared `synced` timestamp as last seen by this pod.
        self._synced: float | None = None

    def _redis(self) -> "redis_async.Redis":
        if self._client is None:
            if not REDIS_URL:
                raise RepoError(
                    "git_redis: repo.connection.redis_url is not configured"
                )
            self._client = redis_async.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._client

    def state(self) -> RepoState:
        """
        The state of the data all pods serve: synced when any pod last
        published, failing only if this pod's own attempt failed since.
        """
        own = _gd().state()
        synced = max(
            (t for t in (own.synced, self._synced) if t is not None), default=None
        )
        if own.failed is not None and (synced is None or own.failed > synced):
            return RepoState(
                synced=synced,
                error=own.error,
                failed=own.failed,
                maintenance=own.maintenance,
            )
        return RepoState(synced=synced)

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
            # Publishing is best-effort: a failure here leaves Redis
            # outdated (the next read refreshes via the normal TTL path)
            # but does NOT undo the successful push.
            #
            # Deliberately NOT serialized with the cross-pod `pull_lock`:
            # taking it here (while holding the git_direct writer lock)
            # would invert the lock order of `_ensure_snapshot` (pull_lock
            # first, then the writer scope). Racing publishers are kept
            # apart by the compare-and-set on `latest` instead.
            try:
                await self._publish_snapshot(self._redis(), user)
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
        """
        The hash of the snapshot to read from: the published one while it
        is recent enough, otherwise a refreshed one. Only one pod at a time
        refreshes; the others keep serving the snapshot there is and only
        wait when there is none yet.
        """
        deadline = time.monotonic() + PULL_LOCK_TTL
        while True:
            latest, fresh = await self._published(client, dirty=dirty)
            if latest is not None and fresh:
                return latest
            lock = client.lock(_KEY_PULL_LOCK, timeout=PULL_LOCK_TTL, blocking=False)
            if await lock.acquire():
                try:
                    async with _gd().writer(user):
                        return await self._publish_snapshot(client, user)
                except RepoUnavailable as error:
                    # The remote is down: the published snapshot (if any)
                    # is the best data there is, so serve it as stale.
                    return await self._stale_snapshot(client, error)
                finally:
                    try:
                        await lock.release()
                    except RedisError:
                        pass  # expired (and maybe taken over) in the meantime
            if latest is not None:
                return latest
            if time.monotonic() >= deadline:
                raise RepoError("git_redis: snapshot refresh timed out")
            await asyncio.sleep(0.2)

    async def _stale_snapshot(
        self, client: "redis_async.Redis", error: RepoUnavailable
    ) -> str:
        """
        The hash of the published snapshot when a refresh failed because
        the remote is unavailable, marking the request as stale. Raises
        `error` when there is no snapshot to fall back on.
        """
        latest = await client.get(_KEY_LATEST)
        if not latest or not await client.exists(_snap(latest)):
            raise error
        synced = await client.get(_KEY_SYNCED)
        staleness.mark_stale(float(synced) if synced else None)
        logger.warning(
            "git_redis: remote unavailable, serving stale snapshot %s", latest[:8]
        )
        return latest

    async def _published(
        self, client: "redis_async.Redis", *, dirty: bool
    ) -> tuple[str | None, bool]:
        """
        The hash of the published snapshot (None if there is none) and
        whether it is recent enough for this read.
        """
        latest = await client.get(_KEY_LATEST)
        if not latest or not await client.exists(_snap(latest)):
            return None, False
        synced = await client.get(_KEY_SYNCED)
        if not synced:
            return latest, False
        self._synced = float(synced)
        max_age = MAX_AGE
        if dirty:
            max_age = max(MAX_AGE, 60 * _gd_module().DIRTY_MAX)
        return latest, self._synced + max_age > time.time()

    async def _publish_snapshot(
        self, client: "redis_async.Redis", user: User | None
    ) -> str:
        """
        Make the snapshot of git_direct's checkout the published one, unless
        a newer one is published already; returns the hash of the snapshot
        to read from. Must be called inside a `git_direct` writer scope.
        """
        synced_again = False
        for _ in range(5):
            local = await self._read_git_head(_gd().path)
            latest = await client.get(_KEY_LATEST)
            published = latest
            if published and not await client.exists(_snap(published)):
                published = None  # `latest` without its snapshot

            if published == local:
                await self._set_synced(client)
                return local

            if published:
                relation = await self._compare(_gd().path, local, published)
                if relation == "newer":
                    logger.info(
                        "git_redis: keeping published snapshot %s (newer than"
                        " local HEAD %s)",
                        published[:8],
                        local[:8],
                    )
                    return published
                if relation == "unknown" and not synced_again:
                    # Not in this clone: another pod may have pushed and
                    # published it after this pod last fetched. Fetch, then
                    # decide again.
                    synced_again = True
                    # pylint: disable-next=protected-access
                    await _gd()._pull(user)
                    continue

            try:
                await self._upload(client, local, latest)
            except _Superseded:
                continue
            return local
        raise RepoError("git_redis: could not publish a snapshot")

    async def _set_synced(self, client: "redis_async.Redis") -> None:
        self._synced = time.time()
        await client.set(_KEY_SYNCED, str(self._synced))

    async def _upload(
        self, client: "redis_async.Redis", new_hash: str, expected: str | None
    ) -> None:
        """
        Store the checkout as snapshot `new_hash` and switch `latest` to it,
        provided `latest` still is `expected` (else: _Superseded). Readers
        never see a partial snapshot: it is built under a temporary key and
        renamed into place in the same transaction that switches `latest`.
        """
        entries = await self._collect_entries(_gd().path)
        tmp = f"{_snap(new_hash)}:tmp:{uuid.uuid4().hex}"
        try:
            pipe = client.pipeline(transaction=False)
            pipe.hset(tmp, _MARKER, "")
            pipe.expire(tmp, _TMP_TTL)
            for i in range(0, len(entries), _UPLOAD_BATCH):
                pipe.hset(tmp, mapping=dict(entries[i : i + _UPLOAD_BATCH]))
            await pipe.execute()

            async with client.pipeline(transaction=True) as pipe:
                await pipe.watch(_KEY_LATEST)
                current = await pipe.get(_KEY_LATEST)
                if current not in (expected, new_hash):
                    raise _Superseded()
                now = time.time()
                pipe.multi()
                pipe.rename(tmp, _snap(new_hash))
                pipe.persist(_snap(new_hash))
                pipe.set(_KEY_LATEST, new_hash)
                pipe.set(_KEY_SYNCED, str(now))
                if current and current != new_hash:
                    # in-flight readers may still be pinned to it
                    pipe.expire(_snap(current), GRACE)
                try:
                    await pipe.execute()
                except WatchError as error:
                    raise _Superseded() from error
            self._synced = now
        except BaseException:
            try:
                await client.delete(tmp)
            except RedisError:
                pass  # expires by itself
            raise
        logger.info(
            "git_redis: published snapshot %s (%d entries)",
            new_hash[:8],
            len(entries),
        )

    @staticmethod
    async def _compare(repo_path: str, local: str, published: str) -> str:
        """
        How the published snapshot relates to the local HEAD: "newer" (the
        local HEAD is its ancestor), "unknown" (the commit is not in this
        pod's clone) or "other" (older or diverged).
        """
        gr = git.Repo(path=repo_path, env={"LANG": "C"})
        try:
            return "newer" if await gr.is_ancestor(local, published) else "other"
        except git.GitError:
            return "unknown"

    @staticmethod
    async def _read_git_head(repo_path: str) -> str:
        # Avoid spawning git just to read HEAD: the loose ref file is
        # enough. Fall back to git if there is none (packed refs).
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


handler = GitRedisRepo()
