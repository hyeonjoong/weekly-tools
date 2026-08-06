"""시각·소요시간 파싱 (표준 라이브러리만 사용).

수면일기는 사람이 손으로 적기 때문에 표기가 제각각이다 ("23:15", "11:15 PM",
"오후 11시 15분", "2026-03-01 23:15", "1130"). 이 모듈은 그런 표기를
`자정 기준 분(minute-of-day)` 하나로 정규화하고, 자정을 넘어가는 구간의
경과시간을 안전하게 계산한다.
"""

from __future__ import annotations

import math
import re

MINUTES_PER_DAY = 24 * 60

# "23:15", "23:15:30", "2026-03-01 23:15" 등에서 시:분(:초)만 뽑는다.
_HHMM = re.compile(r"(?<![\d.])(\d{1,2})\s*[:시]\s*(\d{1,2})(?:\s*[:분]\s*(\d{1,2}))?")
# 구분자 없는 "2315", "0730"
_COMPACT = re.compile(r"^(\d{3,4})$")

_AM_TOKENS = ("am", "a.m.", "오전", "새벽", "아침")
_PM_TOKENS = ("pm", "p.m.", "오후", "저녁", "밤")
# "11:15p" 처럼 한 글자로 끝나는 표기 (뒤에 다른 글자가 없을 때만)
_BARE_MERIDIEM = re.compile(r"\d\s*([ap])\.?m?\.?\s*$")
# "밤 12시", "새벽 12시" 는 정오가 아니라 자정을 뜻한다
_MIDNIGHT_WORDS = ("밤", "새벽", "한밤")


class TimeParseError(ValueError):
    """시각/소요시간 문자열을 해석할 수 없을 때."""


def _apply_meridiem(hour: int, text: str) -> int:
    """오전/오후 표기가 있으면 12시간제를 24시간제로 바꾼다."""
    low = text.lower()
    is_pm = any(tok in low for tok in _PM_TOKENS)
    is_am = any(tok in low for tok in _AM_TOKENS)
    bare = _BARE_MERIDIEM.search(low)
    if bare and not (is_pm or is_am):
        is_pm = bare.group(1) == "p"
        is_am = bare.group(1) == "a"
    # 한국어 구어에서 "밤 12시"는 정오가 아니라 자정이다. 12시간제 규칙을
    # 그대로 적용하면 12시간 어긋난 밤이 조용히 만들어진다.
    if hour == 12 and any(word in text for word in _MIDNIGHT_WORDS):
        return 0
    if is_pm and is_am:
        raise TimeParseError(f"오전/오후가 동시에 표기됨: {text!r}")
    if is_pm:
        if hour > 12:
            raise TimeParseError(f"오후 표기와 24시간제 시각이 충돌: {text!r}")
        return hour % 12 + 12
    if is_am:
        if hour > 12:
            raise TimeParseError(f"오전 표기와 24시간제 시각이 충돌: {text!r}")
        return hour % 12
    return hour


def parse_clock(value: str) -> float:
    """시각 문자열 → 자정 기준 분 (0 ≤ x < 1440).

    >>> parse_clock("23:15")
    1395.0
    >>> parse_clock("11:15 PM")
    1395.0
    >>> parse_clock("2026-03-01 07:30")
    450.0
    """
    if value is None:
        raise TimeParseError("빈 시각")
    text = str(value).strip()
    if not text:
        raise TimeParseError("빈 시각")

    # 날짜 부분(2026-03-01, 2026/03/01)은 시:분 탐색 전에 제거해야 오탐이 없다.
    body = re.sub(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", " ", text)

    m = _HHMM.search(body)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        second = int(m.group(3)) if m.group(3) else 0
    else:
        compact = _COMPACT.match(body.strip())
        if not compact:
            raise TimeParseError(f"시각으로 해석할 수 없음: {value!r}")
        digits = compact.group(1).zfill(4)
        hour, minute, second = int(digits[:2]), int(digits[2:]), 0

    if not 0 <= minute < 60 or not 0 <= second < 60:
        raise TimeParseError(f"분/초 범위를 벗어남: {value!r}")
    hour = _apply_meridiem(hour, body)
    if hour == 24 and minute == 0 and second == 0:
        hour = 0  # "24:00" = 자정
    if not 0 <= hour < 24:
        raise TimeParseError(f"시 범위를 벗어남: {value!r}")
    return hour * 60.0 + minute + second / 60.0


def parse_duration_minutes(value: str) -> float:
    """소요시간 → 분. "45", "45분", "1:05"(=65분), "1h20m", "1.5시간"을 받는다."""
    if value is None:
        raise TimeParseError("빈 소요시간")
    text = str(value).strip().lower()
    if not text:
        raise TimeParseError("빈 소요시간")

    # "1:05" 형식은 시:분으로 본다.
    colon = re.fullmatch(r"(\d{1,3})\s*:\s*(\d{1,2})", text)
    if colon:
        minute = int(colon.group(2))
        if minute >= 60:
            raise TimeParseError(f"분이 60 이상: {value!r}")
        return int(colon.group(1)) * 60.0 + minute

    # "1h20m", "1시간 20분", "2시간"
    hm = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hour|hours|시간))?\s*"
                      r"(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|분))?", text)
    if hm and (hm.group(1) or hm.group(2)):
        hours = float(hm.group(1)) if hm.group(1) else 0.0
        mins = float(hm.group(2)) if hm.group(2) else 0.0
        return hours * 60.0 + mins

    try:
        number = float(text)
    except ValueError as exc:
        raise TimeParseError(f"소요시간으로 해석할 수 없음: {value!r}") from exc
    # float()은 "nan"/"inf"/"Infinity"도 받아들인다. 그대로 통과시키면
    # 평균·SD가 전부 NaN이 되면서도 밤은 '유효'로 남는다.
    if not math.isfinite(number):
        raise TimeParseError(f"수가 아닌 값입니다: {value!r}")
    return number


def forward_minutes(start: float, end: float) -> float:
    """`start`에서 `end`까지 시계 방향 경과 분 (0 ≤ x < 1440).

    자정을 넘어가는 구간(예: 23:00 → 07:00 = 480분)을 자동 처리한다.
    같은 시각이면 0분으로 본다 (하루 전체가 아님).
    """
    return (end - start) % MINUTES_PER_DAY


def fmt_clock(minute_of_day: float) -> str:
    """자정 기준 분 → "HH:MM" (반올림). 수가 아니면 "—"."""
    if minute_of_day is None or not math.isfinite(minute_of_day):
        return "—"
    total = int(round(minute_of_day)) % MINUTES_PER_DAY
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_hm(minutes: float) -> str:
    """분 → "7h 12m" 형태 (음수도 표기). 수가 아니면 "—"."""
    if minutes is None or not math.isfinite(minutes):
        return "—"
    sign = "-" if minutes < 0 else ""
    total = int(round(abs(minutes)))
    return f"{sign}{total // 60}h {total % 60:02d}m"
