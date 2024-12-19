import re
import socket

import netaddr

from async_lru import alru_cache


@alru_cache(maxsize=1000, ttl=300)
async def host_in_ip4ranges(hostname: str | None, ipranges: list[str]) -> bool:
    if hostname is None:
        return False
    # TODO switch to non-blocking https://github.com/aio-libs/aiodns
    return len(netaddr.all_matching_cidrs(socket.gethostbyname(hostname), ipranges)) > 0


async def regex_match(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.match(pattern, value))
