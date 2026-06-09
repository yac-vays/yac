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
