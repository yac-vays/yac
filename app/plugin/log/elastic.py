"""
Retrieve logs from an Elastic Search server using an EQL query.

Details:

  url:        URL of the index to search (`/_eql/search` is appended)
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

from app.lib import j2
from app.lib import log as log_lib
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
        # OSError: e.g. `ssl_verify` names a CA file that cannot be loaded
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, OSError) as error:
            raise LogError(
                "Could not run elastic log eql query: "
                f"{type(error).__name__}: {error}"
            ) from error

        try:
            events = logs.json().get("hits", {}).get("events", [])
        except (ValueError, AttributeError) as error:
            raise LogError(
                f"Elastic did not return a valid EQL result: {error}"
            ) from error

        result = []
        for l in events:
            result.append(
                await log_lib.build_entry(
                    "elastic",
                    facility,
                    d,
                    props | {"log": l.get("_source", {})},
                    time_default="{{ log['@timestamp'] }}",
                    message_default="",
                )
            )
        return result


log = ElasticLog()
