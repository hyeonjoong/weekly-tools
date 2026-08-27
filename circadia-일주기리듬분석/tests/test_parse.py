"""파싱 — 인코딩·구분자·별칭·타임스탬프 규율·벤더 3벌 등가성."""

import datetime as dt
import os

import pytest

from circadia.parse import (CircadiaError, check_cross_file_offsets,
                            parse_timestamp, read_series, read_sleep)

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def w(tmp_path, name, content, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(content, encoding=encoding)
    return str(p)


# ---------------------------------------------------------------- 타임스탬프

def test_timestamp_formats_iso_us_dotted():
    assert parse_timestamp("2026-08-03 23:30:00")[0] == dt.datetime(2026, 8, 3, 23, 30)
    assert parse_timestamp("2026-08-03T23:30")[0] == dt.datetime(2026, 8, 3, 23, 30)
    assert parse_timestamp("2026-08-03 23:30:00.000")[0] == dt.datetime(2026, 8, 3, 23, 30)
    assert parse_timestamp("8/3/2026 11:30:00 PM")[0] == dt.datetime(2026, 8, 3, 23, 30)
    assert parse_timestamp("8/3/2026 12:05:00 AM")[0] == dt.datetime(2026, 8, 3, 0, 5)
    assert parse_timestamp("2026.08.03 23:30")[0] == dt.datetime(2026, 8, 3, 23, 30)


def test_timestamp_offset_recorded_not_converted():
    stamp, off = parse_timestamp("2026-08-03 23:30:00 +0900")
    assert stamp == dt.datetime(2026, 8, 3, 23, 30)   # 로컬 시각 유지
    assert off == 540
    assert parse_timestamp("2026-08-03T23:30:00Z")[1] == 0
    assert parse_timestamp("2026-08-03 23:30:00 -05:00")[1] == -300


def test_epoch_timestamp_refused_with_explanation():
    with pytest.raises(CircadiaError, match="epoch"):
        parse_timestamp("1754236200")
    with pytest.raises(CircadiaError, match="epoch"):
        parse_timestamp("1754236200000")


def test_unknown_timestamp_refused():
    with pytest.raises(CircadiaError, match="인식하지"):
        parse_timestamp("셋째 주 화요일쯤")


# ---------------------------------------------------------------- 인코딩·구분자

def test_utf8_sig_bom(tmp_path):
    p = w(tmp_path, "a.csv", "timestamp,hr\n2026-08-03 00:00,70\n2026-08-03 00:05,71\n",
          encoding="utf-8-sig")
    s = read_series(p, "심박")
    assert s.meta.encoding == "utf-8-sig"
    assert s.samples[0] == (dt.datetime(2026, 8, 3), 70.0)


def test_cp949_korean_headers(tmp_path):
    p = w(tmp_path, "a.csv", "시각,심박수\n2026-08-03 00:00,70\n2026-08-03 00:05,71\n",
          encoding="cp949")
    s = read_series(p, "심박")
    assert s.meta.encoding == "cp949"
    assert [v for _, v in s.samples] == [70.0, 71.0]


def test_semicolon_delimiter(tmp_path):
    p = w(tmp_path, "a.csv", "timestamp;hr\n2026-08-03 00:00;70\n2026-08-03 00:05;71\n")
    s = read_series(p, "심박")
    assert s.meta.delimiter == ";"
    assert len(s.samples) == 2


# ---------------------------------------------------------------- 열 인식

def test_ambiguous_columns_refused_never_guessed(tmp_path):
    p = w(tmp_path, "a.csv", "timestamp,time,hr\n2026-08-03 00:00,x,70\n")
    with pytest.raises(CircadiaError, match="후보가 여러"):
        read_series(p, "심박")


def test_column_override_wins(tmp_path):
    p = w(tmp_path, "a.csv", "when,pulse\n2026-08-03 00:00,70\n2026-08-03 00:05,71\n")
    s = read_series(p, "심박", time_col="when", value_col="pulse")
    assert s.meta.columns["값"].how == "지정"
    assert len(s.samples) == 2


def test_missing_column_error_names_actual_columns(tmp_path):
    p = w(tmp_path, "a.csv", "when,pulse\n2026-08-03 00:00,70\n")
    with pytest.raises(CircadiaError, match="pulse"):
        read_series(p, "심박")


# ---------------------------------------------------------------- 값 검증·자백

def test_out_of_range_hr_excluded_and_confessed(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00,70\n2026-08-03 00:05,300\n"
          "2026-08-03 00:10,0\n2026-08-03 00:15,71\n")
    s = read_series(p, "심박")
    assert len(s.samples) == 2
    assert sum(s.meta.excluded.values()) == 2
    assert any("범위 밖" in k for k in s.meta.excluded)


def test_nan_and_nonnumeric_excluded(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00,nan\n2026-08-03 00:05,측정실패\n"
          "2026-08-03 00:10,70\n")
    s = read_series(p, "심박")
    assert len(s.samples) == 1
    assert s.meta.excluded.get("nan/inf") == 1
    assert s.meta.excluded.get("숫자 아님") == 1


def test_negative_steps_excluded(tmp_path):
    p = w(tmp_path, "a.csv", "timestamp,steps\n2026-08-03 00:00,-5\n2026-08-03 01:00,10\n")
    s = read_series(p, "걸음")
    assert len(s.samples) == 1 and s.meta.excluded["음수 걸음"] == 1


# ---------------------------------------------------------------- 시간 규율

def test_non_monotonic_refused(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 01:00,70\n2026-08-03 00:00,71\n")
    with pytest.raises(CircadiaError, match="역행"):
        read_series(p, "심박")


def test_duplicate_timestamp_refused(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00,70\n2026-08-03 00:00,71\n")
    with pytest.raises(CircadiaError, match="중복"):
        read_series(p, "심박")


def test_future_timestamp_refused(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00,70\n2050-01-01 00:00,71\n")
    with pytest.raises(CircadiaError, match="미래"):
        read_series(p, "심박")


def test_mixed_tz_offsets_refused(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00:00 +0900,70\n2026-08-03 01:00:00 +0800,71\n")
    with pytest.raises(CircadiaError, match="섞여"):
        read_series(p, "심박")


def test_partial_tz_offsets_refused(tmp_path):
    p = w(tmp_path, "a.csv",
          "timestamp,hr\n2026-08-03 00:00:00 +0900,70\n2026-08-03 01:00:00,71\n")
    with pytest.raises(CircadiaError, match="일부 행"):
        read_series(p, "심박")


def test_cross_file_conflicting_offsets_refused(tmp_path):
    p1 = w(tmp_path, "a.csv", "timestamp,hr\n2026-08-03 00:00:00 +0900,70\n"
           "2026-08-03 00:05:00 +0900,71\n")
    p2 = w(tmp_path, "b.csv", "timestamp,steps\n2026-08-03 00:00:00 +0000,5\n"
           "2026-08-03 01:00:00 +0000,6\n")
    s1, s2 = read_series(p1, "심박"), read_series(p2, "걸음")
    with pytest.raises(CircadiaError, match="서로 다릅"):
        check_cross_file_offsets([s1.meta, s2.meta])


# ---------------------------------------------------------------- 수면 파일

def test_sleep_end_before_start_refused(tmp_path):
    p = w(tmp_path, "s.csv", "start,end\n2026-08-03 23:00,2026-08-03 22:00\n")
    with pytest.raises(CircadiaError, match="빠르거나"):
        read_sleep(p)


def test_sleep_over_24h_refused(tmp_path):
    p = w(tmp_path, "s.csv", "start,end\n2026-08-03 23:00,2026-08-05 07:00\n")
    with pytest.raises(CircadiaError, match="24시간"):
        read_sleep(p)


def test_apple_stage_rows_filtered_and_merged(tmp_path):
    p = w(tmp_path, "s.csv",
          "startDate,endDate,value\n"
          "2026-08-03 22:55,2026-08-04 07:05,HKCategoryValueSleepAnalysisInBed\n"
          "2026-08-03 23:00,2026-08-04 01:00,HKCategoryValueSleepAnalysisAsleepCore\n"
          "2026-08-04 01:00,2026-08-04 03:00,HKCategoryValueSleepAnalysisAsleepDeep\n"
          "2026-08-04 03:00,2026-08-04 07:00,HKCategoryValueSleepAnalysisAsleepREM\n")
    sl = read_sleep(p)
    assert sl.intervals == [(dt.datetime(2026, 8, 3, 23), dt.datetime(2026, 8, 4, 7))]
    assert sl.meta.excluded["각성/침대(InBed·Awake) 단계 행"] == 1
    assert any("병합" in n for n in sl.meta.notes)


def test_unknown_stage_label_refused_not_guessed(tmp_path):
    p = w(tmp_path, "s.csv",
          "start,end,stage\n2026-08-03 23:00,2026-08-04 07:00,mystery_state\n")
    with pytest.raises(CircadiaError, match="알 수 없는 수면 단계"):
        read_sleep(p)


def test_overlapping_sleep_intervals_merged_with_note(tmp_path):
    p = w(tmp_path, "s.csv",
          "start,end\n2026-08-03 23:00,2026-08-04 03:00\n"
          "2026-08-04 02:00,2026-08-04 07:00\n")
    sl = read_sleep(p)
    assert sl.intervals == [(dt.datetime(2026, 8, 3, 23), dt.datetime(2026, 8, 4, 7))]
    assert any("병합" in n for n in sl.meta.notes)


# ---------------------------------------------------------------- 벤더 3벌 등가성

@pytest.mark.parametrize("scenario", ["규칙적_1주", "불규칙_1주"])
def test_three_vendor_flavors_parse_to_identical_content(scenario):
    """같은 시나리오의 애플/삼성/핏빗 파일은 표기만 다르고 내용이 같아야 한다."""
    parsed = {}
    for vendor in ("애플건강", "삼성헬스", "핏빗"):
        d = os.path.join(EXAMPLES, f"{scenario}_{vendor}")
        hr = read_series(os.path.join(d, "심박.csv"), "심박")
        st = read_series(os.path.join(d, "걸음.csv"), "걸음")
        sl = read_sleep(os.path.join(d, "수면.csv"))
        parsed[vendor] = (hr.samples, st.samples, sl.intervals)
    assert parsed["애플건강"] == parsed["삼성헬스"] == parsed["핏빗"]


def test_vendor_alias_columns_recognized():
    d = os.path.join(EXAMPLES, "규칙적_1주_애플건강")
    hr = read_series(os.path.join(d, "심박.csv"), "심박")
    assert hr.meta.columns["시각"].raw_name == "startDate"
    assert hr.meta.columns["값"].raw_name == "value"
    d = os.path.join(EXAMPLES, "규칙적_1주_삼성헬스")
    st = read_series(os.path.join(d, "걸음.csv"), "걸음")
    assert st.meta.columns["값"].raw_name == "step_count"
    d = os.path.join(EXAMPLES, "규칙적_1주_핏빗")
    hr = read_series(os.path.join(d, "심박.csv"), "심박")
    assert hr.meta.columns["값"].raw_name == "Heart Rate"
