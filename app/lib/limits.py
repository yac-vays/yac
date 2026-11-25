"""
Raises: [app.model.err.RequestError]
"""

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

# Decimal places kept of a limit sum (see measure).
_SUM_DECIMALS = 9


async def _contribution(
    lim: TypeLimit,
    base: dict,
    name: str,
    data: dict,
    scopes: dict[str, bool] | None = None,
) -> float:
    """
    The amount one entity (`old`) contributes to the limit's sum: 0 if it is
    outside the scope, else the rendered `value` (which defaults to 1, so an
    unset `value` counts entities).

    `scopes` holds what the scopes tested for this entity so far rendered to:
    limits tend to share their scope (e.g. "of the same owner"), which then is
    tested once per entity instead of once per entity and limit.

    A `value` that cannot be coerced to a number -- typically because the
    entity's data is schema-invalid (e.g. a non-number where a number is
    expected) -- contributes 0 rather than aborting the whole limit. The data is
    rejected by schema validation anyway, so this only lets the validate endpoint
    report that real error instead of failing on the limit first.
    """
    entity_props = {**base, "old": {"name": name, "data": data}, "name": name}
    if scopes is None:
        scopes = {}
    try:
        if lim.scope not in scopes:
            scopes[lim.scope] = await j2.render_test(lim.scope, entity_props)
        if not scopes[lim.scope]:
            return 0.0
        return await j2.render_number(lim.value, entity_props, default=0.0)
    except j2.J2Error as error:
        raise RequestError(
            f'Limit "{lim.title}" could not be evaluated: {error}'
        ) from error


def _existing_data(
    existing: list[str],
    stored: dict[str, tuple[str, str | None]],
    edited: str | None,
    new_data: dict,
) -> dict[str, dict]:
    """
    The data of the existing entities, from what the repo holds of them
    (`stored`, see `IRepoSession.get_all`). An entity that could not be read
    counts with empty data.

    A symlink pointing at the entity being edited (`edited`) holds that entity's
    data, so once the edit is committed it will hold `new_data` -- not the stale
    copy on disk. Such entities are therefore scored with `new_data`, otherwise
    the new value escapes the limit through the link.
    """
    data = {}
    for name in existing:
        yaml_text, link_target = stored.get(name, ("", None))
        if edited is not None and link_target == edited:
            data[name] = new_data
        else:
            data[name] = _repo.parse_data(yaml_text)
    return data


async def measure(
    rpo: IRepoSession,
    op: OperationRequest,
    s: Specs,
    old: Entity,
    new_data: dict,
) -> list[out.LimitUsage]:
    """
    Compute the usage of every `limits` rule that applies to this operation.

    The candidate set is every existing entity of the type (minus the entity
    being replaced, on `edit`) plus the incoming entity — so create,
    rename, owner-change and quota-increase all fall out of one uniform scan.
    A rule whose `scope`/`value` only reference `old.name` (not `old.data`) is
    counted from the name list alone; the YAML of the entities is only loaded
    (in one bulk read) if a rule needs it.

    Must be called inside an open repo reader scope. Returns the usages for
    display; enforcement (raising on `not ok`) happens in `lib.validator`.
    """
    if s.type is None or not s.type.limits:
        return []

    applicable = [lim for lim in s.type.limits if op.operation in lim.on]
    if not applicable:
        return []

    new_name = props.effective_name(op)
    base = props.get_limits_base(op, s.request, s.context)
    base["new"] = {"name": new_name, "data": new_data or {}}

    names = await rpo.list(op.type_name)
    # On edit, the current version of the entity is replaced by the incoming
    # one, so it must not be double-counted.
    edited = old.name if op.operation == "edit" else None
    existing = [n for n in names if not (edited is not None and n == edited)]

    # Read (in bulk) and parsed at most once for all the limits, like a scope
    # that several limits share is tested once per entity. Sound, because a
    # scope that reads the data gets it with every limit that has this scope.
    data: dict[str, dict] | None = None
    scopes: dict[str, dict[str, bool]] = {name: {} for name in existing}

    usages: list[out.LimitUsage] = []
    for lim in applicable:
        # A limit that does not reference the data of the entities is scored
        # straight from their names.
        needs_data = j2.uses_old_data(lim.scope) or j2.uses_old_data(lim.value)
        if needs_data and data is None:
            data = _existing_data(
                existing, await rpo.get_all(op.type_name), edited, new_data or {}
            )

        # The incoming entity always contributes (its data is `new_data`).
        total = await _contribution(lim, base, new_name or "", new_data or {})
        for name in existing:
            total += await _contribution(
                lim, base, name, data[name] if needs_data else {}, scopes[name]
            )
        # Float sums of decimal values carry noise (0.1 + 0.2), which would
        # show up in the usage (0.30000000000000004/2.5) and could tip a sum
        # exactly at the cap over it.
        total = round(total, _SUM_DECIMALS)

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
                path=lim.path,
            )
        )

    return usages


async def enforce(
    rpo: IRepoSession,
    op: OperationRequest,
    s: Specs,
    old: Entity,
    new_data: dict,
) -> None:
    """
    Re-measure the limits against `rpo` and raise if any is exceeded.

    TOCTOU guard for the quota-consuming write paths (create/copy/link and
    edit): the routers measure usages inside a *reader* scope (that result
    feeds the validator and is what the UI displays), but the write happens
    later inside a separate *writer* scope -- so two concurrent operations
    could both pass the reader-scope check at usage N-1 and end at N+1.
    Callers therefore invoke this with the writer-scope session, immediately
    before writing, so measurement and write are serialized by the repo
    writer lock. Raises the same RequestError as `lib.validator.test_all`
    (via `assert_within`), so the HTTP response shape is unchanged.
    """
    assert_within(await measure(rpo, op, s, old, new_data))


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
