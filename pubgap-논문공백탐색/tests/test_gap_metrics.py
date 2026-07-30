"""공백쌍의 연관 지표(deficit/Jaccard/Ochiai/nPMI)와 시간 추이·정렬 — 손계산 대조."""

from math import log2, sqrt

import pytest

from pubgap.analyze import (
    GAP_SORTS,
    GapPair,
    _classify_gap_trend,
    _pair_metrics,
    gap_pairs,
    sort_gaps,
)
from pubgap.records import Article


def _mk(pmid, year, mesh):
    return Article(pmid=pmid, year=year, journal="J", title="t", mesh=list(mesh))


# --------------------------------------------------------------------------- #
# _pair_metrics — 정의식 그대로 재계산해 대조
# --------------------------------------------------------------------------- #
def test_pair_metrics_hand_computed():
    n, ca, cb, obs = 100, 20, 10, 1
    jac, cos, npmi = _pair_metrics(n, ca, cb, obs)
    assert jac == pytest.approx(1 / (20 + 10 - 1))
    assert cos == pytest.approx(1 / sqrt(20 * 10))
    p_ab, p_a, p_b = obs / n, ca / n, cb / n
    assert npmi == pytest.approx(log2(p_ab / (p_a * p_b)) / -log2(p_ab))


def test_npmi_is_minus_one_when_never_together():
    _, _, npmi = _pair_metrics(100, 20, 10, 0)
    assert npmi == -1.0


def test_npmi_is_zero_under_independence():
    """관측이 정확히 기대와 같으면 PMI=0 → nPMI=0."""
    n, ca, cb = 100, 20, 10
    obs = ca * cb // n  # 기대 = 2.0
    _, _, npmi = _pair_metrics(n, ca, cb, obs)
    assert npmi == pytest.approx(0.0, abs=1e-12)


def test_npmi_is_plus_one_when_perfectly_coincident():
    """A 와 B 가 항상 같이 나오면(cA=cB=obs) nPMI = +1."""
    _, _, npmi = _pair_metrics(100, 10, 10, 10)
    assert npmi == pytest.approx(1.0)


def test_pair_metrics_bounds_are_respected():
    for n in (1, 5, 50, 1000):
        for ca in (0, 1, n):
            for cb in (0, 1, n):
                for obs in range(0, min(ca, cb) + 1):
                    jac, cos, npmi = _pair_metrics(n, ca, cb, obs)
                    assert 0.0 <= jac <= 1.0
                    assert 0.0 <= cos <= 1.0 + 1e-12
                    assert -1.0 <= npmi <= 1.0


def test_pair_metrics_degenerate_inputs_do_not_crash():
    assert _pair_metrics(0, 0, 0, 0) == (0.0, 0.0, 0.0)
    assert _pair_metrics(10, 0, 5, 0)[2] == 0.0  # cA=0 → 정의 불가 → 0


# --------------------------------------------------------------------------- #
# gap_trend — 구간 논문 수로 정규화한 비율 비교
# --------------------------------------------------------------------------- #
def test_gap_trend_normalises_by_segment_size():
    # 최근 구간에 논문이 4배 많다. 동시등장 2편(초기 1편)은 '비율'로는 감소다.
    assert _classify_gap_trend(1, 2, 10, 40) == "widening"
    assert _classify_gap_trend(1, 5, 10, 40) == "closing"
    assert _classify_gap_trend(1, 4, 10, 40) == "stable"


def test_gap_trend_empty_beats_the_single_segment_guard():
    """'한 번도 함께 안 나왔다'는 시간과 무관한 사실이다.

    회귀: 구간이 하나뿐인 코퍼스(전부 같은 해)에서 `n_early<=0` 가드가 먼저 걸려,
    가장 강한 신호인 완전공백이 '판단불가'로 감춰졌다.
    """
    assert _classify_gap_trend(0, 0, 0, 10) == "empty"
    assert _classify_gap_trend(0, 0, 10, 0) == "empty"
    assert _classify_gap_trend(0, 0, 5, 5) == "empty"
    # 동시등장이 있는데 구간이 하나뿐이면 방향은 말할 수 없다.
    assert _classify_gap_trend(0, 4, 0, 10) == "unknown"
    assert _classify_gap_trend(4, 0, 10, 0) == "unknown"


