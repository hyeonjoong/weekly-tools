"""날짜 파싱·되쓰기·야간 귀속."""

from __future__ import annotations

import datetime as _dt

import pytest

from deidaudit.dates import (
    ambiguous_date_ratio,
    date_ratio,
    looks_like_birth,
    night_date,
    parse_date,
    render_date,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-03-14", _dt.datetime(2026, 3, 14)),
        ("2026/3/14", _dt.datetime(2026, 3, 14)),
        ("2026.03.14", _dt.datetime(2026, 3, 14)),
        ("20260314", _dt.datetime(2026, 3, 14)),
        ("2026년 3월 14일", _dt.datetime(2026, 3, 14)),
        ("2026-03-13 23:40", _dt.datetime(2026, 3, 13, 23, 40)),
        ("2026-03-13T23:40:07", _dt.datetime(2026, 3, 13, 23, 40, 7)),
    ],
)
def test_parse_supported_formats(text, expected):
    parsed = parse_date(text)
    assert parsed is not None and parsed.value == expected


@pytest.mark.parametrize("text", ["03/14/2026", "14/03/2026", "3-14-26", "", "없음", "2026-13-01", "2026-02-30"])
def test_reject_ambiguous_or_invalid(text):
    assert parse_date(text) is None


def test_render_round_trips_original_layout():
    for text in ["2026-03-14", "2026/3/14", "2026.03.14", "20260314", "2026-03-13 23:40"]:
        parsed = parse_date(text)
        assert render_date(parsed, parsed.value) == text


def test_render_preserves_time_and_shifts_date():
    parsed = parse_date("2026-03-13 23:40")
    shifted = render_date(parsed, parsed.value + _dt.timedelta(days=-100))
    assert shifted == "2025-12-03 23:40"


def test_night_date_attributes_past_midnight_to_previous_day():
    assert night_date(_dt.datetime(2026, 3, 14, 3, 20)) == _dt.date(2026, 3, 13)
    assert night_date(_dt.datetime(2026, 3, 13, 23, 40)) == _dt.date(2026, 3, 13)
    assert night_date(_dt.datetime(2026, 3, 14, 12, 0)) == _dt.date(2026, 3, 14)


def test_looks_like_birth():
    today = _dt.date(2026, 8, 27)
    assert looks_like_birth(_dt.datetime(1988, 4, 2), today)
    assert not looks_like_birth(_dt.datetime(2026, 3, 14), today)   # 올해
    assert not looks_like_birth(_dt.datetime(1850, 1, 1), today)    # 120년 초과


def test_ratios():
    values = ["2026-03-14", "2026-03-21", "", "없음"]
    assert date_ratio(values) == pytest.approx(2 / 3)
    assert ambiguous_date_ratio(["03/14/2026", "04/01/2026"]) == 1.0
    assert date_ratio([]) == 0.0
