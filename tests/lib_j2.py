"""
Tests for `lib.j2`, the Jinja2 rendering layer the specs file is evaluated with.

Focus on the contracts the rest of the app relies on: numeric coercion (incl.
the `default` fallback used by `limits`), boolean tests, string rendering with
the StrictUndefined env, and the `uses_old_data` static analysis that decides
whether a per-entity data load is needed.
"""

import pytest

from app.consts import OMIT
from app.lib import j2


async def test_render_number_basic():
    assert await j2.render_number("2 + 3", {}) == 5.0
    assert await j2.render_number("old.data.cpus", {"old": {"data": {"cpus": 8}}}) == 8.0


async def test_render_number_default_on_non_number():
    # Renders fine but is not coercible -> falls back to the default.
    assert await j2.render_number("'not-a-number'", {}, default=0.0) == 0.0
    assert await j2.render_number("'x'", {}, default=42.0) == 42.0


async def test_render_number_without_default_raises():
    with pytest.raises(j2.J2Error):
        await j2.render_number("'not-a-number'", {})


async def test_render_number_broken_expression_raises_even_with_default():
    # A Jinja error (undefined var under StrictUndefined) is a real spec error
    # and must surface regardless of the numeric default.
    with pytest.raises(j2.J2Error):
        await j2.render_number("nonexistent_variable", {}, default=0.0)


async def test_render_test_boolean():
    assert await j2.render_test("1 > 0", {}) is True
    assert await j2.render_test("1 < 0", {}) is False
    assert await j2.render_test("x == 'a'", {"x": "a"}) is True


async def test_render_str_templating():
    assert await j2.render_str("hello {{ name }}", {"name": "world"}) == "hello world"


async def test_render_str_strict_undefined_raises():
    with pytest.raises(j2.J2Error):
        await j2.render_str("{{ missing }}", {})


def test_uses_old_data():
    assert j2.uses_old_data("old.data.cpus") is True
    assert j2.uses_old_data("old['data']['cpus']") is True
    assert j2.uses_old_data("old.name") is False
    assert j2.uses_old_data("user.name") is False
    # Unparseable expressions conservatively report True (load data to be safe).
    assert j2.uses_old_data("{{{ broken") is True


# ----- render: dict/list traversal + non-string coercion -----

async def test_render_dict_and_list():
    out = await j2.render(
        {"a": "{{ x }}", "nested": {"c": "{{ y }}"}, "literal": 5}, {"x": "1", "y": "2"}
    )
    assert out == {"a": "1", "nested": {"c": "2"}, "literal": 5}
    assert await j2.render(["{{ x }}", "plain", 7], {"x": "z"}) == ["z", "plain", 7]


async def test_render_str_non_string_coercion():
    # A whole-expression `{{ ... }}` is JSON-decoded, so types survive.
    assert await j2.render_str("{{ 1 + 2 }}", {}) == 3
    assert await j2.render_str("{{ true }}", {}) is True
    assert await j2.render_str("{{ [1, 2] }}", {}) == [1, 2]
    # A mixed template stays a string.
    assert await j2.render_str("val={{ 1 }}", {}) == "val=1"
    assert await j2.render_str("plain text", {}) == "plain text"


async def test_render_print_renders_expression_as_string():
    assert await j2.render_print('"hi " ~ n', {"n": "bob"}) == "hi bob"


async def test_render_skip_leaves_matching_locs_raw():
    out = await j2.render(
        {"keep": "{{ x }}", "descr": "{{ x }}"}, {"x": "v"}, skip=r"^#/descr"
    )
    assert out == {"keep": "v", "descr": "{{ x }}"}  # skipped loc not rendered


async def test_omit_global_renders_sentinel():
    assert await j2.render_str("{{ omit }}", {}) == OMIT


async def test_render_sync_str():
    assert j2.render_sync_str("{{ a }}", {"a": "1"}) == "1"
    assert j2.render_sync_str("plain", {}) == "plain"
