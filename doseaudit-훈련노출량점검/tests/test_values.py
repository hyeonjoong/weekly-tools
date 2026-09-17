"""단위가 붙은 문자열과 날짜를 숫자로 — 그리고 **실패를 조용히 삼키지 않기**."""

import datetime

import pytest

from doseaudit.values import (COUNT_UNITS, SECONDS_UNITS, ValueError_, fmt_date,
                              fmt_dot, parse_date, strip_unit)


@pytest.mark.parametrize("raw,expected", [
    ("0초", 0.0), ("4.04초", 4.04), ("12", 12.0), ("12.5", 12.5),
    ("  7초  ", 7.0), ("1,024초", 1024.0), ("３초", 3.0),
    ("+5", 5.0), ("0", 0.0), ("0.0초", 0.0),
    ("2초", 2.0), ("999999초", 999999.0), ("0.001초", 0.001),
    ("5sec", 5.0), ("5s", 5.0),
])
def test_기대한_단위를_뗀다(raw, expected):
    assert strip_unit(raw, SECONDS_UNITS) == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [("1회", 1.0), ("100회", 100.0), ("１２회", 12.0),
                                          ("3번", 3.0), ("7", 7.0)])
def test_횟수_단위(raw, expected):
    assert strip_unit(raw, COUNT_UNITS) == pytest.approx(expected)


@pytest.mark.parametrize("raw,wrong", [
    ("5분", "분"), ("3 분", "분"), ("2시간", "시간"), ("500ms", "밀리초"),
    ("500밀리초", "밀리초"), ("1h", "시간"),
])
def test_차원이_다른_단위는_환산하지_않고_거절한다(raw, wrong):
    """`소요시간(초)` 열의 `'5분'` 을 5.0 으로 읽으면 그 날 합계가 **60배** 줄어든다."""
    with pytest.raises(ValueError_) as info:
        strip_unit(raw, SECONDS_UNITS)
    assert wrong in str(info.value)
    assert "환산하지 않습니다" in str(info.value)


@pytest.mark.parametrize("raw", ["-5", "-100초", "-0.5초"])
def test_음수는_받지_않는다(raw):
    """`-100초` 를 평균에 넣으면 '평균 소요시간 -21.67초' 가 인쇄된다."""
    with pytest.raises(ValueError_) as info:
        strip_unit(raw, SECONDS_UNITS)
    assert "음수" in str(info.value)


def test_음수를_명시적으로_허용할_수도_있다():
    assert strip_unit("-5", SECONDS_UNITS, allow_negative=True) == -5.0


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_유한하지_않은_값은_거절(raw):
    with pytest.raises(ValueError_):
        strip_unit(raw, SECONDS_UNITS)


@pytest.mark.parametrize("raw", [
    "", "   ", None, "abc", "--초", "N/A", "없음", "초", "?", "-", "1.2.3",
    "1초2", "１２３가", "0x10", "7레벨", "1회",
])
def test_해석하지_못하면_예외다_0이_아니다(raw):
    """빈 칸을 0 으로 읽는 것이 이 툴이 막으려는 바로 그 사고다."""
    with pytest.raises(ValueError_):
        strip_unit(raw, SECONDS_UNITS)


@pytest.mark.parametrize("raw,expected", [
    ("2026.02.05", (2026, 2, 5)), ("2026-02-05", (2026, 2, 5)),
    ("2026/02/05", (2026, 2, 5)), ("20260205", (2026, 2, 5)),
    ("2026.2.5", (2026, 2, 5)), ("2026년 2월 5일", (2026, 2, 5)),
    ("2026.12.31", (2026, 12, 31)), ("2026.01.01", (2026, 1, 1)),
    ("2026-02-05 14:33", (2026, 2, 5)), ("2026-02-05T14:33:00", (2026, 2, 5)),
    ("2024.02.29", (2024, 2, 29)),
])
def test_연도먼저_날짜만_읽는다(raw, expected):
    assert parse_date(raw) == datetime.date(*expected)


@pytest.mark.parametrize("raw", [
    "05.02.2026", "02/05/2026", "26.02.05", "", None, "2026.13.01", "2026.02.30",
    "2023.02.29", "어제", "2026.02", "0000.00.00", "2026.00.05", "2026.02.00",
])
def test_애매하거나_불가능한_날짜는_거절(raw):
    """`05.02.2026` 이 2월 5일인지 5월 2일인지 이 툴은 알 방법이 없다."""
    with pytest.raises(ValueError_):
        parse_date(raw)


def test_날짜_포맷():
    day = datetime.date(2026, 3, 2)
    assert fmt_date(day) == "2026-03-02"
    assert fmt_dot(day) == "2026.03.02"
    assert fmt_date(None) == ""
    assert fmt_dot(None) == ""


def test_NFD_로_들어온_한글_단위도_읽는다():
    import unicodedata
    assert strip_unit(unicodedata.normalize("NFD", "7초"), SECONDS_UNITS) == 7.0
