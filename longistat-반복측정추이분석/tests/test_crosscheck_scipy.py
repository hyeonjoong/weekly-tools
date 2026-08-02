"""Cross-checks against SciPy / statsmodels.

These pin longistat's numbers to independent, widely trusted implementations.
They are *skipped* when the libraries are absent — the tool itself never needs
them, and the rest of the suite verifies the same code against hand-computed
values, so a bare Python install still gets a meaningful test run.
"""

from __future__ import annotations

import math
import random

import pytest

scipy_stats = pytest.importorskip("scipy.stats")

from longistat.anova import rm_anova                                # noqa: E402
from longistat.basics import (mann_whitney, paired_t, welch_t,      # noqa: E402
                              wilcoxon_signed_rank)
from longistat.nonparam import friedman                             # noqa: E402
from longistat.normality import shapiro_wilk                        # noqa: E402
from longistat.responder import chi2_2x2, fisher_exact_2x2          # noqa: E402
from longistat.special import chi2_sf, f_sf, t_sf_two_sided         # noqa: E402


def _rng():
    return random.Random(20260731)


def test_distribution_tails_match_scipy():
    for df in (1, 2, 5, 30, 500):
        for t in (0.1, 1.0, 2.5, 8.0, 40.0):
            assert t_sf_two_sided(t, df) == pytest.approx(
                2 * scipy_stats.t.sf(t, df), rel=1e-10)
    for d1, d2 in ((1, 5), (2, 30), (3, 7), (10, 100)):
        for f in (0.5, 1.0, 4.0, 25.0):
            assert f_sf(f, d1, d2) == pytest.approx(
                scipy_stats.f.sf(f, d1, d2), rel=1e-10)
    for df in (1, 2, 5, 20):
        for x in (0.5, 3.0, 12.0, 60.0):
            assert chi2_sf(x, df) == pytest.approx(
                scipy_stats.chi2.sf(x, df), rel=1e-9)


def test_shapiro_wilk_matches_scipy():
    rng = _rng()
    for n in (5, 12, 30, 60):
        xs = [rng.gauss(0, 1) for _ in range(n)]
        w, p = shapiro_wilk(xs)
        ref = scipy_stats.shapiro(xs)
        assert w == pytest.approx(ref.statistic, abs=1e-6)
        assert p == pytest.approx(ref.pvalue, abs=1e-5)


def test_paired_and_welch_t_match_scipy():
    rng = _rng()
    for _ in range(20):
        n = rng.randint(4, 25)
        a = [rng.gauss(10, 3) for _ in range(n)]
        b = [rng.gauss(11, 4) for _ in range(n)]
        res = paired_t([y - x for x, y in zip(a, b)])
        ref = scipy_stats.ttest_rel(b, a)
        assert res.t == pytest.approx(ref.statistic, rel=1e-10)
        assert res.p == pytest.approx(ref.pvalue, rel=1e-10)
        w = welch_t(a, b)
        refw = scipy_stats.ttest_ind(a, b, equal_var=False)
        assert w.t == pytest.approx(refw.statistic, rel=1e-10)
        assert w.p == pytest.approx(refw.pvalue, rel=1e-10)
        assert w.df == pytest.approx(refw.df, rel=1e-10)


def test_wilcoxon_and_mann_whitney_match_scipy():
    rng = _rng()
    for _ in range(40):
        n = rng.randint(5, 18)
        diffs = rng.sample(range(-400, 400), n)
        diffs = [d for d in diffs if d != 0]
        if len(set(abs(d) for d in diffs)) != len(diffs):
            continue
        mine = wilcoxon_signed_rank(diffs)
        ref = scipy_stats.wilcoxon(diffs, method="exact")
        assert mine["w"] == pytest.approx(ref.statistic)
        assert mine["p"] == pytest.approx(ref.pvalue, rel=1e-12)
    for _ in range(40):
        n1, n2 = rng.randint(3, 12), rng.randint(3, 12)
        vals = rng.sample(range(1, 500), n1 + n2)
        a, b = vals[:n1], vals[n1:]
        mine = mann_whitney(a, b)
        ref = scipy_stats.mannwhitneyu(a, b, alternative="two-sided",
                                       method="exact")
        assert mine["u1"] == pytest.approx(ref.statistic)
        assert mine["p"] == pytest.approx(ref.pvalue, rel=1e-12)


def test_tied_rank_tests_match_the_scipy_asymptotic_versions():
    a = [1, 2, 2, 3, 4, 4, 5, 5, 6]
    b = [2, 3, 3, 4, 5, 6, 6, 7, 8, 8]
    mine = mann_whitney(a, b)
    ref = scipy_stats.mannwhitneyu(a, b, alternative="two-sided",
                                   method="asymptotic", use_continuity=True)
    assert mine["p"] == pytest.approx(ref.pvalue, rel=1e-12)
    diffs = [1, 1, 2, 2, 3, -1, -1, 4, 5, 5, 6, -2, 3, 3, 2, 2, 1, 7, -3, 4]
    mine_w = wilcoxon_signed_rank(diffs)
    ref_w = scipy_stats.wilcoxon(diffs, method="approx", correction=True)
    assert mine_w["p"] == pytest.approx(ref_w.pvalue, rel=1e-12)


