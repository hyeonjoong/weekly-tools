"""가벼운 통계 — 짝지은(paired) 코호트 검정. 표준 라이브러리만.

장치 검증에서 여러 피험자의 (기저, 개입) 짝을 모아 지표별로 개입 효과를
정량화합니다. HRV 지표는 정규성이 약한 경우가 많아 비모수 **Wilcoxon 부호순위
검정**을 기본 유의성 지표로 제공합니다.

정확 검정(exact test)이 기본인 이유
-----------------------------------
디바이스 검증 코호트는 실제로 n=8~20 인 경우가 대부분인데, 이 영역에서 정규
근사 p값은 참값과 크게 어긋납니다(예: n=8 전부 같은 방향 → 정확 p=0.0078,
정규 근사 p=0.0143 — 1.8배 보수적). 따라서 |차이|에 동점이 없고 n≤25 이면
**부호순위합의 정확 분포**(부분집합 합 DP)로 p값을 계산하고, 동점이 있거나
n이 크면 동점·연속성 보정 정규 근사로 자동 전환합니다(`method="auto"`).

효과 추정과 다중비교
--------------------
- **Hodges–Lehmann 추정량**(Walsh 평균의 중앙값)과 그 **분포무관 신뢰구간**을
  제공합니다. Wilcoxon 검정과 쌍대(duality)를 이루는 구간이라 "p<0.05" 와
  "CI가 0을 포함하지 않음"이 정확히 일치합니다(테스트로 고정).
- 지표를 여러 개 동시에 검정하므로 **Holm–Bonferroni**(FWER)와
  **Benjamini–Hochberg**(FDR) 보정 p값을 함께 냅니다.

외부 의존성 없음 — 정규 근사 p값은 math.erf 로 계산합니다.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional, Sequence

__all__ = ["normal_cdf", "wilcoxon_signed_rank", "paired_summary",
           "signed_rank_null_counts", "walsh_averages", "hodges_lehmann",
           "wilcoxon_ci", "holm_adjust", "benjamini_hochberg",
           "mannwhitney_null_counts", "mann_whitney_u", "pairwise_differences",
           "hodges_lehmann_2sample", "mann_whitney_ci", "unpaired_summary",
           "inversion_counts", "mann_kendall", "betainc", "student_t_cdf",
           "student_t_sf2", "student_t_ppf", "pearson_r", "ols_slope_test"]

# 정확 검정을 쓰는 최대 표본 수. n=25 → 2^25 경우의 수를 DP로 세지만 상태
# 공간은 n(n+1)/2+1 = 326 개뿐이라 즉시 계산됩니다.
EXACT_MAX_N = 25

# 두 표본(Mann–Whitney) 정확 분포를 쓰는 최대 **군당** 표본 수. 상태 공간은
# m·n+1 개이고 DP는 O(m²n²) 이라 m=n=30 이면 810k 연산 — 즉시 끝납니다.
EXACT_MAX_N_2SAMPLE = 30

# Mann–Kendall(추세) 정확 분포를 쓰는 최대 표본 수. 상태 공간은 역위 수
# 0..n(n-1)/2 이고 DP는 O(n³) — n=25 면 ~16k 연산.
EXACT_MAX_N_TREND = 25


def normal_cdf(z: float) -> float:
    """표준정규 누적분포 Φ(z)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _average_ranks(values: Sequence[float]) -> List[float]:
    """동점은 평균 순위를 부여한 1-기반 순위 리스트."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-기반 평균 순위
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# --------------------------------------------------------------------------- #
# 정확 분포 (exact null distribution of W+)
# --------------------------------------------------------------------------- #
def signed_rank_null_counts(n: int) -> List[int]:
    """H0 하에서 W+ = w 가 되는 부호 배정의 수를 w=0..n(n+1)/2 로 반환.

    각 순위 i(1..n)의 부호가 독립·등확률 ±이므로, W+ 는 {1..n} 의 부분집합 합.
    counts[w] = (합이 w 인 부분집합의 수). 전체 합 = 2^n.
    생성함수 Π(1 + x^i) 를 부분집합-합 DP로 전개합니다.
    """
    if n < 0:
        raise ValueError("n은 음수일 수 없습니다.")
    total = n * (n + 1) // 2
    counts = [0] * (total + 1)
    counts[0] = 1
    upto = 0
    for i in range(1, n + 1):
        upto += i
        # 내림차순으로 갱신해 같은 i를 두 번 쓰지 않도록 (0/1 배낭).
        for w in range(upto, i - 1, -1):
            counts[w] += counts[w - i]
    return counts


def _exact_two_sided_p(w_plus: float, n: int) -> float:
    """정확 부호순위 검정의 양측 p값.

    p = 2·min(P(W+ ≤ w), P(W+ ≥ w)) 를 1로 절단(관례적 정의).
    """
    counts = signed_rank_null_counts(n)
    total = float(sum(counts))
    w = int(round(w_plus))
    lower = sum(counts[: w + 1]) / total
    upper = sum(counts[w:]) / total
    return min(1.0, 2.0 * min(lower, upper))


def _has_ties(absd: Sequence[float]) -> bool:
    return len(set(absd)) != len(absd)


def wilcoxon_signed_rank(diffs: Sequence[float],
                         method: str = "auto") -> Dict[str, float]:
    """Wilcoxon 부호순위 검정.

    method:
      "auto"   — |차이|에 동점이 없고 0이 아닌 차이 수 n ≤ EXACT_MAX_N(25) 이면
                 정확 분포, 아니면 정규 근사. (기본)
      "exact"  — 항상 정확 분포. 동점이 있으면 ValueError(정확 분포가 정의되지
                 않음), n이 크면 계산량 경고 없이 그대로 수행.
      "approx" — 항상 정규 근사(연속성·동점 보정).

    0 차이는 제외(Wilcoxon 방식). 반환 키:
      n_pairs      : 0이 아닌 차이 수(검정에 사용된 표본 수)
      w_plus       : 양의 차이 순위합 (검정통계량)
      z            : 정규 근사 z (연속성 보정; 정확 검정에서도 참고용으로 제공)
      p_value      : 양측 p값
      method       : 실제로 사용된 방법 ("exact" | "approx")
    표본이 없거나(모두 0) 분산이 0이면 z=0, p=1.
    """
    if method not in ("auto", "exact", "approx"):
        raise ValueError(f"알 수 없는 method: {method!r} (auto/exact/approx)")

    nz = [d for d in diffs if d != 0.0]
    n = len(nz)
    out: Dict[str, float] = {
        "n_pairs": n, "w_plus": float("nan"), "z": float("nan"),
        "p_value": float("nan"), "method": "approx",
    }
    if n == 0:
        out.update({"w_plus": 0.0, "z": 0.0, "p_value": 1.0})
        return out

    absd = [abs(d) for d in nz]
    ranks = _average_ranks(absd)
    w_plus = sum(r for r, d in zip(ranks, nz) if d > 0)
    out["w_plus"] = w_plus

    ties = _has_ties(absd)
    if method == "exact" and ties:
        raise ValueError(
            "정확 부호순위 검정은 |차이|에 동점이 없어야 합니다 "
            "(method='auto' 를 쓰면 자동으로 정규 근사로 전환됩니다).")
    use_exact = (method == "exact") or (
        method == "auto" and not ties and n <= EXACT_MAX_N)

    # z는 두 경로 모두에서 참고 지표로 계산(정규 근사 p는 아래에서만 사용).
    mean_w = n * (n + 1) / 4.0
    tie_term = 0.0
    counts: Dict[float, int] = {}
    for a in absd:
        counts[a] = counts.get(a, 0) + 1
    for t in counts.values():
        if t > 1:
            tie_term += t ** 3 - t
    var_w = (n * (n + 1) * (2 * n + 1) - tie_term / 2.0) / 24.0
    if var_w <= 0:
        out.update({"z": 0.0, "p_value": 1.0,
                    "method": "exact" if use_exact else "approx"})
        return out

    num = w_plus - mean_w
    cc = num - math.copysign(0.5, num) if num != 0 else 0.0  # 연속성 보정
    z = cc / math.sqrt(var_w)
    out["z"] = z

    if use_exact:
        out["p_value"] = _exact_two_sided_p(w_plus, n)
        out["method"] = "exact"
    else:
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
        out["p_value"] = min(1.0, max(0.0, p))
        out["method"] = "approx"
    return out


# --------------------------------------------------------------------------- #
# Hodges–Lehmann 추정량 + 분포무관 신뢰구간
# --------------------------------------------------------------------------- #
def walsh_averages(diffs: Sequence[float]) -> List[float]:
    """모든 Walsh 평균 (d_i + d_j)/2, i ≤ j 를 정렬해 반환 (개수 = n(n+1)/2)."""
    d = [float(x) for x in diffs]
    n = len(d)
    out: List[float] = []
    for i in range(n):
        for j in range(i, n):
            out.append((d[i] + d[j]) / 2.0)
    out.sort()
    return out


def hodges_lehmann(diffs: Sequence[float]) -> float:
    """Hodges–Lehmann 추정량 = Walsh 평균의 중앙값 (짝지은 차이의 pseudomedian).

    평균차보다 이상값에 강건하고, Wilcoxon 검정과 짝을 이루는 위치 추정량입니다.
    차이가 없으면 NaN.
    """
    w = walsh_averages(diffs)
    if not w:
        return float("nan")
    return statistics.median(w)


def wilcoxon_ci(diffs: Sequence[float], alpha: float = 0.05) -> Dict[str, float]:
    """Hodges–Lehmann 추정량의 분포무관 (1-alpha) 신뢰구간.

    정확 부호순위 분포에서 각 끝을 k개씩 잘라내는 고전적 구성
    (Hollander & Wolfe): 정렬된 Walsh 평균 W_(1..M), M=n(n+1)/2 에 대해
        k = max{ k ≥ 0 : P(W+ ≤ k-1) ≤ alpha/2 }
        CI = [ W_(k+1), W_(M-k) ]
    이 구간은 정확 양측 검정과 **쌍대**입니다 — 즉 "CI가 μ0를 포함하지 않음"
    ⇔ "H0: pseudomedian=μ0 를 정확 검정이 alpha에서 기각". 테스트로 고정합니다.

    주의: 이 구성은 |차이|에 동점이 없다는 정확 분포 가정 위에 있습니다.
    동점/영차이가 있으면 구간은 보수적(coverage ≥ 1-alpha)이 됩니다.
    n이 EXACT_MAX_N을 넘으면 정규 근사로 k를 정합니다.

    영차이(0) 처리: Wilcoxon 검정과 **동일하게** 0인 차이를 제외하고 구성합니다.
    따라서 함께 반환하는 hl_shift 도 같은 (0 제외) 표본에서 계산해, 점추정·구간·p값이
    항상 같은 것을 가리키게 합니다. 전역 hodges_lehmann() 은 교과서 정의대로 모든
    차이를 쓰므로 영차이가 있으면 이 값과 다를 수 있습니다(예: diffs=[0,0,0,5,6] →
    전역 HL=2.5, 여기 hl_shift=5.5, CI=[5,6]). 과거엔 paired_summary 가 전역 HL과
    이 CI를 나란히 실어, **점추정이 자기 신뢰구간 밖**에 놓이는 모순이 났습니다.

    반환 키: ci_low, ci_high, ci_alpha, ci_k, ci_method, n_pairs, hl_shift
    0이 아닌 차이가 없으면 전부 NaN.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha는 0과 1 사이여야 합니다.")
    nz = [float(d) for d in diffs if d != 0.0]
    n = len(nz)
    out: Dict[str, float] = {
        "ci_low": float("nan"), "ci_high": float("nan"), "ci_alpha": alpha,
        "ci_k": float("nan"), "ci_method": None, "n_pairs": n,
        "hl_shift": float("nan"),
    }
    if n == 0:
        return out

    walsh = walsh_averages(nz)
    m = len(walsh)
    # CI와 **같은 표본**에서 점추정을 내 항상 CI 안에 들도록 합니다.
    out["hl_shift"] = statistics.median(walsh)

    if n <= EXACT_MAX_N:
        counts = signed_rank_null_counts(n)
        total = float(sum(counts))
        # k = 각 끝에서 잘라낼 개수 = P(W+ ≤ k) < alpha/2 를 만족하는 **최대** k.
        #
        # 과거엔 조건이 `P(W+ ≤ k-1) ≤ alpha/2` 여서 k가 항상 1 커졌고, 그 결과
        # 구간이 좁아져 **명목 수준에 미달**했습니다(n=8·alpha=0.05 에서 실제 피복률
        # 0.9453 < 0.95; n=4~20 전 구간에서 미달). 또한 정확검정과의 쌍대성이 깨져
        # "p>0.05 인데 CI가 그 값을 배제" 하는 자기모순이 5.1% 의 격자점에서 났습니다.
        # (n>EXACT_MAX_N 의 정규 근사 분기는 처음부터 올바른 k를 계산하고 있어,
        #  n=25↔26 경계에서 피복률이 튀는 것이 단서였습니다.)
        cum = 0.0
        k = -1
        for w in range(len(counts)):
            cum += counts[w] / total          # 이 시점 cum = P(W+ ≤ w)
            if cum < alpha / 2.0:
                k = w
            else:
                break
        method = "exact"
    else:
        # 정규 근사: k ≈ n(n+1)/4 - z_{1-alpha/2}·sqrt(n(n+1)(2n+1)/24)
        z = _inv_normal_cdf(1.0 - alpha / 2.0)
        mean_w = n * (n + 1) / 4.0
        sd_w = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        k = int(math.floor(mean_w - z * sd_w))
        method = "approx"

    if k < 0 or k > (m - 1) // 2:
        # 이 n에서는 어떤 유한 구간도 1-alpha 를 담보할 수 없습니다
        # (예: n=4, alpha=0.05 — 정확검정이 낼 수 있는 최소 p가 2/2^4=0.125).
        # 전체 범위를 "95% 구간"이라 부르면 거짓이므로, 참인 답인 (-∞, ∞)를 내고
        # 표본 부족을 명시합니다. 이러면 쌍대성도 유지됩니다(검정도 절대 기각 못 함).
        out["ci_low"] = float("-inf")
        out["ci_high"] = float("inf")
        out["ci_k"] = float("nan")
        out["ci_method"] = "insufficient-n"
        return out
    out["ci_low"] = walsh[k]
    out["ci_high"] = walsh[m - 1 - k]
    out["ci_k"] = k
    out["ci_method"] = method
    return out


