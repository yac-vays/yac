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
    def __init__(self, data=None, status=200, text="", headers=None):
        self._data = data
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=None, response=None
            )

    def json(self):
        if self._data is None:
            raise ValueError("no JSON")
        return self._data


class _FakeClient:
    last = None
    calls: list = []

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
        _FakeClient.calls.append(_FakeClient.last)
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
            HTTPStatusError=httpx.HTTPStatusError,
            ConnectError=httpx.ConnectError,
            ConnectTimeout=httpx.ConnectTimeout,
        ),
    )
    return mod


async def test_parses_records_and_renders_fields(http_mod):
    logs = await http_mod.log.get(
        "install", details=DETAILS, props=PROPS
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
    await http_mod.log.get("install", details=DETAILS, props=PROPS)

    assert _FakeClient.last.url == (
        "https://logs-test.bootstrap.inf.ethz.ch/logs/node1.example.com/install?limit=10"
    )
    assert _FakeClient.last.headers["Authorization"] == "Bearer SEKRET"


async def test_array_path_drills_into_nested_response(http_mod, monkeypatch):
    async def _nested(self, method, url):
        return _FakeResp({"data": {"items": DATA}})

    monkeypatch.setattr(_FakeClient, "request", _nested)
    details = {**DETAILS, "array": "data.items"}
    logs = await http_mod.log.get("install", details=details, props=PROPS)
    assert [l.message for l in logs] == ["Installer started", "Installer finished"]


async def test_http_error_becomes_log_error(http_mod, monkeypatch):
    from app.model.err import LogError

    def _boom(self, method, url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_FakeClient, "request", _boom)
    with pytest.raises(LogError):
        await http_mod.log.get("install", details=DETAILS, props=PROPS)


async def test_connect_failure_is_retried_once(http_mod, monkeypatch):
    calls = []

    async def _flaky(self, method, url):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("")
        return _FakeResp(DATA)

    monkeypatch.setattr(_FakeClient, "request", _flaky)
    logs = await http_mod.log.get("install", details=DETAILS, props=PROPS)
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
        await http_mod.log.get("install", details=DETAILS, props=PROPS)
    assert len(calls) == 1


async def test_persistent_connect_failure_gives_up_after_retry(http_mod, monkeypatch):
    from app.model.err import LogError

    calls = []

    async def _down(self, method, url):
        calls.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_FakeClient, "request", _down)
    with pytest.raises(LogError, match="ConnectError"):
        await http_mod.log.get("install", details=DETAILS, props=PROPS)
    assert len(calls) == 2


#
# Generic problem / progress, text responses, empty_on and content
#


async def test_problem_and_progress_unset_if_not_defined(http_mod):
    details = {k: v for k, v in DETAILS.items() if k not in ("problem", "progress")}
    logs = await http_mod.log.get("install", details=details, props=PROPS)
    assert all(l.problem is None and l.progress is None for l in logs)
    assert all(l.has_content is False for l in logs)


async def test_progress_is_clamped(http_mod):
    details = {**DETAILS, "progress": "{{ 250 if 'finished' in log.message else -5 }}"}
    logs = await http_mod.log.get("install", details=details, props=PROPS)
    assert [l.progress for l in logs] == [0, 100]


STATE_DETAILS = {
    "method": "HEAD",
    "url": "https://logs.example.com/states/{{ name }}/facts.yaml",
    "headers": {"Authorization": "Bearer {{ env.token }}"},
    "response": "text",
    "empty_on": [404],
    "time": "{{ log.headers['last-modified'] }}",
    "message": "status {{ log.status }}",
    "problem": "{{ (log.headers['last-modified'] | to_datetime('%a, %d %b %Y %H:%M:%S GMT')) < (now() - timedelta(days=14)) }}",
    "content": {"time": "{{ log.headers['last-modified'] }}"},
}


def _state_server(monkeypatch, status=200, body="a: 1\n", modified="Wed, 18 Nov 2026 13:12:55 GMT"):
    _FakeClient.calls = []

    async def _state(self, method, url):
        _FakeClient.calls.append(
            types.SimpleNamespace(method=method, url=url, headers=self.kwargs.get("headers", {}))
        )
        return _FakeResp(
            status=status,
            text="" if method == "HEAD" else body,
            headers={"Last-Modified": modified, "Content-Type": "text/plain"},
        )

    monkeypatch.setattr(_FakeClient, "request", _state)


async def test_text_response_is_one_record(http_mod, monkeypatch):
    _state_server(monkeypatch, modified="Mon, 02 Jan 2006 15:04:05 GMT")
    logs = await http_mod.log.get("facts", details=STATE_DETAILS, props=PROPS)
    assert len(logs) == 1
    entry = logs[0]
    assert entry.time == "Mon, 02 Jan 2006 15:04:05 GMT"  # header names lowercased
    assert entry.message == "status 200"
    assert entry.problem is True  # older than 14 days
    assert entry.has_content is True
    call = _FakeClient.calls[0]
    assert call.method == "HEAD"
    assert call.url == "https://logs.example.com/states/node1.example.com/facts.yaml"


async def test_empty_on_gives_no_entries(http_mod, monkeypatch):
    _state_server(monkeypatch, status=404)
    assert await http_mod.log.get("facts", details=STATE_DETAILS, props=PROPS) == []


async def test_other_errors_still_raise(http_mod, monkeypatch):
    from app.model.err import LogError

    _state_server(monkeypatch, status=500)
    with pytest.raises(LogError):
        await http_mod.log.get("facts", details=STATE_DETAILS, props=PROPS)


async def test_content_inherits_the_request_and_defaults_to_get_text(http_mod, monkeypatch):
    _state_server(monkeypatch, body="ansible_hostname: node1\n")
    c = await http_mod.log.content("facts", details=STATE_DETAILS, props=PROPS)
    assert c.name == "facts"
    assert c.content == "ansible_hostname: node1\n"
    assert c.time == "Wed, 18 Nov 2026 13:12:55 GMT"
    call = _FakeClient.calls[0]
    assert call.method == "GET"  # not the HEAD of the entries
    assert call.url == "https://logs.example.com/states/node1.example.com/facts.yaml"
    assert call.headers["Authorization"] == "Bearer SEKRET"


async def test_content_missing_raises_not_found(http_mod, monkeypatch):
    from app.model.err import LogContentNotFound

    _state_server(monkeypatch, status=404)
    with pytest.raises(LogContentNotFound):
        await http_mod.log.content("facts", details=STATE_DETAILS, props=PROPS)
    with pytest.raises(LogContentNotFound):
        await http_mod.log.content("install", details=DETAILS, props=PROPS)


async def test_content_json_response(http_mod):
    details = {
        **DETAILS,
        "content": {
            "url": "https://logs.example.com/raw/{{ name }}",
            "response": "json",
            "text": "{{ log | length }} records, first: {{ log[0].message }}",
        },
    }
    c = await http_mod.log.content("install", details=details, props=PROPS)
    assert c.content == "2 records, first: Installer started"
    assert _FakeClient.last.url == "https://logs.example.com/raw/node1.example.com"


async def test_invalid_response_mode_is_a_specs_error(http_mod):
    from app.model.err import LogSpecsError

    with pytest.raises(LogSpecsError):
        await http_mod.log.get("install", details={**DETAILS, "response": "xml"}, props=PROPS)
