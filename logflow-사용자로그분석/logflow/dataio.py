"""CSV 로딩과 타임스탬프 파싱.

이벤트 로그는 최소 3개 열을 가진다고 가정한다: 사용자 ID, 이벤트 이름, 타임스탬프.
열 이름은 CLI에서 바꿀 수 있다. 타임스탬프는 ISO-8601 문자열이나 epoch(초/밀리초)을
모두 받아들인다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

# 결측을 뜻하는 흔한 토큰 (pandas/엑셀 CSV 내보내기에서 자주 나옴). 이 값들은 빈 칸처럼 건너뛴다.
NULL_TOKENS = {"", "nan", "null", "none", "na", "n/a", "#n/a"}


@dataclass(frozen=True)
class Event:
    """로그 한 줄: 누가(user), 무엇을(name), 언제(ts)."""

    user: str
    name: str
    ts: datetime


def parse_timestamp(raw: str) -> datetime:
    """타임스탬프 문자열을 tz-naive(UTC 기준) datetime으로 변환.

    지원 형식:
      - epoch 초     : "1735718400"
      - epoch 밀리초 : "1735718400000"
      - ISO-8601     : "2025-01-01T08:00:00", "2025-01-01 08:00:00",
                       "2025-01-01T08:00:00Z", "2025-01-01T08:00:00+09:00"

    오프셋이 있으면 UTC로 변환한 뒤 tzinfo를 제거해 비교를 단순화한다.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("빈 타임스탬프")

    # epoch (정수/소수, 부호 없음으로 가정)
    if _looks_numeric(s):
        value = float(s)
        # 초(~1.7e9) vs 밀리초(~1.7e12) 구분: 1e11 이상이면 밀리초로 본다.
        if abs(value) >= 1e11:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)

    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _looks_numeric(s: str) -> bool:
    body = s[1:] if s[:1] in "+-" else s
    if not body:
        return False
    # 숫자와 소수점 한 개까지만 epoch로 취급 (날짜의 '-'/':' 는 제외됨)
    return body.replace(".", "", 1).isdigit()


def load_events(
    path: str,
    user_col: str = "user_id",
    event_col: str = "event",
    time_col: str = "timestamp",
    encoding: str = "utf-8-sig",
    tz_offset_hours: float = 0.0,
    skip_bad_rows: bool = False,
    counters: Optional[Dict[str, int]] = None,
) -> List[Event]:
    """CSV 파일을 읽어 Event 리스트로 반환 (타임스탬프 오름차순 정렬).

    - tz_offset_hours: 파싱된 시각에 더할 시간(시 단위). 날짜 버킷팅이 UTC가 아니라
      현지시각 기준이 되도록 보정할 때 쓴다. 예: 로그가 UTC인데 KST(+9) 기준으로
      날짜를 끊고 싶으면 9 를 준다.
    - skip_bad_rows: True 면 파싱 불가한 타임스탬프 행을 오류 없이 건너뛴다(기본은 오류).
    - counters: 주어지면 {'skipped_missing','skipped_bad'} 카운트를 채워 넣는다.

    필수 열이 없으면 명확한 오류를 던진다.
    """
    if counters is None:
        counters = {}
    counters.setdefault("skipped_missing", 0)
    counters.setdefault("skipped_bad", 0)
    shift = timedelta(hours=tz_offset_hours)

    with open(path, "r", newline="", encoding=encoding) as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"빈 파일이거나 헤더가 없습니다: {path}")
        _require_columns(reader.fieldnames, [user_col, event_col, time_col], path)
        events = _rows_to_events(
            reader, user_col, event_col, time_col, shift, skip_bad_rows, counters
        )

    if not events:
        raise ValueError(f"유효한 데이터 행이 없습니다: {path}")
    events.sort(key=lambda e: (e.ts, e.user))
    return events


def _require_columns(fieldnames: Iterable[str], needed: Iterable[str], path: str) -> None:
    present = set(fieldnames)
    missing = [c for c in needed if c not in present]
    if missing:
        raise ValueError(
            f"필수 열이 없습니다: {missing} (파일 {path} 의 열: {sorted(present)})"
        )


def _rows_to_events(
    reader, user_col, event_col, time_col, shift, skip_bad_rows, counters
) -> List[Event]:
    events: List[Event] = []
    for i, row in enumerate(reader, start=2):  # 2 = 헤더 다음 첫 데이터 행
        user = (row.get(user_col) or "").strip()
        name = (row.get(event_col) or "").strip()
        raw_ts = (row.get(time_col) or "").strip()
        # 핵심 값이 비었거나 결측 토큰(nan/null/...)이면 그 행은 건너뛴다 (로그에는 흔함).
        if (
            user.lower() in NULL_TOKENS
            or name.lower() in NULL_TOKENS
            or raw_ts.lower() in NULL_TOKENS
        ):
            counters["skipped_missing"] += 1
            continue
        try:
            ts = parse_timestamp(raw_ts) + shift
        except (ValueError, OSError, OverflowError) as exc:
            if skip_bad_rows:
                counters["skipped_bad"] += 1
                continue
            raise ValueError(
                f"{i}행 타임스탬프 파싱 실패 ({raw_ts!r}): {exc}. "
                f"손상된 행을 건너뛰려면 --skip-bad-rows 를 쓰세요."
            ) from exc
        events.append(Event(user=user, name=name, ts=ts))
    return events