# --------------------------------------------------------------------------- #
# 분포 함수 — Student t (방법비교/Bland–Altman 신뢰구간용)
#
# 여기서만 모수적(정규 이론) 구간을 씁니다. 짝지은/독립 2군 검정은 비모수인데
# Bland–Altman 의 편의(bias) 신뢰구간과 비례편의 회귀 검정은 정의 자체가 정규
# 이론이라, 원 논문(Bland & Altman 1986/1999)과 같은 식을 그대로 씁니다.
# 표본이 작을 때 z 대신 t 를 쓰는 것이 원 논문 권고이자 실제로 중요합니다
# (n=10 이면 t=2.262 vs z=1.96 — 구간이 15% 좁게 나와 과신하게 됩니다).
# --------------------------------------------------------------------------- #
def _betacf(a: float, b: float, x: float) -> float:
    """정칙 불완전베타의 연분수 (Lentz 알고리즘, Numerical Recipes 형태)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """정칙 불완전베타 함수 I_x(a, b). a,b>0, 0≤x≤1."""
    if not (a > 0.0 and b > 0.0):
        raise ValueError("betainc: a와 b는 양수여야 합니다.")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """Student t 분포의 누적분포 P(T ≤ t)."""
    if df <= 0:
        raise ValueError("student_t_cdf: 자유도는 양수여야 합니다.")
    if t != t:
        return float("nan")
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    x = df / (df + t * t)
    tail = 0.5 * betainc(0.5 * df, 0.5, x)
    return tail if t <= 0.0 else 1.0 - tail


def student_t_sf2(t: float, df: float) -> float:
    """양측 p값 = P(|T| ≥ |t|)."""
    if t != t or df <= 0:
        return float("nan")
    return 2.0 * (1.0 - student_t_cdf(abs(t), df))


def student_t_ppf(p: float, df: float) -> float:
    """t 분위수 — cdf 를 이분법으로 역산 (표준 라이브러리만).

    구간을 정규 근사에서 출발해 넓혀 잡고 100회 이분하면 배정도 한계까지
    수렴합니다. scipy 없이도 t 임계값이 필요한 곳(Bland–Altman CI)에 씁니다.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("student_t_ppf: p는 (0,1) 이어야 합니다.")
    if df <= 0:
        raise ValueError("student_t_ppf: 자유도는 양수여야 합니다.")
    lo, hi = -1.0, 1.0
    for _ in range(200):
        if student_t_cdf(lo, df) < p:
            break
        lo *= 2.0
    for _ in range(200):
        if student_t_cdf(hi, df) > p:
            break
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if student_t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson 상관계수. 표준편차가 0이면 정의되지 않으므로 NaN."""
    n = len(x)
    if n != len(y) or n < 2:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0.0 or syy <= 0.0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def ols_slope_test(x: Sequence[float], y: Sequence[float]) -> Dict[str, float]:
    """y = a + b·x 최소제곱 적합과 기울기의 t 검정 (H0: b = 0).

    Bland–Altman 의 **비례편의(proportional bias)** 검정에 씁니다: 차이(y)를
    두 방법의 평균(x)에 회귀했을 때 기울기가 0과 다르면, 편의가 측정값 크기에
    따라 달라진다는 뜻이라 단일 LoA 로 요약하면 안 됩니다.

    반환: slope, intercept, se_slope, t, p, df, r2. n<3 이면 전부 NaN.
    """
    nan = float("nan")
    n = len(x)
    out = {"slope": nan, "intercept": nan, "se_slope": nan, "t": nan,
           "p": nan, "df": 0, "r2": nan}
    if n != len(y) or n < 3:
        return out
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0.0:            # x가 전부 같은 값 → 기울기 정의 불가
        return out
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [b - (intercept + slope * a) for a, b in zip(x, y)]
    sse = sum(r * r for r in resid)
    df = n - 2
    syy = sum((b - my) ** 2 for b in y)
    out["slope"] = slope
    out["intercept"] = intercept
    out["df"] = df
    out["r2"] = (1.0 - sse / syy) if syy > 0.0 else nan
    if sse <= 0.0:
        # 잔차가 0 — 완전 적합. 표준오차 0 은 t=±inf 를 뜻하므로 p=0 으로 두되
        # 기울기가 정확히 0 이면 검정할 것이 없습니다.
        out["se_slope"] = 0.0
        out["t"] = 0.0 if slope == 0.0 else math.copysign(float("inf"), slope)
        out["p"] = 1.0 if slope == 0.0 else 0.0
        return out
    se = math.sqrt((sse / df) / sxx)
    out["se_slope"] = se
    out["t"] = slope / se
    out["p"] = student_t_sf2(slope / se, df)
    return out


def _inv_normal_cdf(p: float) -> float:
    """Φ⁻¹(p) — Acklam 근사(정확도 ~1e-9), 표준 라이브러리만."""
    if not (0.0 < p < 1.0):
        raise ValueError("p는 (0,1) 이어야 합니다.")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q +
                c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q +
                 c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r +
            a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r +
                          b[4]) * r + 1)


# --------------------------------------------------------------------------- #
# 다중비교 보정
# --------------------------------------------------------------------------- #
def holm_adjust(pvals: Sequence[float]) -> List[float]:
    """Holm–Bonferroni 보정 p값 (FWER 통제). 입력 순서를 유지해 반환.

    p_(1)≤…≤p_(m) 에 대해 조정값 = max 누적 (m-i+1)·p_(i), 1로 절단.
    NaN 입력은 NaN 그대로 두고 검정 개수 m 에서 제외합니다.
    """
    idx = [i for i, p in enumerate(pvals) if _finite(p)]
    m = len(idx)
    out = [float("nan")] * len(pvals)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    running = 0.0
    for rank, i in enumerate(order):
        adj = (m - rank) * float(pvals[i])
        running = max(running, adj)          # 단조성 강제
        out[i] = min(1.0, running)
    return out


def benjamini_hochberg(pvals: Sequence[float]) -> List[float]:
    """Benjamini–Hochberg 보정 p값(q값, FDR 통제). 입력 순서를 유지해 반환.

    p_(1)≤…≤p_(m) 에 대해 q_(i) = min_{j≥i} min(1, m/j · p_(j)) (단조성 강제).
    NaN 입력은 NaN 그대로 두고 검정 개수 m 에서 제외합니다.
    """
    idx = [i for i, p in enumerate(pvals) if _finite(p)]
    m = len(idx)
    out = [float("nan")] * len(pvals)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    running = 1.0
    # 큰 p부터 거꾸로 올라가며 최소값 누적 → 단조 비감소 q값.
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        adj = m / (rank + 1) * float(pvals[i])
        running = min(running, adj)
        out[i] = min(1.0, running)
    return out


def _finite(x) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return xf == xf and not math.isinf(xf)


# --------------------------------------------------------------------------- #
# 지표 하나에 대한 코호트 요약
# --------------------------------------------------------------------------- #
def paired_summary(baseline: Sequence[float],
                   intervention: Sequence[float],
                   alpha: float = 0.05) -> Dict[str, float]:
    """한 지표에 대한 짝지은 코호트 요약.

    baseline[i], intervention[i] 는 같은 피험자 i의 값. 유한한 짝만 사용.
    반환 키:
      n            : 유효 짝 수
      mean_base, mean_interv, mean_diff, sd_diff, sem_diff
      cohens_dz    : 표준화 효과크기 = mean_diff / sd_diff
      median_diff
      hl_shift     : Hodges–Lehmann 위치 추정량(강건)
      ci_low, ci_high, ci_alpha, ci_method : HL의 분포무관 신뢰구간
      wilcoxon_z, wilcoxon_p, wilcoxon_method, w_plus, n_pairs
      n_increased  : 개입에서 값이 증가한 피험자 수
    """
    pairs = [(float(b), float(v)) for b, v in zip(baseline, intervention)
             if _finite(b) and _finite(v)]
    n = len(pairs)
    out: Dict[str, float] = {"n": n}
    if n == 0:
        return out
    diffs = [v - b for b, v in pairs]
    out["mean_base"] = statistics.fmean(b for b, _ in pairs)
    out["mean_interv"] = statistics.fmean(v for _, v in pairs)
    out["mean_diff"] = statistics.fmean(diffs)
    out["median_diff"] = statistics.median(diffs)
    sd = statistics.stdev(diffs) if n >= 2 else 0.0
    out["sd_diff"] = sd
    out["sem_diff"] = sd / math.sqrt(n) if n >= 2 else 0.0
    out["cohens_dz"] = (out["mean_diff"] / sd) if sd > 0 else float("nan")
    out["n_increased"] = sum(1 for d in diffs if d > 0)
    ci = wilcoxon_ci(diffs, alpha=alpha)
    # hl_shift 는 CI와 같은 표본(0 제외)에서 나온 값 — 점추정이 구간 밖으로
    # 나가지 않도록 wilcoxon_ci 가 함께 계산한 것을 씁니다.
    out["hl_shift"] = ci["hl_shift"]
    out["ci_low"] = ci["ci_low"]
    out["ci_high"] = ci["ci_high"]
    out["ci_alpha"] = ci["ci_alpha"]
    out["ci_method"] = ci["ci_method"]
    w = wilcoxon_signed_rank(diffs)
    out["wilcoxon_z"] = w["z"]
    out["wilcoxon_p"] = w["p_value"]
    out["wilcoxon_method"] = w["method"]
    out["w_plus"] = w["w_plus"]
    out["n_pairs"] = w["n_pairs"]
    return out


# --------------------------------------------------------------------------- #
# 독립 2군(unpaired) — Mann–Whitney U / Hodges–Lehmann 이동량 + 분포무관 CI
#
# 왜 필요한가: 짝지은(pre–post) 설계 외에 **평행군(parallel-arm) 시험**
# — 약물 대 위약, 디바이스 대 sham — 은 각 피험자가 한 군에만 속하므로
# Wilcoxon 부호순위가 아니라 Mann–Whitney(=Wilcoxon 순위합)를 써야 합니다.
# 짝지은 검정을 독립 자료에 쓰면 짝짓기가 임의가 되어 p값이 무의미해집니다.
# --------------------------------------------------------------------------- #
def mannwhitney_null_counts(m: int, n: int) -> List[int]:
    """H0 하에서 U = u 가 되는 배열의 수를 u=0..m·n 로 반환 (동점 없음 가정).

    U 는 "군2의 값이 군1의 값보다 큰 쌍의 수". 결합 표본의 순서배열
    C(m+n, m) 가지가 등확률이므로, counts[u] = (U=u 인 배열 수),
    Σcounts = C(m+n, m).

    점화식(고전적):
        C(m, n, u) = C(m-1, n, u-n) + C(m, n-1, u)
    직관: 결합 정렬열의 **마지막 원소**가 군1이면 그 원소는 군2의 모든 값보다
    크므로 남은 문제에서 u 가 n 만큼 줄고, 군2면 u 는 그대로입니다.
    """
    if m < 0 or n < 0:
        raise ValueError("표본 수는 음수일 수 없습니다.")
    # dp[i][j] = 크기 (i, j) 에 대한 counts 배열 (길이 i*j+1).
    dp: List[List[Optional[List[int]]]] = [[None] * (n + 1)
                                           for _ in range(m + 1)]
    for j in range(n + 1):
        dp[0][j] = [1]                      # 군1이 비면 U=0 만 가능
    for i in range(1, m + 1):
        dp[i][0] = [1]                      # 군2가 비면 U=0 만 가능
        for j in range(1, n + 1):
            size = i * j
            a = dp[i - 1][j]                # 길이 (i-1)*j+1
            b = dp[i][j - 1]                # 길이 i*(j-1)+1
            arr = [0] * (size + 1)
            for u in range(size + 1):
                v = 0
                if 0 <= u - j < len(a):
                    v += a[u - j]
                if u < len(b):
                    v += b[u]
                arr[u] = v
            dp[i][j] = arr
    return dp[m][n]


def _exact_mw_two_sided_p(u: float, m: int, n: int) -> float:
    """정확 Mann–Whitney 양측 p값 = 2·min(P(U≤u), P(U≥u)), 1로 절단."""
    counts = mannwhitney_null_counts(m, n)
    total = float(sum(counts))
    k = int(round(u))
    k = max(0, min(k, m * n))
    lower = sum(counts[: k + 1]) / total
    upper = sum(counts[k:]) / total
    return min(1.0, 2.0 * min(lower, upper))


def mann_whitney_u(a: Sequence[float], b: Sequence[float],
                   method: str = "auto") -> Dict[str, float]:
    """Mann–Whitney U 검정 (= Wilcoxon 순위합). 양측.

    a = 군1(기준/대조), b = 군2(개입). 반환하는 U 는 **군2 기준**:
        U = #{(i,j) : b_j > a_i} + 0.5·#{b_j == a_i}
    따라서 U > m·n/2 이면 군2가 큰 쪽입니다.

    method:
      "auto"   — 결합 표본에 동점이 없고 두 군 모두 EXACT_MAX_N_2SAMPLE(30) 이하면
                 정확 분포, 아니면 동점 보정 정규 근사. (기본)
      "exact"  — 항상 정확 분포. 동점이 있으면 ValueError.
      "approx" — 항상 정규 근사(연속성 보정 + 동점 보정 분산).

    반환 키: n_a, n_b, u_stat, z, p_value, method, rank_biserial, cles
      rank_biserial = 2U/(mn) - 1  (−1..+1, Kerby 2014; 부호는 군2 방향)
      cles          = U/(mn)       (common-language effect size: P(b > a))
    """
    if method not in ("auto", "exact", "approx"):
        raise ValueError(f"알 수 없는 method: {method!r} (auto/exact/approx)")
    av = [float(x) for x in a]
    bv = [float(x) for x in b]
    m, n = len(av), len(bv)
    out: Dict[str, float] = {
        "n_a": m, "n_b": n, "u_stat": float("nan"), "z": float("nan"),
        "p_value": float("nan"), "method": "approx",
        "rank_biserial": float("nan"), "cles": float("nan"),
    }
    if m == 0 or n == 0:
        return out

    pooled = av + bv
    ranks = _average_ranks(pooled)
    r_b = sum(ranks[m:])
    # U(군2) = R2 - n(n+1)/2 — 순위합 항등식. 동점은 평균순위라 0.5씩 나눠 가집니다.
    u = r_b - n * (n + 1) / 2.0
    out["u_stat"] = u
    mn = float(m * n)
    out["rank_biserial"] = 2.0 * u / mn - 1.0
    out["cles"] = u / mn

    ties = len(set(pooled)) != len(pooled)
    if method == "exact" and ties:
        raise ValueError(
            "정확 Mann–Whitney 검정은 결합 표본에 동점이 없어야 합니다 "
            "(method='auto' 를 쓰면 자동으로 정규 근사로 전환됩니다).")
    use_exact = (method == "exact") or (
        method == "auto" and not ties
        and m <= EXACT_MAX_N_2SAMPLE and n <= EXACT_MAX_N_2SAMPLE)

    # 동점 보정 분산: Var(U) = mn/12 · [(N+1) − Σ(t³−t)/(N(N−1))]
    N = m + n
    counts: Dict[float, int] = {}
    for v in pooled:
        counts[v] = counts.get(v, 0) + 1
    tie_sum = sum(t ** 3 - t for t in counts.values() if t > 1)
    if N > 1:
        var_u = mn / 12.0 * ((N + 1) - tie_sum / (N * (N - 1.0)))
    else:
        var_u = 0.0
    if var_u <= 0:
        out.update({"z": 0.0, "p_value": 1.0,
                    "method": "exact" if use_exact else "approx"})
        return out

    num = u - mn / 2.0
    cc = num - math.copysign(0.5, num) if num != 0 else 0.0   # 연속성 보정
    z = cc / math.sqrt(var_u)
    out["z"] = z

    if use_exact:
        out["p_value"] = _exact_mw_two_sided_p(u, m, n)
        out["method"] = "exact"
    else:
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
        out["p_value"] = min(1.0, max(0.0, p))
        out["method"] = "approx"
    return out


def pairwise_differences(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """모든 쌍 차이 b_j − a_i 를 정렬해 반환 (개수 = m·n)."""
    av = [float(x) for x in a]
    bv = [float(x) for x in b]
    out = [y - x for y in bv for x in av]
    out.sort()
    return out


def hodges_lehmann_2sample(a: Sequence[float], b: Sequence[float]) -> float:
    """두 표본 Hodges–Lehmann 이동량 추정 = median(b_j − a_i).

    Mann–Whitney 검정과 쌍대인 위치 이동(location shift) 추정량으로,
    평균차보다 이상값에 강건합니다. 한쪽이 비면 NaN.
    """
    d = pairwise_differences(a, b)
    if not d:
        return float("nan")
    return statistics.median(d)


def mann_whitney_ci(a: Sequence[float], b: Sequence[float],
                    alpha: float = 0.05) -> Dict[str, float]:
    """이동량(b − a)의 분포무관 (1−alpha) 신뢰구간 — Mann–Whitney 쌍대.

    정렬된 쌍 차이 D_(1..M), M = m·n 에 대해
        k = max{ k ≥ 0 : P(U ≤ k) < alpha/2 }
        CI = [ D_(k+1), D_(M−k) ]
    이 구성은 wilcoxon_ci 와 **동일한 논리**이며 정확 양측 검정과 쌍대입니다:
    U(Δ)=#{D_ij > Δ} 라 하면 기각 ⇔ U ≤ k 또는 U ≥ M−k ⇔ Δ ∉ [D_(k+1), D_(M−k)].
    (동점이 없다는 가정 위에 있고, 동점이 있으면 구간은 보수적이 됩니다.)

    n이 커서 정확 분포를 쓸 수 없으면 정규 근사로 k를 정합니다.
    k 가 유효 범위를 벗어나면(표본 부족) (-∞, ∞) 와 ci_method="insufficient-n".

    반환 키: ci_low, ci_high, ci_alpha, ci_k, ci_method, n_a, n_b, hl_shift
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha는 0과 1 사이여야 합니다.")
    av = [float(x) for x in a]
    bv = [float(x) for x in b]
    m, n = len(av), len(bv)
    out: Dict[str, float] = {
        "ci_low": float("nan"), "ci_high": float("nan"), "ci_alpha": alpha,
        "ci_k": float("nan"), "ci_method": None, "n_a": m, "n_b": n,
        "hl_shift": float("nan"),
    }
    if m == 0 or n == 0:
        return out

    diffs = pairwise_differences(av, bv)
    M = len(diffs)
    out["hl_shift"] = statistics.median(diffs)

    if m <= EXACT_MAX_N_2SAMPLE and n <= EXACT_MAX_N_2SAMPLE:
        counts = mannwhitney_null_counts(m, n)
        total = float(sum(counts))
        cum = 0.0
        k = -1
        for u in range(len(counts)):
            cum += counts[u] / total          # cum = P(U ≤ u)
            if cum < alpha / 2.0:
                k = u
            else:
                break
        method = "exact"
    else:
        z = _inv_normal_cdf(1.0 - alpha / 2.0)
        mean_u = m * n / 2.0
        sd_u = math.sqrt(m * n * (m + n + 1) / 12.0)
        k = int(math.floor(mean_u - z * sd_u))
        method = "approx"

    if k < 0 or k > (M - 1) // 2:
        out["ci_low"] = float("-inf")
        out["ci_high"] = float("inf")
        out["ci_k"] = float("nan")
        out["ci_method"] = "insufficient-n"
        return out
    out["ci_low"] = diffs[k]
    out["ci_high"] = diffs[M - 1 - k]
    out["ci_k"] = k
    out["ci_method"] = method
    return out


