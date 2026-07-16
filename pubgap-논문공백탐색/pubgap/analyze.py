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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from math import comb, erf, exp, lgamma, sqrt
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


def strip_check_tags(articles: Sequence["Article"]) -> List["Article"]:
    """각 논문의 분석용 주제(mesh)에서 체크 태그를 제거한 새 리스트."""
    from dataclasses import replace

    out: List[Article] = []
    for a in articles:
        filtered = [t for t in a.mesh if t not in CHECK_TAGS]
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
    if N <= 0 or K < 0 or n < 0 or K > N or n > N:
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
    pmids_a: List[str] = field(default_factory=list)      # A만 다룬 대표 논문(검증용)
    pmids_b: List[str] = field(default_factory=list)      # B만 다룬 대표 논문
    pmids_both: List[str] = field(default_factory=list)   # 둘 다 다룬 논문(observed>0일 때)
    bridges: List = field(default_factory=list)           # Swanson ABC 가교: [[C, A&C수, C&B수], ...]


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
) -> None:
    """(내부) 한 gap 쌍에 대해 대표 PMID 와 Swanson ABC 가교 주제 C 를 계산.

    가교(bridge): A 와 함께 자주 나오고(A&C), B 와도 자주 나오지만(C&B), A–B 자체는
    드문 제3의 주제 C. "A는 C를 통해 B와 연결된다"는 기전 서사를 만들어, 리뷰어에게
    '왜 이 주제인가'를 설명할 근거가 된다. 강도 = min(A&C 편수, C&B 편수).
    반환은 없고 (pmids_a, pmids_b, pmids_both, bridges) 튜플을 준다.
    """
    pa: List[str] = []
    pb: List[str] = []
    both: List[str] = []
    ac: Counter = Counter()
    cb: Counter = Counter()
    for art in articles:
        ms = set(art.mesh)
        has_a = a in ms
        has_b = b in ms
        if has_a and has_b and len(both) < n_examples:
            both.append(art.pmid)
        elif has_a and not has_b and len(pa) < n_examples:
            pa.append(art.pmid)
        elif has_b and not has_a and len(pb) < n_examples:
            pb.append(art.pmid)
        if has_a:
            for c in ms:
                if c != a and c != b:
                    ac[c] += 1
        if has_b:
            for c in ms:
                if c != a and c != b:
                    cb[c] += 1
    bridges = []
    for c in set(ac) & set(cb):
        strength = min(ac[c], cb[c])
        bridges.append((strength, ac[c] + cb[c], c, ac[c], cb[c]))
    bridges.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return pa, pb, both, [[c, x_ac, x_cb] for _, _, c, x_ac, x_cb in bridges[:bridge_top_n]]


def gap_pairs(
    articles: Sequence[Article],
    top_k: int = 12,
    min_expected: float = 2.0,
    max_lift: float = 0.5,
    n_examples: int = 3,
    bridge_top_n: int = 3,
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
    n = len(articles)
    if n == 0:
        return []

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
        candidates.append(GapPair(a_term, b_term, observed, expected, lift, ca, cb, p))

    # 2) 검정한 후보 전체에 BH-FDR 를 적용해 q-value 를 채운다(필터 전에!).
    qs = benjamini_hochberg([g.p_value for g in candidates])
    for g, q in zip(candidates, qs):
        g.q_value = q

    # 3) lift 임계 통과분만 남겨 lift 오름차순(놓친 정도 큰 순)으로.
    out = [g for g in candidates if g.lift <= max_lift]
    out.sort(key=lambda g: (g.lift, -g.expected, g.term_a, g.term_b))

    # 4) 살아남은 소수의 공백에만 대표 PMID·가교 주제를 채운다(비용은 작다).
    if n_examples or bridge_top_n:
        for g in out:
            pa, pb, both, bridges = _enrich_gap(
                articles, g.term_a, g.term_b, n_examples=n_examples, bridge_top_n=bridge_top_n
            )
            g.pmids_a, g.pmids_b, g.pmids_both, g.bridges = pa, pb, both, bridges
    return out


def growth_summary(counts: Dict[int, int], split: Optional[int] = None) -> Dict[str, float]:
    """연도별 편수 dict → 초기(year<split) vs 최근(year>=split) 총량 비교.

    split 은 term_trends/split_point 과 **동일한** 연도 경계를 쓴다(리포트 내
    두 구간 정의가 어긋나지 않도록). 미지정 시 (최소연도+최대연도+1)//2.
    """
    if not counts:
        return {"total": 0, "recent_share": 0.0, "ratio": 0.0, "split": None, "cagr": None}
    years = sorted(counts)
    if split is None:
        split = (years[0] + years[-1] + 1) // 2
    early_total = sum(v for y, v in counts.items() if y < split)
    recent_total = sum(v for y, v in counts.items() if y >= split)
    total = early_total + recent_total
    ratio = (recent_total / early_total) if early_total else float("inf")
    return {
        "total": total,
        "early_total": early_total,
        "recent_total": recent_total,
        "recent_share": recent_total / total if total else 0.0,
        "ratio": ratio,
        "split": split,
        "cagr": _cagr(counts),
    }


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
