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


def incoming_new_data(op: OperationRequest, old: Entity) -> tuple[dict, str | None]:
    """
    The data the operation would write, parsed to a dict. Returns
    `(data, None)` on success or `({}, error_message)` if the payload YAML is
    malformed. Shared between `test_all` (schema generation) and the routers
    (limit measurement) so the two never disagree on what "the new data" is.

    Copies/links inherit the source entity's data (`old.data`), matching how
    the create router generates their name.
    """
    try:
        if isinstance(op.entity, NewEntity):
            return yaml.load_as_dict(op.entity.yaml), None
        if isinstance(op.entity, (CopyEntity, LinkEntity)):
            return old.data or {}, None
        if isinstance(op.entity, ReplaceEntity):
            return yaml.load_as_dict(op.entity.yaml_new), None
        if isinstance(op.entity, UpdateEntity):
            base = op.entity.yaml_base if op.entity.yaml_base is not None else (old.yaml or "")
            return yaml.load_as_dict(yaml.update(base, op.entity.data)), None
        if op.entity is None and op.operation == "read":
            return old.data or {}, None
        return {}, None
    except yaml.YAMLError as error:
        return {}, str(error)


def incoming_new_yaml(op: OperationRequest, old: Entity) -> str | None:
    """
    The canonical YAML the operation would write, with comments/quoting/key
    order preserved (unlike `incoming_new_data`, which goes through
    `load_as_dict` and drops them). Returned to the UI in `Schema.yaml` so a
    YAML editor can stay in sync with the form without re-implementing YAC's
    ruamel serialization client-side.

    Returns `None` for operations that do not produce writable YAML (reads,
    copies, links) or when the payload YAML is malformed (the schema/request
    validation already surfaces that error).
    """
    try:
        if isinstance(op.entity, UpdateEntity):
            # Merge the patch into `yaml_base` (the YAML the client is editing) if
            # given, else the stored YAML; either way comments are preserved.
            base = op.entity.yaml_base if op.entity.yaml_base is not None else (old.yaml or "")
            return yaml.update(base, op.entity.data)
        if isinstance(op.entity, NewEntity):
            # Round-trip to normalize formatting while keeping comments.
            return yaml.dump(yaml.load(op.entity.yaml))
        if isinstance(op.entity, ReplaceEntity):
            return yaml.dump(yaml.load(op.entity.yaml_new))
        return None
    except (yaml.YAMLError, RequestError):
        return None


def has_content_changes(op: OperationRequest) -> bool:
    """
    Whether an edit operation would modify the entity's YAML content (as
    opposed to a pure rename or a no-op). Mirrors the `has_changes` notion in
    plugin/validator/perms.py that decides whether `edt` is required, so the
    schema enforcement and the permission check always agree on what counts
    as a content change.
    """
    if isinstance(op.entity, UpdateEntity):
        return bool(op.entity.data)
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
    schema_on_read=False
) -> ValidationResult:
    """
    Will try to generate the schemas even with faulty data. It either throws a
    RequestError or (if not raise_on_error) just returns the first validation
    error in the ValidationResult.

    `usages` are the pre-computed `limits` results (gathered inside the repo
    reader scope via `lib.limits.measure`); they are echoed back in the
    result for the UI and enforced here so the standard error handling applies.
    """

    # The admin override (`force` on the write endpoints) is only available to
    # users holding the "adm" permission — enforced here, not (only) in the UI.
    # `force` can only arrive via the write routers, which always call with
    # raise_on_error=True; /validate cannot set it.
    if op.force and "adm" not in perms:
        raise RequestForbidden('You need the "adm" permission to force a write.')

    request = Request(valid=True)

    new_data, yaml_error = incoming_new_data(op, old)
    if yaml_error is not None:
        if raise_on_error:
            raise RequestError(yaml_error)
        request.valid = False
        request.message = yaml_error

    if (
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
    schemas.yaml = incoming_new_yaml(op, old) or ""

    require = ("actions", "conflicts", "names", "operations", "perms", "type_spec")
    try:
        for p in plugin.get_sorted("validator", "tester", require=require, late=False):
            await p.test_always(op, specs)
        for p in plugin.get_sorted("validator", "tester", require=require, late=True):
            await p.test_nolist(op, specs, old, new, perms)
        # Enforced last so a forbidden / conflicting operation surfaces its own
        # (more specific) error before a limit message.
        limits.assert_within(usages)
    except RequestError as error:
        if raise_on_error:
            raise error
        if request.valid:
            request.valid = False
            request.message = str(error)

    # An admin override (`force`, gated on "adm" above) skips exactly this
    # SCHEMA enforcement — everything before it (YAML syntax, name/request
    # rules, permissions, conflicts, limits) raised already when violated.
    if raise_on_error and not schemas.valid and not op.force:
        # A change without content changes (a pure rename or a no-op) moves
        # the stored YAML as-is, so stored data that no longer matches the
        # current schema must not block it. The validity is still reported
        # in the result (the /validate endpoint shows it), just not enforced.
        if op.operation != "edit" or has_content_changes(op):
            raise RequestError(schemas.message)

    return ValidationResult(
        schemas=schemas, request=request, usages=usages, perms=perms
    )


async def test_ls(op: OperationRequest, specs: Specs) -> None:
    require = ("names", "operations", "type_spec")
    for p in plugin.get_sorted("validator", "tester", require=require, late=False):
        await p.test_always(op, specs)
