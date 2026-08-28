from fastapi import APIRouter
from fastapi import Request

from app.lib import action
from app.lib import limits
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib import yaml
from app.lib.auth import CurrentUser
from app.model.err import RepoError
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryForce
from app.model.inp import QueryMsg
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.out import Diff
from app.model.out import TypeActionHook

router = APIRouter()


@router.put(
    "/entity/{type}/{name}",
    summary="Overwrite an existing entity",
    responses=http_responses(),
)
async def update_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    entity: ReplaceEntity,
    msg: QueryMsg = "Edit",
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
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="edit",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=entity,
        force=force,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)
        new_data, _ = validator.incoming_new_data(op, old)
        usages = await limits.measure(hash, rpo, op, s, old, new_data)

    result = await validator.test_all(op, s, old, new, perms, usages)
    if force and not result.schemas.valid:
        # Audit trail: the repo history must show that this commit was written
        # past a failing schema validation.
        msg = f"{msg} (admin override: schema validation bypassed)"

    await action.run(TypeActionHook.EDIT_BEFORE, op, s)

    async with repo.handler.writer(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        # The reader-scope measurement above produced the `usages` the
        # validator checked (and the UI displays), but a concurrent write
        # may have consumed the remaining quota since that scope closed.
        # Re-measure with the writer session and enforce again before
        # writing (enforcement only; `usages` stays the displayed value).
        await limits.enforce(rpo, op, s, old, new_data)
        if entity_name == entity.name:
            diff = await rpo.write(
                type_name, entity_name, entity.yaml_old, entity.yaml_new, msg
            )
        else:
            diff = await rpo.write_rename(
                type_name,
                entity_name,
                entity.name or entity_name,
                entity.yaml_old,
                entity.yaml_new,
                msg,
            )

    await action.run(TypeActionHook.EDIT_AFTER, op, s)

    return diff


@router.patch(
    "/entity/{type}/{name}",
    summary="Edit some data of an existing entity",
    responses=http_responses(),
)
async def change_entity(
    request: Request,
    user: CurrentUser,
    type_name: PathType,
    entity_name: PathName,
    entity: UpdateEntity,
    msg: QueryMsg = "Edit",
    run: QueryActions = [],
) -> Diff:
    """
    Will validate the given data (in combination with the existing data
    for this entity), overwrite the existing entity with the changes and,
    if configured and/or requested, run actions.
    """
    op = OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation="edit",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=entity,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)
        new_data, _ = validator.incoming_new_data(op, old)
        usages = await limits.measure(hash, rpo, op, s, old, new_data)

    await validator.test_all(op, s, old, new, perms, usages)

    await action.run(TypeActionHook.EDIT_BEFORE, op, s)

    try:
        yaml_new = yaml.update(old.yaml or "", entity.data)
    except yaml.YAMLError as error:
        raise RepoError(f"Failed to parse YAML of {op.type_name} {old.name}") from error

    yaml_old = (old.yaml or "") if entity.yaml_old is None else entity.yaml_old

    async with repo.handler.writer(op.user) as raw:
        rpo = raw.session(s.repo.details if s.type else {})
        # Same TOCTOU guard as in `update_entity`: re-enforce the limits
        # with the writer session right before writing.
        await limits.enforce(rpo, op, s, old, new_data)
        if entity_name == entity.name:
            diff = await rpo.write(type_name, entity_name, yaml_old, yaml_new, msg)

        else:
            diff = await rpo.write_rename(
                type_name,
                entity_name,
                entity.name or entity_name,
                yaml_old,
                yaml_new,
                msg,
            )

    await action.run(TypeActionHook.EDIT_AFTER, op, s)

    return diff
