"""
Retrieve logs from an HTTP(S) endpoint that returns a JSON array of records.

Written for the bootstrap-logs service (`GET /logs/{host}/{service}` returning
`[{"time": ..., "message": ...}, ...]`), but generic enough for any endpoint
whose response is (or contains) a JSON list of log records.

Details:

  url:        Request URL (j2, all props available, e.g. "{{ name }}")
              type: string
              default: "" -> required!
  method:     HTTP method
              type: string
              default: "GET"
  headers:    Extra request headers (j2, e.g. an auth token from "env")
              type: dict
              default: {}
  array:      Dotted path to the record list inside the response body;
              empty means the body itself is the list
              type: string
              default: "" (response is the list)
              example: "hits.events"
  ssl_verify: Verify the HTTPS-Certificate
              type: bool
              default: true
  timeout:    Abort request after n seconds
              type: int
              default: 5
  time:       Timestamp of the log entry
              type: string (with all j2 props + the record as var "log")
              default: "{{ log.time }}"
  message:    Message of the log entry
              type: string (with all j2 props + the record as var "log")
              default: ""
  problem:    Does the log entry indicate a problem
              type: bool (with all j2 props + the record as var "log")
              default: false
  progress:   Progress indicated by the log entry
              type: int (with all j2 props + the record as var "log")
              default: 0
"""

import httpx

from app.lib import j2
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.plg import ILog


class HttpLog(ILog):
    async def get(
        self,
        facility: str,
        problem: bool,
        progress: bool,
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
            raise LogSpecsError(f'In details for log plugin "http": {error}') from error

        headers = d.get("headers", {}) or {}
        if not isinstance(headers, dict):
            raise LogSpecsError('In details.headers for log plugin "http": not a dict')

        try:
            async with httpx.AsyncClient(
                headers={"Accept": "application/json", **headers},
                verify=d.get("ssl_verify", True),
                timeout=d.get("timeout", 5),
            ) as client:
                logs = await client.request(
                    method=d.get("method", "GET"),
                    url=d.get("url", ""),
                )
            logs.raise_for_status()
        except httpx.HTTPError as error:
            raise LogError(f"Could not fetch logs over http: {error}") from error

        try:
            body = logs.json()
        except ValueError as error:
            raise LogError(f"Log endpoint did not return valid JSON: {error}") from error

        # Drill into the response to reach the list of records. An empty
        # `array` means the response body is already the list.
        records = body
        for part in filter(None, d.get("array", "").split(".")):
            records = records.get(part, []) if isinstance(records, dict) else []
        if not isinstance(records, list):
            records = []

        result = []
        for l in records:
            log_props = props.copy()
            log_props.update({"log": l})

            time = await self.__render(d, "time", str, "{{ log.time }}", log_props)
            message = await self.__render(d, "message", str, "", log_props)

            entry = out.Log(
                name=facility,
                time=time,
                message=message,
            )

            if problem:
                entry.problem = await self.__render(
                    d, "problem", bool, False, log_props
                )

            if progress:
                prog = await self.__render(d, "progress", (int, float), 0, log_props)
                if 0 <= prog <= 100:
                    entry.progress = int(prog)
                else:
                    entry.progress = 100 if 100 < prog else 0

            result.append(entry)

        return result

    async def __render(self, data: dict, key: str, typ, default, props: dict):
        try:
            result = await j2.render(data.get(key, default), props)
            assert isinstance(result, typ)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(
                f'In details.{key} for log plugin "http": {error}'
            ) from error
        return result


log = HttpLog()
