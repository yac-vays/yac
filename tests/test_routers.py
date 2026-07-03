"""
Endpoint-level (router) tests, driving the real FastAPI app through
httpx.AsyncClient + ASGITransport.

Seams used (no app/ code is modified):

- Specs: the process-wide specs were loaded from tests/fixtures/minimal.yml at
  import time, so each test monkeypatches `app.lib.specs._RAW_DATA` with the
  contents of tests/fixtures/routers.yml. `specs.read()` deep-copies and
  re-parses that dict per (cached) request signature.
- Auth: `app.lib.auth.get_current_user` is replaced via FastAPI
  `dependency_overrides` with a function returning a synthetic `User`. Every
  login generates a unique `sub` claim so the specs/perms caches (keyed on
  stable JWT claims) can never bleed between tests.
- Repo: `app.lib.repo.handler` is monkeypatched with an in-memory `IRepo`
  whose reader/writer scopes yield a writable subclass of the shared
  `FakeRepoSession`. Every test gets a unique repo hash so the entity-lookup
  cache (keyed on the hash) can never bleed between tests.
- Log/action plugins: the module objects returned by `plugin.get_module`
  (an lru-cached `pydoc.importfile` import, distinct from the regular
  `app.plugin.*` modules!) get their `log` / `action` attribute replaced
  with call-recording fakes.

Permission matrix (from tests/fixtures/routers.yml):
  alice: adm+secrets     -> everything, incl. the custom `secrets` perm
  bob:   edt+cln         -> see/edt/cln, but NO add/del/act/secrets
  carol: see             -> read-only
  erin:  see+add+edt+cln -> can create/edit, but NO secrets
  dave:  (no role)       -> nothing, not even `see`
"""

import copy
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.lib import plugin
from app.lib import repo as repo_lib
from app.lib import specs as specs_lib
from app.lib import yaml as yaml_lib
from app.lib.auth import get_current_user
from app.main import yac
from app.model.err import RepoConflict, SpecsError
from app.model.out import Diff, Log, User

from tests.conftest import FakeRepoSession

RAW_SPECS = yaml_lib.load_as_dict(
    (Path(__file__).parent / "fixtures" / "routers.yml").read_text(encoding="utf-8"),
    strict=False,
)

TOP_SECRET = "TOPSECRET_VALUE_XYZ"
NESTED_SECRET = "NESTED_SECRET_VALUE_ABC"

WEB01_YAML = f"""owner: alice
legacy: keep-me
top_secret: {TOP_SECRET}
networking:
  ip: 10.0.0.1
  note: nested-const-note
  vlan_secret: {NESTED_SECRET}
"""


#
# Fakes
#


class WritableRepoSession(FakeRepoSession):
    """conftest's FakeRepoSession + working writers and a per-test hash."""

    def __init__(self, files=None, links=None, repo_hash="testhash"):
        super().__init__(files=files, links=links)
        self.repo_hash = repo_hash

    async def get_hash(self):
        return self.repo_hash

    async def write(self, type, name, content_old, content_new, msg):
        self.files[name] = content_new
        return Diff(name=name, hash=self.repo_hash, patch="")

    async def write_rename(self, type, name_old, name_new, content_old, content_new, msg):
        self.files.pop(name_old, None)
        self.files[name_new] = content_new
        return Diff(name=name_new, hash=self.repo_hash, patch="")

    async def copy(self, type, name_dest, name_src, msg):
        self.files[name_dest] = self.files[name_src]
        return Diff(name=name_dest, hash=self.repo_hash, patch="")

    async def link(self, type, name_link, name_src, msg):
        self.links[name_link] = name_src
        return Diff(name=name_link, hash=self.repo_hash, patch="")

    async def delete(self, type, name, content_old, msg):
        # Mirror the plugin contract (git_direct._delete): a delete validated
        # against stale content must conflict, not act.
        if name in self.files and self.files[name] != content_old:
            raise RepoConflict("The data has changed in the meantime")
        self.files.pop(name, None)
        self.links.pop(name, None)


class _Untyped:
    def __init__(self, sess):
        self._sess = sess

    async def get_hash(self):
        return await self._sess.get_hash()

    def session(self, details):
        return self._sess


class FakeRepoHandler:
    """In-memory IRepo: reader/writer scopes both yield the same session."""

    def __init__(self, sess):
        self._sess = sess

    @asynccontextmanager
    async def reader(self, user=None, *, dirty=False):
        yield _Untyped(self._sess)

    @asynccontextmanager
    async def writer(self, user=None):
        yield _Untyped(self._sess)


