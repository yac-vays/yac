from app.model.err import RequestForbidden
from app.model.err import RequestNotFound
from app.model.inp import OperationRequest
from app.model.spc import Specs
from app.model.plg import IValidator


class TypeTester(IValidator):
    def order(self) -> tuple[bool, int]:
        return False, 10

    async def test_always(self, op: OperationRequest, spec: Specs) -> None:
        """
        Test if the type exists and if operation is allowed for this type.
        """

        if spec.type is None:
            raise RequestNotFound(f"Type {op.type_name} is not defined")

        if op.operation == "create" and not spec.type.create:
            raise RequestForbidden('The operation "create" is disabled')
        if op.operation == "change" and not spec.type.change:
            raise RequestForbidden('The operation "change" is disabled')
        if op.operation == "delete" and not spec.type.delete:
            raise RequestForbidden('The operation "delete" is disabled')


tester = TypeTester()
