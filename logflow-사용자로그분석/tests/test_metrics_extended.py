from datetime import datetime, timedelta

from logflow.dataio import Event
from logflow.metrics import (
    activity_by_hour,
    activity_by_weekday,
    funnel,
    peak_hour,
    retention,
)
from logflow.sessionize import session_summary, sessionize


def _ev(user, name, day=0, hour=0, minute=0):
    return Event(user, name, datetime(2026, 1, 5) + timedelta(days=day, hours=hour, minutes=minute))
    # 2026-01-05 is a Monday


# ---- activity timing ----

def test_activity_by_hour_counts():
    evs = [_ev("u1", "a", hour=9), _ev("u1", "a", hour=9), _ev("u2", "a", hour=22)]
    h = activity_by_hour(evs)
    assert len(h) == 24
    assert h[9] == 2
    assert h[22] == 1
    assert sum(h) == 3


def test_peak_hour_and_tiebreak():
    evs = [_ev("u1", "a", hour=8), _ev("u2", "a", hour=20)]  # tie -> earlier hour
    assert peak_hour(evs) == 8
    assert peak_hour([]) is None


def test_activity_by_weekday_monday_zero():
    # 2026-01-05 is Monday (weekday 0)
    evs = [_ev("u1", "a", day=0), _ev("u1", "a", day=2)]  # Mon, Wed
    w = activity_by_weekday(evs)
    assert len(w) == 7
    assert w[0] == 1
    assert w[2] == 1
    assert sum(w) == 2


# ---- retention CI ----

def test_retention_ci_present_and_brackets_rate():
    events = [
        _ev("u1", "a", 0), _ev("u1", "a", 1),
        _ev("u2", "a", 0),
        _ev("u3", "a", 0), _ev("u3", "a", 1),
    ]
    res = {r.n: r for r in retention(events, days=[1])}
    r = res[1]
    assert r.eligible == 3 and r.retained == 2
    assert r.ci is not None
    lo, hi = r.ci
    assert lo <= r.rate <= hi


def test_retention_ci_none_when_no_eligible():
    events = [_ev("u1", "a", 0)]
    res = {r.n: r for r in retention(events, days=[7])}
    assert res[7].eligible == 0
    assert res[7].ci is None


def test_retention_confidence_width():
    events = [
        _ev("u1", "a", 0), _ev("u1", "a", 1),
        _ev("u2", "a", 0),
    ]
    r95 = {r.n: r for r in retention(events, days=[1], confidence=0.95)}[1]
    r80 = {r.n: r for r in retention(events, days=[1], confidence=0.80)}[1]
    assert (r95.ci[1] - r95.ci[0]) > (r80.ci[1] - r80.ci[0])


# ---- funnel timing + CI ----

def test_funnel_median_time_between_steps():
    # u1: open@0min, buy@+10min ; u2: open@0min, buy@+20min -> median 15min = 900s
    base = datetime(2026, 1, 5, 12, 0)
    evs = [
        Event("u1", "open", base), Event("u1", "buy", base + timedelta(minutes=10)),
        Event("u2", "open", base), Event("u2", "buy", base + timedelta(minutes=20)),
    ]
    steps = funnel(evs, ["open", "buy"])
    assert steps[0].median_seconds_from_prev is None   # first step has no prev
    assert abs(steps[1].median_seconds_from_prev - 900.0) < 1e-9


def test_funnel_step_ci_present():
    evs = [
        Event("u1", "open", datetime(2026, 1, 5, 12, 0)),
        Event("u1", "buy", datetime(2026, 1, 5, 12, 1)),
        Event("u2", "open", datetime(2026, 1, 5, 12, 0)),
    ]
    steps = funnel(evs, ["open", "buy"])
    assert steps[0].step_ci is None
    assert steps[1].step_ci is not None
    lo, hi = steps[1].step_ci
    assert lo <= steps[1].step_conversion <= hi


# ---- session summary ----

def test_session_summary_distribution():
    # one session 3 events spanning 25min, one single-event session
    base = datetime(2026, 1, 5, 0, 0)
    evs = [
        Event("u1", "a", base),
        Event("u1", "a", base + timedelta(minutes=10)),
        Event("u1", "a", base + timedelta(minutes=25)),
        Event("u2", "a", base),
    ]
    summ = session_summary(sessionize(evs, gap_seconds=1800))
    # durations exclude single-event sessions -> only one duration = 1500s
    assert summ["duration_seconds"]["n"] == 1
    assert abs(summ["duration_seconds"]["median"] - 1500.0) < 1e-9
    # events per session over all sessions: [3, 1]
    assert summ["events_per_session"]["n"] == 2
    assert summ["events_per_session"]["max"] == 3.0


def test_session_summary_all_single_event():
    evs = [Event("u1", "a", datetime(2026, 1, 5)), Event("u2", "a", datetime(2026, 1, 5))]
    summ = session_summary(sessionize(evs))
    assert summ["duration_seconds"] is None
    assert summ["events_per_session"]["n"] == 2
