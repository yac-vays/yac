"""
Tests for `lib.plugin` -- the convention-based loader that discovers plugin
modules/functions under `app/plugin/{kind}/`. The loader itself is exercised
against the real built-in plugins (so the tests double as a smoke check that the
shipped plugins import cleanly).

The most important piece is `_is_plugin_function`: it must accept not just bare
functions but also functions wrapped by a caching decorator (lru_cache /
alru_cache) which `inspect.isfunction` rejects. Missing that dropped decorated
builtins like `host_in_ip4ranges` (see the regression guard below).
"""

import functools

import pytest

from app.lib import plugin
from app.model.err import PluginError


# ----- _is_plugin_function -----

def test_is_plugin_function_accepts_plain_and_async():
    def plain():
        ...

    async def coro():
        ...

    assert plugin._is_plugin_function(plain) is True
    assert plugin._is_plugin_function(coro) is True
    assert plugin._is_plugin_function(lambda: None) is True


def test_is_plugin_function_accepts_cache_wrapped():
    @functools.lru_cache
    def cached():
        ...

    # lru_cache returns a callable wrapper object, not a function; it must still
    # be recognised via its __wrapped__ attribute.
    assert plugin._is_plugin_function(cached) is True


def test_is_plugin_function_rejects_non_functions():
    class C:
        ...

    assert plugin._is_plugin_function(C) is False        # a class is callable but not a fn
    assert plugin._is_plugin_function(42) is False
    assert plugin._is_plugin_function("x") is False


# ----- get_functions (regression: decorated builtins survive) -----

def test_get_functions_includes_alru_cached_builtin():
    # host_in_ip4ranges is wrapped in @alru_cache; it must not be dropped.
    fns = plugin.get_functions("j2_tests")
    assert "host_in_ip4ranges" in fns
    assert "regex_match" in fns


def test_get_functions_is_cached():
    assert plugin.get_functions("j2_filters") is plugin.get_functions("j2_filters")


# ----- get_module / get_modules error paths -----

def test_get_module_missing_raises_plugin_error():
    with pytest.raises(PluginError):
        plugin.get_module("repo", "does_not_exist")


def test_get_module_loads_real_plugin():
    mod = plugin.get_module("repo", "git_direct")
    assert hasattr(mod, "GitRepo")


def test_get_modules_require_missing_raises():
    with pytest.raises(PluginError):
        plugin.get_modules("repo", require=("totally_absent_plugin",))


def test_get_modules_require_present_ok():
    mods = plugin.get_modules("repo", require=("git_direct",))
    assert "git_direct" in mods


# ----- get_sorted ordering + late/early partition -----

def test_get_sorted_partitions_and_orders():
    early = plugin.get_sorted("json_schema", "processor", late=False)
    late = plugin.get_sorted("json_schema", "processor", late=True)

    # every returned processor agrees with the partition it was placed in
    assert all(p.order()[0] is False for p in early)
    assert all(p.order()[0] is True for p in late)

    # within a partition, order numbers are non-decreasing
    nums = [p.order()[1] for p in early]
    assert nums == sorted(nums)
