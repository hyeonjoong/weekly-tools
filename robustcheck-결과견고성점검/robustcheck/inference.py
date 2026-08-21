"""검정 6종 + 공변량 보정 2종 — 전부 자체 구현(stdlib 전용).

이 모듈이 계산하는 것:
  · Welch t / Student t (독립 2군)         · Mann–Whitney U (독립 2군, 비모수)
  · 대응표본 t                              · Wilcoxon 부호순위 (대응, 비모수)
  · Pearson r                               · Spearman ρ (비모수)
  · ANCOVA (기저값 보정, 모수)              · Quade 순위 ANCOVA (기저값 보정, 비모수)

scipy 와의 일치는 `tests/test_inference.py` 가 **하드코딩된 scipy 값**과
대조해 증명한다(테스트도 완전 오프라인이다).

정확 검정 선택 규칙(리포트에도 인쇄된다):
  · Mann–Whitney: 동점이 없고 n1·n2 ≤ 400 이면 정확분포, 아니면 동점보정 +
    연속성보정 정규근사.
  · Wilcoxon: 0 차이와 동점이 없고 n ≤ 30 이면 정확분포, 아니면 동점보정
    정규근사(연속성보정 없음 — scipy 기본과 동일).
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .distributions import (
    normal_two_sided,
    student_t_two_sided,
)

__all__ = [
    "TestResult",
    "mean",
    "variance",
    "stdev",
    "rankdata",
    "welch_t_test",
    "student_t_test",
    "paired_t_test",
    "mann_whitney_u",
    "wilcoxon_signed_rank",
    "pearson_r",
    "spearman_rho",
    "ancova_baseline",
    "quade_rank_ancova",
    "SingularModel",
]

# 정확분포를 쓰는 상한. 넘으면 정규근사로 내려가고, 어느 쪽을 썼는지
# 결과 객체의 `method` 에 남는다(조용히 바꾸지 않는다).
MWU_EXACT_MAX_CELLS = 400
WILCOXON_EXACT_MAX_N = 30


class SingularModel(Exception):
    """공변량 모형이 특이(공변량 분산 0, 완전 공선성)해서 풀 수 없을 때."""


class TestResult:
    """검정 하나의 결과. `p` 는 언제나 양측이다."""

    __slots__ = ("name", "statistic", "p", "df", "method", "n", "extra")

    def __init__(
        self,
        name: str,
        statistic: float,
        p: float,
        df: Optional[float] = None,
        method: str = "",
        n: Optional[int] = None,
        extra: Optional[Dict[str, float]] = None,
    ) -> None:
        self.name = name
        self.statistic = statistic
        self.p = p
        self.df = df
        self.method = method
        self.n = n
        self.extra = extra or {}

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return "TestResult(%s, stat=%.6g, p=%.6g, method=%s)" % (
            self.name,
            self.statistic,
            self.p,
            self.method,
        )


# ---------------------------------------------------------------- 기초 통계


def mean(values: Sequence[float]) -> float:
    """산술평균. 합이 배정도를 넘으면 **죽지 않고** ValueError 로 알린다.

    `math.fsum([1e308] * 4)` 는 `OverflowError` 를 던진다. 그대로 두면 툴이
    트레이스백으로 죽으면서 종료코드 1(= '취약')을 내보내, 크래시와 실제
    취약 판정을 구분할 수 없게 된다(실측). 큰 값은 먼저 나눠서 더한다.
    """
    n = len(values)
    if not n:
        raise ValueError("mean: 빈 표본")
    try:
        return math.fsum(values) / n
    except OverflowError:
        pass
    scale = max(abs(v) for v in values)
    if not math.isfinite(scale) or scale == 0.0:
        raise ValueError("mean: 값에 무한대가 섞여 있습니다")
    result = math.fsum(v / scale for v in values) / n * scale
    if not math.isfinite(result):
        raise ValueError(
            "평균이 배정도 범위를 넘습니다 (값의 크기가 %.3g 수준) — "
            "단위를 바꿔 입력하세요" % scale)
    return result


def variance(values: Sequence[float]) -> float:
    """표본분산(ddof=1). **척도 불변**으로 계산한다.

    순진하게 `fsum((v−m)²)` 를 쓰면 값이 1e250 근처일 때 평균의 1 ulp 오차가
    제곱되면서 `OverflowError` 로 툴 자체가 죽는다(실측). 편차를 최대편차로
    나눠 O(1) 로 만든 뒤 되돌린다. 그래도 배정도를 벗어나면 **추측하지 않고**
    ValueError 를 던져 그 시나리오만 사유와 함께 건너뛰게 한다.
    """
    n = len(values)
    if n < 2:
        raise ValueError("variance: n ≥ 2 가 필요합니다")
    m = mean(values)
    deviations = [v - m for v in values]
    scale = max(abs(d) for d in deviations)
    if scale == 0.0:
        return 0.0
    normalised = math.fsum((d / scale) ** 2 for d in deviations) / (n - 1)
    result = normalised * scale * scale
    if not math.isfinite(result):
        raise ValueError(
            "분산이 배정도 상한을 넘습니다 (값의 크기가 %.3g 수준) — "
            "단위를 바꿔 입력하세요" % scale)
    if result == 0.0:
        raise ValueError(
            "분산이 배정도 하한 아래입니다 (값의 크기가 %.3g 수준) — "
            "단위를 바꿔 입력하세요" % scale)
    return result


def stdev(values: Sequence[float]) -> float:
    """표본표준편차. 분산이 배정도를 벗어나는 자료에서도 살아남는다."""
    n = len(values)
    if n < 2:
        raise ValueError("stdev: n ≥ 2 가 필요합니다")
    m = mean(values)
    deviations = [v - m for v in values]
    scale = max(abs(d) for d in deviations)
    if scale == 0.0:
        return 0.0
    return scale * math.sqrt(
        math.fsum((d / scale) ** 2 for d in deviations) / (n - 1))


def rankdata(values: Sequence[float]) -> List[float]:
    """동점은 평균순위(midrank). 안정 정렬이라 같은 입력 → 같은 출력."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_groups(values: Sequence[float]) -> List[int]:
    """동점 그룹 크기 목록(크기 1 포함)."""
    counts: Dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.values())


