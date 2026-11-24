from typing import Callable

from fastapi import APIRouter
from fastapi import Request
from fastapi import Response

from app.lib import action
from app.lib import limits
from app.lib import repo
from app.lib import validator
from app.lib import yaml
from app.lib.auth import CurrentUser
from app.model.err import RepoError
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryForce
from app.model.inp import QueryMsg
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.int import Entity
from app.model.out import Diff
from app.model.out import TypeActionHook
from app.model.plg import CommitMsg
from app.router.common import load
from app.router.common import operation_request
from app.router.common import session

router = APIRouter()


async def _edit(
    op: OperationRequest,
    response: Response,
    msg: str,
    yamls: Callable[[Entity], tuple[str, str]],
) -> Diff:
    """
    The pipeline of both edit endpoints. `yamls` gives the (old, new) YAML
    to write for the stored entity; it is called after the `before` actions.
    """
    ctx = await load(op, with_limits=True)
    s, old = ctx.specs, ctx.old

    result = await validator.test_all(op, s, old, ctx.new, ctx.perms, ctx.usages)
    commit = CommitMsg(body=msg)
    if op.force and not result.schemas.valid:
        # Audit trail: the repo history must show that this commit was written
        # past a failing schema validation.
        commit.note = "admin override: schema validation bypassed"

    await action.run(TypeActionHook.EDIT_BEFORE, op, s)

    yaml_old, yaml_new = yamls(old)
    name_old = op.name or ""
    name_new = (op.entity.name if op.entity else None) or name_old

    async with repo.handler.writer(op.user) as raw:
        rpo = session(raw, s)
        # The usages the validator checked were measured in the reader scope;
        # a concurrent write may have consumed the remaining quota since.
        # Measure and enforce again, serialized with the write.
        await limits.enforce(rpo, op, s, old, ctx.new_data)
        if name_new == name_old:
            diff = await rpo.write(op.type_name, name_old, yaml_old, yaml_new, commit)
        else:
            diff = await rpo.write_rename(
                op.type_name, name_old, name_new, yaml_old, yaml_new, commit
            )

    await action.run_after(TypeActionHook.EDIT_AFTER, op, s, response)

    return diff


@router.put(
    "/entity/{type}/{name}",
    summary="Overwrite an existing entity",
)
async def replace_entity(
    request: Request,
    response: Response,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    entity: ReplaceEntity,
    msg: QueryMsg = "",
    run: QueryActions = [],
    force: QueryForce = False,
) -> Diff:
    """
    Will validate the given data, overwrite the existing entity and, if
    configured and/or requested, run actions.

    With `force` (admin override, requires the "adm" permission), the write is
    accepted even when the data fails schema validation; YAML syntax, name
    rules, permissions, conflicts and limits are still enforced.
    """
    op = operation_request(
        request,
        user,
        "edit",
        type_name,
        entity_name,
        actions=run,
        entity=entity,
        force=force,
    )
    return await _edit(
        op, response, msg, lambda old: (entity.yaml_old, entity.yaml_new)
    )


@router.patch(
    "/entity/{type}/{name}",
    summary="Edit some data of an existing entity",
)
async def patch_entity(
    request: Request,
    response: Response,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    entity: UpdateEntity,
    msg: QueryMsg = "",
    run: QueryActions = [],
) -> Diff:
    """
    Will validate the given data (in combination with the existing data
    for this entity), overwrite the existing entity with the changes and,
    if configured and/or requested, run actions.

    `yaml_base` is ignored here: the patch is always applied to (and
    validated against) the stored entity.
    """
    # `yaml_base` only exists for `/validate` (a client validating the YAML
    # it is editing). The write merges the patch into the STORED YAML, so the
    # validation, the limits and the write must all see exactly that -- not a
    # base document chosen by the client.
    if entity.yaml_base is not None:
        entity = entity.model_copy(update={"yaml_base": None})
    op = operation_request(
        request, user, "edit", type_name, entity_name, actions=run, entity=entity
    )

    def yamls(old: Entity) -> tuple[str, str]:
        try:
            yaml_new = yaml.update(old.yaml or "", entity.data)
        except yaml.YAMLError as error:
            raise RepoError(
                f"Failed to parse YAML of {op.type_name} {old.name}"
            ) from error
        yaml_old = (old.yaml or "") if entity.yaml_old is None else entity.yaml_old
        return yaml_old, yaml_new

    return await _edit(op, response, msg, yamls)
