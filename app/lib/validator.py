"""
Raises: [app.model.err.RequestError]
"""

from app.lib import plugin
from app.lib import schema
from app.lib import yaml
from app.model.err import RequestError
from app.model.inp import NewEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.out import Request
from app.model.out import Schema
from app.model.out import ValidationResult
from app.model.int import Entity
from app.model.spc import Specs


async def test_all(
    op: OperationRequest, specs: Specs, old: Entity, new: Entity, *, raise_on_error=True
) -> ValidationResult:
    """
    Will try to generate the schemas even with faulty data. It either throws a
    RequestError or (if not raise_on_error) just returns the first validation
    error in the ValidationResult.
    """

    request = Request(valid=True)

    try:
        if isinstance(op.entity, NewEntity):
            new_data = yaml.load_as_dict(op.entity.yaml)
        elif isinstance(op.entity, ReplaceEntity):
            new_data = yaml.load_as_dict(op.entity.yaml_new)
        elif isinstance(op.entity, UpdateEntity):
            new_data = yaml.load_as_dict(yaml.update(old.yaml or "", op.entity.data))
        else:
            new_data = {}
    except yaml.YAMLError as error:
        if raise_on_error:
            raise RequestError(str(error)) from error
        new_data = {}
        request.valid = False
        request.message = str(error)

    if op.operation == "change" or (
        op.operation == "create" and isinstance(op.entity, NewEntity)
    ):
        schemas = await schema.get(
            op,
            specs.json_schema,
            specs.request,
            old.data or {},
            old.perms or [],
            new_data,
        )
    else:
        schemas = Schema(json_schema={}, ui_schema={}, data={}, valid=True)

    require = ("actions", "conflicts", "names", "operations", "perms", "type_spec")
    try:
        for p in plugin.get_sorted("validator", "tester", require=require, late=False):
            await p.test_always(op, specs)
        for p in plugin.get_sorted("validator", "tester", require=require, late=True):
            await p.test_nolist(op, specs, old, new)
    except RequestError as error:
        if raise_on_error:
            raise error
        if request.valid:
            request.valid = False
            request.message = str(error)

    if raise_on_error and not schemas.valid:
        raise RequestError(schemas.message)

    return ValidationResult(schemas=schemas, request=request)


async def test_ls(op: OperationRequest, specs: Specs) -> None:
    require = ("names", "operations", "type_spec")
    for p in plugin.get_sorted("validator", "tester", require=require, late=False):
        await p.test_always(op, specs)
