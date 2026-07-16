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
           "wilcoxon_ci", "holm_adjust", "benjamini_hochberg"]

# 정확 검정을 쓰는 최대 표본 수. n=25 → 2^25 경우의 수를 DP로 세지만 상태
# 공간은 n(n+1)/2+1 = 326 개뿐이라 즉시 계산됩니다.
EXACT_MAX_N = 25


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
