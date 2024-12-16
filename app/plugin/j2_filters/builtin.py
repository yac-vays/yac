import datetime
import re
import socket

from app.model.err import RequestError


def rformat(string: str, pattern: str) -> str:
    return pattern % string


def to_consts(lst: list[str]) -> list:
    return [{"const": l, "title": l} for l in lst]


def to_datetime(string, format="%Y-%m-%d %H:%M:%S"):
    return datetime.datetime.strptime(string, format)


def to_fqhn(ip: str) -> str:
    return socket.gethostbyaddr(str(ip))[0]


def regex_replace(value: str = "", pattern: str = "", replacement: str = "") -> str:
    return re.sub(pattern, replacement, value)


def re_escape(string: str) -> str:
    return re.escape(string)


def next_int_by_regex(
    names: list[str],
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
    for name in names:
        r = p.search(name)
        if r:
            try:
                n.append(int(r.group(1)))
            except:
                pass  # accept matches that have no group 1 or cannot be casted to int

    result = max(n) + 1

    if limit != 0 and result > limit:
        # TODO not working yet
        raise RequestError(f"Number of {limit} reached!")

    return result
