from app.lib import validator
from app.lib import yaml
from app.model.err import RequestError
from app.model.err import RequestForbidden
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.inp import ReplaceEntity
from app.model.inp import UpdateEntity
from app.model.int import Entity
from app.model.spc import Specs
from app.model.plg import IValidator


class PermissionTester(IValidator):
    def __assert_perm(
        self, perm: str, perms: list[str] | None, what: str = "execute this operation"
    ):
        if perm not in (perms or []):
            raise RequestForbidden(f'You need the "{perm}" permission to {what}.')

    def order(self) -> tuple[bool, int]:
        return True, 10

    async def test_nolist(
        self,
        op: OperationRequest,
        spec: Specs,
        old: Entity,
        new: Entity,
        perms: list[str],
    ) -> None:
        """
        Test if the user has the correct permissions for the requested operation.
        """

        if op.operation == "read":
            self.__assert_perm("see", perms)

        elif op.operation == "create":
            self.__assert_perm("add", perms)
            # Copying / linking needs the right on the SOURCE entity (`old`),
            # evaluated for the source alone: `perms` belong to the new name.
            if isinstance(op.entity, CopyEntity):
                self.__assert_perm("cpy", old.perms, "copy this entity")
            elif isinstance(op.entity, LinkEntity):
                self.__assert_perm("lnk", old.perms, "link to this entity")

        elif op.operation == "edit":
            # "cln" covers what the schema does not: comments, key order,
            # quoting / style. Which DATA changes are allowed (including the
            # removal of keys) is decided by the schema alone, see
            # plugin/json_schema/add_consts.py for keys the schema does not
            # define.
            has_changes = validator.has_content_changes(op, old)
            if isinstance(op.entity, ReplaceEntity):
                try:
                    if yaml.has_structural_changes(
                        op.entity.yaml_old, op.entity.yaml_new
                    ):
                        self.__assert_perm(
                            "cln",
                            perms,
                            "change comments, key order or formatting of the YAML",
                        )
                except yaml.YAMLError as error:
                    raise RequestError(str(error)) from error
            elif not isinstance(op.entity, UpdateEntity):
                # (A patch only expresses data changes: it never needs "cln".)
                raise RequestError("The entity has the wrong format for this operation")

            renamed = op.name != op.entity.name
            # `perms` are the rights on the stored entity (for a rename
            # evaluated for the old name alone, see lib.repo.get_entities).
            if has_changes:
                self.__assert_perm("edt", perms)
            if renamed:
                self.__assert_perm("rnm", perms, "rename this entity")
                # ... while the new name must be one the user may create.
                self.__assert_perm(
                    "add", new.perms, "create an entity with the new name"
                )
            if not has_changes and not renamed:
                # Nothing is to be changed (VAYS validates this to open an
                # entity for editing / renaming): still only for those who
                # may change it at all. Without this, the request would need
                # no permission whatsoever.
                if not {"edt", "rnm"}.intersection(perms or []):
                    self.__assert_perm("edt", perms)

        elif op.operation == "delete":
            self.__assert_perm("del", perms)


tester = PermissionTester()
