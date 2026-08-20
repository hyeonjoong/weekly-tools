"""날짜 파서 — 완료 기준의 형식 목록을 하나씩, 손으로 계산한 값과 대조."""

import datetime as dt

import pytest

from visitaudit.dates import iso, parse_asof, parse_date


def d(y, m, day):
    return dt.date(y, m, day)


@pytest.mark.parametrize("raw,expected", [
    ("2026-03-02", d(2026, 3, 2)),
    ("2026/03/02", d(2026, 3, 2)),
    ("2026.3.2", d(2026, 3, 2)),
    ("2026년 3월 2일", d(2026, 3, 2)),
    ("20260302", d(2026, 3, 2)),
    ("  2026-03-02  ", d(2026, 3, 2)),
    ("2026-3-2", d(2026, 3, 2)),
    ("2028-02-29", d(2028, 2, 29)),       # 윤년 — 실제 존재하는 날
    ("20280229", d(2028, 2, 29)),
    ("2026-12-31", d(2026, 12, 31)),
    ("2026-01-31", d(2026, 1, 31)),       # 월말
])
def test_formats_ok(raw, expected):
    p = parse_date(raw)
    assert p.date == expected
    assert p.error is None
    assert p.had_time is False


@pytest.mark.parametrize("raw,expected", [
    # 잘 알려진 엑셀 시리얼 고정값: 44927 = 2023-01-01, 45292 = 2024-01-01
    ("44927", d(2023, 1, 1)),
    ("45292", d(2024, 1, 1)),
])
def test_excel_serial_hand_values(raw, expected):
    p = parse_date(raw)
    assert p.date == expected
    assert p.error is None


def test_excel_serial_fraction_is_time():
    p = parse_date("44927.5")
    assert p.date == d(2023, 1, 1)
    assert p.had_time is True


@pytest.mark.parametrize("raw,expected", [
    ("2026-03-02 14:30", d(2026, 3, 2)),
    ("2026-03-02T14:30", d(2026, 3, 2)),
    ("2026-03-02T14:30:00", d(2026, 3, 2)),
    ("2026/3/2 09:00", d(2026, 3, 2)),
    ("2026-03-02 9:05:59", d(2026, 3, 2)),
])
def test_time_stripped_and_confessed(raw, expected):
    p = parse_date(raw)
    assert p.date == expected
    assert p.had_time is True      # 시각을 버렸다는 사실이 플래그로 남는다
    assert p.error is None


@pytest.mark.parametrize("raw", [
    "", "   ", None,
    "abc",
    "07-06-2026",        # 일/월-먼저 — 추측하지 않는다
    "03/01/2026",
    "2026-13-01",        # 없는 달
    "2026-02-30",        # 없는 날
    "2027-02-29",        # 평년의 2/29
    "2026.2.29",         # 2026 은 평년
    "20261301",
    "123",               # 엑셀 시리얼 범위 밖 (너무 작음)
    "99999",             # 범위 밖 (너무 큼)
    "19999",             # 하한 바로 아래
    "80001",             # 상한 바로 위
    "2026-03",
    "-5",
    "2026-03-02 오후2시",  # 시각 형식이 아님
])
def test_parse_failures(raw):
    p = parse_date(raw)
    assert p.date is None
    assert p.error  # 실패 사유가 반드시 남는다


def test_serial_bounds_inclusive():
    assert parse_date("20000").date == d(1899, 12, 30) + dt.timedelta(days=20000)
    assert parse_date("80000").date == d(1899, 12, 30) + dt.timedelta(days=80000)


def test_asof_ok():
    assert parse_asof("2026-08-14") == d(2026, 8, 14)
    assert parse_asof("  2026-08-14 ") == d(2026, 8, 14)


@pytest.mark.parametrize("raw", [
    "2026-08-14 10:00", "garbage", "",
    # B2 회귀: 문서는 'YYYY-MM-DD 만'을 약속한다 — 시리얼·점·한글·구분자없음 전부 거부.
    # 특히 "44927" 이 조용히 2023-01-01 이 되는 것이 위험했다.
    "44927", "2026.8.14", "2026년 8월 14일", "20260814", "2026/08/14",
    "2026-8-14",          # 자릿수까지 엄격하게
    "2026-13-01",         # 형식은 맞지만 달력에 없음
])
def test_asof_rejects_nonstrict(raw):
    with pytest.raises(ValueError):
        parse_asof(raw)


def test_iso_helper():
    assert iso(d(2026, 3, 2)) == "2026-03-02"
    assert iso(None) == ""