def test_friedman_matches_scipy_including_ties():
    rng = _rng()
    for _ in range(30):
        n, k = rng.randint(4, 14), rng.randint(3, 5)
        m = [[rng.randint(1, 9) for _ in range(k)] for _ in range(n)]
        mine = friedman(m)
        ref = scipy_stats.friedmanchisquare(*[[r[j] for r in m] for j in range(k)])
        assert mine.chi2 == pytest.approx(ref.statistic, rel=1e-10)
        assert mine.p == pytest.approx(ref.pvalue, rel=1e-10)


def test_fisher_and_chi2_match_scipy():
    rng = _rng()
    for _ in range(150):
        a, b, c, d = (rng.randint(0, 15) for _ in range(4))
        if min(a + b, c + d, a + c, b + d) == 0:
            continue
        assert fisher_exact_2x2(a, b, c, d) == pytest.approx(
            scipy_stats.fisher_exact([[a, b], [c, d]])[1], rel=1e-12)
        chi2, p = chi2_2x2(a, b, c, d)
        ref = scipy_stats.chi2_contingency([[a, b], [c, d]], correction=False)
        assert chi2 == pytest.approx(ref.statistic, rel=1e-10)
        assert p == pytest.approx(ref.pvalue, rel=1e-10)


def test_one_way_rm_anova_matches_statsmodels():
    sm = pytest.importorskip("statsmodels.stats.anova")
    pd = pytest.importorskip("pandas")
    rng = _rng()
    matrix = [[rng.gauss(10 + 2 * j, 3) for j in range(4)] for _ in range(14)]
    res = rm_anova(matrix, ["t1", "t2", "t3", "t4"])
    rows = [{"s": i, "t": j, "y": v}
            for i, row in enumerate(matrix) for j, v in enumerate(row)]
    ref = sm.AnovaRM(pd.DataFrame(rows), "y", "s", within=["t"]).fit()
    eff = res.effect("시점(시간)")
    assert eff.f == pytest.approx(float(ref.anova_table["F Value"].iloc[0]),
                                  rel=1e-10)
    assert eff.p == pytest.approx(float(ref.anova_table["Pr > F"].iloc[0]),
                                  rel=1e-9)


def test_unbalanced_mixed_time_effect_is_statsmodels_type_iii():
    smf = pytest.importorskip("statsmodels.formula.api")
    sma = pytest.importorskip("statsmodels.stats.anova")
    pd = pytest.importorskip("pandas")
    from longistat.anova import contrast_scores
    rng = _rng()
    matrix, groups = [], []
    for gi, n in enumerate((9, 5)):
        for _ in range(n):
            b = rng.gauss(0, 2)
            matrix.append([b + 10 + 2 * j + gi * 1.5 * j + rng.gauss(0, 2)
                           for j in range(3)])
            groups.append(f"G{gi}")
    res = rm_anova(matrix, ["t0", "t1", "t2"], groups)
    _, ys = contrast_scores(matrix)
    ss3 = err = 0.0
    for c in range(len(ys[0])):
        df = pd.DataFrame({"y": [r[c] for r in ys], "g": groups})
        table = sma.anova_lm(smf.ols("y ~ C(g, Sum)", data=df).fit(), typ=3)
        ss3 += float(table.loc["Intercept", "sum_sq"])
        err += float(table.loc["Residual", "sum_sq"])
    assert res.effect("시점(시간)").ss == pytest.approx(ss3, rel=1e-9)
    assert res.ss_error_within == pytest.approx(err, rel=1e-9)


def test_balanced_mixed_anova_matches_the_classical_formulas():
    rng = _rng()
    matrix, groups = [], []
    for gi in range(2):
        for _ in range(8):
            b = rng.gauss(0, 2)
            matrix.append([b + 10 + 2 * j + gi * j + rng.gauss(0, 2)
                           for j in range(3)])
            groups.append(f"G{gi}")
    res = rm_anova(matrix, ["t0", "t1", "t2"], groups)

    n, k = len(matrix), 3
    grand = sum(v for r in matrix for v in r) / (n * k)
    col_means = [sum(r[j] for r in matrix) / n for j in range(k)]
    ss_time = n * sum((m - grand) ** 2 for m in col_means)
    ss_gt = 0.0
    for gl in ("G0", "G1"):
        idx = [i for i, g in enumerate(groups) if g == gl]
        gm = sum(matrix[i][j] for i in idx for j in range(k)) / (len(idx) * k)
        for j in range(k):
            cell = sum(matrix[i][j] for i in idx) / len(idx)
            ss_gt += len(idx) * (cell - gm - col_means[j] + grand) ** 2
    assert res.effect("시점(시간)").ss == pytest.approx(ss_time, rel=1e-9)
    assert res.effect("그룹 × 시점").ss == pytest.approx(ss_gt, rel=1e-9)


