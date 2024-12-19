from fastapi import APIRouter
from fastapi import Request

from app.lib import repo
from app.lib import specs
from app.lib import validator
from app.model.err import http_responses
from app.model.inp import Operation
from app.model.inp import OperationRequest
from app.model.inp import User
from app.model.out import ValidationResult

router = APIRouter()


@router.post(
    "/validate",
    summary="Validate an operation (including entity data) and return the schema",
    responses=http_responses(),
)
async def validate_operation(
    request: Request, user: User, op: Operation
) -> ValidationResult:
    """
    **Note** that the schema is not static but generated and thus may change
    depending on all the data sent in the request.
    """

    op = OperationRequest(
        request=request,
        user=user,
        operation=op.operation,
        type=op.type_name,
        name=op.name,
        actions=op.actions,
        entity=op.entity,
    )

    async with repo.handler.reader(op.user, details={}, dirty=True) as rpo:
        s = await specs.read(op, rpo)
        old, new = await repo.get_entities(rpo, op, s)

    return await validator.test_all(op, s, old, new, raise_on_error=False)
