"""새 통계(BH-FDR, Mann–Kendall, CAGR)와 gap q-value 를 손계산/브루트포스로 검증."""

import math
import random
from itertools import combinations

import pytest

from pubgap.analyze import (
    CHECK_TAGS,
    benjamini_hochberg,
    fisher_exact_two_sided,
    gap_pairs,
    growth_summary,
    hypergeom_lower_tail,
    mann_kendall,
    strip_check_tags,
    top_mesh,
    trend_test,
    yearly_series_dense,
)
from pubgap.records import Article


def _mk(pmid, year, mesh):
    return Article(pmid=pmid, year=year, journal="J", title="t", mesh=mesh)


# --------------------------------------------------------------------------- #
# Benjamini–Hochberg
# --------------------------------------------------------------------------- #
def _bh_reference(pvals):
    """교과서 정의를 그대로 옮긴 독립 구현(검증용)."""
    m = len(pvals)
    if m == 0:
        return []
    idx = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = idx[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        q[i] = min(prev, 1.0)
    return q


def test_bh_empty_and_single():
    assert benjamini_hochberg([]) == []
    assert benjamini_hochberg([0.3]) == [0.3]
    assert benjamini_hochberg([0.0]) == [0.0]


def test_bh_known_small_case():
    # p=[0.01,0.02,0.03,0.04,0.05], m=5
    # 정렬됨 → q_(5)=0.05, q_(4)=0.04*5/4=0.05, q_(3)=0.03*5/3=0.05,
    # q_(2)=0.02*5/2=0.05, q_(1)=0.01*5/1=0.05 → 전부 0.05
    q = benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05])
    assert all(abs(v - 0.05) < 1e-12 for v in q)


def test_bh_monotone_and_bounds():
    for seed in range(100):
        random.seed(seed)
        ps = [random.random() for _ in range(random.randint(1, 30))]
        q = benjamini_hochberg(ps)
        ref = _bh_reference(ps)
        assert max(abs(a - b) for a, b in zip(q, ref)) < 1e-12
        assert all(0.0 <= v <= 1.0 for v in q)
        # q >= p 는 아니지만, 정렬 후 단조 비감소여야 한다.
        order = sorted(range(len(ps)), key=lambda i: ps[i])
        qs_sorted = [q[i] for i in order]
        assert all(qs_sorted[i] <= qs_sorted[i + 1] + 1e-12 for i in range(len(qs_sorted) - 1))


def test_bh_qvalue_never_below_pvalue_for_min():
    # 가장 작은 p 의 q 는 p*m (>= p) 로 커진다.
    ps = [0.001, 0.5, 0.9]
    q = benjamini_hochberg(ps)
    assert q[0] >= ps[0]


# --------------------------------------------------------------------------- #
# Mann–Kendall
# --------------------------------------------------------------------------- #
def _mk_S_bruteforce(vals):
    s = 0
    for i, j in combinations(range(len(vals)), 2):
        d = vals[j] - vals[i]
        s += (d > 0) - (d < 0)
    return s


def test_mk_insufficient_for_short_series():
    r = mann_kendall([1, 2])
    assert r.direction == "insufficient"
    assert r.p_value == 1.0
    assert r.n == 2


def test_mk_strictly_increasing():
    r = mann_kendall([1, 2, 3, 4, 5, 6])
    assert r.s == _mk_S_bruteforce([1, 2, 3, 4, 5, 6])
    assert r.s == 15  # 모든 쌍 증가
    assert abs(r.tau - 1.0) < 1e-12
    assert r.direction == "increasing"
    assert r.p_value < 0.05


def test_mk_strictly_decreasing():
    r = mann_kendall([9, 7, 5, 3, 1, 0])
    assert r.s == -15
    assert abs(r.tau + 1.0) < 1e-12
    assert r.direction == "decreasing"


def test_mk_flat_all_equal():
    r = mann_kendall([3, 3, 3, 3, 3])
    assert r.s == 0
    assert r.tau == 0.0
    assert r.direction == "flat"
    assert r.p_value == 1.0


def test_mk_S_matches_bruteforce_random():
    for seed in range(200):
        random.seed(seed)
        n = random.randint(3, 25)
        vals = [random.randint(0, 6) for _ in range(n)]
        assert mann_kendall(vals).s == _mk_S_bruteforce(vals)


def test_mk_tau_bounds():
    for seed in range(100):
        random.seed(seed + 7)
        vals = [random.randint(0, 4) for _ in range(random.randint(3, 20))]
        r = mann_kendall(vals)
        assert -1.0 - 1e-9 <= r.tau <= 1.0 + 1e-9
        assert 0.0 <= r.p_value <= 1.0


