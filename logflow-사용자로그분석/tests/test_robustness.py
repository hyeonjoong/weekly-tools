"""적대적 리뷰(P1~P5)에서 나온 엣지케이스 회귀 테스트."""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from logflow.dataio import Event, load_events
from logflow.metrics import funnel, retention


def _write_csv(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# P1 — tz-offset 으로 날짜 버킷을 현지시각 기준으로 보정
def test_tz_offset_shifts_date_bucket():
    # UTC 23:00 + 9h = 다음날 08:00
    path = _write_csv("user_id,event,timestamp\nu1,a,2026-01-01T23:00:00\n")
    try:
        ev_utc = load_events(path)
        ev_kst = load_events(path, tz_offset_hours=9)
        assert ev_utc[0].ts.date() == datetime(2026, 1, 1).date()
        assert ev_kst[0].ts.date() == datetime(2026, 1, 2).date()
    finally:
        os.remove(path)


def test_offset_timestamp_with_tz_offset_roundtrips_to_local_day():
    # +09:00 08:00 -> UTC 전날 23:00 -> +9 보정 -> 다시 01-02 08:00 (현지일자 보존)
    path = _write_csv("user_id,event,timestamp\nu1,a,2026-01-02T08:00:00+09:00\n")
    try:
        ev = load_events(path, tz_offset_hours=9)
        assert ev[0].ts == datetime(2026, 1, 2, 8, 0, 0)
    finally:
        os.remove(path)


# P2 — 결측 토큰은 건너뛰고, 손상 행은 옵션에 따라 처리
def test_null_tokens_skipped_and_counted():
    path = _write_csv(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u2,nan,2026-01-01T09:00:00\n"     # 이벤트 결측
        "u3,a,NULL\n"                       # 시각 결측
        "NA,a,2026-01-01T09:00:00\n"        # 사용자 결측
    )
    try:
        counters = {}
        events = load_events(path, counters=counters)
        assert [e.user for e in events] == ["u1"]
        assert counters["skipped_missing"] == 3
        assert counters["skipped_bad"] == 0
    finally:
        os.remove(path)


def test_bad_timestamp_aborts_by_default_but_skippable():
    path = _write_csv(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u2,a,garbage-date\n"
    )
    try:
        with pytest.raises(ValueError):
            load_events(path)
        counters = {}
        events = load_events(path, skip_bad_rows=True, counters=counters)
        assert [e.user for e in events] == ["u1"]
        assert counters["skipped_bad"] == 1
    finally:
        os.remove(path)


# P3 — 퍼널: 한 이벤트가 두 단계를 만족하면 안 됨, 중복 단계 이름 안전
def test_funnel_does_not_reuse_same_event():
    base = datetime(2026, 1, 1)
    one_a = [Event("u1", "a", base)]
    steps = funnel(one_a, ["a", "a"])
    assert steps[0].reached == 1
    assert steps[1].reached == 0   # 두 번째 a 는 별개 이벤트가 없으므로 도달 X


def test_funnel_same_timestamp_distinct_events_progress_by_order():
    base = datetime(2026, 1, 1)
    evs = [Event("u1", "open", base), Event("u1", "buy", base)]  # 동일 시각
    steps = funnel(evs, ["open", "buy"])
    assert steps[0].reached == 1
    assert steps[1].reached == 1   # 서로 다른 이벤트이므로 진행


# P4 — 리텐션 N<1 은 거부
def test_retention_rejects_non_positive_n():
    base = datetime(2026, 1, 1)
    evs = [Event("u1", "a", base), Event("u1", "a", base + timedelta(days=1))]
    with pytest.raises(ValueError):
        retention(evs, days=[0])
    with pytest.raises(ValueError):
        retention(evs, days=[-1])
