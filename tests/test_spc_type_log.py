"""
Tests for the `logs` entries of types in the specs (`spc.TypeLog`) and their
public form: renderer + options instead of the removed progress/problem flags.
"""

import pydantic
import pytest

from app.model import out
from app.model import spc


def test_defaults():
    log = spc.TypeLog(name="install", title="Install", plugin="http")
    assert log.renderer == "messages"
    assert log.options == {}


def test_renderer_and_options():
    log = spc.TypeLog(
        name="facts",
        title="Facts",
        plugin="http",
        renderer="document",
        options={"language": "yaml"},
    )
    public = out.TypeLog.model_validate(log.model_dump(exclude={"plugin", "details"}))
    assert public.model_dump() == {
        "name": "facts",
        "title": "Facts",
        "description": "",
        "renderer": "document",
        "options": {"language": "yaml"},
    }


@pytest.mark.parametrize("flag", ["progress", "problem"])
def test_removed_flags_are_rejected_with_a_hint(flag):
    with pytest.raises(pydantic.ValidationError, match=f"renderer: {flag}"):
        spc.TypeLog(name="install", title="Install", plugin="http", **{flag: True})


@pytest.mark.parametrize("renderer", ["Document", "my-renderer", "", "1st"])
def test_invalid_renderer_names(renderer):
    with pytest.raises(pydantic.ValidationError):
        spc.TypeLog(name="x", title="X", plugin="http", renderer=renderer)
