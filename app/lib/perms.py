"""
Raises: []
"""

import logging
from dataclasses import dataclass, field

from app.model import spc
from app.model import inp
from app.lib import j2
from app.lib import props

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
            type_name, set_name, perm = role_def.split(":", maxsplit=2)

            if type_name != op.type_name:
                continue

            # Set test
            if set_name == "all":
                set_test_residual: str | None = None
            else:
                set_test = getattr(specs.sets, type_name, {}).get(
                    set_name, "false"
                )
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


async def get_from_roles(
    op: inp.OperationRequest,
    specs: spc.Specs,
    old_data: dict,
    *,
    active_roles: list[ActiveRole] | None = None,
) -> list[str]:
    """
    Reads the role definitions from specs and renders them with given data
    and request context. If they match, the perms are returned if the role
    definition also matches (including set definition for sets).

    `active_roles` may be supplied by callers that iterate over many
    entities (e.g. the list endpoint) so the user-only role prefilter runs
    only once per request. When omitted, the prefilter is computed inline.
    """
    if active_roles is None:
        active_roles = await get_active_role_set(op, specs)

    role_props = props.get_roles(op, specs.request, old_data)
    perms: list[str] = []
    for ar in active_roles:
        if ar.set_test is not None:
            try:
                stest = await j2.render_test(ar.set_test, role_props)
            except j2.J2Error as error:
                logger.error(
                    f"Set in {ar.role_def} could not be rendered: {error}"
                )
                stest = False
            if not stest:
                continue

        if ar.role_test is not None:
            try:
                rtest = await j2.render_test(ar.role_test, role_props)
            except j2.J2Error as error:
                logger.error(
                    f"Role {ar.role_def} could not be rendered: {error}"
                )
                rtest = False
            if not rtest:
                continue

        perms.append(ar.perm)
    logger.debug(f'Extracted perms: {", ".join(perms)}')
    return __expand_perms(perms)
