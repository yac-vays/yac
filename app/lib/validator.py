"""
Raises: [app.model.err.RequestError]
"""

from app.lib import limits
from app.lib import plugin
from app.lib import schema
from app.lib import yaml
from app.model.err import RequestError
from app.model.err import RequestForbidden
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import NewEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.out import LimitUsage
from app.model.out import Request
from app.model.out import Schema
from app.model.out import ValidationResult
from app.model.int import Entity
from app.model.spc import Specs


def _written_document(
    op: OperationRequest, old: Entity
) -> tuple[dict, str | None, str | None] | None:
    """
    `(data, yaml, error)` of the YAML document the operation would write, or
    None if it writes none of its own (a read, copy, link, ...). `yaml` is the
    canonical document with comments / quoting / key order preserved, `data`
    what it parses to; with an `error` (malformed YAML, not a mapping) the
    data is `{}`.

    The round-trip parser is by far the slowest part of a validation and an
    operation is asked for its document several times (the limits, the
    permission check, the schema), so the result is kept on the operation
    for the base document it was derived from.
    """
    entity = op.entity
    if isinstance(entity, UpdateEntity):
        # The patch is merged into `yaml_base` (the YAML the client is
        # editing) if given, else into the stored YAML.
        base = entity.yaml_base if entity.yaml_base is not None else (old.yaml or "")
    elif isinstance(entity, NewEntity):
        base = entity.yaml
    elif isinstance(entity, ReplaceEntity):
        base = entity.yaml_new
    else:
        return None

    memo = op.written_document
    if memo is not None and memo[0] is entity and memo[1] == base:
        return memo[2]

    text: str | None = None
    try:
        if isinstance(entity, UpdateEntity):
            text = yaml.update(base, entity.data)
            data = yaml.load_mapping(text)
        else:
            # Dumped right away (a round-trip that normalizes the formatting
            # while keeping the comments), before anything gets to see `data`.
            node = yaml.load(base)
            text = yaml.dump(node)
            data = yaml.as_mapping(node)
        result = (data, text, None)
    except yaml.YAMLError as error:
        result = ({}, text, str(error))
    op.written_document = (entity, base, result)
    return result


def incoming_new_data(op: OperationRequest, old: Entity) -> tuple[dict, str | None]:
    """
    The data the operation would write, parsed to a dict. Returns
    `(data, None)` on success or `({}, error_message)` if the payload YAML is
    malformed or not a mapping. Shared between `test_all` (schema generation)
    and the routers (limit measurement) so the two never disagree on what "the
    new data" is.

    Copies/links inherit the source entity's data (`old.data`), matching how
    the create router generates their name.
    """
    document = _written_document(op, old)
    if document is not None:
        return document[0], document[2]
    if isinstance(op.entity, (CopyEntity, LinkEntity)):
        return old.data or {}, None
    if op.entity is None and op.operation == "read":
        return old.data or {}, None
    return {}, None


def incoming_new_yaml(op: OperationRequest, old: Entity) -> str | None:
    """
    The canonical YAML the operation would write, with comments/quoting/key
    order preserved (unlike `incoming_new_data`, which drops them). Returned
    to the UI in `Schema.yaml` so a YAML editor can stay in sync with the form
    without re-implementing YAC's ruamel serialization client-side.

    Returns `None` for operations that do not produce writable YAML (reads,
    copies, links) or when the payload YAML is malformed (the schema/request
    validation already surfaces that error).
    """
    document = _written_document(op, old)
    return None if document is None else document[1]


def may_read(old: Entity, perms: list[str]) -> bool:
    """
    Whether anything derived from the stored entity `old` (its data, its
    YAML, a schema generated from it) may be handed to the user. Reading is
    all-or-nothing and tied to "see"; an entity that does not exist has
    nothing to protect.

    `old.perms` are the rights on the stored entity itself (for a rename /
    copy / link they differ from `perms`, see `lib.repo.get_entities`).
    """
    if not old.exists:
        return True
    return "see" in (old.perms if old.perms is not None else perms)


def has_content_changes(op: OperationRequest, old: Entity) -> bool:
    """
    Whether an edit operation would modify the entity's YAML content (as
    opposed to a pure rename or a no-op). Also used by
    plugin/validator/perms.py to decide whether `edt` is required, so the
    schema enforcement and the permission check always agree.

    A patch counts by its effect on the data: re-sending stored values is
    not a change.
    """
    if isinstance(op.entity, UpdateEntity):
        if not op.entity.data:
            return False
        new_data, error = incoming_new_data(op, old)
        return error is not None or not yaml.same_data(new_data, old.data or {})
    if isinstance(op.entity, ReplaceEntity):
        return op.entity.yaml_old != op.entity.yaml_new
    return True