def test_trend_test_fills_gaps_with_zero():
    # 2015=5편, 2018=0(빠짐), 2020=5편 → 조밀 시계열은 6포인트
    counts = {2015: 5, 2020: 5}
    series = yearly_series_dense(counts)
    assert [y for y, _ in series] == [2015, 2016, 2017, 2018, 2019, 2020]
    assert [v for _, v in series] == [5, 0, 0, 0, 0, 5]
    assert trend_test(counts).n == 6


def test_yearly_series_dense_empty():
    assert yearly_series_dense({}) == []


# --------------------------------------------------------------------------- #
# CAGR
# --------------------------------------------------------------------------- #
def test_cagr_basic_doubling():
    # 2010:10 → 2020:20, 10년 → (20/10)^(1/10)-1
    g = growth_summary({2010: 10, 2020: 20})
    assert g["cagr"] is not None
    assert abs(g["cagr"] - (2 ** (1 / 10) - 1)) < 1e-12


def test_cagr_none_when_zero_endpoint_or_single_year():
    assert growth_summary({2020: 5})["cagr"] is None          # 1년
    assert growth_summary({2010: 0, 2020: 5})["cagr"] is None  # 첫해 0
    assert growth_summary({2010: 5, 2020: 0})["cagr"] is None  # 끝해 0


# --------------------------------------------------------------------------- #
# gap_pairs 의 q-value / FDR-over-all-tested
# --------------------------------------------------------------------------- #
def test_gap_qvalue_present_and_bounded():
    arts = [
        _mk("1", 2020, ["Sleep", "Respiration"]),
        _mk("2", 2020, ["Sleep", "EEG"]),
        _mk("3", 2020, ["Sleep", "Heart Rate"]),
        _mk("4", 2020, ["Sleep", "Respiration"]),
        _mk("5", 2020, ["EEG", "Heart Rate"]),
        _mk("6", 2020, ["Sleep", "Respiration"]),
    ]
    gaps = gap_pairs(arts, top_k=4, min_expected=1.0, max_lift=2.0)
    for g in gaps:
        assert 0.0 <= g.q_value <= 1.0
        assert g.q_value >= g.p_value - 1e-12  # FDR 보정은 p 이상


def test_gap_fdr_computed_over_all_tested_not_just_survivors():
    # max_lift 로 일부만 살아남아도 q 는 '검정한 전체'(기대>=min_expected) 기준.
    # 살아남은 항목의 q 가 raw p 보다 (검정 수만큼) 커져 있어야 한다.
    arts = [
        _mk("1", 2020, ["A", "B"]),
        _mk("2", 2020, ["A", "C"]),
        _mk("3", 2020, ["A", "D"]),
        _mk("4", 2020, ["B", "C"]),
        _mk("5", 2020, ["B", "D"]),
        _mk("6", 2020, ["C", "D"]),
        _mk("7", 2020, ["A", "B"]),
        _mk("8", 2020, ["C", "D"]),
    ]
    gaps = gap_pairs(arts, top_k=4, min_expected=1.0, max_lift=0.5)
    # 검정된 후보가 여러 개이므로, 최상위 gap 의 q > p 여야(단일 검정이 아님).
    assert gaps
    top = gaps[0]
    assert top.q_value > top.p_value


def test_gap_observed_matches_bruteforce_cooccurrence():
    # 효율적 co-occurrence(_cooccurrence) 가 전수 스캔과 동일한 observed 를 낸다.
    random.seed(42)
    vocab = [f"T{i}" for i in range(8)]
    arts = []
    for pid in range(60):
        k = random.randint(1, 5)
        mesh = random.sample(vocab, k)
        arts.append(_mk(str(pid), 2020, mesh))
    gaps = gap_pairs(arts, top_k=8, min_expected=0.0, max_lift=10.0)
    for g in gaps:
        brute = sum(1 for a in arts if g.term_a in a.mesh and g.term_b in a.mesh)
        assert g.observed == brute
        assert abs(g.expected - g.count_a * g.count_b / len(arts)) < 1e-9


def test_gap_pairs_empty_corpus():
    assert gap_pairs([], top_k=5) == []


# --------------------------------------------------------------------------- #
# 회귀: 큰 N 오버플로 / 정확도 (edge-case 리뷰 Defect 1)
# --------------------------------------------------------------------------- #
def test_hypergeom_no_overflow_large_N():
    # 예전엔 comb() 정수가 float 로 못 들어가 OverflowError 가 났다.
    for N, K, n, k in [(5000, 2500, 2400, 100), (8000, 4000, 30, 0), (1030, 515, 515, 200)]:
        p = hypergeom_lower_tail(N, K, n, k)
        assert 0.0 <= p <= 1.0


