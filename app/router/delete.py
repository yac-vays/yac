from fastapi import APIRouter
from fastapi import Request
from fastapi import status

from app.lib import action
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryMsg
from app.model.inp import User
from app.model.out import TypeActionHook

router = APIRouter()


@router.delete(
    "/entity/{type}/{name}",
    summary="Delete an entity",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=http_responses(),
)
async def delete_entity(  # pylint: disable=too-many-arguments,dangerous-default-value
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
    msg: QueryMsg = "Delete",
    run: QueryActions = [],
) -> None:
    """
    Will delete an entity and possibly run actions.
    """

    op = OperationRequest(
        request=request,
        user=user,
        operation="delete",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=None,
    )

    async with repo.handler.reader(op.user, details={}) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)

    await validator.test_all(op, s, old, new)

    await action.run(TypeActionHook.DELETE_BEFORE, op, s)

    async with repo.handler.writer(
        op.user, details=s.type.details if s.type else {}
    ) as rpo:
        await rpo.delete(op.name or "", msg)

    await action.run(TypeActionHook.DELETE_AFTER, op, s)
