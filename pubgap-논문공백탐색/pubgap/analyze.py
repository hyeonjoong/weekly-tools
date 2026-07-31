"""연구 동향·연구공백 분석 — 순수 함수 모음(오프라인 테스트 가능).

핵심 아이디어
-------------
- **동향(trend)**: 연도별 발행량, 초기 대비 최근 성장.
- **부상/쇠퇴 주제**: 전체 기간을 초기/최근 두 구간으로 나눠, 각 MeSH 주제가
  차지하는 '비중(share)'의 변화를 본다. 최근 비중이 크게 오른 주제 = 부상.
- **연구공백(gap) = 저조 조합**: 개별적으로는 자주 등장하는 두 주제가
  '함께'는 기대보다 훨씬 드물게 나타나면(관측/기대 = lift 가 낮으면),
  아직 덜 엮인 조합 → 논문 각도 후보. (연관규칙의 lift, 문헌기반발견/Swanson
  ABC 모델과 같은 계열의 휴리스틱.)
- **근거 공백(evidence gap)**: 주제 조합이 아니라 *연구 설계* 축의 공백.
  PubMed PublicationType 으로 각 논문의 근거 수준(메타분석/RCT/임상시험/관찰/
  증례/종설)을 판정해, "논문은 많은데 개입연구(RCT·임상시험)는 거의 없는 주제"를
  찾는다. 임상·제약 연구자에게 이는 곧 '시험을 설계할 자리'다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from math import comb, erf, exp, lgamma, log, log1p, log2, sqrt
from typing import Dict, List, Optional, Sequence, Tuple

from .records import Article


# PubMed 가 임상 논문 대부분에 자동으로 다는 '체크 태그'(연구 주제가 아니라 대상·성별·
# 연령·종을 나타내는 색인 항목). 그대로 두면 top_mesh·부상/쇠퇴·공백을 전부 오염시키므로
# (예: "Male 이 쇠퇴 주제") 기본적으로 분석에서 제외한다. --include-check-tags 로 해제.
CHECK_TAGS = frozenset({
    "Humans", "Animals",
    "Male", "Female",
    "Adult", "Middle Aged", "Aged", "Aged, 80 and over", "Young Adult",
    "Adolescent", "Child", "Child, Preschool", "Infant", "Infant, Newborn",
    "Pregnancy",
    "Mice", "Rats", "Dogs", "Rabbits", "Swine", "Cattle",
    "Mice, Inbred C57BL", "Rats, Sprague-Dawley", "Rats, Wistar",
    "Retrospective Studies", "Prospective Studies", "Cross-Sectional Studies",
    "Follow-Up Studies", "Reproducibility of Results", "Time Factors",
})

# 연구 *주제* 가 아니라 **방법론·연구설계·통계** 를 가리키는 MeSH descriptor.
# 실제 PubMed 코퍼스를 돌려 보면 이들이 상위 주제를 절반 가까이 차지하고, 서로 짝지어져
# "Randomized Controlled Trials as Topic × Surveys and Questionnaires" 같은 무의미한
# '공백'을 최상위로 밀어올린다. 체크 태그와 같은 이유로 기본 제외한다.
# (`... as Topic` 으로 끝나는 descriptor 는 정의상 방법론 표제어라 접미사 규칙으로도 잡는다.)
METHOD_TAGS = frozenset({
    "Treatment Outcome", "Research Design", "Sensitivity and Specificity",
    "Predictive Value of Tests", "Severity of Illness Index", "Pilot Projects",
    "Double-Blind Method", "Single-Blind Method", "Random Allocation",
    "Sample Size", "Data Interpretation, Statistical", "Logistic Models",
    "Regression Analysis", "Multivariate Analysis", "Analysis of Variance",
    "Odds Ratio", "Risk Assessment", "Risk Factors",
    "Surveys and Questionnaires", "Questionnaires",
    "Longitudinal Studies", "Cohort Studies", "Case-Control Studies",
    "Feasibility Studies", "Patient Selection", "Sample Size",
    "Databases, Factual", "Reproducibility of Results",
})
_AS_TOPIC_SUFFIX = " as Topic"


def is_non_topical(term: str) -> bool:
    """그 MeSH descriptor 가 '연구 주제'가 아니라 색인·방법론 표제어인가."""
    return (
        term in CHECK_TAGS
        or term in METHOD_TAGS
        or term.endswith(_AS_TOPIC_SUFFIX)
    )


def strip_check_tags(articles: Sequence["Article"]) -> List["Article"]:
    """각 논문의 분석용 주제(mesh)에서 색인용 체크 태그·방법론 표제어를 제거한 새 리스트."""
    from dataclasses import replace

    out: List[Article] = []
    for a in articles:
        filtered = [t for t in a.mesh if not is_non_topical(t)]
        out.append(replace(a, mesh=filtered) if len(filtered) != len(a.mesh) else a)
    return out


def drop_terms(articles: Sequence["Article"], terms: Sequence[str]) -> List["Article"]:
    """지정한 주제어를 분석에서 제외한 새 리스트(대소문자 무시).

    검색어 자체가 거의 모든 논문에 붙는 경우(예: 'sleep' 을 검색하면 MeSH 'Sleep' 이
    90% 논문에 등장)가 흔하다. 그런 주제는 상위 K 를 차지하면서 자명한 조합만 만들어
    공백 목록을 희석하므로, 연구자가 직접 빼고 다시 볼 수 있어야 한다.
    """
    from dataclasses import replace

    drop = {t.strip().lower() for t in terms if t and t.strip()}
    if not drop:
        return list(articles)
    out: List[Article] = []
    for a in articles:
        filtered = [t for t in a.mesh if t.lower() not in drop]
        out.append(replace(a, mesh=filtered) if len(filtered) != len(a.mesh) else a)
    return out


# --------------------------------------------------------------------------- #
# 기본 집계
# --------------------------------------------------------------------------- #
def yearly_counts(articles: Sequence[Article]) -> Dict[int, int]:
    """연도 → 발행 편수(연도 오름차순). 연도 미상은 제외."""
    c = Counter(a.year for a in articles if a.year is not None)
    return dict(sorted(c.items()))


def year_span(articles: Sequence[Article]) -> Optional[Tuple[int, int]]:
    years = [a.year for a in articles if a.year is not None]
    if not years:
        return None
    return (min(years), max(years))


def top_journals(articles: Sequence[Article], n: int = 10) -> List[Tuple[str, int]]:
    """저널별 편수 상위 n(결정론적: 편수 내림차순, 동률이면 저널명 오름차순)."""
    c = Counter(a.journal for a in articles if a.journal)
    return _ranked(c)[:n]


def _mesh_freq(articles: Sequence[Article]) -> Counter:
    """주제(MeSH descriptor)별 '이 주제를 단 논문 수'."""
    c: Counter = Counter()
    for a in articles:
        for term in set(a.mesh):  # 논문 내 중복 방지
            c[term] += 1
    return c


def _ranked(freq: Counter) -> List[Tuple[str, int]]:
    """빈도 내림차순, 동률이면 주제명 오름차순 — 실행 간 재현성 보장.

    (Counter.most_common 은 동률 시 삽입순 → set 해시 랜덤화로 뒤바뀔 수 있어
    직접 결정론적 정렬을 쓴다.)
    """
    return sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))


def top_mesh(articles: Sequence[Article], n: int = 20) -> List[Tuple[str, int]]:
    """주제별 논문 수 상위 n개(결정론적 정렬)."""
    return _ranked(_mesh_freq(articles))[:n]


def _log_comb(a: int, b: int) -> float:
    """log C(a, b) — lgamma 기반(큰 N 에서도 오버플로 없음)."""
    if b < 0 or b > a:
        return float("-inf")
    return lgamma(a + 1) - lgamma(b + 1) - lgamma(a - b + 1)


def hypergeom_lower_tail(N: int, K: int, n: int, k: int) -> float:
    """초기하분포 하단 꼬리확률 P(X <= k).

    전체 N편 중 주제 A를 단 논문 K편, 주제 B를 단 논문 n편일 때, 둘을 함께 단
    논문이 k편 '이하'로 관측될 확률. 값이 작을수록 '기대보다 유의하게 덜 엮임'
    = 통계적으로 뒷받침되는 공백.

    큰 N(수천 편) 에서 comb() 정수가 float 범위를 넘어 OverflowError 가 나던 문제를
    피하려, 확률을 log 공간(math.lgamma)에서 합산한다. 표본이 작을 땐(<=60) 정확한
    정수 comb 로 계산해 부동소수 오차 없이 정확값을 준다.
    """
    if K < 0 or n < 0:
        raise ValueError(f"음수 개수는 허용되지 않습니다: K={K}, n={n}")
    if N <= 0 or K > N or n > N:
        return 1.0
    if k < 0:
        return 0.0
    kmax = min(K, n)  # 관측 상한(support 상단)
    if k >= kmax:
        return 1.0  # 전체 꼬리 = 1 (부동소수 오차 없이 정확히)
    lo = max(0, n - (N - K))  # 가능한 최소 동시등장
    if k < lo:
        return 0.0

    # 작은 표본: 정확한 정수 연산(빠르고 정확).
    if N <= 60:
        denom = comb(N, n)
        if denom == 0:
            return 1.0
        total = sum(comb(K, i) * comb(N - K, n - i) for i in range(lo, k + 1))
        return min(1.0, total / denom)

    # 큰 표본: log 공간 합산으로 오버플로 회피.
    log_denom = _log_comb(N, n)
    total = 0.0
    for i in range(lo, k + 1):
        total += exp(_log_comb(K, i) + _log_comb(N - K, n - i) - log_denom)
    return min(1.0, total)


def _hyp_pmf(N: int, K: int, n: int, k: int) -> float:
    """초기하 확률질량 P(X=k) — log 공간(오버플로 안전)."""
    if k < 0 or k > K or k > n or (n - k) > (N - K):
        return 0.0
    return exp(_log_comb(K, k) + _log_comb(N - K, n - k) - _log_comb(N, n))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 분할표 [[a,b],[c,d]] 의 Fisher 정확검정 양측 p-value.

    부상/쇠퇴 주제의 '최근 vs 초기' 등장 여부가 우연 이상인지 판단하는 데 쓴다.
    관측 표의 확률 이하인 모든 표의 확률을 합산(표준 양측 정의). 순수 표준 라이브러리.
    """
    if min(a, b, c, d) < 0:
        # 음수 칸은 호출부의 계산 실수(예: 잘못된 나머지 집단 크기)를 뜻한다.
        # 조용히 0.0(=최대 유의)을 돌려주면 그 버그가 '아주 유의한 발견'으로 둔갑한다.
        raise ValueError(f"2×2 표에 음수 칸이 있습니다: [[{a},{b}],[{c},{d}]]")
    N = a + b + c + d
    K = a + c          # 그 주제를 단 총 편수
    n = a + b          # 최근 구간 편수
    if N <= 0 or K == 0 or K == N or n == 0 or n == N:
        return 1.0
    lo = max(0, n - (N - K))
    hi = min(n, K)
    p_obs = _hyp_pmf(N, K, n, a)
    tol = p_obs * (1.0 + 1e-7)
    total = 0.0
    for k in range(lo, hi + 1):
        pk = _hyp_pmf(N, K, n, k)
        if pk <= tol:
            total += pk
    return min(1.0, total)


