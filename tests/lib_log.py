"""
Tests for `lib.log` -- aggregates audit-trail entries from every configured log
plugin for a type. The real plugins (file/elastic) touch the filesystem /
network, so they are replaced with a fake `log.get`. Key behaviours: results
from several log specs are concatenated (in specs order), a `LogError` from one
plugin is swallowed (logged, not raised) so one broken log source cannot break
the others, and all sources are fetched concurrently (not serialised).
"""

import asyncio
import types

import pytest

from app.lib import log as log_lib
from app.lib import plugin
from app.model.inp import OperationRequest
from app.model.out import User, Log, TypeLog
from app.model.err import LogError
from app.model.spc import Specs, Type, Request


class _SpcLog(TypeLog):
    plugin: str
    details: dict = {}
    problem: bool = False
    progress: bool = False


def _op(name="e1"):
    return OperationRequest(
        request_headers={},
        request_ip="1.2.3.4",
        user=User(name="alice", email="a@x.com", full_name="Alice"),
        operation="read",
        type="host",
        name=name,
        actions=[],
        entity=None,
    )


def _specs(*logs):
    typ = Type.model_construct(name="host", logs=list(logs))
    return Specs.model_construct(type=typ, request=Request())


def _logspec(name, plugin_name="fake"):
    return _SpcLog.model_construct(
        name=name, title=name, plugin=plugin_name, details={}, problem=False,
        progress=False,
    )


def _entry(name, msg):
    return Log(name=name, message=msg, time="2024-01-01")


class _FakeLog:
    def __init__(self, entries=None, raise_exc=None):
        self.entries = entries or []
        self.raise_exc = raise_exc
        self.calls = 0

    async def get(self, facility, problem, progress, *, details, props):
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.entries


@pytest.fixture
def patch_plugins(monkeypatch):
    """Map plugin-name -> fake `log` object, dispatched by `get_module`."""

    def _install(mapping):
        def _get_module(kind, name):
            return types.SimpleNamespace(log=mapping[name])

        monkeypatch.setattr(plugin, "get_module", _get_module)
        return mapping

    return _install


async def test_concatenates_entries_from_all_log_specs(patch_plugins):
    patch_plugins({
        "p1": _FakeLog([_entry("install", "a")]),
        "p2": _FakeLog([_entry("boot", "b"), _entry("boot", "c")]),
    })
    logs = await log_lib.get(
        _op(), _specs(_logspec("install", "p1"), _logspec("boot", "p2"))
    )
    assert [l.message for l in logs] == ["a", "b", "c"]


async def test_log_error_from_one_plugin_is_swallowed(patch_plugins):
    patch_plugins({
        "good": _FakeLog([_entry("ok", "kept")]),
        "bad": _FakeLog(raise_exc=LogError("source down")),
    })
    logs = await log_lib.get(
        _op(), _specs(_logspec("ok", "good"), _logspec("broken", "bad"))
    )
    # the broken source contributes nothing; the working one survives
    assert [l.message for l in logs] == ["kept"]


async def test_no_type_returns_empty(patch_plugins):
    patch_plugins({})
    specs = Specs.model_construct(type=None, request=Request())
    assert await log_lib.get(_op(), specs) == []


async def test_no_logs_configured_returns_empty(patch_plugins):
    patch_plugins({})
    assert await log_lib.get(_op(), _specs()) == []


class _BarrierLog:
    """Records how many `get` calls are in flight at once."""

    def __init__(self, state):
        self.state = state

    async def get(self, facility, problem, progress, *, details, props):
        self.state["inflight"] += 1
        self.state["max_inflight"] = max(
            self.state["max_inflight"], self.state["inflight"]
        )
        try:
            # Block until every source has entered `get`; if `lib.log` awaited
            # sources one-by-one this barrier would never be released and the
            # wait_for below would time out.
            await asyncio.wait_for(self.state["all_started"].wait(), timeout=2)
        finally:
            self.state["inflight"] -= 1
        return [_entry(facility, facility)]


async def test_sources_are_fetched_concurrently(patch_plugins):
    state = {"inflight": 0, "max_inflight": 0, "all_started": asyncio.Event()}

    async def _release_when_all_started():
        # Two sources -> release the barrier once both are in flight.
        while state["inflight"] < 2:
            await asyncio.sleep(0)
        state["all_started"].set()

    patch_plugins({"p1": _BarrierLog(state), "p2": _BarrierLog(state)})

    releaser = asyncio.ensure_future(_release_when_all_started())
    logs = await log_lib.get(
        _op(), _specs(_logspec("a", "p1"), _logspec("b", "p2"))
    )
    await releaser

    # Both sources were in `get` simultaneously (would be 1 if serialised),
    # and results keep specs order.
    assert state["max_inflight"] == 2
    assert [l.message for l in logs] == ["a", "b"]
