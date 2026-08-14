"""표본수·검정력 계획 (sample-size / power planning) — 표준 라이브러리만.

왜 필요한가
-----------
파일럿(예비) 연구를 돌린 임상/제약 연구자가 그 다음에 반드시 답해야 하는 질문은
"그래서 본시험에 몇 명이 필요한가?" 입니다. hrvkit 의 --paired/--groups 는 효과크기
(Cohen's dz·Hedges g)와 신뢰구간까지 내지만, 그 숫자를 **설계 표본수로 옮기는**
단계가 비어 있었습니다. 이 모듈이 그 다리입니다.

핵심 원리
---------
표본평균차 검정의 검정통계량은 귀무가설이 거짓일 때 **비중심 t 분포**를 따릅니다:

    T' = (Z + δ) / √(V/df),   Z~N(0,1),  V~χ²_df,  δ = 비중심모수(ncp)

양측 검정력은  power = P(|T'| > t_{1−α/2, df}) 입니다. 정확한 비중심 t CDF 를
직접 적분해서 구합니다(근사식 아님):

    P(T' ≤ t) = E_{U}[ Φ( t·U − δ ) ],   U = √(V/df)

U 의 밀도는 닫힌 형태로 알려져 있으므로(아래 _log_pdf_u) Simpson 적분 한 번이면
됩니다. df=1 에서 χ² 밀도가 0 에서 발산하는 문제를 피하려고 V 가 아니라 U 로
변수변환해 적분합니다 (U 밀도는 u^(df−1) 이라 df≥1 에서 유한).

설계별 df 와 ncp
----------------
  paired / one-sample : df = n − 1,     ncp = d·√n            (n = 피험자 수)
  parallel (군당 n)   : df = 2n − 2,    ncp = d·√(n/2)        (n = **군당** 수)

정직성에 대한 메모
------------------
1) **사후 검정력(observed/post-hoc power)은 계산해서 보고하지 않습니다.** 관측된
   효과크기를 그대로 넣은 검정력은 p값의 단조함수라 새로운 정보가 없고("p가 작으면
   검정력이 높다"는 동어반복), 유의하지 않은 결과를 변명하는 데 오용됩니다.
   대신 **다음 시험에 필요한 N** 을 냅니다.
2) 파일럿의 효과크기는 표본오차가 커서 **낙관적으로 치우칩니다**. 그래서 관측
   효과 기준 N 과 함께, 효과크기 신뢰구간의 **0 쪽 경계**(보수적 경계)로 계산한
   N 을 같이 보고합니다. 실제 설계는 보수적 N 쪽에 가깝게 잡는 것이 안전합니다.
3) 여기 나오는 N 은 **t 검정 기준**입니다. hrvkit 이 실제로 쓰는 검정은
   Wilcoxon/Mann–Whitney(순위 기반)이므로, 정규분포 하에서의 점근상대효율
   ARE = 3/π ≈ 0.955 를 반영해 약 +4.7% 를 더한 N 도 함께 냅니다. 분포가 두껍거나
   치우친 경우(HRV 의 HF power 등) 순위검정이 오히려 **더** 효율적이라 이 보정은
   보수적인 쪽입니다.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from .stats import student_t_ppf

__all__ = [
    "NONPARAM_ARE", "MAX_N", "noncentral_t_cdf", "t_test_power",
    "required_n", "detectable_delta", "inflate_for_dropout", "min_exact_n",
    "plan_paired", "plan_parallel", "power_grid",
]

# Wilcoxon 부호순위 / Mann–Whitney 의 t 검정 대비 점근상대효율(정규분포 가정).
# Pitman ARE = 3/π. 필요한 N 은 1/ARE 배 ≈ 1.047 배.
NONPARAM_ARE = 3.0 / math.pi

# 표본수 탐색 상한. 이보다 커지면 "설계 불가(효과가 너무 작음)"로 봅니다.
MAX_N = 100_000

# Simpson 적분 구간 수(짝수). 4000 이면 비중심 t CDF 오차가 1e-10 수준입니다.
_NODES = 4000


def _phi(x: float) -> float:
    """표준정규 CDF — erfc 기반이라 꼬리에서도 정확합니다."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _log_pdf_u(u: float, df: float) -> float:
    """U = √(V/df), V~χ²_df 의 로그밀도.

    f_U(u) = 2·df^(df/2)·u^(df−1)·exp(−df·u²/2) / (2^(df/2)·Γ(df/2))
    """
    return (math.log(2.0)
            + (df / 2.0) * math.log(df)
            + (df - 1.0) * math.log(u)
            - df * u * u / 2.0
            - (df / 2.0) * math.log(2.0)
            - math.lgamma(df / 2.0))


