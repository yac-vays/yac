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
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="arbitrary",
        type=type_name,
        name=entity_name,
        actions=[run],
        entity=None,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)

    await validator.test_all(op, s, old, new, perms)

    return await action.run(TypeActionHook.ARBITRARY, op, s)
