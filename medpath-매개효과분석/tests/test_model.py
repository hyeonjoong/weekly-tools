"""OLS inference and diagnostics."""

import math
from random import Random

import pytest

from helpers import exact_ols_with_intercept, exact_residual_ss, exact_se
from medpath.model import (breusch_pagan, fit_ols, influence_summary, sd,
                           vif_table)


def _data(n=45, seed=5):
    rng = Random(seed)
    x = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    z = [round(rng.gauss(10, 2), 3) for _ in range(n)]
    y = [round(2.0 + 1.5 * a - 0.4 * b + rng.gauss(0, 0.8), 3)
         for a, b in zip(x, z)]
    return x, z, y


def test_coefficients_and_ses_match_exact_arithmetic():
    x, z, y = _data()
    reg = fit_ols("y", y, [("x", x), ("z", z)])
    want_b = exact_ols_with_intercept([x, z], y)
    want_se = exact_se([x, z], y)
    for coef, b, se in zip(reg.coefs, want_b, want_se):
        assert coef.estimate == pytest.approx(float(b), rel=1e-11, abs=1e-11)
        assert coef.se == pytest.approx(se, rel=1e-10, abs=1e-12)


def test_r_squared_matches_exact_residual_sum_of_squares():
    x, z, y = _data()
    reg = fit_ols("y", y, [("x", x), ("z", z)])
    n = len(y)
    ybar = sum(y) / n
    tss = sum((v - ybar) ** 2 for v in y)
    rss = float(exact_residual_ss([x, z], y))
    assert reg.r2 == pytest.approx(1 - rss / tss, rel=1e-11)
    assert reg.rss == pytest.approx(rss, rel=1e-10)


def test_single_predictor_f_equals_t_squared():
    x, z, y = _data()
    reg = fit_ols("y", y, [("x", x)])
    t = reg.coef("x").t
    assert reg.f == pytest.approx(t * t, rel=1e-9)
    assert reg.f_p == pytest.approx(reg.coef("x").p, rel=1e-9)


def test_confidence_interval_brackets_estimate_and_widens_with_level():
    x, z, y = _data()
    r95 = fit_ols("y", y, [("x", x), ("z", z)], conf=0.95)
    r99 = fit_ols("y", y, [("x", x), ("z", z)], conf=0.99)
    c95, c99 = r95.coef("x"), r99.coef("x")
    assert c95.ci_lo < c95.estimate < c95.ci_hi
    assert c99.ci_lo < c95.ci_lo and c99.ci_hi > c95.ci_hi
    # interval is symmetric around the estimate
    assert (c95.ci_hi + c95.ci_lo) / 2 == pytest.approx(c95.estimate, rel=1e-12)


def test_hc3_changes_standard_errors_but_not_estimates():
    rng = Random(2)
    n = 120
    x = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    # variance grows with x -> classical SEs are wrong, HC3 should differ
    y = [round(1 + 2 * xi + rng.gauss(0, 0.5 + abs(xi)), 3) for xi in x]
    plain = fit_ols("y", y, [("x", x)])
    rob = fit_ols("y", y, [("x", x)], robust="hc3")
    assert rob.coef("x").estimate == pytest.approx(plain.coef("x").estimate, rel=1e-12)
    assert rob.coef("x").se != pytest.approx(plain.coef("x").se, rel=1e-6)
    assert rob.robust == "hc3"


def test_vif_matches_definition():
    rng = Random(8)
    n = 60
    a = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    b = [round(0.8 * v + rng.gauss(0, 0.4), 3) for v in a]
    c = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    table = dict(vif_table([("a", a), ("b", b), ("c", c)]))
    # recompute VIF for 'a' the long way
    r2 = fit_ols("a", a, [("b", b), ("c", c)]).r2
    assert table["a"] == pytest.approx(1 / (1 - r2), rel=1e-10)
    assert table["a"] > 2.0          # correlated pair inflates
    assert table["c"] == pytest.approx(1.0, abs=0.25)


def test_vif_is_infinite_for_perfect_collinearity():
    n = 20
    a = [float(i) for i in range(n)]
    b = [2.0 * v + 1.0 for v in a]
    table = dict(vif_table([("a", a), ("b", b)]))
    assert math.isinf(table["a"]) and math.isinf(table["b"])


def test_vif_empty_for_single_predictor():
    assert vif_table([("a", [1.0, 2.0, 3.0])]) == []


