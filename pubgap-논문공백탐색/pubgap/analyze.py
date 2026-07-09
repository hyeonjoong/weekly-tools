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
from dataclasses import dataclass
from itertools import combinations
from math import comb
from typing import Dict, List, Optional, Sequence, Tuple

from .records import Article


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
    c = Counter(a.journal for a in articles if a.journal)
    return c.most_common(n)


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


def hypergeom_lower_tail(N: int, K: int, n: int, k: int) -> float:
    """초기하분포 하단 꼬리확률 P(X <= k).

    전체 N편 중 주제 A를 단 논문 K편, 주제 B를 단 논문 n편일 때, 둘을 함께 단
    논문이 k편 '이하'로 관측될 확률. 값이 작을수록 '기대보다 유의하게 덜 엮임'
    = 통계적으로 뒷받침되는 공백. 표준 라이브러리 math.comb 만 사용.
    """
    if N <= 0 or K < 0 or n < 0 or K > N or n > N:
        return 1.0
    if k < 0:
        return 0.0
    k = min(k, K, n)  # 관측 상한(=min(K,n)) 초과는 사실상 P=1 로 클램프
    lo = max(0, n - (N - K))  # 가능한 최소 동시등장
    denom = comb(N, n)
    if denom == 0:
        return 1.0
    total = 0.0
    for i in range(lo, k + 1):
        total += comb(K, i) * comb(N - K, n - i)
    return min(1.0, total / denom)


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

    early = [a for a in articles if a.year is not None and a.year < split]
    recent = [a for a in articles if a.year is not None and a.year >= split]
    n_early = len(early) or 1  # 0 division 방지
    n_recent = len(recent) or 1

    all_terms = {t for a in articles for t in a.mesh}
    out: List[TermTrend] = []
    for term in all_terms:
        ec = sum(1 for a in early if term in a.mesh)
        rc = sum(1 for a in recent if term in a.mesh)
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


def gap_pairs(
    articles: Sequence[Article],
    top_k: int = 12,
    min_expected: float = 2.0,
    max_lift: float = 0.5,
) -> List[GapPair]:
    """빈출 상위 top_k 주제쌍 중 '저조 조합'을 lift 오름차순으로 반환.

    - 관측(observed): 두 주제를 함께 단 논문 수.
    - 기대(expected): 독립 가정 하 기대 동시등장 수 = count_a * count_b / N.
    - lift = observed / expected. lift<1 이면 기대보다 덜 엮인 것.
    - p_value: 초기하분포 하단꼬리 — '이만큼 덜 엮일' 확률(우연 여부 판단용).
    - min_expected: 기대값이 이 값 미만이면(애초에 만날 일이 드묾) 제외 —
      '충분히 만날 만한데도 안 만난' 조합만 공백으로 본다.
    - max_lift: 이 값 이하인 조합만 반환.
    """
    n = len(articles)
    if n == 0:
        return []

    freq = _mesh_freq(articles)
    top_terms = [t for t, _ in _ranked(freq)[:top_k]]  # 결정론적 상위 선택

    out: List[GapPair] = []
    for a_term, b_term in combinations(top_terms, 2):
        ca, cb = freq[a_term], freq[b_term]
        observed = sum(1 for art in articles if a_term in art.mesh and b_term in art.mesh)
        expected = ca * cb / n
        if expected < min_expected:
            continue
        lift = observed / expected if expected > 0 else 0.0
        if lift > max_lift:
            continue
        p = hypergeom_lower_tail(n, ca, cb, observed)
        out.append(GapPair(a_term, b_term, observed, expected, lift, ca, cb, p))

    # lift 낮을수록, 기대가 클수록(= 놓친 정도가 클수록) 우선.
    out.sort(key=lambda g: (g.lift, -g.expected, g.term_a, g.term_b))
    return out


def growth_summary(counts: Dict[int, int], split: Optional[int] = None) -> Dict[str, float]:
    """연도별 편수 dict → 초기(year<split) vs 최근(year>=split) 총량 비교.

    split 은 term_trends/split_point 과 **동일한** 연도 경계를 쓴다(리포트 내
    두 구간 정의가 어긋나지 않도록). 미지정 시 (최소연도+최대연도+1)//2.
    """
    if not counts:
        return {"total": 0, "recent_share": 0.0, "ratio": 0.0, "split": None}
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
    }
