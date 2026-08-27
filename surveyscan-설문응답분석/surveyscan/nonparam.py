"""순위 기반(비모수) 검정 — Mann-Whitney U · Kruskal-Wallis H · Wilcoxon 부호순위.

**왜 필요한가.** 설문 점수는 리커트 문항의 합/평균이라 정규분포와 거리가 있고, 임상
자료는 표본이 작고 바닥·천장 효과로 치우치는 일이 흔하다. 이런 자료에서 심사자·통계
리뷰어가 "비모수 검정으로도 같은 결론인가"를 묻는 것은 표준 절차다. 이 모듈은 t/ANOVA
결과 **옆에 나란히** 놓을 순위 기반 검정을 제공한다(대체가 아니라 민감도 분석).

설계 원칙
- **동순위(tie) 보정을 항상 적용한다.** 리커트 점수는 동점이 매우 많아서, 보정 없는
  근사는 p 를 체계적으로 크게(보수적으로) 만든다.
- **연속성 보정(continuity correction)** 을 항상 쓴다(Mann-Whitney 는 scipy 기본과 같고,
  Wilcoxon 은 scipy 의 `correction=True` 에 해당한다 — scipy 기본값은 False 다).
- **효과크기를 함께 낸다.** 순위기반 효과크기(rank-biserial r, ε²)는 p 와 달리 표본
  크기에 휘둘리지 않는다.
- 계산이 불가능하면(분산 0, N 부족) 틀린 숫자 대신 None 을 돌려준다.
- p 는 꼬리를 직접 계산해 1e-300 수준까지 유지하지만, |z|≳38.5 에서는 double 로 표현할 수
  없어 0.0 이 된다(부동소수의 한계 — 그 경우 리포트는 '<.001' 로 표기된다).

정확도: scipy.stats(mannwhitneyu/kruskal/wilcoxon, 점근법)와 대조해 ~1e-12 수준에서
일치함을 테스트로 확인한다(테스트에서만 scipy 사용).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import special

# Wilcoxon 부호순위에서 정확검정(exact)을 쓸 표본 상한. 이보다 크면 정규근사.
# scipy 기본(n<=25)과 같은 기준. 동순위나 0 차이가 있으면 정확분포가 성립하지 않아
# 크기와 무관하게 정규근사(동순위 보정 포함)를 쓴다.
WILCOXON_EXACT_MAX_N = 25


def rank_average(xs: Sequence[float]) -> Tuple[List[float], float]:
    """평균순위(동점은 평균으로) 와 동순위 보정항 Σ(t³−t) 를 함께 반환.

    반환: (순위 리스트(입력 순서), tie_sum) — tie_sum = Σ over 동점그룹 (t³ − t).
    """
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    tie_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        t = j - i + 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 평균순위
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        if t > 1:
            tie_sum += float(t) ** 3 - float(t)
        i = j + 1
    return ranks, tie_sum


def mannwhitney_u(
    xs: Sequence[float], ys: Sequence[float]
) -> Optional[Dict[str, object]]:
    """Mann-Whitney U 검정(양측, 정규근사 + 동순위·연속성 보정).

      U₁ = R₁ − n₁(n₁+1)/2      (R₁ = 1집단 순위합)
      μ  = n₁n₂/2
      σ² = (n₁n₂/12)·[(N+1) − Σ(t³−t)/(N(N−1))]
      z  = (U₁ − μ ∓ 0.5)/σ,   p = 2·P(Z ≥ |z|)

    효과크기는 rank-biserial r = 2U₁/(n₁n₂) − 1 ∈ [−1, 1]
    (= P(x>y) − P(x<y), 부호는 1집단 기준). 모든 값이 동일해 σ=0 이면 None.
    """
    n1, n2 = len(xs), len(ys)
    if n1 < 1 or n2 < 1:
        return None
    combined = list(xs) + list(ys)
    n = n1 + n2
    ranks, tie_sum = rank_average(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    # 동순위 보정 분산. 모든 값이 같으면 대괄호 안이 0 → σ=0.
    var = (n1 * n2 / 12.0) * ((n + 1) - tie_sum / (n * (n - 1.0))) if n > 1 else 0.0
    if var <= 0.0:
        return None
    sd = math.sqrt(var)
    diff = u1 - mu
    # 연속성 보정: 평균 쪽으로 0.5 당긴다(부호에 따라). |diff|<0.5 면 0 으로.
    corr = math.copysign(min(0.5, abs(diff)), diff)
    z = (diff - corr) / sd
    p = min(max(2.0 * special.norm_sf(abs(z)), 0.0), 1.0)
    return {
        "test": "mannwhitney",
        "U": u1,
        "U2": n1 * n2 - u1,
        "z": z,
        "p": p,
        "n1": n1,
        "n2": n2,
        "rank_biserial": 2.0 * u1 / (n1 * n2) - 1.0,
    }


def kruskal_wallis(groups: Sequence[Sequence[float]]) -> Optional[Dict[str, object]]:
    """Kruskal-Wallis H 검정(3집단 이상, 동순위 보정).

      H = 12/(N(N+1)) · Σ R_i²/n_i − 3(N+1),   H' = H / [1 − Σ(t³−t)/(N³−N)]
      df = k−1,  p = P(χ²_df ≥ H')

    효과크기 ε² = H'/(N−1) ∈ [0,1] (Tomczak & Tomczak 2014).
    모든 값이 같아 보정항이 0 이 되면(=순위 분산 없음) None.
    """
    k = len(groups)
    if k < 2:
        return None
    ns = [len(g) for g in groups]
    if any(n < 1 for n in ns):
        return None
    n = sum(ns)
    if n < 3:
        return None
    flat: List[float] = []
    for g in groups:
        flat.extend(g)
    ranks, tie_sum = rank_average(flat)
    pos = 0
    h = 0.0
    for size in ns:
        rsum = sum(ranks[pos:pos + size])
        h += (rsum * rsum) / size
        pos += size
    h = 12.0 / (n * (n + 1.0)) * h - 3.0 * (n + 1.0)
    corr = 1.0 - tie_sum / (float(n) ** 3 - n)
    if corr <= 0.0:
        return None  # 전부 동점 — 순위에 정보가 없다
    h /= corr
    if h < 0.0:
        h = 0.0
    df = float(k - 1)
    p = min(max(special.chi2_sf(h, df), 0.0), 1.0)
    return {
        "test": "kruskal",
        "H": h,
        "df": df,
        "p": p,
        "n": n,
        "epsilon_sq": h / (n - 1.0),
    }


def _wilcoxon_exact_p(w: float, n: int) -> float:
    """부호순위합 W⁺=w (정수), n 쌍일 때의 양측 정확 p.

    가능한 2ⁿ 부호조합에서 W⁺ 의 분포를 동적계획법으로 센다(순위가 1..n 정수일 때만
    성립 — 동순위·0차이가 있으면 호출하지 않는다).
    """
    total_rank = n * (n + 1) // 2
    counts = [0] * (total_rank + 1)
    counts[0] = 1
    for r in range(1, n + 1):
        for s in range(total_rank, r - 1, -1):
            counts[s] += counts[s - r]
        # 위 갱신은 '순위 r 을 양수쪽에 넣는다'는 선택을 누적한 것이다.
    total = float(2 ** n)
    wi = int(round(w))
    lower = sum(counts[:wi + 1]) / total          # P(W⁺ ≤ w)
    upper = sum(counts[wi:]) / total              # P(W⁺ ≥ w)
    return min(1.0, 2.0 * min(lower, upper))


def wilcoxon_signed_rank(diffs: Sequence[float]) -> Optional[Dict[str, object]]:
    """Wilcoxon 부호순위 검정(대응표본, 양측).

    0 인 차이는 제외(Wilcoxon 원안). 남은 |차이| 에 평균순위를 매겨
      W⁺ = 양의 차이 순위합,  W⁻ = 음의 차이 순위합
    정확검정(n≤25, 동순위·0 없음) 또는 동순위 보정 정규근사:
      μ = n(n+1)/4,  σ² = n(n+1)(2n+1)/24 − Σ(t³−t)/48,  z = (W⁺ − μ ∓ 0.5)/σ

    효과크기: matched-pairs rank-biserial r = (W⁺ − W⁻)/(n(n+1)/2) ∈ [−1,1]
    (양수면 '증가한 쌍의 순위합이 크다'는 뜻).
    차이가 전부 0 이거나 남은 쌍이 없으면 None.
    """
    nz = [d for d in diffs if d != 0.0]
    n_zero = len(diffs) - len(nz)
    n = len(nz)
    if n < 1:
        return None
    ranks, tie_sum = rank_average([abs(d) for d in nz])
    w_plus = sum(r for r, d in zip(ranks, nz) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, nz) if d < 0)
    total_rank = n * (n + 1) / 2.0
    rb = (w_plus - w_minus) / total_rank if total_rank > 0 else None

    exact = n <= WILCOXON_EXACT_MAX_N and tie_sum == 0.0 and n_zero == 0
    if exact:
        p = _wilcoxon_exact_p(w_plus, n)
        z = None
    else:
        mu = n * (n + 1.0) / 4.0
        var = n * (n + 1.0) * (2.0 * n + 1.0) / 24.0 - tie_sum / 48.0
        if var <= 0.0:
            return None
        sd = math.sqrt(var)
        diff = w_plus - mu
        corr = math.copysign(min(0.5, abs(diff)), diff)
        z = (diff - corr) / sd
        p = min(max(2.0 * special.norm_sf(abs(z)), 0.0), 1.0)
    return {
        "test": "wilcoxon",
        "W": min(w_plus, w_minus),
        "W_plus": w_plus,
        "W_minus": w_minus,
        "n": n,
        "n_zero": n_zero,
        "z": z,
        "p": p,
        "exact": exact,
        "rank_biserial": rb,
    }


def rank_effect_label(r: Optional[float]) -> str:
    """rank-biserial r 의 관례적 크기 라벨(|.1| 작음 / |.3| 중간 / |.5| 큼).

    Cohen 의 r 기준을 순위 효과크기에 그대로 옮긴 **관례**일 뿐, 임상적 중요도와
    같지 않다.
    """
    if r is None:
        return "-"
    a = abs(r)
    if a >= 0.5:
        return "큼"
    if a >= 0.3:
        return "중간"
    if a >= 0.1:
        return "작음"
    return "매우 작음"