def test_hypergeom_large_matches_exact_small_scaling():
    # 대칭성/단조성: k 가 커질수록 하단꼬리는 비감소.
    prev = -1.0
    for k in range(0, 40):
        p = hypergeom_lower_tail(4000, 200, 300, k)
        assert p >= prev - 1e-12
        prev = p
    assert abs(hypergeom_lower_tail(4000, 200, 300, min(200, 300)) - 1.0) < 1e-9


def test_hypergeom_log_path_matches_exact_rational():
    """N>60 의 log 경로를 **값으로** 고정한다.

    회귀 배경: 예전 테스트는 단조성과 '꼬리 끝=1.0'(그 값은 조기반환이라 합산 루프를
    타지도 않는다)만 봤다. 그래서 log 경로 합산의 off-by-one 을 scipy 가 설치된
    환경에서만 잡을 수 있었다 — scipy 는 이 패키지의 의존성이 아니다.
    """
    from fractions import Fraction
    from math import comb

    def exact(N, K, n, k):
        lo = max(0, n - (N - K))
        return Fraction(
            sum(comb(K, i) * comb(N - K, n - i) for i in range(lo, k + 1)), comb(N, n)
        )

    for N, K, n, k in [(61, 30, 20, 4), (100, 50, 40, 15), (200, 30, 80, 5),
                       (777, 111, 222, 20), (2000, 500, 400, 90)]:
        got = hypergeom_lower_tail(N, K, n, k)
        assert got == pytest.approx(float(exact(N, K, n, k)), rel=1e-9, abs=1e-13)


def test_hypergeom_small_still_exact_integer_path():
    # 작은 N(<=60)은 정확 정수 경로 — 손계산값과 정확히 일치.
    assert abs(hypergeom_lower_tail(18, 8, 6, 0) - 210 / 18564) < 1e-12


# --------------------------------------------------------------------------- #
# T1: Mann–Kendall 정확한 p/z/tau (동률 포함, 독립 손계산)
# --------------------------------------------------------------------------- #
def test_mk_exact_values_with_ties():
    # 시계열 [1, 2, 2, 3]: 쌍(1,2)(1,2)(1,3)(2,2)(2,3)(2,3) → S: +1+1+1+0+1+1 = 5
    # 동률군: 값 2 가 2개 → tie_term = 2*1*9 = 18
    # Var = [4*3*13 - 18]/18 = [156-18]/18 = 138/18 = 7.6667
    # z = (S-1)/sqrt(Var) = 4/sqrt(7.6667) = 1.44463
    # n0 = 6, ties_x(값2)=1 → denom = sqrt((6-1)*6)=sqrt(30)=5.4772
    # tau = 5/5.4772 = 0.91287
    import math

    r = mann_kendall([1, 2, 2, 3])
    assert r.s == 5
    assert abs(r.z - 4 / math.sqrt(138 / 18)) < 1e-9
    assert abs(r.tau - 5 / math.sqrt(30)) < 1e-9
    expected_p = 2 * (1 - 0.5 * (1 + math.erf((4 / math.sqrt(138 / 18)) / math.sqrt(2))))
    assert abs(r.p_value - expected_p) < 1e-12


def test_mk_flat_direction_when_nonzero_but_insignificant():
    # 약한 추세(S != 0)지만 유의하지 않으면 direction='flat'.
    r = mann_kendall([2, 1, 3, 2, 4, 1, 3])
    assert r.s != 0 or r.direction == "flat"
    if r.p_value >= 0.05:
        assert r.direction == "flat"


# --------------------------------------------------------------------------- #
# T2: BH 독립 손계산(비단조 raw p)
# --------------------------------------------------------------------------- #
def test_bh_handcomputed_nonmonotone():
    # p=[0.04, 0.01], m=2. 정렬: 0.01(rank1)->0.01*2/1=0.02; 0.04(rank2)->0.04*2/2=0.04
    # step-up 단조: q(0.01)=min(0.02,0.04)=0.02, q(0.04)=0.04
    q = benjamini_hochberg([0.04, 0.01])
    assert abs(q[0] - 0.04) < 1e-12
    assert abs(q[1] - 0.02) < 1e-12


def test_term_trends_equivalent_and_fast():
    # 역색인 재작성이 결과 동일함을 보증(작은 코퍼스 손검증) + 큰 코퍼스에서 빠름.
    import time

    from pubgap.analyze import term_trends
    from pubgap.records import Article

    arts = [
        Article("1", 2016, "A", "t", ["Sleep", "Respiration"]),
        Article("2", 2016, "A", "t", ["Sleep", "EEG"]),
        Article("3", 2021, "B", "t", ["Sleep", "Heart Rate"]),
        Article("4", 2022, "C", "t", ["Sleep", "Respiration"]),
    ]
    tr = {t.term: t for t in term_trends(arts, min_total=1)}
    # split=(2016+2022+1)//2=2019. early=1,2(2편) recent=3,4(2편)
    assert tr["Sleep"].early_count == 2 and tr["Sleep"].recent_count == 2
    assert tr["Respiration"].early_count == 1 and tr["Respiration"].recent_count == 1

    big = [Article(str(i), 2010 + i % 12, "J", "t", [f"T{i}_{j}" for j in range(15)])
           for i in range(3000)]
    t0 = time.time()
    term_trends(big, min_total=1)
    assert time.time() - t0 < 5.0  # 예전엔 분 단위, 이제 1초 이내


