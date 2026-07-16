"""가벼운 통계 — 짝지은(paired) 코호트 검정. 표준 라이브러리만.

장치 검증에서 여러 피험자의 (기저, 개입) 짝을 모아 지표별로 개입 효과를
정량화합니다. HRV 지표는 정규성이 약한 경우가 많아 비모수 **Wilcoxon 부호순위
검정**을 기본 유의성 지표로 제공하고, 효과크기(Cohen's dz)와 평균 차이±SD를
함께 냅니다. 정규 근사 p값은 math.erf 로 계산합니다(외부 의존성 없음).
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Sequence

__all__ = ["normal_cdf", "wilcoxon_signed_rank", "paired_summary"]


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


def wilcoxon_signed_rank(diffs: Sequence[float]) -> Dict[str, float]:
    """Wilcoxon 부호순위 검정 (정규 근사, 연속성·동점 보정).

    0 차이는 제외(Wilcoxon 방식). 반환 키:
      n_pairs      : 0이 아닌 차이 수(검정에 사용된 표본 수)
      w_plus       : 양의 차이 순위합 (검정통계량)
      z            : 정규 근사 z (연속성 보정)
      p_value      : 양측 p값
    표본이 없거나(모두 0) 분산이 0이면 z=0, p=1.
    """
    nz = [d for d in diffs if d != 0.0]
    n = len(nz)
    out = {"n_pairs": n, "w_plus": float("nan"), "z": float("nan"),
           "p_value": float("nan")}
    if n == 0:
        out.update({"w_plus": 0.0, "z": 0.0, "p_value": 1.0})
        return out

    absd = [abs(d) for d in nz]
    ranks = _average_ranks(absd)
    w_plus = sum(r for r, d in zip(ranks, nz) if d > 0)
    out["w_plus"] = w_plus

    mean_w = n * (n + 1) / 4.0
    # 동점 보정: var = [n(n+1)(2n+1) - Σ(t³-t)/2] / 24
    tie_term = 0.0
    counts: Dict[float, int] = {}
    for a in absd:
        counts[a] = counts.get(a, 0) + 1
    for t in counts.values():
        if t > 1:
            tie_term += t ** 3 - t
    var_w = (n * (n + 1) * (2 * n + 1) - tie_term / 2.0) / 24.0
    if var_w <= 0:
        out.update({"z": 0.0, "p_value": 1.0})
        return out

    num = w_plus - mean_w
    cc = num - math.copysign(0.5, num) if num != 0 else 0.0  # 연속성 보정
    z = cc / math.sqrt(var_w)
    p = 2.0 * (1.0 - normal_cdf(abs(z)))
    p = min(1.0, max(0.0, p))
    out.update({"z": z, "p_value": p})
    return out


def paired_summary(baseline: Sequence[float],
                   intervention: Sequence[float]) -> Dict[str, float]:
    """한 지표에 대한 짝지은 코호트 요약.

    baseline[i], intervention[i] 는 같은 피험자 i의 값. 유한한 짝만 사용.
    반환 키:
      n            : 유효 짝 수
      mean_base, mean_interv, mean_diff, sd_diff, sem_diff
      cohens_dz    : 표준화 효과크기 = mean_diff / sd_diff
      median_diff
      wilcoxon_z, wilcoxon_p, w_plus, n_pairs (0 아닌 차이)
      n_increased  : 개입에서 값이 증가한 피험자 수
    """
    pairs = [(float(b), float(v)) for b, v in zip(baseline, intervention)
             if math.isfinite(b) and math.isfinite(v)]
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
    w = wilcoxon_signed_rank(diffs)
    out["wilcoxon_z"] = w["z"]
    out["wilcoxon_p"] = w["p_value"]
    out["w_plus"] = w["w_plus"]
    out["n_pairs"] = w["n_pairs"]
    return out
