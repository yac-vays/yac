"""
Action and log plugins turn what can go wrong with a rendered value or a
response into their own error classes (never a raw 500), and a log record
of an unexpected shape only fails its own log.
"""

import httpx
import pytest

from app.lib import plugin
from app.model.err import ActionError, ActionSpecsError, LogError, LogSpecsError


def _mock_httpx(monkeypatch, mod, handler):
    """Route the plugin's AsyncClient to `handler(request) -> httpx.Response`."""
    real = httpx.AsyncClient

    def client(**kwargs):
        kwargs.pop("verify", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", client)


# ----- action http -----


async def test_http_action_sends_rendered_objects_and_numbers(monkeypatch):
    mod = plugin.get_module("action", "http")
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["x-count"] = request.headers["x-count"]
        return httpx.Response(200)

    _mock_httpx(monkeypatch, mod, handler)
    await mod.action.run(
        details={
            "url": "https://hook.example/x",
            "method": "POST",
            "body": "{{ new.data }}",
            "headers": {"X-Count": "{{ new.data.count }}"},
        },
        props={"new": {"data": {"count": 3}}},
    )
    assert seen == {"body": b'{"count": 3}', "x-count": "3"}


async def test_http_action_bad_values_are_action_errors(monkeypatch):
    mod = plugin.get_module("action", "http")
    _mock_httpx(monkeypatch, mod, lambda request: httpx.Response(200))
    with pytest.raises(ActionSpecsError):
        await mod.action.run(
            details={"url": "https://x/", "headers": {"X": "{{ [1, 2] }}"}}, props={}
        )
    with pytest.raises(ActionError):
        await mod.action.run(details={"url": "https://exa mple:x/"}, props={})


async def test_http_action_undecodable_error_body(monkeypatch):
    mod = plugin.get_module("action", "http")
    _mock_httpx(monkeypatch, mod, lambda request: httpx.Response(500, content=b"\xff\xfe"))
    with pytest.raises(ActionError):
        await mod.action.run(details={"url": "https://x/"}, props={})


# ----- log file -----


def _logfile(tmp_path, content: bytes) -> str:
    path = tmp_path / "x.log"
    path.write_bytes(content)
    return str(path)


async def test_file_log_survives_invalid_bytes(tmp_path):
    mod = plugin.get_module("log", "file")
    logs = await mod.log.get(
        "install", details={"path": _logfile(tmp_path, b"ok\nb\xffd\n")}, props={}
    )
    assert [l.message for l in logs] == ["ok", "b�d"]


async def test_file_log_unknown_encoding_is_a_specs_error(tmp_path):
    mod = plugin.get_module("log", "file")
    with pytest.raises(LogSpecsError):
        await mod.log.get(
            "install",
            details={"path": _logfile(tmp_path, b"x\n"), "encoding": "no-such-enc"},
            props={},
        )


async def test_record_of_unexpected_type_fails_only_its_log(tmp_path):
    mod = plugin.get_module("log", "file")
    path = _logfile(tmp_path, b"1700000000 started\n")
    details = {"path": path, "line_format": r"^(\d+) (.*)$", "message": "{{ log[1] }}"}

    # a numeric timestamp is no string
    with pytest.raises(LogError) as excinfo:
        await mod.log.get("install", details={**details, "time": "{{ log[0] | int }}"}, props={})
    assert not isinstance(excinfo.value, LogSpecsError)
    assert "details.time" in str(excinfo.value) and "int" in str(excinfo.value)

    # a bool is no progress
    with pytest.raises(LogError):
        await mod.log.get("install", details={**details, "progress": "{{ true }}"}, props={})

    # a template that cannot render is a specs error
    with pytest.raises(LogSpecsError):
        await mod.log.get("install", details={**details, "time": "{{ log[0] | nope }}"}, props={})

    logs = await mod.log.get(
        "install", details={**details, "time": "{{ log[0] }}", "progress": "{{ 50 }}"}, props={}
    )
    assert (logs[0].time, logs[0].message, logs[0].progress) == ("1700000000", "started", 50)


# ----- log elastic -----


async def test_elastic_log_non_json_answer_is_a_log_error(monkeypatch):
    mod = plugin.get_module("log", "elastic")
    _mock_httpx(monkeypatch, mod, lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(LogError):
        await mod.log.get("es", details={"url": "https://es/idx", "query": "any"}, props={})


async def test_elastic_log_entries(monkeypatch):
    mod = plugin.get_module("log", "elastic")
    body = {"hits": {"events": [{"_source": {"@timestamp": "2026-01-01", "msg": "hi"}}]}}
    _mock_httpx(monkeypatch, mod, lambda request: httpx.Response(200, json=body))
    logs = await mod.log.get(
        "es",
        details={"url": "https://es/idx", "query": "any", "message": "{{ log.msg }}"},
        props={},
    )
    assert [(l.name, l.time, l.message, l.problem) for l in logs] == [
        ("es", "2026-01-01", "hi", None)
    ]
