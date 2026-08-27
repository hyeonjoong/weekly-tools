"""48시간 더블플롯 액토그램 — 구조·문자 배치·42일 상한."""

import datetime as dt

from circadia.actogram import MAX_DAYS, render_actogram

D0 = dt.datetime(2026, 8, 3)


def _rows(text):
    """날짜 행만 (앞 4줄은 헤더·범례·눈금)."""
    return text.split("\n")[4:]


def test_double_plot_repeats_next_day_on_same_line():
    """수면 23:00–07:00 이틀 — 첫 줄 오른쪽 절반(다음날 0–7시)에 Z가 보인다."""
    sleep = [(D0 + dt.timedelta(hours=23), D0 + dt.timedelta(hours=31)),
             (D0 + dt.timedelta(hours=47), D0 + dt.timedelta(hours=55))]
    text = render_actogram(None, sleep)
    rows = _rows(text)
    line1 = rows[0]
    body = line1[6:]                      # 'MM-DD ' 라벨 6자 제거
    assert len(body) == 96
    # 첫날 23:00~24:00 → 슬롯 46·47
    assert body[46] == "Z" and body[47] == "Z"
    # 더블플롯: 다음날 00:00~07:00 → 슬롯 48~61
    assert set(body[48:62]) == {"Z"}
    # 이틀째 줄 왼쪽 절반에도 같은 아침 수면
    assert set(rows[1][6:][0:14]) == {"Z"}


def test_activity_levels_and_no_data_chars():
    """값 0 → '.', 양수 → 3분위(-,+,#), 표본 없는 슬롯 → 공백."""
    act = [(D0 + dt.timedelta(hours=0), 0.0),
           (D0 + dt.timedelta(hours=1), 10.0),
           (D0 + dt.timedelta(hours=2), 100.0),
           (D0 + dt.timedelta(hours=3), 1000.0)]
    text = render_actogram(act, None)
    body = _rows(text)[0][6:]
    assert body[0] == "."          # 0시 00분 슬롯, 값 0
    assert body[2] == "-"          # 최저 3분위
    assert body[4] == "+"
    assert body[6] == "#"
    assert body[1] == " "          # 표본 없음
    assert body[8] == " "


def test_returns_none_without_any_input():
    assert render_actogram(None, None) is None
    assert render_actogram([], None) is None


def test_caps_at_42_days_and_confesses():
    act = [(D0 + dt.timedelta(days=d, hours=12), 5.0) for d in range(45)]
    text = render_actogram(act, None)
    assert f"마지막 {MAX_DAYS}일만 표시" in text.split("\n")[0]
    assert len(_rows(text)) == MAX_DAYS


def test_sleep_slot_requires_half_overlap():
    """수면이 슬롯의 50% 미만이면 Z 가 아니다 — 23:20 시작이면 23:00 슬롯은
    10/30분만 수면이라 Z 아님, 23:30 슬롯은 Z."""
    sleep = [(D0 + dt.timedelta(hours=23, minutes=20),
              D0 + dt.timedelta(hours=30)),
             (D0 + dt.timedelta(hours=47), D0 + dt.timedelta(hours=54))]
    body = _rows(render_actogram(None, sleep))[0][6:]
    assert body[46] == " "     # 23:00–23:30, 수면 10분 → 1/3 < 1/2
    assert body[47] == "Z"     # 23:30–24:00 전부 수면
