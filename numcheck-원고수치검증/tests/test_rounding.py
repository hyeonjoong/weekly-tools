"""반올림 허용 구간 — 이 툴이 오탐으로 죽지 않게 하는 부품."""

from __future__ import annotations

import pytest

from numcheck.rounding import Reported, consistent, fmt, intervals_overlap, parse_number


def test_parse_plain_decimal():
    r = parse_number("47.9")
    assert r.value == pytest.approx(47.9)
    assert r.ulp == pytest.approx(0.1)
    assert r.decimals == 1


def test_parse_leading_dot():
    r = parse_number(".03")
    assert r.value == pytest.approx(0.03)
    assert r.ulp == pytest.approx(0.01)


def test_parse_integer():
    r = parse_number("48")
    assert r.value == 48.0
    assert r.ulp == 1.0
    assert r.decimals == 0


def test_parse_thousands_separator():
    r = parse_number("1,234")
    assert r.value == 1234.0
    assert r.ulp == 1.0


def test_parse_unicode_minus():
    for text in ("−7.4", "–7.4", "-7.4"):
        r = parse_number(text)
        assert r.value == pytest.approx(-7.4)
        assert r.ulp == pytest.approx(0.1)


def test_parse_scientific_notation():
    r = parse_number("1.2e-4")
    assert r.value == pytest.approx(1.2e-4)
    assert r.ulp == pytest.approx(1e-5)
    r2 = parse_number("2.5 × 10^-3")
    assert r2.value == pytest.approx(2.5e-3)
    assert r2.ulp == pytest.approx(1e-4)


def test_parse_rejects_garbage():
    for bad in ("", "  ", "abc", "1.2.3", "--3", "1e", None, "1,23"):
        assert parse_number(bad) is None


def test_proposal_example_all_roundings_accepted():
    """제안서의 기준: 실제 47.916% 는 47.9 · 48 · 47.92 모두 통과해야 한다."""
    truth = 23 / 48 * 100  # 47.9166…
    for text in ("47.9", "48", "47.92"):
        assert consistent(parse_number(text), truth)


def test_truncation_and_ceiling_conventions_accepted():
    """버림·올림도 허용한다: 참값 47.96 에서 47.9(버림)·48.0(올림) 모두 통과."""
    for text in ("47.9", "48.0"):
        assert consistent(parse_number(text), 47.96)


def test_clear_error_is_still_rejected():
    truth = 23 / 48 * 100
    assert not consistent(parse_number("45.2"), truth)
    assert not consistent(parse_number("47.5"), truth)


def test_tighter_tolerance_flags_more():
    """k = 0.5 이면 반올림만 허용 — 버림으로 만들어진 값은 걸린다."""
    r = parse_number("47.9")
    assert consistent(r, 47.96, k=1.0)
    assert not consistent(r, 47.96, k=0.5)


def test_interval_is_symmetric_and_padded():
    lo, hi = parse_number("2.31").interval(1.0)
    assert lo == pytest.approx(2.30, abs=1e-9)
    assert hi == pytest.approx(2.32, abs=1e-9)


def test_intervals_overlap():
    assert intervals_overlap((0.0, 1.0), (0.5, 2.0))
    assert intervals_overlap((0.0, 1.0), (1.0, 2.0))
    assert not intervals_overlap((0.0, 1.0), (1.001, 2.0))


def test_fmt_readable():
    assert fmt(0.0) == "0"
    assert fmt(47.9166, 4) == "47.9166"
    assert fmt(1.0) == "1"
    assert fmt(3e-7) == "3e-07"
    assert fmt(float("nan")) == "nan"


def test_reported_dataclass_is_frozen():
    r = Reported("1.0", 1.0, 0.1)
    with pytest.raises(Exception):
        r.value = 2.0  # type: ignore[misc]
