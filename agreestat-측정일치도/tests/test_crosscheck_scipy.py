"""Optional cross-checks against numpy/scipy.

These run only when numpy AND scipy are importable; otherwise the whole module
is skipped so the suite still passes on a pure standard-library install.
"""

import math
import random

import pytest

np = pytest.importorskip("numpy")
stats = pytest.importorskip("scipy.stats")

from agreestat import agreement as A
from agreestat import regression as Reg
from agreestat import special as sp


def _sample(seed, n=40):
    random.seed(seed)
    x = [random.gauss(50, 12) for _ in range(n)]
    y = [xi * 0.97 + random.gauss(1.5, 4.0) for xi in x]
    return x, y


def test_pearson_matches_scipy():
    for seed in (1, 2, 3):
        x, y = _sample(seed)
        r_ref, _ = stats.pearsonr(x, y)
        assert abs(A.pearson(x, y).r - r_ref) < 1e-10


def test_pearson_ci_matches_fisher():
    x, y = _sample(11, n=50)
    pr = A.pearson(x, y)
    r = pr.r
    z = math.atanh(r)
    se = 1.0 / math.sqrt(len(x) - 3)
    zc = stats.norm.ppf(0.975)
    lo = math.tanh(z - zc * se)
    hi = math.tanh(z + zc * se)
    assert abs(pr.ci_lower - lo) < 1e-9
    assert abs(pr.ci_upper - hi) < 1e-9


def test_paired_t_matches_scipy():
    for seed in (4, 5, 6):
        x, y = _sample(seed)
        t_ref, p_ref = stats.ttest_rel(x, y)
        pt = A.paired_t(x, y)
        assert abs(pt.t - t_ref) < 1e-8
        assert abs(pt.pvalue - p_ref) < 1e-10


def test_proportional_slope_matches_linregress():
    x, y = _sample(7)
    means = [(a + b) / 2 for a, b in zip(x, y)]
    diffs = [a - b for a, b in zip(x, y)]
    slope, intercept, p = A._ols_slope_test(means, diffs)
    lr = stats.linregress(means, diffs)
    assert abs(slope - lr.slope) < 1e-10
    assert abs(intercept - lr.intercept) < 1e-9
    assert abs(p - lr.pvalue) < 1e-10


def test_f_ppf_matches_scipy():
    for (p, d1, d2) in [(0.975, 5, 15), (0.975, 15, 5), (0.95, 2, 20),
                        (0.975, 5.0, 4.785167), (0.99, 12, 8)]:
        assert abs(sp.f_ppf(p, d1, d2) - stats.f.ppf(p, d1, d2)) < 1e-6


def test_t_ppf_matches_scipy():
    for df in (1, 2.5, 5, 12.0, 30, 0.5):
        for p in (1e-4, 0.025, 0.5, 0.975, 1 - 1e-4):
            got = sp.t_ppf(p, df)
            ref = stats.t.ppf(p, df)
            assert abs(got - ref) <= 1e-6 * max(1.0, abs(ref))


def test_ccc_matches_numpy_definition():
    x, y = _sample(9)
    xa, ya = np.array(x), np.array(y)
    sx2, sy2 = xa.var(), ya.var()
    sxy = ((xa - xa.mean()) * (ya - ya.mean())).mean()
    ccc_ref = 2 * sxy / (sx2 + sy2 + (xa.mean() - ya.mean()) ** 2)
    assert abs(A.ccc(x, y).value - ccc_ref) < 1e-10


def test_icc_ci_matches_independent_mcgraw_wong():
    """Recompute the ICC(2,1)/(3,1) CIs independently with scipy.stats.f.ppf."""
    rows = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
            [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]
    icc21, icc31, ms = A.icc(rows)
    n, k = ms.n, ms.k
    msr, msc, mse = ms.msr, ms.msc, ms.mse
    df1, df2 = n - 1, (n - 1) * (k - 1)
    f = msr / mse
    a = 0.05

    # ICC(3,1) consistency
    fl = f / stats.f.ppf(1 - a / 2, df1, df2)
    fu = f * stats.f.ppf(1 - a / 2, df2, df1)
    lo3 = (fl - 1) / (fl + k - 1)
    hi3 = (fu - 1) / (fu + k - 1)
    assert abs(icc31.ci_lower - lo3) < 1e-6
    assert abs(icc31.ci_upper - hi3) < 1e-6

    # ICC(2,1) absolute agreement
    rho = icc21.value
    aa = (k * rho) / (n * (1 - rho))
    bb = 1 + (k * rho * (n - 1)) / (n * (1 - rho))
    v = (aa * msc + bb * mse) ** 2 / (
        (aa * msc) ** 2 / (k - 1) + (bb * mse) ** 2 / ((n - 1) * (k - 1)))
    fu_c = stats.f.ppf(1 - a / 2, df1, v)
    fl_c = stats.f.ppf(1 - a / 2, v, df1)
    common = k * msc + (k * n - k - n) * mse
    lo2 = n * (msr - fu_c * mse) / (fu_c * common + n * msr)
    hi2 = n * (fl_c * msr - mse) / (common + n * fl_c * msr)
    assert abs(icc21.ci_lower - lo2) < 1e-6
    assert abs(icc21.ci_upper - hi2) < 1e-6


