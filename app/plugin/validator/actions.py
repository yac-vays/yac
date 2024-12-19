from app.model.err import RequestForbidden
from app.model.err import RequestNotFound
from app.model.inp import OperationRequest
from app.model.int import Entity
from app.model.spc import Specs
from app.model.plg import IValidator

# pylint: disable=unused-argument


class ActionTester(IValidator):
    def order(self) -> tuple[bool, int]:
        return True, 20

    async def test_nolist(
        self, op: OperationRequest, spec: Specs, old: Entity, new: Entity
    ) -> None:
        """
        Test if the actions are valid for this operation and if the user has the
        permissions to execute them.
        """

        for action in op.actions:
            action_spec = next(
                (a for a in getattr(spec.type, "actions", []) if a.name == action), None
            )

            if action_spec is None:
                raise RequestNotFound(f"Action {action} is not defined")

            hooked = False
            for hook in action_spec.hooks:
                if hook.split(":")[0] == op.operation:
                    hooked = True
                    break

            if not hooked:
                raise RequestNotFound(
                    f"Action {action} is not defined for this operation"
                )

            perms_required = getattr(action_spec, "perms", ["act"])
            if len(set(old.perms or []).intersection(set(perms_required))) <= 0:
                if op.operation == "arbitrary" or not action_spec.force:
                    raise RequestForbidden(
                        f"You need one of these permission to run this action(s): "
                        f'{", ".join(perms_required)}'
                    )


tester = ActionTester()
