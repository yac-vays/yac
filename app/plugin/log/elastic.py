"""
Retreive logs from an Elastic Search server using an EQL query.

Details:

  url:        EQL search base URL
              type: string
              default: "" -> required!
  query:      EQL search query
              type: string
              default: "" -> required!
  ssl_verify: Verify the HTTPS-Certificate
              type: bool
              default: true
  timeout:    Abort request after n seconds
              type: int
              default: 5
  time:       Timestamp of the log entry
              type: string (with all j2 props + the query result as var "log")
              default: "{{ log['@timestamp'] }}"
  message:    Message of the log entry
              type: string (with all j2 props + the query result as var "log")
              default: ""
  problem:    Does the log entry indicate a problem (left unset if missing)
              type: bool (with all j2 props + the query result as var "log")
  progress:   Progress indicated by the log entry, 0 to 100 (left unset if
              missing)
              type: int (with all j2 props + the query result as var "log")
"""

import httpx

from app import consts
from app.lib import j2
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.plg import ILog


class ElasticLog(ILog):
    async def get(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> list[out.Log]:
        try:
            d = await j2.render(
                details,
                props,
                skip=r"^#/(time|message|problem|progress)$",
            )
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(
                f'In details for log plugin "elastic": {error}'
            ) from error

        try:
            async with httpx.AsyncClient(
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=d.get("ssl_verify", True),
                timeout=d.get("timeout", 5),
            ) as client:
                logs = await client.request(
                    method="GET",
                    url=f"{d.get('url', '')}/_eql/search",
                    json={"query": d.get("query", "")},
                )
            logs.raise_for_status()
        except httpx.HTTPError as error:
            raise LogError(
                "Could not run elastic log eql query: "
                f"{type(error).__name__}: {error}"
            ) from error

        result = []
        for l in logs.json().get("hits", {}).get("events", []):
            log_props = props.copy()
            log_props.update({"log": l.get("_source", {})})

            time = await self.__render(
                d, "time", str, "{{ log['@timestamp'] }}", log_props
            )
            message = await self.__render(d, "message", str, "", log_props)

            entry = out.Log(
                name=facility,
                time=time,
                message=message,
            )

            if "problem" in d:
                entry.problem = await self.__render(d, "problem", bool, False, log_props)

            if "progress" in d:
                prog = await self.__render(d, "progress", (int, float), 0, log_props)
                if prog is not None:
                    entry.progress = int(min(max(prog, 0), 100))

            result.append(entry)

        return result

    async def __render(self, data: dict, key: str, typ, default, props: dict):
        try:
            result = await j2.render(data.get(key, default), props)
            if result == consts.OMIT:
                # `omit`: the optional fields stay unset, texts empty
                return None if key in ("problem", "progress") else ""
            assert isinstance(result, typ)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(
                f'In details.{key} for log plugin "elastic": {error}'
            ) from error
        return result


log = ElasticLog()
