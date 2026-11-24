"""Loader edge cases of app.lib.yaml."""

import pytest

from app.lib import yaml


@pytest.mark.parametrize("doc", ["x: ._", "x: 0x_", "x: 0o_", "x: 0b_"])
def test_broken_numbers_are_yaml_errors(doc):
    with pytest.raises(yaml.YAMLError):
        yaml.load(doc)
    with pytest.raises(yaml.YAMLError):
        yaml.load_as_dict_fast(doc)


def test_deep_nesting_is_a_yaml_error():
    with pytest.raises(yaml.YAMLTooComplexError):
        yaml.load("x: " + "[" * 400 + "]" * 400)
    with pytest.raises(yaml.YAMLError):
        yaml.load_as_dict_fast("a: &a [1]\nb: " + "[" * 3000 + "*a" + "]" * 3000)


@pytest.mark.parametrize("doc", ["- [a, b]\n- [c, d]", "- ab\n- cd", "text", "42"])
def test_non_mapping_documents(doc):
    assert yaml.load_as_dict(doc) == {}
    assert yaml.load_as_dict_fast(doc) == {}
    with pytest.raises(yaml.YAMLError):
        yaml.load_mapping(doc)


@pytest.mark.parametrize("doc", ["", "---\n", "null", "# only a comment\n"])
def test_empty_documents_are_empty_mappings(doc):
    assert yaml.load_mapping(doc) == {}


def test_load_mapping():
    assert yaml.load_mapping("a: 1\nb: [x]") == {"a": 1, "b": ["x"]}
