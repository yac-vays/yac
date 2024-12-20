"""
Run an HTTP(S) request.

Details:

  method:     The HTTP request method.
              type: literal[GET, HEAD, POST, PUT, PATCH, DELETE, CONNECT, OPTIONS, TRACE]
              default: GET
  url:        The request URL.
              type: string
              default: "" -> required!
  body:       The request body.
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
  error:      List of status codes considered a client-side error where the stdout and stderr will
              be printed to the user. (All other return codes will lead to a server-side error.)
              type: list[int]
              default: []
"""

import httpx

from app.lib import j2
from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import ActionSpecsError
from app.model.plg import IAction


class HttpAction(IAction):
    async def run(self, *, details: dict, props: dict) -> None:
        try:
            d = await j2.render(details, props)
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise ActionSpecsError(
                f'In details for action plugin "http": {error}'
            ) from error

        try:
            async with httpx.AsyncClient(
                headers=d.get("headers", {}),
                verify=d.get("ssl_verify", True),
                timeout=d.get("timeout", 5),
            ) as client:
                response = await client.request(
                    method=d.get("method", "GET"),
                    url=d.get("url", ""),
                    content=d.get("body"),
                )
        except httpx.HTTPError as error:
            raise ActionError(f"Could not run HTTP request: {error}") from error

        if response.status_code in d.get(
            "success", [200, 201, 202, 203, 204, 205, 206, 207, 208, 226]
        ):
            return

        if response.status_code in d.get("error", []):
            raise ActionClientError(response.content.decode("utf-8"))

        raise ActionError(response.content.decode("utf-8"))


action = HttpAction()