# --------------------------------------------------------------------------- #
# gap_pairs 통합 — 필드가 실제로 채워지고 서로 일관되는가
# --------------------------------------------------------------------------- #
def _corpus():
    """A 와 B 는 각각 흔하지만 함께는 한 번만. C 는 둘 다와 자주 엮인다.

    C 가 *모든* 논문에 붙으면 가교로서 정보량이 0이라 배제되므로, C 없는 논문을
    섞어 유병률을 임계(BRIDGE_MAX_PREVALENCE) 아래로 둔다.
    """
    arts = []
    for i in range(10):
        arts.append(_mk(f"a{i}", 2015 + (i % 3), ["A", "C"]))
    for i in range(10):
        arts.append(_mk(f"b{i}", 2021 + (i % 3), ["B", "C"]))
    arts.append(_mk("both", 2023, ["A", "B", "C"]))
    for i in range(8):
        arts.append(_mk(f"x{i}", 2018 + (i % 4), ["A"] if i % 2 else ["B"]))
    return arts


def test_gap_pairs_fill_new_metric_fields_consistently():
    gaps = gap_pairs(_corpus(), top_k=5, min_expected=1.0, max_lift=1.0)
    ab = [g for g in gaps if {g.term_a, g.term_b} == {"A", "B"}]
    assert ab, "A×B 가 공백 후보에 있어야 한다"
    g = ab[0]
    assert g.observed == 1
    assert g.deficit == pytest.approx(g.expected - g.observed)
    jac, cos, npmi = _pair_metrics(len(_corpus()), g.count_a, g.count_b, g.observed)
    assert (g.jaccard, g.cosine, g.npmi) == pytest.approx((jac, cos, npmi))
    assert g.observed_early + g.observed_recent == g.observed
    assert g.gap_trend in ("closing", "widening", "stable", "unknown")


def test_gap_pairs_bridge_still_found():
    gaps = gap_pairs(_corpus(), top_k=5, min_expected=1.0, max_lift=1.0)
    g = [x for x in gaps if {x.term_a, x.term_b} == {"A", "B"}][0]
    assert g.bridges and g.bridges[0][0] == "C"


def test_no_bridges_when_bridge_top_n_zero():
    gaps = gap_pairs(_corpus(), top_k=5, min_expected=1.0, max_lift=1.0, bridge_top_n=0)
    assert all(g.bridges == [] for g in gaps)
    # 가교를 꺼도 대표 PMID·시간 추이는 그대로 채워져야 한다.
    assert any(g.pmids_a or g.pmids_b for g in gaps)


# --------------------------------------------------------------------------- #
# sort_gaps
# --------------------------------------------------------------------------- #
def _g(a, b, lift, expected, q, npmi=0.0, observed=0):
    return GapPair(
        a, b, observed, expected, lift, 10, 10, 0.5, q_value=q,
        deficit=expected - observed, npmi=npmi,
    )


def test_sort_gaps_by_each_key():
    gaps = [
        _g("A", "B", 0.5, 10.0, 0.20, npmi=-0.1, observed=5),   # deficit 5
        _g("C", "D", 0.0, 3.0, 0.01, npmi=-1.0, observed=0),    # deficit 3
        _g("E", "F", 0.2, 40.0, 0.50, npmi=-0.4, observed=8),   # deficit 32
    ]
    assert [g.term_a for g in sort_gaps(gaps, "lift")] == ["C", "E", "A"]
    assert [g.term_a for g in sort_gaps(gaps, "deficit")] == ["E", "A", "C"]
    assert [g.term_a for g in sort_gaps(gaps, "q")] == ["C", "A", "E"]
    assert [g.term_a for g in sort_gaps(gaps, "expected")] == ["E", "A", "C"]
    assert [g.term_a for g in sort_gaps(gaps, "npmi")] == ["C", "E", "A"]


def test_sort_gaps_is_deterministic_on_ties():
    gaps = [_g("B", "Z", 0.0, 5.0, 0.1), _g("A", "Y", 0.0, 5.0, 0.1)]
    for key in GAP_SORTS:
        assert [g.term_a for g in sort_gaps(gaps, key)] == ["A", "B"]
        assert [g.term_a for g in sort_gaps(list(reversed(gaps)), key)] == ["A", "B"]


def test_sort_gaps_rejects_unknown_key():
    with pytest.raises(ValueError):
        sort_gaps([], "nope")


def test_gap_pairs_sort_option_changes_order_only():
    corpus = _corpus()
    by_lift = gap_pairs(corpus, top_k=5, min_expected=1.0, max_lift=1.0, sort="lift")
    by_def = gap_pairs(corpus, top_k=5, min_expected=1.0, max_lift=1.0, sort="deficit")
    key = lambda g: (g.term_a, g.term_b)
    assert sorted(map(key, by_lift)) == sorted(map(key, by_def))
