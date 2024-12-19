from app.lib import yaml
from app.model.err import RequestError
from app.model.err import RequestForbidden
from app.model.err import ServerError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.int import Entity
from app.model.spc import Specs
from app.model.plg import IValidator


class PermissionTester(IValidator):
    def __assert_perm(self, perm: str, perms: list[str] | None):
        if perm not in (perms or []):
            raise RequestForbidden(
                f'You need the "{perm}" permission to execute this operation.'
            )

    def order(self) -> tuple[bool, int]:
        return True, 10

    async def test_nolist(
        self, op: OperationRequest, spec: Specs, old: Entity, new: Entity
    ) -> None:
        """
        Test if the user has the correct permissions for the requested operation.
        """

        if op.operation == "read":
            self.__assert_perm("see", old.perms)

        elif op.operation == "create":
            self.__assert_perm("add", new.perms)
            if isinstance(op.entity, CopyEntity):
                self.__assert_perm("cpy", old.perms)
            elif isinstance(op.entity, LinkEntity):
                self.__assert_perm("lnk", old.perms)

        elif op.operation == "change":
            has_changes = True
            if isinstance(op.entity, UpdateEntity):
                if not op.entity.data:
                    has_changes = False
            elif isinstance(op.entity, ReplaceEntity):
                if op.entity.yaml_old == op.entity.yaml_new:
                    has_changes = False
                try:
                    if yaml.has_structural_changes(
                        op.entity.yaml_old, op.entity.yaml_new
                    ):
                        self.__assert_perm("cln", old.perms)
                except yaml.YAMLError as error:
                    raise RequestError(str(error)) from error
            else:
                raise RequestError("The entity has the wrong format for this operation")

            if has_changes:
                self.__assert_perm("edt", old.perms)
            if op.name != op.entity.name:
                self.__assert_perm("add", new.perms)
                self.__assert_perm("rnm", old.perms)

        elif op.operation == "delete":
            self.__assert_perm("del", old.perms)


tester = PermissionTester()
