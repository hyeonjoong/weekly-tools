"""세션화: 한 사용자의 이벤트를 비활동 간격(inactivity gap) 기준으로 세션으로 묶는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .dataio import Event


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
