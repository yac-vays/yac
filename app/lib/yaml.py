"""
Raises: [app.lib.yaml.YAMLError, app.model.err.RequestConflict]
"""

import io
import logging
import re
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


# PyYAML follows YAML 1.1; ruamel's round-trip loader (`load`/`load_as_dict`,
# used for validation) follows YAML 1.2. They disagree on several implicit
# scalar resolutions, so a value can read one way for the read paths
# (permissions, option extraction, the data the UI form renders) and validate
# another way -- blocking a commit with no error visible in the form. The known
# divergences (see the contract test in tests/lib_yaml.py):
#   - bool:  yes/no/on/off are booleans in 1.1, strings in 1.2.
#            e.g. `monitoring_enabled: no` -> fast False vs validator "no".
#   - int:   `0644` is octal (420) in 1.1, decimal (644) in 1.2; 1.1 also reads
#            sexagesimal `1:2:3`; 1.2 understands `0o`/`0b` prefixes.
#   - float: `1e3` (no dot) is a string in 1.1, a float in 1.2; 1.1 reads
#            sexagesimal `1:2.5`.
# Re-resolve bool/int/float on the fast loader to match ruamel exactly. Both the
# resolver (what tag a scalar gets) and the constructor (how the tagged scalar
# becomes a value) must change, since PyYAML's 1.1 constructors would still read
# `0644` as octal even once the resolver tags it as int.
#
# Copy the inherited resolver table first (don't mutate the shared base class),
# then drop the 1.1 bool/int/float resolvers and register 1.2-equivalent ones.
_REASSIGNED_TAGS = {
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
}
_FastLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for tag, regexp in resolvers if tag not in _REASSIGNED_TAGS]
    for ch, resolvers in _FastLoader.yaml_implicit_resolvers.items()
}
_FastLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_FastLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^[-+]?(?:0x[0-9a-fA-F_]+|0o[0-7_]+|0b[0-1_]+|[0-9][0-9_]*)$"),
    list("-+0123456789"),
)
_FastLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:
            [-+]?(?:\.[0-9_]+|[0-9][0-9_]*\.[0-9_]*|[0-9][0-9_]*)[eE][-+]?[0-9]+  # exponent (dot optional)
          | [-+]?(?:\.[0-9_]+|[0-9][0-9_]*\.[0-9_]*)                              # dot, no exponent
          | [-+]?\.(?:inf|Inf|INF)
          | \.(?:nan|NaN|NAN)
        )$""",
        re.VERBOSE,
    ),
    list("-+0123456789."),
)


def _construct_fast_int(loader, node):
    v = loader.construct_scalar(node).replace("_", "")
    neg = v[0] == "-"
    if v[0] in "+-":
        v = v[1:]
    if v[:2] in ("0x", "0X"):
        n = int(v[2:], 16)
    elif v[:2] in ("0o", "0O"):
        n = int(v[2:], 8)
    elif v[:2] in ("0b", "0B"):
        n = int(v[2:], 2)
    else:
        n = int(v, 10)  # leading zeros are decimal in 1.2 (0644 -> 644)
    return -n if neg else n


def _construct_fast_float(loader, node):
    v = loader.construct_scalar(node).replace("_", "").lower()
    sign = -1.0 if v and v[0] == "-" else 1.0
    if v and v[0] in "+-":
        v = v[1:]
    if v == ".inf":
        return sign * float("inf")
    if v == ".nan":
        return float("nan")
    return sign * float(v)


_FastLoader.add_constructor("tag:yaml.org,2002:int", _construct_fast_int)
_FastLoader.add_constructor("tag:yaml.org,2002:float", _construct_fast_float)


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
