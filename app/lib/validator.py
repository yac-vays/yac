"""
Raises: [app.model.err.RequestError]
"""

from app.lib import limits
from app.lib import plugin
from app.lib import schema
from app.lib import yaml
from app.model.err import RequestError
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
            return yaml.load_as_dict(yaml.update(old.yaml or "", op.entity.data)), None
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
            # Merge the patch into the stored YAML, keeping its comments.
            return yaml.update(old.yaml or "", op.entity.data)
        if isinstance(op.entity, NewEntity):
            # Round-trip to normalize formatting while keeping comments.
            return yaml.dump(yaml.load(op.entity.yaml))
        if isinstance(op.entity, ReplaceEntity):
            return yaml.dump(yaml.load(op.entity.yaml_new))
        return None
    except (yaml.YAMLError, RequestError):
        return None


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

    request = Request(valid=True)

    new_data, yaml_error = incoming_new_data(op, old)
    if yaml_error is not None:
        if raise_on_error:
            raise RequestError(yaml_error)
        request.valid = False
        request.message = yaml_error

    if (
        op.operation == "change"
        or (op.operation == "create" and isinstance(op.entity, NewEntity))
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

    if raise_on_error and not schemas.valid:
        raise RequestError(schemas.message)

    return ValidationResult(schemas=schemas, request=request, usages=usages)


async def test_ls(op: OperationRequest, specs: Specs) -> None:
    require = ("names", "operations", "type_spec")
    for p in plugin.get_sorted("validator", "tester", require=require, late=False):
        await p.test_always(op, specs)
