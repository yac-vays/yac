from fastapi import APIRouter
from fastapi import Request
from fastapi import status

from app.lib import action
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathAction
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import User
from app.model.out import TypeActionHook

router = APIRouter()


@router.post(
    "/entity/{type}/{name}/run/{action}",
    summary="Run an arbitrary action on an entity",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=http_responses(),
)
async def run_action_on_entity(
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
    run: PathAction,
) -> None:
    """
    Will run the specified action on a specific entity.
    """

    op = OperationRequest(
        request=request,
        user=user,
        operation="arbitrary",
        type=type_name,
        name=entity_name,
        actions=[run],
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

    return await action.run(TypeActionHook.ARBITRARY, op, s)
