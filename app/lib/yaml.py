"""
Raises: [app.lib.yaml.YAMLError, app.model.err.RequestConflict]
"""

import io
import logging
import re
from collections import Counter
from typing import Any

import ruamel.yaml
import ruamel.yaml.comments
import ruamel.yaml.tokens
from ruamel.yaml.scalarbool import ScalarBoolean
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
# Used by the test suite to assert that loaded objects are round-trip safe.
YAMLSafeBase = ruamel.yaml.comments.CommentedBase  # type: ignore
YAMLError = ruamel.yaml.YAMLError

# Only used for typing.
YAMLObject = ruamel.yaml.YAMLObject


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


# Structural-change detection
#
# A change is "structural" if the new YAML differs from the old YAML by more
# than a data patch can express. Data changes (values, added keys, removed
# keys) are governed by the schema; everything else (comments, key order,
# quoting / scalar style, anchors) is what the "cln" permission covers.
#
# Implementation: project the data of the new document onto the old one
# (which keeps the old comments, key order and styles, like `update` does),
# then compare the two documents with their comments stripped -- that covers
# key order, quoting / style and anchors. Comments are compared separately as
# a multiset, because ruamel attaches comment lines to the *preceding* key, so
# deleting a key silently drops the comment that follows it while the new
# document (parsed on its own) still carries that comment elsewhere.

_COMMENT_SLOTS = ("comment", "_items", "_post", "_pre")


def _plain(value: Any) -> Any:
    """
    Reduce a (possibly ruamel-typed) scalar to its plain Python value so data
    comparisons ignore quoting / style, but keep int, float, bool and str apart
    (`1` vs `1.0` vs `true` is a data change in JSON-schema terms).
    """
    if isinstance(value, (bool, ScalarBoolean)):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _data_equal(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_data_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_data_equal, a, b))
    pa, pb = _plain(a), _plain(b)
    return type(pa) is type(pb) and pa == pb


def _project(old: Any, new: Any) -> None:
    """
    Apply the data of `new` onto `old` in place with the same semantics as
    `update` (mappings are merged, lists and scalars replaced wholesale).
    Replaced values are the *nodes of `new`* so their quoting / style is taken
    over as-is; new keys are inserted at the position they have in `new` (so a
    plain insertion is not a reorder of the existing keys).
    """
    for key in [k for k in old if k not in new]:
        del old[key]
        # ruamel does not reliably drop the key's comment entry along with it
        old.ca.items.pop(key, None)
    prev = None
    for key, value in new.items():
        if key in old:
            if isinstance(old[key], dict) and isinstance(value, dict):
                _project(old[key], value)
            elif not _data_equal(old[key], value):
                old[key] = value
        else:
            pos = 0 if prev is None else list(old.keys()).index(prev) + 1
            old.insert(pos, key, value)
        prev = key


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _comment_tokens(node: Any) -> Any:
    seen: set[int] = set()

    def tokens(x: Any) -> Any:
        if isinstance(x, ruamel.yaml.tokens.CommentToken):
            if id(x) not in seen:
                seen.add(id(x))
                yield x
        elif isinstance(x, (list, tuple)):
            for i in x:
                yield from tokens(i)
        elif isinstance(x, dict):
            for i in x.values():
                yield from tokens(i)

    for sub in _walk(node):
        ca = getattr(sub, ruamel.yaml.comments.Comment.attrib, None)
        if ca is not None:
            yield from tokens([getattr(ca, slot) for slot in _COMMENT_SLOTS])


def _comments(node: Any) -> Counter:
    """
    Multiset of the comment lines of a document. Blank lines (which ruamel
    stores inside the comment tokens) are ignored: they are pure spacing.
    """
    lines: Counter = Counter()
    for token in _comment_tokens(node):
        for line in token.value.splitlines():
            line = line.strip()
            if line.startswith("#"):
                lines[line] += 1
    return lines


def _strip_comments(node: Any) -> Any:
    for sub in _walk(node):
        ca = getattr(sub, ruamel.yaml.comments.Comment.attrib, None)
        if ca is not None:
            ca.comment = None
            ca._items = {}  # pylint: disable=protected-access
            ca._post = []  # pylint: disable=protected-access
            ca._pre = None  # pylint: disable=protected-access
    return node


def has_structural_changes(yaml_old: str, yaml_new: str) -> bool:
    """
    True if `yaml_new` differs from `yaml_old` beyond what a data patch (see
    `update`) can express: comments, key order (of existing keys), quoting /
    scalar style of unchanged values, anchors. Pure data changes -- including
    removing keys -- are never structural; whether they are allowed is the
    schema's business.

    Comments may only vanish together with the key they are attached to; any
    other comment removal, addition or edit is structural. Blank lines are
    ignored. Known blind spot: a comment change inside a list whose content
    also changed is not detected (the list is replaced wholesale).
    """
    old = load(yaml_old)
    new = load(yaml_new)

    if old is None:
        return False
    if new is None:
        return True

    if not isinstance(old, dict) or not isinstance(new, dict):
        return dump(old) != dump(new)

    comments_old = _comments(old)
    _project(old, new)
    comments_projected = _comments(old)
    comments_new = _comments(new)

    if not comments_projected <= comments_new <= comments_old:
        return True
    return dump(_strip_comments(old)) != dump(_strip_comments(new))


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
