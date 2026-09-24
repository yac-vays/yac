"""
`GET /status` and the stale-data response headers, driven through the real
app with a fake repo handler.
"""

import copy
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.lib import repo as repo_lib
from app.lib import specs as specs_lib
from app.lib import staleness
from app.lib import yaml as yaml_lib
from app.main import yac
from app.model.err import RepoMaintenance
from app.model.plg import IRepo, RepoState
from app.router import status as status_router


class _Untyped:
    async def get_hash(self):
        return "abc123"

    def session(self, details):
        raise NotImplementedError


class _Handler(IRepo):
    def __init__(self, *, unavailable=False, stale_since=None, state=None):
        self._unavailable = unavailable
        self._stale_since = stale_since
        self._state = state or RepoState()

    def state(self):
        return self._state

    @asynccontextmanager
    async def reader(self, user=None, *, dirty=False):
        if self._unavailable:
            raise RepoMaintenance(RepoMaintenance.default_message)
        if self._stale_since is not None:
            staleness.mark_stale(self._stale_since)
        yield _Untyped()

    @asynccontextmanager
    async def writer(self, user=None):
        yield _Untyped()


RAW_SPECS = yaml_lib.load_as_dict(
    (Path(__file__).parent / "fixtures" / "routers.yml").read_text(encoding="utf-8"),
    strict=False,
)


@pytest.fixture
def client(monkeypatch):
    # /status reads the specs; the minimal import-time fixture is not a
    # complete specs file, so use the routers one (like test_routers does).
    monkeypatch.setattr(specs_lib, "_RAW_DATA", copy.deepcopy(RAW_SPECS))
    monkeypatch.setattr(specs_lib, "_STATIC_REPO", copy.deepcopy(RAW_SPECS["repo"]))
    monkeypatch.setattr(
        status_router, "_status_cache", {"status": None, "expires_at": 0.0}
    )

    def make(handler):
        monkeypatch.setattr(repo_lib, "handler", handler)
        transport = httpx.ASGITransport(app=yac)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return make


async def test_status_healthy(client):
    async with client(_Handler(state=RepoState(synced=1_700_000_000.0))) as c:
        resp = await c.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hash"] == "abc123"
    assert body["repo"]["available"] is True
    assert body["repo"]["stale"] is False
    assert body["repo"]["error"] is None
    assert body["repo"]["synced"].startswith("2023-11-14")
    assert staleness.WARNING_HEADER not in resp.headers


async def test_status_stale_is_200_with_headers(client):
    state = RepoState(
        synced=1_700_000_000.0, error="Could not resolve host", failed=1_700_000_100.0
    )
    async with client(_Handler(stale_since=1_700_000_000.0, state=state)) as c:
        resp = await c.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hash"] == "abc123"
    assert body["repo"]["available"] is False
    assert body["repo"]["stale"] is True
    # the public body carries the user-facing reason, not the git output
    assert "Could not resolve host" not in resp.text
    assert body["repo"]["error"]
    assert resp.headers[staleness.WARNING_HEADER] == staleness.WARNING_VALUE
    assert resp.headers[staleness.SYNCED_HEADER] == "1700000000"


async def test_status_unavailable_is_503_with_body(client):
    state = RepoState(error="503", failed=1_700_000_100.0, maintenance=True)
    async with client(_Handler(unavailable=True, state=state)) as c:
        resp = await c.get("/status")
        assert resp.status_code == 503
        body = resp.json()
        assert body["hash"] is None
        assert body["repo"]["available"] is False
        assert body["repo"]["error"] == RepoMaintenance.default_message
        # not cached: the next call re-checks
        resp2 = await c.get("/status")
        assert resp2.status_code == 503


async def test_stale_header_not_added_on_normal_responses(client):
    async with client(_Handler()) as c:
        resp = await c.get("/health")
    assert resp.status_code == 204
    assert staleness.WARNING_HEADER not in resp.headers