def benjamini_hochberg(pvalues: Sequence[float]) -> List[float]:
    """Benjamini–Hochberg FDR 보정: p-value 리스트 → q-value 리스트(같은 순서).

    다수의 주제쌍을 동시에 검정하면 우연히 유의한 p 가 섞이므로, 발견율(FDR)을
    통제하도록 보정한다. q_i = min_{j>=rank(i)} ( p_(j) * m / j ) 를 계단식(step-up)
    으로 계산하며, 단조성을 위해 뒤(큰 p)에서 앞으로 누적 최소를 취한다.
    입력 순서 그대로의 q-value 를 돌려준다(외부 의존성 없음).
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])  # p 오름차순 인덱스
    q = [1.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):  # 큰 p 부터 step-up
        idx = order[rank - 1]
        val = pvalues[idx] * m / rank
        running_min = min(running_min, val)
        q[idx] = min(running_min, 1.0)
    return q


def _normal_cdf(z: float) -> float:
    """표준정규 누적분포 Φ(z) — math.erf 만 사용."""
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


# --------------------------------------------------------------------------- #
# 정확 신뢰구간 — 점추정만으로는 "이 공백이 견고한가"를 답할 수 없다
# --------------------------------------------------------------------------- #
# 표에 lift 0.12 라고만 적으면, 그것이 관측 1편/기대 8편(꽤 견고)인지 관측 0편/기대
# 2편(한 편만 색인돼도 뒤집힘)인지 구분되지 않는다. 아래 두 함수로 **정확(exact)**
# 신뢰구간을 붙여, 작은 표본에서의 불확실성을 리포트가 숨기지 않게 한다.
# 외부 의존성 없이(정규화 불완전 감마·베타를 직접 구현) 계산한다.
_BETACF_MAX_ITER = 300
_TINY = 1e-300
_EPS = 3e-16


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타의 연분수(모던 Lentz 알고리즘)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    h = d
    for m in range(1, _BETACF_MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def reg_inc_beta(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타 I_x(a, b) ∈ [0,1] — 이항분포 CDF 의 닫힌 형태."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if a <= 0.0 or b <= 0.0:
        raise ValueError(f"a, b 는 양수여야 합니다: a={a}, b={b}")
    front = exp(
        lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log1p(-x)
    )
    # 연분수는 x 가 작을 때 빨리 수렴한다. 큰 x 는 대칭관계로 옮겨 계산하되,
    # **재귀 호출은 쓰지 않는다** — x 가 정확히 경계값이면 두 분기가 서로를 계속
    # 호출해 RecursionError 가 난다(실제로 a=b, x=0.5 에서 발생했다).
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, front * _betacf(a, b, x) / a)
    return max(0.0, min(1.0, 1.0 - front * _betacf(b, a, 1.0 - x) / b))


def _beta_ppf(p: float, a: float, b: float) -> float:
    """I_x(a,b) = p 를 만족하는 x — 이분법(단조 함수라 항상 수렴)."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if reg_inc_beta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """이항비율의 Clopper–Pearson **정확** 신뢰구간 (기본 95%).

    'RCT 3/12편 = 25%' 같은 값은 표본이 작을수록 신뢰구간이 넓다(여기서는 5~57%).
    구간 없이 25% 만 적으면 독자가 그 수치를 과신한다. 정규근사(Wald)는 0% 나 100%
    에서 폭이 0 이 되는 치명적 결함이 있어, 경계에서도 정직한 정확구간을 쓴다.

    k=0 이면 하한 0, k=n 이면 상한 1(정의상 정확).
    """
    if n <= 0:
        return (0.0, 1.0)
    if k < 0 or k > n:
        raise ValueError(f"0 <= k <= n 이어야 합니다: k={k}, n={n}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 는 0 과 1 사이여야 합니다: {alpha}")
    half = alpha / 2.0
    lo = 0.0 if k == 0 else _beta_ppf(half, k, n - k + 1)
    hi = 1.0 if k == n else _beta_ppf(1.0 - half, k + 1, n - k)
    return (max(0.0, min(1.0, lo)), max(0.0, min(1.0, hi)))


def _reg_lower_gamma(s: float, x: float) -> float:
    """정규화 불완전 감마 P(s, x) — 급수/연분수(포아송 CDF 의 닫힌 형태)."""
    if x <= 0.0:
        return 0.0
    if s <= 0.0:
        return 1.0
    if x < s + 1.0:  # 급수 전개
        term = 1.0 / s
        total = term
        n = s
        for _ in range(1000):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * _EPS:
                break
        return min(1.0, total * exp(-x + s * log(x) - lgamma(s)))
    # 연분수(상단 불완전 감마) → 1 - Q
    b = x + 1.0 - s
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    q = exp(-x + s * log(x) - lgamma(s)) * h
    return max(0.0, min(1.0, 1.0 - q))


def _gamma_ppf(p: float, s: float) -> float:
    """P(s, x) = p 를 만족하는 x (scale=1) — 구간을 넓힌 뒤 이분법."""
    if p <= 0.0 or s <= 0.0:
        return 0.0
    if p >= 1.0:
        return float("inf")
    hi = max(1.0, s + 10.0 * sqrt(s) + 10.0)
    for _ in range(100):
        if _reg_lower_gamma(s, hi) >= p:
            break
        hi *= 2.0
    lo = 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _reg_lower_gamma(s, mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def poisson_count_ci(k: int, alpha: float = 0.05) -> Tuple[float, float]:
    """관측 편수 k 에 대한 포아송 평균의 **정확(Garwood)** 신뢰구간.

    L = gammaincinv(k, α/2), U = gammaincinv(k+1, 1−α/2) (χ² 형태와 동치).
    k=0 → (0, 3.689) 처럼 관측이 0 이어도 상한이 유한하다.
    """
    if k < 0:
        raise ValueError(f"k 는 0 이상이어야 합니다: {k}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 는 0 과 1 사이여야 합니다: {alpha}")
    half = alpha / 2.0
    lo = 0.0 if k == 0 else _gamma_ppf(half, float(k))
    hi = _gamma_ppf(1.0 - half, float(k) + 1.0)
    return (lo, hi)


def lift_ci(observed: int, expected: float, alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """lift(=관측/기대)의 신뢰구간 — 관측 편수의 포아송 정확구간을 기대값으로 나눈 값.

    역학에서 표준화발생비(SIR/SMR)에 쓰는 것과 **같은 방식**이다: 기대값은 고정으로
    보고 관측 편수만 확률변수로 취급한다. 초기하(유한모집단)보다 약간 보수적이라
    구간이 조금 넓지만, 기대값이 표본 크기에 비해 작은(여기서는 항상 그렇다) 상황에서
    포아송 근사는 매우 정확하다. 기대값이 0 이면 비를 정의할 수 없어 (None, None).
    """
    if expected is None or expected <= 0:
        return (None, None)
    lo, hi = poisson_count_ci(observed, alpha=alpha)
    return (lo / expected, hi / expected)


@dataclass
class TrendTest:
    """Mann–Kendall 단조추세 검정 결과."""

    n: int
    tau: float          # Kendall's tau-b (동률 보정)
    s: int              # Mann–Kendall S 통계량
    z: float            # 정규근사 검정통계량(연속성 보정)
    p_value: float      # 양측 p-value
    direction: str      # 'increasing' | 'decreasing' | 'flat' | 'insufficient'


def mann_kendall(values: Sequence[float]) -> TrendTest:
    """연도순 시계열의 Mann–Kendall 단조추세 검정(정규근사, 동률 보정).

    S = Σ_{i<j} sign(x_j − x_i). Var(S) 는 표본수 n 과 동률군 크기로 보정.
    z 는 연속성 보정 후, p 는 양측. 표본 n<3 이면 'insufficient'.
    발행량이 시간에 따라 유의하게 증가/감소하는지를 (초기/최근 2분할보다) 견고하게 본다.
    """
    x = list(values)
    n = len(x)
    if n < 3:
        return TrendTest(n=n, tau=0.0, s=0, z=0.0, p_value=1.0, direction="insufficient")

    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = x[j] - x[i]
            if d > 0:
                s += 1
            elif d < 0:
                s -= 1

    # 동률군 보정항
    tie_counts = Counter(x)
    tie_term = sum(t * (t - 1) * (2 * t + 5) for t in tie_counts.values())
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0

    n0 = n * (n - 1) / 2.0
    # tau-b: 동률을 고려한 분모
    denom = sqrt((n0 - sum(t * (t - 1) / 2.0 for t in tie_counts.values())) * n0)
    tau = (s / denom) if denom > 0 else 0.0

    if var_s <= 0:
        z = 0.0
    elif s > 0:
        z = (s - 1) / sqrt(var_s)
    elif s < 0:
        z = (s + 1) / sqrt(var_s)
    else:
        z = 0.0

    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    p = min(1.0, max(0.0, p))

    if s > 0 and p < 0.05:
        direction = "increasing"
    elif s < 0 and p < 0.05:
        direction = "decreasing"
    else:
        direction = "flat"
    return TrendTest(n=n, tau=tau, s=s, z=z, p_value=p, direction=direction)


def yearly_series_dense(counts: Dict[int, int]) -> List[Tuple[int, int]]:
    """연도별 편수 dict → 최소~최대 연도까지 빠진 해를 0 으로 채운 (연도, 수) 리스트.

    Mann–Kendall 추세검정은 균등한 시간축이 필요하므로, 관측된 연도만이 아니라
    구간 전체를 조밀하게 편다(빠진 해 = 0편).
    """
    if not counts:
        return []
    years = sorted(counts)
    lo, hi = years[0], years[-1]
    return [(y, counts.get(y, 0)) for y in range(lo, hi + 1)]


def trend_test(counts: Dict[int, int]) -> TrendTest:
    """연도별 편수에 대한 Mann–Kendall 추세검정(조밀 시계열 기준)."""
    series = yearly_series_dense(counts)
    return mann_kendall([v for _, v in series])


# --------------------------------------------------------------------------- #
# 부상 / 쇠퇴
# --------------------------------------------------------------------------- #
@dataclass
class TermTrend:
    term: str
    early_count: int
    recent_count: int
    early_share: float
    recent_share: float
    delta: float  # recent_share - early_share
    p_value: Optional[float] = None  # Fisher 정확검정(최근vs초기 등장) 양측 p — 표시행에만 채움
    q_value: Optional[float] = None  # 표시한 행들에 BH-FDR 를 적용한 q


def split_point(articles: Sequence[Article]) -> Optional[int]:
    """초기/최근을 가르는 연도. year >= split_point 이면 '최근' 구간.

    (min_year + max_year + 1) // 2 로 정한다(연도 미상 논문은 무시).
    """
    span = year_span(articles)
    if span is None:
        return None
    lo, hi = span
    return (lo + hi + 1) // 2


def term_trends(
    articles: Sequence[Article],
    split: Optional[int] = None,
    min_total: int = 2,
) -> List[TermTrend]:
    """각 주제의 초기/최근 비중 변화. delta 내림차순 정렬.

    - split 미지정 시 split_point() 사용.
    - min_total: 전체 등장 편수가 이 값 미만인 주제는 노이즈로 제외.
    - 연도 미상 논문은 두 구간 어디에도 넣지 않는다.
    """
    if split is None:
        split = split_point(articles)
    if split is None:
        return []

    # 한 번의 스캔으로 주제별 초기/최근 등장 편수를 센다(역색인).
    # 이전엔 주제마다 전체 논문을 두 번씩 훑어(O(주제수×논문수)) 수천 주제에서
    # 몇 분씩 걸렸다. 여기서는 O(Σ 주제수_per_article) 로 선형.
    early_c: Counter = Counter()
    recent_c: Counter = Counter()
    n_early = 0
    n_recent = 0
    for a in articles:
        if a.year is None:
            continue
        if a.year < split:
            n_early += 1
            for t in set(a.mesh):
                early_c[t] += 1
        else:
            n_recent += 1
            for t in set(a.mesh):
                recent_c[t] += 1
    n_early = n_early or 1  # 0 division 방지
    n_recent = n_recent or 1

    out: List[TermTrend] = []
    for term in set(early_c) | set(recent_c):
        ec = early_c.get(term, 0)
        rc = recent_c.get(term, 0)
        if ec + rc < min_total:
            continue
        es = ec / n_early
        rs = rc / n_recent
        out.append(TermTrend(term, ec, rc, es, rs, rs - es))
    out.sort(key=lambda t: (-t.delta, t.term))
    return out


def emerging(trends: Sequence[TermTrend], n: int = 8) -> List[TermTrend]:
    return [t for t in trends if t.delta > 0][:n]


def declining(trends: Sequence[TermTrend], n: int = 8) -> List[TermTrend]:
    dec = [t for t in trends if t.delta < 0]
    dec.sort(key=lambda t: (t.delta, t.term))  # 가장 많이 떨어진 순
    return dec[:n]


# --------------------------------------------------------------------------- #
# 연구공백(저조 조합)
# --------------------------------------------------------------------------- #
@dataclass
class GapPair:
    term_a: str
    term_b: str
    observed: int
    expected: float
    lift: float  # observed / expected
    count_a: int
    count_b: int
    p_value: float  # 초기하 하단꼬리 P(X<=observed): 작을수록 유의한 공백
    q_value: float = 1.0  # 다중검정(BH-FDR) 보정 후 q-value
    deficit: float = 0.0   # 기대−관측 = '있었어야 하는데 없는 논문 편수'(연구자 단위 효과크기)
    jaccard: float = 0.0   # |A∩B| / |A∪B| — 공동출현의 절대적 겹침 정도
    cosine: float = 0.0    # Ochiai/코사인 = obs / sqrt(cA·cB) — 공어(co-word) 분석 표준
    npmi: float = 0.0      # 정규화 점별상호정보량 ∈ [-1,1]; obs=0 이면 -1(완전 배타)
    # lift 의 95% 정확 신뢰구간(포아송/Garwood). 관측 0편·기대 2편과 관측 1편·기대
    # 8편은 lift 가 비슷해도 견고함이 전혀 다르다 — 구간이 그 차이를 드러낸다.
    lift_ci_low: Optional[float] = None
    lift_ci_high: Optional[float] = None
    observed_early: int = 0    # 초기 구간의 동시등장 편수
    observed_recent: int = 0   # 최근 구간의 동시등장 편수
    gap_trend: str = "unknown"  # 'closing'(메워지는 중) | 'widening' | 'stable' | 'unknown'
    pmids_a: List[str] = field(default_factory=list)      # A만 다룬 대표 논문(검증용)
    pmids_b: List[str] = field(default_factory=list)      # B만 다룬 대표 논문
    pmids_both: List[str] = field(default_factory=list)   # 둘 다 다룬 논문(observed>0일 때)
    bridges: List = field(default_factory=list)           # Swanson ABC 가교: [[C, A&C수, C&B수], ...]
    # 대표 논문의 **서지 정보**(id, 연도, 저널, 제목). 리포트는 "대표 논문을 직접
    # 확인하라"고 말하는데, 번호만 주면 그 조언을 실행하려고 브라우저를 아홉 번
    # 열어야 한다. 제목까지 주면 대부분 그 자리에서 판단할 수 있다.
    refs_a: List[List] = field(default_factory=list)
    refs_b: List[List] = field(default_factory=list)
    refs_both: List[List] = field(default_factory=list)
    # 함께 다룬 논문들의 **연구 설계 구성**({tier: 편수}). "관찰연구만 12편, RCT 0편"
    # 과 "아무것도 0편"은 전혀 다른 상황이다 — 앞은 시험을 설계할 자리, 뒤는 대개
    # 색인 artifact 이거나 애초에 성립하지 않는 조합이다.
    both_tiers: Dict[str, int] = field(default_factory=dict)
    # 어휘상 상하위어(부모–자식)로 의심되는 쌍인가 — 한쪽 이름이 다른 쪽에 포함.
    hierarchy_suspect: bool = False
    n_early: int = 0     # 초기 구간 논문 수(추이를 비율로 읽기 위한 분모)
    n_recent: int = 0    # 최근 구간 논문 수


def _pair_metrics(n: int, ca: int, cb: int, observed: int) -> Tuple[float, float, float]:
    """(jaccard, cosine/Ochiai, npmi) — 공어(co-word) 분석의 표준 연관 지표 3종.

    lift 하나만 보면 '기대'가 아주 작을 때 값이 요동친다(예: 기대 2.0, 관측 0 → lift 0).
    서로 다른 정규화를 쓰는 지표를 함께 보면 그 공백이 규모의 문제인지 배타성의
    문제인지 구분할 수 있다.

    - Jaccard = obs / (cA + cB − obs): 합집합 대비 겹침. 규모에 둔감.
    - Ochiai(cosine) = obs / sqrt(cA·cB): 문헌계량 공어분석의 표준 유사도.
    - nPMI = PMI / (−log2 p(A,B)) ∈ [−1, 1]. obs=0 이면 정의상 −1(완전 배타),
      독립이면 0, 항상 함께 나오면 +1. lift 의 로그를 정보량으로 정규화한 값이다.
    """
    union = ca + cb - observed
    jaccard = observed / union if union > 0 else 0.0
    cosine = observed / sqrt(ca * cb) if ca > 0 and cb > 0 else 0.0
    if n <= 0 or ca <= 0 or cb <= 0:
        npmi = 0.0
    elif observed <= 0:
        npmi = -1.0
    else:
        p_ab = observed / n
        p_a = ca / n
        p_b = cb / n
        denom = -log2(p_ab)
        npmi = 1.0 if denom <= 0 else (log2(p_ab / (p_a * p_b)) / denom)
        npmi = max(-1.0, min(1.0, npmi))
    return jaccard, cosine, npmi


# 이 편수 미만의 동시등장으로는 '메워지는 중/벌어지는 중'을 말할 수 없다.
# (실측에서 1편짜리 차이가 그대로 '↗ 메워짐' 으로 찍혀 후보를 잘못 탈락시켰다.)
GAP_TREND_MIN_OBS = 3


def _classify_gap_trend(
    obs_early: int,
    obs_recent: int,
    n_early: int,
    n_recent: int,
    observed: Optional[int] = None,
) -> str:
    """공백의 시간 추이를 판정.

    반환값:
      - `'empty'`      : 두 구간 모두 동시등장 0편 = **완전 공백**(가장 강한 신호).
      - `'closing'`    : 최근 구간의 동시등장 *비율* 이 더 높다(남들이 들어오는 중).
      - `'widening'`   : 최근 구간의 비율이 더 낮다(여전히 비어 있음).
      - `'stable'`     : 비율이 같다.
      - `'unknown'`    : 판단 불가 — 한쪽 구간이 비었거나, 동시등장이 너무 적거나
                         (`GAP_TREND_MIN_OBS` 미만), 연도 미상 논문이 많아 구간에
                         배정된 편수가 전체 동시등장보다 적을 때.

    두 구간의 동시등장을 **구간 논문 수로 정규화해** 비교한다. 정규화하지 않으면
    최근 구간에 논문이 많다는 이유만으로 전부 'closing' 으로 보인다.

    실무적 의미: 이미 메워지는 중인 공백은 남이 먼저 들어간 자리라 선점 가치가 낮고,
    벌어지는(또는 완전히 빈) 공백은 여전히 비어 있다는 뜻이다.
    """
    dated = obs_early + obs_recent
    if observed is not None and observed > 0 and dated < observed:
        # 연도 미상 논문이 동시등장에 섞여 있다 — 구간 비교는 표본의 일부만 본 것이라
        # 방향을 단언하면 안 된다.
        return "unknown"
    if dated == 0:
        # '한 번도 함께 나온 적이 없다'는 **시간과 무관한** 사실이다. 구간이 하나뿐인
        # 코퍼스(전부 같은 해)라고 해서 이 가장 강한 신호를 '판단불가'로 감추면 안 된다.
        return "empty"
    if n_early <= 0 or n_recent <= 0:
        return "unknown"
    if dated < GAP_TREND_MIN_OBS:
        return "unknown"
    r_early = obs_early / n_early
    r_recent = obs_recent / n_recent
    if r_recent > r_early:
        return "closing"
    if r_recent < r_early:
        return "widening"
    return "stable"


def _cooccurrence(
    articles: Sequence[Article], terms: Sequence[str]
) -> Dict[Tuple[str, str], int]:
    """주어진 주제 집합 안에서만 쌍별 공동출현 수를 한 번의 스캔으로 센다.

    각 논문마다 관심 주제의 교집합만 뽑아 쌍을 세므로 O(Σ m_i²) — 논문 수 N 과
    top_k 가 커도 (top_k² × N) 전수 스캔보다 훨씬 빠르고, 큰 입력에서도 안정적이다.
    키는 항상 정렬된 (a, b) 튜플.
    """
    term_set = set(terms)
    pair_obs: Counter = Counter()
    for art in articles:
        present = sorted(t for t in set(art.mesh) if t in term_set)
        for a, b in combinations(present, 2):
            pair_obs[(a, b)] += 1
    return pair_obs


def _enrich_gap(
    articles: Sequence[Article],
    a: str,
    b: str,
    n_examples: int = 3,
    bridge_top_n: int = 3,
    split: Optional[int] = None,
    freq: Optional[Counter] = None,
):
    """(내부) 한 gap 쌍에 대해 대표 PMID·Swanson ABC 가교 주제 C·시간 추이를 계산.

    가교(bridge): A 와 함께 자주 나오고(A&C), B 와도 자주 나오지만(C&B), A–B 자체는
    드문 제3의 주제 C. "A는 C를 통해 B와 연결된다"는 기전 서사를 만들어, 리뷰어에게
    '왜 이 주제인가'를 설명할 근거가 된다. 순위는 `_rank_bridges`(lift 곱) 참고.

    반환: dict(pmids_*, refs_*, both_tiers, bridges, observed_early, observed_recent).
    `split` 이 주어지면 동시등장 편수를 초기/최근으로 나눠 세어 공백이 메워지는
    중인지 판정할 재료를 만든다(연도 미상 논문은 어느 구간에도 넣지 않는다).
    """
    pa: List[str] = []
    pb: List[str] = []
    both: List[str] = []
    ref_a: List[list] = []
    ref_b: List[list] = []
    ref_both: List[list] = []
    both_tiers: Counter = Counter()
    ac: Counter = Counter()
    cb: Counter = Counter()
    obs_early = 0
    obs_recent = 0

    def _ref(art) -> list:
        return [art.pmid, art.year, art.journal, art.title]

    if freq is None:
        freq = _mesh_freq(articles)
    n_total = len(articles)
    for art in articles:
        ms = set(art.mesh)
        has_a = a in ms
        has_b = b in ms
        if has_a and has_b:
            if len(both) < n_examples:
                both.append(art.pmid)
                ref_both.append(_ref(art))
            both_tiers[article_tier(art)] += 1
            if split is not None and art.year is not None:
                if art.year < split:
                    obs_early += 1
                else:
                    obs_recent += 1
        elif has_a and not has_b and len(pa) < n_examples:
            pa.append(art.pmid)
            ref_a.append(_ref(art))
        elif has_b and not has_a and len(pb) < n_examples:
            pb.append(art.pmid)
            ref_b.append(_ref(art))
        if bridge_top_n:
            if has_a:
                for c in ms:
                    if c != a and c != b:
                        ac[c] += 1
            if has_b:
                for c in ms:
                    if c != a and c != b:
                        cb[c] += 1
    bridges = _rank_bridges(ac, cb, freq, n_total, bridge_top_n)
    return {
        "pmids_a": pa, "pmids_b": pb, "pmids_both": both,
        "refs_a": ref_a, "refs_b": ref_b, "refs_both": ref_both,
        "both_tiers": dict(both_tiers), "bridges": bridges,
        "observed_early": obs_early, "observed_recent": obs_recent,
    }


# 가교 후보에서 뺄 '거의 모든 논문에 붙는' 주제의 유병률 상한(코퍼스의 80%). 대부분의
# 논문에 달린 주제(보통 검색어 자체)는 무엇과도 함께 나오므로 가교로서 정보가 0이다.
# 임계를 낮게 잡으면 좁은 분야에서 핵심 개념이 정당한 가교인데도 사라지므로, 아래 lift
# 기반 점수에 실질적 벌점을 맡기고 여기서는 '사실상 보편적인' 주제만 잘라낸다.
BRIDGE_MAX_PREVALENCE = 0.8
BRIDGE_MIN_SUPPORT = 2  # A&C, C&B 각각 최소 이만큼은 있어야 서사를 세울 수 있다


def _rank_bridges(
    ac: Counter, cb: Counter, freq: Counter, n_total: int, top_n: int
) -> List[list]:
    """Swanson ABC 가교 주제 C 를 **빈도가 아니라 연관 강도**로 순위 매긴다.

    이전에는 `min(A&C, C&B)` 원시 편수로 정렬해, 코퍼스에서 가장 흔한 주제(대개
    검색어 자체)가 항상 1위였다 — "A와 B는 당신이 검색한 그 단어를 통해 연결됩니다"
    라는 무의미한 서사가 나왔다. 이제 두 변의 **lift** 곱으로 매긴다:

        score = lift(A,C) × lift(C,B),  lift(X,C) = 관측(X&C) / (cX·cC/N)

    흔한 C 는 기대값이 커서 자동으로 벌점을 받고, A·B 둘 다와 *특이적으로* 엮이는
    C 가 올라온다. 유병률이 `BRIDGE_MAX_PREVALENCE`(80%)를 넘는 주제와 지지도가
    `BRIDGE_MIN_SUPPORT` 미만인 후보는 제외한다.
    """
    if top_n <= 0 or n_total <= 0:
        return []
    scored = []
    for c in set(ac) & set(cb):
        n_ac, n_cb = ac[c], cb[c]
        if n_ac < BRIDGE_MIN_SUPPORT or n_cb < BRIDGE_MIN_SUPPORT:
            continue
        c_count = freq.get(c, 0)
        if c_count <= 0 or c_count / n_total > BRIDGE_MAX_PREVALENCE:
            continue
        # lift 의 공통 인수(cA, cB)는 후보 간 상수라 순위에 영향이 없어 생략하고,
        # C 쪽 기대값만 나눠 준다: (n_ac/c_count) * (n_cb/c_count) * N².
        score = (n_ac * n_cb) / (c_count * c_count)
        scored.append((-score, -min(n_ac, n_cb), c, n_ac, n_cb))
    scored.sort()
    return [[c, n_ac, n_cb] for _, _, c, n_ac, n_cb in scored[:top_n]]


_WORD_RE = __import__("re").compile(r"[^a-z0-9]+")


def looks_hierarchical(term_a: str, term_b: str) -> bool:
    """두 주제어가 MeSH 트리에서 부모–자식일 가능성이 높은가(어휘 휴리스틱).

    `Sleep` × `Sleep, REM`, `Respiration` × `Respiration, Artificial`,
    `Heart Rate` × `Heart Rate, Fetal` 처럼 한쪽 이름이 다른 쪽에 **통째로** 들어가는
    쌍은 사실상 같은 개념의 상하위어다. 그런 쌍은 정의상 함께 색인되지 않으므로
    lift 가 낮게 나오지만 **연구 공백이 아니다** — 사용법 문서가 사용자에게 손으로
    걸러내라고 시키던 첫 번째 규칙이 바로 이것이라, 도구가 대신 표시해 준다.

    MeSH 트리 파일 없이 어휘만 보므로 완벽하지 않다(표시만 하고 제외하지 않는다).
    """
    wa = [w for w in _WORD_RE.split(term_a.lower()) if w]
    wb = [w for w in _WORD_RE.split(term_b.lower()) if w]
    if not wa or not wb or wa == wb:
        return False
    sa, sb = set(wa), set(wb)
    return sa < sb or sb < sa


# --gap-sort 로 고를 수 있는 정렬 키. 값이 작을수록(=튜플이 앞설수록) 위에 온다.
GAP_SORTS: Tuple[str, ...] = ("lift", "deficit", "q", "expected", "npmi")


def sort_gaps(gaps: List[GapPair], key: str = "deficit") -> List[GapPair]:
    """공백 목록을 지정한 기준으로 정렬(동률은 항상 결정론적으로 깨뜨린다).

    - `lift`     : 미개척 정도(관측/기대) 오름차순 — '얼마나 안 엮였나'.
    - `deficit`  : 기대−관측(편수) 내림차순 — '몇 편이 비어 있나'. **기본값.** 기대가
                   큰(=문헌이 두꺼워 근거가 탄탄한) 공백을 위로 올리므로, 실제 착수
                   후보를 고를 땐 lift 보다 이쪽이 실용적이다.
    - `q`        : BH-FDR q 오름차순 — 통계적으로 가장 견고한 순.
    - `expected` : 기대 동시등장 내림차순 — 분야 규모 순.
    - `npmi`     : 정규화 상호정보량 오름차순 — 가장 배타적인 조합 순.
    """
    if key not in GAP_SORTS:
        raise ValueError(f"알 수 없는 정렬 기준: {key!r} (가능: {', '.join(GAP_SORTS)})")
    keyfuncs = {
        "lift": lambda g: (g.lift, -g.expected, g.term_a, g.term_b),
        "deficit": lambda g: (-g.deficit, g.lift, g.term_a, g.term_b),
        "q": lambda g: (g.q_value, g.lift, -g.expected, g.term_a, g.term_b),
        "expected": lambda g: (-g.expected, g.lift, g.term_a, g.term_b),
        "npmi": lambda g: (g.npmi, -g.expected, g.term_a, g.term_b),
    }
    return sorted(gaps, key=keyfuncs[key])


def gap_pairs(
    articles: Sequence[Article],
    top_k: int = 12,
    min_expected: float = 2.0,
    max_lift: float = 0.5,
    n_examples: int = 3,
    bridge_top_n: int = 3,
    sort: str = "deficit",
) -> List[GapPair]:
    """빈출 상위 top_k 주제쌍 중 '저조 조합'을 lift 오름차순으로 반환.

    - 관측(observed): 두 주제를 함께 단 논문 수.
    - 기대(expected): 독립 가정 하 기대 동시등장 수 = count_a * count_b / N.
    - lift = observed / expected. lift<1 이면 기대보다 덜 엮인 것.
    - p_value: 초기하분포 하단꼬리 — '이만큼 덜 엮일' 확률(우연 여부 판단용).
    - q_value: 검정한 모든 후보쌍(기대>=min_expected)에 대해 BH-FDR 로 보정한 값.
      여러 쌍을 동시에 보므로 raw p 대신 q 로 판단하는 것이 정직하다.
    - min_expected: 기대값이 이 값 미만이면(애초에 만날 일이 드묾) 제외 —
      '충분히 만날 만한데도 안 만난' 조합만 공백으로 본다.
    - max_lift: 이 값 이하인 조합만 반환.
    """
    # 분모(N)는 **주제어를 하나라도 가진 논문 수**여야 한다.
    # MeSH 가 아직 안 붙은 논문(실제 PubMed 조회에서 30~40%가 흔하다)은 구조적으로
    # 어떤 주제쌍도 가질 수 없으므로, 이를 분모에 넣으면 기대값이 낮아지고 lift 가
    # 부풀려져 **진짜 공백이 가려진다**. 실측 예: N=299(전체) 대비 N=193(주제 보유)에서
    # 같은 쌍의 p 가 0.069 → 0.012 로 바뀌어 결론이 뒤집혔다.
    topical = [a for a in articles if a.mesh]
    n = len(topical)
    if n == 0:
        return []
    articles = topical

    freq = _mesh_freq(articles)
    top_terms = [t for t, _ in _ranked(freq)[:top_k]]  # 결정론적 상위 선택
    pair_obs = _cooccurrence(articles, top_terms)

    # 1) 기대>=min_expected 인 모든 후보에 대해 p 를 계산(= 실제로 수행한 검정 집합).
    candidates: List[GapPair] = []
    for a_term, b_term in combinations(top_terms, 2):
        ca, cb = freq[a_term], freq[b_term]
        expected = ca * cb / n
        if expected < min_expected:
            continue
        key = (a_term, b_term) if a_term < b_term else (b_term, a_term)
        observed = pair_obs.get(key, 0)
        lift = observed / expected if expected > 0 else 0.0
        p = hypergeom_lower_tail(n, ca, cb, observed)
        jac, cos, npmi = _pair_metrics(n, ca, cb, observed)
        ci_lo, ci_hi = lift_ci(observed, expected)
        candidates.append(
            GapPair(
                a_term, b_term, observed, expected, lift, ca, cb, p,
                deficit=expected - observed, jaccard=jac, cosine=cos, npmi=npmi,
                lift_ci_low=ci_lo, lift_ci_high=ci_hi,
            )
        )

    # 2) 검정한 후보 전체에 BH-FDR 를 적용해 q-value 를 채운다(필터 전에!).
    qs = benjamini_hochberg([g.p_value for g in candidates])
    for g, q in zip(candidates, qs):
        g.q_value = q

    # 3) lift 임계 통과분만 남겨 요청한 기준으로 정렬.
    out = sort_gaps([g for g in candidates if g.lift <= max_lift], sort)

    # 4) 살아남은 소수의 공백에만 대표 PMID·가교 주제·시간 추이를 채운다(비용은 작다).
    split = split_point(articles)
    n_early = sum(1 for a in articles if a.year is not None and split is not None and a.year < split)
    n_recent = sum(1 for a in articles if a.year is not None and split is not None and a.year >= split)
    for g in out:
        info = _enrich_gap(
            articles, g.term_a, g.term_b, n_examples=n_examples,
            bridge_top_n=bridge_top_n, split=split, freq=freq,
        )
        for key, value in info.items():
            setattr(g, key, value)
        g.gap_trend = _classify_gap_trend(
            g.observed_early, g.observed_recent, n_early, n_recent, observed=g.observed
        )
        g.hierarchy_suspect = looks_hierarchical(g.term_a, g.term_b)
        g.n_early, g.n_recent = n_early, n_recent
    return out


# --------------------------------------------------------------------------- #
# 연구 각도(MeSH 부주제어/qualifier) 공백 — '무엇을' 이 아니라 '어떻게' 의 축
# --------------------------------------------------------------------------- #
# PubMed 색인자는 주제어에 부주제어를 붙여 그 논문이 주제를 **어떤 각도로** 다뤘는지
# 표시한다: `Hypertension/drug therapy`, `Insomnia/therapy`, `Melatonin/adverse effects`.
# 임상·제약 연구자에게 이 축은 주제쌍만큼 중요하다 — "이 질환은 병태생리 논문만 잔뜩
# 있고 약물치료(drug therapy)·이상반응(adverse effects) 논문은 없다" 가 곧 개발 공백이다.
@dataclass
class AngleGap:
    """한 주제(term) × 한 연구각도(qualifier)의 저조 조합.

    **분석 단위는 논문이 아니라 '색인 표목(heading) = (논문, 주제어) 한 칸'** 이다.
    색인자는 논문마다 주제어를 여러 개 달고, 각 주제어에 부주제어를 붙인다. 따라서
    "이 각도가 이 주제에 붙었는가"는 표목 수준의 사건이고, 주변확률도 표목 수준에서
    세어야 같은 모집단이 된다(아래 `n_term`·`n_qualifier` 참고).
    """

    term: str
    qualifier: str
    n_term: int            # 그 주제가 **부주제어와 함께** 색인된 표목 수
    n_qualifier: int       # 그 각도가 붙은 표목 수(어느 주제에든)
    observed: int          # 그 주제에 **그 각도**가 붙은 표목 수
    expected: float        # 독립 가정 기대값 = n_term · n_qualifier / N(전체 표목 수)
    deficit: float         # 기대 − 관측
    lift: float            # 관측 / 기대
    p_value: float         # 초기하 하단꼬리
    q_value: float = 1.0   # BH-FDR
    lift_ci_low: Optional[float] = None
    lift_ci_high: Optional[float] = None
    # 그 주제가 **실제로** 많이 쓰인 각도 상위 3개 — "그럼 뭘 하고 있나"의 맥락.
    top_angles: List[List] = field(default_factory=list)
    # 이 조합이 MeSH 색인 규칙상 **가능해 보이는가**(휴리스틱, `_angle_plausible`).
    # False 라도 검정에서 빼지 않는다 — 표시 순서만 뒤로 미루고 표에 표시한다.
    plausible: bool = True


ANGLE_SORTS: Tuple[str, ...] = ("deficit", "lift", "q", "expected")


def article_angles(article: "Article") -> List[Tuple[str, str]]:
    """이 논문의 (주제, 각도) 쌍 중 **현재 분석 대상 주제**에 해당하는 것만.

    `strip_check_tags`/`drop_terms`/`--major-topics-only` 는 `mesh` 만 바꾸고
    `qualifiers` 는 원본 그대로 둔다. 각도 분석이 `qualifiers` 를 그대로 쓰면
    사용자가 뺀 주제가 각도 표에 되살아난다 — 여기서 한 번에 걸러 준다.
    """
    ms = set(article.mesh)
    out: List[Tuple[str, str]] = []
    seen = set()
    for t, q in article.qualifiers:
        if t in ms and (t, q) not in seen:
            seen.add((t, q))
            out.append((t, q))
    return out


def qualifier_coverage(articles: Sequence[Article]) -> Dict:
    """부주제어 색인 커버리지 — 각도 분석을 신뢰할 수 있는지 판단할 근거.

    RIS/CSV 내보내기나 아직 색인되지 않은 최신 논문은 부주제어가 아예 없다.
    커버리지를 밝히지 않으면 '각도 공백'이 '색인 부재'와 구분되지 않는다.
    """
    n = len(articles)
    n_q = 0
    quals: Counter = Counter()
    for a in articles:
        pairs = article_angles(a)
        if pairs:
            n_q += 1
            for q in {q for _t, q in pairs}:
                quals[q] += 1
    return {
        "n_articles": n,
        "n_with_qualifiers": n_q,
        "coverage": (n_q / n) if n else 0.0,
        "n_distinct": len(quals),
        "top_qualifiers": _ranked(quals)[:10],
    }


def _qualifier_families(
    quals_by_term: Dict[str, set],
    topics_by_qual: Dict[str, set],
    quals: Sequence[str],
) -> Dict[str, Counter]:
    """각 부주제어가 속한 '가족' — 그 각도를 쓰는 주제들이 **함께** 쓰는 다른 각도들.

    NLM 은 descriptor 범주별로 붙일 수 있는 부주제어를 정해 둔다 — 해부·생리 용어에는
    `/physiology`·`/drug effects`, 진단기법에는 `/methods`·`/instrumentation`, 질환에는
    `/therapy`·`/drug therapy` … 그래서 '진단기법 × /physiology' 같은 칸은 연구 공백이
    아니라 **애초에 색인될 수 없는 조합**이다.

    MeSH 규칙 파일을 넣지 않고도(무의존 유지) 이를 데이터에서 추정한다: 같은 범주의
    주제들은 같은 각도 어휘를 공유하므로, "Q 를 쓰는 주제들이 함께 쓰는 각도"를 Q 의
    가족으로 본다. 집합이 아니라 **Counter**(각 각도를 몇 개의 주제가 쓰는지)로 두는
    이유는 `_angle_plausible` 이 후보 주제 자신의 기여를 빼고(leave-one-out) 판단하기
    위해서다 — 그래야 판정이 '검정하려는 칸의 결과'에 의존하지 않는다.

    비용 주의: 이 함수는 **실제로 검정할 상위 각도(`quals`)에 대해서만** 계산한다.
    코퍼스 전체 각도로 돌리면 O(|Q|²) 로 부풀어(실측: 서로 다른 각도 16,000종인
    251KB 입력에서 2GB) 작은 파일 하나가 메모리를 고갈시킨다.
    """
    family: Dict[str, Counter] = {}
    for q in quals:
        fam: Counter = Counter()
        for t in topics_by_qual.get(q, ()):  # Q 를 쓰는 주제들
            for q2 in quals_by_term.get(t, ()):
                fam[q2] += 1
        fam.pop(q, None)  # Q 자신은 공유 신호가 될 수 없다
        family[q] = fam
    return family


def _angle_plausible(
    term: str,
    qual: str,
    quals_by_term: Dict[str, set],
    topics_by_qual: Dict[str, set],
    family: Dict[str, Counter],
) -> bool:
    """이 (주제, 각도) 칸이 **색인될 수 있는** 조합으로 보이는가(휴리스틱).

    판정은 **그 칸의 결과(관측 편수)와 무관해야 한다**. 예전 구현은 "주제가 이미 그
    각도를 쓰고 있으면 가능"이라는 지름길을 뒀는데, 그것은 곧 `관측 ≥ 1` 과 같은 말이라
    **관측 0인 칸(=바로 우리가 찾는 공백)만 골라서** 탈락시켰다. 그래서 여기서는
    후보 주제 자신의 기여를 빼고(leave-one-out) 다른 주제들의 어휘만 본다:

      가능 ⟺ (주제가 쓰는 다른 각도) ∩ (T 를 뺀, Q 를 쓰는 주제들의 각도 어휘) ≠ ∅

    주제의 각도 정보가 전혀 없으면 판단하지 않고 통과시킨다(없는 근거로 후보를 지우는
    쪽이 더 위험하다). 판정 결과는 **검정에서 빼는 데 쓰지 않고**(BH-FDR 의 분모가
    결과에 의존하면 안 되므로) 표시 순서와 경고 표시에만 쓴다.
    """
    own = quals_by_term.get(term)
    if not own:
        return True
    fam = family.get(qual)
    if not fam:
        return False
    term_uses_qual = term in topics_by_qual.get(qual, ())
    for q2 in own:
        if q2 == qual:
            continue
        # T 자신이 Q 를 쓰고 있었다면 fam[q2] 에 T 의 기여 1 이 들어 있다 — 빼고 본다.
        others = fam.get(q2, 0) - (1 if term_uses_qual else 0)
        if others > 0:
            return True
    return False


def _angle_candidates(
    articles: Sequence[Article],
    top_k: int,
    top_qualifiers: int,
    min_expected: float,
    min_term_articles: int,
    alpha: float,
) -> Tuple[List[AngleGap], int]:
    """(검정한 모든 (주제×각도) 칸, 그 중 구조적 불가로 보이는 칸 수).

    **분석 단위는 색인 표목(heading) = (논문, 주제어) 한 칸**이며, 부주제어가 하나라도
    붙은 표목만 센다. 이게 핵심이다 — 예전 구현은 주변확률을 *논문* 수준
    (`그 각도를 어느 주제에든 쓴 논문 수`)에서 세면서 관측은 *표목* 수준
    (`그 주제에 그 각도가 붙은 논문 수`)에서 세어, 서로 다른 모집단을 비교했다.
    그 결과 논문당 주제어가 d개면 lift 가 체계적으로 1/d 로 눌려 **모든 칸이 공백으로**
    보였다(실측: 진짜 귀무가설 코퍼스에서 p≤0.05 비율이 1.00, 중앙값 p=0). 표목 수준
    에서는 같은 시뮬레이션이 0.04 로 정상 보정된다.

    부주제어가 안 붙은 표목(bare descriptor)은 분모에서 빠진다 — '색인자가 각도를
    안 붙였다'와 '그 각도의 연구가 없다'를 섞지 않기 위해서다.

    **한계**: NLM 은 descriptor 범주별로 붙일 수 있는 부주제어를 제한한다. 그런 칸은
    `plausible=False` 로 표시하고 순위를 뒤로 미루지만, **검정에서 빼지는 않는다**
    (제외하면 BH-FDR 의 분모 m 이 결과에 의존하게 되어 q 가 정직하지 않게 된다).
    """
    if top_k <= 0 or top_qualifiers <= 0:
        return [], 0

    slot_term: Counter = Counter()      # 주제별 표목 수
    slot_qual: Counter = Counter()      # 각도별 표목 수
    cell: Counter = Counter()           # (주제, 각도) 표목 수
    n_slots = 0
    angles_by_term: Dict[str, Counter] = {}
    quals_by_term: Dict[str, set] = {}
    topics_by_qual: Dict[str, set] = {}
    for a in articles:
        by_desc: Dict[str, set] = {}
        for t, q in article_angles(a):
            by_desc.setdefault(t, set()).add(q)
        for t, qs in by_desc.items():
            n_slots += 1
            slot_term[t] += 1
            for q in qs:
                slot_qual[q] += 1
                cell[(t, q)] += 1
                angles_by_term.setdefault(t, Counter())[q] += 1
                quals_by_term.setdefault(t, set()).add(q)
                topics_by_qual.setdefault(q, set()).add(t)
    if n_slots == 0:
        return [], 0

    top_terms = [t for t, c in _ranked(slot_term)[:top_k] if c >= min_term_articles]
    top_quals = [q for q, _ in _ranked(slot_qual)[:top_qualifiers]]
    family = _qualifier_families(quals_by_term, topics_by_qual, top_quals)

    n_implausible = 0
    candidates: List[AngleGap] = []
    for t in top_terms:
        kt = slot_term[t]
        for q in top_quals:
            nq = slot_qual[q]
            expected = kt * nq / n_slots
            if expected < min_expected:
                continue
            obs = cell.get((t, q), 0)
            lo, hi = lift_ci(obs, expected, alpha=alpha)
            ok = _angle_plausible(t, q, quals_by_term, topics_by_qual, family)
            if not ok:
                n_implausible += 1
            candidates.append(
                AngleGap(
                    term=t,
                    qualifier=q,
                    n_term=kt,
                    n_qualifier=nq,
                    observed=obs,
                    expected=expected,
                    deficit=expected - obs,
                    lift=obs / expected if expected > 0 else 0.0,
                    p_value=hypergeom_lower_tail(n_slots, kt, nq, obs),
                    lift_ci_low=lo,
                    lift_ci_high=hi,
                    top_angles=[
                        [qq, cc] for qq, cc in _ranked(angles_by_term.get(t, Counter()))[:3]
                    ],
                    plausible=ok,
                )
            )

    for g, qv in zip(candidates, benjamini_hochberg([c.p_value for c in candidates])):
        g.q_value = qv
    return candidates, n_implausible


def sort_angle_gaps(gaps: Sequence[AngleGap], sort: str = "deficit") -> List[AngleGap]:
    """각도 공백 정렬 — **구조적으로 불가능해 보이는 칸은 항상 뒤로** 미룬다.

    그런 칸은 관측이 0이라 어떤 기준으로 정렬하든 맨 위를 차지한다(실측: 기대 7.0편의
    `Sleep × methods` 가 1위). 지우지는 않되(검정 집합은 정직해야 하므로) 순위에서만
    내리고 표에 표시한다. 동률은 항상 결정론적으로 깨뜨린다.
    """
    if sort not in ANGLE_SORTS:
        raise ValueError(f"알 수 없는 정렬 기준: {sort!r} (가능: {', '.join(ANGLE_SORTS)})")
    keyfuncs = {
        "deficit": lambda g: (-g.deficit, g.lift, g.term, g.qualifier),
        "lift": lambda g: (g.lift, -g.expected, g.term, g.qualifier),
        "q": lambda g: (g.q_value, g.lift, g.term, g.qualifier),
        "expected": lambda g: (-g.expected, g.lift, g.term, g.qualifier),
    }
    key = keyfuncs[sort]
    return sorted(gaps, key=lambda g: (not g.plausible,) + tuple(key(g)))


def angle_analysis(
    articles: Sequence[Article],
    top_k: int = 12,
    top_qualifiers: int = 10,
    min_expected: float = 1.0,
    max_lift: float = 0.5,
    min_term_articles: int = 3,
    sort: str = "deficit",
    alpha: float = 0.05,
    hide_implausible: bool = False,
) -> Tuple[List[AngleGap], int, int]:
    """(표시할 칸, 검정한 칸 수 m, 그 중 구조적 불가로 보이는 칸 수).

    m 을 밝히지 않으면 "왜 q≤0.05 인 후보가 없는지"를 사용자가 알 수 없다.
    `hide_implausible=True` 면 구조적 불가로 보이는 칸을 **표시에서만** 뺀다
    (검정은 그대로 수행하므로 q 는 변하지 않는다).
    """
    if sort not in ANGLE_SORTS:
        raise ValueError(f"알 수 없는 정렬 기준: {sort!r} (가능: {', '.join(ANGLE_SORTS)})")
    cands, n_implausible = _angle_candidates(
        articles, top_k=top_k, top_qualifiers=top_qualifiers,
        min_expected=min_expected, min_term_articles=min_term_articles, alpha=alpha,
    )
    keep = [g for g in cands if g.lift <= max_lift]
    if hide_implausible:
        keep = [g for g in keep if g.plausible]
    return sort_angle_gaps(keep, sort), len(cands), n_implausible


def angle_gaps(
    articles: Sequence[Article],
    top_k: int = 12,
    top_qualifiers: int = 10,
    min_expected: float = 1.0,
    max_lift: float = 0.5,
    min_term_articles: int = 3,
    sort: str = "deficit",
    alpha: float = 0.05,
    hide_implausible: bool = False,
) -> List[AngleGap]:
    """주제 × 연구각도 격자의 저조 조합(=각도 공백) 목록. 자세한 설명은 `angle_analysis`."""
    return angle_analysis(
        articles, top_k=top_k, top_qualifiers=top_qualifiers,
        min_expected=min_expected, max_lift=max_lift,
        min_term_articles=min_term_articles, sort=sort, alpha=alpha,
        hide_implausible=hide_implausible,
    )[0]


def count_angle_tests(
    articles: Sequence[Article],
    top_k: int = 12,
    top_qualifiers: int = 10,
    min_expected: float = 1.0,
    min_term_articles: int = 3,
) -> int:
    """각도 공백에서 실제로 수행되는 검정 수 m(= BH-FDR 의 분모)."""
    return len(
        _angle_candidates(
            articles, top_k=top_k, top_qualifiers=top_qualifiers,
            min_expected=min_expected, min_term_articles=min_term_articles, alpha=0.05,
        )[0]
    )


def gap_candidate_terms(articles: Sequence[Article], top_k: int) -> List[str]:
    """공백 탐색이 실제로 사용할 상위 주제 목록.

    리포트의 '주요 주제'(--top-mesh)와 공백 탐색 대상(--gap-top-k)은 개수가 다르다.
    무엇이 후보였는지 밝히지 않으면, 목록에 보이는 주제가 왜 공백표에 한 번도 안
    나오는지 사용자가 알 길이 없다.
    """
    if top_k <= 0:
        return []
    topical = [a for a in articles if a.mesh]
    return [t for t, _ in _ranked(_mesh_freq(topical))[:top_k]]


def count_gap_tests(articles: Sequence[Article], top_k: int, min_expected: float) -> int:
    """실제로 수행되는 공백 검정의 개수 m (= BH-FDR 의 분모).

    검정 수가 많을수록 q 는 나빠지므로, m 을 밝혀야 "왜 유의한 후보가 없는지"를
    사용자가 이해하고 `--gap-top-k` 를 조정할 수 있다.
    (주의: `q ≥ p × m` 같은 하한은 **성립하지 않는다** — BH 는
    `q_(i) = min_{j≥i}(m·p_(j)/j)` 이라 q 가 p×m 보다 작을 수 있다.)
    """
    topical = [a for a in articles if a.mesh]
    n = len(topical)
    if n == 0 or top_k <= 0:
        return 0
    freq = _mesh_freq(topical)
    top_terms = [t for t, _ in _ranked(freq)[:top_k]]
    return sum(
        1
        for a_term, b_term in combinations(top_terms, 2)
        if freq[a_term] * freq[b_term] / n >= min_expected
    )


def growth_summary(counts: Dict[int, int], split: Optional[int] = None) -> Dict[str, float]:
    """연도별 편수 dict → 초기(year<split) vs 최근(year>=split) 총량 비교.

    split 은 term_trends/split_point 과 **동일한** 연도 경계를 쓴다(리포트 내
    두 구간 정의가 어긋나지 않도록). 미지정 시 (최소연도+최대연도+1)//2.
    """
    if not counts:
        # 정상 경로와 **같은 키 집합**을 돌려준다(JSON 소비자가 모양이 바뀌지 않도록).
        return {
            "total": 0, "early_total": 0, "recent_total": 0,
            "early_years": 0, "recent_years": 0,
            "early_per_year": 0.0, "recent_per_year": 0.0,
            "recent_share": 0.0, "ratio": 0.0, "ratio_per_year": 0.0,
            "split": None, "cagr": None, "theil_sen": None,
        }
    years = sorted(counts)
    if split is None:
        split = (years[0] + years[-1] + 1) // 2
    early_total = sum(v for y, v in counts.items() if y < split)
    recent_total = sum(v for y, v in counts.items() if y >= split)
    total = early_total + recent_total
    ratio = (recent_total / early_total) if early_total else float("inf")

    # 두 구간의 **햇수가 다를 수 있다**(예: 2016–2026, split 2021 → 초기 5년 / 최근 6년).
    # 그때 총량 비(2.56배)는 창 길이 차이를 성장으로 착각하게 만든다. 연평균 편수로
    # 정규화한 비(2.13배)를 함께 보고해, 리포트가 둘을 구분해 쓸 수 있게 한다.
    lo, hi = years[0], years[-1]
    early_years = max(0, min(split, hi + 1) - lo)
    recent_years = max(0, hi + 1 - max(split, lo))
    early_py = (early_total / early_years) if early_years else 0.0
    recent_py = (recent_total / recent_years) if recent_years else 0.0
    ratio_py = (recent_py / early_py) if early_py else float("inf")
    return {
        "total": total,
        "early_total": early_total,
        "recent_total": recent_total,
        "early_years": early_years,
        "recent_years": recent_years,
        "early_per_year": early_py,
        "recent_per_year": recent_py,
        "recent_share": recent_total / total if total else 0.0,
        "ratio": ratio,
        "ratio_per_year": ratio_py,
        "split": split,
        "cagr": _cagr(counts),
        "theil_sen": theil_sen([v for _, v in yearly_series_dense(counts)]),
    }


def theil_sen(values: Sequence[float]) -> Optional[float]:
    """Theil–Sen 기울기 = 모든 점쌍 기울기의 중앙값(단위: 편/년).

    CAGR 은 구간 **양끝 한 해**로만 계산해, 첫해가 검색 날짜필터에 잘리거나 마지막
    해가 아직 진행 중이면(항상 그렇다) 완전히 엉뚱한 값을 낸다 — 실측에서 평평한
    분야가 '연평균 −17%' 로 보고됐다. Theil–Sen 은 관측치의 절반이 오염돼도 견디는
    로버스트 추정량이고, Mann–Kendall 과 같은 순위 기반 계열이라 함께 읽기 좋다.
    """
    x = list(values)
    n = len(x)
    if n < 3:
        return None
    slopes = [
        (x[j] - x[i]) / (j - i)
        for i in range(n - 1)
        for j in range(i + 1, n)
    ]
    slopes.sort()
    m = len(slopes)
    if m % 2:
        return slopes[m // 2]
    return 0.5 * (slopes[m // 2 - 1] + slopes[m // 2])


def _cagr(counts: Dict[int, int]) -> Optional[float]:
    """연도별 편수의 연평균 성장률(CAGR). 구간 양끝 해의 편수로 계산.

    (마지막해 / 첫해)^(1/연수) − 1. 첫해 편수가 0 이거나 구간이 1년이면 None.
    첫/끝 한 해의 잡음에 민감하므로 유의성 판단은 Mann–Kendall(trend_test)로 한다.
    """
    if not counts:
        return None
    years = sorted(counts)
    lo, hi = years[0], years[-1]
    span = hi - lo
    first, last = counts.get(lo, 0), counts.get(hi, 0)
    if span <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / span) - 1.0


# --------------------------------------------------------------------------- #
# 근거 수준(연구 설계) — PublicationType 기반
# --------------------------------------------------------------------------- #
# PubMed PublicationType → 근거 계층(tier). 한 논문에 여러 타입이 붙으므로
# (예: RCT 는 보통 'Randomized Controlled Trial' + 'Clinical Trial' + 'Journal Article')
# **가장 높은 tier 하나**로 대표시킨다. 아래 순서가 곧 우선순위(위가 강함).
#
# 근거: PubMed 가 실제로 색인하는 PublicationType 문자열만 사용한다(추측 금지).
# 목록: https://www.nlm.nih.gov/mesh/pubtypes.html
EVIDENCE_TIERS: Tuple[Tuple[str, str, frozenset], ...] = (
    ("systematic_review", "메타분석/체계적 문헌고찰", frozenset({
        "Meta-Analysis", "Systematic Review",
    })),
    ("rct", "무작위배정 임상시험(RCT)", frozenset({
        "Randomized Controlled Trial",
    })),
    ("trial", "기타 임상시험(비무작위·초기상)", frozenset({
        "Clinical Trial", "Controlled Clinical Trial",
        "Clinical Trial, Phase I", "Clinical Trial, Phase II",
        "Clinical Trial, Phase III", "Clinical Trial, Phase IV",
        "Pragmatic Clinical Trial", "Adaptive Clinical Trial",
        "Equivalence Trial", "Clinical Trial, Veterinary",
    })),
    ("observational", "관찰연구", frozenset({
        "Observational Study", "Observational Study, Veterinary",
        "Comparative Study", "Twin Study", "Validation Study",
    })),
    ("case_report", "증례보고", frozenset({
        "Case Reports",
    })),
    ("review", "종설/지침(비1차연구)", frozenset({
        "Review", "Scoping Review", "Guideline", "Practice Guideline",
        "Consensus Development Conference",
        "Consensus Development Conference, NIH",
    })),
)

# 개입(interventional) 연구 = 연구자가 처치를 배정한 1차 연구. '근거 공백'의 분자.
INTERVENTIONAL_TIERS = frozenset({"rct", "trial"})

_TIER_RANK: Dict[str, int] = {tier: i for i, (tier, _, _) in enumerate(EVIDENCE_TIERS)}
_TIER_LABEL: Dict[str, str] = {tier: label for tier, label, _ in EVIDENCE_TIERS}
_TIER_LABEL["other"] = "기타/미분류"
_TYPE_TO_TIER: Dict[str, str] = {
    pt: tier for tier, _, types in EVIDENCE_TIERS for pt in types
}


# NLM 은 코호트·환자대조·후향 연구를 PublicationType 이 아니라 **MeSH** 로 색인한다.
# 그래서 PublicationType 만 보면 실제 관찰연구의 대부분이 '설계 미상'으로 빠진다.
# PublicationType 에 설계 신호가 없을 때만 이 MeSH 를 보조 신호로 쓴다.
OBSERVATIONAL_MESH = frozenset({
    "Cohort Studies", "Retrospective Studies", "Prospective Studies",
    "Case-Control Studies", "Cross-Sectional Studies", "Longitudinal Studies",
    "Follow-Up Studies",
})


def evidence_tier(pub_types: Sequence[str], mesh: Sequence[str] = ()) -> str:
    """PublicationType(+보조로 MeSH) → 대표 근거 tier(가장 높은 하나). 미상이면 'other'.

    'Journal Article'·'English Abstract'·'Research Support, ...' 처럼 연구 설계가
    아닌 태그만 달린 논문은 설계 **미상**이다. PubMed 레코드는 사실상 전부
    'Journal Article' 을 달고 있으므로, "pub_types 가 비었는가"로 미상을 판정하면
    커버리지가 항상 100%로 나오는 함정에 빠진다 — 반드시 이 함수가 'other' 를
    돌려주는지로 판정해야 한다(evidence_profile 참고).

    `mesh` 를 주면, PublicationType 에 설계 신호가 없을 때에 한해 코호트/후향/환자대조
    같은 **연구설계 MeSH** 를 관찰연구 신호로 인정한다(NLM 색인 관행 보정).
    """
    best: Optional[str] = None
    for pt in pub_types:
        tier = _TYPE_TO_TIER.get(pt.strip())
        if tier is None:
            continue
        if best is None or _TIER_RANK[tier] < _TIER_RANK[best]:
            best = tier
    if best is None and mesh:
        for term in mesh:
            if term in OBSERVATIONAL_MESH:
                return "observational"
    return best or "other"


def article_tier(article: "Article") -> str:
    """한 논문의 근거 tier(설계 MeSH 보조 신호 포함)."""
    return evidence_tier(article.pub_types, article.mesh)


def tier_label(tier: str) -> str:
    """tier 코드 → 한국어 표시 이름."""
    return _TIER_LABEL.get(tier, tier)


def evidence_profile(articles: Sequence[Article], tiers: Optional[Sequence[str]] = None) -> Dict:
    """코퍼스의 연구 설계 구성(근거 지형).

    **설계 미상 논문은 분모에서 뺀다.** 여기서 '미상'은 `evidence_tier()` 가 'other'
    를 돌려주는 논문이다 — PubMed 레코드는 사실상 전부 'Journal Article' 을 달고
    있으므로 "PublicationType 이 비었는가"로 판정하면 커버리지가 언제나 100%로 나오고
    설계 미상 논문이 조용히 분모에 섞인다(그리고 개입연구 비율이 희석된다).

    `tiers` 로 논문별 tier 를 미리 계산해 넘길 수 있다(체크 태그를 떼기 *전* 의
    MeSH 로 설계 신호를 읽어야 하는 리포트 경로용).

    반환 dict:
      n_articles, n_typed, n_unknown, coverage,
      tiers: [{tier, label, count, share}] — EVIDENCE_TIERS 순서(설계 미상은 제외),
      n_interventional, interventional_share
    """
    n = len(articles)
    if tiers is None:
        tiers = [article_tier(a) for a in articles]
    counts: Counter = Counter(t for t in tiers if t != "other")
    n_typed = sum(counts.values())
    order = [t for t, _, _ in EVIDENCE_TIERS]
    rows = [
        {
            "tier": t,
            "label": tier_label(t),
            "count": counts.get(t, 0),
            "share": (counts.get(t, 0) / n_typed) if n_typed else 0.0,
        }
        for t in order
    ]
    # 개입연구 편수는 대표 tier 가 아니라 설계 태그의 '존재 여부'로 센다
    # (Meta-Analysis + RCT 가 함께 달린 논문을 놓치지 않기 위해).
    n_int = sum(
        1 for a, t in zip(articles, tiers) if t != "other" and is_interventional(a)
    )
    ci_lo, ci_hi = clopper_pearson(n_int, n_typed) if n_typed else (0.0, 1.0)
    return {
        "n_articles": n,
        "n_typed": n_typed,
        "n_unknown": n - n_typed,
        "coverage": (n_typed / n) if n else 0.0,
        "tiers": rows,
        "n_interventional": n_int,
        "interventional_share": (n_int / n_typed) if n_typed else 0.0,
        "interventional_share_ci": [ci_lo, ci_hi],
    }


@dataclass
class TopicEvidence:
    """한 주제의 '개입연구 밀도'와, 코퍼스 나머지 대비 유의성."""

    term: str
    n_articles: int          # 설계 정보를 가진 논문 중 이 주제를 단 편수
    n_interventional: int    # 그 중 RCT/임상시험 편수
    share: float             # n_interventional / n_articles
    rest_n: int              # 이 주제를 달지 않은(설계 정보 보유) 논문 수
    rest_interventional: int
    rest_share: float
    p_value: float           # Fisher 정확검정 양측(주제 × 개입여부, 나머지 대비)
    q_value: float = 1.0     # 검정한 주제 전체에 BH-FDR 보정
    tier_counts: Dict[str, int] = field(default_factory=dict)
    # 개입비율의 Clopper–Pearson 95% 정확구간. '0/8편 = 0%' 를 구간 없이 적으면
    # "이 주제엔 시험이 없다"로 읽히지만, 실제 상한은 37% 다.
    share_ci_low: float = 0.0
    share_ci_high: float = 1.0


# 개입(interventional) 판정에 쓰는 PublicationType. **대표 tier 가 아니라 '존재 여부'**
# 로 본다: 'Meta-Analysis' + 'Randomized Controlled Trial' 이 함께 달린 논문은 대표
# tier 가 systematic_review 로 잡히지만, 그 논문은 분명 무작위배정 시험이다.
_INTERVENTIONAL_TYPES = frozenset(
    pt for tier, _, types in EVIDENCE_TIERS if tier in INTERVENTIONAL_TIERS for pt in types
)


def is_interventional(article: "Article") -> bool:
    """이 논문이 개입(중재)연구인가 — 설계 태그 '존재 여부'로 판정."""
    return any(pt.strip() in _INTERVENTIONAL_TYPES for pt in article.pub_types)


def topic_evidence(
    articles: Sequence[Article],
    top_k: int = 12,
    min_articles: int = 3,
    tiers: Optional[Sequence[str]] = None,
) -> List[TopicEvidence]:
    """빈출 상위 top_k 주제별 개입연구(RCT·임상시험) 밀도 — 낮은 순 정렬.

    "논문은 충분히 많은데 개입연구는 기대보다 적은 주제" = 시험을 설계할 자리.

    - **설계 정보(PublicationType)가 있는 논문만** 대상으로 한다. 그래야 '색인이
      안 됐다'와 '시험이 없다'가 섞이지 않는다.
    - 각 주제에 대해 2×2 [[주제&개입, 주제&비개입], [나머지&개입, 나머지&비개입]]
      Fisher 정확검정(양측) → 코퍼스 평균 대비 유의하게 적은지/많은지.
    - 검정한 모든 주제에 BH-FDR 를 적용해 q-value 를 채운다(다중검정 보정).
    - min_articles: 이 편수 미만인 주제는 통계가 무의미하므로 제외.
    - 정렬: share 오름차순(개입연구가 가장 비어 있는 주제 우선), 동률이면 편수 많은 순.
    """
    if tiers is None:
        tiers = [article_tier(a) for a in articles]
    # 설계가 확인된(tier != 'other') **그리고** 주제어를 가진 논문만 대상.
    # - 설계 미상을 포함하면 '색인 안 됨'이 '시험 없음'으로 둔갑한다.
    # - 주제어가 없는 논문은 어떤 주제에도 속할 수 없으므로 '나머지' 집단에만 들어가
    #   비교군을 조용히 희석시킨다.
    pairs = [
        (a, t) for a, t in zip(articles, tiers) if t != "other" and a.mesh
    ]
    if not pairs or top_k <= 0:
        return []
    typed = [a for a, _ in pairs]
    typed_tiers = [t for _, t in pairs]

    is_int = [is_interventional(a) for a in typed]
    total_int = sum(is_int)
    n_typed = len(typed)

    freq = _mesh_freq(typed)
    top_terms = [t for t, _ in _ranked(freq)[:top_k]]
    term_set = set(top_terms)

    n_by_term: Counter = Counter()
    int_by_term: Counter = Counter()
    tiers_by_term: Dict[str, Counter] = {t: Counter() for t in top_terms}
    for art, interventional, tier in zip(typed, is_int, typed_tiers):
        for t in set(art.mesh) & term_set:
            n_by_term[t] += 1
            tiers_by_term[t][tier] += 1
            if interventional:
                int_by_term[t] += 1

    out: List[TopicEvidence] = []
    for term in top_terms:
        n_t = n_by_term.get(term, 0)
        if n_t < min_articles:
            continue
        i_t = int_by_term.get(term, 0)
        rest_n = n_typed - n_t
        rest_i = total_int - i_t
        p = fisher_exact_two_sided(i_t, n_t - i_t, rest_i, rest_n - rest_i)
        ci_lo, ci_hi = clopper_pearson(i_t, n_t)
        out.append(
            TopicEvidence(
                term=term,
                n_articles=n_t,
                n_interventional=i_t,
                share=i_t / n_t,
                rest_n=rest_n,
                rest_interventional=rest_i,
                rest_share=(rest_i / rest_n) if rest_n else 0.0,
                p_value=p,
                tier_counts=dict(tiers_by_term[term]),
                share_ci_low=ci_lo,
                share_ci_high=ci_hi,
            )
        )

    qs = benjamini_hochberg([t.p_value for t in out])
    for t, q in zip(out, qs):
        t.q_value = q

    out.sort(key=lambda t: (t.share, -t.n_articles, t.term))
    return out


# --------------------------------------------------------------------------- #
# 대상집단 공백(population gap) — '누구를 대상으로 연구했는가' 축
# --------------------------------------------------------------------------- #
# NLM 색인자는 인체 대상 논문에 연령·성별 체크 태그를 단다(Child/Adult/Aged…).
# 이 태그들은 *연구 주제* 가 아니라서 주제 분석에서는 잡음이지만(→ CHECK_TAGS 로 제외),
# **그 자체가 하나의 축** 이다: "이 질환은 논문이 200편인데 고령(Aged) 논문은 12편뿐"
# 은 임상·제약 연구자에게 곧바로 시험 설계로 이어지는 정보다(ICH E7 고령자 지침,
# 소아 개발계획(PSP/PIP), NIH 의 성별을 생물학적 변수로 다루는 정책).
# 연령 구간은 NLM 정의를 그대로 따른다(구간이 서로 **겹친다** — 40~70세 코호트는
# Adult + Middle Aged + Aged 가 동시에 붙는다. 그래서 비중의 합은 1이 아니다).
POPULATION_GROUPS: Tuple[Tuple[str, str, str, frozenset], ...] = (
    ("pediatric", "age", "소아·청소년 (0–18)", frozenset({
        "Infant, Newborn", "Infant", "Child, Preschool", "Child", "Adolescent",
    })),
    ("young_adult", "age", "청년 (19–24)", frozenset({"Young Adult"})),
    ("adult", "age", "성인 (19–44)", frozenset({"Adult"})),
    ("middle_aged", "age", "중년 (45–64)", frozenset({"Middle Aged"})),
    ("aged", "age", "고령 (65+)", frozenset({"Aged"})),
    ("oldest_old", "age", "초고령 (80+)", frozenset({"Aged, 80 and over"})),
    ("female", "sex", "여성", frozenset({"Female"})),
    ("male", "sex", "남성", frozenset({"Male"})),
    ("pregnancy", "sex", "임신", frozenset({"Pregnancy"})),
)
POPULATION_AXES: Tuple[str, ...] = ("age", "sex")
_POP_LABEL: Dict[str, str] = {k: lab for k, _ax, lab, _t in POPULATION_GROUPS}
_POP_AXIS: Dict[str, str] = {k: ax for k, ax, _lab, _t in POPULATION_GROUPS}
_POP_TERMS: Dict[str, frozenset] = {k: t for k, _ax, _lab, t in POPULATION_GROUPS}
# 그룹 판정에 쓰이는 모든 MeSH 체크 태그(주제 후보에서 빼기 위해서도 쓴다).
POPULATION_TAGS: frozenset = frozenset(
    term for _k, _ax, _lab, terms in POPULATION_GROUPS for term in terms
)
POPULATION_SORTS: Tuple[str, ...] = ("deficit", "share", "q", "lift")


def article_populations(article: "Article") -> frozenset:
    """이 논문에 붙은 대상집단 그룹 키 집합.

    **체크 태그를 떼기 전** 의 MeSH 로 읽어야 한다(strip_check_tags 가 지우는 바로 그
    태그들이 여기서는 신호다). 그래서 build_report 는 tiers 와 같은 시점에 이 값을
    미리 계산해 둔다.
    """
    mesh = set(article.mesh)
    if not mesh:
        return frozenset()
    return frozenset(k for k, terms in _POP_TERMS.items() if mesh & terms)


def population_profile(
    articles: Sequence[Article], pops: Optional[Sequence[frozenset]] = None
) -> Dict:
    """코퍼스 전체의 대상집단 지형 + 색인 커버리지.

    `articles` 는 **체크 태그를 떼기 전** 의 코퍼스여야 한다(Humans/Animals 집계도
    체크 태그를 읽는다). `pops` 를 함께 주면 그 값을 그대로 쓴다.

    커버리지를 함께 내는 이유는 근거 지형과 같다: **'색인이 안 됨'과 '연구가 없음'을
    섞지 않기 위해서**다. RIS/CSV 내보내기나 색인 전 최신 논문에는 체크 태그가 아예
    없고, 동물 실험에는 애초에 연령 태그가 붙지 않는다.
    분모는 **축(axis)마다 따로** 잡는다 — 성별 태그만 있고 연령 태그가 없는 논문이
    흔한데, 이를 연령 분모에 넣으면 모든 연령대가 실제보다 비어 보인다.
    """
    n = len(articles)
    if pops is None:
        pops = [article_populations(a) for a in articles]
    if len(pops) != len(articles):
        raise ValueError("pops 길이가 articles 와 다릅니다")
    n_human = sum(1 for a in articles if "Humans" in a.mesh)
    n_animal = sum(1 for a in articles if "Animals" in a.mesh)
    axis_base = {
        ax: sum(1 for p in pops if any(_POP_AXIS[k] == ax for k in p))
        for ax in POPULATION_AXES
    }
    counts: Counter = Counter()
    for p in pops:
        for k in p:
            counts[k] += 1
    rows = []
    for key, axis, label, _terms in POPULATION_GROUPS:
        base = axis_base.get(axis, 0)
        c = counts.get(key, 0)
        lo, hi = clopper_pearson(c, base) if base else (0.0, 1.0)
        rows.append({
            "key": key,
            "axis": axis,
            "label": label,
            "count": c,
            "base": base,
            "share": (c / base) if base else 0.0,
            "share_ci_low": lo,
            "share_ci_high": hi,
        })
    n_indexed = sum(1 for p in pops if p)
    return {
        "n_articles": n,
        "n_indexed": n_indexed,
        "coverage": (n_indexed / n) if n else 0.0,
        "n_human": n_human,
        "n_animal": n_animal,
        "axis_base": axis_base,
        "groups": rows,
    }


@dataclass
class PopulationGap:
    """한 주제 × 한 대상집단의 '대표성' — 나머지 논문 대비 유의하게 적은가."""

    term: str
    group: str               # POPULATION_GROUPS 의 키
    axis: str                # 'age' | 'sex'
    label: str               # 사람이 읽는 집단 이름
    n_articles: int          # 그 축에 색인된 논문 중 이 주제를 단 편수
    observed: int            # 그 중 이 집단에 색인된 편수
    share: float             # observed / n_articles
    rest_n: int              # 이 주제를 달지 않은(같은 축에 색인된) 논문 수
    rest_observed: int
    rest_share: float
    expected: float          # n_articles × rest_share (비교군 기준 기대 편수)
    deficit: float           # expected − observed (양수 = 과소대표)
    lift: float              # observed / expected
    p_value: float           # Fisher 정확검정 양측
    q_value: float = 1.0     # 검정한 모든 (주제×집단)에 BH-FDR
    share_ci_low: float = 0.0    # Clopper–Pearson
    share_ci_high: float = 1.0
    lift_ci_low: Optional[float] = None   # 포아송(Garwood) 정확구간 기반
    lift_ci_high: Optional[float] = None


def sort_population_gaps(
    gaps: Sequence[PopulationGap], sort: str = "deficit"
) -> List[PopulationGap]:
    """대상집단 공백 정렬(결정론적 — 동률은 주제명·집단명 오름차순)."""
    if sort not in POPULATION_SORTS:
        raise ValueError(
            f"알 수 없는 정렬 기준: {sort!r} (가능: {', '.join(POPULATION_SORTS)})"
        )
    keys = {
        "deficit": lambda g: (-g.deficit, g.q_value),
        "share": lambda g: (g.share, -g.deficit),
        "q": lambda g: (g.q_value, -g.deficit),
        "lift": lambda g: (g.lift, -g.deficit),
    }
    key = keys[sort]
    return sorted(gaps, key=lambda g: (*key(g), g.term, g.group))


def population_gaps(
    articles: Sequence[Article],
    pops: Optional[Sequence[frozenset]] = None,
    top_k: int = 12,
    min_articles: int = 5,
    sort: str = "deficit",
) -> Tuple[List[PopulationGap], int]:
    """빈출 상위 top_k 주제 × 대상집단의 과소대표 검정. (목록, 검정 수) 반환.

    - 분모는 **축마다** 그 축에 색인된 논문으로 잡는다(연령 태그가 없는 논문은
      연령 검정에서 빠진다). 그래야 '색인 안 됨'이 '연구 안 됨'으로 둔갑하지 않는다.
    - 각 (주제, 집단)에 2×2 [[주제&집단, 주제&비집단], [나머지&집단, 나머지&비집단]]
      Fisher 정확검정(양측) → 코퍼스의 나머지와 대표성이 다른지.
    - **검정한 전부**(과대대표 포함)에 BH-FDR 를 적용한다. 과소대표만 골라 보정하면
      분모가 결과에 의존해 q 가 정직하지 않다.
    - `share` 에 Clopper–Pearson, `lift` 에 포아송 정확구간을 붙인다: `0/7편 = 0%` 는
      "고령 연구가 없다"가 아니라 "상한이 41% 다"라는 뜻이기 때문이다.
    - 반환에는 과대대표 행도 포함된다(렌더러가 갈라 보여 준다).
    """
    if pops is None:
        pops = [article_populations(a) for a in articles]
    if len(pops) != len(articles):
        raise ValueError("pops 길이가 articles 와 다릅니다")
    if top_k <= 0 or not articles:
        return [], 0
    min_articles = max(1, int(min_articles))

    # 주제 후보: 대상집단이 하나라도 색인된 논문에서 뽑는다(그 논문들만 검정에 쓰이므로).
    # 체크 태그·방법론 표제어는 주제 축에서 제외한다 — --include-check-tags 를 켠
    # 사용자라도 'Aged × 고령' 같은 자기순환 검정을 보고 싶진 않다.
    indexed = [a for a, p in zip(articles, pops) if p and a.mesh]
    if not indexed:
        return [], 0
    freq = Counter()
    for a in indexed:
        for t in set(a.mesh):
            if not is_non_topical(t):
                freq[t] += 1
    top_terms = [t for t, _c in _ranked(freq)[:top_k]]
    if not top_terms:
        return [], 0
    term_set = set(top_terms)

    out: List[PopulationGap] = []
    for axis in POPULATION_AXES:
        axis_keys = [k for k, ax, _lab, _t in POPULATION_GROUPS if ax == axis]
        base = [
            (a, p) for a, p in zip(articles, pops)
            if any(_POP_AXIS[k] == axis for k in p)
        ]
        n_base = len(base)
        if not n_base:
            continue
        total_by_group = {k: sum(1 for _a, p in base if k in p) for k in axis_keys}
        n_by_term: Counter = Counter()
        obs: Dict[str, Counter] = {t: Counter() for t in top_terms}
        for a, p in base:
            for t in set(a.mesh) & term_set:
                n_by_term[t] += 1
                for k in p:
                    if _POP_AXIS[k] == axis:
                        obs[t][k] += 1
        for term in top_terms:
            n_t = n_by_term.get(term, 0)
            if n_t < min_articles:
                continue
            rest_n = n_base - n_t
            if rest_n <= 0:
                continue
            for k in axis_keys:
                o = obs[term].get(k, 0)
                rest_o = total_by_group[k] - o
                if not total_by_group[k] or total_by_group[k] == n_base:
                    # 그 집단이 축 전체에 0편이거나 **전부**면 2×2 표의 한 변이 상수라
                    # Fisher p 가 항상 정확히 1.0 이다 — 정보가 없는데도 BH 의 분모(m)만
                    # 키워 실제 공백의 q 를 부풀린다. PubMed 는 혼성 임상연구 대부분에
                    # Male 과 Female 을 **둘 다** 달기 때문에 성별 축에서 늘 일어난다.
                    continue
                rest_share = rest_o / rest_n
                expected = n_t * rest_share
                lift = (o / expected) if expected > 0 else float("inf")
                lo, hi = clopper_pearson(o, n_t)
                l_lo, l_hi = lift_ci(o, expected)
                out.append(PopulationGap(
                    term=term, group=k, axis=axis, label=_POP_LABEL[k],
                    n_articles=n_t, observed=o, share=o / n_t,
                    rest_n=rest_n, rest_observed=rest_o, rest_share=rest_share,
                    expected=expected, deficit=expected - o, lift=lift,
                    p_value=fisher_exact_two_sided(
                        o, n_t - o, rest_o, rest_n - rest_o
                    ),
                    share_ci_low=lo, share_ci_high=hi,
                    lift_ci_low=l_lo, lift_ci_high=l_hi,
                ))

    for g, q in zip(out, benjamini_hochberg([g.p_value for g in out])):
        g.q_value = q
    return sort_population_gaps(out, sort), len(out)