def test_deming_lambda1_matches_scipy_odr():
    """Deming with lam=1 is orthogonal distance regression == scipy.odr."""
    odr = pytest.importorskip("scipy.odr")

    def _f(beta, xx):
        return beta[0] * xx + beta[1]

    for seed in (1, 2, 3, 21):
        x, y = _sample(seed)
        d = Reg.deming(x, y, lam=1.0)
        out = odr.ODR(odr.Data(x, y), odr.Model(_f), beta0=[1.0, 0.0]).run()
        slope_ref, intercept_ref = out.beta
        assert abs(d.slope - slope_ref) < 1e-4
        assert abs(d.intercept - intercept_ref) < 1e-3


def test_deming_negative_slope_matches_odr():
    odr = pytest.importorskip("scipy.odr")

    def _f(beta, xx):
        return beta[0] * xx + beta[1]

    random.seed(31)
    x = [random.gauss(20, 5) for _ in range(40)]
    y = [-0.8 * xi + 100 + random.gauss(0, 3) for xi in x]
    d = Reg.deming(x, y, lam=1.0)
    out = odr.ODR(odr.Data(x, y), odr.Model(_f), beta0=[-1.0, 0.0]).run()
    assert abs(d.slope - out.beta[0]) < 1e-4


# ==========================================================================
# Categorical agreement — cross-checks against sklearn / statsmodels
# ==========================================================================
from agreestat import categorical as Cat  # noqa: E402


def _rater_sample(seed, n=200, cats=("W", "N1", "N2", "N3", "REM"), p_agree=0.7):
    rng = random.Random(seed)
    a = [rng.choice(cats) for _ in range(n)]
    b = [x if rng.random() < p_agree else rng.choice(cats) for x in a]
    return a, b


@pytest.mark.parametrize("seed", [1, 2, 3, 17])
@pytest.mark.parametrize("scheme,sk_weights", [
    ("unweighted", None), ("linear", "linear"), ("quadratic", "quadratic"),
])
def test_kappa_matches_sklearn(seed, scheme, sk_weights):
    sk = pytest.importorskip("sklearn.metrics")
    a, b = _rater_sample(seed)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.kappa(cm, scheme).value
    ref = sk.cohen_kappa_score(a, b, labels=cm.categories, weights=sk_weights)
    assert abs(ours - ref) < 1e-12


@pytest.mark.parametrize("seed", [1, 5, 9])
def test_kappa_se_ci_and_ztest_match_statsmodels(seed):
    ir = pytest.importorskip("statsmodels.stats.inter_rater")
    a, b = _rater_sample(seed)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.kappa(cm)
    ref = ir.cohens_kappa(np.array(cm.counts))
    assert abs(ours.value - ref.kappa) < 1e-12
    assert abs(ours.se - ref.std_kappa) < 1e-12
    assert abs(ours.ci_lower - ref.kappa_low) < 1e-10
    assert abs(ours.ci_upper - ref.kappa_upp) < 1e-10
    assert abs(ours.z - ref.z_value) < 1e-9        # uses the H0 variance
    assert abs(ours.pvalue - ref.pvalue_two_sided) < 1e-12


@pytest.mark.parametrize("seed", [2, 6])
def test_weighted_kappa_se_matches_statsmodels(seed):
    ir = pytest.importorskip("statsmodels.stats.inter_rater")
    a, b = _rater_sample(seed, cats=("0", "1", "2", "3"))
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.kappa(cm, "linear")
    ref = ir.cohens_kappa(np.array(cm.counts), wt="linear")
    assert abs(ours.value - ref.kappa) < 1e-12
    assert abs(ours.se - ref.std_kappa) < 1e-12