def _pdf_u(u: float, df: float) -> float:
    """U 의 밀도. u=0 은 극한값으로 처리합니다 (df=1 이면 √(2/π), df>1 이면 0)."""
    if u <= 0.0:
        if df < 1.0:
            return float("inf")     # df<1 은 여기서 발생하지 않지만 명시적으로
        return math.sqrt(2.0 / math.pi) if df == 1.0 else 0.0
    lp = _log_pdf_u(u, df)
    return math.exp(lp) if lp > -700.0 else 0.0


def _u_range(df: float) -> tuple:
    """적분 구간 [lo, hi] — U 의 확률질량이 사실상 전부 들어가도록 잡습니다.

    U 는 1 근처에 sd ≈ 1/√(2df) 로 몰립니다. df 가 커지면 스파이크가 아주
    좁아지므로 구간도 같이 좁혀야 Simpson 격자가 봉우리를 충분히 표본합니다
    (고정 구간이면 df=2e5 에서 봉우리당 격자점이 몇 개뿐이 됩니다).
    작은 df 에서는 꼬리가 길어 넉넉히 잡습니다(df=1 이면 U=|Z|).
    """
    half = 14.0 / math.sqrt(2.0 * df) + 10.0 / df
    return max(0.0, 1.0 - half), 1.0 + half


# Φ 가 사실상 0→1 로 바뀌는 표준정규 구간의 반폭. |z| > 8.5 이면 Φ 는
# 배정밀도에서 0 또는 1 과 구분되지 않습니다.
_PHI_HALFWIDTH = 8.5


def _simpson(t: float, df: float, ncp: float, a: float, b: float,
             n: int) -> float:
    """[a, b] 에서 f_U(u)·Φ(t·u − ncp) 의 Simpson 적분."""
    if b <= a:
        return 0.0
    if n % 2:
        n += 1
    h = (b - a) / n
    total = 0.0
    for i in range(n + 1):
        u = a + i * h
        dens = _pdf_u(u, df)
        if dens == 0.0:
            continue
        w = 1.0 if i in (0, n) else (4.0 if i % 2 == 1 else 2.0)
        total += w * dens * _phi(t * u - ncp)
    return total * h / 3.0


def noncentral_t_cdf(t: float, df: float, ncp: float,
                     nodes: int = _NODES) -> float:
    """비중심 t 분포의 CDF  P(T' ≤ t) — 구간 분할 Simpson 수치적분.

    ncp=0 이면 중심 t 분포 CDF 와 일치해야 합니다(테스트에서 대조합니다).

    **왜 구간을 쪼개나**: 피적분함수는 밀도 f_U(u)(폭 ~1/√(2df))와 계단 모양의
    Φ(t·u − ncp)(폭 ~1/|t|)의 곱입니다. 두 폭은 서로 무관하게 달라지므로
    하나의 균일 격자로는 |t| 가 큰 경우 계단을 통째로 건너뛰어 결과가 크게
    틀어집니다(고정 격자에서 P(T'≤1000; df=1)=0.99931, 참값 0.99968).
    Φ 의 전이 구간 u ∈ [(ncp∓8.5)/t] 을 경계로 [앞 / 전이 / 뒤] 세 구간을
    각각 nodes 점으로 적분하면, 두 스케일이 아무리 달라도 계단이 항상
    충분히 표본됩니다 — 평가 횟수는 3·nodes 로 고정입니다.
    """
    if df <= 0:
        raise ValueError("df 는 양수여야 합니다.")
    if not math.isfinite(t):
        return 1.0 if t > 0 else 0.0
    lo, hi = _u_range(df)
    n = nodes if nodes % 2 == 0 else nodes + 1
    if t == 0.0:
        return min(1.0, max(0.0, _simpson(t, df, ncp, lo, hi, n)))
    # Φ(t·u − ncp) 의 전이 구간(u 기준). t<0 이면 순서가 뒤집힙니다.
    e0 = (ncp - _PHI_HALFWIDTH) / t
    e1 = (ncp + _PHI_HALFWIDTH) / t
    a, b = (e0, e1) if e0 <= e1 else (e1, e0)
    a = min(max(a, lo), hi)
    b = min(max(b, lo), hi)
    total = (_simpson(t, df, ncp, lo, a, n)
             + _simpson(t, df, ncp, a, b, n)
             + _simpson(t, df, ncp, b, hi, n))
    return min(1.0, max(0.0, total))


