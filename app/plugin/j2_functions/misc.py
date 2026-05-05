import re
import socket
import uuid as module_uuid

from jinja2 import pass_context
import ipaddress

from app.model.err import RequestError


async def ip4net_to_fqhn(subnet: str) -> list[str]:
    return [socket.gethostbyaddr(str(ip))[0] for ip in ipaddress.IPv4Network(subnet)]


async def regex_replace(
    value: str = "", pattern: str = "", replacement: str = ""
) -> str:
    return re.sub(pattern, replacement, value)


async def uuid() -> str:
    return str(module_uuid.uuid4())


@pass_context
async def re_next_int(
    ctx: dict,
    pattern: str = r"^(.*)$",
    *,
    limit: int = 0,
) -> int:
    """
    Takes the list of existing names, filters them by the given regex
    and increments the highest number by one.
    If it fails or exceeds the limit (if defined !=0), it will raise a
    RequestError error.
    """

    p = re.compile(pattern)
    n = []
    for name in ctx.get("old", {}).get("list", []):
        r = p.search(name)
        if r:
            try:
                n.append(int(r.group(1)))
            except:  # pylint: disable=bare-except
                pass  # accept matches that have no group 1 or cannot be casted to int

    if limit != 0 and len(n) == limit:
        raise RequestError(f"Maximum number of {limit} reached!")

    return max(n) + 1 if n else 1