def unpaired_summary(a: Sequence[float], b: Sequence[float],
                     alpha: float = 0.05) -> Dict[str, float]:
    """독립 2군(a=대조, b=개입) 한 지표의 요약 — 평행군 시험용.

    유한한 값만 사용합니다(NaN 지표는 그 군에서 자동 제외).
    반환 키:
      n_a, n_b, mean_a, mean_b, sd_a, sd_b, median_a, median_b
      mean_diff  : mean_b − mean_a
      sd_pooled  : 합동 표준편차 (√(((na−1)sa²+(nb−1)sb²)/(na+nb−2)))
      cohens_d   : mean_diff / sd_pooled
      hedges_g   : 소표본 편의 보정 d (J = 1 − 3/(4(na+nb)−9))
      hl_shift, ci_low, ci_high, ci_alpha, ci_method : HL 이동량과 분포무관 CI
      u_stat, mw_z, mw_p, mw_method, rank_biserial, cles
    """
    av = [float(x) for x in a if _finite(x)]
    bv = [float(x) for x in b if _finite(x)]
    na, nb = len(av), len(bv)
    out: Dict[str, float] = {"n_a": na, "n_b": nb}
    if na == 0 or nb == 0:
        return out
    out["mean_a"] = statistics.fmean(av)
    out["mean_b"] = statistics.fmean(bv)
    out["mean_diff"] = out["mean_b"] - out["mean_a"]
    out["median_a"] = statistics.median(av)
    out["median_b"] = statistics.median(bv)
    sa = statistics.stdev(av) if na >= 2 else 0.0
    sb = statistics.stdev(bv) if nb >= 2 else 0.0
    out["sd_a"] = sa
    out["sd_b"] = sb
    if na + nb > 2:
        sp2 = ((na - 1) * sa * sa + (nb - 1) * sb * sb) / (na + nb - 2)
        sp = math.sqrt(sp2)
    else:
        sp = 0.0
    out["sd_pooled"] = sp
    out["cohens_d"] = (out["mean_diff"] / sp) if sp > 0 else float("nan")
    # Hedges g — d 는 소표본에서 효과를 과대추정하므로 J 로 보정합니다.
    denom = 4.0 * (na + nb) - 9.0
    j = (1.0 - 3.0 / denom) if denom > 0 else float("nan")
    out["hedges_g"] = (out["cohens_d"] * j) if _finite(j) else float("nan")

    ci = mann_whitney_ci(av, bv, alpha=alpha)
    out["hl_shift"] = ci["hl_shift"]
    out["ci_low"] = ci["ci_low"]
    out["ci_high"] = ci["ci_high"]
    out["ci_alpha"] = ci["ci_alpha"]
    out["ci_method"] = ci["ci_method"]

    w = mann_whitney_u(av, bv)
    out["u_stat"] = w["u_stat"]
    out["mw_z"] = w["z"]
    out["mw_p"] = w["p_value"]
    out["mw_method"] = w["method"]
    out["rank_biserial"] = w["rank_biserial"]
    out["cles"] = w["cles"]
    return out


