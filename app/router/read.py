import logging

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.lib import log
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model.err import RepoError
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryLimit
from app.model.inp import QuerySearch
from app.model.inp import QuerySkip
from app.model.inp import User
from app.model.out import DetailedEntity
from app.model.out import EntityList
from app.model.out import Log
from app.model.out import Type

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/entity",
    summary="List all entity types",
    responses=http_responses(),
)
async def get_types(
    request: Request,
    user: User,
) -> list[Type]:
    """
    Lists all available entity types with their complete specifications.
    """
    op = OperationRequest(
        request=request,
        user=user,
        operation="read",
        type_name="does-not-exist",
        name=None,
        actions=[],
        entity=None,
    )

    async with repo.handler.reader(op.user, details={}, dirty=True) as rpo:
        s = await specs.read(op, rpo)

    # List comprehension dict hack is required because otherwise pydantic 2.7.4
    # returns the whole object instead of reducing it to the values of out.Type.
    return [t.model_dump() for t in s.types]  # type: ignore


@router.get(
    "/entity/{type}",
    summary="List entities of a specific type",
    responses=http_responses(),
)
async def get_entities(  # pylint: disable=too-many-arguments
    request: Request,
    user: User,
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
        request=request,
        user=user,
        operation="read",
        type=type_name,
        name=None,
        actions=[],
        entity=None,
    )

    result = []
    async with repo.handler.reader(op.user, details={}) as rpo:
        s = await specs.read(op, rpo)
        validator.test_ls(op, s)

        list_hash = await rpo.get_hash()
        for entity_name in await rpo.list():
            if not search in entity_name:
                continue  # skip the entities where search is not a substring

            op.name = entity_name
            try:
                old, _ = await repo.get_entities(rpo, op, s)
            except RepoError as error:
                logger.warning(error)
                continue  # skip the entities we have errors reading
            if "see" not in (old.perms or []):
                continue  # skip the entities we have no permissions

            result.append(repo.to_detailed_entity(old, list_hash, s.type))

            if (limit + skip) <= len(result):
                break

    return EntityList(hash=list_hash, list=result[skip:])


@router.get(
    "/entity/{type}/{name}",
    summary="Get all data of a specific entity",
    responses=http_responses(),
)
async def get_entity(
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
) -> DetailedEntity:
    """
    Lists all data of a specific entity including the raw YAML data and logs.
    """
    op = OperationRequest(
        request=request,
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    async with repo.handler.reader(op.user, details={}) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)
        entity_hash = await rpo.get_hash()

    validator.test_all(op, s, old, new)

    return repo.to_detailed_entity(old, entity_hash, s.type)


@router.get(
    "/entity/{type}/{name}/yaml",
    summary="Get the raw YAML data of a specific entity",
    response_class=PlainTextResponse,
    responses=http_responses(),
)
async def get_entity_yaml(
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
):
    op = OperationRequest(
        request=request,
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    async with repo.handler.reader(op.user, details={}) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)

    validator.test_all(op, s, old, new)

    return PlainTextResponse(content=old.yaml, media_type="application/yaml")


@router.get(
    "/entity/{type}/{name}/logs",
    summary="Get the logs of a specific entity",
    responses=http_responses(),
)
async def get_entity_logs(
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
) -> list[Log]:

    op = OperationRequest(
        request=request,
        user=user,
        operation="read",
        type=type_name,
        name=entity_name,
        actions=[],
        entity=None,
    )

    # TODO run the validation lazyly
    async with repo.handler.reader(op.user, details={}, dirty=True) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)

    validator.test_all(op, s, old, new)

    return await log.get(op, s)
