"""Tests for the categorical / ordinal agreement engine.

Reference values are hand-computed from the published formulae (Cohen 1960;
Fleiss, Cohen & Everitt 1969; Byrt 1993; Gwet 2008) — see test docstrings.
Cross-checks against sklearn/statsmodels live in test_crosscheck_scipy.py.
"""

import math

import pytest

from agreestat import categorical as C
from agreestat.catanalyze import analyze_categorical


def _cm(counts, cats=None):
    """Build a ConfusionMatrix straight from a count table."""
    k = len(counts)
    cats = cats or [str(i) for i in range(k)]
    n = sum(sum(r) for r in counts)
    row = [sum(r) for r in counts]
    col = [sum(counts[i][j] for i in range(k)) for j in range(k)]
    return C.ConfusionMatrix(cats, counts, n, row, col)


def _expand(counts, cats=None):
    """Expand a count table into paired label lists."""
    k = len(counts)
    cats = cats or [str(i) for i in range(k)]
    a, b = [], []
    for i in range(k):
        for j in range(k):
            a.extend([cats[i]] * counts[i][j])
            b.extend([cats[j]] * counts[i][j])
    return a, b


# --------------------------------------------------------------------------
# Confusion matrix
# --------------------------------------------------------------------------
def test_confusion_matrix_counts_and_marginals():
    a = ["x", "x", "y", "y", "y"]
    b = ["x", "y", "y", "y", "x"]
    cm = C.confusion_matrix(a, b)
    assert cm.categories == ["x", "y"]
    assert cm.counts == [[1, 1], [1, 2]]
    assert cm.row_totals == [2, 3]
    assert cm.col_totals == [2, 3]
    assert cm.n == 5
    assert cm.po == pytest.approx(3 / 5)


def test_confusion_matrix_rejects_length_mismatch():
    with pytest.raises(ValueError):
        C.confusion_matrix(["a", "b"], ["a"])


def test_confusion_matrix_rejects_label_missing_from_categories():
    with pytest.raises(ValueError, match="missing observed labels"):
        C.confusion_matrix(["a", "z"], ["a", "a"], categories=["a", "b"])


def test_confusion_matrix_honours_explicit_category_order():
    a, b = ["N2", "W"], ["N2", "W"]
    cm = C.confusion_matrix(a, b, categories=["W", "N1", "N2"])
    assert cm.categories == ["W", "N1", "N2"]
    # N1 is unused -> an all-zero row and column, but the table stays square.
    assert cm.counts == [[1, 0, 0], [0, 0, 0], [0, 0, 1]]


def test_order_categories_sorts_numeric_labels_numerically():
    cats, notes = C.order_categories(["10", "2", "1"])
    assert cats == ["1", "2", "10"]      # not lexicographic ["1","10","2"]
    assert notes == []


def test_order_categories_warns_on_arbitrary_alphabetical_order():
    cats, notes = C.order_categories(["severe", "mild", "moderate"])
    assert cats == ["mild", "moderate", "severe"]
    assert any("--categories" in n for n in notes)


def test_order_categories_rejects_incomplete_explicit_list():
    with pytest.raises(ValueError, match="없는 값"):
        C.order_categories(["a", "b"], explicit=["a"])


# --------------------------------------------------------------------------
# Cohen's kappa — hand-computed references
# --------------------------------------------------------------------------
def test_kappa_matches_hand_computation():
    """Table [[20,5],[10,15]]: po=.7, pe=(25*30+25*20)/2500=.5, k=.4."""
    cm = _cm([[20, 5], [10, 15]])
    k = C.kappa(cm)
    assert k.po == pytest.approx(0.70)
    assert k.pe == pytest.approx(0.50)
    assert k.value == pytest.approx(0.40)


def test_kappa_is_one_for_perfect_agreement():
    cm = _cm([[10, 0], [0, 10]])
    k = C.kappa(cm)
    assert k.value == pytest.approx(1.0)
    assert k.ci_upper <= 1.0


def test_kappa_is_zero_at_exactly_chance_agreement():
    """Independent margins -> po == pe -> kappa == 0."""
    cm = _cm([[25, 25], [25, 25]])
    k = C.kappa(cm)
    assert k.value == pytest.approx(0.0)
    assert k.pvalue == pytest.approx(1.0)


def test_kappa_is_negative_for_systematic_disagreement():
    cm = _cm([[0, 25], [25, 0]])
    assert C.kappa(cm).value == pytest.approx(-1.0)


def test_kappa_ci_brackets_point_estimate_and_is_bounded():
    cm = _cm([[40, 5], [6, 49]])
    k = C.kappa(cm)
    assert k.ci_lower < k.value < k.ci_upper
    assert -1.0 <= k.ci_lower and k.ci_upper <= 1.0


def test_kappa_undefined_when_both_raters_use_one_category():
    cm = _cm([[30, 0], [0, 0]])
    k = C.kappa(cm)
    assert math.isnan(k.value)
    assert "pe=1" in k.note


def test_kappa_z_test_uses_h0_variance_not_ci_variance():
    """The H0 SE and the CI SE are different quantities (Fleiss 1969)."""
    cm = _cm([[20, 5], [10, 15]])
    k = C.kappa(cm)
    se_h0 = k.value / k.z
    assert not math.isclose(se_h0, k.se, rel_tol=1e-3)