async def test_all(
    op: OperationRequest,
    specs: Specs,
    old: Entity,
    new: Entity,
    perms: list[str] = [],
    usages: list[LimitUsage] = [],
    *,
    raise_on_error=True,
    schema_on_read=False,
    incoming: tuple[dict, str | None] | None = None
) -> ValidationResult:
    """
    Will try to generate the schemas even with faulty data. It either throws a
    RequestError or (if not raise_on_error) just returns the first validation
    error in the ValidationResult.

    `usages` are the pre-computed `limits` results (gathered inside the repo
    reader scope via `lib.limits.measure`); they are echoed back in the
    result for the UI and enforced here so the standard error handling applies.

    `incoming` is the result of `incoming_new_data` if the caller has it
    already (it parses the YAML of the request, which is not cheap).
    """

    # The admin override (`force` on the write endpoints) is only available to
    # users holding the "adm" permission — enforced here, not (only) in the UI.
    # `force` can only arrive via the write routers, which always call with
    # raise_on_error=True; /validate cannot set it.
    if op.force and "adm" not in perms:
        raise RequestForbidden('You need the "adm" permission to force a write.')

    request = Request(valid=True)

    # First whether the operation is allowed at all: a request that is not
    # must not make YAC parse its documents and generate schemas (the
    # expensive part of a validation) before it is refused.
    require = ("actions", "conflicts", "names", "operations", "perms", "type_spec")
    refusal: RequestError | None = None
    try:
        for p in plugin.get_sorted("validator", "tester", require=require, late=False):
            await p.test_always(op, specs)
        for p in plugin.get_sorted("validator", "tester", require=require, late=True):
            await p.test_nolist(op, specs, old, new, perms)
        # Enforced last so a forbidden / conflicting operation surfaces its own
        # (more specific) error before a limit message.
        limits.assert_within(usages)
        # A write that changes nothing (no content change, no rename) is
        # refused instead of being passed on: applying an empty patch still
        # re-serializes the stored YAML, which must not become a commit.
        # (/validate reports such a request as valid -- that is how a client
        # asks for the schema of an entity it is about to edit.)
        if (
            raise_on_error
            and op.operation == "edit"
            and op.entity is not None
            and op.entity.name == op.name
            and not has_content_changes(op, old)
        ):
            raise RequestError("The request does not change anything")
    except RequestError as error:
        if raise_on_error:
            raise error
        refusal = error

    # Everything below that is derived from the stored entity (the merged
    # data, the generated schema with its echoed consts, the canonical YAML)
    # ends up in the result of `/validate` -- regardless of whether the
    # operation itself is allowed. So without "see" on the stored entity,
    # none of it is generated; the refusal above still says why the
    # operation is not allowed. The same goes for an entity that does not
    # exist (except when creating one): answering differently would tell
    # which names exist to users who may not see them.
    readable = may_read(old, perms) and (old.exists or op.operation == "create")

    if incoming is None or not readable:
        incoming = incoming_new_data(op, old) if readable else ({}, None)
    new_data, yaml_error = incoming
    if yaml_error is not None:
        if raise_on_error:
            raise RequestError(yaml_error)
        request.valid = False
        request.message = yaml_error

    if readable and (
        op.operation == "edit"
        or (op.operation == "create" and isinstance(op.entity, (NewEntity, UpdateEntity)))
        or (op.operation == "read" and schema_on_read)
    ):
        schemas = await schema.get(
            op,
            specs.json_schema,
            specs.request,
            specs.context,
            old.data or {},
            perms,
            new_data,
        )
    else:
        schemas = Schema(json_schema={}, ui_schema={}, data={}, valid=True)

    # Canonical YAML for the new data (comments preserved), so a YAML editor in
    # the UI can mirror the form without re-implementing ruamel serialization.
    schemas.yaml = (incoming_new_yaml(op, old) or "") if readable else ""

    if refusal is not None:
        if request.valid:
            request.valid = False
            request.message = str(refusal)
        if isinstance(refusal, RequestForbidden):
            # How much of a limit is used is derived from the stored
            # entities of others: not for those the operation is refused to.
            usages = []

    # An admin override (`force`, gated on "adm" above) skips exactly this
    # SCHEMA enforcement — everything before it (YAML syntax, name/request
    # rules, permissions, conflicts, limits) raised already when violated.
    if raise_on_error and not schemas.valid and not op.force:
        # A change without content changes (a pure rename or a no-op) moves
        # the stored YAML as-is, so stored data that no longer matches the
        # current schema must not block it. The validity is still reported
        # in the result (the /validate endpoint shows it), just not enforced.
        if op.operation != "edit" or has_content_changes(op, old):
            raise RequestError(schemas.message)

    return ValidationResult(
        schemas=schemas, request=request, usages=usages, perms=perms
    )


async def test_ls(op: OperationRequest, specs: Specs) -> None:
    require = ("names", "operations", "type_spec")
    for p in plugin.get_sorted("validator", "tester", require=require, late=False):
        await p.test_always(op, specs)
