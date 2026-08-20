"""날짜 파서 — 좁고 결정론적으로.

허용하는 형식 (전부 연도-먼저):
  2026-03-02 / 2026/03/02 / 2026.3.2 / 2026년 3월 2일 / 20260302
  엑셀 날짜 시리얼 정수(20000~80000, 1900 에포크)
  위 형식 + 시각 (2026-03-02 14:30, 2026-03-02T14:30:00) — 시각은 버리고 자백

일-먼저(07-06-2026)·월-먼저(03/01/2026)는 해석하지 않는다 — 추측하면 조용히
틀리기 때문. 파싱 실패는 '데이터 오류'로 크게 보고되지, 추정되지 않는다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple, Optional

# 엑셀 1900 날짜 체계: 시리얼 1 = 1900-01-01 이지만 가공의 1900-02-29 버그 때문에
# 1899-12-30 을 기점으로 더하면 1900-03-01 이후의 모든 시리얼이 맞다.
# 시리얼 범위를 20000(1954-10-03)~80000(2119-01-11)으로 제한하므로 버그 구간과 만나지 않는다.
_EXCEL_EPOCH = dt.date(1899, 12, 30)
EXCEL_SERIAL_MIN = 20000
EXCEL_SERIAL_MAX = 80000

_RE_YMD = re.compile(
    r"^(?P<y>\d{4})\s*[-/.년]\s*(?P<m>\d{1,2})\s*[-/.월]\s*(?P<d>\d{1,2})\s*일?(?P<rest>.*)$"
)
# 앞 구분자(T/공백)는 선택 — 날짜 정규식의 \s* 가 공백을 이미 소비했을 수 있다
_RE_TIME_REST = re.compile(r"^[Tt\s]?\s*\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*$")
_RE_COMPACT = re.compile(r"^(19|20)\d{6}$")
_RE_SERIAL = re.compile(r"^(?P<int>\d{1,6})(?P<frac>\.\d+)?$")


class Parsed(NamedTuple):
    date: Optional[dt.date]
    had_time: bool
    error: Optional[str]  # 실패 사유 (한국어) — date is None 일 때만


def parse_date(raw: object) -> Parsed:
    """문자열 하나를 날짜로. 실패하면 (None, False, 사유)."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return Parsed(None, False, "빈 값")

    m = _RE_YMD.match(text)
    if m:
        rest = m.group("rest").strip()
        had_time = False
        if rest:
            if _RE_TIME_REST.match(m.group("rest")):
                had_time = True
            else:
                return Parsed(None, False, f"형식 인식 불가: {text!r}")
        try:
            d = dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return Parsed(None, False, f"달력에 없는 날짜: {text!r}")
        return Parsed(d, had_time, None)

    if _RE_COMPACT.match(text):
        try:
            d = dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return Parsed(None, False, f"달력에 없는 날짜: {text!r}")
        return Parsed(d, False, None)

    m = _RE_SERIAL.match(text)
    if m:
        serial = int(m.group("int"))
        if EXCEL_SERIAL_MIN <= serial <= EXCEL_SERIAL_MAX:
            had_time = bool(m.group("frac")) and float(m.group("frac")) > 0
            return Parsed(_EXCEL_EPOCH + dt.timedelta(days=serial), had_time, None)
        return Parsed(None, False, f"엑셀 시리얼 범위 밖의 숫자: {text!r}")

    return Parsed(None, False, f"형식 인식 불가: {text!r}")


_RE_ASOF = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_asof(text: str) -> dt.date:
    """--as-of 값. 문서가 약속한 대로 YYYY-MM-DD **만** 허용.

    방문일 파서를 재사용하지 않는다 — 엑셀 시리얼("44927")이나 점 표기가
    조용히 기준시점이 되는 것이 위험하기 때문이다.
    """
    t = (text or "").strip()
    if not _RE_ASOF.match(t):
        raise ValueError(f"--as-of 는 YYYY-MM-DD 형식이어야 합니다: {text!r}")
    try:
        return dt.date(int(t[:4]), int(t[5:7]), int(t[8:10]))
    except ValueError:
        raise ValueError(f"--as-of 가 달력에 없는 날짜입니다: {text!r}")


def iso(d: Optional[dt.date]) -> str:
    return d.isoformat() if d else ""


def md(d: Optional[dt.date]) -> str:
    """리포트 본문용 짧은 표기 (MM-DD)."""
    return d.strftime("%m-%d") if d else ""