class LogRecorder:
    def __init__(self):
        self.calls = []

    async def get(self, facility, problem, progress, *, details, props):
        self.calls.append(facility)
        return [Log(name=facility, message="hello", time="2026-01-01T00:00:00Z")]


class ActionRecorder:
    def __init__(self):
        self.calls = []

    async def run(self, *, details, props):
        self.calls.append(details)


#
# Fixtures
#


@pytest.fixture
def repo_session(monkeypatch):
    """
    Patch the specs raw data + repo handler. The unique hash keeps the
    hash-keyed entity cache from bleeding between tests.
    """
    sess = WritableRepoSession(
        files={"web01": WEB01_YAML, "web02": "owner: bob\n"},
        repo_hash=f"hash-{uuid.uuid4().hex}",
    )
    monkeypatch.setattr(specs_lib, "_RAW_DATA", copy.deepcopy(RAW_SPECS))
    # `specs.__parse` ignores the raw `repo` section and reuses the
    # startup-rendered _STATIC_REPO (from minimal.yml, whose connection is
    # not a dict and fails Specs validation) -- patch it as well.
    monkeypatch.setattr(
        specs_lib,
        "_STATIC_REPO",
        copy.deepcopy(RAW_SPECS["repo"]),
    )
    monkeypatch.setattr(repo_lib, "handler", FakeRepoHandler(sess))
    return sess


@pytest.fixture
def login():
    """Override the auth dependency; unique `sub` per login (cache isolation)."""

    def _login(name: str) -> User:
        user = User(
            name=name,
            full_name=name.title(),
            email=f"{name}@example.com",
            token={"sub": f"{name}-{uuid.uuid4().hex}", "aud": "test"},
        )
        yac.dependency_overrides[get_current_user] = lambda: user
        return user

    yield _login
    yac.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def client(repo_session, login):
    transport = httpx.ASGITransport(app=yac)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def log_recorder(monkeypatch):
    rec = LogRecorder()
    # plugin.get_module imports by file path (pydoc.importfile) and caches the
    # resulting module object -- patch *that* object, not app.plugin.log.file.
    monkeypatch.setattr(plugin.get_module("log", "file"), "log", rec)
    return rec


@pytest.fixture
def action_recorder(monkeypatch):
    rec = ActionRecorder()
    monkeypatch.setattr(plugin.get_module("action", "shell"), "action", rec)
    return rec


def _validate_body(data: dict) -> dict:
    return {
        "operation": "edit",
        "type": "host",
        "name": "web01",
        "entity": {"name": "web01", "data": data},
    }


#
# 1) /validate const-leak regression (fix #1)
#


async def test_validate_does_not_leak_forbidden_values_as_consts(client, login):
    """
    bob lacks the `secrets` perm guarding `top_secret` (top-level) and
    `networking.vlan_secret` (nested). yac_perms removes those subschemas and
    records the removals, so add_consts must NOT re-inject the stored values
    as `const` nodes in the schema returned by POST /validate. It re-injects
    only a permissive value-free stub (so the merged data still validates
    under `additionalProperties: false`).
    """
    login("bob")
    resp = await client.post("/validate", json=_validate_body({"owner": "robert"}))
    assert resp.status_code == 200
    schema = resp.json()["schemas"]["json_schema"]
    serialized = json.dumps(schema)

    # The forbidden stored values appear NOWHERE in the returned schema.
    assert TOP_SECRET not in serialized
    assert NESTED_SECRET not in serialized
    # The removed properties come back only as non-leaking permissive stubs.
    top_stub = schema["properties"]["top_secret"]
    nested_stub = schema["properties"]["networking"]["properties"]["vlan_secret"]
    for stub in (top_stub, nested_stub):
        assert "const" not in stub
        assert set(stub.keys()) <= {"description"}


async def test_validate_still_adds_consts_for_allowed_unspecified_data(client, login):
    """
    The add_consts feature itself must keep working: stored keys that are not
    in the schema and not perms-removed (top-level `legacy`, nested
    `networking.note`) still come back as `const`.
    """
    login("bob")
    resp = await client.post("/validate", json=_validate_body({"owner": "robert"}))
    assert resp.status_code == 200
    schema = resp.json()["schemas"]["json_schema"]

    assert schema["properties"]["legacy"]["const"] == "keep-me"
    nested = schema["properties"]["networking"]["properties"]
    assert nested["note"]["const"] == "nested-const-note"


