from app.model.err import RequestError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import NewEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.spc import Specs
from app.model.plg import IValidator


class OperationTester(IValidator):
    def order(self) -> tuple[bool, int]:
        return False, 0

    async def test_always(self, op: OperationRequest, spec: Specs) -> None:
        """
        Test for the correct entity formats and actions count.
        """
        del spec

        if op.operation == "create":
            # `UpdateEntity` is accepted here only for the `/validate` endpoint, so
            # a client can validate a not-yet-created entity by merging its form
            # data into the YAML it is editing (`yaml_base`). The create POST
            # router's body type still only accepts NewEntity/Copy/Link.
            if not isinstance(op.entity, (NewEntity, CopyEntity, LinkEntity, UpdateEntity)):
                raise RequestError("The entity has the wrong format for this operation")

        elif op.operation == "change":
            if not isinstance(op.entity, (ReplaceEntity, UpdateEntity)):
                raise RequestError("The entity has the wrong format for this operation")

        else:  # read, delete, arbitrary
            if op.entity is not None:
                raise RequestError("The entity must not be set for this operation")

        if op.operation == "arbitrary":
            if len(op.actions) != 1:
                raise RequestError("Exactly one action is required for this operation")


tester = OperationTester()
