"""
Tests for the built-in Jinja2 plugin functions/filters/tests that specs files
use. These are pure helpers, so they are called directly. Network-dependent ones
(DNS: `to_fqhn`, `host_in_ip4ranges` resolution) are only exercised on their
no-network branches.
"""

import re
import uuid as uuidlib

import pytest

from app.model.err import RequestError
from app.plugin.j2_filters import builtin as filt
from app.plugin.j2_functions import date as datef
from app.plugin.j2_functions import misc
from app.plugin.j2_tests import builtin as tst


# ----- filters -----

async def test_rformat_and_to_consts():
    assert await filt.rformat("host", "prefix-%s") == "prefix-host"
    assert await filt.to_consts(["a", "b"]) == [
        {"const": "a", "title": "a"},
        {"const": "b", "title": "b"},
    ]
    assert await filt.to_consts([]) == []


async def test_datetime_roundtrip():
    dt = await filt.to_datetime("2024-03-10 12:30:00")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 3, 10, 12, 30)
    assert await filt.to_datestr(dt) == "2024-03-10 12:30:00"
    assert await filt.to_datestr(dt, "%Y/%m/%d") == "2024/03/10"


async def test_to_datetime_bad_input_raises():
    with pytest.raises(ValueError):
        await filt.to_datetime("not-a-date")


async def test_regex_replace_and_escape():
    assert await filt.regex_replace("a-b-c", "-", "_") == "a_b_c"
    assert await filt.regex_replace("abc", "x", "y") == "abc"  # no match
    assert await filt.re_escape("a.b*c") == r"a\.b\*c"


async def test_quoting_filters():
    assert await filt.shell_quote("a b") == "'a b'"
    assert await filt.shell_quote("safe") == "safe"
    assert await filt.url_quote("a/b c") == "a%2Fb%20c"
    assert await filt.url_quote(42) == "42"  # non-str coerced


# ----- tests -----

async def test_regex_match():
    assert await tst.regex_match("hello", "h.*o") is True
    assert await tst.regex_match("hello", "^x") is False
    assert await tst.regex_match(None, ".*") is False  # None short-circuits


async def test_host_in_ip4ranges_none_host():
    # No DNS needed: a None hostname is False before any resolution.
    assert await tst.host_in_ip4ranges(None, ("10.0.0.0/8",)) is False


# ----- misc functions -----

async def test_uuid_is_valid_v4():
    val = await misc.uuid()
    parsed = uuidlib.UUID(val)
    assert parsed.version == 4


async def test_re_next_int_increments_and_defaults():
    assert await misc.re_next_int({}, r"^(.*)$") == 1  # empty list
    assert await misc.re_next_int({"old": {"list": []}}, r"^(.*)$") == 1
    assert await misc.re_next_int({"old": {"list": ["1", "5", "3"]}}, r"^(.*)$") == 6
    # Non-matching / non-integer names are ignored.
    ctx = {"old": {"list": ["host1", "host7", "garbage", "host3"]}}
    assert await misc.re_next_int(ctx, r"^host(\d+)$") == 8


async def test_re_next_int_limit_raises():
    ctx = {"old": {"list": ["1", "2"]}}
    with pytest.raises(RequestError):
        await misc.re_next_int(ctx, r"^(.*)$", limit=2)
    # limit 0 disables the check.
    assert await misc.re_next_int(ctx, r"^(.*)$", limit=0) == 3


# ----- date functions -----

def _matcher(pattern: str):
    rx = re.compile(pattern)
    return lambda s: bool(rx.match(s))


async def test_date_range_within_one_month():
    m = _matcher(await datef.date_range_pattern("2024-03-10", days=4))
    assert all(m(f"2024-03-{d:02d}") for d in range(10, 15))  # 10..14 inclusive
    assert not m("2024-03-09")
    assert not m("2024-03-15")


