import datetime
import re
import shlex
import socket
import urllib.parse


async def rformat(string: str, pattern: str) -> str:
    return pattern % string


async def to_consts(lst: list[str]) -> list:
    return [{"const": l, "title": l} for l in lst]


async def to_datetime(string, fmt="%Y-%m-%d %H:%M:%S") -> datetime.datetime:
    return datetime.datetime.strptime(string, fmt)


async def to_datestr(date: datetime.datetime, fmt="%Y-%m-%d %H:%M:%S"):
    return date.strftime(fmt)


async def to_fqhn(ip: str) -> str:
    return socket.gethostbyaddr(str(ip))[0]


async def regex_replace(
    value: str = "", pattern: str = "", replacement: str = ""
) -> str:
    return re.sub(pattern, replacement, value)


async def re_escape(string: str) -> str:
    return re.escape(string)


async def shell_quote(string: str) -> str:
    return shlex.quote(str(string))


async def url_quote(string: str) -> str:
    return urllib.parse.quote(str(string), safe="")
