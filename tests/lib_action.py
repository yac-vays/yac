"""
Tests for `lib.action` -- the dispatcher that decides which configured actions
fire for a given hook/operation and wraps plugin failures.

The real action *plugins* (shell/http) run subprocesses / network calls, so the
plugin module is replaced with a fake whose `action.run` records its calls or
raises a chosen error. This isolates the dispatch logic: hook matching, the
`force`/`arbitrary` interaction, opt-in via `op.actions`, and error wrapping
(`ActionClientError` passes through untouched, `ActionError` is re-wrapped with
context).
"""

import types

import pytest

from app.lib import action as action_lib
from app.lib import plugin
from app.model.inp import OperationRequest, NewEntity
from app.model.out import User, TypeActionHook
from app.model.err import ActionError, ActionClientError
from app.model.spc import Specs, Type, TypeAction, Request


def _op(actions=None, operation="change", name="e1", entity_name="e1"):
    return OperationRequest(
        request_headers={},
        request_ip="1.2.3.4",
        user=User(name="alice", email="a@x.com", full_name="Alice"),
        operation=operation,
        type="host",
        name=name,
        actions=actions or [],
        entity=NewEntity(name=entity_name, yaml="") if entity_name else None,
    )


def _specs(*actions):
    typ = Type.model_construct(name="host", actions=list(actions))
    return Specs.model_construct(type=typ, request=Request())


def _act(name, *, hooks, force=False, plugin_name="fake"):
    return TypeAction.model_construct(
        name=name, hooks=hooks, force=force, plugin=plugin_name, details={"k": "v"}
    )


class _Recorder:
    """Stand-in for a plugin module's `action` object."""

    def __init__(self, raise_exc=None):
        self.calls = []
        self.raise_exc = raise_exc

    async def run(self, *, details, props):
        self.calls.append({"details": details, "props": props})
        if self.raise_exc is not None:
            raise self.raise_exc


@pytest.fixture
def patch_plugin(monkeypatch):
    """Install a fake action plugin module returned by `plugin.get_module`."""

    def _install(recorder):
        module = types.SimpleNamespace(action=recorder)
        monkeypatch.setattr(plugin, "get_module", lambda kind, name: module)
        return recorder

    return _install


# ----- opt-in actions -----

async def test_opt_in_action_runs_when_requested_and_hooked(patch_plugin):
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.CHANGE_AFTER,
        _op(actions=["install"]),
        _specs(_act("install", hooks=[TypeActionHook.CHANGE_AFTER])),
    )
    assert len(rec.calls) == 1
    assert rec.calls[0]["details"] == {"k": "v"}
    assert rec.calls[0]["props"]["operation"] == "change"


async def test_opt_in_action_skipped_when_not_requested(patch_plugin):
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.CHANGE_AFTER,
        _op(actions=[]),  # not requested
        _specs(_act("install", hooks=[TypeActionHook.CHANGE_AFTER])),
    )
    assert rec.calls == []


async def test_action_skipped_when_hook_mismatches(patch_plugin):
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.CHANGE_BEFORE,
        _op(actions=["install"]),
        _specs(_act("install", hooks=[TypeActionHook.CHANGE_AFTER])),  # different hook
    )
    assert rec.calls == []


# ----- forced actions -----

async def test_forced_action_runs_without_being_requested(patch_plugin):
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.CHANGE_AFTER,
        _op(actions=[]),  # not requested, but force=True
        _specs(_act("audit", hooks=[TypeActionHook.CHANGE_AFTER], force=True)),
    )
    assert len(rec.calls) == 1


async def test_forced_action_not_auto_run_on_arbitrary_hook(patch_plugin):
    # `force` is ignored for the `arbitrary` hook: it must be explicitly opted in.
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.ARBITRARY,
        _op(actions=[], operation="arbitrary"),
        _specs(_act("audit", hooks=[TypeActionHook.ARBITRARY], force=True)),
    )
    assert rec.calls == []

    # opting in makes it run
    await action_lib.run(
        TypeActionHook.ARBITRARY,
        _op(actions=["audit"], operation="arbitrary"),
        _specs(_act("audit", hooks=[TypeActionHook.ARBITRARY], force=True)),
    )
    assert len(rec.calls) == 1


# ----- error handling -----

async def test_client_error_passes_through_unwrapped(patch_plugin):
    patch_plugin(_Recorder(raise_exc=ActionClientError("bad input")))
    with pytest.raises(ActionClientError, match="bad input"):
        await action_lib.run(
            TypeActionHook.CHANGE_AFTER,
            _op(actions=["x"]),
            _specs(_act("x", hooks=[TypeActionHook.CHANGE_AFTER])),
        )


async def test_server_error_is_wrapped_with_context(patch_plugin):
    patch_plugin(_Recorder(raise_exc=ActionError("boom")))
    with pytest.raises(ActionError) as exc:
        await action_lib.run(
            TypeActionHook.CHANGE_AFTER,
            _op(actions=["deploy"], name="host9"),
            _specs(_act("deploy", hooks=[TypeActionHook.CHANGE_AFTER])),
        )
    msg = str(exc.value)
    assert "deploy" in msg and "host9" in msg and "boom" in msg


# ----- no type / multiple actions -----

async def test_no_type_is_noop(patch_plugin):
    rec = patch_plugin(_Recorder())
    specs = Specs.model_construct(type=None, request=Request())
    await action_lib.run(TypeActionHook.CHANGE_AFTER, _op(actions=["x"]), specs)
    assert rec.calls == []


async def test_multiple_matching_actions_all_run(patch_plugin):
    rec = patch_plugin(_Recorder())
    await action_lib.run(
        TypeActionHook.CHANGE_AFTER,
        _op(actions=["a", "b"]),
        _specs(
            _act("a", hooks=[TypeActionHook.CHANGE_AFTER]),
            _act("b", hooks=[TypeActionHook.CHANGE_AFTER]),
        ),
    )
    assert len(rec.calls) == 2