def _df_ncp(d: float, n: float, design: str) -> tuple:
    """설계별 (자유도, 비중심모수)."""
    if design == "paired":
        return n - 1.0, d * math.sqrt(n)
    if design == "parallel":
        return 2.0 * n - 2.0, d * math.sqrt(n / 2.0)
    raise ValueError(f"design 은 'paired' 또는 'parallel' 이어야 합니다 (받은 값: {design!r})")


def t_test_power(d: float, n: int, *, design: str = "paired",
                 alpha: float = 0.05) -> float:
    """효과크기 d, 표본수 n 에서의 **양측** t 검정 검정력.

    design="paired"   : n = 피험자 수, d = Cohen's dz (평균차 / 차이의 SD)
    design="parallel" : n = **군당** 피험자 수, d = Cohen's d (평균차 / 합동 SD)

    d 의 부호는 검정력에 영향이 없으므로 절댓값을 씁니다.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha 는 0과 1 사이여야 합니다.")
    if n < 2:
        raise ValueError("n 은 2 이상이어야 합니다.")
    if not math.isfinite(d):
        return float("nan")
    df, ncp = _df_ncp(abs(float(d)), float(n), design)
    if df <= 0:
        return float("nan")
    tcrit = student_t_ppf(1.0 - alpha / 2.0, df)
    # 양측: 위쪽 기각역 + 아래쪽 기각역(효과 방향과 반대 — 작지만 정확히 포함).
    upper = 1.0 - noncentral_t_cdf(tcrit, df, ncp)
    lower = noncentral_t_cdf(-tcrit, df, ncp)
    return min(1.0, max(0.0, upper + lower))


def required_n(d: float, *, target_power: float = 0.80,
               design: str = "paired", alpha: float = 0.05,
               max_n: int = MAX_N) -> Optional[int]:
    """목표 검정력을 달성하는 최소 표본수 (parallel 이면 **군당**).

    검정력은 n 에 대해 단조증가하므로 이분 탐색으로 정확한 최솟값을 찾습니다.
    효과가 0/NaN 이거나 max_n 으로도 목표에 못 미치면 None 을 반환합니다.
    """
    if not (0.0 < target_power < 1.0):
        raise ValueError("target_power 는 0과 1 사이여야 합니다.")
    if not math.isfinite(d) or d == 0.0:
        return None
    if max_n < 2:
        return None
    lo, hi = 2, 2
    # 지수 확장으로 목표를 넘기는 n 을 먼저 찾습니다.
    while t_test_power(d, hi, design=design, alpha=alpha) < target_power:
        lo = hi
        hi *= 2
        if hi > max_n:
            if t_test_power(d, max_n, design=design, alpha=alpha) < target_power:
                return None
            hi = max_n
            break
    while lo < hi:
        mid = (lo + hi) // 2
        if mid < 2:
            mid = 2
        if t_test_power(d, mid, design=design, alpha=alpha) >= target_power:
            hi = mid
        else:
            lo = mid + 1
    return lo


def detectable_delta(n: int, *, target_power: float = 0.80,
                     design: str = "paired", alpha: float = 0.05,
                     sd: float = 1.0, tol: float = 1e-6) -> float:
    """주어진 n 에서 목표 검정력으로 **탐지 가능한 최소 효과**(MDE).

    sd 를 주면 원 단위(ms 등)의 최소 탐지 가능 차이(MDD)로 환산해 돌려줍니다.
    검정력이 d 에 대해 단조증가하는 성질을 이용한 이분 탐색입니다.
    """
    if n < 2:
        raise ValueError("n 은 2 이상이어야 합니다.")
    if not (0.0 < target_power < 1.0):
        raise ValueError("target_power 는 0과 1 사이여야 합니다.")
    # 목표가 α 이하면 d=0 에서 이미 달성되므로 "탐지 가능한 최소 차이"는 0 입니다.
    if target_power <= alpha:
        return 0.0
    lo, hi = 0.0, 1.0
    while t_test_power(hi, n, design=design, alpha=alpha) < target_power:
        if hi > 1e4:               # 사실상 도달 불가(n=2 등) — 배가 **후**에 검사
            return float("nan")
        hi *= 2.0
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if t_test_power(mid, n, design=design, alpha=alpha) >= target_power:
            hi = mid
        else:
            lo = mid
    return hi * abs(sd)


def inflate_for_dropout(n: Optional[int], dropout: float) -> Optional[int]:
    """탈락률을 반영해 **모집해야 할** 인원으로 올림.  n_enrol = ⌈n/(1−dropout)⌉."""
    if n is None:
        return None
    if not (0.0 <= dropout < 1.0):
        raise ValueError("dropout 은 0 이상 1 미만이어야 합니다.")
    if dropout == 0.0:
        return n
    return int(math.ceil(n / (1.0 - dropout)))


def _nonparam_n(n: Optional[int]) -> Optional[int]:
    """t 검정 기준 N 을 순위검정(Wilcoxon/Mann–Whitney) 기준으로 환산."""
    if n is None:
        return None
    return int(math.ceil(n / NONPARAM_ARE))


def min_exact_n(alpha: float = 0.05, design: str = "paired",
                max_n: int = 200) -> Optional[int]:
    """정확검정이 α 에서 **애초에 기각할 수 있는** 최소 표본수.

    왜 필요한가: 순위검정의 정확 영분포는 이산적이라, 표본이 작으면 **효과가
    아무리 커도** 달성 가능한 최소 p 가 α 를 넘습니다. 짝지은 부호순위는 모든
    피험자가 같은 방향이어도 양측 p ≥ 2^(1−n) 이라 α=0.05 에서 n≤5 는 기각이
    불가능하고, Mann–Whitney 는 두 군이 완전히 분리돼도 p = 2/C(2n,n) 이라
    군당 3명 이하는 불가능합니다.

    비중심 t 로 계산한 N 이 이 하한보다 작게 나오면 "검정력 80%" 라는 답이
    거짓이 됩니다(그 설계로는 어떤 결과가 나와도 유의할 수 없으므로 실제
    검정력은 0). 그래서 계획 결과를 이 값으로 바닥칩니다.

    달성 가능한 최소 양측 p 는 닫힌 형태로 나옵니다 — 영분포 전체를 전개할
    필요가 없습니다(전개는 n 이 커지면 O(n⁴) 라 실용적이지 않습니다):

      부호순위   : 2^(1−n)      — 부호 배열 2ⁿ 가지 중 한쪽 극단이 1가지, 양측이니 ×2
      Mann–Whitney: 2 / C(2n,n) — 순위 배열 C(2n,n) 가지 중 완전분리가 1가지

    이 두 식이 hrvkit 의 정확 영분포 구현(stats._exact_two_sided_p /
    _exact_mw_two_sided_p)과 실제로 일치하는지는 테스트에서 대조합니다.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha 는 0과 1 사이여야 합니다.")
    if design not in ("paired", "parallel"):
        raise ValueError(
            f"design 은 'paired' 또는 'parallel' 이어야 합니다 (받은 값: {design!r})")
    for n in range(2, max_n + 1):
        if design == "paired":
            p = 2.0 ** (1 - n)
        else:
            p = 2.0 / math.comb(2 * n, n)
        if p <= alpha:
            return n
    return None


