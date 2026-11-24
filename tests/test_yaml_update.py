"""app.lib.yaml.update: applying a data patch to a YAML document."""

import copy

from app.lib import yaml

DOC = """---
a:
  - x  # inline
  - z
b: [1, 2]
n: 1
f: 1.0
s: 'quoted'  # why
"""


def test_unchanged_values_keep_comments_and_style():
    patch = {"a": ["x", "z"], "b": [1, 2], "n": 1, "f": 1.0, "s": "quoted"}
    assert yaml.update(DOC, patch) == DOC


def test_changed_list_is_replaced():
    out = yaml.load_as_dict(yaml.update(DOC, {"a": ["x"], "b": [2, 1]}))
    assert out["a"] == ["x"]
    assert out["b"] == [2, 1]


def test_scalar_type_changes_are_applied():
    patch = {"x": True, "y": False, "z": True}
    out = yaml.load_as_dict(yaml.update("x: 1\ny: 0\nz: 'true'\n", patch))
    assert out == {"x": True, "y": False, "z": True}
    assert all(isinstance(v, bool) for v in out.values())


def test_json_cannot_tell_integral_floats_from_ints():
    # A JS client sends 1 for a stored 1.0: not a change.
    assert yaml.update("x: 1.0\n", {"x": 1}) == "---\nx: 1.0\n"


def test_patch_is_not_mutated():
    patch = {"a": {"gone": "~undefined", "l": [{"k": "~undefined", "v": 1}, "~undefined"]}}
    before = copy.deepcopy(patch)
    out = yaml.load_as_dict(yaml.update("a: 1\n", patch))
    assert patch == before
    assert out == {"a": {"l": [{"v": 1}]}}


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "value",
    [
        "a b\nc",
        "a\x85b\nc",
        "a\r\nb",
        "a\x07b\nc",
        "plain\ntext\n",
        "  lead\n trail  \n",
        "ümläut\n日本\n\U0001f600",
    ],
)
def test_multiline_strings_round_trip(value):
    out = yaml.update("k: 1\n", {"k": value})
    assert yaml.load_as_dict(out)["k"] == value


def test_ordinary_multiline_strings_are_literal_blocks():
    assert yaml.update("k: 1\n", {"k": "a\nb\n"}) == "---\nk: |\n  a\n  b\n"