@pytest.mark.parametrize("seed", [3, 8, 12])
def test_mcnemar_exact_matches_statsmodels(seed):
    ct = pytest.importorskip("statsmodels.stats.contingency_tables")
    a, b = _rater_sample(seed, n=80, cats=("pos", "neg"), p_agree=0.8)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.mcnemar(cm)
    ref = ct.mcnemar(np.array(cm.counts), exact=True)
    assert abs(ours.pvalue - ref.pvalue) < 1e-12


def test_mcnemar_chi2_matches_statsmodels():
    ct = pytest.importorskip("statsmodels.stats.contingency_tables")
    counts = [[10, 900], [700, 10]]
    cm = Cat.ConfusionMatrix(["a", "b"], counts, 1620, [910, 710], [710, 910])
    ours = Cat.mcnemar(cm, exact_max=100)
    ref = ct.mcnemar(np.array(counts), exact=False, correction=True)
    assert abs(ours.statistic - ref.statistic) < 1e-10
    assert abs(ours.pvalue - ref.pvalue) < 1e-12


@pytest.mark.parametrize("seed", [1, 4, 11])
def test_stuart_maxwell_matches_statsmodels(seed):
    ct = pytest.importorskip("statsmodels.stats.contingency_tables")
    a, b = _rater_sample(seed)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.stuart_maxwell(cm)
    # shift_zeros=False: statsmodels otherwise adds 0.5 to every cell when the
    # table contains a zero, which changes the statistic.
    ref = ct.SquareTable(np.array(cm.counts), shift_zeros=False).homogeneity()
    assert ours.df == ref.df
    assert abs(ours.statistic - ref.statistic) < 1e-8
    assert abs(ours.pvalue - ref.pvalue) < 1e-10


@pytest.mark.parametrize("seed", [7, 13])
def test_kappa_se_agrees_with_bootstrap(seed):
    """The asymptotic SE should track a nonparametric bootstrap SE."""
    a, b = _rater_sample(seed, n=300)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.kappa(cm)
    rng = random.Random(99)
    boots = []
    for _ in range(1500):
        idx = [rng.randrange(len(a)) for _ in range(len(a))]
        c = Cat.confusion_matrix([a[i] for i in idx], [b[i] for i in idx],
                                 categories=cm.categories)
        boots.append(Cat.kappa(c).value)
    boot_se = np.std(boots, ddof=1)
    assert abs(ours.se - boot_se) < 0.12 * ours.se


@pytest.mark.parametrize("seed", [7, 13])
def test_gwet_ac1_linearization_se_agrees_with_bootstrap(seed):
    """Gwet's linearized variance vs a bootstrap. This is a coarse sanity net
    (a bootstrap SE is itself noisy); the exact influence-function algebra is
    pinned by the hand-computed test in test_categorical.py."""
    a, b = _rater_sample(seed, n=300)
    cm = Cat.confusion_matrix(a, b)
    ours = Cat.gwet_ac(cm)
    rng = random.Random(123)
    boots = []
    for _ in range(1500):
        idx = [rng.randrange(len(a)) for _ in range(len(a))]
        c = Cat.confusion_matrix([a[i] for i in idx], [b[i] for i in idx],
                                 categories=cm.categories)
        boots.append(Cat.gwet_ac(c).value)
    boot_se = np.std(boots, ddof=1)
    assert abs(ours.se - boot_se) < 0.12 * ours.se


def test_quadratic_weighted_kappa_approximates_icc_on_scores():
    """A classic identity: quadratic-weighted kappa ~ ICC(2,1) on the scores."""
    a, b = _rater_sample(4, n=400, cats=("0", "1", "2", "3"))
    cm = Cat.confusion_matrix(a, b)
    kq = Cat.kappa(cm, "quadratic").value
    i21, _i31, _ms = A.icc([[float(x), float(y)] for x, y in zip(a, b)])
    assert abs(kq - i21.value) < 0.05


# --------------------------------------------------------------------------
# Multi-rater (3+ raters) cross-checks
# --------------------------------------------------------------------------
def _multi_counts():
    from agreestat import multirater as MR
    rows = [["mild", "mild", "moderate"], ["severe", "severe", "severe"],
            ["mild", "moderate", "mild"], ["moderate", "moderate", "moderate"],
            ["severe", "moderate", "severe"], ["mild", "mild", "mild"],
            ["moderate", "severe", "moderate"], ["severe", "severe", "moderate"],
            ["mild", "mild", "mild"], ["moderate", "moderate", "severe"]]
    cats = ["mild", "moderate", "severe"]
    return MR, rows, cats, [[r.count(c) for c in cats] for r in rows]


