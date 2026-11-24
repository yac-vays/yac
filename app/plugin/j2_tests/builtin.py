import re

import aiodns
import netaddr

from async_lru import alru_cache


# Single shared resolver per process. aiodns.DNSResolver does its own polling
# loop integration, so this is safe to share across coroutines.
_resolver: aiodns.DNSResolver | None = None


def _get_resolver() -> aiodns.DNSResolver:
    global _resolver
    if _resolver is None:
        _resolver = aiodns.DNSResolver()
    return _resolver


_NO_SUCH_HOST = (aiodns.error.ARES_ENOTFOUND, aiodns.error.ARES_ENODATA)


@alru_cache(maxsize=1000, ttl=300)
async def host_in_ip4ranges(hostname: str | None, ipranges: tuple[str, ...]) -> bool:
    """
    Whether one of the host's A records is in one of the ranges. `ipranges`
    must be hashable (a tuple, not a list).
    """
    if hostname is None:
        return False
    try:
        result = await _get_resolver().query(hostname, "A")
    except aiodns.error.DNSError as error:
        if error.args and error.args[0] in _NO_SUCH_HOST:
            return False
        # A resolver failure must not be cached as "not in the ranges".
        raise
    return any(netaddr.all_matching_cidrs(r.host, ipranges) for r in result or [])


async def regex_match(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.match(pattern, value))
