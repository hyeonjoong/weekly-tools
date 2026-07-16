"""세션화: 한 사용자의 이벤트를 비활동 간격(inactivity gap) 기준으로 세션으로 묶는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .dataio import Event
from .stats import describe


@dataclass
class Session:
    user: str
    start: "object"  # datetime
    end: "object"    # datetime
    event_count: int

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def sessionize(events: List[Event], gap_seconds: float = 1800.0) -> List[Session]:
    """이벤트를 사용자별로 모은 뒤, 직전 이벤트와의 간격이 gap_seconds를 초과하면
    새 세션을 시작한다 (기본 30분).

    반환: 모든 세션을 (user, start) 순으로 정렬한 리스트.
    """
    if gap_seconds < 0:
        raise ValueError("gap_seconds 는 음수일 수 없습니다")

    by_user: Dict[str, List[Event]] = {}
    for e in events:
        by_user.setdefault(e.user, []).append(e)

    sessions: List[Session] = []
    for user, evs in by_user.items():
        evs = sorted(evs, key=lambda e: e.ts)
        cur = Session(user=user, start=evs[0].ts, end=evs[0].ts, event_count=1)
        for prev, e in zip(evs, evs[1:]):
            if (e.ts - prev.ts).total_seconds() > gap_seconds:
                sessions.append(cur)
                cur = Session(user=user, start=e.ts, end=e.ts, event_count=1)
            else:
                cur.end = e.ts
                cur.event_count += 1
        sessions.append(cur)

    sessions.sort(key=lambda s: (s.user, s.start))
    return sessions


def session_summary(sessions: List[Session]) -> Dict[str, Optional[dict]]:
    """세션 분포 요약.

    치우친 로그에서 평균만 보면 오해하기 쉬우므로 중앙값·사분위수를 함께 낸다.
    - duration_seconds: 단일 이벤트 세션(길이 0)은 제외한 실질 세션 길이 분포.
    - events_per_session: 모든 세션의 이벤트 수 분포.
    분포를 낼 세션이 없으면 해당 키는 None.
    """
    durations = [s.duration_seconds for s in sessions if s.event_count > 1]
    eps = [float(s.event_count) for s in sessions]
    return {
        "duration_seconds": describe(durations),
        "events_per_session": describe(eps),
    }
