import re

from app.model.err import RequestError
from app.model.inp import CopyEntity
from app.model.inp import LinkEntity
from app.model.inp import OperationRequest
from app.model.spc import Specs
from app.model.plg import IValidator


class NameTester(IValidator):
    def __assert_match(self, pattern: str, name: str | None, loc: str) -> None:
        if name is None:
            raise RequestError(f"The {loc} must be set for this operation")
        if not re.match(pattern, name):
            raise RequestError(
                f'The {loc} {name} does not match the type spec pattern "{pattern}"'
            )

    def __assert_none(self, name: str | None, loc: str) -> None:
        if name is not None:
            raise RequestError(f"The {loc} must not be set for this operation")

    def order(self) -> tuple[bool, int]:
        return False, 20

    async def test_always(self, op: OperationRequest, spec: Specs) -> None:
        """
        Test if all the entity names are set (or not set) correctly.
        """

        if op.operation == "read":
            return

        assert spec.type is not None  # validated in type_spec plugin

        if op.operation == "create":
            self.__assert_none(op.name, "name")
        else:  # change, delete, arbitrary
            self.__assert_match(spec.type.name_pattern, op.name, "name")

        if op.operation == "change":
            assert op.entity is not None  # validated in operations plugin
            self.__assert_match(spec.type.name_pattern, op.entity.name, "entity.name")

        if op.operation == "create":
            assert op.entity is not None  # validated in operations plugin
            if spec.type.name_generated == "never":
                self.__assert_match(
                    spec.type.name_pattern, op.entity.name, "entity.name"
                )
            elif spec.type.name_generated == "optional":
                if op.entity.name is not None:
                    self.__assert_match(
                        spec.type.name_pattern, op.entity.name, "entity.name"
                    )
            else:  # enforced
                self.__assert_none(op.entity.name, "entity.name")

            if isinstance(op.entity, CopyEntity):
                self.__assert_match(
                    spec.type.name_pattern, op.entity.copy_name, "entity.copy"
                )
            elif isinstance(op.entity, LinkEntity):
                self.__assert_match(
                    spec.type.name_pattern, op.entity.link_name, "entity.link"
                )


tester = NameTester()
