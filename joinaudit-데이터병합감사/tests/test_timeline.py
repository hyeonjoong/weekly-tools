"""날짜 해석과 자정 넘김 귀속 — 손으로 계산한 기대값과 대조한다.

수면 자료에서 가장 조용한 오류가 여기서 난다. 03:20 의 HRV 값이 어느 밤에
속하는지를 파일마다 다르게 정하면 표는 완성되지만 하루씩 어긋난다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from joinaudit.timeline import (VisitNormalizer, night_of, parse_cutoff,
                                parse_date_cell, plan_date_column)

NOON = _dt.time(12, 0)


def _one(token: str):
    plan = plan_date_column([token])
    return parse_date_cell(token, plan)


# --------------------------------------------------------------------------
# 자정 넘김 귀속 — 이 툴의 핵심 규칙
# --------------------------------------------------------------------------

def test_before_and_after_midnight_land_on_the_same_night():
    """23:40 과 다음날 03:20 은 **같은 밤**, 같은 날 13:00 은 **다음 밤**."""
    plan = plan_date_column(["2026-03-10 23:40", "2026-03-11 03:20",
                             "2026-03-11 13:00"])
    got = [night_of(parse_date_cell(t, plan), NOON) for t in
           ("2026-03-10 23:40", "2026-03-11 03:20", "2026-03-11 13:00")]
    assert got == [_dt.date(2026, 3, 10),      # 취침 직후
                   _dt.date(2026, 3, 10),      # 자정을 넘긴 같은 밤
                   _dt.date(2026, 3, 11)]      # 정오를 넘겼으니 다음 밤


def test_cutoff_boundary_is_exclusive_at_the_cutoff_itself():
    plan = plan_date_column(["2026-03-11 12:00", "2026-03-11 11:59"])
    assert night_of(parse_date_cell("2026-03-11 12:00", plan), NOON) == \
        _dt.date(2026, 3, 11)
    assert night_of(parse_date_cell("2026-03-11 11:59", plan), NOON) == \
        _dt.date(2026, 3, 10)


def test_custom_cutoff_moves_the_boundary():
    plan = plan_date_column(["2026-03-11 05:00"])
    parsed = parse_date_cell("2026-03-11 05:00", plan)
    assert night_of(parsed, _dt.time(4, 0)) == _dt.date(2026, 3, 11)
    assert night_of(parsed, _dt.time(6, 0)) == _dt.date(2026, 3, 10)


def test_date_without_time_is_not_shifted():
    """시각을 모르는데 하루를 옮기는 것이 더 큰 오류다."""
    plan = plan_date_column(["2026-03-10"])
    assert night_of(parse_date_cell("2026-03-10", plan), NOON) == \
        _dt.date(2026, 3, 10)


def test_month_and_year_boundaries():
    plan = plan_date_column(["2026-01-01 02:00"])
    assert night_of(parse_date_cell("2026-01-01 02:00", plan), NOON) == \
        _dt.date(2025, 12, 31)


def test_leap_day_boundary():
    plan = plan_date_column(["2028-03-01 01:00"])
    assert night_of(parse_date_cell("2028-03-01 01:00", plan), NOON) == \
        _dt.date(2028, 2, 29)


@pytest.mark.parametrize("bad", ["25:00", "12:99", "abc", "12", ""])
def test_parse_cutoff_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        parse_cutoff(bad)


# --------------------------------------------------------------------------
# 날짜 형식 판정
# --------------------------------------------------------------------------

def test_iso_dates_are_not_reported_as_ambiguous():
    """`2026-03-10` 은 어느 규칙으로 읽어도 같은 날이다 — 사람을 붙잡지 않는다."""
    plan = plan_date_column(["2026-03-10", "2026-03-11"])
    assert plan.ambiguous is False
    assert plan.parsed == 2


def test_genuinely_ambiguous_column_is_flagged():
    """`03/01/2026` 만 있으면 3월 1일인지 1월 3일인지 알 수 없다."""
    plan = plan_date_column(["03/01/2026", "05/02/2026"])
    assert plan.ambiguous is True
    assert set(plan.candidates) == {"dmy", "mdy"}


def test_a_single_impossible_day_settles_the_ambiguity():
    """`13/01/2026` 은 13월이 없으므로 dmy 로 확정된다."""
    plan = plan_date_column(["03/01/2026", "13/01/2026"])
    assert plan.ambiguous is False
    assert plan.order == "dmy"
    assert parse_date_cell("03/01/2026", plan).date == _dt.date(2026, 1, 3)


def test_iso_plus_dmy_is_still_explainable():
    """`2026-03-10` 은 연-월-일이 자명하고 `13/01/2026` 은 dmy 로만 읽힌다.

    두 값을 동시에 설명하는 규칙이 정확히 하나 남으므로 이것은 '혼재'가 아니다.
    """
    plan = plan_date_column(["2026-03-10", "13/01/2026"])
    assert plan.candidates == ("dmy",)
    assert plan.ambiguous is False


def test_truly_mixed_formats_are_reported_not_guessed():
    """`13/01`(dmy 확정) 과 `01/13`(mdy 확정) 은 한 규칙으로 설명할 수 없다."""
    plan = plan_date_column(["13/01/2026", "01/13/2026"])
    assert plan.candidates == ()
    assert plan.ambiguous is True
    assert "혼재" in plan.note


@pytest.mark.parametrize("token, expected", [
    ("2026-03-10", _dt.date(2026, 3, 10)),
    ("2026/03/10", _dt.date(2026, 3, 10)),
    ("2026.03.10", _dt.date(2026, 3, 10)),
    ("20260310", _dt.date(2026, 3, 10)),
    ("2026년 3월 10일", _dt.date(2026, 3, 10)),
    ("26.3.10", _dt.date(2026, 3, 10)),
])
def test_common_korean_and_iso_notations(token, expected):
    parsed = _one(token)
    assert parsed is not None and parsed.date == expected


def test_excel_date_serial_column():
    """엑셀이 날짜를 숫자로 내보낸 CSV.

    46091 = 2026-03-10 (1899-12-30 부터 센 날 수 — 엑셀의 1900-02-29 버그 포함).
    """
    plan = plan_date_column(["46091", "46092"])
    assert plan.excel_serial is True
    assert parse_date_cell("46091", plan).date == _dt.date(2026, 3, 10)
    assert parse_date_cell("46092", plan).date == _dt.date(2026, 3, 11)


def test_impossible_date_fails_instead_of_wrapping():
    plan = plan_date_column(["2026-03-10", "2026-03-11"])
    assert parse_date_cell("2026-13-45", plan) is None


def test_hour_24_is_rejected_because_it_would_move_the_day():
    plan = plan_date_column(["2026-03-10 23:00"])
    assert parse_date_cell("2026-03-10 24:00", plan) is None


def test_timezone_offsets_are_recorded_not_converted():
    plan = plan_date_column(["2026-03-10 23:40+09:00", "2026-03-11 03:20"])
    assert plan.offsets == {"+09:00"}
    assert plan.naive_count == 1
    assert plan.mixed_timezone is True
    # 오프셋이 있어도 값 자체는 그대로 읽는다(변환하지 않는다).
    parsed = parse_date_cell("2026-03-10 23:40+09:00", plan)
    assert parsed.date == _dt.date(2026, 3, 10) and parsed.offset == "+09:00"


def test_uniform_offsets_are_not_mixed():
    plan = plan_date_column(["2026-03-10 23:40+09:00", "2026-03-11 03:20+09:00"])
    assert plan.mixed_timezone is False


# --------------------------------------------------------------------------
# 방문 라벨
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("BL", "baseline"), ("기저", "baseline"), ("baseline", "baseline"),
    ("W4", "week4"), ("4주", "week4"), ("week 4", "week4"),
    ("V2", "visit2"), ("방문2", "visit2"), ("D14", "day14"),
])
def test_known_visit_labels(raw, expected):
    label, known = VisitNormalizer()(raw)
    assert (label, known) == (expected, True)


def test_v1_is_not_silently_called_baseline():
    """`V1` 을 baseline 으로 볼지는 프로토콜마다 다르다 — 툴이 정하지 않는다."""
    label, known = VisitNormalizer()("V1")
    assert label == "visit1" and label != "baseline"


def test_unknown_label_is_kept_and_flagged():
    label, known = VisitNormalizer()("2차추적방문")
    assert known is False
    assert label == "2차추적방문"      # 추측하지 않고 원본을 그대로 쓴다


def test_spec_supplied_aliases_win():
    norm = VisitNormalizer({"baseline": ["V1", "방문1"]})
    assert norm("V1") == ("baseline", True)
    assert norm("방문1") == ("baseline", True)
