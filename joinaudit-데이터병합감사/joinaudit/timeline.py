"""시점 정렬 — 날짜 파싱, 자정 넘김 귀속, 방문 라벨 정규화.

수면 데이터에서 가장 흔하고 가장 조용한 오류가 여기서 난다. 03:20의 HRV 값이
"어느 날 밤에 속하는가"를 파일마다 다르게 정하면 표는 완성되지만 하루씩
어긋난다.

이 모듈이 지키는 원칙
--------------------
* **날짜 형식은 셀이 아니라 열 단위로 정한다.** `03/01/2026` 한 칸만 보면
  3월 1일인지 1월 3일인지 알 수 없다. 열 전체를 보고 **한 가지 해석만 남을
  때** 그것을 쓰고, 둘 이상 남으면 추측하지 않고 `--date-format` 을 요구한다.
* **타임존은 변환하지 않는다.** 오프셋이 섞여 있으면 보고하고 멈춘다.
  (`+09:00` 과 naive 를 섞어 계산하면 결과가 조용히 9시간 밀린다.)
* **모르는 방문 라벨은 추측하지 않는다.** 원본 라벨을 그대로 시점으로 쓰되
  "이 라벨은 사전 정의표에 없다"고 보고한다.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .dataio import is_missing

__all__ = [
    "ParsedTime", "DatePlan", "plan_date_column", "parse_date_cell",
    "night_of", "parse_cutoff", "normalize_visit", "VisitNormalizer",
    "VISIT_ALIASES",
]

# 날짜 / 시각 / 오프셋 분해
_SPLIT_RE = re.compile(
    r"^\s*(?P<date>.+?)"
    r"(?:[ T](?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?))?"
    r"\s*(?P<tz>Z|z|[+-]\d{2}:?\d{2})?\s*$")

_YMD_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\.?$")
_THREE_RE = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})\.?$")
_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_KOREAN_RE = re.compile(r"^(\d{2,4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?$")
_SERIAL_RE = re.compile(r"^\d{4,6}(\.\d+)?$")

# 엑셀 날짜 시리얼로 볼 수 있는 범위: 1950-01-01 ~ 2079-12-31 근방.
_SERIAL_MIN, _SERIAL_MAX = 18_264, 65_745

_ORDERS = ("ymd", "dmy", "mdy")


@dataclass(frozen=True)
class ParsedTime:
    """파싱된 한 시점."""

    date: _dt.date
    time: Optional[_dt.time] = None
    offset: str = ""            # '' = naive, 'Z'/'+09:00' = 오프셋 표기 있음

    def isoformat(self) -> str:
        if self.time is None:
            return self.date.isoformat()
        return f"{self.date.isoformat()} {self.time.isoformat()}"


@dataclass
class DatePlan:
    """한 날짜 열에 대해 확정한 해석 규칙."""

    order: str = "ymd"                 # 'ymd' | 'dmy' | 'mdy'
    excel_serial: bool = False
    candidates: Tuple[str, ...] = ()   # 증거와 양립하는 해석들
    ambiguous: bool = False
    offsets: Set[str] = field(default_factory=set)
    naive_count: int = 0
    aware_count: int = 0
    parsed: int = 0
    failed: int = 0
    has_time: int = 0
    note: str = ""

    @property
    def mixed_timezone(self) -> bool:
        """오프셋이 두 종류 이상이거나, 오프셋 있는 행과 없는 행이 섞였는가."""
        if len(self.offsets) > 1:
            return True
        return bool(self.offsets) and self.naive_count > 0


def _norm_token(token: str) -> str:
    return unicodedata.normalize("NFKC", token or "").strip()


def _split(token: str) -> Optional[Tuple[str, Optional[str], str]]:
    m = _SPLIT_RE.match(token)
    if not m:
        return None
    tz = (m.group("tz") or "").strip()
    if tz.lower() == "z":
        tz = "Z"
    elif tz and ":" not in tz:
        tz = tz[:3] + ":" + tz[3:]
    return m.group("date").strip(), m.group("time"), tz


def _make_time(text: Optional[str]) -> Tuple[Optional[_dt.time], bool]:
    """'23:40' -> time. (시각, 유효성)."""
    if not text:
        return None, True
    parts = text.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(float(parts[2])) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return None, False
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        # 24:00 은 자정을 뜻하기도 하지만, 그 해석이 하루를 밀 수 있으므로
        # 받아들이지 않고 파싱 실패로 보고한다.
        return None, False
    return _dt.time(hour, minute, second), True


def _date_from(a: int, b: int, c: int, order: str,
               short_year: bool = True) -> Optional[_dt.date]:
    if order == "ymd":
        y, m, d = a, b, c
    elif order == "dmy":
        d, m, y = a, b, c
    else:                       # mdy
        m, d, y = a, b, c
    # 두 자리 연도: 69 이하는 2000년대(POSIX 관행). **네 자리로 적힌 `0001` 은
    # 두 자리 연도가 아니므로** 손대지 않는다 — 고치면 1901년이 되어 버린다.
    if short_year and y < 100:
        y += 2000 if y <= 68 else 1900
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def _date_candidates(text: str) -> Optional[Dict[str, _dt.date]]:
    """날짜 문자열 -> {해석: 날짜}. None 이면 날짜 모양이 아니다."""
    text = text.strip()

    m = _KOREAN_RE.match(text)
    if m:
        d = _date_from(int(m.group(1)), int(m.group(2)), int(m.group(3)), "ymd")
        return {o: d for o in _ORDERS} if d else {}

    m = _COMPACT_RE.match(text)
    if m:
        d = _date_from(int(m.group(1)), int(m.group(2)), int(m.group(3)), "ymd")
        return {o: d for o in _ORDERS} if d else {}

    m = _YMD_RE.match(text)
    if m:
        d = _date_from(int(m.group(1)), int(m.group(2)), int(m.group(3)), "ymd",
                       short_year=len(m.group(1)) <= 2)
        return {o: d for o in _ORDERS} if d else {}

    m = _THREE_RE.match(text)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        out: Dict[str, _dt.date] = {}
        for order in _ORDERS:
            # 4자리 숫자는 연도 자리에만 올 수 있다.
            if len(m.group(1)) == 4 and order != "ymd":
                continue
            if len(m.group(3)) == 4 and order == "ymd":
                continue
            year_len = len(m.group(3) if order != "ymd" else m.group(1))
            d = _date_from(a, b, c, order, short_year=year_len <= 2)
            if d:
                out[order] = d
        return out

    return None


def plan_date_column(tokens: Sequence[str]) -> DatePlan:
    """열 전체를 보고 날짜 해석 규칙을 확정한다.

    모든 값과 양립하는 해석만 남긴다. 하나만 남으면 확정, 둘 이상 남으면
    `ambiguous=True` — 호출부가 `--date-format` 을 요구하고 멈춘다.
    """
    plan = DatePlan()
    values = [_norm_token(t) for t in tokens]
    present = [v for v in values if v and not is_missing(v)]
    if not present:
        plan.note = "값이 없어 형식을 판정하지 못했습니다"
        plan.candidates = tuple(_ORDERS)
        return plan

    # 엑셀이 날짜를 숫자로 내보낸 경우: 전부 시리얼 범위의 숫자.
    if all(_SERIAL_RE.match(v) for v in present):
        try:
            nums = [float(v) for v in present]
        except ValueError:
            nums = []
        if nums and all(_SERIAL_MIN <= n <= _SERIAL_MAX for n in nums):
            plan.excel_serial = True
            plan.parsed = len(present)
            plan.candidates = ("excel-serial",)
            plan.order = "ymd"
            plan.note = "엑셀 날짜 시리얼(숫자)로 해석했습니다"
            return plan

    viable = set(_ORDERS)
    seen: List[Dict[str, _dt.date]] = []
    for value in present:
        split = _split(value)
        if split is None:
            plan.failed += 1
            continue
        date_text, time_text, tz = split
        _, time_ok = _make_time(time_text)
        if time_text:
            plan.has_time += 1
        if not time_ok:
            plan.failed += 1
            continue
        if tz:
            plan.offsets.add(tz)
            plan.aware_count += 1
        else:
            plan.naive_count += 1
        cands = _date_candidates(date_text)
        if cands is None or not cands:
            plan.failed += 1
            continue
        plan.parsed += 1
        viable &= set(cands)
        seen.append(cands)

    if not viable:
        # 어떤 단일 해석으로도 열 전체를 설명할 수 없다 — 형식이 섞였다.
        plan.candidates = ()
        plan.ambiguous = True
        plan.note = "한 가지 날짜 형식으로 열 전체를 설명할 수 없습니다(형식 혼재)"
        return plan

    ordered = tuple(o for o in _ORDERS if o in viable)
    plan.candidates = ordered
    plan.order = ordered[0]

    # 해석이 여러 개 남았다고 해서 곧바로 모호한 것은 아니다. `2026-03-10` 은
    # ymd/dmy/mdy 어느 규칙으로 읽어도 **같은 날**이다. 실제로 값이 갈리는
    # 행이 하나라도 있을 때만 모호로 본다 — 그렇지 않으면 ISO 날짜 열마다
    # `--date-format` 을 요구하는 쓸모없는 툴이 된다.
    plan.ambiguous = any(
        len({cands[o] for o in ordered if o in cands}) > 1 for cands in seen)
    if plan.ambiguous:
        plan.note = ("이 열만 보고는 " + " / ".join(ordered) +
                     " 를 구분할 수 없습니다")
    elif len(ordered) > 1:
        plan.note = ("어느 해석으로 읽어도 같은 날짜입니다"
                     f"({ordered[0]} 로 확정)")
    else:
        plan.note = f"{ordered[0]} 형식으로 확정했습니다"
    return plan


def parse_date_cell(token: str, plan: DatePlan) -> Optional[ParsedTime]:
    """확정된 규칙으로 셀 하나를 파싱한다. 실패하면 None."""
    value = _norm_token(token)
    if not value or is_missing(value):
        return None

    if plan.excel_serial:
        try:
            serial = float(value)
        except ValueError:
            return None
        if not _SERIAL_MIN <= serial <= _SERIAL_MAX:
            return None
        base = _dt.datetime(1899, 12, 31)
        days = serial - 1 if serial >= 61 else serial
        try:
            dt = base + _dt.timedelta(days=days)
        except (OverflowError, ValueError):
            return None
        if dt.microsecond:
            dt = (dt + _dt.timedelta(microseconds=500_000)).replace(microsecond=0)
        time = dt.time() if (dt.hour or dt.minute or dt.second) else None
        return ParsedTime(dt.date(), time, "")

    split = _split(value)
    if split is None:
        return None
    date_text, time_text, tz = split
    time, ok = _make_time(time_text)
    if not ok:
        return None
    cands = _date_candidates(date_text)
    if not cands:
        return None
    date = cands.get(plan.order)
    if date is None:
        # 이 셀은 확정된 해석과 맞지 않는다(예: 열은 dmy 인데 이 값은 13/25/…).
        return None
    return ParsedTime(date, time, tz)


def parse_cutoff(text: str) -> _dt.time:
    """'12:00' -> time. 잘못된 값은 ValueError."""
    parts = str(text).strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"시각 형식이 잘못되었습니다: '{text}' (예: 12:00)")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ValueError(f"시각 형식이 잘못되었습니다: '{text}' (예: 12:00)")
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"시각 범위를 벗어났습니다: '{text}'")
    return _dt.time(hour, minute, second)


def night_of(parsed: ParsedTime, cutoff: _dt.time) -> _dt.date:
    """자정 넘김 귀속: `cutoff` 이전 시각은 **앞선 날짜의 밤**에 속한다.

    정오(12:00) 기준일 때
    ``2026-03-10 23:40`` -> 2026-03-10 밤,
    ``2026-03-11 03:20`` -> 2026-03-10 밤(같은 밤),
    ``2026-03-11 13:00`` -> 2026-03-11 밤(다음 밤).

    시각이 없는 값은 날짜를 그대로 쓴다 — 시각을 모르는데 하루를 옮기는 것이
    더 큰 오류이기 때문이다(리포트에 건수를 남긴다).
    """
    if parsed.time is None:
        return parsed.date
    return parsed.date - _dt.timedelta(days=1) if parsed.time < cutoff else parsed.date


# --------------------------------------------------------------------------
# 방문 라벨
# --------------------------------------------------------------------------

# 사전 정의 별칭표. **연구마다 다른 매핑은 넣지 않는다** — 예를 들어 `V1` 을
# baseline 으로 볼지 첫 추적으로 볼지는 프로토콜마다 다르므로, `V1` 은 자기
# 자신의 계열(`visit1`)로만 정규화하고 baseline 과 섞지 않는다. 연구별 매핑은
# `--spec` 의 `visit_aliases` 로 사람이 명시한다.
VISIT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "baseline": ("BASELINE", "BASE", "BL", "기저", "기저선", "사전", "PRE",
                 "T0", "V0", "방문0", "SCREENING", "선별"),
    "endpoint": ("ENDPOINT", "END", "POST", "사후", "종료", "FINAL", "EOS",
                 "종료시점"),
    "followup": ("FOLLOWUP", "FOLLOW-UP", "FOLLOW UP", "FU", "추적",
                 "추적관찰"),
}

_WEEK_RE = re.compile(r"^(?:W|WK|WEEK|주)\s*(\d{1,3})$|^(\d{1,3})\s*(?:주|WEEK|W)$")
_MONTH_RE = re.compile(r"^(?:M|MO|MONTH|개월)\s*(\d{1,3})$|^(\d{1,3})\s*(?:개월|MONTH|M)$")
_DAY_RE = re.compile(r"^(?:D|DAY|일차)\s*(\d{1,3})$|^(\d{1,3})\s*(?:일차|DAY)$")
_VISIT_RE = re.compile(r"^(?:V|VISIT|방문)\s*(\d{1,3})$|^(\d{1,3})\s*(?:방문|VISIT)$")

_PATTERNS = ((_WEEK_RE, "week"), (_MONTH_RE, "month"),
             (_DAY_RE, "day"), (_VISIT_RE, "visit"))


def _build_lookup(extra: Optional[Dict[str, Sequence[str]]]
                  ) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for canon, labels in VISIT_ALIASES.items():
        for label in labels:
            lookup[label.upper()] = canon
        lookup[canon.upper()] = canon
    for canon, labels in (extra or {}).items():
        # 사용자 지정이 기본표를 덮어쓴다.
        lookup[str(canon).upper()] = str(canon)
        for label in labels or ():
            lookup[_norm_token(str(label)).upper()] = str(canon)
    return lookup


class VisitNormalizer:
    """방문 라벨 정규화기. 별칭표를 한 번만 만들어 두고 재사용한다."""

    def __init__(self, extra: Optional[Dict[str, Sequence[str]]] = None) -> None:
        self._lookup = _build_lookup(extra)

    def __call__(self, raw: str) -> Tuple[str, bool]:
        """방문 라벨 -> (정규 라벨, 사전 정의표/패턴으로 인식했는가).

        모르는 라벨은 **추측하지 않는다.** 공백/대소문자만 정리한 원본을 그대로
        시점으로 쓰고 `False` 를 함께 돌려주어, 호출부가 "이 라벨은 사전 정의표에
        없다"고 보고하게 한다.
        """
        token = _norm_token(raw)
        if not token or is_missing(token):
            return "", False
        probe = re.sub(r"\s+", " ", token).strip().upper()
        if probe in self._lookup:
            return self._lookup[probe], True
        squashed = probe.replace(" ", "").replace("_", "").replace("-", "")
        if squashed in self._lookup:
            return self._lookup[squashed], True
        for pattern, family in _PATTERNS:
            m = pattern.match(squashed)
            if m:
                number = m.group(1) or m.group(2)
                return f"{family}{int(number)}", True
        return probe, False


_DEFAULT_VISITS = VisitNormalizer()


def normalize_visit(raw: str,
                    extra: Optional[Dict[str, Sequence[str]]] = None
                    ) -> Tuple[str, bool]:
    """`VisitNormalizer` 의 단발 호출용 래퍼."""
    normalizer = VisitNormalizer(extra) if extra else _DEFAULT_VISITS
    return normalizer(raw)
