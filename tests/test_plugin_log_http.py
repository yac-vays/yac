"""
Tests for the `http` log plugin -- fetches a JSON array of records over HTTP and
renders each into an `out.Log`. `httpx` is faked (no real network): we assert the
URL/headers are j2-rendered from props, the response list is parsed, and
time/message/problem/progress are rendered per-record with the record as `log`.
"""

import types

import httpx
import pytest

from app.lib import plugin


DATA = [
    {"time": "2026-06-29T12:00:00Z", "message": "Installer started"},
    {"time": "2026-06-29T12:30:00Z", "message": "Installer finished"},
]

DETAILS = {
    "url": "https://logs-test.bootstrap.inf.ethz.ch/logs/{{ name }}/install?limit=10",
    "headers": {"Authorization": "Bearer {{ env.token }}"},
    "time": "{{ log.time }}",
    "message": "{{ log.message }}",
    "progress": '{{ 100 if log.message == "Installer finished" else 25 }}',
    "problem": '{{ "fail" in log.message }}',
}

PROPS = {"name": "node1.example.com", "env": {"token": "SEKRET"}}


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    last = None

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url):
        _FakeClient.last = types.SimpleNamespace(
            method=method, url=url, headers=self.kwargs.get("headers", {})
        )
        return _FakeResp(DATA)


@pytest.fixture
def http_mod(monkeypatch):
    mod = plugin.get_module("log", "http")
    monkeypatch.setattr(
        mod,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=_FakeClient,
            HTTPError=httpx.HTTPError,
            ConnectError=httpx.ConnectError,
            ConnectTimeout=httpx.ConnectTimeout,
        ),
    )
    return mod


async def test_parses_records_and_renders_fields(http_mod):
    logs = await http_mod.log.get(
        "install", True, True, details=DETAILS, props=PROPS
    )

    assert [l.message for l in logs] == ["Installer started", "Installer finished"]
    assert [l.time for l in logs] == [
        "2026-06-29T12:00:00Z",
        "2026-06-29T12:30:00Z",
    ]
    assert [l.progress for l in logs] == [25, 100]
    assert all(l.problem is False for l in logs)
    assert all(l.name == "install" for l in logs)


async def test_url_and_headers_are_rendered_from_props(http_mod):
    await http_mod.log.get("install", False, False, details=DETAILS, props=PROPS)

    assert _FakeClient.last.url == (
        "https://logs-test.bootstrap.inf.ethz.ch/logs/node1.example.com/install?limit=10"
    )
    assert _FakeClient.last.headers["Authorization"] == "Bearer SEKRET"


async def test_array_path_drills_into_nested_response(http_mod, monkeypatch):
    async def _nested(self, method, url):
        return _FakeResp({"data": {"items": DATA}})

    monkeypatch.setattr(_FakeClient, "request", _nested)
    details = {**DETAILS, "array": "data.items"}
    logs = await http_mod.log.get("install", False, False, details=details, props=PROPS)
    assert [l.message for l in logs] == ["Installer started", "Installer finished"]


async def test_http_error_becomes_log_error(http_mod, monkeypatch):
    from app.model.err import LogError

    def _boom(self, method, url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_FakeClient, "request", _boom)
    with pytest.raises(LogError):
        await http_mod.log.get("install", False, False, details=DETAILS, props=PROPS)


async def test_connect_failure_is_retried_once(http_mod, monkeypatch):
    calls = []

    async def _flaky(self, method, url):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("")
        return _FakeResp(DATA)

    monkeypatch.setattr(_FakeClient, "request", _flaky)
    logs = await http_mod.log.get("install", False, False, details=DETAILS, props=PROPS)
    assert len(calls) == 2
    assert [l.message for l in logs] == ["Installer started", "Installer finished"]


async def test_read_timeout_is_not_retried_and_names_the_error(http_mod, monkeypatch):
    from app.model.err import LogError

    calls = []

    async def _slow(self, method, url):
        calls.append(url)
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(_FakeClient, "request", _slow)
    with pytest.raises(LogError, match=r"ReadTimeout after \d+\.\ds"):
        await http_mod.log.get("install", False, False, details=DETAILS, props=PROPS)
    assert len(calls) == 1


async def test_persistent_connect_failure_gives_up_after_retry(http_mod, monkeypatch):
    from app.model.err import LogError

    calls = []

    async def _down(self, method, url):
        calls.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_FakeClient, "request", _down)
    with pytest.raises(LogError, match="ConnectError"):
        await http_mod.log.get("install", False, False, details=DETAILS, props=PROPS)
    assert len(calls) == 2
