"""반올림 인지 매칭 — 손으로 계산한 경계 사례를 못 박아 둡니다.

여기서 한 자리라도 틀리면 '있는 출처를 없다'고 말하게 되고, 그 순간 리포트
전체가 신뢰를 잃습니다. 그래서 경계값은 전부 사람이 계산해서 적었습니다.
"""

from decimal import Decimal

import pytest

from tracecheck.bundle import Cell
from tracecheck.match import (NumberIndex, distinct_values,
                              inequality_bounds, match_method, needed_decimals,
                              norm_key, rounded_keys)


def cell(value, file="out.csv", row=2, col="mean"):
    return Cell(file=file, rel=file, sheet="", row=row, col=col, ordinal=0,
                raw=str(value), value=Decimal(str(value)),
                decimals=len(str(value).split(".")[1]) if "." in str(value) else 0,
                is_percent=False)


def index(values, decimals=(0, 1, 2, 3, 4)):
    return NumberIndex([cell(v) for v in values], decimals)


# --------------------------------------------------------------------------- #
# 반올림 후보 (손계산)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,decimals,expected", [
    # 12.35 → half-up 은 12.4, 부동소수(12.3499999…) 반올림은 12.3
    ("12.35", 1, {"12.4", "12.3"}),
    # -3.475 → half-up 은 -3.48, 절반 버림(half-down)은 -3.47
    ("-3.475", 2, {"-3.48", "-3.47"}),
    ("0.0021", 3, {"0.002"}),
    ("12.44", 1, {"12.4"}),
    ("11.68", 1, {"11.7"}),
    ("9.82", 1, {"9.8"}),
    ("0.5", 0, {"0", "1"}),          # half-even/half-down 은 0, half-up 은 1
    ("1.5", 0, {"1", "2"}),
    ("2.5", 0, {"2", "3"}),
    ("-0.004", 2, {"0"}),            # -0.00 은 0 으로 정규화
])
def test_rounded_keys(value, decimals, expected):
    assert rounded_keys(Decimal(value), decimals) == expected


def test_norm_key_normalizes_trailing_zeros_and_minus_zero():
    assert norm_key(Decimal("12.40")) == "12.4"
    assert norm_key(Decimal("-0.00")) == "0"
    assert norm_key(Decimal("1E+3")) == "1000"


# --------------------------------------------------------------------------- #
# 색인 조회
# --------------------------------------------------------------------------- #

def test_lookup_exact_and_rounded():
    idx = index(["12.44", "15.91"])
    assert [c.value for c in idx.lookup(Decimal("12.4"), 1)] == [Decimal("12.44")]
    assert [c.value for c in idx.lookup(Decimal("12.44"), 2)] == [Decimal("12.44")]
    assert idx.lookup(Decimal("12.5"), 1) == []


def test_lookup_both_rounding_conventions_are_accepted():
    idx = index(["12.35"])
    assert idx.lookup(Decimal("12.4"), 1)      # half-up 으로 적은 원고
    assert idx.lookup(Decimal("12.3"), 1)      # 통계 소프트웨어가 낸 값


def test_integer_manuscript_value_matches_rounded_output():
    idx = index(["83.7", "84"])
    values = {c.value for c in idx.lookup(Decimal("84"), 0)}
    assert values == {Decimal("83.7"), Decimal("84")}


def test_range_lookup_for_inequality():
    idx = index(["0.0003", "0.001", "0.02"])
    low, high, inc_low, inc_high = inequality_bounds("<", Decimal("0.001"))
    found = idx.lookup_range(low, high, include_low=inc_low, include_high=inc_high)
    assert [c.value for c in found] == [Decimal("0.0003")]


def test_range_lookup_greater_than():
    idx = index(["0.98", "0.995"])
    low, high, inc_low, inc_high = inequality_bounds(">", Decimal("0.99"))
    found = idx.lookup_range(low, high, include_low=inc_low, include_high=inc_high)
    assert [c.value for c in found] == [Decimal("0.995")]


def test_nearest_returns_closest_value():
    idx = index(["87.3", "42", "12.44"])
    assert idx.nearest(Decimal("91.2"))[0].value == Decimal("87.3")


def test_nearest_on_empty_index():
    assert NumberIndex([], (1, 2)).nearest(Decimal("1")) == []


def test_at_coord_finds_same_position_in_another_bundle():
    current = NumberIndex([cell("9.82", file="stat.csv", row=4, col="diff")], (2,))
    assert current.at_coord(("stat.csv", "", 4, "diff", 0))[0].value == \
        Decimal("9.82")
    assert current.at_coord(("other.csv", "", 4, "diff", 0)) == []


def test_needed_decimals_covers_percent_conversion():
    assert needed_decimals([2]) == {0, 2, 4}
    assert needed_decimals([0]) == {0, 2}


def test_match_method_labels():
    cells = [cell("12.44")]
    assert match_method(Decimal("12.44"), cells, 2) == "정확"
    assert match_method(Decimal("12.4"), cells, 1) == "반올림(1자리)"


def test_distinct_values_sorted_unique():
    assert distinct_values([cell("2"), cell("1"), cell("2")]) == \
        [Decimal("1"), Decimal("2")]


def test_index_only_builds_requested_decimals():
    """원고에 없는 자릿수까지 색인하면 큰 번들에서 메모리가 터집니다."""
    idx = NumberIndex([cell("12.3456")], (2,))
    assert idx.lookup(Decimal("12.35"), 2)
    assert idx.lookup(Decimal("12.3"), 1) == []


def test_huge_and_tiny_values_do_not_crash():
    idx = NumberIndex([cell("99999999999999999999"), cell("0.00000001")],
                      (0, 8))
    assert idx.lookup(Decimal("0.00000001"), 8)
    assert idx.nearest(Decimal("1"))[0].value == Decimal("0.00000001")
