"""
Raises: [app.model.err.RepoError, app.model.err.RepoSpecsError]
"""

from app import consts
from app.lib import j2
from app.lib import perms
from app.lib import plugin
from app.lib import props
from app.lib import yaml
from app.model.err import RepoClientError
from app.model.err import RepoError
from app.model.err import RepoSpecsError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.out import DetailedEntity
from app.model.out import User
from app.model.rpo import Entity
from app.model.rpo import Repo
from app.model.spc import Specs
from app.model.spc import Type

repo_plugin = plugin.get_module("repo", consts.ENV.repo_plugin)
handler: Repo = repo_plugin.handler


# TODO alru caching: from async_lru import alru_cache -> @alru_cache(maxsize=32, ttl=1)
async def get_entities(
    rpo: Repo, op: OperationRequest, specs: Specs
) -> tuple[Entity, Entity]:
    """
    Try to collect data about the entity refered in this OperationRequest.
    Should not fail even if the provided data is nonsense.
    """
    old = Entity()
    new = Entity()

    if op.operation == "create":
        new.name = None if op.entity is None else op.entity.name
        if op.entity and isinstance(op.entity, CopyEntity):
            old.name = op.entity.copy_name
        if op.entity and isinstance(op.entity, LinkEntity):
            old.name = op.entity.link_name
    elif op.operation == "change":
        old.name = op.name
        new.name = None if op.entity is None else op.entity.name
    else:  # read, delete, arbitrary
        old.name = op.name

    if specs.type is not None:
        if old.name is not None:
            if await rpo.exists(old.name):
                old.exists = True
                old.is_link = await rpo.is_link(old.name)
                old.link = await rpo.get_link(old.name) if old.is_link else None
                old.yaml = await rpo.get(old.name)
        if new.name is not None:
            if await rpo.exists(new.name):
                new.exists = True
                new.is_link = await rpo.is_link(new.name)

    if old.yaml is not None:
        try:
            old.data = yaml.load_as_dict(old.yaml, strict=False)
        except yaml.YAMLError as error:
            raise RepoError(
                f"Failed to parse YAML of {op.type_name} {old.name}: {error}"
            ) from error

    old.perms = perms.get_from_roles(op, specs, old.data or {}, new_name=False)
    # we only use old data to render the perms!
    new.perms = perms.get_from_roles(op, specs, old.data or {}, new_name=True)

    return old, new


def to_detailed_entity(
    entity: Entity, entity_hash: str, type_spec: Type
) -> DetailedEntity:
    options = {}
    for o in type_spec.options:
        if o.name in (entity.data or {}) or o.default is not None:
            options[o.name] = (entity.data or {}).get(o.name, o.default)

    return DetailedEntity(
        name=entity.name,
        link=entity.link if entity.is_link else None,
        options=options,
        data=entity.data,
        yaml=entity.yaml,
        perms=entity.perms,
        hash=entity_hash,
    )


def gen_name(
    op: OperationRequest, s: Specs, old_list: list[str], new_data: dict
) -> str:
    namegen_props = props.get_namegen(op, s.request, old_list, new_data)
    if s.type is None:
        raise RepoClientError("Type is not defined")
    try:
        return j2.render_str(
            f"{{{{ {s.type.name_generator} }}}}", namegen_props, allow_nonstr=False
        )
    except j2.J2Error as error:
        raise RepoSpecsError(f"In types name_generator: {error}") from error