def test_max_kappa_is_one_for_balanced_margins():
    cm = _cm([[20, 5], [5, 20]])
    assert C.kappa(cm).max_kappa == pytest.approx(1.0)


def test_max_kappa_below_one_for_imbalanced_margins():
    """Margins A=(45,5) vs B=(30,20) cannot both be satisfied by a perfect
    table, so kappa is structurally capped below 1."""
    cm = _cm([[30, 15], [0, 5]])
    k = C.kappa(cm)
    assert k.max_kappa < 1.0
    assert k.value <= k.max_kappa + 1e-12


# --------------------------------------------------------------------------
# Weighted kappa
# --------------------------------------------------------------------------
def test_weight_matrix_unweighted_is_identity():
    w, _ = C.weight_matrix(["a", "b", "c"], "unweighted")
    assert w == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_weight_matrix_linear_and_quadratic_endpoints():
    wl, _ = C.weight_matrix(["0", "1", "2"], "linear")
    wq, _ = C.weight_matrix(["0", "1", "2"], "quadratic")
    assert wl[0][2] == pytest.approx(0.0)   # farthest pair -> zero credit
    assert wq[0][2] == pytest.approx(0.0)
    assert wl[0][1] == pytest.approx(0.5)
    assert wq[0][1] == pytest.approx(0.75)  # 1 - (1/2)^2


def test_weight_matrix_uses_real_numeric_spacing():
    """Labels 0,1,4 are unequally spaced; weights must respect the values."""
    w, note = C.weight_matrix(["0", "1", "4"], "linear")
    assert w[0][1] == pytest.approx(1 - 1 / 4)
    assert "숫자값" in note


def test_weight_matrix_falls_back_to_rank_spacing_for_text_labels():
    w, note = C.weight_matrix(["mild", "moderate", "severe"], "linear")
    assert w[0][1] == pytest.approx(0.5)
    assert "순서" in note


def test_weighted_kappa_reduces_to_unweighted_on_two_categories():
    """With k=2 every disagreement weight is 0 -> weighted == unweighted."""
    cm = _cm([[20, 5], [10, 15]])
    assert C.kappa(cm, "linear").value == pytest.approx(C.kappa(cm).value)
    assert C.kappa(cm, "quadratic").value == pytest.approx(C.kappa(cm).value)


