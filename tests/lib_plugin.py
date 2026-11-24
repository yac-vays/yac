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
import sys

import pytest

from app.lib import plugin
from app.model.err import PluginError


def _is_fn(obj) -> bool:
    """Is `obj`, as a public member of this very module, a plugin function?"""
    return plugin._is_plugin_function("member", obj, sys.modules[__name__])


# ----- _is_plugin_function -----

def test_is_plugin_function_accepts_plain_and_async():
    def plain():
        ...

    async def coro():
        ...

    assert _is_fn(plain) is True
    assert _is_fn(coro) is True
    assert _is_fn(lambda: None) is True


def test_get_functions_only_exposes_the_plugins_own_public_functions():
    fns = plugin.get_functions("j2_functions")
    assert "re_next_int" in fns and "uuid" in fns
    assert "pass_context" not in fns  # imported by misc.py
    tests = plugin.get_functions("j2_tests")
    assert "alru_cache" not in tests
    assert "_get_resolver" not in tests


def test_is_plugin_function_accepts_cache_wrapped():
    @functools.lru_cache
    def cached():
        ...

    # lru_cache returns a callable wrapper object, not a function; it must still
    # be recognised via its __wrapped__ attribute.
    assert _is_fn(cached) is True


def test_is_plugin_function_rejects_non_functions():
    class C:
        ...

    assert _is_fn(C) is False        # a class is callable but not a fn
    assert _is_fn(42) is False
    assert _is_fn("x") is False


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


def test_get_modules_returns_same_objects_for_all_call_arities():
    # Regression: the lru_cache used to sit on get_modules itself, so
    # get_modules("x"), get_modules("x", None) and get_modules("x", require=...)
    # were distinct cache keys, each re-importing every plugin and yielding
    # duplicate module (and processor) instances.
    a = plugin.get_modules("json_schema")
    b = plugin.get_modules("json_schema", None)
    c = plugin.get_modules("json_schema", require=("required_defaults",))
    assert a["required_defaults"] is b["required_defaults"]
    assert a["required_defaults"] is c["required_defaults"]
    assert a["required_defaults"].processor is b["required_defaults"].processor


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


def test_get_sorted_required_defaults_runs_after_yac_optional():
    # required_defaults needs the required list that yac_optional builds; a tie
    # in order numbers would leave their relative order to the filesystem
    # enumeration of the plugin dir (non-deterministic across deployments).
    late = plugin.get_sorted("json_schema", "processor", late=True)
    names = [type(p).__name__ for p in late]
    assert names.index("YacOptional") < names.index("RequiredDefaults")
    orders = {type(p).__name__: p.order()[1] for p in late}
    assert orders["YacOptional"] < orders["RequiredDefaults"]


def test_is_plugin_function_rejects_private_and_imported():
    def own():
        ...

    assert plugin._is_plugin_function("_own", own, sys.modules[__name__]) is False
    assert _is_fn(functools.reduce) is False
    assert _is_fn(pytest.raises) is False
