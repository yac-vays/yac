"""
Tests for async `schema_formats` plugins: jsonschema calls format checkers
synchronously, so `lib.schema` resolves the values of async formats upfront
(concurrently, once per distinct value) and the validator only looks them up.
"""

import asyncio

import pytest
from async_lru import alru_cache

from app.lib import plugin
from app.lib import schema
from app.model.inp import OperationRequest
from app.model.out import User
from app.model.spc import Request, Schema

CALLS: list[str] = []


async def fake_user(data) -> bool:
    CALLS.append(data)
    await asyncio.sleep(0)
    return isinstance(data, str) and data.startswith("ok")


@alru_cache
async def cached_user(data) -> bool:
    return data == "cached"


@pytest.fixture(autouse=True)
def async_formats(monkeypatch):
    CALLS.clear()
    real = plugin.get_functions

    def get_functions(kind):
        functions = real(kind)
        if kind == "schema_formats":
            return {**functions, "fake_user": fake_user, "cached_user": cached_user}
        return functions

    monkeypatch.setattr(plugin, "get_functions", get_functions)


async def _get(spec, data):
    return await schema.get(
        OperationRequest(
            request_headers={}, request_ip="1.2.3.4",
            user=User(name="alice", email="a@x.com", full_name="A"),
            operation="create", type="host", name="e1", actions=[], entity=None,
        ),
        Schema.model_validate(spec), Request(), {}, {}, ["see", "edt"], data,
    )


def _users_spec(fmt="fake_user"):
    return {
        "type": "object",
        "properties": {
            "users": {
                "type": "array", "vays_category": "X",
                "items": {"type": "string", "format": fmt},
            },
        },
    }


async def test_async_format_valid():
    out = await _get(_users_spec(), {"users": ["ok1", "ok2"]})
    assert out.valid is True


async def test_async_format_invalid_reports_location():
    out = await _get(_users_spec(), {"users": ["ok1", "nope"]})
    assert out.valid is False
    assert out.validator == "format"
    assert out.data_loc == "#/users/1"
    assert "fake_user" in out.message


async def test_async_format_checks_each_value_once():
    await _get(_users_spec(), {"users": ["ok1", "ok1", "nope", "ok1"]})
    assert sorted(CALLS) == ["nope", "ok1"]


async def test_async_format_alru_cache_wrapper():
    assert (await _get(_users_spec("cached_user"), {"users": ["cached"]})).valid
    assert not (await _get(_users_spec("cached_user"), {"users": ["x"]})).valid


async def test_async_format_branch_revealed_by_result():
    # "nope" fails the first anyOf branch only once it is resolved; the second
    # branch then validates "other", which the first pass never visited.
    spec = {
        "type": "object",
        "properties": {
            "x": {
                "vays_category": "X",
                "anyOf": [
                    # both properties in each branch (YAC injects
                    # additionalProperties: false)
                    {"type": "object",
                     "properties": {"a": {"format": "fake_user"}, "b": {}}},
                    {"type": "object",
                     "properties": {"a": {}, "b": {"format": "fake_user"}}},
                ],
            },
        },
    }
    out = await _get(spec, {"x": {"a": "nope", "b": "other"}})
    assert out.valid is False
    assert sorted(CALLS) == ["nope", "other"]
    assert (await _get(spec, {"x": {"a": "nope", "b": "ok"}})).valid is True


async def test_sync_formats_unaffected():
    out = await _get(_users_spec("ssh_key"), {"users": ["not-a-key"]})
    assert out.valid is False and out.validator == "format"
    assert CALLS == []
