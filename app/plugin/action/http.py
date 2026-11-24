"""
Run an HTTP(S) request.

Details:

  method:     The HTTP request method.
              type: literal[GET, HEAD, POST, PUT, PATCH, DELETE, CONNECT, OPTIONS, TRACE]
              default: GET
  url:        The request URL.
              type: string
              default: "" -> required!
  body:       The request body (objects and lists are sent as JSON).
              type: string
              default: ""
  headers:    The request headers.
              type: object
              default: {}
  timeout:    Abort request after n seconds
              type: int
              default: 5
  ssl_verify: Verify the HTTPS-Certificate
              type: bool
              default: true
  success:    List of status codes considered a success.
              type: list[int]
              default: [200, 201, 202, 203, 204, 205, 206, 207, 208, 226]
  error:      List of status codes considered a client-side error where the response body will
              be shown to the user. (All other status codes will lead to a server-side error.)
              type: list[int]
              default: []

Of the response body, at most 1 MiB is read.
"""

import json

import httpx

from app.lib import j2
from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import ActionSpecsError
from app.model.plg import IAction


# The response body is only used for error messages.
MAX_BODY_BYTES = 1024 * 1024


def _as_str(value: object, loc: str) -> str:
    """
    j2 renders full-expression templates to their JSON value, so a rendered
    value may be a number or bool.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ActionSpecsError(
        f'In details for action plugin "http": {loc} must render to a single'
        f" string (got {type(value).__name__})"
    )


class HttpAction(IAction):
    async def run(self, *, details: dict, props: dict) -> None:
        try:
            d = await j2.render(details, props)
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise ActionSpecsError(
                f'In details for action plugin "http": {error}'
            ) from error

        body = d.get("body")
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        elif body is not None:
            body = _as_str(body, "body")
        headers = d.get("headers") or {}
        if not isinstance(headers, dict):
            raise ActionSpecsError(
                'In details for action plugin "http": headers must be an object'
            )
        headers = {str(k): _as_str(v, f"headers.{k}") for k, v in headers.items()}

        try:
            async with httpx.AsyncClient(
                headers=headers,
                verify=d.get("ssl_verify", True),
                timeout=d.get("timeout", 5),
            ) as client, client.stream(
                method=d.get("method", "GET"),
                url=d.get("url", ""),
                content=body,
            ) as response:
                status = response.status_code
                answer = bytearray()
                async for chunk in response.aiter_bytes():
                    answer += chunk
                    if len(answer) >= MAX_BODY_BYTES:
                        break
        # OSError: e.g. `ssl_verify` names a CA file that cannot be loaded
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, OSError) as error:
            raise ActionError(
                f"Could not run HTTP request: {type(error).__name__}: {error}"
            ) from error

        if status in d.get(
            "success", [200, 201, 202, 203, 204, 205, 206, 207, 208, 226]
        ):
            return

        message = answer[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
        if status in d.get("error", []):
            raise ActionClientError(message)

        raise ActionError(message)


action = HttpAction()
