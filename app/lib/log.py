"""
Raises: [app.model.err.LogSpecsError]
"""

from typing import List
import logging

from app.lib import plugin
from app.lib import props
from app.model import inp
from app.model import out
from app.model import spc
from app.model.err import LogError

logger = logging.getLogger(__name__)


async def get(op: inp.OperationRequest, specs: spc.Specs) -> List[out.Log]:
    log_props = props.get_log(op, specs.request)

    logs = []
    for log_spec in specs.type.logs if specs.type else []:
        log_plugin = plugin.get_module("log", log_spec.plugin)
        try:
            logs.extend(
                await log_plugin.log.get(
                    log_spec.name,
                    log_spec.problem,
                    log_spec.progress,
                    details=log_spec.details,
                    props=log_props,
                )
            )
        except LogError as error:
            logger.error(str(error))
    return logs
