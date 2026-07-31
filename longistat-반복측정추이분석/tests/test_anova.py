"""Hand-computed checks of the repeated-measures / mixed ANOVA engine."""

from __future__ import annotations

import math

import pytest

from longistat.anova import Sphericity, contrast_scores, helmert, rm_anova

# Textbook 4 subjects x 3 conditions.  Every quantity below is worked out by
# hand in the docstring of test_one_way_hand_computed.
HAND = [
    [1.0, 2.0, 3.0],
    [2.0, 4.0, 5.0],
    [4.0, 5.0, 9.0],
    [3.0, 6.0, 7.0],
]


def test_helmert_is_orthonormal_and_orthogonal_to_one():
    for k in range(2, 8):
        c = helmert(k)
        assert len(c) == k and len(c[0]) == k - 1
        for a in range(k - 1):
            col_a = [c[j][a] for j in range(k)]
            assert math.isclose(sum(col_a), 0.0, abs_tol=1e-12)
            assert math.isclose(sum(v * v for v in col_a), 1.0, rel_tol=1e-12)
            for b in range(a + 1, k - 1):
                dot = sum(c[j][a] * c[j][b] for j in range(k))
                assert math.isclose(dot, 0.0, abs_tol=1e-12)


def test_contrast_scores_decompose_the_total_sum_of_squares():
    """Σu² + Σy² is SS_total about the grand mean (the scores are centred)."""
    u, y = contrast_scores(HAND)
    grand = sum(v for row in HAND for v in row) / 12
    ss_total = sum((v - grand) ** 2 for row in HAND for v in row)
    rebuilt = sum(v * v for v in u) + sum(v * v for row in y for v in row)
    assert math.isclose(ss_total, rebuilt, rel_tol=1e-12)


def test_contrast_scores_survive_a_huge_constant_offset():
    """Every SS is shift-invariant; without centring, F lost 8 digits at 1e9."""
    ref = rm_anova(HAND, ["c1", "c2", "c3"]).effect("시점(시간)")
    for offset in (1e6, 1e9, 1e12, 1e15):
        shifted = [[v + offset for v in row] for row in HAND]
        got = rm_anova(shifted, ["c1", "c2", "c3"]).effect("시점(시간)")
        assert math.isclose(got.f, ref.f, rel_tol=1e-9), offset
        assert math.isclose(got.ss, ref.ss, rel_tol=1e-9), offset


def _lcg_matrix(rows: int, cols: int, seed: int = 12345):
    """Reproducible pseudo-random matrix without depending on `random`'s stream."""
    x = seed
    out = []
    for _ in range(rows):
        row = []
        for _ in range(cols):
            x = (1103515245 * x + 12345) % (2 ** 31)
            row.append(round(10.0 + 8.0 * x / 2 ** 31, 4))
        out.append(row)
    return out


def test_mauchly_survives_units_that_would_overflow_a_direct_ratio():
    """W is scale-free; ``det / (tr/d)**d`` raised OverflowError at k=26, sd~1e5.

    A 31-day cost diary in KRW reproduced it as a raw traceback; micro-scale
    units underflowed the other way and printed a false "singular covariance".
    """
    names = [f"d{j}" for j in range(26)]
    base = _lcg_matrix(60, 26)
    ref = rm_anova(base, names).sphericity
    assert ref.mauchly_ok and 0.0 < ref.w <= 1.0
    for scale in (1e-6, 1e-3, 1e5, 1e8):
        got = rm_anova([[v * scale for v in row] for row in base],
                       names).sphericity
        assert got.mauchly_ok, scale
        assert math.isclose(got.w, ref.w, rel_tol=1e-9), scale
        assert math.isclose(got.eps_gg, ref.eps_gg, rel_tol=1e-12), scale


def test_singular_contrast_covariance_keeps_the_epsilon_correction():
    """Mauchly may be uncomputable while ε̂ is not — and ε̂ is then at its floor.

    Dropping the correction in exactly that case gave the most extreme
    sphericity violation the most liberal p-value available.
    """
    # An unestimable W must still select a correction, not fall back to none.
    singular = Sphericity(epsilon_ok=True, mauchly_ok=False, eps_gg=0.5,
                          eps_hf=0.5, eps_lb=0.5)
    assert singular.recommended() == "gg"
    assert singular.violated() is False
    assert Sphericity(epsilon_ok=False, mauchly_ok=False).recommended() == "none"

    # Rank-1 profiles: ε̂ sits exactly on its 1/d floor and GG must be applied.
    base = [1.0, 2.5, 0.4, 3.1, 1.7, 2.2, 0.9, 2.8]
    matrix = [[a * m for m in (1.0, 2.0, 3.0)] for a in base]
    res = rm_anova(matrix, ["t1", "t2", "t3"])
    assert res.sphericity.epsilon_ok
    assert res.sphericity.eps_gg == pytest.approx(res.sphericity.eps_lb, rel=1e-9)
    assert res.sphericity.recommended() == "gg"
    eff = res.effect("시점(시간)")
    assert eff.p_gg is not None and eff.p_gg > eff.p


