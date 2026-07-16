"""CSV 로딩과 타임스탬프 파싱.

이벤트 로그는 최소 3개 열을 가진다고 가정한다: 사용자 ID, 이벤트 이름, 타임스탬프.
열 이름은 CLI에서 바꿀 수 있다. 타임스탬프는 ISO-8601 문자열이나 epoch(초/밀리초)을
모두 받아들인다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

# 결측을 뜻하는 흔한 토큰 (pandas/엑셀 CSV 내보내기에서 자주 나옴). 이 값들은 빈 칸처럼 건너뛴다.
NULL_TOKENS = {"", "nan", "null", "none", "na", "n/a", "#n/a"}

# 구분자 자동감지에 시도할 후보들 (엑셀 유럽 로케일의 ';', TSV 의 '\t' 등).
_DELIM_CANDIDATES = ",;\t|"


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
    if not s:
        return False
    # 부호 없는 숫자 + 소수점 한 개까지만 epoch로 취급 (날짜의 '-'/':', 음수 epoch는 제외).
    # 음수/부호 붙은 값은 epoch로 보지 않으므로 아래 ISO 파싱에서 명확한 오류가 난다.
    return s.replace(".", "", 1).isdigit()


def load_events(
    path: str,
    user_col: str = "user_id",
    event_col: str = "event",
    time_col: str = "timestamp",
    encoding: str = "utf-8-sig",
    tz_offset_hours: float = 0.0,
    skip_bad_rows: bool = False,
    counters: Optional[Dict[str, int]] = None,
    delimiter: Optional[str] = None,
    dedup: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Event]:
    """CSV 파일을 읽어 Event 리스트로 반환 (타임스탬프 오름차순 정렬).

    - tz_offset_hours: 파싱된 시각에 더할 시간(시 단위). 날짜 버킷팅이 UTC가 아니라
      현지시각 기준이 되도록 보정할 때 쓴다. 예: 로그가 UTC인데 KST(+9) 기준으로
      날짜를 끊고 싶으면 9 를 준다.
    - skip_bad_rows: True 면 파싱 불가한 타임스탬프 행을 오류 없이 건너뛴다(기본은 오류).
    - counters: 주어지면 {'skipped_missing','skipped_bad','deduped','filtered'} 카운트를 채운다.
    - delimiter: None(기본)이면 구분자를 자동감지(',', ';', 탭, '|')한다. 지정하면 그대로.
    - dedup: True 면 (user, event, ts) 가 완전히 같은 중복 행을 하나만 남긴다.
    - date_from/date_to: (tz 보정 후) 이 달력 날짜 구간[포함] 밖의 이벤트를 제외한다.

    열 이름은 앞뒤 공백을 무시하고 매칭하며, 정확히 못 찾으면 대소문자 무시로 재시도한다.
    필수 열이 없으면 명확한 오류를 던진다.
    """
    if counters is None:
        counters = {}
    for key in ("skipped_missing", "skipped_bad", "deduped", "filtered"):
        counters.setdefault(key, 0)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError(f"date_from({date_from}) 이 date_to({date_to}) 보다 늦습니다")
    shift = timedelta(hours=tz_offset_hours)

    with open(path, "r", newline="", encoding=encoding) as fh:
        sample = fh.read(8192)
        fh.seek(0)
        delim = delimiter or _detect_delimiter(sample)
        reader = csv.DictReader(fh, delimiter=delim)
        if reader.fieldnames is None:
            raise ValueError(f"빈 파일이거나 헤더가 없습니다: {path}")
        colmap = _resolve_columns(reader.fieldnames, [user_col, event_col, time_col], path)
        events = _rows_to_events(
            reader, colmap[user_col], colmap[event_col], colmap[time_col],
            shift, skip_bad_rows, counters, date_from, date_to,
        )

    if dedup:
        events = _dedup(events, counters)
    if not events:
        raise ValueError(f"유효한 데이터 행이 없습니다: {path}")
    events.sort(key=lambda e: (e.ts, e.user))
    return events


def _detect_delimiter(sample: str) -> str:
    """헤더/본문 샘플에서 구분자를 추정. 실패하면 콤마."""
    if not sample:
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIM_CANDIDATES).delimiter
    except csv.Error:
        # 첫 줄에서 가장 많이 등장하는 후보를 고른다 (동률이면 콤마 우선).
        first = sample.splitlines()[0] if sample.splitlines() else ""
        best, best_n = ",", 0
        for cand in ",;\t|":
            n = first.count(cand)
            if n > best_n:
                best, best_n = cand, n
        return best


def _resolve_columns(
    fieldnames: Iterable[str], needed: Iterable[str], path: str
) -> Dict[str, str]:
    """요청한 열 이름을 실제 헤더 키로 매핑. 공백/대소문자 관용 매칭.

    반환: {요청이름: 실제헤더키}. 못 찾은 열이 있으면 오류.
    """
    actual = list(fieldnames)
    # 여러 헤더가 공백/대소문자만 다른 경우 모호하므로 후보를 모아 둔다.
    strip_groups: Dict[str, List[str]] = {}
    lower_groups: Dict[str, List[str]] = {}
    for fn in actual:
        strip_groups.setdefault((fn or "").strip(), []).append(fn)
        lower_groups.setdefault((fn or "").strip().lower(), []).append(fn)
    resolved: Dict[str, str] = {}
    missing = []
    for want in needed:
        w = want.strip()
        if w in strip_groups:
            group = strip_groups[w]
        elif w.lower() in lower_groups:
            group = lower_groups[w.lower()]
        else:
            missing.append(want)
            continue
        if len(group) > 1:
            raise ValueError(
                f"열 이름이 모호합니다: {want!r} 에 대응하는 헤더가 여러 개입니다 "
                f"({group}). 헤더의 공백/대소문자를 정리하거나 정확한 열 이름을 지정하세요."
            )
        resolved[want] = group[0]
    if missing:
        shown = sorted((fn or "").strip() for fn in actual)
        raise ValueError(
            f"필수 열이 없습니다: {missing} (파일 {path} 의 열: {shown})"
        )
    return resolved


def _rows_to_events(
    reader, user_col, event_col, time_col, shift, skip_bad_rows, counters,
    date_from, date_to,
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
        d = ts.date()
        if (date_from is not None and d < date_from) or (
            date_to is not None and d > date_to
        ):
            counters["filtered"] += 1
            continue
        events.append(Event(user=user, name=name, ts=ts))
    return events


def _dedup(events: List[Event], counters: Dict[str, int]) -> List[Event]:
    seen = set()
    out: List[Event] = []
    for e in events:
        key = (e.user, e.name, e.ts)
        if key in seen:
            counters["deduped"] += 1
            continue
        seen.add(key)
        out.append(e)
    return out