# ------------------------------------------------------------ 독립 2군 · 모수


def welch_t_test(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """Welch 이분산 t 검정 (등분산 가정 없음)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("welch_t_test: 각 군 n ≥ 2 가 필요합니다")
    v1, v2 = variance(a), variance(b)
    se2 = v1 / n1 + v2 / n2
    if se2 <= 0.0 or not math.isfinite(se2):
        # 두 군 모두 분산 0(또는 수치범위 초과) — 통계량이 정의되지 않는다.
        raise ValueError("welch_t_test: 두 군의 분산이 모두 0 이거나 범위를 넘습니다")
    t = (mean(a) - mean(b)) / math.sqrt(se2)
    denom = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    if denom <= 0.0 or not math.isfinite(denom):
        # 분산이 극단적으로 작아 제곱이 0 으로 언더플로 — 자유도를 추측하지 않는다.
        raise ValueError("welch_t_test: 자유도를 계산할 수 없습니다(값의 크기가 극단적)")
    df = se2 * se2 / denom
    return TestResult("Welch t", t, student_t_two_sided(t, df), df, "정확", n1 + n2)


def student_t_test(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """등분산 가정 Student t 검정."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        raise ValueError("student_t_test: 각 군 n ≥ 2 가 필요합니다")
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * variance(a) + (n2 - 1) * variance(b)) / df
    if sp2 <= 0.0:
        raise ValueError("student_t_test: 합동분산이 0 입니다")
    t = (mean(a) - mean(b)) / math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    return TestResult("Student t", t, student_t_two_sided(t, df), df, "정확", n1 + n2)


# ---------------------------------------------------------- 독립 2군 · 비모수