def hedges_j(df: float) -> float:
    """Hedges 의 소표본 편의 보정 계수  J(df) = Γ(df/2) / (√(df/2)·Γ((df−1)/2)).

    표본 효과크기 d 는 모집단 δ 를 **과대추정**합니다(E[d] = δ/J, J < 1).
    표본수 계획은 δ 의 덜 치우친 추정치로 해야 하므로 관측 d 에 J 를 곱합니다.
    df 는 SD 를 추정한 자유도입니다 — 짝지은/일표본은 n−1, 평행군은 na+nb−2.

    lgamma 로 계산해 df 가 커도 오버플로하지 않습니다(J → 1).
    """
    if not math.isfinite(df) or df < 2.0:
        return float("nan")
    return math.exp(math.lgamma(df / 2.0) - math.lgamma((df - 1.0) / 2.0)) \
        / math.sqrt(df / 2.0)


def _pack(d: float, *, design: str, target_power: float, alpha: float,
          dropout: float, sd: float) -> Dict[str, object]:
    """효과크기 하나에 대한 N 묶음 (t 기준·순위검정 보정·정확검정 하한·탈락 반영).

    n_recommended 가 최종 권고값입니다 — 순위검정 보정 N 과 정확검정 하한
    중 큰 쪽. floored=True 면 하한 때문에 올라간 것이라 "그 효과크기에서는
    검정력이 남아돌지만 검정이 기각할 수 있으려면 이만큼 필요하다"는 뜻입니다.
    """
    n_t = required_n(d, target_power=target_power, design=design, alpha=alpha)
    n_np = _nonparam_n(n_t)
    n_floor = min_exact_n(alpha=alpha, design=design)
    n_rec = n_np
    floored = False
    if n_np is not None and n_floor is not None and n_np < n_floor:
        n_rec = n_floor
        floored = True
    return {
        "d": d,
        "n_t": n_t,
        "n_nonparam": n_np,
        "n_exact_floor": n_floor,
        "n_recommended": n_rec,
        "floored": floored,
        "n_enrol": inflate_for_dropout(n_rec, dropout),
        "delta": (d * sd) if (math.isfinite(d) and math.isfinite(sd)) else float("nan"),
    }


