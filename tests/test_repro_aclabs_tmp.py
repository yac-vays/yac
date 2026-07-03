"""
TEMPORARY repro (not part of the suite): capture the real edit schema + merged
YAML for aclabs.ethz.ch (which carries unspecified `docker_daemon` data that
add_consts turns into an object `const`), for the monaco-yaml investigation.
"""

import copy
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.lib import repo as repo_lib
from app.lib import specs as specs_lib
from app.lib import yaml as yaml_lib
from app.lib.auth import get_current_user
from app.main import yac
from app.model.out import Diff, User

from tests.conftest import FakeRepoSession

KIOSK = Path("/root/repos/k8s-isg-kiosk/helm/files/yac-inventory-test")
HOST = "aclabs.ethz.ch"
STORED_YAML = Path(
    "/root/repos/ansible-pull/all/inventory/host_vars/aclabs.ethz.ch.yml"
).read_text(encoding="utf-8")

ROUTERS_SPECS = yaml_lib.load_as_dict(
    (Path(__file__).parent / "fixtures" / "routers.yml").read_text(encoding="utf-8"),
    strict=False,
)


def build_specs() -> dict:
    specs = yaml_lib.load_as_dict(
        (KIOSK / "yac.yml").read_text(encoding="utf-8"), strict=False
    )
    specs["schema"] = yaml_lib.load_as_dict(
        (KIOSK / "schema.yml").read_text(encoding="utf-8"), strict=False
    )
    specs["repo"] = copy.deepcopy(ROUTERS_SPECS["repo"])
    return specs


class WritableRepoSession(FakeRepoSession):
    def __init__(self, files=None, repo_hash="testhash"):
        super().__init__(files=files)
        self.repo_hash = repo_hash

    async def get_hash(self):
        return self.repo_hash

    async def write(self, type, name, content_old, content_new, msg):
        self.files[name] = content_new
        return Diff(name=name, hash=self.repo_hash, patch="")


class _Untyped:
    def __init__(self, sess):
        self._sess = sess

    async def get_hash(self):
        return await self._sess.get_hash()

    def session(self, details):
        return self._sess


class FakeRepoHandler:
    def __init__(self, sess):
        self._sess = sess

    @asynccontextmanager
    async def reader(self, user=None, *, dirty=False):
        yield _Untyped(self._sess)

    @asynccontextmanager
    async def writer(self, user=None):
        yield _Untyped(self._sess)


@pytest.fixture
def repro_client(monkeypatch):
    specs = build_specs()
    sess = WritableRepoSession(
        files={HOST: STORED_YAML},
        repo_hash=f"hash-{uuid.uuid4().hex}",
    )
    monkeypatch.setattr(specs_lib, "_RAW_DATA", specs)
    monkeypatch.setattr(specs_lib, "_STATIC_REPO", copy.deepcopy(specs["repo"]))
    monkeypatch.setattr(repo_lib, "handler", FakeRepoHandler(sess))
    user = User(
        name="alice",
        full_name="Alice",
        email="alice@example.com",
        token={
            "sub": f"alice-{uuid.uuid4().hex}",
            "aud": "test",
            "groups": ["INFK-ISG-Staff"],
        },
    )
    yac.dependency_overrides[get_current_user] = lambda: user
    transport = httpx.ASGITransport(app=yac)
    yield httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60)
    yac.dependency_overrides.pop(get_current_user, None)


OUT = Path("/tmp/claude-0/-root/d0c50bd9-1dfa-414c-842d-7dbf9ac321fa/scratchpad")


@pytest.mark.asyncio
async def test_capture_aclabs_schema(repro_client):
    async with repro_client as client:
        body = {
            "operation": "edit",
            "type": "host",
            "name": HOST,
            "actions": [],
            "entity": {"name": HOST, "data": {}, "yaml_base": STORED_YAML},
        }
        r = (await client.post("/validate", json=body)).json()
        js = r["schemas"]["json_schema"]
        print("\nschemas.valid:", r["schemas"]["valid"], "| msg:", r["schemas"].get("message"))
        print("docker_daemon subschema:", json.dumps(js["properties"].get("docker_daemon")))
        (OUT / "aclabs_schema.json").write_text(json.dumps(js), encoding="utf-8")
        (OUT / "aclabs_yaml.txt").write_text(r["schemas"]["yaml"], encoding="utf-8")
        print("yaml captured:", len(r["schemas"]["yaml"]), "chars")
