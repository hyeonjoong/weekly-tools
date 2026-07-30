"""Guard against a green run that silently skipped every independent cross-check.

The package has no runtime dependencies, so a default install has no numpy/scipy and
the 15 oracle tests (FFT vs numpy.fft, Welch vs scipy.signal.welch, Mann-Kendall vs
scipy.stats.kendalltau, Theil-Sen vs theilslopes, t quantiles, type-7 quantiles, the
log-log fit vs polyfit, the slope SE vs linregress) turn into skips. A green suite in
that state is NOT evidence the estimators are right.

Set ``EEGBAND_REQUIRE_ORACLES=1`` (do this in CI) to make their absence a failure.
"""

import math
import os
import unittest

from eegband.stats import quantile, t_crit


class TestOraclesAvailable(unittest.TestCase):
    def test_cross_check_dependencies_present_when_required(self):
        if not os.environ.get("EEGBAND_REQUIRE_ORACLES"):
            self.skipTest("set EEGBAND_REQUIRE_ORACLES=1 to require numpy/scipy")
        import numpy        # noqa: F401
        import scipy        # noqa: F401


class TestStdlibOnlyReferences(unittest.TestCase):
    """Hard-coded reference values so these estimators are never oracle-less."""

    def test_t_quantiles_against_published_table(self):
        for df, want in ((1, 12.7062), (2, 4.3027), (5, 2.5706), (10, 2.2281),
                         (20, 2.0860), (30, 2.0423), (40, 2.0211), (60, 2.0003),
                         (120, 1.9799), (1000, 1.9623)):
            self.assertAlmostEqual(t_crit(df), want, delta=1.5e-3, msg=f"df={df}")

    def test_type7_quantiles_by_hand(self):
        # numpy's default (linear interpolation): pos = q*(n-1)
        vals = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(quantile(vals, 0.0), 1.0, places=12)
        self.assertAlmostEqual(quantile(vals, 0.25), 1.75, places=12)
        self.assertAlmostEqual(quantile(vals, 0.5), 2.5, places=12)
        self.assertAlmostEqual(quantile(vals, 0.75), 3.25, places=12)
        self.assertAlmostEqual(quantile(vals, 1.0), 4.0, places=12)
        odd = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertAlmostEqual(quantile(odd, 0.25), 20.0, places=12)
        self.assertAlmostEqual(quantile(odd, 0.9), 46.0, places=12)

    def test_slope_se_by_hand(self):
        """SE(slope) = sqrt(SSres/(n-2)/Sxx) on a tiny data set computed by hand."""
        from eegband.aperiodic import _ols
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [1.0, 3.0, 2.0, 5.0]
        b, a, sxx = _ols(xs, ys)
        # by hand: x̄=1.5, ȳ=2.75, Sxy=5.5, Sxx=5 -> b=1.1, a=2.75-1.1*1.5=1.1
        self.assertAlmostEqual(b, 1.1, places=12)
        self.assertAlmostEqual(a, 1.1, places=12)
        self.assertAlmostEqual(sxx, 5.0, places=12)
        # residuals -0.1, 0.8, -1.3, 0.6 -> SSres = 2.7
        ss_res = math.fsum((ys[i] - (a + b * xs[i])) ** 2 for i in range(4))
        self.assertAlmostEqual(ss_res, 2.7, places=12)
        se = math.sqrt(ss_res / 2 / sxx)              # sqrt(0.27)
        self.assertAlmostEqual(se, math.sqrt(0.27), places=12)


if __name__ == "__main__":
    unittest.main()