def plan_paired(summary: Dict[str, float], *, target_power: float = 0.80,
                alpha: float = 0.05, dropout: float = 0.0) -> Dict[str, object]:
    """짝지은(pre–post) 파일럿 요약 한 개로부터 본시험 표본수를 계획.

    summary 는 stats.paired_summary 의 반환값 (n, mean_diff, sd_diff 사용).

    반환 키:
      n_pilot, mean_diff, sd_diff, sd_used
      observed      : 관측 효과크기 기준 {d, n_t, n_nonparam, n_enrol, delta}
      conservative  : 평균차 신뢰구간의 **0 쪽 경계** 기준 (동일 구조).
                      CI 가 0 을 포함하면 None — "이 파일럿만으로는 보수적
                      설계가 불가능"이라는 뜻입니다.
      target_power, alpha, dropout, design
    """
    n = int(summary.get("n", 0) or 0)
    md = summary.get("mean_diff", float("nan"))
    sd = summary.get("sd_diff", float("nan"))
    out: Dict[str, object] = {
        "design": "paired", "n_pilot": n,
        "mean_diff": md, "sd_diff": sd, "sd_used": sd,
        "target_power": target_power, "alpha": alpha, "dropout": dropout,
        "observed": None, "conservative": None,
    }
    if n < 3:
        # n<3 이면 SD 의 자유도가 1 이하라 효과크기 추정이 의미가 없습니다.
        out["note"] = f"파일럿의 유효 짝이 {n}개뿐이라(n<3) 효과크기를 추정할 수 없음."
        return out
    if not _ok(md) or not _ok(sd):
        out["note"] = "평균차 또는 차이의 SD 가 유한하지 않음(그 지표가 NaN 인 기록이 섞임)."
        return out
    if sd <= 0.0:
        # 원인이 표본 크기가 아니라 분산 0 인데 "표본이 작다"고 하면 오진입니다.
        out["note"] = ("피험자 간 차이의 분산이 0 — 모든 피험자의 변화량이 동일해 "
                       "효과크기(평균차/SD)가 정의되지 않습니다. 자료 중복 여부를 확인하세요.")
        return out
    # 관측 dz 는 모집단 효과크기를 과대추정하므로 Hedges J(df=n−1) 로 보정합니다
    # — plan_parallel 이 Cohen's d 대신 Hedges g 를 쓰는 것과 같은 이유입니다.
    # 보정하지 않으면 짝지은 시험의 N 이 **작게** 나오는데, 이것이 바로 "파일럿
    # 효과는 낙관적으로 치우친다"는 이 모듈의 경고가 막으려는 실패 방식입니다
    # (n_pilot=5, dz=0.8 이면 보정 없이 15명, 보정하면 22명).
    j = hedges_j(n - 1)
    out["hedges_j"] = j
    d_obs = (md / sd) * j
    out["dz_uncorrected"] = md / sd
    out["observed"] = _pack(d_obs, design="paired", target_power=target_power,
                            alpha=alpha, dropout=dropout, sd=sd)
    sem = sd / math.sqrt(n)
    tcrit = student_t_ppf(1.0 - alpha / 2.0, n - 1)
    lo, hi = md - tcrit * sem, md + tcrit * sem
    bound = _toward_zero(lo, hi)
    out["ci_low"], out["ci_high"] = lo, hi
    if bound is None:
        out["note"] = ("평균차의 신뢰구간이 0 을 포함해 보수적 표본수를 낼 수 "
                       "없습니다 — 파일럿이 효과의 방향조차 확정하지 못했습니다.")
    else:
        con = _pack(bound / sd * j, design="paired",
                    target_power=target_power, alpha=alpha,
                    dropout=dropout, sd=sd)
        # delta 는 "이 N 이 탐지하는 원 단위 차이" 여야 하므로 J 로 축소되기 전의
        # 신뢰한계를 그대로 씁니다(J 는 효과크기 척도 보정이지 차이 자체가 아님).
        con["delta"] = bound
        out["conservative"] = con
    return out