async def test_date_range_crossing_month_boundary():
    m = _matcher(await datef.date_range_pattern("2024-03-28", days=5))
    for good in ("2024-03-28", "2024-03-31", "2024-04-01", "2024-04-02"):
        assert m(good), good
    for bad in ("2024-03-27", "2024-04-03", "2024-05-01"):
        assert not m(bad), bad


async def test_date_range_weeks_and_years_units():
    # weeks and years feed the same delta; just assert range size via endpoints.
    m = _matcher(await datef.date_range_pattern("2024-01-01", weeks=1))
    assert m("2024-01-01") and m("2024-01-08") and not m("2024-01-09")


async def test_date_range_zero_delta_raises():
    with pytest.raises(ValueError):
        await datef.date_range_pattern("2024-03-10", days=0)


async def test_timedelta_function():
    td = await datef.timedelta(days=2, hours=3)
    assert td.days == 2 and td.seconds == 3 * 3600


# ----- regressions -----

async def test_re_next_int_limit_also_applies_when_already_exceeded():
    ctx = {"old": {"list": ["1", "2", "3"]}}
    with pytest.raises(RequestError):
        await misc.re_next_int(ctx, r"^(.*)$", limit=2)


class _Record:
    def __init__(self, host):
        self.host = host


class _Resolver:
    def __init__(self, answer):
        self.answer = answer
        self.queries = 0

    async def query(self, hostname, qtype):
        self.queries += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_host_in_ip4ranges_checks_all_records(monkeypatch):
    resolver = _Resolver([_Record("192.0.2.1"), _Record("10.1.2.3")])
    monkeypatch.setattr(tst, "_get_resolver", lambda: resolver)
    assert await tst.host_in_ip4ranges("multi.example", ("10.0.0.0/8",)) is True
    assert await tst.host_in_ip4ranges("multi.example", ("172.16.0.0/12",)) is False


async def test_host_in_ip4ranges_does_not_cache_resolver_failures(monkeypatch):
    import aiodns

    resolver = _Resolver(aiodns.error.DNSError(aiodns.error.ARES_ETIMEOUT, "timeout"))
    monkeypatch.setattr(tst, "_get_resolver", lambda: resolver)
    for _ in range(2):
        with pytest.raises(aiodns.error.DNSError):
            await tst.host_in_ip4ranges("flaky.example", ("10.0.0.0/8",))
    assert resolver.queries == 2

    resolver.answer = aiodns.error.DNSError(aiodns.error.ARES_ENOTFOUND, "nxdomain")
    assert await tst.host_in_ip4ranges("gone.example", ("10.0.0.0/8",)) is False


async def test_fqhn_lookups_run_off_the_event_loop(monkeypatch):
    import socket
    import threading

    main = threading.get_ident()
    threads = set()

    def lookup(ip):
        threads.add(threading.get_ident())
        if ip.endswith(".2"):
            raise socket.herror("no PTR")
        return (f"host-{ip.rsplit('.', 1)[1]}.example", [], [ip])

    monkeypatch.setattr(socket, "gethostbyaddr", lookup)
    assert await filt.to_fqhn("192.0.2.1") == "host-1.example"
    # network / broadcast address are no hosts, missing PTRs are skipped
    assert await misc.ip4net_to_fqhn("192.0.2.0/30") == ["host-1.example"]
    assert main not in threads


def test_unix_password_hash_with_rounds():
    from app.lib import plugin

    check = plugin.get_functions("schema_formats")["unix_password_hash"]
    assert check("$6$rounds=656000$abcdefghijklmnop$" + "a" * 86) is True
    assert check("$5$rounds=535000$abcdefghijklmnop$" + "a" * 43) is True
    assert check("$6$saltsalt$" + "a" * 86) is True
    assert check("$6$rounds=x$salt$" + "a" * 86) is False
    assert check("$6$salt$with$dollar$" + "a" * 86) is False


def test_plain_log_format_separates_the_traceback():
    import logging
    import sys

    from app.lib import plugin

    formatter = plugin.get_module("format", "plain").formatter
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "msg", None, sys.exc_info())
    assert " msg\nTraceback (most recent call last):" in formatter.format(record)
