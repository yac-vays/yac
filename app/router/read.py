import logging
import asyncio

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.lib import log
from app.lib import perms
from app.lib import props as _props
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.err import RepoError
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryLimit
from app.model.inp import QuerySearch
from app.model.inp import QuerySkip
from app.model.out import DetailedEntity
from app.model.out import EntityList
from app.model.out import Log
from app.model.out import Type

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
    responses=http_responses(),
)
async def get_types(
    request: Request,
    user: CurrentUser,
) -> list[Type]:
    """
    Lists all available entity types with their complete specifications.
    """
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="read",
        type="does-not-exist",
        name=None,
        actions=[],
        entity=None,
    )

    s = await specs.read(op)

    # List comprehension dict hack is required because otherwise pydantic 2.7.4
    # returns the whole object instead of reducing it to the values of out.Type.
    return [t.model_dump() for t in s.types]  # type: ignore


@router.get(
    "/entity/{type}",
    summary="List entities of a specific type",
    responses=http_responses(),
)
async def get_entities(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    search: QuerySearch = "",
    skip: QuerySkip = 0,
    limit: QueryLimit = 100,
) -> EntityList:
    """
    Will collect some data about the (searched) entities of {type}. The `perm`
    option reduces the result to the entities where the user has all the defined
    permissions ('see' is required implicitly in any case).
    """
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="read",
        type=type_name,
        name=None,
        actions=[],
        entity=None,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        await validator.test_ls(op, s)

        # Pre-evaluate the user-only role tests once for this request so the
        # per-entity loop only renders the entity-dependent residuals.
        active_roles = await perms.get_active_role_set(op, s)
        # Build the per-request shared portion of role props once; each
        # gathered task shallow-extends it with its own old/new/name keys.
        base_props = _props.get_roles_base(op, s.request)

        matches = [n for n in await rpo.list(type_name) if search in n]
        sem = asyncio.Semaphore(_LIST_CONCURRENCY)

        async def _process(entity_name: str) -> DetailedEntity | None:
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
            return repo.to_detailed_entity(old, entity_perms, hash, s.type)

        # Process in waves wide enough to saturate the semaphore so we can
        # stop early once the page is filled (preserves the original
        # `if (limit + skip) <= len(result): break` semantics under
        # bounded parallelism).
        target = limit + skip
        wave = max(target, _LIST_CONCURRENCY)
        result: list[DetailedEntity] = []
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
    responses=http_responses(),
)
async def get_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
) -> DetailedEntity:
    """
    Lists all data of a specific entity including the raw YAML data and logs.
    """
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)
        entity_hash = await rpo.get_hash()

    await validator.test_all(op, s, old, new, perms)

    return repo.to_detailed_entity(old, perms, entity_hash, s.type)


@router.get(
    "/entity/{type}/{name}/yaml",
    summary="Get the raw YAML data of a specific entity",
    response_class=PlainTextResponse,
    responses=http_responses(),
)
async def get_entity_yaml(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
):
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)

    await validator.test_all(op, s, old, new, perms)

    return PlainTextResponse(content=old.yaml, media_type="application/yaml")


@router.get(
    "/entity/{type}/{name}/logs",
    summary="Get the logs of a specific entity",
    responses=http_responses(),
)
async def get_entity_logs(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
) -> list[Log]:

    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    s = await specs.read(op)
    # request the logs before/while validating the request to optimize performance
    logs = asyncio.create_task(log.get(op, s))
    # return control to the loop so the task can start immediately
    await asyncio.sleep(0)

    async with repo.handler.reader(op.user, dirty=True) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)

    await validator.test_all(op, s, old, new, perms)
    return await logs