def _mwu_counts(n1: int, n2: int) -> List[int]:
    """U 통계량의 정확 도수분포 (동점 없음 가정).

    점화식 N(m,n,u) = N(m−1,n,u−n) + N(m,n−1,u).
    """
    size = n1 * n2
    # prev[m] 을 한 줄씩 올리는 대신 2차원 DP 를 그대로 쓴다(n ≤ 20 이라 충분).
    table = [[None] * (n2 + 1) for _ in range(n1 + 1)]
    for m in range(n1 + 1):
        for n in range(n2 + 1):
            if m == 0 or n == 0:
                row = [0] * (size + 1)
                row[0] = 1
                table[m][n] = row
                continue
            row = [0] * (size + 1)
            left = table[m - 1][n]
            down = table[m][n - 1]
            for u in range(m * n + 1):
                total = down[u]
                if u - n >= 0:
                    total += left[u - n]
                row[u] = total
            table[m][n] = row
    return table[n1][n2]


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """Mann–Whitney U (양측).

    반환 statistic 은 첫 번째 표본 기준 U1 (scipy 기본과 동일).
    """
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        raise ValueError("mann_whitney_u: 각 군 n ≥ 1 이 필요합니다")
    pooled = list(a) + list(b)
    ranks = rankdata(pooled)
    r1 = math.fsum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    groups = _tie_groups(pooled)
    has_ties = any(g > 1 for g in groups)

    if not has_ties and n1 * n2 <= MWU_EXACT_MAX_CELLS:
        counts = _mwu_counts(n1, n2)
        total = math.fsum(counts)
        u = int(round(u1))
        # P(U ≥ u) 와 P(U ≤ u) 중 작은 쪽 × 2 (scipy method='exact' 와 동일)
        upper = math.fsum(counts[u:]) / total
        lower = math.fsum(counts[: u + 1]) / total
        p = min(1.0, 2.0 * min(upper, lower))
        return TestResult("Mann-Whitney U", u1, p, None, "정확분포", n1 + n2,
                          {"동점": 0.0})

    mu = n1 * n2 / 2.0
    tie_term = math.fsum(g ** 3 - g for g in groups)
    n = n1 + n2
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1.0)))
    if sigma2 <= 0.0:
        # 모든 값이 동일 — 순위가 전부 같아 검정 불가. 추정하지 않는다.
        raise ValueError("mann_whitney_u: 모든 관측값이 동일해 순위 분산이 0 입니다")
    z = (abs(u1 - mu) - 0.5) / math.sqrt(sigma2)
    z = max(z, 0.0)
    p = min(1.0, normal_two_sided(z))
    return TestResult(
        "Mann-Whitney U", u1, p, None, "정규근사(동점·연속성 보정)", n1 + n2,
        {"동점": 1.0 if has_ties else 0.0, "z": z},
    )


# ---------------------------------------------------------------- 대응표본


def paired_t_test(pre: Sequence[float], post: Sequence[float]) -> TestResult:
    """대응표본 t 검정. 차이는 post − pre."""
    if len(pre) != len(post):
        raise ValueError("paired_t_test: 두 계열의 길이가 다릅니다")
    diffs = [q - p for p, q in zip(pre, post)]
    n = len(diffs)
    if n < 2:
        raise ValueError("paired_t_test: n ≥ 2 가 필요합니다")
    sd = stdev(diffs)
    if sd <= 0.0:
        raise ValueError("paired_t_test: 차이의 분산이 0 입니다")
    t = mean(diffs) / (sd / math.sqrt(n))
    df = n - 1
    return TestResult("대응 t", t, student_t_two_sided(t, df), df, "정확", n)


def _wilcoxon_counts(n: int) -> List[int]:
    """W+ 의 정확 도수분포. 생성함수 ∏(1 + q^i)."""
    size = n * (n + 1) // 2
    counts = [0] * (size + 1)
    counts[0] = 1
    for i in range(1, n + 1):
        for w in range(size, i - 1, -1):
            counts[w] += counts[w - i]
    return counts


