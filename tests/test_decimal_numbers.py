"""
Decimal numbers: `multipleOf` is checked in decimal (as the numbers are
written), not in binary floating point, and limit sums carry no float noise.
"""

import pytest

from app.lib import limits
from app.lib.schema import Draft7Validator
from app.model.int import Entity
from app.model.spc import TypeLimit

from tests.lib_limits import _edit_op, _specs_with


def _valid(multiple_of, instance) -> bool:
    return Draft7Validator({"type": "number", "multipleOf": multiple_of}).is_valid(instance)


@pytest.mark.parametrize("step", [0.005, 0.01, 0.1, 0.25, 0.5])
def test_every_decimal_multiple_is_valid(step):
    # jsonschema's float check rejects e.g. 0.035 / 0.005 = 7.000000000000001
    decimals = len(str(step).split(".")[1])
    values = [round(i * step, decimals) for i in range(1, 2001)]
    assert [v for v in values if not _valid(step, v)] == []


@pytest.mark.parametrize(
    "multiple_of, instance",
    [(0.005, 0.036), (0.005, 0.0051), (0.1, 0.35), (0.25, 0.3), (5, 7), (5, 7.5)],
)
def test_non_multiples_are_invalid(multiple_of, instance):
    assert not _valid(multiple_of, instance)


def test_integers_and_mixed():
    assert _valid(5, 10)
    assert _valid(5, 10.0)
    assert _valid(0.5, 3)
    assert _valid(0.005, 0)
    assert _valid(0.005, -0.035)


def test_non_numbers_are_ignored():
    # `type` reports those; multipleOf alone must not fail on them
    v = Draft7Validator({"multipleOf": 0.005})
    assert v.is_valid("0.035")
    assert v.is_valid(True)
    assert v.is_valid(None)


def test_inf_and_nan_are_never_multiples():
    assert not _valid(0.005, float("inf"))
    assert not _valid(0.005, float("-inf"))
    assert not _valid(0.005, float("nan"))


def test_huge_quotients_fall_back_to_the_float_check():
    # beyond the decimal precision (28 digits): no raise, jsonschema decides
    assert _valid(1e-30, 1e30)


def test_error_message():
    errors = list(Draft7Validator({"multipleOf": 0.005}).iter_errors(0.036))
    assert [e.message for e in errors] == ["0.036 is not a multiple of 0.005"]


async def test_limit_sums_carry_no_float_noise(fake_repo):
    rpo = fake_repo(files={"A": "cpus: 0.1", "C": "cpus: 0.2"})
    lim = TypeLimit.model_construct(
        title="cpus", on=["edit", "create"], scope="true",
        value="old.data.cpus | default(0)", max="0.6",
    )
    old = Entity(name="A", exists=True, data={"cpus": 0.1})
    usages = await limits.measure(
        "h", rpo, _edit_op({"cpus": 0.4}), _specs_with(lim), old, {"cpus": 0.4}
    )
    # 0.4 + 0.2 is 0.6000000000000001 in floats, which would be over the cap
    assert usages[0].used == 0.6
    assert usages[0].ok