# --------------------------------------------------------------------------- #
# 단조 추세 — Mann–Kendall / Kendall's tau-b
#
# 왜 필요한가: 한 기록을 여러 구간(epoch)으로 쪼개면 "RMSSD가 시간에 따라
# 오르는가?" 라는 정상성(stationarity)·순응(habituation) 질문이 생깁니다.
# 회귀 기울기는 이상 구간 하나에 끌려가므로 순위 기반 추세검정을 씁니다.
# --------------------------------------------------------------------------- #
def inversion_counts(n: int) -> List[int]:
    """길이 n 순열의 역위(inversion) 수 분포 — counts[d] = 역위 d 인 순열 수.

    Kendall S 의 정확 영분포에 필요합니다: S = C(n,2) − 2·D (D=역위 수).
    생성함수 Π_{i=1..n} (1 + x + … + x^{i−1}) 를 전개합니다. Σcounts = n!.
    """
    if n < 0:
        raise ValueError("n은 음수일 수 없습니다.")
    max_d = n * (n - 1) // 2
    counts = [0] * (max_d + 1)
    counts[0] = 1
    upto = 0
    for i in range(2, n + 1):
        upto += i - 1
        # 슬라이딩 누적합으로 (1+x+…+x^{i-1}) 을 곱합니다 — O(n³) 전체.
        prefix = [0] * (upto + 2)
        for d in range(upto + 1):
            prefix[d + 1] = prefix[d] + counts[d]
        for d in range(upto, -1, -1):
            lo = max(0, d - (i - 1))
            counts[d] = prefix[d + 1] - prefix[lo]
    return counts


