from fastapi import APIRouter
from fastapi import Request

from app.lib import limits
from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.lib.auth import CurrentUser
from app.model.err import RequestError
from app.model.inp import Operation
from app.model.out import ValidationResult
from app.router.common import operation_request
from app.router.common import session

router = APIRouter()


@router.post(
    "/validate",
    summary="Validate an operation (including entity data) and return the schema",
)
async def validate_operation(
    request: Request, user: CurrentUser, op: Operation
) -> ValidationResult:
    """
    **Note** that the schema is not static but generated and thus may change
    depending on all the data sent in the request.
    """

    op = operation_request(
        request,
        user,
        op.operation,
        op.type_name,
        op.name,
        actions=op.actions,
        entity=op.entity,
    )

    s = await specs.read(op)
    async with repo.handler.reader(op.user, dirty=True) as raw:
        rpo = session(raw, s)
        hash = await rpo.get_hash()
        old, new, perms = await repo.get_entities(hash, rpo, op, s)
        # Nothing derived from an entity the user may not read (see
        # `validator.test_all`), not even its share of a limit.
        usages = []
        limit_error = None
        if validator.may_read(old, perms):
            new_data, _ = validator.incoming_new_data(op, old)
            try:
                usages = await limits.measure(rpo, op, s, old, new_data)
            except RequestError as error:
                # E.g. a limit referencing data the form has not filled in
                # yet: a validation result like any other, not a failure.
                limit_error = str(error)

    result = await validator.test_all(
        op, s, old, new, perms, usages, raise_on_error=False, schema_on_read=True
    )
    if limit_error is not None and result.request.valid:
        result.request.valid = False
        result.request.message = limit_error
    return result