def plan_parallel(summary: Dict[str, float], *, target_power: float = 0.80,
                  alpha: float = 0.05, dropout: float = 0.0) -> Dict[str, object]:
    """평행군(독립 2군) 파일럿 요약으로부터 **군당** 표본수를 계획.

    summary 는 stats.unpaired_summary 의 반환값
    (n_a, n_b, mean_diff, sd_pooled, hedges_g 사용).

    관측 효과크기로는 Cohen's d 가 아니라 **Hedges g**(소표본 편의 보정)를
    씁니다 — 계획에 필요한 것은 모집단 효과크기의 덜 치우친 추정치입니다.
    """
    na = int(summary.get("n_a", 0) or 0)
    nb = int(summary.get("n_b", 0) or 0)
    md = summary.get("mean_diff", float("nan"))
    sp = summary.get("sd_pooled", float("nan"))
    g = summary.get("hedges_g", float("nan"))
    out: Dict[str, object] = {
        "design": "parallel", "n_pilot_a": na, "n_pilot_b": nb,
        "mean_diff": md, "sd_pooled": sp, "sd_used": sp,
        "target_power": target_power, "alpha": alpha, "dropout": dropout,
        "observed": None, "conservative": None,
    }
    if na < 2 or nb < 2:
        out["note"] = f"군별 유효 표본이 {na}/{nb} 로 너무 작음(각 군 2명 이상 필요)."
        return out
    if _ok(sp) and sp <= 0.0:
        # 원인이 표본 크기가 아니라 분산 0 인데 "표본이 작다"고 하면 오진입니다.
        out["note"] = ("군 내 분산이 0 — 모든 값이 동일해 효과크기가 정의되지 "
                       "않습니다. 자료 중복 여부를 확인하세요.")
        return out
    if not _ok(md) or not _ok(sp) or not _ok(g):
        out["note"] = "군간 평균차 또는 합동 SD 가 유한하지 않음(그 지표가 NaN 인 기록이 섞임)."
        return out
    out["observed"] = _pack(g, design="parallel", target_power=target_power,
                            alpha=alpha, dropout=dropout, sd=sp)
    se = sp * math.sqrt(1.0 / na + 1.0 / nb)
    tcrit = student_t_ppf(1.0 - alpha / 2.0, na + nb - 2)
    lo, hi = md - tcrit * se, md + tcrit * se
    bound = _toward_zero(lo, hi)
    out["ci_low"], out["ci_high"] = lo, hi
    if bound is None:
        out["note"] = ("군간 평균차의 신뢰구간이 0 을 포함해 보수적 표본수를 낼 수 "
                       "없습니다 — 파일럿이 효과의 방향조차 확정하지 못했습니다.")
    else:
        # 보수적 경계도 같은 J 보정을 적용해 관측 효과와 척도를 맞춥니다.
        # J 는 관측 g/d 의 비로 되짚지 않고 자유도에서 직접 계산합니다 —
        # cohens_d 가 없거나 0 이면 비로는 조용히 1.0(보정 없음)이 되어
        # 보수적 N 이 도리어 작게 나옵니다.
        j = hedges_j(na + nb - 2)
        if not _ok(j):
            j = 1.0
        con = _pack(bound / sp * j, design="parallel",
                    target_power=target_power, alpha=alpha,
                    dropout=dropout, sd=sp)
        con["delta"] = bound          # 원 단위 차이는 J 보정 전의 신뢰한계
        out["conservative"] = con
    return out


