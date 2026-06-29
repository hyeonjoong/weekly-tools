from datetime import datetime, timedelta

from logflow.dataio import Event
from logflow.sessionize import sessionize


def _ev(user, name, minute):
    return Event(user=user, name=name, ts=datetime(2026, 1, 1, 0, 0) + timedelta(minutes=minute))


def test_single_event_session():
    sessions = sessionize([_ev("u1", "a", 0)])
    assert len(sessions) == 1
    assert sessions[0].event_count == 1
    assert sessions[0].duration_seconds == 0.0


def test_gap_splits_sessions():
    # 0,10,25분 = 한 세션(30분 미만 간격), 그 뒤 70분 = 새 세션
    events = [_ev("u1", "a", t) for t in (0, 10, 25, 70)]
    sessions = sessionize(events, gap_seconds=1800)
    assert len(sessions) == 2
    assert sessions[0].event_count == 3
    assert sessions[0].duration_seconds == 25 * 60
    assert sessions[1].event_count == 1


def test_exactly_at_gap_stays_same_session():
    # 정확히 30분 간격은 '초과'가 아니므로 같은 세션
    events = [_ev("u1", "a", 0), _ev("u1", "b", 30)]
    sessions = sessionize(events, gap_seconds=1800)
    assert len(sessions) == 1
    assert sessions[0].event_count == 2


def test_multiple_users_independent():
    events = [_ev("u1", "a", 0), _ev("u2", "a", 5), _ev("u1", "b", 10)]
    sessions = sessionize(events)
    by_user = {s.user: s for s in sessions}
    assert len(sessions) == 2
    assert by_user["u1"].event_count == 2
    assert by_user["u2"].event_count == 1
