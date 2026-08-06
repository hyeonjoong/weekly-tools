"""1박 지표 계산 — 손으로 검산할 수 있는 밤들로 검증."""

import datetime

import pytest

from sleepdiary.nightly import build_night, parse_date

COLS = {
    "subject": "subject", "date": "date", "period": "period",
    "bedtime": "bedtime", "lights_off": "lights_off", "sol": "sol",
    "waso": "waso", "awakenings": "awakenings",
    "final_awake": "final_awake", "out_of_bed": "out_of_bed",
}


def night(**over):
    """기준이 되는 정상적인 밤 하나 (23:00 취침 → 07:00 기상)."""
    row = {"subject": "S1", "date": "2026-03-02", "period": "base",
           "bedtime": "22:50", "lights_off": "23:00", "sol": "20", "waso": "30",
           "awakenings": "2", "final_awake": "07:00", "out_of_bed": "07:15"}
    row.update(over)
    return build_night(row, COLS, row_no=2)


# ---------------------------------------------------------------- 기본 계산

def test_reference_night_matches_hand_computation():
    n = night()
    assert n.valid, n.errors
    assert n.tib == pytest.approx(8 * 60 + 25)        # 22:50 → 07:15
    assert n.spt == pytest.approx(8 * 60)             # 23:00 → 07:00
    assert n.tst == pytest.approx(8 * 60 - 20 - 30)   # SPT − SOL − WASO = 430
    assert n.twak == pytest.approx(15)                # 07:00 → 07:15
    assert n.se == pytest.approx(430 / 505 * 100)
    assert n.onset == pytest.approx(23 * 60 + 20)     # 23:20 입면
    assert n.awakenings == 2.0


def test_midsleep_is_the_midpoint_between_onset_and_final_awakening():
    n = night()
    # 23:20 입면 → 07:00 기상 = 460분, 중점은 입면 + 230분 = 03:10
    assert n.midsleep == pytest.approx(3 * 60 + 10)


def test_midsleep_stays_on_the_clock_when_it_wraps_past_midnight():
    n = night(lights_off="21:00", sol="0", final_awake="05:00", out_of_bed="05:10",
              bedtime="21:00", waso="0")
    assert 0 <= n.midsleep < 1440
    assert n.midsleep == pytest.approx(1 * 60)        # 21:00~05:00 의 중점 = 01:00


def test_sleep_efficiency_is_tst_over_tib_not_over_spt():
    n = night(bedtime="22:00", out_of_bed="08:00")    # TIB = 10시간
    assert n.tib == pytest.approx(600)
    assert n.tst == pytest.approx(430)
    assert n.se == pytest.approx(430 / 600 * 100)


def test_missing_sol_and_waso_default_to_zero_and_that_inflates_efficiency():
    """결측을 0으로 채우는 것은 의도된 동작이며 SE를 낙관적으로 만든다."""
    n = night(sol="", waso="")
    assert n.sol == 0.0 and n.waso == 0.0
    assert n.tst == pytest.approx(n.spt)


def test_all_night_awake_at_the_edge_is_flagged_not_silently_negative():
    n = night(sol="300", waso="200")                  # SOL+WASO(500) > SPT(480)
    assert not n.valid
    assert any("TST" in e for e in n.errors)


# ---------------------------------------------------------------- 자정 넘김

def test_bedtime_after_midnight_still_computes_a_normal_night():
    n = night(bedtime="01:30", lights_off="01:40", final_awake="09:00",
              out_of_bed="09:10", sol="10", waso="5")
    assert n.valid, n.errors
    assert n.tib == pytest.approx(7 * 60 + 40)
    assert n.spt == pytest.approx(7 * 60 + 20)
    assert n.tst == pytest.approx(7 * 60 + 20 - 15)


def test_evening_sleeper_who_never_crosses_midnight():
    n = night(bedtime="20:00", lights_off="20:10", final_awake="23:30",
              out_of_bed="23:40", sol="10", waso="0")
    assert n.valid, n.errors
    assert n.tib == pytest.approx(3 * 60 + 40)
    assert n.tst == pytest.approx(3 * 60 + 20 - 10)


# ---------------------------------------------------------------- 오류 검출

def test_swapped_final_awake_and_out_of_bed_is_rejected():
    """최종기상이 침대에서 나온 시각보다 늦으면 SPT > TIB 가 되어 걸려야 한다."""
    n = night(final_awake="07:15", out_of_bed="07:00")
    assert not n.valid
    assert any("SPT" in e and "TIB" in e for e in n.errors)