async def test_validate_keeps_guarded_properties_for_privileged_user(client, login):
    """
    Control: alice holds the `secrets` perm, so the guarded subschemas stay
    in place (as real subschemas, not consts) and the consts feature still
    covers her unspecified keys.
    """
    login("alice")
    resp = await client.post("/validate", json=_validate_body({"owner": "alice"}))
    assert resp.status_code == 200
    schema = resp.json()["schemas"]["json_schema"]

    assert schema["properties"]["top_secret"]["type"] == "string"
    nested = schema["properties"]["networking"]["properties"]
    assert nested["vlan_secret"]["type"] == "string"
    assert schema["properties"]["legacy"]["const"] == "keep-me"


#
# 2) Cross-router permission consistency
#

PUT_BODY = {
    "name": "web01",
    "yaml_old": WEB01_YAML,
    "yaml_new": WEB01_YAML.replace("owner: alice", "owner: zelda"),
}


def _op_request(client, kind: str):
    if kind == "create":
        return client.post(
            "/entity/host", json={"name": "new01", "yaml": "owner: alice\n"}
        )
    if kind == "put":
        return client.put("/entity/host/web01", json=PUT_BODY)
    if kind == "patch":
        return client.patch(
            "/entity/host/web01", json={"name": "web01", "data": {"owner": "zelda"}}
        )
    if kind == "delete":
        return client.delete("/entity/host/web01")
    if kind == "action":
        return client.post("/entity/host/web01/run/deploy")
    raise ValueError(kind)


# (kind, user lacking the relevant perm, expected success code for alice)
PERM_CASES = [
    ("create", "bob", 201),  # bob has edt but not add
    ("put", "carol", 200),  # carol has see but not edt
    ("patch", "carol", 200),
    ("delete", "bob", 204),  # bob has edt but not del
    ("action", "bob", 204),  # bob has edt but not act
]


@pytest.mark.parametrize("kind,unprivileged,success_code", PERM_CASES)
async def test_perm_enforcement_across_routers(
    client, login, repo_session, action_recorder, kind, unprivileged, success_code
):
    """
    For every mutating router: a user without the relevant permission gets a
    403 (RequestForbidden) and causes no repo write / action run; the
    privileged user succeeds with the documented 2xx code.
    """
    files_before = dict(repo_session.files)

    login(unprivileged)
    resp = await _op_request(client, kind)
    assert resp.status_code == 403, resp.text
    assert resp.json()["title"] == "Forbidden"
    assert repo_session.files == files_before  # nothing was written/deleted
    assert action_recorder.calls == []  # no action hook ran

    login("alice")
    resp = await _op_request(client, kind)
    assert resp.status_code == success_code, resp.text

    if kind == "create":
        assert "new01" in repo_session.files
    elif kind in ("put", "patch"):
        assert "owner: zelda" in repo_session.files["web01"]
    elif kind == "delete":
        assert "web01" not in repo_session.files
    elif kind == "action":
        assert len(action_recorder.calls) == 1


#
# 3) GET .../logs permission enforcement (fix #2)
#


async def test_logs_forbidden_user_does_not_trigger_log_plugin(
    client, login, log_recorder
):
    """dave has no role at all (no `see`): 403, and the log plugin never ran."""
    login("dave")
    resp = await client.get("/entity/host/web01/logs")
    assert resp.status_code == 403
    assert log_recorder.calls == []


async def test_logs_missing_entity_does_not_trigger_log_plugin(
    client, login, log_recorder
):
    """Entity existence is enforced before the log fetch as well."""
    login("alice")
    resp = await client.get("/entity/host/ghost/logs")
    assert resp.status_code == 404
    assert log_recorder.calls == []


async def test_logs_allowed_user_gets_logs(client, login, log_recorder):
    login("alice")
    resp = await client.get("/entity/host/web01/logs")
    assert resp.status_code == 200, resp.text
    assert log_recorder.calls == ["install"]
    assert [l["name"] for l in resp.json()] == ["install"]


#
# 4) Property-level write permissions (PATCH)
#