def power_grid(*, delta: Optional[float] = None, sd: float = 1.0,
               n: Optional[int] = None, design: str = "paired",
               alpha: float = 0.05, dropout: float = 0.0,
               target_power: Optional[float] = None,
               powers: Optional[List[float]] = None) -> Dict[str, object]:
    """파일럿 없이 가정값만으로 계획 — --plan 모드의 계산 본체.

    delta+sd 를 주면 목표 검정력별 **필요 N** 표를,
    n+sd 를 주면 그 N 에서 **탐지 가능한 최소 차이(MDD)** 표를 만듭니다.
    둘 다 주면 두 표에 더해 그 조합의 실제 검정력을 냅니다.
    """
    if powers is None:
        powers = [0.80, 0.85, 0.90, 0.95]
        # --target-power 를 무시하면 사용자가 요청한 검정력이 표에 아예 없을 수
        # 있습니다(예: 0.99). 표준 격자에 합쳐 넣고 어느 행이 요청값인지 표시합니다.
        if target_power is not None:
            if not (0.0 < target_power < 1.0):
                raise ValueError("target_power 는 0과 1 사이여야 합니다.")
            powers = sorted(set(powers) | {round(target_power, 10)})
    if not _ok(sd) or sd <= 0.0:
        raise ValueError("--sd 는 0보다 큰 유한한 값이어야 합니다.")
    if delta is None and n is None:
        raise ValueError("--plan 에는 (--delta 와 --sd) 또는 (--plan-n 과 --sd) 가 필요합니다.")
    out: Dict[str, object] = {
        "design": design, "alpha": alpha, "sd": sd, "delta": delta,
        "n": n, "dropout": dropout, "rows": [],
    }
    d = (abs(delta) / sd) if delta is not None else None
    if d is not None and d == 0.0:
        raise ValueError("--delta 는 0이 아니어야 합니다 (효과가 0이면 필요 표본수가 무한).")
    out["requested_power"] = target_power
    for p in powers:
        row: Dict[str, object] = {"target_power": p,
                                  "requested": (target_power is not None and
                                                abs(p - target_power) < 1e-12)}
        if d is not None:
            row.update(_pack(d, design=design, target_power=p, alpha=alpha,
                             dropout=dropout, sd=sd))
        if n is not None:
            mdd = detectable_delta(n, target_power=p, design=design,
                                   alpha=alpha, sd=sd)
            row["mdd"] = mdd
            # 순위검정 기준 MDD. 검정력은 n·d² 로 결정되므로 효율이 ARE 배면
            # 같은 n 에서 탐지 가능한 d 는 1/√ARE 배로 커집니다(약 +2.3%).
            row["mdd_nonparam"] = mdd / math.sqrt(NONPARAM_ARE)
        out["rows"].append(row)
    if d is not None and n is not None:
        out["power_at_n"] = t_test_power(d, n, design=design, alpha=alpha)
    return out


def _ok(x) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(xf)


def _toward_zero(lo: float, hi: float) -> Optional[float]:
    """신뢰구간 [lo, hi] 에서 0 에 가까운 쪽 경계. 0 을 포함하면 None."""
    if lo > 0.0:
        return lo
    if hi < 0.0:
        return hi
    return None
