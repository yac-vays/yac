"""
Raises: [app.lib.yaml.YAMLError, app.model.err.RequestConflict]
"""

import io
import logging
from typing import Any

import ruamel.yaml
import yaml as _pyyaml

from app.model.err import RequestConflict

logger = logging.getLogger(__name__)

y = ruamel.yaml.YAML(typ="rt")
y.indent(mapping=2, sequence=4, offset=2)
y.preserve_quotes = True
y.explicit_start = True
y.width = 4096
y.representer.add_representer(
    type(None),
    lambda self, data: self.represent_scalar("tag:yaml.org,2002:null", "null"),
)
y.representer.add_representer(
    str,
    lambda self, data: self.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|" if "\n" in data else None
    ),
)
y.constructor.add_constructor(
    "tag:yaml.org,2002:timestamp", lambda self, data: self.construct_scalar(data)
)

y_non_strict = ruamel.yaml.YAML(typ="rt")
y_non_strict.allow_duplicate_keys = True
YAMLSafeBase = ruamel.yaml.comments.CommentedBase  # type: ignore
YAMLError = ruamel.yaml.YAMLError


class YAMLObject(ruamel.yaml.YAMLObject):
    pass


def load(yaml: str, *, strict: bool = True) -> YAMLObject | None:
    if strict:
        return y.load(yaml)

    # Non-strict: probe with allow_duplicate_keys=False so ruamel surfaces a
    # DuplicateKeyError, log a warning, then accept the duplicate (last-wins).
    try:
        return y.load(yaml)
    except ruamel.yaml.constructor.DuplicateKeyError as error:
        logger.warning(
            f"Duplicate YAML key in non-strict load (last value wins): {error}"
        )
        return y_non_strict.load(yaml)


def load_as_dict(yaml: str, *, strict: bool = True) -> dict:
    try:
        return dict(load(yaml, strict=strict))
    except (ValueError, TypeError):
        return {}


try:
    _FastLoaderBase = _pyyaml.CSafeLoader
except AttributeError:
    _FastLoaderBase = _pyyaml.SafeLoader


class _FastLoader(_FastLoaderBase):  # type: ignore[misc, valid-type]
    pass


# Mirror the behavior overrides applied to the round-trip ruamel loader
# so permission tests and option extraction see equivalent values
# regardless of which loader produced them. The tests in lib_yaml.py
# pin these as the contract.
_FastLoader.add_constructor(
    "tag:yaml.org,2002:timestamp",
    lambda loader, node: loader.construct_scalar(node),
)


def _construct_omap_as_dict(loader, node):
    result: dict = {}
    for subnode in node.value:
        if not isinstance(subnode, _pyyaml.MappingNode):
            continue
        for key_node, value_node in subnode.value:
            result[loader.construct_object(key_node, deep=True)] = (
                loader.construct_object(value_node, deep=True)
            )
    return result


_FastLoader.add_constructor(
    "tag:yaml.org,2002:omap", _construct_omap_as_dict
)


def load_as_dict_fast(text: str) -> dict:
    """
    Permissive, C-backed YAML->dict loader for hot read paths (entity
    listing, permission filtering). About 10-50x faster than the
    round-trip ruamel loader at the cost of dropping comments, quoting,
    and anchors -- fine for callers that only need the parsed data.

    Falls back to the strict-tolerant ruamel loader on parse errors so
    any YAML accepted by `load_as_dict(strict=False)` still parses.
    """
    try:
        data = _pyyaml.load(text, Loader=_FastLoader)
    except _pyyaml.YAMLError:
        return load_as_dict(text, strict=False)
    if isinstance(data, dict):
        return data
    return {}


def dump(data: dict | YAMLObject | None) -> str:
    buf = io.BytesIO()
    y.dump(data, buf)
    return buf.getvalue().decode("utf-8")


def has_structural_changes(yaml_old: str, yaml_new: str) -> bool:
    old = load(yaml_old)
    new = load(yaml_new)

    # ruamel cannot handle cases where one is None
    if old is None:
        return False
    if new is None:
        return True

    old.update(new)
    return dump(old) != dump(new)


def update(yaml: str, diff: dict) -> str:
    """
    Updates a YAML string with the data object.

    Objects will be integrated, so only supplying one key means you only modify
    this one key (and not replace the whole object). Lists and base types on
    the other hand are completely replaced.

    The string "~undefined" will unset the whole object-key / list-item.
    """
    d = load(yaml)

    try:
        d = __deep_update(d, diff)
    except KeyError as error:
        raise RequestConflict("The key to be set undefined does not exist") from error

    return dump(d)


def __deep_update(data: Any, diff: Any) -> Any:
    if isinstance(diff, dict):
        if not isinstance(data, dict):
            data = {}
        for key in list(diff.keys()):
            if diff[key] == "~undefined":
                # Unset the key. If it is not present in the stored data there
                # is nothing to unset, so skip it entirely -- otherwise the
                # literal string "~undefined" would be written as the value
                # (e.g. for schema-defaulted fields that never got persisted).
                if key in data:
                    data.pop(key)
            else:
                data[key] = __deep_update(data.get(key, diff[key]), diff[key])
    elif isinstance(diff, list):
        data = []
        for item in diff:
            if item != "~undefined":
                data.append(__deep_update(item, item))
    else:
        if data != diff:
            data = diff
    return data
