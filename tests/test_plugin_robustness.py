"""
Log and action plugins against misbehaving surroundings: a CA file that
cannot be loaded is the plugin's own error (no raw 500), a log whose
templates do not render for a record only fails itself, the bodies read from
HTTP endpoints and the output of executed programs are bounded, and an
executed program is stopped (with its children) on timeout -- there is one by
default -- and when the request is cancelled.
"""

import asyncio
import os
import sys
import time

import httpx
import pytest

from app.lib import log as log_lib
from app.lib import plugin
from app.model.err import ActionClientError, ActionError, LogError, LogSpecsError

from tests.lib_log import _FakeLog, _entry, _logspec, _op, _specs
from tests.lib_log import patch_plugins  # noqa: F401  (fixture)
from tests.test_plugin_error_handling import _mock_httpx

#
# ssl_verify
#


@pytest.fixture(params=["missing", "garbage"])
def bad_ca_file(request, tmp_path) -> str:
    file = tmp_path / "ca.pem"
    if request.param == "garbage":
        file.write_text("no certificate\n")
    return str(file)


async def test_http_log_bad_ca_file_is_a_log_error(bad_ca_file):
    http_log = plugin.get_module("log", "http").log
    details = {"url": "https://logs.example/x", "ssl_verify": bad_ca_file}
    with pytest.raises(LogError):
        await http_log.get("install", details=details, props={})
    with pytest.raises(LogError):
        await http_log.content("install", details=details | {"content": {}}, props={})


async def test_elastic_log_bad_ca_file_is_a_log_error(bad_ca_file):
    elastic_log = plugin.get_module("log", "elastic").log
    details = {"url": "https://elastic.example", "ssl_verify": bad_ca_file}
    with pytest.raises(LogError):
        await elastic_log.get("audit", details=details, props={})


async def test_http_action_bad_ca_file_is_an_action_error(bad_ca_file):
    http_action = plugin.get_module("action", "http").action
    details = {"url": "https://hook.example/x", "ssl_verify": bad_ca_file}
    with pytest.raises(ActionError) as excinfo:
        await http_action.run(details=details, props={})
    assert not isinstance(excinfo.value, ActionClientError)


#
# One log failing on its records
#


async def test_specs_error_of_one_log_leaves_the_others(patch_plugins):  # noqa: F811
    patch_plugins(
        {
            "good": _FakeLog([_entry("ok", "kept")]),
            "bad": _FakeLog(raise_exc=LogSpecsError("In details.time: undefined")),
        }
    )
    logs = await log_lib.get(
        _op(), _specs(_logspec("broken", "bad"), _logspec("ok", "good"))
    )
    assert [entry.message for entry in logs] == ["kept"]


async def test_record_without_the_templated_field_only_fails_its_log(monkeypatch):
    """The default `time: "{{ log.time }}"` on a record that has no time."""
    mod = plugin.get_module("log", "http")

    def handler(request):
        if request.url.path == "/broken":
            return httpx.Response(200, json=[{"message": "no time"}])
        return httpx.Response(200, json=[{"time": "t1", "message": "fine"}])

    _mock_httpx(monkeypatch, mod, handler)
    specs = _specs(
        _logspec("broken", "http").model_copy(
            update={"details": {"url": "https://logs.example/broken"}}
        ),
        _logspec("ok", "http").model_copy(
            update={
                "details": {
                    "url": "https://logs.example/ok",
                    "message": "{{ log.message }}",
                }
            }
        ),
    )
    logs = await log_lib.get(_op(), specs)
    assert [(entry.name, entry.message) for entry in logs] == [("ok", "fine")]


#
# Body sizes
#


async def test_http_log_response_size_is_limited(monkeypatch):
    mod = plugin.get_module("log", "http")
    monkeypatch.setattr(mod, "MAX_BODY_BYTES", 1000)
    monkeypatch.setattr(mod, "MAX_CONTENT_BYTES", 5000)
    sizes = {"/entries": 1001, "/small": 1000, "/doc": 5000, "/bigdoc": 5001}
    _mock_httpx(
        monkeypatch,
        mod,
        lambda request: httpx.Response(200, text="x" * sizes[request.url.path]),
    )

    def details(path, content=None):
        d = {"url": f"https://logs.example{path}", "response": "text", "time": ""}
        if content:
            d["content"] = {"url": f"https://logs.example{content}"}
        return d

    with pytest.raises(LogError, match="larger than 1000 bytes"):
        await mod.log.get("install", details=details("/entries"), props={})
    assert len(await mod.log.get("install", details=details("/small"), props={})) == 1

    # A content document may be larger than the polled entries ...
    document = await mod.log.content(
        "install", details=details("/small", "/doc"), props={}
    )
    assert len(document.content) == 5000
    # ... but not unlimited.
    with pytest.raises(LogError, match="larger than 5000 bytes"):
        await mod.log.content("install", details=details("/small", "/bigdoc"), props={})


