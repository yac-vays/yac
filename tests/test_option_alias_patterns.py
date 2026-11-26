"""
`alias_patterns` of type `options`: regex aliases evaluated by the UI. YAC
only validates them (and passes them through), so a pattern a browser cannot
evaluate is rejected when the specs are parsed instead of silently ignored.
"""

import pytest
from pydantic import ValidationError

from app.model.out import TypeOption, TypeOptionAliasPattern
from app.model.spc import Type


def _pattern(match: str, tooltip: str | None = None) -> TypeOptionAliasPattern:
    data = {"match": match, "alias": "A"}
    if tooltip is not None:
        data["tooltip"] = tooltip
    return TypeOptionAliasPattern.model_validate(data)


def test_defaults_and_passthrough():
    option = TypeOption.model_validate(
        {
            "name": "os",
            "title": "OS",
            "aliases": {"unknown": "?"},
            "alias_patterns": [
                {"match": r"ubuntu-(\d+\.\d+)", "alias": "U", "tooltip": "Ubuntu {1}"},
                {"match": "talos-.*", "alias": "T"},
            ],
        }
    )
    assert option.alias_patterns[1].tooltip == "{0}"
    public = Type(name="host", title="Hosts", options=[option]).to_public()
    assert public.model_dump()["options"][0]["alias_patterns"] == [
        {"match": r"ubuntu-(\d+\.\d+)", "alias": "U", "tooltip": "Ubuntu {1}"},
        {"match": "talos-.*", "alias": "T", "tooltip": "{0}"},
    ]


def test_option_without_patterns():
    assert TypeOption(name="os", title="OS").alias_patterns == []


@pytest.mark.parametrize(
    "match",
    [
        r"ubuntu-(.+)",
        r"windows-srv(\d{4})",
        r"(?:redhat|almalinux)-\d+",
        r"(?=x)x(?!y)(?<=x)(?<!z)",
        r"\(\?P<x>",  # escaped, a literal "(?P<x>"
        r"[(?P]\\",  # in a character class / escaped backslash
        r"[^\]a]",
        r"a{2}b+?",
    ],
)
def test_valid_patterns(match):
    assert _pattern(match).match == match


@pytest.mark.parametrize(
    ("match", "message"),
    [
        ("ubuntu-(", "invalid regular expression"),
        (r"(?P<v>\d+)", "(?P"),
        ("(?i)ubuntu", "inline flags"),
        ("(?i:ubuntu)", "inline flags"),
        (r"\Aubuntu\Z", r"\A"),
        ("(?#comment)x", "(?#"),
        ("(?>a+)", "(?>"),
        ("a++", "possessive"),
        ("[]a]", "character class"),
        ("[^]a]", "character class"),
    ],
)
def test_rejected_patterns(match, message):
    with pytest.raises(ValidationError) as error:
        _pattern(match)
    assert message in str(error.value)


def test_tooltip_groups_must_exist():
    assert _pattern(r"(a)(b)", "{0} {1} {2}").tooltip == "{0} {1} {2}"
    assert _pattern("a", "").tooltip == ""
    assert _pattern("a", "{x} {}").tooltip == "{x} {}"  # no placeholders
    with pytest.raises(ValidationError, match=r"\{2\}.*only 1 group"):
        _pattern(r"(a)b", "{2}")