# --------------------------------------------------------------------------- #
# 체크 태그 필터 (usefulness 리뷰 #1)
# --------------------------------------------------------------------------- #
def test_strip_check_tags_removes_demographic_tags():
    arts = [
        _mk("1", 2020, ["Humans", "Male", "Sleep", "Respiration"]),
        _mk("2", 2020, ["Humans", "Female", "Adult", "Sleep"]),
    ]
    stripped = strip_check_tags(arts)
    assert stripped[0].mesh == ["Sleep", "Respiration"]
    assert stripped[1].mesh == ["Sleep"]
    # 원본 불변
    assert "Humans" in arts[0].mesh


def test_check_tags_contains_expected():
    for t in ["Humans", "Male", "Female", "Adult", "Aged", "Animals", "Mice"]:
        assert t in CHECK_TAGS


def test_strip_check_tags_no_change_returns_same_object():
    arts = [_mk("1", 2020, ["Sleep", "EEG"])]
    stripped = strip_check_tags(arts)
    assert stripped[0] is arts[0]  # 바뀐 게 없으면 새 객체 안 만듦


def test_top_mesh_after_strip_excludes_check_tags():
    arts = [_mk(str(i), 2020, ["Humans", "Male", "Sleep"]) for i in range(5)]
    tm = dict(top_mesh(strip_check_tags(arts)))
    assert tm == {"Sleep": 5}


# --------------------------------------------------------------------------- #
# Swanson ABC 가교 + 대표 PMID (usefulness 리뷰 #2, #4)
# --------------------------------------------------------------------------- #
def _bridge_corpus():
    arts = []
    # A=Sleep, B=Inflammation; 가교 C=Autonomic. A&C, C&B 흔하고 A&B 없음.
    for i in range(10):
        arts.append(_mk(f"a{i}", 2020, ["Sleep", "Autonomic"]))
    for i in range(10):
        arts.append(_mk(f"b{i}", 2020, ["Inflammation", "Autonomic"]))
    for i in range(4):
        arts.append(_mk(f"c{i}", 2020, ["Sleep"]))
    for i in range(4):
        arts.append(_mk(f"d{i}", 2020, ["Inflammation"]))
    return arts


def test_gap_bridge_terms_identified():
    gaps = gap_pairs(_bridge_corpus(), top_k=5, min_expected=1.0, max_lift=1.0)
    # Sleep×Inflammation 은 0-observed 공백; 가교로 Autonomic 이 잡혀야 한다.
    g = next(x for x in gaps if {x.term_a, x.term_b} == {"Sleep", "Inflammation"})
    assert g.observed == 0
    assert g.bridges, "가교가 있어야 함"
    top_bridge = g.bridges[0]
    assert top_bridge[0] == "Autonomic"
    assert top_bridge[1] == 10 and top_bridge[2] == 10  # A&C, C&B


def test_gap_example_pmids_populated():
    gaps = gap_pairs(_bridge_corpus(), top_k=5, min_expected=1.0, max_lift=1.0)
    g = next(x for x in gaps if {x.term_a, x.term_b} == {"Sleep", "Inflammation"})
    assert g.pmids_a and g.pmids_b  # A만/B만 다룬 대표 논문
    assert len(g.pmids_a) <= 3 and len(g.pmids_b) <= 3
    assert g.pmids_both == []  # observed=0 이므로 함께는 없음


def test_gap_bridges_disabled_when_zero():
    gaps = gap_pairs(_bridge_corpus(), top_k=5, min_expected=1.0, max_lift=1.0, bridge_top_n=0)
    for g in gaps:
        assert g.bridges == []


def test_gap_pmids_both_when_observed_positive():
    arts = [
        _mk("1", 2020, ["Sleep", "EEG"]),
        _mk("2", 2020, ["Sleep", "Respiration"]),
        _mk("3", 2020, ["Sleep", "Respiration"]),
        _mk("4", 2020, ["EEG"]),
        _mk("5", 2020, ["Respiration"]),
    ]
    gaps = gap_pairs(arts, top_k=4, min_expected=0.1, max_lift=5.0)
    g = next(x for x in gaps if {x.term_a, x.term_b} == {"Sleep", "EEG"})
    assert g.observed == 1
    assert g.pmids_both == ["1"]  # 함께 다룬 논문
