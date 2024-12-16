"""
Raises: [app.model.err.ActionError, app.model.err.ActionClientError, app.model.err.ActionSpecsError]
"""

import logging

from app.lib import plugin
from app.lib import props
from app.model import out
from app.model import inp
from app.model import spc
from app.model.err import ActionClientError
from app.model.err import ActionError

log = logging.getLogger(__name__)


async def run(
    hook: out.TypeActionHook, op: inp.OperationRequest, specs: spc.Specs
) -> None:
    action_props = props.get_action(op, specs.request)
    for action in specs.type.actions if specs.type else []:
        if not action.name in op.actions:
            if not action.force or hook == "arbitrary":
                continue
        if not hook in action.hooks:
            continue

        action_plugin = plugin.get_module("action", action.plugin)
        try:
            await action_plugin.run(details=action.details, props=action_props)
        except ActionClientError as error:
            raise error
        except ActionError as error:
            raise ActionError(
                f'Action {action.name} for {action_props.get("type", "(unknown type)")} '
                f'"{action_props.get("old", {}).get("name", "(unknown name)")}" failed with: {error}'
            ) from error