def wilcoxon_signed_rank(pre: Sequence[float], post: Sequence[float]) -> TestResult:
    """Wilcoxon 부호순위 검정 (양측).

    0 차이는 버린다(scipy 기본 `zero_method='wilcox'`). 반환 statistic 은
    min(W+, W−) — 역시 scipy 기본과 같다.
    """
    if len(pre) != len(post):
        raise ValueError("wilcoxon_signed_rank: 두 계열의 길이가 다릅니다")
    diffs = [q - p for p, q in zip(pre, post)]
    nonzero = [d for d in diffs if d != 0.0]
    dropped = len(diffs) - len(nonzero)
    n = len(nonzero)
    if n < 1:
        raise ValueError("wilcoxon_signed_rank: 0 이 아닌 차이가 없습니다")
    absd = [abs(d) for d in nonzero]
    ranks = rankdata(absd)
    w_plus = math.fsum(r for d, r in zip(nonzero, ranks) if d > 0)
    w_minus = math.fsum(r for d, r in zip(nonzero, ranks) if d < 0)
    stat = min(w_plus, w_minus)
    groups = _tie_groups(absd)
    has_ties = any(g > 1 for g in groups)

    if not has_ties and dropped == 0 and n <= WILCOXON_EXACT_MAX_N:
        counts = _wilcoxon_counts(n)
        total = float(2 ** n)
        w = int(round(stat))
        lower = math.fsum(counts[: w + 1]) / total
        upper = math.fsum(counts[w:]) / total
        p = min(1.0, 2.0 * min(lower, upper))
        return TestResult("Wilcoxon 부호순위", stat, p, None, "정확분포", n,
                          {"버린0": float(dropped)})

    mu = n * (n + 1) / 4.0
    tie_term = math.fsum(g ** 3 - g for g in groups) / 48.0
    sigma2 = n * (n + 1) * (2 * n + 1) / 24.0 - tie_term
    if sigma2 <= 0.0:
        raise ValueError("wilcoxon_signed_rank: 순위 분산이 0 입니다")
    z = (w_plus - mu) / math.sqrt(sigma2)
    p = min(1.0, normal_two_sided(z))
    return TestResult(
        "Wilcoxon 부호순위", stat, p, None, "정규근사(동점 보정)", n,
        {"버린0": float(dropped), "z": z},
    )


# ------------------------------------------------------------------- 상관


def _pearson(x: Sequence[float], y: Sequence[float]) -> Tuple[float, int]:
    n = len(x)
    if n != len(y):
        raise ValueError("pearson: 두 계열의 길이가 다릅니다")
    if n < 3:
        raise ValueError("pearson: n ≥ 3 이 필요합니다")
    mx, my = mean(x), mean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = math.fsum((a - mx) ** 2 for a in x)
    syy = math.fsum((b - my) ** 2 for b in y)
    if sxx <= 0.0 or syy <= 0.0:
        raise ValueError("pearson: 한쪽 변수의 분산이 0 입니다")
    r = sxy / math.sqrt(sxx * syy)
    # 부동소수 오차로 |r| 가 1 을 아주 살짝 넘는 경우를 막는다.
    r = max(-1.0, min(1.0, r))
    return r, n


def pearson_r(x: Sequence[float], y: Sequence[float]) -> TestResult:
    """Pearson 상관 + t 근사 양측 p (scipy.stats.pearsonr 와 동일한 방식)."""
    r, n = _pearson(x, y)
    df = n - 2
    if abs(r) >= 1.0:
        return TestResult("Pearson r", r, 0.0, df, "정확(|r|=1)", n)
    t = r * math.sqrt(df / (1.0 - r * r))
    return TestResult("Pearson r", r, student_t_two_sided(t, df), df, "t 근사", n,
                      {"t": t})


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> TestResult:
    """Spearman 순위상관 + t 근사 양측 p (scipy.stats.spearmanr 와 동일)."""
    rx, ry = rankdata(x), rankdata(y)
    r, n = _pearson(rx, ry)
    df = n - 2
    if abs(r) >= 1.0:
        return TestResult("Spearman rho", r, 0.0, df, "정확(|rho|=1)", n)
    t = r * math.sqrt(df / (1.0 - r * r))
    return TestResult("Spearman rho", r, student_t_two_sided(t, df), df, "t 근사", n,
                      {"t": t})


# ------------------------------------------------------------ 공변량 보정


