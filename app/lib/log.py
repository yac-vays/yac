"""
Raises: [app.model.err.LogSpecsError, app.model.err.LogError,
         app.model.err.RequestNotFound]
"""

from typing import Any, List
import asyncio
import logging

from app import consts
from app.lib import j2
from app.lib import plugin
from app.lib import props
from app.model import inp
from app.model import out
from app.model import spc
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.err import RequestNotFound

logger = logging.getLogger(__name__)


async def render_field(
    plugin_name: str, details: dict, key: str, typ: Any, default: Any, log_props: dict
) -> Any:
    """
    For log plugins: a field of `details` (dotted `key` below it, e.g.
    `content.time`) rendered for one record, checked to be of type `typ`.
    `omit` yields None for problem / progress and "" for texts.

    A template that does not render is a specs error; a value of the wrong
    type depends on the record and is a LogError (the log is left out).
    """
    template = details
    for part in key.split("."):
        template = template.get(part, default) if isinstance(template, dict) else default
    try:
        result = await j2.render(template, log_props)
    except j2.J2Error as error:
        raise LogSpecsError(
            f'In details.{key} for log plugin "{plugin_name}": {error}'
        ) from error
    if result == consts.OMIT:
        return None if key in ("problem", "progress") else ""
    # bool is an int for Python, but no number for a log
    if not isinstance(result, typ) or (isinstance(result, bool) and typ is not bool):
        raise LogError(
            f'details.{key} of log plugin "{plugin_name}" rendered to'
            f" {type(result).__name__}: {str(result)[:100]!r}"
        )
    return result


async def build_entry(
    plugin_name: str,
    name: str,
    details: dict,
    log_props: dict,
    *,
    time_default: str,
    message_default: str,
) -> out.Log:
    """
    For log plugins: the entry of one record (`log` in `log_props`), from the
    time / message / problem / progress templates of the rendered `details`.
    """
    entry = out.Log(
        name=name,
        time=await render_field(
            plugin_name, details, "time", str, time_default, log_props
        ),
        message=await render_field(
            plugin_name, details, "message", str, message_default, log_props
        ),
    )
    if "problem" in details:
        entry.problem = await render_field(
            plugin_name, details, "problem", bool, None, log_props
        )
    if "progress" in details:
        progress = await render_field(
            plugin_name, details, "progress", (int, float), None, log_props
        )
        if progress is not None:
            entry.progress = int(min(max(progress, 0), 100))
    return entry


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
