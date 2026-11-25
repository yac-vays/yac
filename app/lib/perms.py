"""
Raises: []
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

from app import consts
from app.model import spc
from app.model import inp
from app.lib import j2
from app.lib import props
from app.lib import specs as _specs
from app.lib.cache import keyed_alru_cache

logger = logging.getLogger(__name__)


_PERM_EXPANSIONS: dict[str, list[str]] = {
    "all": ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act"],
    "adm": ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act", "adm"],
    "cln": ["see", "cln"],
    "edt": ["see", "edt"],
    "lnk": ["see", "lnk"],
    "cpy": ["see", "cpy"],
    "rnm": ["see", "rnm"],
    "add": ["see", "add"],
}


def __expand_perms(p: list[str]) -> list[str]:
    parts: list[str] = []
    for q in p:
        parts.extend(q.split("+"))

    expanded: set[str] = set()
    for r in parts:
        expanded.update(_PERM_EXPANSIONS.get(r, [r]))
    return sorted(expanded)


@dataclass
class ActiveRole:
    """
    A role whose user-only sub-expressions have already been evaluated for
    this request.

    `set_test` / `role_test` are None when the corresponding expression was
    user-only and rendered True at prefilter time, so no per-entity work is
    needed for it. When non-None, the expression still depends on entity
    data and must be rendered per entity. `granted` is the expanded set of
    base perms this role would contribute if its remaining tests pass.
    """

    perm: str
    role_def: str
    set_test: str | None
    role_test: str | None
    granted: frozenset[str] = field(default_factory=frozenset)


async def get_active_role_set(
    op: inp.OperationRequest, specs: spc.Specs
) -> list[ActiveRole]:
    """
    Pre-evaluate every role's user-only sub-expressions once for this
    request. Roles whose set or role test does not reference any
    entity-dependent variable (`old`, `new`, `name`) yield the same result
    for every entity, so we evaluate them here and either drop the role
    entirely or carry only the residual entity-dependent halves through to
    `get_from_roles`.

    This is the structural optimization that makes single-entity lookups in
    repos with many roles cheap: roles that cannot possibly match the
    current user are filtered out once, not once per entity.
    """
    # User-only expressions, by definition, don't touch old.data — pass {}.
    role_props = props.get_roles(op, specs.request, {})

    active: list[ActiveRole] = []
    for role in specs.roles:
        for role_def, role_test in dict(role).items():
            logger.debug(f"Prefiltering role {role_def}")
            try:
                type_name, set_name, perm = role_def.split(":", maxsplit=2)
            except ValueError:
                # Shape is validated at startup (lib.specs); this is a
                # defensive guard so a malformed key can never 500 a request.
                logger.error(
                    f"Skipping malformed role key '{role_def}' (expected"
                    " '<type>:<set>:<perm>')"
                )
                continue

            if type_name != op.type_name:
                continue

            # Set test
            if set_name == "all":
                set_test_residual: str | None = None
            else:
                # `model_extra`, not getattr: a type may be named like a
                # pydantic attribute (copy, json, ...).
                type_sets = (specs.sets.model_extra or {}).get(type_name)
                if not isinstance(type_sets, dict):
                    type_sets = {}
                set_test = type_sets.get(set_name, "false")
                if j2.is_user_only(set_test):
                    try:
                        passed = await j2.render_test(set_test, role_props)
                    except j2.J2Error as error:
                        logger.error(
                            f"Set {op.type_name}.{set_name} (prefilter) could"
                            f" not be rendered: {error}"
                        )
                        # Fail safe: keep for per-entity eval; never drop a
                        # role just because the prefilter raised.
                        set_test_residual = set_test
                    else:
                        if not passed:
                            continue
                        set_test_residual = None
                else:
                    set_test_residual = set_test

            # Role test
            if j2.is_user_only(role_test):
                try:
                    passed = await j2.render_test(role_test, role_props)
                except j2.J2Error as error:
                    logger.error(
                        f"Role {role_def} (prefilter) could not be"
                        f" rendered: {error}"
                    )
                    role_test_residual: str | None = role_test
                else:
                    if not passed:
                        continue
                    role_test_residual = None
            else:
                role_test_residual = role_test

            active.append(
                ActiveRole(
                    perm=perm,
                    role_def=role_def,
                    set_test=set_test_residual,
                    role_test=role_test_residual,
                    granted=frozenset(__expand_perms([perm])),
                )
            )

    # Admin shortcut: any conditional role whose perms are already fully
    # covered by an unconditional role can be dropped — its per-entity test
    # cannot add anything new. For admins (who typically hold `adm` or `all`
    # unconditionally) this drops the whole per-entity role loop.
    unconditional: frozenset[str] = frozenset().union(
        *(ar.granted for ar in active if ar.set_test is None and ar.role_test is None)
    )
    if unconditional:
        active = [
            ar
            for ar in active
            if (ar.set_test is None and ar.role_test is None)
            or not ar.granted.issubset(unconditional)
        ]
    return active


async def _render_residuals(
    role_props: dict, active_roles: list[ActiveRole], failed: list[str] | None = None
) -> list[str]:
    """
    Inner loop shared between `get_from_roles` (single entity) and
    `get_from_roles_for_entity` (list hot path). Renders only the residual
    entity-dependent halves of each role and returns the matched base perms
    (still unexpanded).

    Perms only add up, so a role is not rendered at all if everything it
    grants is granted already (by the roles without tests or those that
    matched before it); a set that several roles refer to is tested once.

    A test that cannot be rendered counts as not matching; its role is added
    to `failed` (if given).
    """
    perms: list[str] = []
    granted: set[str] = set()
    for ar in active_roles:
        if ar.set_test is None and ar.role_test is None:
            granted |= ar.granted
    sets: dict[str, bool] = {}

    for ar in active_roles:
        if ar.set_test is None and ar.role_test is None:
            perms.append(ar.perm)
            continue
        if ar.granted <= granted:
            continue

        if ar.set_test is not None:
            if ar.set_test not in sets:
                try:
                    sets[ar.set_test] = await j2.render_test(ar.set_test, role_props)
                except j2.J2Error as error:
                    logger.error(
                        f"Set in {ar.role_def} could not be rendered: {error}"
                    )
                    sets[ar.set_test] = False
                    if failed is not None:
                        failed.append(ar.role_def)
            if not sets[ar.set_test]:
                continue

        if ar.role_test is not None:
            try:
                rtest = await j2.render_test(ar.role_test, role_props)
            except j2.J2Error as error:
                logger.error(
                    f"Role {ar.role_def} could not be rendered: {error}"
                )
                rtest = False
                if failed is not None:
                    failed.append(ar.role_def)
            if not rtest:
                continue

        perms.append(ar.perm)
        granted |= ar.granted
    return perms


async def get_from_roles(
    op: inp.OperationRequest,
    specs: spc.Specs,
    old_data: dict,
    *,
    active_roles: list[ActiveRole] | None = None,
    source_name: str | None = None,
) -> list[str]:
    """
    Reads the role definitions from specs and renders them with given data
    and request context. If they match, the perms are returned if the role
    definition also matches (including set definition for sets).

    `source_name` evaluates the roles for the stored entity of that name on
    its own (see `props.get_roles`) instead of for the request as a whole.

    `active_roles` may be supplied by callers that iterate over many
    entities (e.g. the list endpoint) so the user-only role prefilter runs
    only once per request. When omitted, the prefilter is computed inline.
    """
    if active_roles is None:
        active_roles = await get_active_role_set(op, specs)

    return await _match(
        active_roles,
        lambda: props.get_roles(
            op, specs.request, old_data, source_name=source_name
        ),
    )


async def _match(
    active_roles: list[ActiveRole],
    role_props: Callable[[], dict],
    failed: list[str] | None = None,
) -> list[str]:
    """
    The expanded perms of the matching roles. `role_props` is only called
    (and the residual tests only rendered) if a role depends on the entity.
    """
    if all(ar.set_test is None and ar.role_test is None for ar in active_roles):
        matched = [ar.perm for ar in active_roles]
    else:
        matched = await _render_residuals(role_props(), active_roles, failed)
    logger.debug(f'Extracted perms: {", ".join(matched)}')
    return __expand_perms(matched)


def list_signature(
    op: inp.OperationRequest, active_roles: list[ActiveRole]
) -> tuple | None:
    """
    What the perms on the entities of a list request depend on besides the
    entities themselves: the caller (as for the specs cache: user, stable
    token claims, IP, `yac-*` headers) and what is left to test of the roles
    after the per-request prefilter. Entities evaluated with an equal
    signature may share their result, see `get_from_roles_for_entity`.

    None if there is nothing to share: no role depends on the entity, or the
    reuse is turned off (`YAC_LIST_PERMS_TTL=0`).
    """
    if consts.ENV.list_perms_ttl <= 0 or all(
        ar.set_test is None and ar.role_test is None for ar in active_roles
    ):
        return None
    return (
        _specs.op_signature(op),
        tuple((ar.role_def, ar.set_test, ar.role_test) for ar in active_roles),
    )


class _Unreliable(Exception):
    """Perms that a failed role test may have reduced: not to be cached."""

    def __init__(self, perms: tuple[str, ...]):
        super().__init__()
        self.perms = perms


@keyed_alru_cache(
    key_fn=lambda signature, entity_name, yaml_text, *_: (
        signature,
        entity_name,
        yaml_text,
    ),
    maxsize=100_000,
    ttl=consts.ENV.list_perms_ttl,
)
async def _listed(
    signature: tuple,
    entity_name: str,
    yaml_text: str,
    base_props: dict,
    active_roles: list[ActiveRole],
    old_data: dict,
) -> tuple[str, ...]:
    del signature, yaml_text  # the cache key (with the name)
    failed: list[str] = []
    perms = tuple(
        await _match(
            active_roles,
            lambda: {
                **base_props,
                "old": {"name": entity_name, "data": old_data},
                "new": {"name": None},
                "name": entity_name,
            },
            failed,
        )
    )
    if failed:
        raise _Unreliable(perms)
    return perms


async def get_from_roles_for_entity(
    base_props: dict,
    active_roles: list[ActiveRole],
    entity_name: str,
    old_data: dict,
    *,
    signature: tuple | None = None,
    yaml_text: str = "",
) -> list[str]:
    """
    List-endpoint hot path. Builds the per-entity role-props dict by
    shallow-merging entity-specific keys onto `base_props` (built once per
    request via `props.get_roles_base()`) and renders only residual tests.

    `base_props` is read but not mutated, so this is safe to call
    concurrently from gathered tasks that share the same base.

    With a `signature` (see `list_signature`), the result is reused for
    `YAC_LIST_PERMS_TTL` seconds for the same caller and entity, as long as
    the entity's YAML (`yaml_text`, what `old_data` was parsed from) is the
    same: rendering the role tests is what a list of many entities spends
    most of its time on. A role test that looks beyond the request and the
    entity (the time, a lookup in a directory) is therefore up to that long
    behind in the list -- and only there, every operation on an entity
    evaluates the roles anew. A result with a role test that failed to render
    (e.g. a lookup that timed out) is not kept.
    """
    args = (signature, entity_name, yaml_text, base_props, active_roles, old_data)
    compute = _listed if signature is not None else _listed.__wrapped__
    try:
        return list(await compute(*args))
    except _Unreliable as unreliable:
        return list(unreliable.perms)
