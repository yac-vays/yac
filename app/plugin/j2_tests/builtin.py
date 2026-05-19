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


@alru_cache(maxsize=1000, ttl=300)
async def host_in_ip4ranges(hostname: str | None, ipranges: tuple[str]) -> bool:
    if hostname is None:
        return False
    try:
        result = await _get_resolver().query(hostname, "A")
    except aiodns.error.DNSError:
        return False
    if not result:
        return False
    # Match the previous semantics (one IP per host); pick the first A record.
    return len(netaddr.all_matching_cidrs(result[0].host, ipranges)) > 0


async def regex_match(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.match(pattern, value))
