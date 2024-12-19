import datetime
import re
import socket
import uuid  # type: ignore

import ipaddress


def ip4net_to_fqhn(subnet: str) -> list[str]:
    return [socket.gethostbyaddr(str(ip))[0] for ip in ipaddress.IPv4Network(subnet)]


def regex_replace(value: str = "", pattern: str = "", replacement: str = "") -> str:
    return re.sub(pattern, replacement, value)


def now():
    return datetime.datetime.now()


def uuid() -> str:
    return str(uuid.uuid4())  # type: ignore
