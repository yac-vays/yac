"""
Retreive logs from an Elastic Search server using an EQL query.

Details:
              
  url:        EQL search base URL
              type: format-string (with all props available)
              default: null -> required!
  query:      EQL search query
              type: format-string (with all props available)
              default: null -> required!
  ssl_verify: Verify the HTTPS-Certificate
              type: bool
              default: true
  timeout:    Abort request after n seconds
              type: int
              default: 5
  time:       Timestamp of the log entry
              type: jinja2-string (with all props + the query result as var "log")
              default: 'log["@timestamp"]'
  message:    Message of the log entry
              type: jinja2-string (with all props + the query result as var "log")
              default: ''
  problem:    Does the log entry indicate a problem
              type: jinja2-bool (with all props + the query result as var "log")
              default: 'false'
  progress:   Progress indicated by the log entry
              type: jinja2-int (with all props + the query result as var "log")
              default: '0'
"""

import httpx

from app.lib import j2
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError


async def get(
    log_name: str, problem: bool, progress: bool, *, details: dict, props: dict
) -> list[out.Log]:
    try:
        url = details["url"].format(**props)
        query = details["query"].format(**props)
    except (KeyError, IndexError, ValueError, AttributeError) as error:
        raise LogSpecsError(
            f"In elastic log plugin details.url/query: {error}"
        ) from error

    ssl = details.get("ssl_verify", True)
    timeout = details.get("timeout", 5)

    try:
        async with httpx.AsyncClient(
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            verify=ssl,
            timeout=timeout,
        ) as client:
            logs = await client.request(
                method="GET",
                url=f"{url}/_eql/search",
                json={"query": query},
            )
        logs.raise_for_status()
    except httpx.HTTPError as error:
        raise LogError(f"Could not run elastic log eql query: {error}") from error

    result = []
    for l in logs.json().get("hits", {}).get("events", []):
        log_props = props.copy()
        log_props.update({"log": l.get("_source", {})})

        try:
            entry = out.Log(
                name=log_name,
                time=j2.render_print(
                    details.get("time", 'log["@timestamp"]'), log_props
                ),
                message=j2.render_print(details.get("message", '""'), log_props),
            )
        except j2.J2Error as error:
            raise LogSpecsError(
                f"In elastic log plugin details.time/message: {error}"
            ) from error

        if problem:
            try:
                entry.problem = j2.render_test(
                    details.get("problem", "false"), log_props
                )
            except j2.J2Error as error:
                raise LogSpecsError(
                    f"In elastic log plugin details.problem: {error}"
                ) from error

        if progress:
            try:
                percent = int(j2.render_print(details.get("progress", "0"), log_props))
            except j2.J2Error as error:
                raise LogSpecsError(
                    f"In elastic log plugin details.progress: {error}"
                ) from error

            if 0 <= percent <= 100:
                entry.progress = percent
            else:
                entry.progress = 100 if 100 < percent else 0

        result.append(entry)

    return result
