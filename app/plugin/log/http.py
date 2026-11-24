"""
Retrieve logs from an HTTP(S) endpoint.

Written for the bootstrap-logs service (`GET /logs/{host}/{service}` returning
`[{"time": ..., "message": ...}, ...]`, and `GET /states/{host}/{name}`
returning a document), but generic enough for any endpoint whose response is
(or contains) a JSON list of log records, or is a plain text document.

Details:

  url:        Request URL (j2, all props available, e.g. "{{ name }}")
              type: string
              default: "" -> required!
  method:     HTTP method (e.g. "HEAD" to only get the headers of a document)
              type: string
              default: "GET"
  headers:    Extra request headers (j2, e.g. an auth token from "env")
              type: dict
              default: {}
  response:   How the response is turned into log records:
                json: the response body is (or contains, see `array`) a JSON
                      list, each element is a record
                text: the response is one record
                      {"status": 200, "headers": {...}, "body": "..."}
                      (header names in lowercase)
              type: string
              default: "json"
  array:      Dotted path to the record list inside a JSON response body;
              empty means the body itself is the list
              type: string
              default: "" (response is the list)
              example: "hits.events"
  empty_on:   HTTP status codes that mean "no log entries" (instead of an
              error), e.g. [404] for a document that does not exist (yet)
              type: list of int
              default: []
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
  problem:    Does the log entry indicate a problem (left unset if missing)
              type: bool (with all j2 props + the record as var "log")
  progress:   Progress indicated by the log entry, 0 to 100 (left unset if
              missing)
              type: int (with all j2 props + the record as var "log")
  content:    If set, the log provides a content document, fetched on demand
              (only when it is opened in the UI) with its own request. All
              request details (url, method, headers, response, empty_on,
              ssl_verify, timeout) default to the ones above, except:
                method:   default "GET"
                response: default "text"
                text:     the document (with all j2 props + the response as var
                          "log": the record for "text", the parsed body for
                          "json")
                          default: "{{ log.body }}"
                time:     its timestamp (j2 like `text`)
                          default: ""
              type: dict
              default: unset (no content)

A response larger than 1 MiB (16 MiB for a content document) is an error.
"""

from dataclasses import dataclass
from time import monotonic
import json
import logging

import httpx

from app.lib import j2
from app.lib import log as log_lib
from app.model import out
from app.model.err import LogContentNotFound
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.plg import ILog

logger = logging.getLogger(__name__)

# Request details that `content` inherits from the main request
_REQUEST_KEYS = ("url", "headers", "empty_on", "ssl_verify", "timeout")
# Limits for the (decoded) response body: the log source is not to exhaust
# the memory, and the entries are polled.
MAX_BODY_BYTES = 1024 * 1024
MAX_CONTENT_BYTES = 16 * 1024 * 1024


@dataclass
class _Response:
    """What is used of a response, its body read up to the limit."""

    status_code: int
    headers: dict
    content: bytes
    encoding: str

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def json(self):
        return json.loads(self.content)


