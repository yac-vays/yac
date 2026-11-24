"""
Raises:
  - app.model.err.ActionError
  - app.model.err.ActionClientError
  - app.model.err.ActionSpecsError
"""

import logging
import re

from fastapi import Response

from app.lib import plugin
from app.lib import props
from app.model import out
from app.model import inp
from app.model import spc
from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import YACError

log = logging.getLogger(__name__)

# Response header of a write whose `*:after` action failed.
WARNING_HEADER = "YAC-Warning"


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
            await action_plugin.action.run(details=action.details, props=action_props)
        except ActionClientError as error:
            error.action_name = action.name
            raise error
        except ActionError as error:
            wrapped = ActionError(
                f"Action {action.name} for {op.type_name}"
                f" {op.name or action_props['name']} failed with: {error}"
            )
            wrapped.action_name = action.name
            raise wrapped from error


async def run_after(
    hook: out.TypeActionHook,
    op: inp.OperationRequest,
    specs: spc.Specs,
    response: Response,
) -> None:
    """
    Run the `*:after` actions of a write that is already committed. A failure
    must not turn the response into an error (the client would retry a write
    that happened): it is logged and reported in the `YAC-Warning` header.
    """
    try:
        await run(hook, op, specs)
    except YACError as error:
        log.error(f"{hook} failed after the write: {error}")
        name = getattr(error, "action_name", None)
        text = f"Action {name} failed" if name else "An action failed"
        if error.code < 500:
            text += f": {error}"
        # Header values: one line of printable ASCII.
        response.headers[WARNING_HEADER] = re.sub(r"[^\x20-\x7e]+", " ", text)[:300]
