"""
Raises: [app.model.err.RepoError, app.model.err.RepoSpecsError]
"""

import re

from app import consts
from app.lib import j2
from app.lib import perms
from app.lib import plugin
from app.lib import props
from app.lib import specs as _specs
from app.lib import yaml
from app.lib.cache import keyed_alru_cache
from app.model.err import RepoClientError
from app.model.err import RepoError
from app.model.err import RepoSpecsError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.out import DetailedEntity
from app.model.int import Entity
from app.model.plg import IRepo
from app.model.plg import IRepoSession
from app.model.spc import Specs
from app.model.spc import Type

repo_plugin = plugin.get_module("repo", _specs.get_repo_plugin())
handler: IRepo = repo_plugin.handler


@keyed_alru_cache(
    key_fn=lambda yaml_text: (hash(yaml_text), len(yaml_text)),
    maxsize=10000,
)
async def _parse_yaml_dict_cached(yaml_text: str) -> dict:
    """
    Content-keyed YAML->dict cache. Lives outside `__lookup_entities`
    (which is keyed on the repo hash) so commits do not force a re-parse
    of entities whose YAML content has not changed.
    """
    return yaml.load_as_dict_fast(yaml_text)


@keyed_alru_cache(
    key_fn=lambda repo_hash, type_name, old_name, new_name, type_exists, rpo: (
        repo_hash,
        type_name,
        old_name,
        new_name,
        type_exists,
    ),
    maxsize=10000,
)
async def __lookup_entities(
    repo_hash: str,
    type_name: str,
    old_name: str | None,
    new_name: str | None,
    type_exists: bool,
    rpo: IRepoSession,
) -> tuple[Entity, Entity]:
    del repo_hash  # only required to scope the cache to a repo state

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
            old.data = await _parse_yaml_dict_cached(old.yaml)
        except yaml.YAMLError as error:
            raise RepoError(
                f"Failed to parse YAML of {type_name} {old.name}: {error}"
            ) from error

    return old, new


async def load_data(rpo: IRepoSession, type_name: str, name: str) -> dict:
    """
    Best-effort load + parse of a single entity's YAML into a dict, reusing
    the content-keyed parse cache. Returns an empty dict on any read/parse
    error so callers that aggregate over many entities (e.g. `lib.limits`)
    never fail on a single malformed file.
    """
    try:
        yaml_text = await rpo.get(type_name, name)
    except RepoError:
        return {}
    if not yaml_text:
        return {}
    try:
        return await _parse_yaml_dict_cached(yaml_text)
    except yaml.YAMLError:
        return {}


async def get_entities(
    hash: str,
    rpo: IRepoSession,
    op: OperationRequest,
    specs: Specs,
    *,
    active_roles: list[perms.ActiveRole] | None = None,
) -> tuple[Entity, Entity, list[str]]:
    """
    Try to collect data about the entity refered in this OperationRequest.
    Should not fail even if the provided data is nonsense.

    The caller must pass an `IRepoSession` whose `details` already match
    `specs.repo.details` — typically obtained via
    `untyped.session(s.repo.details if s.type else {})`.

    `active_roles` may be passed by callers that iterate over many entities
    (e.g. the list endpoint) so the user-only role prefilter runs only once
    per request rather than once per entity.
    """

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
    p = await perms.get_from_roles(
        op, specs, old.data or {}, active_roles=active_roles
    )
    return old, new, p


async def get_entity_for_list(
    hash: str,
    rpo: IRepoSession,
    type_name: str,
    entity_name: str,
    specs: Specs,
    base_props: dict,
    active_roles: list[perms.ActiveRole],
) -> tuple[Entity, list[str]]:
    """
    List-endpoint specialisation of `get_entities`: assumes a read operation
    with no entity payload, skips the `op.model_copy` per entity, and uses
    the pre-built `base_props` + pre-filtered `active_roles` to compute
    perms without rebuilding the per-request props skeleton.
    """
    old, _ = await __lookup_entities(
        hash, type_name, entity_name, None, specs.type is not None, rpo
    )
    p = await perms.get_from_roles_for_entity(
        base_props, active_roles, entity_name, old.data or {}
    )
    return old, p


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
        name = await j2.render_print(s.type.name_generator, namegen_props)
    except j2.J2Error as error:
        raise RepoSpecsError(f"In types name_generator: {error}") from error

    if not re.fullmatch(consts.NAME_PATTERN, name) or not re.fullmatch(
        s.type.name_pattern, name
    ):
        raise RepoSpecsError(
            f"Generated name '{name}' does not match the type's name_pattern"
            f" '{s.type.name_pattern}'"
        )
    if name in old_list:
        raise RepoSpecsError(
            f"Generated name '{name}' already exists for type {op.type_name}"
        )

    return name
