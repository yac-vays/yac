from fastapi import APIRouter
from fastapi import Request
from fastapi import status

from app.lib import action
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.inp import PathAction
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.out import TypeActionHook
from app.router.common import load
from app.router.common import operation_request

router = APIRouter()


@router.post(
    "/entity/{type}/{name}/run/{action}",
    summary="Run an arbitrary action on an entity",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def run_action_on_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    action_name: PathAction,
) -> None:
    """
    Will run the specified action on a specific entity.
    """

    op = operation_request(
        request, user, "arbitrary", type_name, entity_name, actions=[action_name]
    )
    ctx = await load(op)

    await validator.test_all(op, ctx.specs, ctx.old, ctx.new, ctx.perms)

    return await action.run(TypeActionHook.ARBITRARY, op, ctx.specs)
