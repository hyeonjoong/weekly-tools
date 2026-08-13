"""검정통계량 → p 값. 원고에 적힌 p 를 다시 계산하기 위한 최소 분포 집합.

지원: Student t, Fisher F, Pearson χ², 표준정규 z, 상관계수 r(→ t 변환).

모두 **꼬리 확률을 직접** 계산한다(1 − CDF 를 쓰지 않는다). p = 3e-14 같은 값도
상대오차를 유지해야 "보고값 p < .001 과 재계산값이 모순"인지 아닌지를 옳게
판정할 수 있기 때문이다.

자유도는 실수를 허용한다(Greenhouse–Geisser 로 보정된 df = 1.63 같은 값이
원고에 그대로 적히는 일이 흔하다).
"""

from __future__ import annotations

import math

from .mathx import NumericError, betainc_reg, gammainc_upper_reg

__all__ = [
    "t_sf",
    "t_two_tailed",
    "f_sf",
    "chi2_sf",
    "z_sf",
    "z_two_tailed",
    "r_two_tailed",
    "p_from_statistic",
]


# 지원하는 자유도 상한.
#
# `betainc_reg` 의 앞항은 lgamma(a+b) − lgamma(a) − lgamma(b) 로 계산되는데,
# a = df/2 가 커지면 각 항이 10^6 규모가 되어 차이에서 상대정밀도가 깎인다.
# 측정: df = 7×10^5 에서 t 의 상대오차가 6.2e-9 로 저장소 기준(≤1e-9)을 넘었다.
# 무작위 4,000점 격자로 확인한 결과 df ≤ 5×10^4 에서는 ≤1e-9 가 유지된다. 원고에 이보다 큰 자유도가 적히는 일은
# 없으므로, **보장할 수 없는 값을 계산해 내놓는 대신 거절하고 그 claim 을 건너뛴다.**
MAX_DF = 50_000.0


def _check_df(df: float, name: str) -> None:
    if not math.isfinite(df) or df <= 0:
        raise NumericError(f"{name} 자유도는 0보다 큰 유한한 값이어야 합니다: {df!r}")
    if df > MAX_DF:
        raise NumericError(
            f"{name} 자유도가 지원 범위({MAX_DF:g})를 넘습니다: {df!r}. "
            "이 값은 정확도를 보장할 수 없어 재계산하지 않습니다."
        )


def t_sf(t: float, df: float) -> float:
    """P(T > t) — 단측(상측) 꼬리."""
    _check_df(df, "t")
    if not math.isfinite(t):
        raise NumericError("t 통계량이 유한하지 않습니다.")
    half = 0.5 * betainc_reg(df / 2.0, 0.5, df / (df + t * t))
    return half if t >= 0 else 1.0 - half


def t_two_tailed(t: float, df: float) -> float:
    """P(|T| > |t|) — 양측."""
    _check_df(df, "t")
    if not math.isfinite(t):
        raise NumericError("t 통계량이 유한하지 않습니다.")
    return betainc_reg(df / 2.0, 0.5, df / (df + t * t))


def f_sf(f: float, df1: float, df2: float) -> float:
    """P(F > f) — F 분포 상측 꼬리(분산분석의 관례적 p)."""
    _check_df(df1, "F 분자")
    _check_df(df2, "F 분모")
    if not math.isfinite(f):
        raise NumericError("F 통계량이 유한하지 않습니다.")
    if f <= 0:
        return 1.0
    return betainc_reg(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def chi2_sf(x: float, df: float) -> float:
    """P(X > x) — χ² 상측 꼬리."""
    _check_df(df, "χ²")
    if not math.isfinite(x):
        raise NumericError("χ² 통계량이 유한하지 않습니다.")
    if x <= 0:
        return 1.0
    return gammainc_upper_reg(df / 2.0, x / 2.0)


def z_sf(z: float) -> float:
    """P(Z > z) — 표준정규 상측 꼬리."""
    if not math.isfinite(z):
        raise NumericError("z 통계량이 유한하지 않습니다.")
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def z_two_tailed(z: float) -> float:
    """P(|Z| > |z|)."""
    if not math.isfinite(z):
        raise NumericError("z 통계량이 유한하지 않습니다.")
    return math.erfc(abs(z) / math.sqrt(2.0))


def r_two_tailed(r: float, df: float) -> float:
    """Pearson r 의 양측 p. t = r·√(df/(1−r²)) 변환을 쓴다."""
    _check_df(df, "r")
    if not math.isfinite(r):
        raise NumericError("r 이 유한하지 않습니다.")
    if abs(r) >= 1.0:
        return 0.0
    t = abs(r) * math.sqrt(df / (1.0 - r * r))
    return t_two_tailed(t, df)


def p_from_statistic(kind: str, value: float, df: tuple, tail: str = "two") -> float:
    """통계량 종류에 따라 p 를 돌려준다.

    Parameters
    ----------
    kind : 't' | 'F' | 'chi2' | 'r' | 'z'
    value : 통계량 값
    df : t/χ²/r 은 ``(df,)``, F 는 ``(df1, df2)``, z 는 ``()``
    tail : 'two' 또는 'one'. F·χ² 는 관례상 항상 상측이므로 무시된다.
    """
    if kind == "t":
        p = t_two_tailed(value, df[0])
        return p if tail == "two" else p / 2.0
    if kind == "r":
        p = r_two_tailed(value, df[0])
        return p if tail == "two" else p / 2.0
    if kind == "z":
        p = z_two_tailed(value)
        return p if tail == "two" else p / 2.0
    if kind == "F":
        return f_sf(value, df[0], df[1])
    if kind == "chi2":
        return chi2_sf(value, df[0])
    raise NumericError(f"알 수 없는 통계량 종류: {kind!r}")
