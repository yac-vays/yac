"""
A log whose content document is rendered from a template, e.g. a how-to for
the entity ("how to connect to this bucket") shown with the `markdown`
renderer. Nothing is fetched: the entry and the document come from the
details, rendered with all j2 props (incl. the entity's data as `old.data`).

The log has one entry, or none if `content` renders to an empty string (so a
template can decide per entity whether there is something to show).

Details:

  content:    The document
              type: string (j2)
              default: "" -> required!
  message:    Message of the entry (e.g. a one-line summary)
              type: string (j2)
              default: ""
  time:       Timestamp of the entry
              type: string (j2)
              default: ""
  problem:    Does the entry indicate a problem (left unset if missing)
              type: bool (j2)
  progress:   Progress indicated by the entry, 0 to 100 (left unset if missing)
              type: int (j2)
"""

import logging

from app.lib import j2
from app.model import out
from app.model.err import LogContentNotFound
from app.model.err import LogSpecsError
from app.model.plg import ILog

logger = logging.getLogger(__name__)


class TemplateLog(ILog):
    async def get(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> list[out.Log]:
        d = await self.__render(details, props)
        if not d["content"].strip():
            return []

        entry = out.Log(
            name=facility,
            time=d["time"],
            message=d["message"],
            has_content=True,
        )
        if "problem" in d:
            entry.problem = d["problem"]
        if "progress" in d:
            entry.progress = int(min(max(d["progress"], 0), 100))
        return [entry]

    async def content(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> out.LogContent:
        d = await self.__render(details, props)
        if not d["content"].strip():
            raise LogContentNotFound(f'Log "{facility}" has no content')
        return out.LogContent(name=facility, time=d["time"], content=d["content"])

    async def __render(self, details: dict, props: dict) -> dict:
        try:
            d = await j2.render(details, props)
            assert isinstance(d, dict), "details must be a dict"
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(f'In details for log plugin "template": {error}') from error

        types = {
            "content": (str, ""),
            "message": (str, ""),
            "time": (str, ""),
            "problem": (bool, None),
            "progress": ((int, float), None),
        }
        for key, (typ, default) in types.items():
            if key not in d:
                if default is not None:
                    d[key] = default
                continue
            if isinstance(d[key], bool) and typ != bool:
                ok = False
            else:
                ok = isinstance(d[key], typ)
            if not ok:
                raise LogSpecsError(
                    f'In details.{key} for log plugin "template": wrong type'
                    f" {type(d[key]).__name__}"
                )
        if "content" not in details:
            raise LogSpecsError('In details for log plugin "template": `content` is required')
        return d


log = TemplateLog()
