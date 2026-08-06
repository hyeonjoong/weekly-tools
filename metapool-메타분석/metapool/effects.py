"""연구별 효과크기(effect size)와 그 분산 계산.

지원 지표(measure)
------------------
- ``smd`` : 표준화 평균차 (Hedges g, 소표본 보정 J 적용)
- ``md``  : 평균차 (원 단위)
- ``or``  : 오즈비 → 분석은 log(OR) 척도
- ``rr``  : 위험비 → 분석은 log(RR) 척도
- ``rd``  : 위험차 (원 단위, -1 ~ 1)
- ``cor`` : 상관계수 → 분석은 Fisher z 척도, 보고는 r 로 되돌림
- ``prop``: 단일군 비율(유병률·반응률) → 분석은 logit 척도, 보고는 % 로 되돌림
- ``generic`` : 이미 계산된 효과크기 + 표준오차(또는 95% CI)

방향 규칙: 모든 2군 지표는 **1군(처치/실험군) 대 2군(대조군)** 이다.
즉 SMD/MD는 ``group1 - group2``, OR/RR은 ``group1 / group2``.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .distributions import normal_ppf

__all__ = [
    "Study",
    "MEASURES",
    "LOG_MEASURES",
    "measure_label",
    "hedges_g",
    "mean_difference",
    "log_odds_ratio",
    "log_risk_ratio",
    "risk_difference",
    "fisher_z",
    "logit_proportion",
    "build_studies",
    "is_missing",
    "MEASURE_SCALE",
    "back_transform",
    "EffectError",
]

MEASURES = ("smd", "md", "or", "rr", "rd", "cor", "prop", "generic")
#: 로그 척도에서 합성한 뒤 지수변환해서 보고하는 지표
LOG_MEASURES = ("or", "rr")

#: 지표별 **분석 척도**. 'raw' 는 변환 없음, 나머지는 보고할 때 되돌린다.
MEASURE_SCALE = {
    "smd": "raw",
    "md": "raw",
    "or": "log",
    "rr": "log",
    "rd": "raw",
    "cor": "fisherz",
    "prop": "logit",
    "generic": "raw",
}

_LABELS = {
    "smd": ("Hedges g", "표준화 평균차"),
    "md": ("MD", "평균차"),
    "or": ("OR", "오즈비"),
    "rr": ("RR", "위험비"),
    "rd": ("RD", "위험차"),
    "cor": ("r", "상관계수"),
    "prop": ("Proportion", "비율"),
    "generic": ("Effect", "효과크기"),
}


def back_transform(value: float, scale: str) -> float:
    """분석 척도 값을 보고 척도로 되돌린다.

    극단값에서 오버플로가 나면 예외를 던지는 대신 경계값으로 포화시킨다
    (리포트에서 실행이 끊기지 않도록).
    """
    if scale == "raw":
        return value
    if scale == "log":
        try:
            return math.exp(value)
        except OverflowError:
            return math.inf if value > 0 else 0.0
    if scale == "fisherz":
        if value > 20:
            return 1.0
        if value < -20:
            return -1.0
        return math.tanh(value)
    if scale == "logit":
        if value > 700:
            return 1.0
        if value < -700:
            return 0.0
        return 1.0 / (1.0 + math.exp(-value))
    raise EffectError("알 수 없는 분석 척도: %r" % (scale,))


class EffectError(ValueError):
    """효과크기를 계산할 수 없는 입력(값 범위 위반 등)."""


def measure_label(measure: str) -> str:
    en, ko = _LABELS[measure]
    return "%s(%s)" % (en, ko)


@dataclass
class Study:
    """분석 척도(로그 지표면 로그 척도) 위의 한 연구."""

    label: str
    yi: float
    vi: float
    subgroup: Optional[str] = None
    n_total: Optional[float] = None
    row: int = 0
    extra: Dict[str, float] = field(default_factory=dict)

    @property
    def sei(self) -> float:
        return math.sqrt(self.vi)

    def ci(self, z: float) -> "tuple[float, float]":
        half = z * self.sei
        return (self.yi - half, self.yi + half)


# --------------------------------------------------------------------------
# 개별 효과크기 공식
# --------------------------------------------------------------------------


def _check_n(n: float, name: str, minimum: int = 2) -> None:
    if not math.isfinite(n) or n < minimum:
        raise EffectError("%s는 %d 이상이어야 합니다 (받은 값: %s)" % (name, minimum, _fmt(n)))


def _check_sd(sd: float, name: str) -> None:
    if not math.isfinite(sd) or sd < 0:
        raise EffectError("%s는 0 이상의 유한한 값이어야 합니다 (받은 값: %s)" % (name, _fmt(sd)))


def _fmt(v: float) -> str:
    return ("%g" % v) if isinstance(v, float) else str(v)


def hedges_g(n1: float, m1: float, sd1: float, n2: float, m2: float, sd2: float):
    """Hedges g와 그 분산.

    d = (m1 - m2) / s_pooled,  s_pooled = sqrt(((n1-1)sd1^2+(n2-1)sd2^2)/(n1+n2-2))
    J = 1 - 3/(4*df - 1),  g = J*d,  var(g) = J^2 * ( (n1+n2)/(n1*n2) + d^2/(2(n1+n2)) )
    """
    _check_n(n1, "n1")
    _check_n(n2, "n2")
    _check_sd(sd1, "sd1")
    _check_sd(sd2, "sd2")
    df = n1 + n2 - 2.0
    s_pooled_sq = ((n1 - 1.0) * sd1 * sd1 + (n2 - 1.0) * sd2 * sd2) / df
    if s_pooled_sq <= 0:
        raise EffectError("두 군의 표준편차가 모두 0이면 표준화 평균차를 계산할 수 없습니다")
    d = (m1 - m2) / math.sqrt(s_pooled_sq)
    j = 1.0 - 3.0 / (4.0 * df - 1.0)
    g = j * d
    var_d = (n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2))
    return g, j * j * var_d


def mean_difference(n1: float, m1: float, sd1: float, n2: float, m2: float, sd2: float):
    """평균차와 그 분산 (등분산 가정 없이 sd1^2/n1 + sd2^2/n2)."""
    _check_n(n1, "n1", minimum=1)
    _check_n(n2, "n2", minimum=1)
    _check_sd(sd1, "sd1")
    _check_sd(sd2, "sd2")
    var = sd1 * sd1 / n1 + sd2 * sd2 / n2
    if var <= 0:
        raise EffectError("두 군의 표준편차가 모두 0이면 평균차의 분산이 0이 되어 가중치를 줄 수 없습니다")
    return m1 - m2, var


def _check_counts(e1: float, n1: float, e2: float, n2: float) -> None:
    for name, e, n in (("1군", e1, n1), ("2군", e2, n2)):
        if not math.isfinite(e) or not math.isfinite(n):
            raise EffectError("%s의 사건수/표본수가 유한한 숫자가 아닙니다" % name)
        if n <= 0:
            raise EffectError("%s의 표본수는 1 이상이어야 합니다 (받은 값: %s)" % (name, _fmt(n)))
        if e < 0:
            raise EffectError("%s의 사건수는 0 이상이어야 합니다 (받은 값: %s)" % (name, _fmt(e)))
        if e > n:
            raise EffectError("%s의 사건수(%s)가 표본수(%s)보다 큽니다" % (name, _fmt(e), _fmt(n)))


def _no_correction_error(cc: float) -> "EffectError":
    return EffectError(
        "0인 칸이 있는데 연속성 보정이 꺼져 있습니다(--cc %g) — 이 연구는 계산할 수 없습니다. "
        "--cc 0.5 (기본값)를 쓰거나 위험차(--measure rd)를 고려하세요" % cc
    )


def log_odds_ratio(e1: float, n1: float, e2: float, n2: float, cc: float = 0.5):
    """log(OR)과 그 분산. 0 셀이 있으면 네 칸 모두에 연속성 보정 ``cc``를 더한다."""
    _check_counts(e1, n1, e2, n2)
    a, b, c, d = e1, n1 - e1, e2, n2 - e2
    corrected = False
    if min(a, b, c, d) == 0:
        if (a == 0 and c == 0) or (b == 0 and d == 0):
            raise EffectError("두 군 모두 사건수가 0(또는 모두 발생)이라 오즈비를 정의할 수 없습니다")
        if cc <= 0:
            raise _no_correction_error(cc)
        a, b, c, d = a + cc, b + cc, c + cc, d + cc
        corrected = True
    yi = math.log((a * d) / (b * c))
    vi = 1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d
    return yi, vi, corrected


def log_risk_ratio(e1: float, n1: float, e2: float, n2: float, cc: float = 0.5):
    """log(RR)과 그 분산. 어느 칸이든 0이면 사건수 및 표본수에 보정을 적용한다.

    (사건수뿐 아니라 비사건수가 0인 경우에도 보정한다 — metafor의 ``to="only0"``와 동일.)
    """
    _check_counts(e1, n1, e2, n2)
    a, c = e1, e2
    n1e, n2e = float(n1), float(n2)
    corrected = False
    if min(a, n1e - a, c, n2e - c) == 0:
        if a == 0 and c == 0:
            raise EffectError("두 군 모두 사건수가 0이라 위험비를 정의할 수 없습니다")
        if a == n1e and c == n2e:
            # RR = 1 이 자료가 아니라 산술에서 나온다 — 보정으로 만든 가짜 구간을
            # 내놓느니 오즈비·위험차와 같은 기준으로 거부한다.
            raise EffectError(
                "두 군 모두 전원 발생(100%)이라 위험비에 정보가 없습니다 — "
                "연속성 보정만으로 만들어진 신뢰구간은 의미가 없습니다"
            )
        if cc <= 0:
            raise _no_correction_error(cc)
        a, c = a + cc, c + cc
        n1e, n2e = n1e + 2.0 * cc, n2e + 2.0 * cc
        corrected = True
    yi = math.log((a / n1e) / (c / n2e))
    vi = 1.0 / a - 1.0 / n1e + 1.0 / c - 1.0 / n2e
    if vi <= 0:
        raise EffectError("위험비의 분산이 0 이하로 계산되었습니다 (두 군 모두 전원 발생인 경우)")
    return yi, vi, corrected


def risk_difference(e1: float, n1: float, e2: float, n2: float):
    """위험차와 그 분산 p1(1-p1)/n1 + p2(1-p2)/n2."""
    _check_counts(e1, n1, e2, n2)
    p1, p2 = e1 / n1, e2 / n2
    vi = p1 * (1.0 - p1) / n1 + p2 * (1.0 - p2) / n2
    if vi <= 0:
        raise EffectError("위험차의 분산이 0입니다 (두 군 모두 0% 또는 100% 발생) — 가중치를 줄 수 없습니다")
    return p1 - p2, vi


def fisher_z(r: float, n: float):
    """상관계수 r 의 Fisher z 변환과 그 분산 1/(n-3).

    r 을 그대로 합성하면 분산이 r 에 의존해 가중치가 왜곡되므로,
    메타분석 관행대로 z = atanh(r) 척도에서 합성한 뒤 tanh 로 되돌린다.
    """
    if not math.isfinite(r):
        raise EffectError("상관계수가 유한한 값이 아닙니다")
    if not (-1.0 < r < 1.0):
        raise EffectError("상관계수는 -1과 1 사이여야 합니다 (±1은 Fisher z가 무한대) (받은 값: %s)" % _fmt(r))
    if not math.isfinite(n) or n < 4:
        raise EffectError("상관계수의 표본수 n은 4 이상이어야 합니다 (분산 1/(n-3)) (받은 값: %s)" % _fmt(n))
    return math.atanh(r), 1.0 / (n - 3.0)


def logit_proportion(events: float, n: float, cc: float = 0.5):
    """단일군 비율의 logit 변환과 그 분산 1/e + 1/(n-e).

    0% 또는 100% 인 연구는 logit 이 무한대라 합성할 수 없으므로,
    그 연구에 한해 사건수·비사건수에 ``cc`` 를 더한다(관행상 0.5).
    반환값은 ``(yi, vi, corrected)``.
    """
    if not math.isfinite(events) or not math.isfinite(n):
        raise EffectError("사건수/표본수가 유한한 숫자가 아닙니다")
    if n <= 0:
        raise EffectError("표본수는 1 이상이어야 합니다 (받은 값: %s)" % _fmt(n))
    if events < 0:
        raise EffectError("사건수는 0 이상이어야 합니다 (받은 값: %s)" % _fmt(events))
    if events > n:
        raise EffectError("사건수(%s)가 표본수(%s)보다 큽니다" % (_fmt(events), _fmt(n)))
    e, non = float(events), float(n) - float(events)
    corrected = False
    if e <= 0 or non <= 0:
        if cc <= 0:
            raise EffectError(
                "비율이 0%% 또는 100%% 인데 연속성 보정이 꺼져 있습니다(--cc %g) — logit 이 무한대라 "
                "이 연구는 계산할 수 없습니다" % cc
            )
        e, non = e + cc, non + cc
        corrected = True
    return math.log(e / non), 1.0 / e + 1.0 / non, corrected


# --------------------------------------------------------------------------
# 표 → Study 목록
# --------------------------------------------------------------------------

#: 지표별로 반드시 필요한 표준 열 이름
REQUIRED_COLUMNS = {
    "smd": ("n1", "mean1", "sd1", "n2", "mean2", "sd2"),
    "md": ("n1", "mean1", "sd1", "n2", "mean2", "sd2"),
    "or": ("events1", "n1", "events2", "n2"),
    "rr": ("events1", "n1", "events2", "n2"),
    "rd": ("events1", "n1", "events2", "n2"),
    "cor": ("r", "n"),
    "prop": ("events", "n"),
    "generic": ("effect",),
}


#: "1,234,567" 처럼 천 단위 구분자로 쓰인 쉼표만 제거한다.
#: 유럽식 소수점 쉼표("0,5")를 천 단위로 오해해 10배 틀리는 사고를 막기 위함.
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")


def _clip(text: str, limit: int = 60) -> str:
    """오류 메시지에 원본 값을 되싣되, 길면 잘라 로그가 비대해지지 않게 한다."""
    text = _strip_control(text)
    return text if len(text) <= limit else text[:limit] + "…"


#: 엑셀 추출표에서 "값 없음"을 뜻하는 관용 표기. 빈 칸과 똑같이 다룬다 —
#: 이걸 빈 칸과 다르게 다루면 se 가 'NA' 인 연구가 신뢰구간을 갖고 있는데도
#: 통째로 버려지고, 그 사실이 "열이 비어 있습니다"라는 엉뚱한 경고로 가려진다.
_MISSING = {"na", "nan", "n/a", "n.a.", "null", "none", "nd", "n/d", ".", "-", "--", "—", "?"}


def is_missing(raw) -> bool:
    """값이 비었거나 결측 표기인가."""
    return (raw or "").strip().lower() in _MISSING or not (raw or "").strip()


def _num(rec: Dict[str, str], key: str) -> float:
    raw = rec.get(key, "")
    text = (raw or "").strip()
    if is_missing(raw):
        raise EffectError("'%s' 열이 비어 있습니다(또는 결측 표기입니다)" % key)
    cleaned = text.replace(",", "") if _THOUSANDS.match(text) else text
    try:
        value = float(cleaned)
    except ValueError:
        hint = ""
        if "," in text:
            hint = " (소수점 쉼표는 지원하지 않습니다 — '0.5' 형식으로 저장하세요)"
        raise EffectError("'%s' 열의 값 %r 을 숫자로 읽을 수 없습니다%s" % (key, _clip(text), hint))
    if not math.isfinite(value):
        raise EffectError("'%s' 열의 값이 유한하지 않습니다 (%r)" % (key, _clip(text)))
    return value


def _se_from_record(rec: Dict[str, str], input_conf: float, log_input: bool = False) -> float:
    """se 열이 있으면 그대로, 없으면 신뢰구간에서 역산.

    ``input_conf`` 는 **입력 파일에 적힌 신뢰구간의 수준**(보통 0.95)이며,
    출력 신뢰수준(--conf)과는 별개다. 둘을 섞으면 모든 가중치가 조용히 틀어진다.
    ``log_input`` 이면 구간의 양 끝에 로그를 취한 뒤 폭을 계산한다.
    """
    has_ci = not is_missing(rec.get("ci_low")) and not is_missing(rec.get("ci_high"))
    if log_input:
        if not has_ci:
            raise EffectError(
                "--log-input 에는 ci_low/ci_high 열이 필요합니다 — 비(ratio) 척도의 표준오차는 "
                "로그 척도로 그대로 옮길 수 없습니다. 신뢰구간을 넣거나 --log-input 없이 "
                "이미 로그로 변환된 effect/se 를 넣으세요"
            )
        low, high = _num(rec, "ci_low"), _num(rec, "ci_high")
        if low <= 0:
            raise EffectError("--log-input 에서는 신뢰구간 하한도 0보다 커야 합니다 (받은 값: %g)" % low)
        if high <= low:
            raise EffectError("신뢰구간 상한(%g)이 하한(%g)보다 커야 합니다" % (high, low))
        z = normal_ppf(0.5 + input_conf / 2.0)
        return (math.log(high) - math.log(low)) / (2.0 * z)
    if not is_missing(rec.get("se")):
        se = _num(rec, "se")
        if se <= 0:
            raise EffectError("표준오차(se)는 0보다 커야 합니다 (받은 값: %g)" % se)
        return se
    if has_ci:
        low, high = _num(rec, "ci_low"), _num(rec, "ci_high")
        if high <= low:
            raise EffectError("신뢰구간 상한(%g)이 하한(%g)보다 커야 합니다" % (high, low))
        z = normal_ppf(0.5 + input_conf / 2.0)
        return (high - low) / (2.0 * z)
    raise EffectError("표준오차(se) 또는 신뢰구간(ci_low, ci_high) 열이 필요합니다")


_CONTROL_MAP = {ord(c): " " for c in map(chr, list(range(0, 32)) + [127])}


def _strip_control(text: str) -> str:
    """제어문자(ANSI 이스케이프·CR·백스페이스 등)를 공백으로 바꾼다.

    CSV의 연구명이 그대로 터미널에 출력되므로, 이스케이프 시퀀스가 살아 있으면
    이미 출력된 숫자를 덮어써 **다른 값처럼 보이게** 만들 수 있다.
    """
    if not text:
        return ""
    cleaned = text.translate(_CONTROL_MAP)
    cleaned = "".join(
        ch for ch in cleaned if unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs")
    )
    return " ".join(cleaned.split())


def build_studies(
    records: List[Dict[str, str]],
    measure: str,
    conf: float = 0.95,
    cc: float = 0.5,
    log_input: bool = False,
    input_conf: Optional[float] = None,
):
    """표준화된 레코드 목록에서 :class:`Study` 목록과 경고 문자열을 만든다.

    계산 불가능한 행은 **버리고 경고로 남긴다** (한 연구 때문에 전체 분석이
    멈추면 실무에서 쓰기 어렵기 때문). 반환값은 ``(studies, warnings)``.
    """
    if measure not in MEASURES:
        raise EffectError("알 수 없는 지표: %r (가능: %s)" % (measure, ", ".join(MEASURES)))
    if input_conf is None:
        input_conf = 0.95

    studies: List[Study] = []
    warnings: List[str] = []
    seen_labels: Dict[str, int] = {}

    for rec in records:
        row = int(rec.get("__row__", 0) or 0)
        label = _clip(_strip_control(rec.get("study") or ""), 80) or "연구%d" % row
        if label in seen_labels:
            seen_labels[label] += 1
            new_label = "%s (%d)" % (label, seen_labels[label])
            warnings.append("행 %d: 연구명 '%s'이 중복되어 '%s'로 구분했습니다." % (row, label, new_label))
            label = new_label
        else:
            seen_labels[label] = 1

        raw_sub = rec.get("subgroup")
        # 'NA'·'-' 같은 결측 표기가 진짜 하위군 수준이 되어 Q_between 을 오염시키지 않게 한다.
        subgroup = None if is_missing(raw_sub) else (_clip(_strip_control(raw_sub or ""), 40) or None)
        try:
            yi, vi, n_total, extra = _one_effect(
                rec, measure, input_conf, cc, log_input, warnings, row
            )
        except EffectError as exc:
            warnings.append("행 %d ('%s') 제외: %s" % (row, label, exc))
            continue
        except (ArithmeticError, ValueError) as exc:  # 수치 오버플로 등 예상 밖의 산술 오류
            warnings.append("행 %d ('%s') 제외: 값이 계산 범위를 벗어났습니다 (%s)" % (row, label, exc))
            continue
        if vi <= 0 or not math.isfinite(vi) or not math.isfinite(yi):
            warnings.append("행 %d ('%s') 제외: 효과크기 또는 분산이 유효하지 않습니다." % (row, label))
            continue
        # 분산이 준정규수(subnormal)면 역분산 가중치 1/vi 가 무한대로 넘쳐
        # 합성 전체가 ZeroDivisionError 로 죽는다. 그 행만 버리고 이유를 남긴다.
        if not math.isfinite(1.0 / vi):
            warnings.append(
                "행 %d ('%s') 제외: 표준오차가 너무 작아(분산 %g) 가중치가 계산 범위를 "
                "벗어납니다 — 자릿수를 확인하세요." % (row, label, vi)
            )
            continue
        studies.append(
            Study(label=label, yi=yi, vi=vi, subgroup=subgroup, n_total=n_total, row=row, extra=extra)
        )
    return studies, warnings


def _one_effect(rec, measure, input_conf, cc, log_input, warnings, row):
    extra: Dict[str, float] = {}
    if measure == "generic":
        yi = _num(rec, "effect")
        se = _se_from_record(rec, input_conf, log_input=log_input)
        if log_input:
            if yi <= 0:
                raise EffectError("--log-input 사용 시 effect는 0보다 커야 합니다 (받은 값: %g)" % yi)
            yi = math.log(yi)
        # n 은 총 표본수 표시에만 쓰는 선택 열이다 — 여기 값이 이상하다고
        # 멀쩡한 effect/se 를 버리면 안 된다.
        n_total = None
        if not is_missing(rec.get("n")):
            try:
                n_total = _num(rec, "n")
            except EffectError as exc:
                warnings.append("행 %d: 표본수(n) 열을 읽지 못해 총 N 계산에서 뺐습니다 (%s)." % (row, exc))
        return yi, se * se, n_total, extra

    if measure == "cor":
        r, n = _num(rec, "r"), _num(rec, "n")
        yi, vi = fisher_z(r, n)
        extra["r"] = r
        return yi, vi, n, extra

    if measure == "prop":
        e, n = _num(rec, "events"), _num(rec, "n")
        if e != int(e) or n != int(n):
            warnings.append("행 %d: 사건수/표본수가 정수가 아닙니다 — 입력을 확인하세요." % row)
        yi, vi, corrected = logit_proportion(e, n, cc=cc)
        extra.update({"events": e, "n": n})
        if corrected:
            warnings.append(
                "행 %d: 비율이 0%% 또는 100%% 라 연속성 보정(+%g)을 적용했습니다 — 해석에 주의하세요." % (row, cc)
            )
        return yi, vi, n, extra

    if measure in ("smd", "md"):
        n1, m1, sd1 = _num(rec, "n1"), _num(rec, "mean1"), _num(rec, "sd1")
        n2, m2, sd2 = _num(rec, "n2"), _num(rec, "mean2"), _num(rec, "sd2")
        fn = hedges_g if measure == "smd" else mean_difference
        yi, vi = fn(n1, m1, sd1, n2, m2, sd2)
        extra.update({"n1": n1, "n2": n2})
        return yi, vi, n1 + n2, extra

    e1, n1 = _num(rec, "events1"), _num(rec, "n1")
    e2, n2 = _num(rec, "events2"), _num(rec, "n2")
    extra.update({"events1": e1, "n1": n1, "events2": e2, "n2": n2})
    if any(v != int(v) for v in (e1, n1, e2, n2)):
        warnings.append("행 %d: 사건수/표본수가 정수가 아닙니다 — 입력을 확인하세요." % row)
    if measure == "rd":
        yi, vi = risk_difference(e1, n1, e2, n2)
        return yi, vi, n1 + n2, extra
    fn = log_odds_ratio if measure == "or" else log_risk_ratio
    yi, vi, corrected = fn(e1, n1, e2, n2, cc=cc)
    if corrected:
        warnings.append(
            "행 %d: 0인 칸이 있어 연속성 보정(+%g)을 적용했습니다 — 결과 해석에 주의하세요." % (row, cc)
        )
    return yi, vi, n1 + n2, extra
