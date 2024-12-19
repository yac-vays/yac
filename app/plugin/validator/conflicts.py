from app.model.err import RequestConflict
from app.model.err import RequestError
from app.model.err import RequestNotFound
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.int import Entity
from app.model.spc import Specs
from app.model.plg import IValidator

# pylint: disable=unused-argument


class ConflictTester(IValidator):
    def order(self) -> tuple[bool, int]:
        return True, 30

    async def test_nolist(
        self, op: OperationRequest, spec: Specs, old: Entity, new: Entity
    ) -> None:
        """
        Test the operation for conflicts in existance of the entities/links.
        """

        if op.operation == "create":
            if new.exists:
                raise RequestConflict(f"{new.name} already exists")
            if isinstance(op.entity, (CopyEntity, LinkEntity)):
                if not old.exists:
                    raise RequestNotFound(f"{old.name} does not exist")
                if old.is_link:
                    raise RequestError("Links cannot be copied/linked")

        else:  # read, change, delete, arbitrary
            if not old.exists:
                raise RequestNotFound(f"{old.name} does not exist")

        if op.operation == "change":
            if old.is_link:
                raise RequestError("Links cannot be modified")
            if old.name != new.name and new.exists:
                raise RequestConflict(f"{new.name} already exists")

            if isinstance(op.entity, ReplaceEntity):
                if old.yaml != op.entity.yaml_old:
                    raise RequestConflict("The data has changed in the meantime")


tester = ConflictTester()
