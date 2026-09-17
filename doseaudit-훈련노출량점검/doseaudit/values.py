"""셀 문자열을 숫자·날짜로 바꾼다 — 그리고 **실패한 행을 센다**.

이 로그의 값은 단위가 붙은 문자열로 온다: `'1회'`, `'0초'`, `'4.04초'`,
날짜는 `'2026.02.05'`. 단위를 떼는 일 자체는 쉽지만, **뗄 수 없는 값을 조용히
버리면 분모가 조용히 줄어든다.** 그래서 모든 변환 함수는 실패를 예외나 None 으로
드러내고, 호출부가 그 건수를 커버리지 자백에 싣는다.
"""

import datetime
import re

from doseaudit.sanitize import nfc

#: 숫자 앞뒤에 붙는 것들. 천단위 콤마·공백·단위·전각 숫자까지 받는다.
_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_FULLWIDTH = {ord("０") + i: ord("0") + i for i in range(10)}
_FULLWIDTH[ord("．")] = ord(".")
_FULLWIDTH[ord("，")] = ord(",")

#: 이 로그가 실제로 쓰는 단위. 열마다 **기대 단위를 지정해서** 부른다.
#: `소요시간(초)` 열에 `'5분'` 이 들어오면 5 로 읽어 버리는 것이 아니라 **해석 실패**로
#: 센다 — 조용히 60배 줄어든 값보다 "못 읽었다"가 낫다.
SECONDS_UNITS = ("초", "sec", "s")
COUNT_UNITS = ("회", "번", "회차")
DAY_UNITS = ("일", "days", "day")
PERCENT_UNITS = ("%", "퍼센트", "pct")

#: 차원이 다른 단위. 만나면 이름을 붙여 거절한다(조용히 떼지 않는다).
_WRONG_DIMENSION = {
    "분": "분", "min": "분", "시간": "시간", "hr": "시간", "h": "시간",
    "ms": "밀리초", "밀리초": "밀리초", "㎳": "밀리초",
}

_DATE_SEP_RE = re.compile(r"^(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})\s*일?$")
#: 뒤에 붙은 시각(`2026-02-05 14:33`)만 떼어낸다. **시각은 다루지 않는다** —
#: 통째로 공백 분리하면 `2026년 2월 5일` 이 `2026년` 에서 잘린다.
_TIME_TAIL_RE = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_DATE_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class ValueError_(ValueError):
    """값을 해석하지 못했다. 호출부가 잡아서 '해석실패'로 센다."""


def normalize_number_text(raw):
    """전각 숫자·구분자를 반각으로. 숫자 해석과 **자릿수 세기가 같은 글자**를 보게 한다."""
    if raw is None:
        return ""
    return nfc(str(raw)).strip().translate(_FULLWIDTH)


def strip_unit(raw, units=(), allow_negative=False):
    """`strip_unit('4.04초', units=SECONDS_UNITS)` → `4.04`. 해석 못 하면 `ValueError_`.

    두 가지를 일부러 까다롭게 한다.

    1. **기대한 단위만 뗀다.** `소요시간(초)` 열의 `'5분'` 을 `5.0` 으로 읽으면 그 날의
       합계가 60배 줄어들고 `--active-day min-seconds=S` 판정이 조용히 뒤집힌다.
       차원이 다른 단위는 이름을 붙여 거절한다.
    2. **음수를 받지 않는다**(`allow_negative=True` 일 때만 허용). 소요시간 `-100초` 를
       평균에 넣으면 '평균 소요시간 -21.67초' 같은 값이 인쇄된다.

    빈 문자열도 실패다 — 빈 칸을 0 으로 읽는 것이 이 툴이 막으려는 바로 그 사고다.
    """
    if raw is None:
        raise ValueError_("빈 값")
    text = normalize_number_text(raw)
    if not text:
        raise ValueError_("빈 값")
    text = text.replace(",", "").replace(" ", "")

    value = None
    if _NUM_RE.match(text):
        value = float(text)
    else:
        lowered = text.lower()
        for unit in sorted(units, key=len, reverse=True):
            u = unit.lower()
            if lowered.endswith(u):
                head = text[: len(text) - len(unit)]
                if _NUM_RE.match(head):
                    value = float(head)
                    break
        if value is None:
            for unit in sorted(_WRONG_DIMENSION, key=len, reverse=True):
                if lowered.endswith(unit.lower()):
                    head = text[: len(text) - len(unit)]
                    if _NUM_RE.match(head):
                        raise ValueError_("단위가 다름(%s) — 이 열의 단위로 환산하지 않습니다"
                                          % _WRONG_DIMENSION[unit])
            raise ValueError_("숫자로 해석하지 못함")

    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError_("유한한 숫자가 아님")
    if value < 0 and not allow_negative:
        raise ValueError_("음수")
    return value


def parse_date(raw):
    """`'2026.02.05'`/`'2026-02-05'`/`'20260205'` → `datetime.date`.

    **연도-먼저 형식만 읽는다.** `05.02.2026` 은 2월 5일인지 5월 2일인지 이 툴이
    알 방법이 없고, 추측하면 활동일수가 조용히 틀린다 → `ValueError_`.
    """
    if raw is None:
        raise ValueError_("빈 날짜")
    text = nfc(str(raw)).strip().translate(_FULLWIDTH)
    if not text:
        raise ValueError_("빈 날짜")
    text = _TIME_TAIL_RE.sub("", text).strip()
    m = _DATE_SEP_RE.match(text) or _DATE_COMPACT_RE.match(text)
    if not m:
        raise ValueError_("연도-먼저 날짜 형식이 아님")
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime.date(y, mo, d)
    except ValueError as exc:
        raise ValueError_("달력에 없는 날짜") from exc


def fmt_date(value):
    """`date` → `'2026-02-05'`. None 은 빈 문자열."""
    return value.isoformat() if value is not None else ""


def fmt_number(value):
    """정수는 정수로, 소수는 소수로. `3.0` 을 `3` 으로, `3.5` 는 `3.5` 로 인쇄한다."""
    if value is None:
        return ""
    return "%d" % value if float(value).is_integer() else ("%g" % value)


def fmt_dot(value):
    """`date` → `'2026.02.05'` (로그 원본 표기와 맞춰 인쇄할 때)."""
    return value.strftime("%Y.%m.%d") if value is not None else ""
