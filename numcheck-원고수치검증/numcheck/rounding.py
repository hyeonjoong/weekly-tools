"""보고된 숫자를 "구간"으로 다루기 — 이 툴이 오탐으로 죽지 않는 이유.

원고에 `47.9%` 라고 적혀 있으면 그것은 47.9 라는 **점**이 아니라, 어떤 참값이
어떤 반올림 관례를 거쳐 47.9 로 인쇄된 결과다. 관례는 한 가지가 아니다.

    반올림(half-up/half-even)   참값 ∈ [47.85, 47.95]
    버림(truncate)              참값 ∈ [47.90, 48.00]
    올림(ceil)                  참값 ∈ [47.80, 47.90]

셋을 모두 허용하면 참값 구간은 마지막 자리 ±1, 즉 [47.8, 48.0] 이 된다.
numcheck 는 이 **넓은 쪽**을 기본으로 쓴다. 좁게 잡으면 정직한 원고에서도
지적이 쏟아지고, 그 순간 이 툴은 두 번 다시 열리지 않는다.

같은 이유로 검정통계량도 구간이다. `t = 2.31` 은 t ∈ [2.30, 2.32] 이므로
p 도 하나의 값이 아니라 구간으로 나온다. 보고된 p 구간과 이 p 구간이
**겹치지 않을 때만** 지적한다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = [
    "Reported",
    "parse_number",
    "NUMBER_PATTERN",
    "intervals_overlap",
    "consistent",
    "effective_k",
    "fmt",
]

# 부동소수 잡음 방어용 여유. 구간 폭에 비해 무시할 만큼 작다.
_FUZZ = 1e-12

# 숫자 토큰: 부호(유니코드 마이너스 포함) · 천단위 쉼표 · 소수부 · 지수부
NUMBER_PATTERN = r"[-−–+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−–+]?\d*\.?\d+(?:[eE][-−+]?\d+)?"

_SCI_TAIL = re.compile(
    r"\s*(?:[×x*]\s*10\s*(?:\^|\*\*|⁻|<sup>)?\s*(?P<exp>[-−–+]?\d+)\s*(?:</sup>)?"
    r"|[eE](?P<exp2>[-−+]?\d+))"
)

_MINUS = {"−": "-", "–": "-", "—": "-", "−": "-"}


_THOUSANDS = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
# 원고에 실제로 섞여 들어오는 공백류(비분리·얇은·좁은 비분리 공백)
_SPACES = ("\u00a0", "\u2009", "\u202f", "\u3000", " ")


def _normalize(raw: str) -> Optional[str]:
    """공백·유니코드 마이너스를 접고 천단위 쉼표를 뗀다.

    쉼표는 **자리가 맞을 때만** 뗀다. `1,23` 을 123 으로 읽어 주면 원고의
    오탈자가 조용히 정상 숫자로 둔갑한다.
    """
    out = raw.strip()
    for bad, good in _MINUS.items():
        out = out.replace(bad, good)
    for space in _SPACES:
        out = out.replace(space, "")
    if "," in out:
        if not _THOUSANDS.match(out):
            return None
        out = out.replace(",", "")
    return out


@dataclass(frozen=True)
class Reported:
    """원고에 **적힌 그대로**의 수 하나.

    ``value``  적힌 값 자체 (47.9)
    ``ulp``    마지막 유효자리 한 칸의 크기 (0.1). 반올림 허용 구간의 단위.
    ``raw``    원문 표기
    """

    raw: str
    value: float
    ulp: float

    def interval(self, k: float = 1.0) -> Tuple[float, float]:
        """참값이 있을 수 있는 구간. ``k=1`` 이면 반올림/버림/올림 모두 허용.

        여유(fuzz)는 **상대적**이어야 한다. 절대값 1e-12 로 고정하면 p = 1.2×10⁻¹⁰
        처럼 ulp 가 1e-18 인 값에서 여유가 ulp 의 100만 배가 되어 구간이 무의미해진다.
        """
        pad = k * self.ulp * (1.0 + 1e-9) + abs(self.value) * 1e-14 + _FUZZ * min(1.0, self.ulp)
        return (self.value - pad, self.value + pad)

    @property
    def decimals(self) -> int:
        """소수 자릿수(지수 표기면 등가 자릿수). 표시용."""
        if self.ulp <= 0:
            return 0
        return max(0, int(round(-math.log10(self.ulp))))

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.raw


def parse_number(raw: str) -> Optional[Reported]:
    """숫자 표기 하나를 :class:`Reported` 로. 해석 불가면 ``None``.

    ``1,234`` · ``.03`` · ``−7.4`` · ``1.2 × 10^-4`` · ``2.5e-3`` 를 받는다.
    """
    if raw is None:
        return None
    text = _normalize(raw)
    if not text:
        return None
    exp = 0
    m = _SCI_TAIL.search(text)
    if m:
        exp_text = _normalize(m.group("exp") or m.group("exp2") or "0")
        if exp_text is None:
            return None
        try:
            exp = int(exp_text)
        except ValueError:
            return None
        text = text[: m.start()]
    if not re.fullmatch(r"[-+]?\d*\.?\d+", text):
        return None
    if "." in text:
        decimals = len(text.split(".", 1)[1])
    else:
        decimals = 0
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    # `1e400` 은 float 로 표현할 수 없다. 막지 않으면 OverflowError 가 그대로
    # 올라가 종료코드 1(= 치명 있음)이 되어, CI 가 크래시를 '지적 있음'으로 읽는다.
    if not (-400 < exp < 309) or not (-400 < -decimals + exp < 309):
        return None
    try:
        if exp:
            value *= 10.0 ** exp
        ulp = 10.0 ** (-decimals + exp)
    except OverflowError:
        return None
    if not math.isfinite(value) or not math.isfinite(ulp) or ulp <= 0:
        return None
    return Reported(raw.strip(), value, ulp)


def effective_k(k: float, *reported: Optional[Reported]) -> float:
    """정수로만 적힌 값들끼리 비교할 때 쓸 허용 배수.

    `48` 은 ulp = 1 이므로 기본 규칙(±1 ulp)이면 47~49 를 모두 허용한다. 비율
    재계산에서는 그게 옳다 — 47.916% 를 `48%` 로 적는 일이 실제로 흔하다.
    그러나 **정수끼리의 포함·차이 검사**에서는 그 관대함이 오류를 통째로 삼킨다.
    `2 (95% CI 4 to 9)` 는 점추정치가 자기 구간 밖인데도 [1,3] 과 [3,10] 이
    닿아 통과해 버렸다. 관련된 값이 **전부** 정수로 적혀 있으면 반올림 폭을
    절반(±0.5, 즉 통상적인 반올림)으로 좁힌다.
    """
    values = [r for r in reported if r is not None]
    if values and all(r.ulp >= 1.0 for r in values):
        return k * 0.5
    return k


def intervals_overlap(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    """두 닫힌 구간이 겹치는가."""
    return a[0] <= b[1] and b[0] <= a[1]


def consistent(reported: Reported, true_value: float, k: float = 1.0) -> bool:
    """참값이 보고값의 반올림 허용 구간 안에 있는가."""
    lo, hi = reported.interval(k)
    return lo <= true_value <= hi


def fmt(value: float, decimals: int = 4) -> str:
    """리포트용 숫자 포맷 — 불필요한 0 을 떼고, 아주 작은 값은 지수 표기."""
    if value != value:  # NaN
        return "nan"
    if value == 0:
        return "0"
    if abs(value) < 1e-4 or abs(value) >= 1e7:
        return f"{value:.3g}"
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"
