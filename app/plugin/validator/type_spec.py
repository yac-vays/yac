import re

from app import consts
from app.model.err import RequestForbidden
from app.model.err import RequestNotFound
from app.model.err import SpecsError
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

        try:
            pattern = re.compile(spec.type.name_pattern)
        except re.error as error:
            raise SpecsError(
                f"Type {spec.type.name} name_pattern is not a valid regex: {error}"
            ) from error
        # Reject patterns that accept tokens that NAME_PATTERN forbids and that
        # would let a user escape the entity directory on disk.
        global_pattern = re.compile(consts.NAME_PATTERN)
        for forbidden in ("/", "../", "..\\", "\x00", "\n"):
            if pattern.fullmatch(forbidden) and not global_pattern.fullmatch(forbidden):
                raise SpecsError(
                    f"Type {spec.type.name} name_pattern '{spec.type.name_pattern}'"
                    f" is too permissive: it matches '{forbidden!r}' which the global"
                    f" NAME_PATTERN forbids"
                )

        if op.operation == "create" and not spec.type.create:
            raise RequestForbidden('The operation "create" is disabled')
        if op.operation == "edit" and not spec.type.edit:
            raise RequestForbidden('The operation "edit" is disabled')
        if op.operation == "delete" and not spec.type.delete:
            raise RequestForbidden('The operation "delete" is disabled')

        # A rename is submitted as an edit with a new entity name, but it
        # creates a new name and deletes the old one — so it must respect the
        # `create` and `delete` type switches as well. Otherwise renaming
        # would bypass them on types whose names are externally managed
        # (create/delete disabled, edit enabled).
        if op.operation == "edit" and not (spec.type.create and spec.type.delete):
            renamed = (
                op.entity is not None
                and op.entity.name is not None
                and op.name is not None
                and op.entity.name != op.name
            )
            if renamed:
                raise RequestForbidden(
                    'The operation "rename" is disabled (it would create a new'
                    " and delete the old entity name)"
                )


tester = TypeTester()