def _solve3(mat: List[List[float]], rhs: List[float]) -> List[float]:
    """3×3 선형계를 부분 피벗 가우스 소거로 푼다."""
    n = 3
    a = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise SingularModel("설계행렬이 특이합니다(공변량 분산 0 또는 공선성)")
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def ancova_baseline(
    values_a: Sequence[float],
    cov_a: Sequence[float],
    values_b: Sequence[float],
    cov_b: Sequence[float],
) -> TestResult:
    """기저값 보정 ANCOVA — y ~ 1 + 군더미 + 기저값.

    군더미는 A 군 = 1, B 군 = 0 이므로 계수는 **A − B 의 보정된 차이**다.
    Welch t 의 (mean_A − mean_B) 와 부호 방향이 같다.
    """
    n1, n2 = len(values_a), len(values_b)
    n = n1 + n2
    if n1 < 2 or n2 < 2:
        raise ValueError("ancova_baseline: 각 군 n ≥ 2 가 필요합니다")
    if n - 3 < 1:
        raise ValueError("ancova_baseline: 잔차 자유도가 부족합니다 (n ≥ 4 필요)")
    y = list(values_a) + list(values_b)
    g = [1.0] * n1 + [0.0] * n2
    c = list(cov_a) + list(cov_b)
    cols = [[1.0] * n, g, c]

    xtx = [[math.fsum(ci * cj for ci, cj in zip(a, b)) for b in cols] for a in cols]
    xty = [math.fsum(ci * yi for ci, yi in zip(a, y)) for a in cols]
    beta = _solve3(xtx, xty)

    fitted = [beta[0] + beta[1] * gi + beta[2] * ci for gi, ci in zip(g, c)]
    resid = [yi - fi for yi, fi in zip(y, fitted)]
    df = n - 3
    sse = math.fsum(r * r for r in resid)
    mse = sse / df
    if mse <= 0.0:
        raise ValueError("ancova_baseline: 잔차분산이 0 입니다")
    # (X'X)^-1 의 [1][1] 성분 = 군더미 계수의 분산 배수
    unit = _solve3(xtx, [0.0, 1.0, 0.0])
    var_beta = mse * unit[1]
    if var_beta <= 0.0:
        raise ValueError("ancova_baseline: 계수 분산이 0 이하입니다")
    t = beta[1] / math.sqrt(var_beta)
    return TestResult(
        "ANCOVA(기저보정) t", t, student_t_two_sided(t, df), df, "정확", n,
        {"보정된차이": beta[1], "MSE": mse, "기울기": beta[2]},
    )


def quade_rank_ancova(
    values_a: Sequence[float],
    cov_a: Sequence[float],
    values_b: Sequence[float],
    cov_b: Sequence[float],
) -> TestResult:
    """Quade(1967) 순위 ANCOVA — 결과와 공변량을 순위로 바꾼 뒤 잔차를 비교.

    비모수 축에서 `--covariate-baseline` 을 **조용히 무시하지 않기** 위한
    구현이다. 순위 잔차에 대한 합동 t 를 쓰고 자유도는 n − 3 으로 둔다
    (공변량 1개 추정을 반영). 이 df 선택을 리포트에 명시한다.
    """
    n1, n2 = len(values_a), len(values_b)
    n = n1 + n2
    if n1 < 2 or n2 < 2:
        raise ValueError("quade_rank_ancova: 각 군 n ≥ 2 가 필요합니다")
    if n - 3 < 1:
        raise ValueError("quade_rank_ancova: 자유도가 부족합니다 (n ≥ 4 필요)")
    y = list(values_a) + list(values_b)
    c = list(cov_a) + list(cov_b)
    ry = rankdata(y)
    rc = rankdata(c)
    mrc = mean(rc)
    sxx = math.fsum((v - mrc) ** 2 for v in rc)
    if sxx <= 0.0:
        raise SingularModel("공변량 순위의 분산이 0 입니다")
    mry = mean(ry)
    sxy = math.fsum((a - mrc) * (b - mry) for a, b in zip(rc, ry))
    slope = sxy / sxx
    resid = [yi - (mry + slope * (ci - mrc)) for yi, ci in zip(ry, rc)]
    ra, rb = resid[:n1], resid[n1:]
    df = n - 3
    ss = math.fsum((v - mean(ra)) ** 2 for v in ra) + math.fsum(
        (v - mean(rb)) ** 2 for v in rb
    )
    sp2 = ss / df
    if sp2 <= 0.0:
        raise ValueError("quade_rank_ancova: 잔차 합동분산이 0 입니다")
    diff = mean(ra) - mean(rb)
    t = diff / math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    return TestResult(
        "Quade 순위 ANCOVA", t, student_t_two_sided(t, df), df, "순위 잔차 t", n,
        {"순위잔차차이": diff},
    )
