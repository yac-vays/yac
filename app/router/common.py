"""
What the entity routers share: building the operation of a request and
loading everything it is validated against.
"""

from dataclasses import dataclass, field

from fastapi import Request

from app.lib import limits
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model import inp
from app.model import out
from app.model.int import Entity
from app.model.plg import IRepoSession
from app.model.plg import IRepoUntyped
from app.model.spc import Specs

# Placeholder type name for requests that concern no entity type.
NO_TYPE = "does-not-exist"


def operation_request(
    request: Request,
    user: out.User,
    operation: str,
    type_name: str,
    name: str | None = None,
    *,
    actions: list[str] | None = None,
    entity: inp.Entity | None = None,
    force: bool = False,
) -> inp.OperationRequest:
    return inp.OperationRequest(
        request_headers=dict(request.headers),
        request_ip=request.client.host if request.client else "",
        user=user,
        operation=operation,
        type=type_name,
        name=name,
        actions=actions or [],
        entity=entity,
        force=force,
    )


def session(raw: IRepoUntyped, s: Specs) -> IRepoSession:
    """The typed repo session for the type selected in the specs."""
    return raw.session(s.repo.details if s.type else {})


@dataclass
class Loaded:
    """The state an operation is validated against."""

    specs: Specs
    hash: str
    old: Entity
    new: Entity
    perms: list[str]
    # Only with `with_limits`.
    new_data: dict = field(default_factory=dict)
    usages: list[out.LimitUsage] = field(default_factory=list)


async def load(
    op: inp.OperationRequest, *, dirty: bool = False, with_limits: bool = False
) -> Loaded:
    s = await specs.read(op)
    async with repo.handler.reader(op.user, dirty=dirty) as raw:
        rpo = session(raw, s)
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)
        loaded = Loaded(s, hash, old, new, perms)
        if with_limits:
            loaded.new_data, _ = validator.incoming_new_data(op, old)
            loaded.usages = await limits.measure(rpo, op, s, old, loaded.new_data)
    return loaded