class HttpLog(ILog):
    async def get(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> list[out.Log]:
        d = await self.__render_details(details, props)

        response = await self.__fetch(d, MAX_BODY_BYTES)
        if response is None:
            return []
        records = self.__records(d, response)

        result = []
        for record in records:
            entry = await log_lib.build_entry(
                "http",
                facility,
                d,
                props | {"log": record},
                time_default="{{ log.time }}",
                message_default="",
            )
            entry.has_content = "content" in d
            result.append(entry)

        return result

    async def content(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> out.LogContent:
        d = await self.__render_details(details, props)
        c = d.get("content")
        if c is None:
            raise LogContentNotFound(f'Log "{facility}" has no content')
        if not isinstance(c, dict):
            raise LogSpecsError('In details.content for log plugin "http": not a dict')

        request = {k: d[k] for k in _REQUEST_KEYS if k in d}
        request |= {"method": "GET", "response": "text"} | c

        response = await self.__fetch(request, MAX_CONTENT_BYTES)
        if response is None:
            raise LogContentNotFound(f'Log "{facility}" has no content (yet)')
        if request.get("response") == "text":
            record = self.__records(request, response)[0]
        else:
            try:
                record = response.json()
            except ValueError as error:
                raise LogError(
                    f"Log content endpoint did not return valid JSON: {error}"
                ) from error

        log_props = props | {"log": record}
        text = await log_lib.render_field(
            "http", d, "content.text", str, "{{ log.body }}", log_props
        )
        time = await log_lib.render_field("http", d, "content.time", str, "", log_props)
        return out.LogContent(name=facility, time=time, content=text)

    async def __render_details(self, details: dict, props: dict) -> dict:
        # The per-record fields are rendered later, with the record as `log`
        try:
            d = await j2.render(
                details,
                props,
                skip=r"^#/(time|message|problem|progress|content/text|content/time)$",
            )
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(f'In details for log plugin "http": {error}') from error

        if not isinstance(d.get("headers", {}) or {}, dict):
            raise LogSpecsError('In details.headers for log plugin "http": not a dict')
        if d.get("response", "json") not in ("json", "text"):
            raise LogSpecsError(
                'In details.response for log plugin "http": must be "json" or "text"'
            )
        return d

    def __records(self, d: dict, response: _Response) -> list:
        if d.get("response", "json") == "text":
            return [
                {
                    "status": response.status_code,
                    "headers": response.headers,
                    "body": response.text,
                }
            ]

        try:
            body = response.json()
        except ValueError as error:
            raise LogError(f"Log endpoint did not return valid JSON: {error}") from error

        # Drill into the response to reach the list of records. An empty
        # `array` means the response body is already the list.
        records = body
        for part in filter(None, d.get("array", "").split(".")):
            records = records.get(part, []) if isinstance(records, dict) else []
        if not isinstance(records, list):
            records = []
        return records

    async def __fetch(self, d: dict, max_bytes: int) -> _Response | None:
        """
        The response (LogError if its body is larger than `max_bytes`), or
        None if its status is one of `empty_on`.
        """
        headers = d.get("headers", {}) or {}
        empty_on = d.get("empty_on", []) or []
        accept = "application/json" if d.get("response", "json") == "json" else "*/*"
        # A failure while connecting (DNS, TCP, TLS) means the request was never
        # sent, so it is always safe to retry once. This hides transient network
        # hiccups that would otherwise show up as an empty log in the UI.
        for attempt in (1, 2):
            start = monotonic()
            try:
                async with httpx.AsyncClient(
                    headers={"Accept": accept, **headers},
                    verify=d.get("ssl_verify", True),
                    timeout=d.get("timeout", 5),
                ) as client, client.stream(
                    method=d.get("method", "GET"),
                    url=d.get("url", ""),
                ) as response:
                    if response.status_code in empty_on:
                        return None
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content += chunk
                        if len(content) > max_bytes:
                            raise LogError(
                                "Could not fetch logs over http: response is"
                                f" larger than {max_bytes} bytes"
                            )
                    return _Response(
                        status_code=response.status_code,
                        headers={k.lower(): v for k, v in response.headers.items()},
                        content=bytes(content),
                        encoding=response.encoding or "utf-8",
                    )
            # OSError: e.g. `ssl_verify` names a CA file that cannot be loaded
            except (httpx.InvalidURL, TypeError, OSError) as error:
                raise LogError(f"Could not fetch logs over http: {error}") from error
            except httpx.HTTPError as error:
                # Timeouts stringify to "", so always name the exception type.
                reason = (
                    f"{type(error).__name__} after "
                    f"{monotonic() - start:.1f}s: {error}"
                )
                if attempt == 1 and isinstance(
                    error, (httpx.ConnectError, httpx.ConnectTimeout)
                ):
                    logger.warning("Retrying log fetch over http: %s", reason)
                    continue
                raise LogError(f"Could not fetch logs over http: {reason}") from error
        raise AssertionError("unreachable")


log = HttpLog()