async def test_patch_forbidden_property_rejected(client, login, repo_session):
    """
    bob may edit the entity (edt) but not the `secrets`-guarded property:
    a PATCH touching it must be rejected and nothing committed.
    """
    login("bob")
    resp = await client.patch(
        "/entity/host/web01",
        json={"name": "web01", "data": {"networking": {"vlan_secret": "HACKED"}}},
    )
    assert resp.status_code in (400, 403), resp.text
    assert repo_session.files["web01"] == WEB01_YAML  # unchanged
    # The rejection must not echo the stored secret back either.
    assert TOP_SECRET not in resp.text
    assert NESTED_SECRET not in resp.text


async def test_patch_allowed_property_accepted(client, login, repo_session):
    """
    bob changes only `owner`, which his perms (edt) fully cover; the entity's
    perms-hidden properties stay untouched. This must be accepted: the
    permissive stub injected by add_consts covers the mere presence of the
    hidden keys under `additionalProperties: false`, and the out-of-schema
    enforcement sees no change at the protected paths.
    """
    login("bob")
    resp = await client.patch(
        "/entity/host/web01", json={"name": "web01", "data": {"owner": "robert"}}
    )
    assert resp.status_code == 200, resp.text
    assert "owner: robert" in repo_session.files["web01"]
    # The hidden values are preserved verbatim in the committed YAML.
    assert TOP_SECRET in repo_session.files["web01"]
    assert NESTED_SECRET in repo_session.files["web01"]


async def test_patch_forbidden_property_rejected_with_open_object(
    monkeypatch, client, login, repo_session
):
    """
    Same as test_patch_forbidden_property_rejected, but the parent object
    explicitly allows additional properties — the schema alone can no longer
    reject the write, so the out-of-schema enforcement (old vs new data at
    the perm-removed paths) must reject it. And the rejection must not leak
    the stored values.
    """
    raw = copy.deepcopy(RAW_SPECS)
    # Open both objects that hold a guarded property; otherwise the *other*
    # hidden key (top_secret) would trip the top-level
    # additionalProperties=false and mask the enforcement on vlan_secret.
    raw["schema"]["additionalProperties"] = True
    raw["schema"]["properties"]["networking"]["additionalProperties"] = True
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)

    login("bob")
    resp = await client.patch(
        "/entity/host/web01",
        json={"name": "web01", "data": {"networking": {"vlan_secret": "HACKED"}}},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["title"] == "Forbidden"
    assert repo_session.files["web01"] == WEB01_YAML  # nothing committed
    # The rejection must not echo the stored secrets back either.
    assert TOP_SECRET not in resp.text
    assert NESTED_SECRET not in resp.text


async def test_hidden_property_cannot_be_set_when_unstored(
    monkeypatch, client, login, repo_session
):
    """
    A user without the guarding perm cannot SET a value in a perms-hidden
    property either — neither on create nor on PATCH of an entity that stores
    no value there. Exercised with open objects (additionalProperties: true),
    where the schema itself no longer rejects the unknown key and only the
    out-of-schema enforcement protects the path.
    """
    raw = copy.deepcopy(RAW_SPECS)
    raw["schema"]["additionalProperties"] = True
    raw["schema"]["properties"]["networking"]["additionalProperties"] = True
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)

    # erin may create entities (add) but lacks `secrets`.
    login("erin")
    resp = await client.post(
        "/entity/host",
        json={"name": "new02", "yaml": "owner: erin\ntop_secret: SNEAKED\n"},
    )
    assert resp.status_code == 403, resp.text
    assert "new02" not in repo_session.files

    # bob may edit (edt+cln) but lacks `secrets`; web02 stores no hidden value.
    login("bob")
    resp = await client.patch(
        "/entity/host/web02",
        json={"name": "web02", "data": {"networking": {"vlan_secret": "SNEAKED"}}},
    )
    assert resp.status_code == 403, resp.text
    assert "SNEAKED" not in repo_session.files["web02"]


#
# 5) Limits TOCTOU: re-check inside the writer scope
#


class RacingRepoHandler(FakeRepoHandler):
    """
    FakeRepoHandler whose *writer* scope reveals one extra entity, simulating
    a concurrent create that landed between the reader scope (where the
    router measured the limits and the validator approved them) and the
    writer scope (where the entity is written).
    """

    @asynccontextmanager
    async def writer(self, user=None):
        self._sess.files.setdefault("sniped", "owner: mallory\n")
        yield _Untyped(self._sess)


def _specs_with_limit(cap: str) -> dict:
    raw = copy.deepcopy(RAW_SPECS)
    raw["types"][0]["limits"] = [{"title": "hosts", "max": cap}]
    return raw