def test_identical_bed_and_rise_time_is_rejected_rather_than_called_24_hours():
    n = night(bedtime="23:00", out_of_bed="23:00")
    assert not n.valid
    assert any("TIB=0" in e for e in n.errors)


def test_absurdly_long_time_in_bed_is_rejected():
    n = night(bedtime="20:00", out_of_bed="15:00")     # 19시간
    assert not n.valid
    assert n.tib == pytest.approx(19 * 60)             # 임계값(18시간)을 넘는다
    assert any("TIB" in e for e in n.errors)


def test_negative_latency_is_an_error_not_a_negative_number():
    n = night(sol="-15")
    assert not n.valid
    assert any("음수" in e for e in n.errors)


def test_unparseable_time_names_the_field_that_failed():
    n = night(final_awake="N/A")
    assert not n.valid
    assert any("final_awake" in e for e in n.errors)


def test_invalid_rows_carry_no_derived_metrics():
    n = night(final_awake="모름")
    assert n.tst is None and n.se is None and n.tib is None


# ---------------------------------------------------------------- 경고

def test_extreme_but_possible_values_warn_and_stay_in_the_analysis():
    n = night(sol="250")                               # 4시간 넘게 못 잠
    assert n.valid                                     # 제외하지 않는다
    assert any("입면잠복기" in w for w in n.warnings)


def test_low_efficiency_warns():
    n = night(sol="200", waso="150")                   # TST 130 / TIB 505 = 25.7%
    assert n.valid
    assert any("수면효율" in w for w in n.warnings)


def test_unreadable_awakening_count_warns_but_keeps_the_night():
    n = night(awakenings="2회")
    assert n.valid
    assert n.awakenings is None
    assert any("각성횟수" in w for w in n.warnings)


def test_negative_awakening_count_is_dropped_with_a_warning():
    n = night(awakenings="-1")
    assert n.valid and n.awakenings is None


# ---------------------------------------------------------------- 날짜 처리

def test_diary_date_defaults_to_the_morning_so_the_night_belongs_to_the_day_before():
    n = build_night({**{"subject": "S1", "date": "2026-03-02", "period": "",
                        "bedtime": "23:00", "lights_off": "23:00", "sol": "10",
                        "waso": "0", "awakenings": "1", "final_awake": "07:00",
                        "out_of_bed": "07:10"}}, COLS, 2, date_means="morning")
    assert n.date == datetime.date(2026, 3, 1)


def test_evening_dated_diary_keeps_the_date_as_written():
    n = build_night({"subject": "S1", "date": "2026-03-02", "period": "",
                     "bedtime": "23:00", "lights_off": "23:00", "sol": "10",
                     "waso": "0", "awakenings": "1", "final_awake": "07:00",
                     "out_of_bed": "07:10"}, COLS, 2, date_means="evening")
    assert n.date == datetime.date(2026, 3, 2)


@pytest.mark.parametrize("text", ["03/04/2026", "4/3/26", "2026년 3월 4일", "", "오늘"])
def test_ambiguous_dates_are_left_unparsed_on_purpose(text):
    """MM/DD 인지 DD/MM 인지 알 수 없는 형식은 추측하지 않는다."""
    assert parse_date(text) is None


@pytest.mark.parametrize("text,expected", [
    ("2026-03-04", datetime.date(2026, 3, 4)),
    ("2026/03/04", datetime.date(2026, 3, 4)),
    ("2026.3.4", datetime.date(2026, 3, 4)),
])
def test_iso_dates_are_parsed(text, expected):
    assert parse_date(text) == expected


def test_impossible_date_is_none_not_an_exception():
    assert parse_date("2026-02-30") is None


# ---------------------------------------------------------------- 열 대체

def test_missing_lights_off_falls_back_to_bedtime():
    cols = dict(COLS, lights_off=None)
    n = build_night({"subject": "S1", "date": "", "period": "", "bedtime": "23:00",
                     "sol": "15", "waso": "0", "awakenings": "1",
                     "final_awake": "07:00", "out_of_bed": "07:00"}, cols, 2)
    assert n.valid, n.errors
    assert n.lights_off == n.bedtime == 23 * 60


def test_as_dict_round_trips_every_reported_field():
    d = night().as_dict()
    for key in ("row", "subject", "tib_min", "tst_min", "se_pct", "midsleep_min",
                "valid", "errors", "warnings"):
        assert key in d
    assert d["valid"] is True and d["errors"] == []
