"""날짜 파싱과 되쓰기.

**연-월-일 순서만 읽습니다.** `03/14/2026` 과 `14/03/2026` 은 구별할 방법이
없고, 잘못 읽으면 날짜 이동이 조용히 분석을 깨뜨립니다. 그래서 읽지 않고
커버리지 자백에 남깁니다.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import List, Optional

# (정규식, 렌더 템플릿) — 렌더 템플릿은 원본 표기를 그대로 재현합니다.
_PATTERNS = [
    (re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"), "-"),
    (re.compile(r"^\s*(\d{4})/(\d{1,2})/(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"), "/"),
    (re.compile(r"^\s*(\d{4})\.\s?(\d{1,2})\.\s?(\d{1,2})\.?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"), "."),
    (re.compile(r"^\s*(\d{4})년\s?(\d{1,2})월\s?(\d{1,2})일\s*$"), "년"),
    (re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$"), "compact"),
]

_MIN_YEAR = 1900
_MAX_YEAR = 2100


@dataclass(frozen=True)
class ParsedDate:
    """파싱된 날짜값.

    Attributes:
        value: datetime (시간이 없으면 00:00:00).
        has_time: 원본에 시각이 있었는가.
        sep: 되쓰기용 구분자 토큰.
        zero_padded: 월/일이 두 자리로 적혀 있었는가.
        seconds: 초까지 적혀 있었는가.
    """

    value: _dt.datetime
    has_time: bool
    sep: str
    zero_padded: bool
    seconds: bool


def parse_date(text: str) -> Optional[ParsedDate]:
    """문자열을 날짜로 읽습니다. 못 읽으면 None."""
    if text is None:
        return None
    s = str(text).strip()
    if not s or len(s) > 32:
        return None
    for pattern, sep in _PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (_MIN_YEAR <= year <= _MAX_YEAR):
            return None
        hour = minute = second = 0
        has_time = False
        seconds = False
        if pattern.groups >= 6:
            if m.group(4) is not None:
                has_time = True
                hour, minute = int(m.group(4)), int(m.group(5))
                if m.group(6) is not None:
                    second = int(m.group(6))
                    seconds = True
        try:
            value = _dt.datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
        zero_padded = sep == "compact" or (len(m.group(2)) == 2 and len(m.group(3)) == 2)
        return ParsedDate(value=value, has_time=has_time, sep=sep, zero_padded=zero_padded, seconds=seconds)
    return None


def render_date(parsed: ParsedDate, value: _dt.datetime) -> str:
    """원본과 같은 표기로 날짜를 되씁니다."""
    y, mo, d = value.year, value.month, value.day
    if parsed.sep == "compact":
        base = f"{y:04d}{mo:02d}{d:02d}"
    elif parsed.sep == "년":
        return f"{y:04d}년 {mo:02d}월 {d:02d}일" if parsed.zero_padded else f"{y}년 {mo}월 {d}일"
    else:
        if parsed.zero_padded:
            base = f"{y:04d}{parsed.sep}{mo:02d}{parsed.sep}{d:02d}"
        else:
            base = f"{y:04d}{parsed.sep}{mo}{parsed.sep}{d}"
    if parsed.has_time:
        if parsed.seconds:
            base += f" {value.hour:02d}:{value.minute:02d}:{value.second:02d}"
        else:
            base += f" {value.hour:02d}:{value.minute:02d}"
    return base


def night_date(value: _dt.datetime, cutoff_hour: int = 12) -> _dt.date:
    """야간 귀속 날짜 — 정오 이전 시각은 '전날 밤'으로 귀속합니다.

    수면 연구에서 23:40 과 다음날 03:20 은 같은 밤입니다.
    """
    if value.hour < cutoff_hour:
        return (value - _dt.timedelta(days=1)).date()
    return value.date()


def looks_like_birth(value: _dt.datetime, today: Optional[_dt.date] = None) -> bool:
    """출생일로 그럴듯한 날짜인가(오늘로부터 1~120년 전)."""
    today = today or _dt.date.today()
    age_days = (today - value.date()).days
    return 365 <= age_days <= 120 * 366


def date_ratio(values: List[str]) -> float:
    """비어 있지 않은 값 중 날짜로 읽히는 비율."""
    non_empty = [v for v in values if str(v).strip()]
    if not non_empty:
        return 0.0
    ok = sum(1 for v in non_empty if parse_date(v) is not None)
    return ok / len(non_empty)


def ambiguous_date_ratio(values: List[str]) -> float:
    """`03/14/2026` 처럼 순서를 알 수 없는 표기의 비율(자백용)."""
    pattern = re.compile(r"^\s*\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s*$")
    non_empty = [str(v).strip() for v in values if str(v).strip()]
    if not non_empty:
        return 0.0
    return sum(1 for v in non_empty if pattern.match(v)) / len(non_empty)
