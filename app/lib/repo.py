"""
Raises: [app.model.err.RepoError, app.model.err.RepoSpecsError]
"""

from app import consts
from app.lib import j2
from app.lib import perms
from app.lib import plugin
from app.lib import props
from app.lib import yaml
from app.lib.cache import partial_alru_cache
from app.model.err import RepoClientError
from app.model.err import RepoError
from app.model.err import RepoSpecsError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.out import DetailedEntity
from app.model.int import Entity
from app.model.plg import IRepo
from app.model.spc import Specs
from app.model.spc import Type

repo_plugin = plugin.get_module("repo", consts.ENV.repo_plugin)
handler: IRepo = repo_plugin.handler


@partial_alru_cache("hash", "type_name", "old_name", "new_name", maxsize=10000)
async def __lookup_entities(
    hash: str,
    type_name: str,
    old_name: str | None,
    new_name: str | None,
    type_exists: bool,
    rpo: IRepo,
) -> tuple[Entity, Entity]:
    del hash  # only required to flush the cache on repo changes

    old = Entity(name=old_name)
    new = Entity(name=new_name)

    if type_exists:
        if old.name is not None:
            if await rpo.exists(type_name, old.name):
                old.exists = True
                old.is_link = await rpo.is_link(type_name, old.name)
                old.link = (
                    await rpo.get_link(type_name, old.name) if old.is_link else None
                )
                old.yaml = await rpo.get(type_name, old.name)
        if new.name is not None:
            if await rpo.exists(type_name, new.name):
                new.exists = True
                new.is_link = await rpo.is_link(type_name, new.name)

    if old.yaml is not None:
        try:
            old.data = yaml.load_as_dict(old.yaml, strict=False)
        except yaml.YAMLError as error:
            raise RepoError(
                f"Failed to parse YAML of {type_name} {old.name}: {error}"
            ) from error

    return old, new


async def get_entities(
    hash: str, rpo: IRepo, op: OperationRequest, specs: Specs
) -> tuple[Entity, Entity, list[str]]:
    """
    Try to collect data about the entity refered in this OperationRequest.
    Should not fail even if the provided data is nonsense.
    """

    await rpo.update_details(specs.repo.details)

    old_name = None
    new_name = None

    if op.operation == "create":
        new_name = None if op.entity is None else op.entity.name
        if op.entity and isinstance(op.entity, CopyEntity):
            old_name = op.entity.copy_name
        if op.entity and isinstance(op.entity, LinkEntity):
            old_name = op.entity.link_name
    elif op.operation == "change":
        old_name = op.name
        new_name = None if op.entity is None else op.entity.name
    else:  # read, delete, arbitrary
        old_name = op.name

    old, new = await __lookup_entities(
        hash, op.type_name, old_name, new_name, specs.type is not None, rpo
    )
    role_props = props.get_roles(op, specs.request, old.data or {})
    p = await perms.get_from_roles(op.type_name, specs, role_props)
    return old, new, p


def to_detailed_entity(
    entity: Entity, p: list[str], entity_hash: str, type_spec: Type | None
) -> DetailedEntity:
    options = {}
    if type_spec is not None:
        for o in type_spec.options:
            if o.name in (entity.data or {}) or o.default is not None:
                options[o.name] = (entity.data or {}).get(o.name, o.default)

    return DetailedEntity(
        name=entity.name or "",
        link=entity.link if entity.is_link else None,
        options=options,
        data=entity.data or {},
        yaml=entity.yaml,
        perms=p,
        hash=entity_hash,
    )


async def gen_name(
    op: OperationRequest, s: Specs, old_list: list[str], new_data: dict
) -> str:
    namegen_props = props.get_namegen(op, s.request, old_list, new_data)
    if s.type is None:
        raise RepoClientError("Type is not defined")
    try:
        return await j2.render_print(s.type.name_generator, namegen_props)
    except j2.J2Error as error:
        raise RepoSpecsError(f"In types name_generator: {error}") from error
