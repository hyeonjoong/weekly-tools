from datetime import datetime, timedelta

from logflow.dataio import Event
from logflow.metrics import (
    active_users,
    event_breakdown,
    funnel,
    retention,
    stickiness,
    user_breakdown,
)


def _ev(user, name, day, hour=0):
    return Event(user=user, name=name, ts=datetime(2026, 1, 1) + timedelta(days=day, hours=hour))


def test_event_breakdown_counts_and_unique():
    events = [
        _ev("u1", "open", 0), _ev("u1", "open", 0), _ev("u2", "open", 0),
        _ev("u1", "click", 0),
    ]
    stats = {s.name: s for s in event_breakdown(events)}
    assert stats["open"].count == 3
    assert stats["open"].unique_users == 2
    assert stats["click"].count == 1
    # 정렬: open(3) 먼저
    assert event_breakdown(events)[0].name == "open"


def test_user_breakdown_active_days_and_bounds():
    events = [_ev("u1", "a", 0, 1), _ev("u1", "a", 0, 5), _ev("u1", "a", 2)]
    u = user_breakdown(events)[0]
    assert u.event_count == 3
    assert u.active_days == 2          # 1/1, 1/3
    assert u.first_seen == datetime(2026, 1, 1, 1)
    assert u.last_seen == datetime(2026, 1, 3, 0)


def test_active_users_dau_wau_mau():
    # u1: day0,1 ; u2: day0 ; u3: day8
    events = [
        _ev("u1", "a", 0), _ev("u1", "a", 1),
        _ev("u2", "a", 0),
        _ev("u3", "a", 8),
    ]
    rows = {a.day: a for a in active_users(events)}
    d0 = datetime(2026, 1, 1).date()
    assert rows[d0].dau == 2          # u1,u2
    assert rows[d0 + timedelta(days=1)].dau == 1   # u1
    assert rows[d0 + timedelta(days=8)].dau == 1   # u3
    # WAU(7일 롤링) at day8 = day2..day8 -> only u3
    assert rows[d0 + timedelta(days=8)].wau == 1
    # WAU at day1 = day0..day1 -> u1,u2
    assert rows[d0 + timedelta(days=1)].wau == 2
    # MAU(28일) at day8 -> u1,u2,u3
    assert rows[d0 + timedelta(days=8)].mau == 3


def test_retention_classic_day_n():
    # u1 day0,1,7 ; u2 day0 ; u3 day0,7
    events = [
        _ev("u1", "a", 0), _ev("u1", "a", 1), _ev("u1", "a", 7),
        _ev("u2", "a", 0),
        _ev("u3", "a", 0), _ev("u3", "a", 7),
    ]
    res = {r.n: r for r in retention(events, days=[1, 7])}
    # max_day = day7. day-1 eligible: 모두 (cohort day0, +1<=7) =3
    assert res[1].eligible == 3
    assert res[1].retained == 1       # u1만 day1 활성
    assert abs(res[1].rate - 1 / 3) < 1e-9
    # day-7 eligible: cohort day0 +7<=7 =3
    assert res[7].eligible == 3
    assert res[7].retained == 2       # u1, u3
    assert abs(res[7].rate - 2 / 3) < 1e-9


def test_retention_excludes_users_without_opportunity():
    # 마지막 코호트는 day-7 관찰 기회 없음 -> eligible 제외
    events = [_ev("u1", "a", 0), _ev("u1", "a", 1), _ev("u2", "a", 5)]
    res = {r.n: r for r in retention(events, days=[7])}
    # max_day=5. u1 cohort day0 +7=7>5 제외, u2 day5+7=12>5 제외 -> eligible 0
    assert res[7].eligible == 0
    assert res[7].rate is None


def test_funnel_temporal_order():
    # u1: open->buy(순서 맞음); u2: buy먼저->open(순서 안맞아 buy 도달X)
    events = [
        _ev("u1", "open", 0, 1), _ev("u1", "buy", 0, 2),
        _ev("u2", "buy", 0, 1), _ev("u2", "open", 0, 2),
    ]
    steps = funnel(events, ["open", "buy"])
    assert steps[0].reached == 2      # 둘 다 open 있음
    assert steps[1].reached == 1      # u1만 open 이후 buy
    assert abs(steps[1].step_conversion - 0.5) < 1e-9
    assert abs(steps[1].overall_conversion - 0.5) < 1e-9


def test_funnel_first_step_conversion_is_one():
    events = [_ev("u1", "open", 0)]
    steps = funnel(events, ["open", "buy"])
    assert steps[0].overall_conversion == 1.0
    assert steps[1].reached == 0


def test_stickiness_ratio():
    events = [_ev("u1", "a", 0), _ev("u1", "a", 1)]
    st = stickiness(active_users(events))
    # 2일: DAU 평균=1, MAU 평균=1 -> 1.0
    assert abs(st - 1.0) < 1e-9
