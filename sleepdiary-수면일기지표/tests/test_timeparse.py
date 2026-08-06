"""시각/소요시간 파싱 — 손으로 쓴 일기에서 실제로 나오는 표기들."""

import pytest

from sleepdiary.timeparse import (
    TimeParseError,
    fmt_clock,
    fmt_hm,
    forward_minutes,
    parse_clock,
    parse_duration_minutes,
)


@pytest.mark.parametrize("text,expected", [
    ("23:15", 23 * 60 + 15),
    ("00:00", 0),
    ("7:05", 7 * 60 + 5),
    ("07:05:30", 7 * 60 + 5 + 0.5),
    ("11:15 PM", 23 * 60 + 15),
    ("11:15 pm", 23 * 60 + 15),
    ("12:30 AM", 30),            # 자정 이후 12시대 → 00:30
    ("12:30 PM", 12 * 60 + 30),  # 정오 이후 12시대 → 12:30
    ("오후 11시 15분", 23 * 60 + 15),
    ("오전 6시 40분", 6 * 60 + 40),
    ("새벽 1시 20분", 80),
    ("2026-03-01 23:15", 23 * 60 + 15),
    ("2026/03/01 07:30", 7 * 60 + 30),
    ("2315", 23 * 60 + 15),
    ("730", 7 * 60 + 30),
    ("24:00", 0),                # 자정 표기
    ("  22:10  ", 22 * 60 + 10),
])
def test_parse_clock_variants(text, expected):
    assert parse_clock(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [
    "", "   ", None, "N/A", "미측정", "25:70", "23:99", "abc", "9시 70분",
    "오전 오후 3시",       # 오전/오후 동시 표기
    "오후 23:00",          # 12시간제 표기 + 24시간제 시각 충돌
])
def test_parse_clock_rejects_garbage(bad):
    with pytest.raises(TimeParseError):
        parse_clock(bad)


def test_parse_clock_does_not_read_the_date_as_a_time():
    """"2026-03-01" 의 03:01 을 시각으로 잘못 읽으면 안 된다."""
    assert parse_clock("2026-03-01 23:15") == pytest.approx(23 * 60 + 15)
    with pytest.raises(TimeParseError):
        parse_clock("2026-03-01")


@pytest.mark.parametrize("text,expected", [
    ("45", 45.0),
    ("45분", 45.0),
    ("0", 0.0),
    ("1:05", 65.0),
    ("2:00", 120.0),
    ("1h20m", 80.0),
    ("1시간 20분", 80.0),
    ("2시간", 120.0),
    ("1.5시간", 90.0),
    ("30 min", 30.0),
    ("12.5", 12.5),
])
def test_parse_duration(text, expected):
    assert parse_duration_minutes(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", None, "모름", "1:75", "잘못"])
def test_parse_duration_rejects_garbage(bad):
    with pytest.raises(TimeParseError):
        parse_duration_minutes(bad)


def test_negative_duration_parses_so_caller_can_reject_it():
    """음수는 파서가 아니라 nightly 층에서 오류로 잡는다 (메시지가 더 친절해서)."""
    assert parse_duration_minutes("-15") == pytest.approx(-15.0)


@pytest.mark.parametrize("start,end,expected", [
    (23 * 60, 7 * 60, 8 * 60),        # 자정 넘김
    (22 * 60 + 30, 6 * 60 + 45, 8 * 60 + 15),
    (1 * 60, 9 * 60, 8 * 60),         # 같은 날
    (0, 0, 0),                        # 같은 시각 = 0분 (하루가 아니다)
    (23 * 60 + 59, 0, 1),
])
def test_forward_minutes(start, end, expected):
    assert forward_minutes(start, end) == pytest.approx(expected)


def test_forward_minutes_is_always_in_one_day():
    for start in range(0, 1440, 37):
        for end in range(0, 1440, 53):
            value = forward_minutes(start, end)
            assert 0 <= value < 1440


def test_formatting_round_trips():
    assert fmt_clock(23 * 60 + 15) == "23:15"
    assert fmt_clock(0) == "00:00"
    assert fmt_clock(1440) == "00:00"       # 넘어가면 감싼다
    assert fmt_clock(-30) == "23:30"
    assert fmt_hm(432) == "7h 12m"
    assert fmt_hm(-95) == "-1h 35m"
    assert fmt_hm(0) == "0h 00m"
