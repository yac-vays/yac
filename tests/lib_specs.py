"""
Tests for the pure helpers in `lib.specs` that can be exercised without the
import-time machinery (the module already loaded the minimal fixture via
conftest):

* `_stable_token` -- drops volatile JWT claims so the specs/perms caches hit
  across token refreshes.
* `_op_signature` -- the cache key derived from a request.
* `_process_includes_sync` -- the `yac_include` merge (existing keys win) plus
  its path-escape guard (an include outside the base dir aborts the process).
"""

import pytest

from app.lib import specs
from app.model.inp import OperationRequest
from app.model.out import User


def _op(headers=None, ip="1.2.3.4", actions=None, token=None):
    return OperationRequest(
        request_headers=headers or {}, request_ip=ip,
        user=User(name="alice", email="a@x.com", full_name="A", token=token or {}),
        operation="read", type="host", name="e1", actions=actions or [], entity=None,
    )


# ----- _stable_token -----

def test_stable_token_drops_volatile_claims():
    token = {"sub": "u", "email": "e", "iat": 1, "exp": 2, "jti": "x", "nonce": "n"}
    assert specs._stable_token(token) == {"sub": "u", "email": "e"}


def test_stable_token_none_is_empty():
    assert specs._stable_token(None) == {}
    assert specs._stable_token({}) == {}


# ----- _op_signature -----

def test_op_signature_ignores_volatile_token_claims():
    a = specs._op_signature(_op(token={"sub": "u", "iat": 1}))
    b = specs._op_signature(_op(token={"sub": "u", "iat": 9999}))
    assert a == b  # iat differs but is volatile -> same signature


def test_op_signature_changes_with_stable_claim():
    a = specs._op_signature(_op(token={"sub": "u"}))
    b = specs._op_signature(_op(token={"sub": "other"}))
    assert a != b


def test_op_signature_reflects_request_fields():
    base = specs._op_signature(_op(ip="1.1.1.1"))
    assert base != specs._op_signature(_op(ip="2.2.2.2"))
    assert base != specs._op_signature(_op(actions=["x"]))


# ----- _process_includes_sync -----

def test_include_merges_without_overriding_existing_keys(tmp_path):
    (tmp_path / "inc.yml").write_text("a: from_include\nb: 2\n")
    data = {"a": "original", "yac_include": "inc.yml"}
    out = specs._process_includes_sync(data, str(tmp_path))
    # existing key `a` wins; new key `b` is pulled in
    assert out == {"a": "original", "b": 2}


def test_include_list_of_files(tmp_path):
    (tmp_path / "one.yml").write_text("a: 1\n")
    (tmp_path / "two.yml").write_text("b: 2\n")
    data = {"yac_include": ["one.yml", "two.yml"]}
    out = specs._process_includes_sync(data, str(tmp_path))
    assert out == {"a": 1, "b": 2}


def test_include_recurses_into_nested_structures(tmp_path):
    (tmp_path / "inc.yml").write_text("nested: deep\n")
    data = {"outer": {"yac_include": "inc.yml"}}
    out = specs._process_includes_sync(data, str(tmp_path))
    assert out["outer"] == {"nested": "deep"}


def test_include_outside_base_dir_aborts(tmp_path):
    # an include that escapes the base directory must abort the process
    (tmp_path.parent / "secret.yml").write_text("leaked: true\n")
    data = {"yac_include": "../secret.yml"}
    with pytest.raises(SystemExit):
        specs._process_includes_sync(data, str(tmp_path))


def test_process_includes_passes_through_plain_data(tmp_path):
    data = {"a": 1, "b": [1, 2, {"c": 3}]}
    assert specs._process_includes_sync(data, str(tmp_path)) == data
