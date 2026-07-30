"""Epoch-level statistics: summaries, autocorrelation adjustment, trend tests.

Mann–Kendall, Kendall's tau-b and Theil–Sen are checked against hand-computed values
on tiny series (where S and the variance can be written out by hand) and, when SciPy
is installed, against ``scipy.stats.kendalltau``/``theilslopes``.
"""

import math
import random
import statistics
import unittest

from eegband.stats import (
    MAX_EXACT_TREND_N,
    effective_n,
    lag1_autocorr,
    mann_kendall,
    quantile,
    summary_stats,
    t_crit,
    theil_sen_slope,
    trend,
)


class TestSummaryStats(unittest.TestCase):
    def test_matches_statistics_module(self):
        vals = [3.0, 1.0, 4.0, 1.5, 9.0, 2.6]
        st = summary_stats(vals)
        self.assertAlmostEqual(st["mean"], statistics.fmean(vals), places=12)
        self.assertAlmostEqual(st["sd"], statistics.stdev(vals), places=12)
        self.assertAlmostEqual(st["sem"], statistics.stdev(vals) / math.sqrt(6),
                               places=12)
        self.assertEqual(st["min"], 1.0)
        self.assertEqual(st["max"], 9.0)
        self.assertEqual(st["n"], 6.0)
        self.assertAlmostEqual(st["median"], statistics.median(vals), places=12)

    def test_quantiles_match_numpy(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        rng = random.Random(4)
        for _ in range(20):
            vals = sorted(rng.uniform(-5, 5) for _ in range(rng.randint(1, 40)))
            for q in (0.0, 0.25, 0.5, 0.75, 1.0):
                self.assertAlmostEqual(quantile(vals, q),
                                       float(np.quantile(vals, q)), places=12)

    def test_single_value_has_undefined_spread_not_zero(self):
        """n=1 must yield NaN spread, never a fabricated '± 0.000' / zero-width CI."""
        st = summary_stats([7.5])
        for key in ("sd", "sem", "sem_adj", "ci_lo", "ci_hi",
                    "ci_lo_adj", "ci_hi_adj", "rho1"):
            self.assertTrue(math.isnan(st[key]), f"{key} should be NaN, got {st[key]}")
        for key in ("mean", "median", "q1", "q3", "min", "max"):
            self.assertEqual(st[key], 7.5)
        self.assertEqual(st["n"], 1.0)
        self.assertEqual(st["adjusted"], 0.0)

    def test_adjusted_flag_reports_whether_an_adjustment_happened(self):
        smooth = summary_stats([math.sin(i / 6.0) + 5.0 for i in range(40)])
        self.assertEqual(smooth["adjusted"], 1.0)
        self.assertLess(smooth["n_eff"], smooth["n"])
        alternating = summary_stats([1.0, -1.0] * 8)      # rho < 0 -> no adjustment
        self.assertEqual(alternating["adjusted"], 0.0)
        self.assertEqual(alternating["n_eff"], alternating["n"])
        self.assertAlmostEqual(alternating["ci_lo_adj"], alternating["ci_lo"],
                               places=12)

    def test_quantile_rejects_out_of_range_q(self):
        for q in (-0.5, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                quantile([1.0, 2.0, 3.0], q)

    def test_ci_uses_student_t(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        st = summary_stats(vals)
        sd = statistics.stdev(vals)
        half = t_crit(4) * sd / math.sqrt(5)
        self.assertAlmostEqual(st["ci_hi"] - st["mean"], half, places=12)

    def test_t_crit_matches_scipy(self):
        try:
            from scipy import stats as sps
        except ImportError:
            self.skipTest("scipy not installed")
        for df in (1, 2, 5, 10, 30, 31, 50, 200, 5000):
            self.assertAlmostEqual(t_crit(df), float(sps.t.ppf(0.975, df)),
                                   delta=1e-3 if df <= 30 else 1e-6)

    def test_t_crit_is_continuous_and_decreasing(self):
        prev = t_crit(1)
        for df in range(2, 400):
            cur = t_crit(df)
            self.assertLess(cur, prev)
            prev = cur
        self.assertGreater(t_crit(400), 1.959963)
        self.assertTrue(math.isnan(t_crit(0)))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            summary_stats([])
        with self.assertRaises(ValueError):
            quantile([], 0.5)


class TestAutocorrelation(unittest.TestCase):
    def test_lag1_of_known_series(self):
        # perfectly alternating series -> strongly negative lag-1 autocorrelation
        vals = [1.0, -1.0] * 8
        self.assertLess(lag1_autocorr(vals), -0.8)
        # monotone ramp -> strongly positive
        self.assertGreater(lag1_autocorr([float(i) for i in range(16)]), 0.8)

    def test_lag1_formula(self):
        vals = [2.0, 4.0, 3.0, 7.0, 5.0]
        mean = statistics.fmean(vals)
        num = sum((vals[i] - mean) * (vals[i + 1] - mean) for i in range(4))
        den = sum((v - mean) ** 2 for v in vals)
        self.assertAlmostEqual(lag1_autocorr(vals), num / den, places=12)

    def test_degenerate_inputs(self):
        self.assertIsNone(lag1_autocorr([1.0, 2.0]))
        self.assertIsNone(lag1_autocorr([5.0] * 10))       # zero variance

    def test_effective_n(self):
        self.assertEqual(effective_n(10, 0.0), 10.0)
        self.assertEqual(effective_n(10, None), 10.0)
        self.assertEqual(effective_n(10, -0.5), 10.0)      # never inflate n
        self.assertAlmostEqual(effective_n(10, 1 / 3), 5.0, places=12)
        self.assertEqual(effective_n(1, 0.9), 1.0)
        self.assertGreaterEqual(effective_n(100, 0.999), 2.0)

    def test_adjusted_ci_is_wider_when_autocorrelated(self):
        # smooth (AR-like) series: heavy positive autocorrelation
        vals = [math.sin(i / 6.0) + 5.0 for i in range(40)]
        st = summary_stats(vals)
        self.assertGreater(st["rho1"], 0.5)
        self.assertLess(st["n_eff"], st["n"])
        self.assertLess(st["ci_lo_adj"], st["ci_lo"])
        self.assertGreater(st["ci_hi_adj"], st["ci_hi"])

    def test_adjusted_equals_naive_for_white_noise(self):
        rng = random.Random(11)
        vals = [rng.gauss(0, 1) for _ in range(500)]
        st = summary_stats(vals)
        self.assertLess(abs(st["rho1"]), 0.15)
        self.assertLessEqual(st["ci_hi_adj"] - st["ci_lo_adj"],
                             1.6 * (st["ci_hi"] - st["ci_lo"]))


class TestMannKendall(unittest.TestCase):
    def test_hand_computed_monotone(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        mk = mann_kendall(vals)
        self.assertEqual(mk["s"], 10.0)                    # all 10 pairs increasing
        # Var(S) = n(n-1)(2n+5)/18 = 5*4*15/18
        self.assertAlmostEqual(mk["var_s"], 5 * 4 * 15 / 18, places=12)
        self.assertAlmostEqual(mk["z"], 9 / math.sqrt(50 / 3), places=12)
        self.assertAlmostEqual(mk["tau"], 1.0, places=12)
        self.assertLess(mk["p"], 0.05)

    def test_sign_symmetry(self):
        vals = [1.0, 3.0, 2.0, 6.0, 5.0, 9.0]
        up = mann_kendall(vals)
        down = mann_kendall([-v for v in vals])
        self.assertAlmostEqual(up["s"], -down["s"], places=12)
        self.assertAlmostEqual(up["z"], -down["z"], places=12)
        self.assertAlmostEqual(up["p"], down["p"], places=12)
        self.assertAlmostEqual(up["tau"], -down["tau"], places=12)

    def test_tie_correction(self):
        vals = [1.0, 1.0, 1.0, 2.0, 2.0, 3.0]
        mk = mann_kendall(vals)
        n = 6
        ties = 3 * 2 * 11 + 2 * 1 * 9        # groups of 3 and 2
        self.assertAlmostEqual(mk["var_s"], (n * 5 * 17 - ties) / 18, places=12)

    def test_all_tied_returns_none(self):
        self.assertIsNone(mann_kendall([4.0] * 8))

    def test_short_series_returns_none(self):
        self.assertIsNone(mann_kendall([1.0, 2.0, 3.0]))

    def test_matches_scipy_kendalltau(self):
        try:
            from scipy import stats as sps
        except ImportError:
            self.skipTest("scipy not installed")
        rng = random.Random(1234)
        for _ in range(25):
            n = rng.randint(6, 40)
            vals = [round(rng.gauss(0, 1), 2) for _ in range(n)]
            if rng.random() < 0.4:      # inject ties
                vals = [round(v, 0) for v in vals]
            mk = mann_kendall(vals)
            if mk is None:
                continue
            ref = sps.kendalltau(list(range(n)), vals, variant="b")
            self.assertAlmostEqual(mk["tau"], float(ref.statistic), places=9)

    def test_p_matches_normal_approximation(self):
        try:
            from scipy import stats as sps
        except ImportError:
            self.skipTest("scipy not installed")
        vals = [float(i) + (0.5 if i % 3 else 0.0) for i in range(25)]
        mk = mann_kendall(vals)
        expect = 2.0 * sps.norm.sf(abs(mk["z"]))
        self.assertAlmostEqual(mk["p"], float(expect), places=12)


class TestTheilSen(unittest.TestCase):
    def test_exact_line(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        vals = [3.0 + 2.5 * x for x in xs]
        ts = theil_sen_slope(vals, xs)
        self.assertAlmostEqual(ts["slope"], 2.5, places=12)
        self.assertAlmostEqual(ts["slope_lo"], 2.5, places=12)
        self.assertAlmostEqual(ts["slope_hi"], 2.5, places=12)

    def test_resistant_to_one_outlier(self):
        xs = list(range(11))
        vals = [2.0 * x for x in xs]
        vals[5] = 500.0                       # single wild epoch
        ts = theil_sen_slope(vals, [float(x) for x in xs])
        self.assertAlmostEqual(ts["slope"], 2.0, delta=0.4)

    def test_matches_scipy_theilslopes(self):
        try:
            from scipy import stats as sps
        except ImportError:
            self.skipTest("scipy not installed")
        rng = random.Random(99)
        for _ in range(15):
            n = rng.randint(6, 30)
            xs = [float(i) for i in range(n)]
            vals = [0.7 * x + rng.gauss(0, 2) for x in xs]
            ts = theil_sen_slope(vals, xs)
            ref = sps.theilslopes(vals, xs, alpha=0.95)
            self.assertAlmostEqual(ts["slope"], float(ref.slope), places=9)
            self.assertAlmostEqual(ts["slope_lo"], float(ref.low_slope), places=6)
            self.assertAlmostEqual(ts["slope_hi"], float(ref.high_slope), places=6)

    def test_default_x_is_index(self):
        vals = [1.0, 3.0, 5.0, 7.0]
        self.assertAlmostEqual(theil_sen_slope(vals)["slope"], 2.0, places=12)

    def test_degenerate(self):
        self.assertIsNone(theil_sen_slope([1.0]))
        self.assertIsNone(theil_sen_slope([1.0, 2.0], [3.0, 3.0]))  # no x spread
        with self.assertRaises(ValueError):
            theil_sen_slope([1.0, 2.0], [0.0])


class TestTrend(unittest.TestCase):
    def test_declining_swa_is_detected(self):
        """A homeostatic-style decline must come out significant and negative."""
        vals = [100.0 - 3.0 * i for i in range(20)]
        xs = [30.0 * i for i in range(20)]
        tr = trend(vals, xs, x_unit="sec")
        self.assertTrue(tr.exact)
        self.assertLess(tr.slope, 0.0)
        self.assertAlmostEqual(tr.slope, -0.1, places=12)   # -3 per 30 s
        self.assertLess(tr.p, 1e-3)
        self.assertAlmostEqual(tr.tau, -1.0, places=12)
        self.assertEqual(tr.x_unit, "sec")

    def test_flat_series_is_not_significant(self):
        vals = [50.0, 50.4, 49.8, 50.1, 50.2, 49.9, 50.3, 50.0]
        tr = trend(vals)
        self.assertGreater(tr.p, 0.2)

    def test_short_or_tied_series_is_none(self):
        self.assertIsNone(trend([1.0, 2.0, 3.0]))
        self.assertIsNone(trend([1.0] * 10))

    def test_over_cap_is_flagged_not_computed(self):
        vals = [float(i % 7) for i in range(MAX_EXACT_TREND_N + 5)]
        tr = trend(vals, max_n=MAX_EXACT_TREND_N)
        self.assertFalse(tr.exact)
        self.assertTrue(math.isnan(tr.p))
        self.assertEqual(tr.n, MAX_EXACT_TREND_N + 5)

    def test_uneven_x_spacing_is_handled(self):
        """Rejected epochs leave gaps; the slope must use the real times."""
        xs = [0.0, 30.0, 90.0, 120.0]      # epoch 2 rejected
        vals = [10.0, 20.0, 40.0, 50.0]
        tr = trend(vals, xs)
        self.assertAlmostEqual(tr.slope, 1.0 / 3.0, places=12)


if __name__ == "__main__":
    unittest.main()
