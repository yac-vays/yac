from fastapi import APIRouter
from fastapi import Request

from app.lib import action
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib import yaml
from app.model.err import RepoError
from app.model.err import http_responses
from app.model.inp import OperationRequest
from app.model.inp import PathName
from app.model.inp import PathType
from app.model.inp import QueryActions
from app.model.inp import QueryMsg
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.inp import User
from app.model.out import Diff
from app.model.out import TypeActionHook

router = APIRouter()


@router.put(
    "/entity/{type}/{name}",
    summary="Overwrite an existing entity",
    responses=http_responses(),
)
async def update_entity(  # pylint: disable=too-many-arguments,dangerous-default-value
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
    entity: ReplaceEntity,
    msg: QueryMsg = "Change",
    run: QueryActions = [],
) -> Diff:
    """
    Will validate the given data, overwrite the existing entity and, if
    configured and/or requested, run actions.
    """
    op = OperationRequest(
        request=request,
        user=user,
        operation="change",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=entity,
    )

    s = None if specs.in_repo() else await specs.read_from_file(op)

    async with repo.read(op.user, details={}) as rpo:
        if specs.in_repo():
            s = await specs.read_from_repo(rpo, op)
        if s is not None and s.type is not None:
            rpo.update_details(s.type.details)

        old, new = await repo.get_entities(rpo, op, s)

    validator.test_all(op, s, old, new)

    await action.run(TypeActionHook.CHANGE_BEFORE, op, s)

    async with repo.handler.writer(op.user, details=s.type.details) as rpo:
        if op.name != op.entity.name:
            diff = await rpo.write_rename(
                op.name, op.entity.name, op.entity.yaml_old, op.entity.yaml_new, msg
            )
        else:
            diff = await rpo.write(op.name, op.entity.yaml_old, op.entity.yaml_new, msg)

    await action.run(TypeActionHook.CHANGE_AFTER, op, s)

    return diff


@router.patch(
    "/entity/{type}/{name}",
    summary="Change some data of an existing entity",
    responses=http_responses(),
)
async def change_entity(  # pylint: disable=too-many-arguments,dangerous-default-value
    request: Request,
    user: User,
    type_name: PathType,
    entity_name: PathName,
    entity: UpdateEntity,
    msg: QueryMsg = "Change",
    run: QueryActions = [],
) -> Diff:
    """
    Will validate the given data (in combination with the existing data
    for this entity), overwrite the existing entity with the changes and,
    if configured and/or requested, run actions.
    """
    op = OperationRequest(
        request=request,
        user=user,
        operation="change",
        type=type_name,
        name=entity_name,
        actions=run,
        entity=entity,
    )

    s = None if specs.in_repo() else await specs.read_from_file(op)

    async with repo.handler.reader(op.user, details={}) as rpo:
        if specs.in_repo():
            s = await specs.read_from_repo(rpo, op)
        if s is not None and s.type is not None:
            rpo.update_details(s.type.details)

        old, new = await repo.get_entities(rpo, op, s)

    validator.test_all(op, s, old, new)

    await action.run(TypeActionHook.CHANGE_BEFORE, op, s)

    try:
        yaml_new = yaml.update(old.yaml, op.entity.data)
    except yaml.YAMLError as error:
        raise RepoError(f"Failed to parse YAML of {op.type_name} {old.name}") from error

    async with repo.handler.writer(op.user, details=s.type.details) as rpo:
        if op.name != op.entity.name:
            diff = await rpo.write_rename(
                op.name, op.entity.name, old.yaml, yaml_new, msg
            )
        else:
            diff = await rpo.write(op.name, old.yaml, yaml_new, msg)

    await action.run(TypeActionHook.CHANGE_AFTER, op, s)

    return diff
