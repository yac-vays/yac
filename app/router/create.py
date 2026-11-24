from fastapi import APIRouter
from fastapi import Request
from fastapi import Response
from fastapi import status

from app.lib import action
from app.lib import limits
from app.lib import repo
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import NewEntity
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryForce
from app.model.inp import QueryMsg
from app.model.out import Diff
from app.model.out import TypeActionHook
from app.model.plg import CommitMsg
from app.router.common import load
from app.router.common import operation_request
from app.router.common import session

router = APIRouter()


@router.post(
    "/entity/{type}",
    summary="Create a new entity",
    status_code=status.HTTP_201_CREATED,
)
async def create_entity(
    request: Request,
    response: Response,
    user: CurrentUser,
    type_name: PathType,
    entity: NewEntity | CopyEntity | LinkEntity,
    msg: QueryMsg = "",
    run: QueryActions = [],
    force: QueryForce = False,
) -> Diff:
    """
    Will validate the given data, create the entity and, if configured and/or
    requested, run actions.

    With `force` (admin override, requires the "adm" permission), the entity is
    created even when its data fails schema validation; YAML syntax, name
    rules, permissions, conflicts and limits are still enforced.
    """
    op = operation_request(
        request, user, "create", type_name, actions=run, entity=entity, force=force
    )
    ctx = await load(op, with_limits=True)
    s, old = ctx.specs, ctx.old

    result = await validator.test_all(op, s, old, ctx.new, ctx.perms, ctx.usages)
    commit = CommitMsg(body=msg)
    if force and not result.schemas.valid:
        # Audit trail: the repo history must show that this commit was written
        # past a failing schema validation.
        commit.note = "admin override: schema validation bypassed"

    await action.run(TypeActionHook.CREATE_BEFORE, op, s)

    async with repo.handler.writer(op.user) as raw:
        rpo = session(raw, s)
        # The usages the validator checked were measured in the reader scope;
        # a concurrent create may have consumed the remaining quota since.
        # Measure and enforce again, serialized with the write.
        await limits.enforce(rpo, op, s, old, ctx.new_data)
        name = entity.name
        if name is None:
            # Copies/links inherit the source entity's data, so their name is
            # generated from it the same way as for new entities.
            namegen_data = (
                result.schemas.data
                if isinstance(entity, NewEntity)
                else old.data or {}
            )
            name = await repo.gen_name(
                op, s, await rpo.list(type_name), namegen_data
            )

        if isinstance(entity, CopyEntity):
            diff = await rpo.copy(type_name, name, entity.copy_name, commit)
        elif isinstance(entity, LinkEntity):
            diff = await rpo.link(type_name, name, entity.link_name, commit)
        else:
            diff = await rpo.write(type_name, name, "", entity.yaml, commit)

    # A generated name is only known now (`create:before` cannot have it).
    op = op.model_copy(update={"entity": entity.model_copy(update={"name": name})})
    await action.run_after(TypeActionHook.CREATE_AFTER, op, s, response)

    return diff
