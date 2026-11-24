import logging
import asyncio

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.lib import links
from app.lib import log
from app.lib import perms
from app.lib import props
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.err import RepoError
from app.model.inp import PathLog
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryLimit
from app.model.inp import QuerySearch
from app.model.inp import QuerySkip
from app.model.out import DetailedEntity
from app.model.out import EntityList
from app.model.out import ListedEntity
from app.model.out import Log
from app.model.out import LogContent
from app.model.out import Type
from app.router.common import NO_TYPE
from app.router.common import load
from app.router.common import operation_request
from app.router.common import session

logger = logging.getLogger(__name__)
router = APIRouter()

# Bounded fan-out for the list endpoint. The per-entity work is a mix of
# async file I/O (via anyio's thread pool) and CPU-bound YAML/j2 work; a
# moderate width lets the I/O parts overlap without overwhelming the
# thread pool or starving other requests.
_LIST_CONCURRENCY = 32


@router.get(
    "/entity",
    summary="List all entity types",
)
async def get_types(
    request: Request,
    user: CurrentUser,
) -> list[Type]:
    """
    Lists all available entity types with their complete specifications.

    Type metadata is intentionally visible to every authenticated user,
    regardless of roles; entity data itself remains role-protected.
    """
    op = operation_request(request, user, "read", NO_TYPE)

    s = await specs.read(op)

    # Intentional: no role filtering here. Any authenticated user may see
    # the metadata of all types (the UI needs it to render the navigation);
    # access to the entities of a type is still enforced per operation.
    return [t.to_public() for t in s.types]


@router.get(
    "/entity/{type}",
    summary="List entities of a specific type",
)
async def list_entities(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    search: QuerySearch = "",
    skip: QuerySkip = 0,
    limit: QueryLimit = 100,
) -> EntityList:
    """
    Will collect some data about the (searched) entities of {type}. Entities
    for which the user does not hold the 'see' permission are filtered out.
    """
    op = operation_request(request, user, "read", type_name)

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = session(raw, s)
        hash = await rpo.get_hash()
        await validator.test_ls(op, s)

        # Pre-evaluate the user-only role tests once for this request so the
        # per-entity loop only renders the entity-dependent residuals.
        active_roles = await perms.get_active_role_set(op, s)
        # Build the per-request shared portion of role props once; each
        # gathered task shallow-extends it with its own old/new/name keys.
        base_props = props.get_roles_base(op, s.request)
        links_props = props.get_links_base(op, s.request)

        matches = [n for n in await rpo.list(type_name) if search in n]
        sem = asyncio.Semaphore(_LIST_CONCURRENCY)

        async def _process(entity_name: str) -> ListedEntity | None:
            async with sem:
                try:
                    old, entity_perms = await repo.get_entity_for_list(
                        hash, rpo, type_name, entity_name, s,
                        base_props, active_roles,
                    )
                except RepoError as error:
                    logger.warning(error)
                    return None
            if "see" not in entity_perms:
                return None
            entity = repo.to_listed_entity(old, entity_perms, s.type)
            entity.links = await links.render(s.type, old, links_props)
            return entity

        # Process in waves wide enough to saturate the semaphore so we can
        # stop early once the page is filled (preserves the original
        # `if (limit + skip) <= len(result): break` semantics under
        # bounded parallelism).
        target = limit + skip
        wave = max(target, _LIST_CONCURRENCY)
        result: list[ListedEntity] = []
        i = 0
        while i < len(matches) and len(result) < target:
            chunk = matches[i : i + wave]
            i += wave
            gathered = await asyncio.gather(*(_process(n) for n in chunk))
            for r in gathered:
                if r is not None:
                    result.append(r)

    return EntityList(hash=hash, list=result[skip : skip + limit])


@router.get(
    "/entity/{type}/{name}",
    summary="Get all data of a specific entity",
)
async def get_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
) -> DetailedEntity:
    """
    All data of a specific entity, including its raw YAML.
    """
    op = operation_request(request, user, "read", type_name, entity_name)

    ctx = await load(op)
    s, old = ctx.specs, ctx.old

    await validator.test_all(op, s, old, ctx.new, ctx.perms)

    entity = repo.to_detailed_entity(old, ctx.perms, ctx.hash, s.type)
    entity.links = await links.render(
        s.type, old, props.get_links_base(op, s.request)
    )
    return entity


@router.get(
    "/entity/{type}/{name}/yaml",
    summary="Get the raw YAML data of a specific entity",
    response_class=PlainTextResponse,
)
async def get_entity_yaml(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
):
    op = operation_request(request, user, "read", type_name, entity_name)

    ctx = await load(op)
    s, old = ctx.specs, ctx.old

    await validator.test_all(op, s, old, ctx.new, ctx.perms)

    return PlainTextResponse(content=old.yaml, media_type="application/yaml")


@router.get(
    "/entity/{type}/{name}/logs",
    summary="Get the logs of a specific entity",
)
async def get_entity_logs(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
) -> list[Log]:

    op = operation_request(request, user, "read", type_name, entity_name)

    ctx = await load(op, dirty=True)
    s, old = ctx.specs, ctx.old

    # Enforce the `see` permission and entity existence BEFORE fetching the
    # logs — log plugins may reach external systems and must not run (or be
    # orphaned) for requests that are not allowed to see the entity.
    await validator.test_all(op, s, old, ctx.new, ctx.perms)
    return await log.get(op, s, old.data)


@router.get(
    "/entity/{type}/{name}/logs/{log}/content",
    summary="Get the content document of a log of a specific entity",
)
async def get_entity_log_content(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    log_name: PathLog,
) -> LogContent:
    """
    The content document (e.g. a YAML file or a Markdown text) of a log whose
    entries have `has_content`. It is only fetched on demand (unlike the
    entries, which are polled), so it may be big.
    """

    op = operation_request(request, user, "read", type_name, entity_name)

    ctx = await load(op, dirty=True)
    s, old = ctx.specs, ctx.old

    # Same as for the entries: `see` and existence first, then the plugin.
    await validator.test_all(op, s, old, ctx.new, ctx.perms)
    return await log.content(op, s, log_name, old.data)
