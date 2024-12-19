from fastapi import APIRouter
from fastapi import Request
from fastapi import status

from app.lib import action
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model.err import ServerError
from app.model.err import http_responses
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import NewEntity
from app.model.inp import OperationRequest
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryMsg
from app.model.inp import User
from app.model.out import Diff
from app.model.out import TypeActionHook

router = APIRouter()


@router.post(
    "/entity/{type}",
    summary="Create a new entity",
    status_code=status.HTTP_201_CREATED,
    responses=http_responses(),
)
async def add_entity(  # pylint: disable=too-many-arguments,dangerous-default-value
    request: Request,
    user: User,
    type_name: PathType,
    entity: NewEntity | CopyEntity | LinkEntity,
    msg: QueryMsg = "Create",
    run: QueryActions = [],
) -> Diff:
    """
    Will validate the given data, create the entity and, if configured and/or
    requested, run actions.
    """
    op = OperationRequest(
        _request=request,
        user=user,
        operation="create",
        type_name=type_name,
        name=None,
        actions=run,
        entity=entity,
    )

    async with repo.handler.reader(op.user, details={}) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)

    result = validator.test_all(op, s, old, new)

    await action.run(TypeActionHook.CREATE_BEFORE, op, s)

    async with repo.handler.writer(
        op.user, details=s.type.details if s.type else {}
    ) as rpo:
        name = op.entity.name if op.entity else None
        if name is None:
            name = repo.gen_name(op, s, await rpo.list(), result.schemas.data)

        if isinstance(op.entity, CopyEntity):
            diff = await rpo.copy(name, op.entity.copy_name, msg)
        elif isinstance(op.entity, LinkEntity):
            diff = await rpo.link(name, op.entity.link_name, msg)
        elif isinstance(op.entity, NewEntity):
            diff = await rpo.write(name, "", op.entity.yaml, msg)
        else:
            raise ServerError("Cannot happen!")

    await action.run(TypeActionHook.CREATE_AFTER, op, s)

    return diff
