from fastapi import APIRouter
from fastapi import Request
from fastapi import status

from app.lib import action
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryMsg
from app.model.out import TypeActionHook

router = APIRouter()


@router.delete(
    "/entity/{type}/{name}",
    summary="Delete an entity",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=http_responses(),
)
async def delete_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    msg: QueryMsg = "Delete",
    run: QueryActions = [],
) -> None:
    """
    Will delete an entity and possibly run actions.
    """

    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="delete",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=None,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)

    await validator.test_all(op, s, old, new, perms)

    await action.run(TypeActionHook.DELETE_BEFORE, op, s)

    async with repo.handler.writer(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        # `old.yaml` is what the validator derived the delete permission from
        # (roles can reference `old.data`) and what the DELETE_BEFORE hook was
        # templated with. The writer scope pulled since then; the plugin
        # compares and raises RepoConflict (409) if the entity changed, so a
        # delete never acts on stale authorization.
        await rpo.delete(type_name, op.name or "", old.yaml or "", msg)

    await action.run(TypeActionHook.DELETE_AFTER, op, s)