def test_girden_rule_picks_huynh_feldt_only_above_075():
    assert Sphericity(epsilon_ok=True, mauchly_ok=True, p=0.01, eps_gg=0.90,
                      eps_hf=0.95).recommended() == "hf"
    assert Sphericity(epsilon_ok=True, mauchly_ok=True, p=0.01, eps_gg=0.75,
                      eps_hf=0.80).recommended() == "hf"
    assert Sphericity(epsilon_ok=True, mauchly_ok=True, p=0.01, eps_gg=0.74,
                      eps_hf=0.80).recommended() == "gg"
    assert Sphericity(epsilon_ok=True, mauchly_ok=True, p=0.20,
                      eps_gg=0.50).recommended() == "none"


def test_huynh_feldt_p_comes_with_huynh_feldt_degrees_of_freedom():
    matrix = _lcg_matrix(20, 4, seed=777)
    eff = rm_anova(matrix, ["a", "b", "c", "d"]).effect("시점(시간)")
    assert eff.df1_hf is not None and eff.df2_hf is not None
    d1, d2 = eff.df_reported("hf")
    assert (d1, d2) == (eff.df1_hf, eff.df2_hf)
    from longistat.special import f_sf
    assert f_sf(eff.f, d1, d2) == pytest.approx(eff.p_hf, rel=1e-12)
    assert eff.df_reported("gg") == (eff.df1_gg, eff.df2_gg)
    assert eff.df_reported("none") == (eff.df1, eff.df2)


def test_zero_residual_variance_yields_no_f_ratio():
    """Perfectly additive data leaves 1e-30 of rounding noise, not a real MS."""
    matrix = [[20.0 + i, 14.0 + i, 10.0 + i] for i in range(10)]
    eff = rm_anova(matrix, ["t1", "t2", "t3"]).effect("시점(시간)")
    assert math.isnan(eff.f) and math.isnan(eff.p)


def test_one_way_hand_computed():
    """Column means 2.5 / 4.25 / 6.0, grand mean 4.25.

    SS_time = 4·[(2.5−4.25)² + 0 + (6−4.25)²]        = 24.5
    SS_subj = 3·[(2−4.25)²+(11/3−4.25)²+(6−4.25)²+(16/3−4.25)²] = 28.916667
    SS_total = 58.25  →  SS_error = 58.25 − 24.5 − 28.916667 = 4.833333
    F = (24.5/2) / (4.833333/6) = 15.2068966
    ηp² = 24.5/29.333333 = .8352273 ;  η²G = 24.5/58.25 = .4206009
    """
    res = rm_anova(HAND, ["c1", "c2", "c3"])
    eff = res.effect("시점(시간)")
    assert eff is not None
    assert math.isclose(eff.ss, 24.5, rel_tol=1e-12)
    assert math.isclose(res.ss_error_within, 4.8333333333333, rel_tol=1e-10)
    assert math.isclose(res.ss_error_between, 28.9166666666667, rel_tol=1e-10)
    assert math.isclose(res.ss_total, 58.25, rel_tol=1e-12)
    assert (eff.df1, eff.df2) == (2.0, 6.0)
    assert math.isclose(eff.f, 15.2068965517241, rel_tol=1e-10)
    assert math.isclose(eff.partial_eta2, 24.5 / 29.3333333333333, rel_tol=1e-10)
    assert math.isclose(eff.generalized_eta2, 24.5 / 58.25, rel_tol=1e-10)
    assert 0.0 < eff.p < 0.01


def test_sums_of_squares_add_up_in_balanced_designs():
    matrix = [[1.0, 3.0, 2.0], [2.0, 5.0, 4.0], [4.0, 4.0, 9.0],
              [3.0, 7.0, 6.0], [5.0, 6.0, 8.0], [2.0, 2.0, 3.0]]
    groups = ["A", "A", "A", "B", "B", "B"]
    res = rm_anova(matrix, ["t1", "t2", "t3"], groups)
    total = (res.effect("그룹(집단)").ss + res.ss_error_between
             + res.effect("시점(시간)").ss + res.effect("그룹 × 시점").ss
             + res.ss_error_within)
    assert math.isclose(total, res.ss_total, rel_tol=1e-10)


