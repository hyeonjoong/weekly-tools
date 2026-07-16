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