def test_breusch_pagan_detects_heteroscedasticity():
    """Residual spread growing with x must be caught; constant spread must not.

    The spread is made *monotone* in x on purpose — BP's auxiliary regression
    is linear in the predictors, so a symmetric pattern like sd ~ |x| is
    invisible to it. That is a real (and documented) limit of the test, not a
    bug, and the tool only ever uses BP as a nudge toward --robust hc3.
    """
    n = 300
    rng = Random(2)
    x = [round(rng.uniform(-3, 3), 3) for _ in range(n)]
    r_homo = Random(102)
    homo = fit_ols("y", [1 + 2 * xi + r_homo.gauss(0, 1) for xi in x], [("x", x)])
    r_het = Random(202)
    hetero = fit_ols("y", [1 + 2 * xi + r_het.gauss(0, 0.2 + (xi + 3) / 2) for xi in x],
                     [("x", x)])
    lm_homo, df_homo, p_homo = breusch_pagan(homo)
    lm_het, _, p_hetero = breusch_pagan(hetero)
    assert df_homo == 1
    assert p_homo > 0.05
    assert p_hetero < 0.001
    assert lm_het > lm_homo


def test_breusch_pagan_returns_none_on_perfect_fit():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0 * v for v in x]
    assert breusch_pagan(fit_ols("y", y, [("x", x)])) is None


def test_cooks_distance_flags_the_planted_outlier():
    rng = Random(6)
    n = 40
    x = [round(rng.gauss(0, 1), 3) for _ in range(n)]
    y = [round(1 + 2 * xi + rng.gauss(0, 0.3), 3) for xi in x]
    x[0], y[0] = 4.0, -30.0                 # high leverage AND wrong direction
    reg = fit_ols("y", y, [("x", x)])
    count, cutoff, top = influence_summary(reg)
    assert count >= 1
    assert top[0][0] == 0                   # the planted row is the worst
    assert top[0][1] > 1.0
    assert cutoff == pytest.approx(4.0 / n)


def test_sd_is_sample_standard_deviation():
    assert sd([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) == pytest.approx(
        math.sqrt(32.0 / 7.0), rel=1e-12)
    assert math.isnan(sd([1.0]))


statsmodels = pytest.importorskip("statsmodels.api", reason="statsmodels not installed")


def test_crosscheck_statsmodels_ols_and_hc3():
    import numpy as np

    x, z, y = _data(n=70, seed=21)
    X = statsmodels.add_constant(np.column_stack([x, z]))
    sm_fit = statsmodels.OLS(np.asarray(y), X).fit()
    ours = fit_ols("y", y, [("x", x), ("z", z)])
    for i, coef in enumerate(ours.coefs):
        assert coef.estimate == pytest.approx(float(sm_fit.params[i]), rel=1e-10)
        assert coef.se == pytest.approx(float(sm_fit.bse[i]), rel=1e-9)
        assert coef.p == pytest.approx(float(sm_fit.pvalues[i]), rel=1e-8, abs=1e-14)
    assert ours.r2 == pytest.approx(float(sm_fit.rsquared), rel=1e-10)
    assert ours.adj_r2 == pytest.approx(float(sm_fit.rsquared_adj), rel=1e-10)
    assert ours.f == pytest.approx(float(sm_fit.fvalue), rel=1e-9)
    assert ours.f_p == pytest.approx(float(sm_fit.f_pvalue), rel=1e-7, abs=1e-14)

    sm_hc3 = statsmodels.OLS(np.asarray(y), X).fit(cov_type="HC3")
    ours_hc3 = fit_ols("y", y, [("x", x), ("z", z)], robust="hc3")
    for i, coef in enumerate(ours_hc3.coefs):
        assert coef.se == pytest.approx(float(sm_hc3.bse[i]), rel=1e-9)


def test_crosscheck_statsmodels_breusch_pagan():
    import numpy as np
    from statsmodels.stats.diagnostic import het_breuschpagan

    x, z, y = _data(n=90, seed=33)
    X = statsmodels.add_constant(np.column_stack([x, z]))
    sm_fit = statsmodels.OLS(np.asarray(y), X).fit()
    lm, lm_p, _, _ = het_breuschpagan(sm_fit.resid, X, robust=True)
    ours = breusch_pagan(fit_ols("y", y, [("x", x), ("z", z)]))
    assert ours[0] == pytest.approx(float(lm), rel=1e-8)
    assert ours[2] == pytest.approx(float(lm_p), rel=1e-7, abs=1e-14)


def test_crosscheck_statsmodels_cooks_distance():
    import numpy as np

    x, z, y = _data(n=50, seed=44)
    X = statsmodels.add_constant(np.column_stack([x, z]))
    sm_fit = statsmodels.OLS(np.asarray(y), X).fit()
    sm_d = sm_fit.get_influence().cooks_distance[0]
    reg = fit_ols("y", y, [("x", x), ("z", z)])
    _, _, top = influence_summary(reg, top=5)
    for row, d in top:
        assert d == pytest.approx(float(sm_d[row]), rel=1e-8)
