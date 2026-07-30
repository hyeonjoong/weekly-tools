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
from math import comb, erf, exp, lgamma, log2, sqrt
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
    observed_early: int = 0    # 초기 구간의 동시등장 편수
    observed_recent: int = 0   # 최근 구간의 동시등장 편수
    gap_trend: str = "unknown"  # 'closing'(메워지는 중) | 'widening' | 'stable' | 'unknown'
    pmids_a: List[str] = field(default_factory=list)      # A만 다룬 대표 논문(검증용)
    pmids_b: List[str] = field(default_factory=list)      # B만 다룬 대표 논문
    pmids_both: List[str] = field(default_factory=list)   # 둘 다 다룬 논문(observed>0일 때)
    bridges: List = field(default_factory=list)           # Swanson ABC 가교: [[C, A&C수, C&B수], ...]


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

    반환: (pmids_a, pmids_b, pmids_both, bridges, obs_early, obs_recent).
    `split` 이 주어지면 동시등장 편수를 초기/최근으로 나눠 세어 공백이 메워지는
    중인지 판정할 재료를 만든다(연도 미상 논문은 어느 구간에도 넣지 않는다).
    """
    pa: List[str] = []
    pb: List[str] = []
    both: List[str] = []
    ac: Counter = Counter()
    cb: Counter = Counter()
    obs_early = 0
    obs_recent = 0
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
            if split is not None and art.year is not None:
                if art.year < split:
                    obs_early += 1
                else:
                    obs_recent += 1
        elif has_a and not has_b and len(pa) < n_examples:
            pa.append(art.pmid)
        elif has_b and not has_a and len(pb) < n_examples:
            pb.append(art.pmid)
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
    return pa, pb, both, bridges, obs_early, obs_recent


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
        candidates.append(
            GapPair(
                a_term, b_term, observed, expected, lift, ca, cb, p,
                deficit=expected - observed, jaccard=jac, cosine=cos, npmi=npmi,
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
        pa, pb, both, bridges, oe, orc = _enrich_gap(
            articles, g.term_a, g.term_b, n_examples=n_examples,
            bridge_top_n=bridge_top_n, split=split, freq=freq,
        )
        g.pmids_a, g.pmids_b, g.pmids_both, g.bridges = pa, pb, both, bridges
        g.observed_early, g.observed_recent = oe, orc
        g.gap_trend = _classify_gap_trend(oe, orc, n_early, n_recent, observed=g.observed)
    return out


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
    return {
        "n_articles": n,
        "n_typed": n_typed,
        "n_unknown": n - n_typed,
        "coverage": (n_typed / n) if n else 0.0,
        "tiers": rows,
        "n_interventional": n_int,
        "interventional_share": (n_int / n_typed) if n_typed else 0.0,
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
            )
        )

    qs = benjamini_hochberg([t.p_value for t in out])
    for t, q in zip(out, qs):
        t.q_value = q

    out.sort(key=lambda t: (t.share, -t.n_articles, t.term))
    return out
