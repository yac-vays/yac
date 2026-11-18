"""
Raises: [app.model.err.LogSpecsError, app.model.err.LogError,
         app.model.err.RequestNotFound]
"""

from typing import List
import asyncio
import logging

from app.lib import plugin
from app.lib import props
from app.model import inp
from app.model import out
from app.model import spc
from app.model.err import LogError
from app.model.err import RequestNotFound

logger = logging.getLogger(__name__)


async def get(
    op: inp.OperationRequest, specs: spc.Specs, data: dict | None = None
) -> List[out.Log]:
    """
    The entries of all logs of the entity (`data` is its data, available to the
    details as `old.data`). A failing log is left out.
    """
    log_props = props.get_log(op, specs.request, data)

    log_specs = specs.type.logs if specs.type else []

    async def _fetch(log_spec) -> List[out.Log]:
        log_plugin = plugin.get_module("log", log_spec.plugin)
        try:
            return await log_plugin.log.get(
                log_spec.name,
                details=log_spec.details,
                props=log_props,
            )
        except LogError as error:
            logger.error(str(error))
            return []

    results = await asyncio.gather(*(_fetch(s) for s in log_specs))

    logs: List[out.Log] = []
    for result in results:
        logs.extend(result)
    return logs


async def content(
    op: inp.OperationRequest, specs: spc.Specs, name: str, data: dict | None = None
) -> out.LogContent:
    """
    The content document of the log `name` of the entity. Unlike `get`, errors
    are raised: the user asked for exactly this document.
    """
    log_specs = specs.type.logs if specs.type else []
    for log_spec in log_specs:
        if log_spec.name == name:
            break
    else:
        raise RequestNotFound(f'There is no log "{name}"')

    log_plugin = plugin.get_module("log", log_spec.plugin)
    return await log_plugin.log.content(
        log_spec.name,
        details=log_spec.details,
        props=props.get_log(op, specs.request, data),
    )
