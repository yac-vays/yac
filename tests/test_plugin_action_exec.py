"""
Tests for the `exec` action plugin -- runs a program from an argv list where
every element is j2-rendered individually (replacing the former `shell`
plugin, whose j2-rendered command *string* ran through a shell and was
injectable via token claims / entity data).

These run real subprocesses (cheap ones: echo / python -c), because the whole
point of the plugin is the process-spawning boundary: argv stays argv, env and
stdin arrive as handed over, and return codes map to the success/error/other
outcome exactly like before.
"""

import os
import sys

import pytest

from app.lib import plugin
from app.model.err import ActionClientError, ActionError, ActionSpecsError

exec_plugin = plugin.get_module("action", "exec").action

# Every payload a shell would interpret; argv must pass it through literally.
HOSTILE = "\"; touch /tmp/exec-plugin-pwned; $(id) | tee -a /etc/passwd & '"

PROPS = {"user": {"name": "u1", "full_name": HOSTILE}, "name": "web01"}


def _echo_details(**overrides):
    # Exit code 3 routes the child's stdout into ActionClientError, making
    # the output assertable without a success-path return value.
    d = {
        "command": [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); sys.exit(3)",
            "{{ user.full_name }}",
        ],
        "error": [3],
    }
    d.update(overrides)
    return d


# ----- the injection regression -----

async def test_hostile_value_stays_one_literal_argument():
    with pytest.raises(ActionClientError) as excinfo:
        await exec_plugin.run(details=_echo_details(), props=PROPS)
    # The child received the hostile value verbatim as a single argument;
    # nothing was interpreted, no side effects happened.
    assert str(excinfo.value).strip() == HOSTILE
    assert not os.path.exists("/tmp/exec-plugin-pwned")


# ----- return-code mapping (unchanged semantics) -----

async def test_success_code_returns_none():
    details = {"command": [sys.executable, "-c", "raise SystemExit(5)"], "success": [5]}
    assert await exec_plugin.run(details=details, props=PROPS) is None


async def test_error_code_raises_client_error_with_output():
    details = {
        "command": [sys.executable, "-c", "print('user-facing'); raise SystemExit(7)"],
        "error": [7],
    }
    with pytest.raises(ActionClientError, match="user-facing"):
        await exec_plugin.run(details=details, props=PROPS)


async def test_other_code_raises_server_error():
    details = {"command": [sys.executable, "-c", "raise SystemExit(9)"]}
    with pytest.raises(ActionError):
        await exec_plugin.run(details=details, props=PROPS)


async def test_missing_program_raises_server_error():
    details = {"command": ["/nonexistent/program"]}
    with pytest.raises(ActionError, match="Could not execute"):
        await exec_plugin.run(details=details, props=PROPS)


# ----- env handover -----

async def test_env_map_is_rendered_and_passed():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import os; print(os.environ['YAC__USER__FULL_NAME']); raise SystemExit(3)",
        ],
        "env": {"YAC__USER__FULL_NAME": "{{ user.full_name }}"},
        "error": [3],
    }
    with pytest.raises(ActionClientError) as excinfo:
        await exec_plugin.run(details=details, props=PROPS)
    assert str(excinfo.value).strip() == HOSTILE


async def test_base_env_is_minimal():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import os; print(sorted(os.environ)); raise SystemExit(3)",
        ],
        "error": [3],
    }
    with pytest.raises(ActionClientError) as excinfo:
        await exec_plugin.run(details=details, props=PROPS)
    listed = str(excinfo.value)
    assert "'PATH'" in listed
    for leaked in ("YAC_", "SECRET", "TOKEN"):
        assert leaked not in listed


# ----- stdin handover -----

async def test_stdin_string_is_piped():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import sys; print(sys.stdin.read()); raise SystemExit(3)",
        ],
        "stdin": "hi {{ user.name }}",
        "error": [3],
    }
    with pytest.raises(ActionClientError, match="hi u1"):
        await exec_plugin.run(details=details, props=PROPS)


async def test_stdin_object_is_json_encoded():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import sys, json; d = json.load(sys.stdin); print(d['name']); raise SystemExit(3)",
        ],
        "stdin": "{{ {'name': name} }}",
        "error": [3],
    }
    with pytest.raises(ActionClientError, match="web01"):
        await exec_plugin.run(details=details, props=PROPS)


async def test_no_stdin_means_closed_stdin():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import sys; print(repr(sys.stdin.read())); raise SystemExit(3)",
        ],
        "error": [3],
    }
    with pytest.raises(ActionClientError, match="''"):
        await exec_plugin.run(details=details, props=PROPS)


# ----- specs validation -----

async def test_string_command_is_rejected_with_migration_hint():
    # The old `shell` plugin format must fail loudly, pointing at argv.
    details = {"command": 'echo "Hello {{ user.full_name }}"'}
    with pytest.raises(ActionSpecsError, match="argv"):
        await exec_plugin.run(details=details, props=PROPS)


@pytest.mark.parametrize(
    "details",
    [
        {"command": []},
        {},
        {"command": [["nested"]]},
        {"command": ["true"], "env": {"BAD NAME": "x"}},
        {"command": ["true"], "env": {"X": "{{ user }}"}},  # renders to a dict
    ],
)
async def test_invalid_details_raise_specs_error(details):
    with pytest.raises(ActionSpecsError):
        await exec_plugin.run(details=details, props=PROPS)


async def test_numeric_render_results_are_coerced():
    details = {
        "command": [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); raise SystemExit(3)",
            "{{ 42 }}",  # full-expression template -> int after render
        ],
        "error": [3],
    }
    with pytest.raises(ActionClientError, match="42"):
        await exec_plugin.run(details=details, props=PROPS)


# ----- timeout -----

async def test_timeout_kills_process():
    details = {
        "command": [sys.executable, "-c", "import time; time.sleep(30)"],
        "timeout": 1,
    }
    with pytest.raises(ActionError, match="timeout"):
        await exec_plugin.run(details=details, props=PROPS)