def test_fleiss_kappa_matches_statsmodels():
    ir = pytest.importorskip("statsmodels.stats.inter_rater")
    MR, _rows, _cats, counts = _multi_counts()
    ours = MR.fleiss_kappa(counts)[0]
    assert abs(ours - ir.fleiss_kappa(np.array(counts))) < 1e-12


def test_fleiss_h0_se_matches_irr_formula():
    """R irr::kappam.fleiss's SE, written in its own (p_j q_j) form."""
    MR, _rows, _cats, counts = _multi_counts()
    _kap, _pbar, _pe, se, m, p_j = MR.fleiss_kappa(counts)
    n = len(counts)
    pj = np.array(p_j)
    qj = 1.0 - pj
    s = float((pj * qj).sum())
    var = (2.0 / (n * m * (m - 1))) * (s ** 2 - float((pj * qj * (qj - pj)).sum())) / s ** 2
    assert abs(se - np.sqrt(var)) < 1e-14


def test_fleiss_h0_se_matches_monte_carlo_under_independence():
    """Independent raters (H0) -> the analytic SE must match the simulated SD.

    Deliberately uses *skewed* marginals, where an m-dependent (wrong) variance
    formula is off by a factor of ~1.5-3.
    """
    import random as _random

    from agreestat.multirater import fleiss_kappa as fk
    rng = _random.Random(20260731)
    probs, n_subj, m = (0.7, 0.3), 60, 4
    counts0 = []
    for _ in range(n_subj):          # one table just to get the analytic SE
        c = [0, 0]
        for _r in range(m):
            c[0 if rng.random() < probs[0] else 1] += 1
        counts0.append(c)
    draws = []
    for _ in range(3000):
        tbl = []
        for _s in range(n_subj):
            c = [0, 0]
            for _r in range(m):
                c[0 if rng.random() < probs[0] else 1] += 1
            tbl.append(c)
        draws.append(fk(tbl)[0])
    mc_sd = float(np.std(draws, ddof=1))
    analytic = fk(counts0)[3]
    assert abs(analytic - mc_sd) < 0.15 * mc_sd


def test_krippendorff_alpha_multi_matches_reference_package():
    kd = pytest.importorskip("krippendorff")
    MR, rows, cats, counts = _multi_counts()
    code = {c: i for i, c in enumerate(cats)}
    rel = np.array([[code[r[j]] for r in rows] for j in range(3)], dtype=float)
    for metric in ("nominal", "ordinal"):
        ours = MR.krippendorff_alpha_multi(counts, cats, metric)
        ref = kd.alpha(reliability_data=rel, level_of_measurement=metric)
        assert abs(ours - ref) < 1e-12, metric


def test_icc_family_matches_pingouin():
    pg = pytest.importorskip("pingouin")
    pd = pytest.importorskip("pandas")
    from agreestat import multirater as MR
    rng = np.random.default_rng(7)
    subj = rng.normal(0, 2, 20)
    tab = np.array([[subj[i] + rng.normal(0, 0.5) + off
                     for off in (0.0, 0.3, -0.2)] for i in range(20)])
    fam = MR.icc_family(tab.tolist())
    df = pd.DataFrame({"s": np.repeat(np.arange(20), 3),
                       "r": np.tile(np.arange(3), 20),
                       "v": tab.reshape(-1)})
    ref = pg.intraclass_corr(data=df, targets="s", raters="r", ratings="v")
    order = ["ICC(1,1)", "ICC(A,1)", "ICC(C,1)", "ICC(1,k)", "ICC(A,k)",
             "ICC(C,k)"]
    ours = [fam.single[0], fam.single[1], fam.single[2],
            fam.average[0], fam.average[1], fam.average[2]]
    ci_col = next((c for c in ref.columns if str(c).startswith("CI")), None)
    for name, mine in zip(order, ours):
        row = ref[ref["Type"] == name].iloc[0]
        assert abs(mine.value - float(row["ICC"])) < 1e-9, name
        if ci_col is not None:            # column name varies across versions
            lo, hi = row[ci_col]
            assert abs(mine.ci_lower - lo) < 5e-3, name
            assert abs(mine.ci_upper - hi) < 5e-3, name
