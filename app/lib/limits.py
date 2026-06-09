"""
Raises: [app.model.err.RequestError]
"""

import asyncio

from app.lib import j2
from app.lib import props
from app.lib import repo as _repo
from app.model import out
from app.model.err import RequestError
from app.model.inp import OperationRequest
from app.model.int import Entity
from app.model.plg import IRepoSession
from app.model.spc import Specs
from app.model.spc import TypeLimit

# Bounded fan-out when a limit's scope/value needs each entity's data, mirroring
# the list endpoint: a moderate width overlaps the async file I/O without
# overwhelming the thread pool.
_SCAN_CONCURRENCY = 32


async def _contribution(
    lim: TypeLimit, base: dict, name: str, data: dict
) -> float:
    """
    The amount one entity (`old`) contributes to the limit's sum: 0 if it is
    outside the scope, else the rendered `value` (which defaults to 1, so an
    unset `value` counts entities).
    """
    entity_props = {**base, "old": {"name": name, "data": data}, "name": name}
    try:
        if not await j2.render_test(lim.scope, entity_props):
            return 0.0
        return await j2.render_number(lim.value, entity_props)
    except j2.J2Error as error:
        raise RequestError(
            f'Limit "{lim.title}" could not be evaluated: {error}'
        ) from error


async def _scan_existing(
    lim: TypeLimit,
    base: dict,
    type_name: str,
    rpo: IRepoSession,
    existing: list[str],
    needs_data: bool,
    edited: str | None,
    new_data: dict,
) -> float:
    """
    Sum the contributions of the existing entities. Entities whose data is not
    referenced by the limit are scored straight from their name (no YAML
    load); otherwise data is loaded with bounded concurrency.

    A symlink pointing at the entity being edited (`edited`) holds that entity's
    data, so once the edit is committed it will hold `new_data` -- not the stale
    copy on disk. Such entities are therefore scored with `new_data`, otherwise
    the new value escapes the limit through the link. This only matters when the
    limit actually reads the data (`needs_data`); a name-only limit is unaffected
    by an edit, so that fast path is left untouched.
    """
    if not needs_data:
        total = 0.0
        for name in existing:
            total += await _contribution(lim, base, name, {})
        return total

    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def _one(name: str) -> float:
        async with sem:
            data, link_target = await _repo.load_data_resolved(rpo, type_name, name)
        if edited is not None and link_target == edited:
            data = new_data
        return await _contribution(lim, base, name, data)

    return sum(await asyncio.gather(*(_one(n) for n in existing)))


async def measure(
    hash: str,
    rpo: IRepoSession,
    op: OperationRequest,
    s: Specs,
    old: Entity,
    new_data: dict,
) -> list[out.LimitUsage]:
    """
    Compute the usage of every `limits` rule that applies to this operation.

    The candidate set is every existing entity of the type (minus the entity
    being replaced, on `change`) plus the incoming entity — so create,
    rename, owner-change and quota-increase all fall out of one uniform scan.
    A rule whose `scope`/`value` only reference `old.name` (not `old.data`) is
    counted from the name list alone, without loading any YAML.

    Must be called inside an open repo reader scope. Returns the usages for
    display; enforcement (raising on `not ok`) happens in `lib.validator`.
    """
    del hash  # the live `rpo` already reflects the scoped repo state

    if s.type is None or not s.type.limits:
        return []

    applicable = [lim for lim in s.type.limits if op.operation in lim.on]
    if not applicable:
        return []

    new_name = op.entity.name if op.entity and op.entity.name else op.name
    base = props.get_limits_base(op, s.request, s.context)
    base["new"] = {"name": new_name, "data": new_data or {}}

    names = await rpo.list(op.type_name)
    # On change, the current version of the entity is replaced by the incoming
    # one, so it must not be double-counted.
    edited = old.name if op.operation == "change" else None
    existing = [n for n in names if not (edited is not None and n == edited)]

    usages: list[out.LimitUsage] = []
    for lim in applicable:
        needs_data = j2.uses_old_data(lim.scope) or j2.uses_old_data(lim.value)

        # The incoming entity always contributes (its data is `new_data`).
        total = await _contribution(lim, base, new_name or "", new_data or {})
        total += await _scan_existing(
            lim, base, op.type_name, rpo, existing, needs_data, edited, new_data or {}
        )

        cap_props = {**base, "old": base["new"], "name": new_name}
        try:
            cap = await j2.render_number(lim.max, cap_props)
        except j2.J2Error as error:
            raise RequestError(
                f'Limit "{lim.title}" cap is invalid: {error}'
            ) from error

        usages.append(
            out.LimitUsage(
                title=lim.title,
                used=total,
                max=cap,
                ok=total <= cap,
            )
        )

    return usages


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def assert_within(usages: list[out.LimitUsage]) -> None:
    """
    Raise a RequestError for the first exceeded limit. Pure (no I/O); called
    from `lib.validator.test_all` so the standard `raise_on_error` handling
    and the live `/validate` flow both apply.
    """
    for u in usages:
        if not u.ok:
            raise RequestError(
                f'Limit "{u.title}" reached: {_fmt(u.used)}/{_fmt(u.max)}.'
            )
