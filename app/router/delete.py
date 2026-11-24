from fastapi import APIRouter
from fastapi import Request
from fastapi import Response
from fastapi import status

from app.lib import action
from app.lib import repo
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryMsg
from app.model.out import TypeActionHook
from app.model.plg import CommitMsg
from app.router.common import load
from app.router.common import operation_request
from app.router.common import session

router = APIRouter()


@router.delete(
    "/entity/{type}/{name}",
    summary="Delete an entity",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_entity(
    request: Request,
    response: Response,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    msg: QueryMsg = "",
    run: QueryActions = [],
) -> None:
    """
    Will delete an entity and possibly run actions.
    """

    op = operation_request(
        request, user, "delete", type_name, entity_name, actions=run
    )
    ctx = await load(op)
    s, old = ctx.specs, ctx.old

    await validator.test_all(op, s, old, ctx.new, ctx.perms)

    await action.run(TypeActionHook.DELETE_BEFORE, op, s)

    async with repo.handler.writer(op.user) as raw:
        rpo = session(raw, s)
        # `old.yaml` is what the validator derived the delete permission from
        # (roles can reference `old.data`) and what the DELETE_BEFORE hook was
        # templated with. The writer scope pulled since then; the plugin
        # compares and raises RepoConflict (409) if the entity changed, so a
        # delete never acts on stale authorization.
        await rpo.delete(type_name, op.name or "", old.yaml or "", CommitMsg(body=msg))

    await action.run_after(TypeActionHook.DELETE_AFTER, op, s, response)