async def test_create_limit_recheck_blocks_raced_quota(
    monkeypatch, client, login, repo_session
):
    """
    Cap 3, two existing entities: the reader-scope check passes (2+1=3), but
    a "concurrent" create consumes the last slot before the writer scope
    opens. The writer-scope re-check must reject the create with the same
    400 the validator would produce, and nothing may be written.
    """
    monkeypatch.setattr(specs_lib, "_RAW_DATA", _specs_with_limit("3"))
    monkeypatch.setattr(repo_lib, "handler", RacingRepoHandler(repo_session))

    login("alice")
    resp = await client.post(
        "/entity/host", json={"name": "new01", "yaml": "owner: alice\n"}
    )
    assert resp.status_code == 400, resp.text
    assert 'Limit "hosts" reached: 4/3' in resp.json()["message"]
    assert "new01" not in repo_session.files  # the raced create was blocked


async def test_create_limit_recheck_passes_without_race(
    monkeypatch, client, login, repo_session
):
    """Control: same cap but no interleaved create -> the writer-scope
    re-check passes and the entity is committed."""
    monkeypatch.setattr(specs_lib, "_RAW_DATA", _specs_with_limit("3"))

    login("alice")
    resp = await client.post(
        "/entity/host", json={"name": "new01", "yaml": "owner: alice\n"}
    )
    assert resp.status_code == 201, resp.text
    assert "new01" in repo_session.files


async def test_edit_limit_recheck_blocks_raced_quota(
    monkeypatch, client, login, repo_session
):
    """The same guard protects the edit path: a PATCH that was fine at
    reader time (web01 is replaced by itself: 1 other + 1 incoming = 2) is
    rejected once the raced create appears in the writer scope (3 > 2)."""
    monkeypatch.setattr(specs_lib, "_RAW_DATA", _specs_with_limit("2"))
    monkeypatch.setattr(repo_lib, "handler", RacingRepoHandler(repo_session))

    login("alice")
    resp = await client.patch(
        "/entity/host/web01", json={"name": "web01", "data": {"owner": "zelda"}}
    )
    assert resp.status_code == 400, resp.text
    assert 'Limit "hosts" reached' in resp.json()["message"]
    assert "owner: zelda" not in repo_session.files["web01"]


class MutatingRepoHandler(FakeRepoHandler):
    """
    FakeRepoHandler whose *writer* scope reveals a concurrently MODIFIED
    web01, simulating an edit that landed between the reader scope (where the
    delete's permission was validated against `old.data`) and the writer
    scope (where the delete happens).
    """

    @asynccontextmanager
    async def writer(self, user=None):
        self._sess.files["web01"] = "owner: mallory\n"
        yield _Untyped(self._sess)


async def test_delete_conflicts_when_entity_changed_meanwhile(
    monkeypatch, client, login, repo_session
):
    """
    The delete's authorization (and its templated DELETE hooks) were derived
    from the reader-scope content. If the entity changed before the writer
    scope, the delete must 409 like an edit would — never act on stale
    authorization — and the (new) file must survive.
    """
    monkeypatch.setattr(repo_lib, "handler", MutatingRepoHandler(repo_session))

    login("alice")
    resp = await client.delete("/entity/host/web01")
    assert resp.status_code == 409, resp.text
    assert "changed in the meantime" in resp.json()["message"]
    assert repo_session.files["web01"] == "owner: mallory\n"  # nothing deleted


async def test_delete_passes_when_entity_unchanged(client, login, repo_session):
    """Control: no interleaved change -> the pinned delete goes through."""
    login("alice")
    resp = await client.delete("/entity/host/web01")
    assert resp.status_code == 204, resp.text
    assert "web01" not in repo_session.files


#
# 6) Specs role-key validation (lib-level, runs without the app fixtures)
#


def test_malformed_role_key_raises_specs_error():
    with pytest.raises(SpecsError, match="host:all"):
        specs_lib._validate_role_keys({"roles": [{"host:all": "true"}]})


def test_wellformed_role_keys_pass():
    specs_lib._validate_role_keys(
        {"roles": [{"host:all:edt": "true", "host:admins:adm+secrets": "true"}]}
    )
    # Non-list shapes are left for pydantic to report later -- no raise here.
    specs_lib._validate_role_keys({"roles": "not-a-list"})
    specs_lib._validate_role_keys({})
