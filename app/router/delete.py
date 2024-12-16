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

    s = None if specs.in_repo() else await specs.read_from_file(op)

    async with repo.handler.reader(op.user, details={}) as rpo:
        if specs.in_repo():
            s = await specs.read_from_repo(rpo, op)
        if s is not None and s.type is not None:
            rpo.update_details(s.type.details)

        old, new = await repo.get_entities(rpo, op, s)

    validator.test_all(op, s, old, new)

    await action.run(TypeActionHook.DELETE_BEFORE, op, s)

    async with repo.handler.writer(op.user, details=s.type.details) as rpo:
        await rpo.delete(op.name, msg)

    await action.run(TypeActionHook.DELETE_AFTER, op, s)