def test_type_three_time_effect_uses_unweighted_group_means():
    """With unequal n the time effect must not be dragged toward the big group."""
    matrix = [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0], [4.0, 7.0], [5.0, 8.0],
              [10.0, 10.0], [12.0, 12.0]]
    groups = ["A"] * 5 + ["B"] * 2
    res = rm_anova(matrix, ["pre", "post"], groups)
    u, y = contrast_scores(matrix)
    col = [row[0] for row in y]
    mean_a = sum(col[:5]) / 5
    mean_b = sum(col[5:]) / 2
    mu = (mean_a + mean_b) / 2
    weight = (1 / 5 + 1 / 2) / 4
    assert math.isclose(res.effect("시점(시간)").ss, mu * mu / weight,
                        rel_tol=1e-12)


def test_sphericity_is_exactly_one_for_a_spherical_sample():
    """Columns orthogonal after centring ⇒ sample covariance ∝ I ⇒ W = ε = 1."""
    matrix = [[1.0, 1.0, 1.0], [1.0, -1.0, -1.0],
              [-1.0, 1.0, -1.0], [-1.0, -1.0, 1.0]]
    res = rm_anova(matrix, ["a", "b", "c"])
    s = res.sphericity
    assert s.epsilon_ok and s.mauchly_ok
    assert math.isclose(s.w, 1.0, rel_tol=1e-12)
    assert math.isclose(s.eps_gg, 1.0, rel_tol=1e-12)
    assert math.isclose(s.eps_hf, 1.0, rel_tol=1e-12)
    assert s.p > 0.99


def test_epsilon_stays_inside_its_theoretical_bounds():
    matrix = [[1.0, 2.0, 9.0, 3.0], [2.0, 2.5, 1.0, 8.0], [4.0, 9.0, 5.0, 2.0],
              [3.0, 1.0, 7.0, 6.0], [5.0, 8.0, 2.0, 4.0], [7.0, 3.0, 6.0, 1.0]]
    res = rm_anova(matrix, ["t1", "t2", "t3", "t4"])
    s = res.sphericity
    assert s.epsilon_ok and s.mauchly_ok
    lower = 1.0 / 3.0
    assert lower - 1e-12 <= s.eps_gg <= 1.0 + 1e-12
    assert s.eps_hf >= s.eps_gg - 1e-12
    assert 0.0 < s.w <= 1.0


def test_two_timepoints_have_no_sphericity_question():
    res = rm_anova([[1.0, 2.0], [3.0, 5.0], [2.0, 2.0], [4.0, 7.0]],
                   ["pre", "post"])
    assert not res.sphericity.epsilon_ok
    assert "2개" in res.sphericity.reason
    eff = res.effect("시점(시간)")
    assert eff.p_gg is None and eff.p_reported("gg") == eff.p


def test_rm_anova_f_equals_squared_paired_t_for_two_timepoints():
    from longistat.basics import paired_t
    matrix = [[3.0, 6.0], [4.0, 5.0], [2.0, 9.0], [7.0, 8.0], [5.0, 5.0]]
    res = rm_anova(matrix, ["pre", "post"])
    t = paired_t([row[1] - row[0] for row in matrix])
    eff = res.effect("시점(시간)")
    assert math.isclose(eff.f, t.t ** 2, rel_tol=1e-10)
    assert math.isclose(eff.p, t.p, rel_tol=1e-10)


def test_group_effect_matches_a_one_way_anova_on_subject_means():
    matrix = [[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [8.0, 9.0, 10.0],
              [9.0, 10.0, 11.0], [4.0, 5.0, 6.0]]
    groups = ["A", "A", "B", "B", "B"]
    res = rm_anova(matrix, ["t1", "t2", "t3"], groups)
    means = [sum(r) / 3 for r in matrix]
    grand = sum(means) / 5
    ma = sum(means[:2]) / 2
    mb = sum(means[2:]) / 3
    ss_group = 3 * (2 * (ma - grand) ** 2 + 3 * (mb - grand) ** 2)
    assert math.isclose(res.effect("그룹(집단)").ss, ss_group, rel_tol=1e-10)


def test_rejects_degenerate_inputs():
    with pytest.raises(ValueError):
        rm_anova([], ["a", "b"])
    with pytest.raises(ValueError):
        rm_anova([[1.0]], ["a"])
    with pytest.raises(ValueError):
        rm_anova([[1.0, 2.0], [2.0, 3.0]], ["a", "b", "c"])
    with pytest.raises(ValueError):
        rm_anova([[1.0, 2.0]], ["a", "b"], ["A"])          # N - g = 0


def test_constant_data_does_not_crash():
    res = rm_anova([[5.0, 5.0, 5.0]] * 4, ["a", "b", "c"])
    eff = res.effect("시점(시간)")
    assert math.isnan(eff.f)
    assert not res.sphericity.epsilon_ok
