import re
import socket

import netaddr


def host_in_ip4ranges(hostname: str | None, ipranges: list[str]) -> bool:
    if hostname is None:
        return False
    return len(netaddr.all_matching_cidrs(socket.gethostbyname(hostname), ipranges)) > 0


def regex_match(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.match(pattern, value))