def mann_kendall(values: Sequence[float],
                 method: str = "auto",
                 positions: Optional[Sequence[float]] = None
                 ) -> Dict[str, float]:
    """Mann–Kendall 단조 추세 검정 (양측) + Kendall tau-b.

    S = Σ_{i<j} sign(x_j − x_i). S>0 = 증가 추세.
    method:
      "auto"   — 동점이 없고 n ≤ EXACT_MAX_N_TREND(25) 이면 정확 분포, 아니면
                 동점 보정 정규 근사(연속성 보정). (기본)
      "exact"  — 항상 정확 분포. 동점이 있으면 ValueError.
      "approx" — 항상 정규 근사.

    positions: 각 값의 **실제 x좌표**(예: 창 번호). Theil–Sen 기울기 분모에 씁니다.
      주지 않으면 0,1,2,… 를 씁니다. **비유한 값이 섞이면 반드시 주세요** —
      이 함수는 비유한 값을 버리고 압축하므로, 압축된 리스트의 이웃은 원래
      이웃이 아닙니다. 예: 창 12개 중 짝수 번째만 유한하면 압축 후 이웃 간격이
      실제로는 2창인데 1창으로 계산돼 기울기가 **2배로 부풀려집니다**
      (S·tau·p 는 순서만 쓰므로 영향 없음 — 기울기만 틀립니다).

    반환 키: n, s, tau, z, p_value, method, slope
      tau   : Kendall tau-b (동점 보정)
      slope : Theil–Sen 기울기 = median((x_j−x_i)/(pos_j−pos_i))
    n < 3 이면 검정 불가(z=NaN, p=NaN)이지만 tau/slope 는 가능하면 냅니다.
    """
    if method not in ("auto", "exact", "approx"):
        raise ValueError(f"알 수 없는 method: {method!r} (auto/exact/approx)")
    vals = [float(v) if _finite(v) else float("nan") for v in values]
    if positions is None:
        pos_all = [float(i) for i in range(len(vals))]
    else:
        if len(positions) != len(values):
            raise ValueError(
                f"positions 길이가 values 와 다릅니다 "
                f"({len(positions)} != {len(values)}).")
        pos_all = [float(p) for p in positions]
    keep = [i for i, v in enumerate(vals) if _finite(v)]
    x = [vals[i] for i in keep]
    pos = [pos_all[i] for i in keep]
    n = len(x)
    out: Dict[str, float] = {
        "n": n, "s": float("nan"), "tau": float("nan"), "z": float("nan"),
        "p_value": float("nan"), "method": "approx", "slope": float("nan"),
    }
    if n < 2:
        return out

    s = 0
    n_ties_pairs = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = x[j] - x[i]
            if d > 0:
                s += 1
            elif d < 0:
                s -= 1
            else:
                n_ties_pairs += 1
    out["s"] = float(s)

    n0 = n * (n - 1) / 2.0
    # tau-b: 시간축(1..n)에는 동점이 없으므로 분모는 √(n0·(n0 − ties)).
    denom = math.sqrt(n0 * (n0 - n_ties_pairs))
    out["tau"] = (s / denom) if denom > 0 else float("nan")

    slopes = [(x[j] - x[i]) / (pos[j] - pos[i])
              for i in range(n - 1) for j in range(i + 1, n)
              if pos[j] != pos[i]]
    out["slope"] = statistics.median(slopes) if slopes else float("nan")

    if n < 3:
        return out

    ties = n_ties_pairs > 0
    if method == "exact" and ties:
        raise ValueError(
            "정확 Mann–Kendall 검정은 동점이 없어야 합니다 "
            "(method='auto' 를 쓰면 자동으로 정규 근사로 전환됩니다).")
    use_exact = (method == "exact") or (
        method == "auto" and not ties and n <= EXACT_MAX_N_TREND)

    # 동점 보정 분산: Var(S) = [n(n−1)(2n+5) − Σ t(t−1)(2t+5)]/18
    tie_groups: Dict[float, int] = {}
    for v in x:
        tie_groups[v] = tie_groups.get(v, 0) + 1
    tie_term = sum(t * (t - 1) * (2 * t + 5)
                   for t in tie_groups.values() if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:
        out.update({"z": 0.0, "p_value": 1.0,
                    "method": "exact" if use_exact else "approx"})
        return out
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    out["z"] = z

    if use_exact:
        counts = inversion_counts(n)
        total = float(sum(counts))
        # S = C(n,2) − 2D → D = (C(n,2) − S)/2. S 의 분포는 0 대칭입니다.
        max_s = int(n0)
        d_obs = (max_s - s) // 2
        lower = sum(counts[: d_obs + 1]) / total          # P(D ≤ d) = P(S ≥ s)
        upper = sum(counts[d_obs:]) / total               # P(D ≥ d) = P(S ≤ s)
        out["p_value"] = min(1.0, 2.0 * min(lower, upper))
        out["method"] = "exact"
    else:
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
        out["p_value"] = min(1.0, max(0.0, p))
        out["method"] = "approx"
    return out
