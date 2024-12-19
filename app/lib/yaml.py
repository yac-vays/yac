"""
Raises: [app.lib.yaml.YAMLError, app.model.err.RequestConflict]
"""

import io
from typing import Any

import ruamel.yaml

from app.model.err import RequestConflict

y = ruamel.yaml.YAML(typ="rt")
y.indent(mapping=2, sequence=4, offset=2)
y.preserve_quotes = True
y.explicit_start = True
y.width = 4096
y.representer.add_representer(
    type(None),
    lambda self, data: self.represent_scalar("tag:yaml.org,2002:null", "null"),
)

y_non_strict = ruamel.yaml.YAML(typ="rt")
y_non_strict.allow_duplicate_keys = True
YAMLSafeBase = ruamel.yaml.comments.CommentedBase  # type: ignore
YAMLError = ruamel.yaml.YAMLError


class YAMLObject(ruamel.yaml.YAMLObject):
    pass


def load(yaml: str, *, strict: bool = True) -> YAMLObject | None:
    if strict:
        data = y.load(yaml)
    else:
        data = y_non_strict.load(yaml)
    return data


def load_as_dict(yaml: str, *, strict: bool = True) -> dict:
    try:
        return dict(load(yaml, strict=strict))
    except (ValueError, TypeError):
        return {}


def dump(data: dict | YAMLObject | None) -> str:
    buf = io.BytesIO()
    y.dump(data, buf)
    return buf.getvalue().decode("utf-8")


def has_structural_changes(yaml_old: str, yaml_new: str) -> bool:
    old = load(yaml_old)
    new = load(yaml_new)
    if old is None:
        return False
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
            if diff[key] == "~undefined" and key in data:
                data.pop(key)
            else:
                data[key] = __deep_update(data.get(key, diff[key]), diff[key])
    elif isinstance(diff, list):
        data = []
        for item in diff:
            if item != "~undefined":
                data.append(__deep_update(item, item))
    else:
        data = diff
    return data