# --------------------------------------------------------------------------
# MMRM: the EM fit must land on the same REML optimum a general-purpose
# optimiser finds.  This is the strongest independent check available without
# an R installation — the objective below is written from the textbook formula
# (Verbeke & Molenberghs eq. 5.8) and shares no code with longistat.
# --------------------------------------------------------------------------

def test_mmrm_em_reaches_the_same_reml_optimum_as_a_numerical_optimiser():
    np = pytest.importorskip("numpy")
    optimize = pytest.importorskip("scipy.optimize")

    from longistat.dataio import Panel                                # noqa: E402
    from longistat.mmrm import _build, _fit_reml, mmrm_analysis       # noqa: E402

    rng = random.Random(11)
    n, n_times = 45, 4
    groups = ["A" if i % 2 else "B" for i in range(n)]
    rows = []
    for i in range(n):
        base = rng.gauss(20, 4)
        arm = -3.0 if groups[i] == "A" else 0.0
        row = [base] + [base + arm * (j + 1) / 3 + rng.gauss(-(j + 1), 2.5)
                        for j in range(n_times - 1)]
        for j in range(1, n_times):                    # monotone dropout
            if rng.random() < 0.18:
                for k in range(j, n_times):
                    row[k] = None
                break
        rows.append(row)
    panel = Panel(subjects=[f"s{i}" for i in range(n)],
                  times=[f"V{j}" for j in range(n_times)],
                  values=rows, groups=groups, group_name="군")

    fitted = mmrm_analysis(panel, 0)
    assert fitted is not None and fitted.converged
    subs, visits, _lab, _cc, _bc, n_cols, _drop, _cov = _build(panel, 0, True)
    t_model = len(visits)

    xs, ys, obs = [], [], []
    for s in subs:
        x = np.zeros((len(s.obs), n_cols))
        for r, entries in enumerate(s.rows):
            for col, val in entries:
                x[r, col] = val
        xs.append(x)
        ys.append(np.asarray(s.y))
        obs.append(list(s.obs))

    tril = np.tril_indices(t_model)

    def minus2_reml(theta):
        low = np.zeros((t_model, t_model))
        low[tril] = theta
        np.fill_diagonal(low, np.exp(np.diag(low)))    # keeps Σ positive definite
        sigma = low @ low.T
        cache, xtx, xty, logdet_v, n_obs = {}, 0.0, 0.0, 0.0, 0
        for x, y, o in zip(xs, ys, obs):
            key = tuple(o)
            if key not in cache:
                block = sigma[np.ix_(o, o)]
                cache[key] = (np.linalg.inv(block),
                              np.linalg.slogdet(block)[1])
            vinv, ld = cache[key]
            logdet_v += ld
            n_obs += len(o)
            xtx = xtx + x.T @ vinv @ x
            xty = xty + x.T @ vinv @ y
        beta = np.linalg.solve(xtx, xty)
        quad = 0.0
        for x, y, o in zip(xs, ys, obs):
            resid = y - x @ beta
            quad += resid @ cache[tuple(o)][0] @ resid
        return (logdet_v + quad + np.linalg.slogdet(xtx)[1]
                + (n_obs - n_cols) * math.log(2 * math.pi))

    start = np.zeros(t_model * (t_model + 1) // 2)
    start[np.cumsum(np.arange(1, t_model + 1)) - 1] = math.log(3.0)
    best = min((optimize.minimize(
        minus2_reml, start + np.linspace(-0.05, 0.05, start.size) * seed,
        method="Nelder-Mead",
        options=dict(maxiter=200000, maxfev=200000, xatol=1e-10, fatol=1e-10))
        for seed in (0, 1, 2)), key=lambda r: r.fun)

    assert best.fun == pytest.approx(-2.0 * fitted.loglik, abs=1e-6)
    low = np.zeros((t_model, t_model))
    low[tril] = best.x
    np.fill_diagonal(low, np.exp(np.diag(low)))
    sigma_ref = low @ low.T
    assert np.abs(sigma_ref - np.asarray(fitted.cov)).max() < 1e-4

    beta_em = _fit_reml(subs, n_cols, t_model, 400, 1e-9)[0]
    xtx, xty, cache = 0.0, 0.0, {}
    for x, y, o in zip(xs, ys, obs):
        key = tuple(o)
        if key not in cache:
            cache[key] = np.linalg.inv(sigma_ref[np.ix_(o, o)])
        xtx = xtx + x.T @ cache[key] @ x
        xty = xty + x.T @ cache[key] @ y
    assert np.abs(np.linalg.solve(xtx, xty) - np.asarray(beta_em)).max() < 1e-5