async def test_http_log_json_and_headers_still_parsed(monkeypatch):
    mod = plugin.get_module("log", "http")
    _mock_httpx(
        monkeypatch,
        mod,
        lambda request: httpx.Response(
            200,
            content="zäh".encode("latin-1"),
            headers={"Content-Type": "text/plain; charset=latin-1", "X-State": "ok"},
        ),
    )
    details = {
        "url": "https://logs.example/x",
        "response": "text",
        "time": "",
        "message": "{{ log.status }} {{ log.headers['x-state'] }} {{ log.body }}",
    }
    (entry,) = await mod.log.get("install", details=details, props={})
    assert entry.message == "200 ok zäh"


async def test_http_action_reads_a_limited_error_body(monkeypatch):
    mod = plugin.get_module("action", "http")
    monkeypatch.setattr(mod, "MAX_BODY_BYTES", 1000)
    _mock_httpx(monkeypatch, mod, lambda request: httpx.Response(409, text="x" * 5000))
    with pytest.raises(ActionClientError) as excinfo:
        await mod.action.run(
            details={"url": "https://hook.example/x", "error": [409]}, props={}
        )
    assert str(excinfo.value) == "x" * 1000


#
# exec
#


def _gone(pid: int) -> bool:
    """Whether the process has ended (a zombie counts)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as file:
            return file.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


async def _wait_gone(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while not _gone(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return _gone(pid)


def _spawning(pid_file) -> list[str]:
    """A program that starts a child (pid to `pid_file`) and waits for it."""
    return [
        sys.executable,
        "-c",
        "import subprocess, sys\n"
        "child = subprocess.Popen(['sleep', '300'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "child.wait()\n",
        str(pid_file),
    ]


async def _child_pid(pid_file) -> int:
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("child was not started")


async def test_exec_has_a_default_timeout(monkeypatch):
    mod = plugin.get_module("action", "exec")
    assert mod.DEFAULT_TIMEOUT == 60
    monkeypatch.setattr(mod, "DEFAULT_TIMEOUT", 0.2)
    with pytest.raises(ActionError, match="timeout of 0.2s"):
        await mod.action.run(details={"command": ["sleep", "30"]}, props={})
    # 0 still means "no timeout"
    await mod.action.run(
        details={"command": ["sleep", "0.4"], "timeout": 0}, props={}
    )


async def test_exec_timeout_kills_the_children_too(tmp_path):
    mod = plugin.get_module("action", "exec")
    pid_file = tmp_path / "pid"
    with pytest.raises(ActionError, match="timeout"):
        await mod.action.run(
            details={"command": _spawning(pid_file), "timeout": 1}, props={}
        )
    assert await _wait_gone(await _child_pid(pid_file))


async def test_exec_cancelled_request_kills_the_process_group(tmp_path):
    mod = plugin.get_module("action", "exec")
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(
        mod.action.run(details={"command": _spawning(pid_file)}, props={})
    )
    pid = await _child_pid(pid_file)
    assert not _gone(pid)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_gone(pid)


async def test_exec_output_is_limited(monkeypatch):
    mod = plugin.get_module("action", "exec")
    monkeypatch.setattr(mod, "MAX_OUTPUT_BYTES", 1000)
    details = {
        "command": [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 300000); sys.exit(3)",
        ],
        "error": [3],
    }
    with pytest.raises(ActionClientError) as excinfo:
        await mod.action.run(details=details, props={})
    assert str(excinfo.value) == "x" * 1000


async def test_exec_stdin_the_program_does_not_read(monkeypatch):
    mod = plugin.get_module("action", "exec")
    await mod.action.run(
        details={"command": ["true"], "stdin": "x" * 1_000_000}, props={}
    )
    assert os.path.exists("/proc/self")  # (no BrokenPipeError above)