def test_weighted_kappa_exceeds_unweighted_when_errors_are_adjacent():
    """Near-miss disagreements get partial credit."""
    cm = _cm([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    assert C.kappa(cm, "linear").value > C.kappa(cm).value


def test_weighted_kappa_rejects_unknown_scheme():
    cm = _cm([[5, 1], [1, 5]])
    with pytest.raises(ValueError):
        C.kappa(cm, "cubic")


# --------------------------------------------------------------------------
# Gwet's AC1 / AC2
# --------------------------------------------------------------------------
def test_gwet_ac1_on_published_paradox_table():
    """Cicchetti-Feinstein table [[118,5],[2,0]] (Gwet 2008): po=.944,
    kappa~-0.02, AC1~0.94 — the canonical kappa-paradox example."""
    cm = _cm([[118, 5], [2, 0]])
    k = C.kappa(cm)
    ac = C.gwet_ac(cm)
    assert cm.po == pytest.approx(0.944)
    assert k.value == pytest.approx(-0.0234, abs=1e-3)
    assert ac.value == pytest.approx(0.9408, abs=1e-3)


def test_gwet_ac1_is_one_for_perfect_agreement():
    cm = _cm([[10, 0], [0, 10]])
    assert C.gwet_ac(cm).value == pytest.approx(1.0)


def test_gwet_ac1_resists_prevalence_where_kappa_collapses():
    """Same po, but one category dominates -> kappa craters, AC1 holds up."""
    balanced = _cm([[45, 5], [5, 45]])
    skewed = _cm([[90, 5], [5, 0]])
    assert balanced.po == pytest.approx(skewed.po)
    assert C.kappa(skewed).value < C.kappa(balanced).value - 0.4
    assert C.gwet_ac(skewed).value > C.kappa(skewed).value


def test_gwet_ac1_ci_brackets_estimate():
    cm = _cm([[40, 5], [6, 49]])
    ac = C.gwet_ac(cm)
    assert ac.ci_lower < ac.value < ac.ci_upper
    assert -1.0 <= ac.ci_lower and ac.ci_upper <= 1.0


def test_gwet_ac2_gives_partial_credit_for_adjacent_errors():
    cm = _cm([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    assert C.gwet_ac(cm, "quadratic").value > C.gwet_ac(cm).value


# --------------------------------------------------------------------------
# Scott's pi / Krippendorff's alpha
# --------------------------------------------------------------------------
def test_scott_pi_matches_hand_computation():
    """[[20,5],[10,15]]: pi_1=(25+30)/100=.55, pi_2=.45, pe=.505,
    pi=(.7-.505)/(1-.505)."""
    cm = _cm([[20, 5], [10, 15]])
    assert C.scott_pi(cm) == pytest.approx((0.7 - 0.505) / (1 - 0.505))


def test_krippendorff_alpha_is_one_for_perfect_agreement():
    cm = _cm([[10, 0], [0, 10]])
    assert C.krippendorff_alpha(cm) == pytest.approx(1.0)


def test_krippendorff_alpha_slightly_exceeds_scott_pi():
    """alpha applies a finite-sample correction to pi; they converge as n grows."""
    cm = _cm([[20, 5], [10, 15]])
    a, p = C.krippendorff_alpha(cm), C.scott_pi(cm)
    assert a > p
    assert a == pytest.approx(p, abs=0.02)


def test_krippendorff_ordinal_credits_near_misses():
    cm = _cm([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    assert (C.krippendorff_alpha(cm, "ordinal")
            > C.krippendorff_alpha(cm, "nominal"))


def test_krippendorff_interval_needs_numeric_labels():
    cm = _cm([[5, 1], [1, 5]], cats=["mild", "severe"])
    with pytest.raises(ValueError):
        C.krippendorff_alpha(cm, "interval")


def test_krippendorff_rejects_unknown_metric():
    cm = _cm([[5, 1], [1, 5]])
    with pytest.raises(ValueError):
        C.krippendorff_alpha(cm, "circular")


# --------------------------------------------------------------------------
# Paradox diagnostics
# --------------------------------------------------------------------------
def test_pabak_is_two_po_minus_one_for_2x2():
    cm = _cm([[40, 5], [5, 50]])
    d = C.paradox_diagnostics(cm, C.kappa(cm).value)
    assert d.pabak == pytest.approx(2 * cm.po - 1)


def test_prevalence_and_bias_indices_hand_computed():
    """[[118,5],[2,0]]: PI=|118-0|/125=.944, BI=|5-2|/125=.024."""
    cm = _cm([[118, 5], [2, 0]])
    d = C.paradox_diagnostics(cm, C.kappa(cm).value)
    assert d.prevalence_index == pytest.approx(0.944)
    assert d.bias_index == pytest.approx(0.024)
    assert d.paradox is True


def test_paradox_indices_are_nan_beyond_2x2():
    cm = _cm([[10, 1, 0], [1, 10, 1], [0, 1, 10]])
    d = C.paradox_diagnostics(cm, C.kappa(cm).value)
    assert math.isnan(d.prevalence_index)
    assert math.isnan(d.bias_index)
    assert not math.isnan(d.pabak)


def test_no_paradox_flag_on_healthy_table():
    cm = _cm([[45, 5], [5, 45]])
    assert C.paradox_diagnostics(cm, C.kappa(cm).value).paradox is False


# --------------------------------------------------------------------------
# Per-category agreement
# --------------------------------------------------------------------------
def test_specific_agreement_is_ppa_npa_for_2x2():
    """[[80,10],[5,105]]: PPA=2*80/(90+85)=0.914, NPA=2*105/(110+115)=0.933."""
    cm = _cm([[80, 10], [5, 105]], cats=["pos", "neg"])
    pcs = C.per_category_agreement(cm)
    assert pcs[0].specific_agreement == pytest.approx(2 * 80 / (90 + 85))
    assert pcs[1].specific_agreement == pytest.approx(2 * 105 / (110 + 115))


def test_per_category_one_vs_rest_kappa_equals_kappa_for_2x2():
    cm = _cm([[80, 10], [5, 105]])
    pcs = C.per_category_agreement(cm)
    assert pcs[0].kappa_ovr == pytest.approx(C.kappa(cm).value)


def test_per_category_handles_never_used_category():
    cm = C.confusion_matrix(["a", "a", "b"], ["a", "b", "b"],
                            categories=["a", "b", "c"])
    pcs = C.per_category_agreement(cm)
    assert pcs[2].n_a == 0 and pcs[2].n_b == 0
    assert math.isnan(pcs[2].specific_agreement)


def test_specific_agreement_ci_brackets_estimate():
    cm = _cm([[80, 10], [5, 105]])
    for pc in C.per_category_agreement(cm):
        assert pc.sa_ci[0] <= pc.specific_agreement <= pc.sa_ci[1]
        assert 0.0 <= pc.sa_ci[0] and pc.sa_ci[1] <= 1.0


# --------------------------------------------------------------------------
# Marginal homogeneity
# --------------------------------------------------------------------------
def test_mcnemar_exact_matches_hand_computed_binomial():
    """b=10, c=2: two-sided exact p = 2*P(X<=2), X~Bin(12,.5)."""
    cm = _cm([[50, 10], [2, 38]])
    m = C.mcnemar(cm)
    expected = 2 * sum(math.comb(12, i) for i in range(3)) / 2 ** 12
    assert m.pvalue == pytest.approx(expected)
    assert m.b == 10 and m.c == 2


def test_mcnemar_p_is_one_when_discordants_are_symmetric():
    cm = _cm([[50, 5], [5, 40]])
    assert C.mcnemar(cm).pvalue == pytest.approx(1.0)


def test_mcnemar_handles_no_discordant_cells():
    cm = _cm([[50, 0], [0, 40]])
    m = C.mcnemar(cm)
    assert m.available and m.pvalue == 1.0


def test_mcnemar_switches_to_chi_square_for_large_discordants():
    cm = _cm([[10, 900], [700, 10]])
    m = C.mcnemar(cm, exact_max=100)
    assert "chi-square" in m.name
    assert m.pvalue < 0.001


def test_mcnemar_rejects_non_2x2():
    cm = _cm([[5, 1, 0], [1, 5, 1], [0, 1, 5]])
    assert C.mcnemar(cm).available is False


def test_stuart_maxwell_zero_when_margins_identical():
    cm = _cm([[10, 2, 0], [2, 10, 2], [0, 2, 10]])
    m = C.stuart_maxwell(cm)
    assert m.statistic == pytest.approx(0.0)
    assert m.pvalue == pytest.approx(1.0)


def test_stuart_maxwell_detects_marginal_shift():
    """B systematically upgrades: A's margins (60,30,10) vs B's (30,40,30)."""
    cm = _cm([[30, 30, 0], [0, 10, 20], [0, 0, 10]])
    m = C.stuart_maxwell(cm)
    assert m.available and m.pvalue < 0.01
    assert m.df == 2


def test_stuart_maxwell_matches_mcnemar_on_2x2():
    """Stuart-Maxwell reduces to the uncorrected McNemar chi-square."""
    cm = _cm([[50, 20], [8, 30]])
    sm = C.stuart_maxwell(cm)
    expected = (20 - 8) ** 2 / (20 + 8)  # uncorrected McNemar
    assert sm.statistic == pytest.approx(expected)
    assert sm.df == 1


def test_stuart_maxwell_drops_unused_categories():
    cm = C.confusion_matrix(["a", "a", "b"], ["a", "b", "b"],
                            categories=["a", "b", "c"])
    m = C.stuart_maxwell(cm)
    assert m.available          # 'c' dropped rather than making V singular
    assert m.df == 1


# --------------------------------------------------------------------------
# Interpretation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (-0.2, "poor"), (0.10, "slight"), (0.30, "fair"), (0.50, "moderate"),
    (0.70, "substantial"), (0.90, "almost perfect"),
])
def test_interpret_kappa_landis_koch_bands(value, expected):
    assert C.interpret_kappa(value).split(" / ")[0] == expected


def test_interpret_kappa_handles_nan():
    assert "판정 불가" in C.interpret_kappa(float("nan"))


# --------------------------------------------------------------------------
# analyze_categorical orchestration
# --------------------------------------------------------------------------
def test_analyze_categorical_end_to_end():
    a, b = _expand([[20, 5], [10, 15]], cats=["neg", "pos"])
    res = analyze_categorical(a, b, name_a="R1", name_b="R2")
    assert res.n == 50
    assert res.kappa.value == pytest.approx(0.40)
    assert res.kappa_weighted is None          # nominal -> no weighting
    assert res.headline == "unweighted"
    assert res.primary is res.kappa


def test_analyze_categorical_ordinal_headlines_weighted_kappa():
    a, b = _expand([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    res = analyze_categorical(a, b, ordinal=True)
    assert res.weights == "quadratic"
    assert res.headline == "weighted"
    assert res.primary is res.kappa_weighted


def test_analyze_categorical_weights_flag_implies_ordinal():
    a, b = _expand([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    res = analyze_categorical(a, b, weights="linear")
    assert res.ordinal is True
    assert res.weights == "linear"


def test_analyze_categorical_rejects_single_category():
    with pytest.raises(ValueError, match="범주가 1개"):
        analyze_categorical(["a"] * 5, ["a"] * 5)


def test_analyze_categorical_rejects_too_few_rows():
    with pytest.raises(ValueError, match="at least 2"):
        analyze_categorical(["a"], ["b"])


def test_analyze_categorical_rejects_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        analyze_categorical(["a", "b"], ["a"])


def test_analyze_categorical_warns_on_paradox():
    a, b = _expand([[118, 5], [2, 0]])
    res = analyze_categorical(a, b)
    assert res.paradox.paradox
    assert any("역설" in w for w in res.warnings)


def test_analyze_categorical_warns_on_marginal_heterogeneity():
    a, b = _expand([[30, 30, 0], [0, 10, 20], [0, 0, 10]])
    res = analyze_categorical(a, b)
    assert any("주변분포가 다릅니다" in w for w in res.warnings)


def test_analyze_categorical_warns_when_ci_lower_grade_is_worse():
    a, b = _expand([[8, 2], [2, 8]])   # small n -> wide CI
    res = analyze_categorical(a, b)
    assert any("신뢰구간 하한" in w for w in res.warnings)


def test_analyze_categorical_warns_on_small_sample():
    a, b = _expand([[6, 2], [2, 6]])
    res = analyze_categorical(a, b)
    assert any("표본이 작습니다" in w for w in res.warnings)


def test_analyze_categorical_warns_on_max_kappa_ceiling():
    a, b = _expand([[30, 15], [0, 5]])
    res = analyze_categorical(a, b)
    assert any("최대 kappa" in w for w in res.warnings)


def test_analyze_categorical_warns_on_one_sided_category_use():
    a = ["a"] * 20 + ["b"] * 20
    b = ["a"] * 20 + ["c"] * 20
    res = analyze_categorical(a, b)
    assert any("한쪽 평가자만 사용한 범주" in w for w in res.warnings)


def test_min_kappa_is_judged_on_ci_lower_bound_not_point_estimate():
    """The whole point: a point estimate above the bar with a CI lower bound
    below it must NOT pass."""
    a, b = _expand([[8, 2], [2, 8]])   # kappa=0.6 point, wide CI
    res = analyze_categorical(a, b, min_kappa=0.6)
    assert res.kappa.value == pytest.approx(0.6)
    assert res.kappa.ci_lower < 0.6
    assert res.meets_threshold is False
    assert any("기준 미충족" in w for w in res.warnings)


def test_min_kappa_passes_when_ci_lower_clears_bar():
    a, b = _expand([[200, 5], [5, 200]])
    res = analyze_categorical(a, b, min_kappa=0.6)
    assert res.meets_threshold is True
    assert any("기준 충족" in w for w in res.warnings)


def test_analyze_categorical_propagates_category_order_note():
    a, b = _expand([[10, 2], [2, 10]], cats=["mild", "severe"])
    res = analyze_categorical(a, b, ordinal=True)
    assert any("--categories" in w for w in res.warnings)


def test_analyze_categorical_respects_explicit_category_order():
    a, b = _expand([[10, 2], [2, 10]], cats=["severe", "mild"])
    res = analyze_categorical(a, b, categories=["mild", "severe"], ordinal=True)
    assert res.cm.categories == ["mild", "severe"]
    assert not any("--categories" in w for w in res.warnings)


def test_analyze_categorical_accepts_numeric_labels_as_strings():
    a, b = _expand([[10, 2], [2, 10]], cats=["1", "2"])
    res = analyze_categorical([int(x) for x in a], [int(x) for x in b])
    assert res.cm.categories == ["1", "2"]


def test_analyze_categorical_reports_unused_declared_category():
    a, b = _expand([[10, 2], [2, 10]], cats=["W", "N2"])
    res = analyze_categorical(a, b, categories=["W", "N1", "N2"])
    assert any("한 번도 나타나지 않은 범주" in w for w in res.warnings)


# --------------------------------------------------------------------------
# Cluster-robust inference (repeated units per subject)
# --------------------------------------------------------------------------
def _clustered(n_subj=15, per=40, seed=5):
    """Subjects with heterogeneous accuracy -> genuine between-subject variance."""
    import random
    rng = random.Random(seed)
    cats = ["a", "b", "c"]
    A, B, S = [], [], []
    for s in range(n_subj):
        acc = 0.5 + 0.45 * rng.random()
        for _ in range(per):
            x = rng.choice(cats)
            y = x if rng.random() < acc else rng.choice(cats)
            A.append(x)
            B.append(y)
            S.append(f"S{s:02d}")
    return A, B, S


def test_cluster_bootstrap_ci_is_wider_than_naive():
    """The whole point: ignoring clustering understates the SE."""
    A, B, S = _clustered()
    res = analyze_categorical(A, B, subjects=S, bootstrap=400)
    cl = res.cluster
    assert cl.available
    assert cl.se > res.kappa.se
    assert cl.ci_upper - cl.ci_lower > res.kappa.ci_upper - res.kappa.ci_lower
    assert cl.design_effect > 1.0
    assert cl.n_effective < cl.n_pairs


def test_cluster_bootstrap_point_estimate_matches_plain_kappa():
    A, B, S = _clustered()
    res = analyze_categorical(A, B, subjects=S, bootstrap=200)
    assert res.cluster.value == pytest.approx(res.kappa.value)


def test_cluster_bootstrap_ci_brackets_point_estimate():
    A, B, S = _clustered()
    res = analyze_categorical(A, B, subjects=S, bootstrap=400)
    assert res.cluster.ci_lower <= res.cluster.value <= res.cluster.ci_upper


def test_cluster_bootstrap_is_deterministic_for_a_given_seed():
    """A published CI must be reproducible."""
    A, B, S = _clustered()
    r1 = analyze_categorical(A, B, subjects=S, bootstrap=200, seed=7)
    r2 = analyze_categorical(A, B, subjects=S, bootstrap=200, seed=7)
    r3 = analyze_categorical(A, B, subjects=S, bootstrap=200, seed=8)
    assert r1.cluster.ci_lower == r2.cluster.ci_lower
    assert r1.cluster.ci_upper == r2.cluster.ci_upper
    assert r1.cluster.ci_lower != r3.cluster.ci_lower  # different seed -> differs


def test_cluster_bootstrap_skipped_when_one_row_per_subject():
    a, b = _expand([[20, 5], [10, 15]])
    subs = [f"S{i}" for i in range(len(a))]
    res = analyze_categorical(a, b, subjects=subs)
    assert res.cluster.available is False
    assert "1행씩" in res.cluster.note
    # rows already independent -> no scary warning, and the naive CI stands
    assert res.decision_ci == (res.kappa.ci_lower, res.kappa.ci_upper)


def test_cluster_bootstrap_reports_per_subject_distribution():
    A, B, S = _clustered()
    cl = analyze_categorical(A, B, subjects=S, bootstrap=200).cluster
    assert cl.n_subject_estimates > 0
    assert cl.subject_min <= cl.subject_median <= cl.subject_max
    assert cl.subject_q1 <= cl.subject_median <= cl.subject_q3


def test_decision_ci_uses_cluster_ci_when_clustered():
    A, B, S = _clustered()
    res = analyze_categorical(A, B, subjects=S, bootstrap=300)
    assert res.decision_ci == (res.cluster.ci_lower, res.cluster.ci_upper)


def test_min_kappa_judged_on_cluster_ci_not_naive_ci():
    """REGRESSION: the naive CI can clear a bar the honest CI does not.
    Judging on the naive one would manufacture a false 'meets criterion'."""
    A, B, S = _clustered()
    res = analyze_categorical(A, B, subjects=S, bootstrap=600, min_kappa=0.6)
    naive_lo, cluster_lo = res.kappa.ci_lower, res.cluster.ci_lower
    assert cluster_lo < naive_lo                      # cluster CI is wider
    bar = (naive_lo + cluster_lo) / 2                 # between the two bounds
    r2 = analyze_categorical(A, B, subjects=S, bootstrap=600, min_kappa=bar)
    assert naive_lo >= bar > cluster_lo               # naive would pass...
    assert r2.meets_threshold is False                # ...but we correctly fail
    assert any("naive CI 하한" in w for w in r2.warnings)


def test_cluster_bootstrap_rejects_mismatched_subject_length():
    a, b = _expand([[10, 2], [2, 10]])
    with pytest.raises(ValueError, match="same length"):
        analyze_categorical(a, b, subjects=["S1"] * 3)


def test_cluster_bootstrap_warns_about_few_replicated_subjects():
    A, B, S = _clustered(n_subj=4, per=20)
    res = analyze_categorical(A, B, subjects=S, bootstrap=200)
    assert any("불안정" in w for w in res.warnings)


def test_cluster_bootstrap_rejects_too_few_replicates():
    A, B, S = _clustered()
    with pytest.raises(ValueError, match="replicates"):
        C.cluster_bootstrap(A, B, S, ["a", "b", "c"], replicates=1)


def test_subject_kappas_matches_manual_per_subject_kappa():
    A, B, S = _clustered(n_subj=3, per=30)
    vals = C.subject_kappas(A, B, S, ["a", "b", "c"])
    manual = []
    for s in sorted(set(S)):
        idx = [i for i, v in enumerate(S) if v == s]
        cm = C.confusion_matrix([A[i] for i in idx], [B[i] for i in idx],
                                categories=["a", "b", "c"])
        manual.append(C.kappa(cm).value)
    assert sorted(vals) == pytest.approx(sorted(manual))


def test_quantile_matches_known_values():
    v = [1.0, 2.0, 3.0, 4.0]
    assert C._quantile(v, 0.5) == pytest.approx(2.5)
    assert C._quantile(v, 0.0) == pytest.approx(1.0)
    assert C._quantile(v, 1.0) == pytest.approx(4.0)
    assert C._quantile(v, 0.25) == pytest.approx(1.75)


# --------------------------------------------------------------------------
# Hand-computed SE / CI / statistic references.
#
# These pin the variance and test-statistic algebra WITHOUT numpy/scipy/sklearn/
# statsmodels. The cross-check module is skipped entirely on a pure-stdlib
# install (which is the advertised install), so without these the SE/CI code
# would be untested exactly where the tool is meant to run. Every constant below
# was derived by evaluating the published formula by hand — see each docstring.
# --------------------------------------------------------------------------
def test_kappa_se_matches_hand_computed_fleiss_variance():
    """Fleiss, Cohen & Everitt (1969) variance for [[20,5],[10,15]], n=50:
    kappa=0.4, pe=0.5. Var = (1/(n(1-pe)^2)) * (SUM p_ij[w_ij-(w_i.+w_.j)(1-k)]^2
    - (k-pe(1-k))^2) = 0.016128 -> SE = 0.12699606."""
    k = C.kappa(_cm([[20, 5], [10, 15]]))
    assert k.se == pytest.approx(0.1269960629, abs=1e-9)
    assert k.se ** 2 == pytest.approx(0.016128, abs=1e-12)


def test_kappa_ci_matches_hand_computed_bounds():
    """kappa=0.4, SE=0.12699606, z=1.95996398 -> 0.4 +/- 0.24891."""
    k = C.kappa(_cm([[20, 5], [10, 15]]))
    assert k.ci_lower == pytest.approx(0.4 - 1.959963984540054 * 0.1269960629,
                                       abs=1e-9)
    assert k.ci_upper == pytest.approx(0.4 + 1.959963984540054 * 0.1269960629,
                                       abs=1e-9)


def test_kappa_h0_variance_and_z_match_hand_computation():
    """H0 variance = (pe + pe^2 - SUM p_i. p_.i (p_i. + p_.i)) / (n(1-pe)^2)
    = 0.0192 -> SE0 = 0.13856406, z = 0.4/0.13856406 = 2.88675135."""
    k = C.kappa(_cm([[20, 5], [10, 15]]))
    assert k.z == pytest.approx(2.88675135, abs=1e-7)
    se0 = k.value / k.z
    assert se0 == pytest.approx(0.1385640646, abs=1e-9)


def test_kappa_se_differs_from_h0_se_by_the_right_amount():
    """Guards against using one variance for both purposes: 0.12700 vs 0.13856."""
    k = C.kappa(_cm([[20, 5], [10, 15]]))
    assert k.se == pytest.approx(0.1269960629, abs=1e-9)
    assert k.value / k.z == pytest.approx(0.1385640646, abs=1e-9)


def test_weighted_kappa_se_reduces_to_unweighted_when_weights_are_identity():
    """The weighted variance formula must collapse onto the unweighted one."""
    cm = _cm([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    ident = C.kappa(cm, "unweighted")
    w, _ = C.weight_matrix(cm.categories, "unweighted")
    po_w, pe_w, pr, pc = C._weighted_agreements(cm, w)
    kw = (po_w - pe_w) / (1.0 - pe_w)
    var = C._kappa_variance(cm, w, kw, po_w, pe_w, pr, pc)
    assert math.sqrt(var) == pytest.approx(ident.se, abs=1e-12)


def test_krippendorff_alpha_matches_hand_computed_value():
    """Coincidence matrix of [[20,5],[10,15]]: o=[[40,15],[15,30]], n=100,
    Do=30/100, De=(55*45+45*55)/(100*99) -> alpha = 0.4 exactly.
    Scott's pi for the same table is 0.3939 — the finite-sample correction is
    the whole difference, so this pins n(n-1) vs n^2."""
    cm = _cm([[20, 5], [10, 15]])
    assert C.krippendorff_alpha(cm, "nominal") == pytest.approx(0.4, abs=1e-12)
    assert C.scott_pi(cm) == pytest.approx(0.3939393939, abs=1e-9)
    assert (C.krippendorff_alpha(cm, "nominal") - C.scott_pi(cm)
            == pytest.approx(0.0060606061, abs=1e-9))


def test_pabak_for_three_categories_is_not_two_po_minus_one():
    """PABAK = (K*po-1)/(K-1). For K=3, po=30/34: 0.8235, NOT 2*po-1=0.7647.
    A 2x2 table cannot distinguish the two formulae."""
    cm = _cm([[10, 1, 0], [1, 10, 1], [0, 1, 10]])
    d = C.paradox_diagnostics(cm, C.kappa(cm).value)
    assert cm.po == pytest.approx(30 / 34)
    assert d.pabak == pytest.approx((3 * (30 / 34) - 1) / 2, abs=1e-12)
    assert d.pabak == pytest.approx(0.8235294118, abs=1e-9)
    assert d.pabak != pytest.approx(2 * cm.po - 1, abs=1e-6)


def test_wilson_ci_matches_hand_computed_interval():
    """Wilson score interval, 80 successes of (90+85)/2=87.5 effective trials,
    z=1.95996398: centre/half-width give [0.83687, 0.95686]."""
    lo, hi = C._wilson_ci(80, 87.5, 0.05)
    assert lo == pytest.approx(0.8368676076, abs=1e-9)
    assert hi == pytest.approx(0.9568573990, abs=1e-9)


def test_wilson_ci_is_not_the_naive_wald_interval():
    """Guards the z^2 centre-shift and the z^2/(4n^2) term: Wald would give
    a symmetric interval around p."""
    lo, hi = C._wilson_ci(80, 87.5, 0.05)
    p = 80 / 87.5
    assert abs((p - lo) - (hi - p)) > 1e-3      # Wilson is asymmetric
    assert (lo + hi) / 2 != pytest.approx(p, abs=1e-4)


def test_wilson_ci_stays_inside_zero_one_at_the_boundary():
    lo, hi = C._wilson_ci(10, 10, 0.05)
    assert 0.0 <= lo < 1.0 and hi == pytest.approx(1.0)


def test_mcnemar_chi_square_applies_continuity_correction():
    """b=900, c=700: corrected chi2 = (|900-700|-1)^2/1600 = 24.750625.
    Without the correction it would be 25.0."""
    cm = _cm([[10, 900], [700, 10]])
    m = C.mcnemar(cm, exact_max=100)
    assert m.statistic == pytest.approx((abs(900 - 700) - 1) ** 2 / 1600)
    assert m.statistic == pytest.approx(24.750625, abs=1e-9)
    assert m.statistic != pytest.approx(25.0, abs=1e-3)


def test_stuart_maxwell_statistic_matches_hand_computed_quadratic_form():
    """3x3 with d=(row-col)=(30,-10) after dropping the last category and
    V=[[r0+c0-2n00, -(n01+n10)], [-(n01+n10), r1+c1-2n11]]; chi2 = d' V^-1 d."""
    counts = [[30, 30, 0], [0, 10, 20], [0, 0, 10]]
    cm = _cm(counts)
    m = C.stuart_maxwell(cm)
    row = [60, 30, 10]
    col = [30, 40, 30]
    d = [row[0] - col[0], row[1] - col[1]]
    V = [[row[0] + col[0] - 2 * counts[0][0],
          -(counts[0][1] + counts[1][0])],
         [-(counts[0][1] + counts[1][0]),
          row[1] + col[1] - 2 * counts[1][1]]]
    det = V[0][0] * V[1][1] - V[0][1] * V[1][0]
    inv = [[V[1][1] / det, -V[0][1] / det], [-V[1][0] / det, V[0][0] / det]]
    expected = sum(d[i] * inv[i][j] * d[j] for i in range(2) for j in range(2))
    assert m.statistic == pytest.approx(expected, abs=1e-9)
    assert m.df == 2


def test_gwet_ac1_se_matches_hand_computed_linearization():
    """Gwet (2008) linearization for [[20,5],[10,15]], n=50, computed here
    directly from the influence function g_i = (pa_i - pe)/(1-pe)
    - 2(1-AC1)(pe_i - pe)/(1-pe), Var = SUM (g_i - gbar)^2 / (n(n-1))."""
    counts = [[20, 5], [10, 15]]
    cm = _cm(counts)
    ac = C.gwet_ac(cm)
    n, k = 50, 2
    pr = [25 / 50, 25 / 50]
    pc = [30 / 50, 20 / 50]
    pi = [(pr[i] + pc[i]) / 2 for i in range(2)]
    tw = 2.0                                   # identity weights, K=2
    pe = (tw / (k * (k - 1))) * sum(v * (1 - v) for v in pi)
    po = 0.7
    gamma = (po - pe) / (1 - pe)
    assert ac.value == pytest.approx(gamma, abs=1e-12)
    infl = []
    for i in range(2):
        for j in range(2):
            pa_i = 1.0 if i == j else 0.0
            pi_i = [((1.0 if i == m else 0.0) + (1.0 if j == m else 0.0)) / 2
                    for m in range(2)]
            pe_i = (tw / (k * (k - 1))) * sum(pi_i[m] * (1 - pi[m])
                                              for m in range(2))
            g = ((pa_i - pe) / (1 - pe)
                 - 2 * (1 - gamma) * (pe_i - pe) / (1 - pe))
            infl.extend([g] * counts[i][j])
    gbar = sum(infl) / n
    var = sum((g - gbar) ** 2 for g in infl) / (n * (n - 1))
    assert ac.se == pytest.approx(math.sqrt(var), abs=1e-12)


def test_gwet_ac1_influence_function_includes_the_chance_term():
    """Dropping the pe_i term from the influence function must change the SE —
    the bootstrap cross-check is too loose to notice, so pin it here."""
    ac = C.gwet_ac(_cm([[20, 5], [10, 15]]))
    # SE computed with the pe_i term omitted (the classic mistake):
    naive = 0.0
    n = 50
    counts = [[20, 5], [10, 15]]
    pi = [0.55, 0.45]
    pe = (2.0 / 2) * sum(v * (1 - v) for v in pi)
    gamma = (0.7 - pe) / (1 - pe)
    infl = []
    for i in range(2):
        for j in range(2):
            pa_i = 1.0 if i == j else 0.0
            infl.extend([(pa_i - pe) / (1 - pe)] * counts[i][j])
    gbar = sum(infl) / n
    naive = math.sqrt(sum((g - gbar) ** 2 for g in infl) / (n * (n - 1)))
    assert abs(ac.se - naive) > 1e-4          # the terms genuinely differ
    assert ac.se != pytest.approx(naive, abs=1e-5)


def test_kappa_ci_upper_is_clamped_at_one():
    """[[97,1],[0,2]]: kappa=0.795, SE=0.1996 -> raw upper bound 1.186.
    A kappa above 1 is impossible and must not be printed."""
    k = C.kappa(_cm([[97, 1], [0, 2]]))
    assert k.value == pytest.approx(0.795082, abs=1e-5)
    assert k.se == pytest.approx(0.199564, abs=1e-5)
    assert k.value + 1.959963984540054 * k.se > 1.0    # would overshoot
    assert k.ci_upper == 1.0                            # ...so it is clamped


def test_kappa_ci_lower_is_clamped_at_minus_one():
    k = C.kappa(_cm([[0, 25], [25, 0]]))
    assert k.ci_lower >= -1.0


# --------------------------------------------------------------------------
# Warning gating (a warning that always fires is as bad as none)
# --------------------------------------------------------------------------
def test_ci_lower_grade_warning_is_absent_when_grades_agree():
    """NEGATIVE case: with a large, unambiguous sample the point grade and the
    CI-lower grade coincide, so the warning must NOT fire."""
    a, b = _expand([[500, 5], [5, 500]])
    res = analyze_categorical(a, b)
    assert _g(res.kappa.value) == _g(res.kappa.ci_lower)
    assert not any("신뢰구간 하한" in w and "점추정 등급" in w
                   for w in res.warnings)


def _g(x):
    return C.interpret_kappa(x).split(" / ")[0]


def test_paradox_warning_absent_on_balanced_table():
    a, b = _expand([[45, 5], [5, 45]])
    res = analyze_categorical(a, b)
    assert not any("역설" in w for w in res.warnings)


def test_marginal_warning_absent_when_margins_match():
    a, b = _expand([[40, 5], [5, 40]])
    res = analyze_categorical(a, b)
    assert not any("주변분포가 다릅니다" in w for w in res.warnings)


def test_sparse_cell_warning_fires():
    """A category with expected count < 1 makes weighted kappa unstable."""
    counts = [[200, 0, 0], [0, 200, 0], [0, 0, 1]]
    a, b = _expand(counts)
    res = analyze_categorical(a, b)
    assert any("기대빈도" in w for w in res.warnings)


def test_min_kappa_verdict_held_when_ci_undefined():
    """pe=1 -> kappa NaN -> no CI -> the verdict must be withheld, not guessed."""
    res = analyze_categorical(["a"] * 30, ["a"] * 30, categories=["a", "b"],
                              min_kappa=0.6)
    assert res.meets_threshold is None
    assert any("판정을 보류합니다" in w for w in res.warnings)


def test_weights_flag_without_ordinal_warns_via_library_api():
    a, b = _expand([[30, 10, 0], [10, 30, 10], [0, 10, 30]])
    res = analyze_categorical(a, b, weights="linear", ordinal=False)
    assert any("--weights" in w for w in res.warnings)
