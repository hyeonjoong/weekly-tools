"""Baseline-vs-post contrasts and the statistics behind them.

Every estimator here is checked against a value computed independently: scipy where it
is installed (the same convention as ``tests/test_oracles.py``), and hard-coded
published/hand-derived numbers otherwise, so a scipy-less environment still cross-
checks rather than silently skipping.
"""

import math
import os
import random
import unittest

from eegband.analyze import analyze
from eegband.report import render_csv_summary, render_text, to_dict
from eegband.stats import (
    ContrastResult,
    bh_fdr,
    student_t_sf,
    t_crit,
    t_quantile,
    welch_ttest,
)

try:                                     # pragma: no cover - environment dependent
    import scipy.stats as _ss
except Exception:                        # pragma: no cover
    _ss = None

_REQUIRE = bool(os.environ.get("EEGBAND_REQUIRE_ORACLES"))


def _need_scipy(test):
    if _ss is None:
        if _REQUIRE:
            raise AssertionError("scipy required (EEGBAND_REQUIRE_ORACLES=1)")
        test.skipTest("scipy not installed")


FS = 128.0


def pd_signal(seconds=240.0, fs=FS, switch=120.0, base_amp=15.0, post_amp=30.0,
              seed=3):
    """A recording whose 1.5 Hz slow-wave amplitude doubles after ``switch`` sec."""
    rng = random.Random(seed)
    out = []
    for i in range(int(seconds * fs)):
        t = i / fs
        amp = base_amp if t < switch else post_amp
        out.append(amp * math.sin(2 * math.pi * 1.5 * t + 0.7)
                   + 10.0 * math.sin(2 * math.pi * 10.0 * t)
                   + 5.0 * rng.gauss(0.0, 1.0))
    return out


class TestStudentT(unittest.TestCase):
    def test_sf_matches_published_table(self):
        # P(T > 2.228) = 0.025 for df = 10 (standard t table).
        self.assertAlmostEqual(student_t_sf(2.228, 10), 0.025, places=4)
        # df = 1 is Cauchy: P(T > 1) = 1/4 exactly.
        self.assertAlmostEqual(student_t_sf(1.0, 1), 0.25, places=10)
        # Symmetry about zero.
        self.assertAlmostEqual(student_t_sf(0.0, 7), 0.5, places=12)
        self.assertAlmostEqual(student_t_sf(-1.3, 7) + student_t_sf(1.3, 7), 1.0,
                               places=12)

    def test_sf_matches_scipy_including_fractional_df(self):
        _need_scipy(self)
        for t, df in [(2.0, 5), (0.3, 1.5), (-1.2, 30), (5.0, 100.7), (0.0, 2.25)]:
            self.assertAlmostEqual(student_t_sf(t, df), float(_ss.t.sf(t, df)),
                                   places=12)

    def test_sf_of_infinite_t(self):
        self.assertEqual(student_t_sf(float("inf"), 5), 0.0)
        self.assertEqual(student_t_sf(float("-inf"), 5), 1.0)
        self.assertTrue(math.isnan(student_t_sf(1.0, 0)))

    def test_quantile_agrees_with_the_existing_table(self):
        for df in (1, 5, 10, 30):
            self.assertAlmostEqual(t_quantile(0.05, df), t_crit(df), places=3)

    def test_quantile_matches_scipy_for_fractional_df(self):
        _need_scipy(self)
        for df in (2.7, 12.34, 61.9):
            self.assertAlmostEqual(t_quantile(0.05, df),
                                   float(_ss.t.ppf(0.975, df)), places=9)

    def test_quantile_rejects_bad_p(self):
        with self.assertRaises(ValueError):
            t_quantile(0.0, 10)
        with self.assertRaises(ValueError):
            t_quantile(1.0, 10)


class TestWelchTTest(unittest.TestCase):
    A = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0]
    B = [5.0, 6.0, 7.0, 6.0, 8.0, 9.0, 7.0]

    def test_unadjusted_matches_scipy(self):
        _need_scipy(self)
        cr = welch_ttest(self.A, self.B, adjust_autocorr=False)
        ref = _ss.ttest_ind(self.B, self.A, equal_var=False)
        self.assertAlmostEqual(cr.t, float(ref.statistic), places=10)
        self.assertAlmostEqual(cr.p, float(ref.pvalue), places=12)
        self.assertAlmostEqual(cr.df, float(ref.df), places=10)

    def test_ci_matches_a_hand_computation(self):
        cr = welch_ttest(self.A, self.B, adjust_autocorr=False)
        half = t_quantile(0.05, cr.df) * cr.se
        self.assertAlmostEqual(cr.ci_hi - cr.ci_lo, 2 * half, places=12)
        self.assertAlmostEqual(0.5 * (cr.ci_lo + cr.ci_hi), cr.diff, places=12)

    def test_diff_and_means_are_post_minus_baseline(self):
        cr = welch_ttest(self.A, self.B, adjust_autocorr=False)
        self.assertAlmostEqual(cr.mean_a, sum(self.A) / len(self.A))
        self.assertAlmostEqual(cr.mean_b, sum(self.B) / len(self.B))
        self.assertAlmostEqual(cr.diff, cr.mean_b - cr.mean_a)
        self.assertAlmostEqual(cr.pct_change, 100.0 * cr.diff / cr.mean_a)

    def test_hedges_g_matches_the_textbook_formula(self):
        cr = welch_ttest(self.A, self.B, adjust_autocorr=False)
        na, nb = len(self.A), len(self.B)
        ma, mb = sum(self.A) / na, sum(self.B) / nb
        va = sum((v - ma) ** 2 for v in self.A) / (na - 1)
        vb = sum((v - mb) ** 2 for v in self.B) / (nb - 1)
        dfp = na + nb - 2
        sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / dfp)
        g = (mb - ma) / sp * (1.0 - 3.0 / (4.0 * dfp - 1.0))
        self.assertAlmostEqual(cr.hedges_g, g, places=12)

    def test_autocorr_adjustment_widens_and_never_narrows(self):
        # A strongly trending (hence autocorrelated) pair of series.
        a = [float(i) for i in range(20)]
        b = [float(i) + 10.0 for i in range(20)]
        raw = welch_ttest(a, b, adjust_autocorr=False)
        adj = welch_ttest(a, b, adjust_autocorr=True)
        self.assertTrue(adj.adjusted)
        self.assertGreater(adj.se, raw.se)
        self.assertGreater(adj.p, raw.p)
        self.assertLess(adj.df, raw.df)
        self.assertAlmostEqual(adj.diff, raw.diff, places=12)
        self.assertAlmostEqual(adj.hedges_g, raw.hedges_g, places=12)

    def test_uncorrelated_series_are_left_alone(self):
        rng = random.Random(11)
        a = [rng.gauss(0, 1) for _ in range(200)]
        b = [rng.gauss(0.5, 1) for _ in range(200)]
        raw = welch_ttest(a, b, adjust_autocorr=False)
        adj = welch_ttest(a, b, adjust_autocorr=True)
        # rho ~ 0, so n_eff ~ n and the two agree closely.
        self.assertLess(abs(adj.p - raw.p), 0.05)

    def test_too_few_values_returns_none(self):
        self.assertIsNone(welch_ttest([1.0], [2.0, 3.0]))
        self.assertIsNone(welch_ttest([1.0, 2.0], [3.0]))

    def test_two_constant_series_return_none(self):
        self.assertIsNone(welch_ttest([2.0] * 5, [3.0] * 5))

    def test_one_constant_series_still_tests(self):
        cr = welch_ttest([2.0] * 5, [3.0, 3.1, 2.9, 3.2, 3.0])
        self.assertIsNotNone(cr)
        self.assertTrue(math.isfinite(cr.p))
        self.assertGreater(cr.diff, 0.0)

    def test_pct_change_undefined_for_non_positive_baseline(self):
        cr = welch_ttest([-1.0, -2.0, -3.0], [1.0, 2.0, 3.0])
        self.assertTrue(math.isnan(cr.pct_change))


class TestBHFDR(unittest.TestCase):
    def test_matches_a_hand_computation(self):
        # p = .01 .04 .03 .20, m = 4: q = min over ranks of p*m/rank, monotone.
        self.assertEqual([round(q, 6) for q in bh_fdr([0.01, 0.04, 0.03, 0.20])],
                         [0.04, 0.053333, 0.053333, 0.20])

    def test_matches_scipy(self):
        _need_scipy(self)
        if not hasattr(_ss, "false_discovery_control"):
            self.skipTest("scipy too old for false_discovery_control")
        ps = [0.001, 0.9, 0.02, 0.03, 0.4, 0.045]
        ref = list(_ss.false_discovery_control(ps))
        for got, want in zip(bh_fdr(ps), ref):
            self.assertAlmostEqual(got, want, places=12)

    def test_is_monotone_in_p(self):
        rng = random.Random(5)
        ps = [rng.random() for _ in range(50)]
        qs = bh_fdr(ps)
        pairs = sorted(zip(ps, qs))
        for (_, q1), (_, q2) in zip(pairs, pairs[1:]):
            self.assertLessEqual(q1, q2 + 1e-12)

    def test_q_never_below_p_and_never_above_one(self):
        ps = [0.001, 0.5, 0.9, 0.99]
        for p, q in zip(ps, bh_fdr(ps)):
            self.assertGreaterEqual(q + 1e-12, p)
            self.assertLessEqual(q, 1.0)

    def test_non_finite_p_passes_through_and_shrinks_the_family(self):
        qs = bh_fdr([0.01, float("nan"), 0.02])
        self.assertTrue(math.isnan(qs[1]))
        # family size is 2, not 3
        self.assertAlmostEqual(qs[0], 0.02)
        self.assertAlmostEqual(qs[2], 0.02)

    def test_empty_and_all_nan(self):
        self.assertEqual(bh_fdr([]), [])
        self.assertTrue(all(math.isnan(q) for q in bh_fdr([float("nan")] * 3)))


class TestBaselineContrastIntegration(unittest.TestCase):
    def test_detects_the_injected_swa_increase(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        self.assertEqual((res.n_baseline, res.n_post), (4, 4))
        cr = res.baseline_contrasts["swa_absolute_uv2"]
        # Amplitude doubled -> power quadrupled (~ +300%).
        self.assertGreater(cr.pct_change, 200.0)
        self.assertLess(cr.q, 0.05)
        self.assertGreater(cr.hedges_g, 2.0)

    def test_unchanged_endpoint_is_not_flagged(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cr = res.baseline_contrasts["alpha_absolute_uv2"]
        self.assertLess(abs(cr.pct_change), 10.0)
        self.assertGreater(cr.q, 0.05)

    def test_flat_recording_yields_no_significant_change(self):
        rng = random.Random(9)
        sig = [20.0 * math.sin(2 * math.pi * 10.0 * i / FS) + rng.gauss(0, 5)
               for i in range(int(240 * FS))]
        res = analyze(sig, fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        sig_keys = [k for k, c in res.baseline_contrasts.items()
                    if math.isfinite(c.q) and c.q < 0.05]
        self.assertEqual(sig_keys, [])

    def test_q_values_are_the_bh_correction_over_the_deduplicated_family(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cs = res.baseline_contrasts
        # One representative per set of bit-identical contrasts.
        groups = {}
        for k, cr in cs.items():
            groups.setdefault((cr.n_a, cr.n_b, cr.mean_a, cr.mean_b,
                               cr.sd_a, cr.sd_b, cr.p), []).append(k)
        reps = [m[0] for m in groups.values()]
        self.assertEqual(res.baseline_family_size, len(reps))
        want = dict(zip(reps, bh_fdr([cs[k].p for k in reps])))
        for members in groups.values():
            for k in members:
                self.assertAlmostEqual(cs[k].q, want[members[0]], places=12)

    def test_swa_and_delta_are_one_test_not_two(self):
        """With default bands SWA *is* the delta band, so they must share a q."""
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cs = res.baseline_contrasts
        for a, b in (("swa_relative", "delta_relative"),
                     ("swa_absolute_uv2", "delta_absolute_uv2")):
            self.assertAlmostEqual(cs[a].p, cs[b].p, places=15)
            self.assertAlmostEqual(cs[a].q, cs[b].q, places=15)
        # The family must be smaller than the endpoint count by exactly the dupes.
        self.assertLess(res.baseline_family_size, len(cs))
        self.assertTrue(any("duplicates of another endpoint" in w
                            for w in res.warnings))

    def test_duplicate_free_band_set_leaves_the_family_intact(self):
        """No 'delta' band -> --swa-band defines SWA -> no structural duplicates."""
        bands = [("slow", 0.5, 2.0), ("mid", 2.0, 8.0), ("fast", 8.0, 30.0)]
        res = analyze(pd_signal(), fs=FS, bands=bands, epoch_sec=30.0,
                      baseline_sec=120.0, swa_band=(0.5, 4.0))
        self.assertEqual(res.baseline_family_size, len(res.baseline_contrasts))
        self.assertFalse(any("duplicates of another endpoint" in w
                             for w in res.warnings))

    def test_family_size_is_the_m_actually_used_in_bh(self):
        """q of the most significant endpoint must be p·m/1 with the DEDUPLICATED m.

        A duplicate used to occupy a second rank slot with an identical p, and BH
        resolves ties to the highest rank — so a copy of the top test promoted it from
        rank 1 to rank 2 and roughly halved its q. Pinning q = p·m here locks the
        honest family size in; whether that is larger or smaller than the naive value
        depends on which endpoint happens to be most significant, so the direction is
        deliberately NOT asserted.
        """
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cs = res.baseline_contrasts
        m = res.baseline_family_size
        best = min(cs, key=lambda k: cs[k].p)
        self.assertAlmostEqual(cs[best].q, min(cs[best].p * m, 1.0), places=12)
        self.assertLess(m, len(cs))

    def test_means_match_the_epochs_they_summarise(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        base = [ep for ep in res.epochs if ep.end_sec <= 120.0 + 1e-9]
        post = [ep for ep in res.epochs if ep.end_sec > 120.0 + 1e-9]
        cr = res.baseline_contrasts["total_power_uv2"]
        self.assertAlmostEqual(
            cr.mean_a, sum(e.spectrum.total_power for e in base) / len(base),
            places=9)
        self.assertAlmostEqual(
            cr.mean_b, sum(e.spectrum.total_power for e in post) / len(post),
            places=9)

    def test_baseline_boundary_is_inclusive_of_the_epoch_that_ends_on_it(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=90.0)
        self.assertEqual(res.n_baseline, 3)
        self.assertEqual(res.n_post, 5)

    def test_too_few_epochs_on_one_side_warns_and_computes_nothing(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=30.0)
        self.assertEqual(res.n_baseline, 1)
        self.assertEqual(res.baseline_contrasts, {})
        self.assertTrue(any("baseline" in w and "at least 2" in w
                            for w in res.warnings))

    def test_baseline_without_epochs_warns(self):
        res = analyze(pd_signal(seconds=60.0), fs=FS, baseline_sec=30.0)
        self.assertEqual(res.baseline_contrasts, {})
        self.assertTrue(any("--baseline needs --epoch" in w for w in res.warnings))

    def test_rejected_epochs_are_excluded_from_both_windows(self):
        sig = pd_signal()
        # Put a huge artifact in the middle of the post window.
        idx = int(200 * FS)
        for k in range(idx, idx + 10):
            sig[k] = 5000.0
        res = analyze(sig, fs=FS, epoch_sec=30.0, baseline_sec=120.0,
                      max_amp=500.0)
        self.assertEqual(res.n_epochs_rejected, 1)
        self.assertEqual(res.n_baseline + res.n_post, res.n_epochs_kept)

    def test_invalid_baseline_raises(self):
        with self.assertRaises(ValueError):
            analyze(pd_signal(seconds=60.0), fs=FS, epoch_sec=10.0,
                    baseline_sec=0.0)
        with self.assertRaises(ValueError):
            analyze(pd_signal(seconds=60.0), fs=FS, epoch_sec=10.0,
                    baseline_sec=float("nan"))

    def test_baseline_beyond_the_recording_warns_instead_of_crashing(self):
        res = analyze(pd_signal(seconds=60.0), fs=FS, epoch_sec=10.0,
                      baseline_sec=1e6)
        self.assertEqual(res.n_post, 0)
        self.assertEqual(res.baseline_contrasts, {})
        self.assertTrue(any("baseline" in w for w in res.warnings))


class TestBaselineReporting(unittest.TestCase):
    def setUp(self):
        self.res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)

    def test_text_report_has_the_section_and_the_caveat(self):
        txt = render_text(self.res)
        self.assertIn("기저 대비 변화", txt)
        self.assertIn("q(FDR)", txt)
        self.assertIn("NOT a placebo-controlled", txt)

    def test_json_round_trips_and_is_strict(self):
        import json
        d = to_dict(self.res)
        blk = d["baseline_contrast"]
        self.assertEqual(blk["baseline_sec"], 120.0)
        self.assertEqual(blk["n_baseline"], 4)
        ep = blk["endpoints"]["swa_absolute_uv2"]
        self.assertIn("q_bh_fdr", ep)
        self.assertIn("hedges_g", ep)
        json.dumps(d, allow_nan=False)      # must not contain NaN/Infinity

    def test_csv_summary_gains_baseline_columns_only_when_used(self):
        with_base = render_csv_summary([self.res], comment=False)
        head = with_base.splitlines()[0]
        self.assertIn("baseline_sec", head)
        self.assertIn("swa_absolute_uv2_base_q_fdr", head)
        plain = analyze(pd_signal(), fs=FS, epoch_sec=30.0)
        head2 = render_csv_summary([plain], comment=False).splitlines()[0]
        self.assertNotIn("baseline_sec", head2)
        self.assertNotIn("_base_q_fdr", head2)

    def test_csv_summary_always_carries_the_line_noise_columns(self):
        head = render_csv_summary([self.res], comment=False).splitlines()[0]
        for col in ("line_freq_hz", "line_detected", "line_notched",
                    "line_max_ratio", "line_excess_uv2"):
            self.assertIn(col, head)

    def test_csv_rows_and_header_stay_aligned(self):
        import csv as _csv
        import io as _io
        other = analyze(pd_signal(seed=4), fs=FS, epoch_sec=30.0,
                        baseline_sec=120.0)
        text = render_csv_summary([self.res, other], comment=False)
        rows = list(_csv.reader(_io.StringIO(text)))
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[1]), len(rows[0]))
        self.assertEqual(len(rows[2]), len(rows[0]))

    def test_mixed_batch_leaves_the_baseline_free_row_blank(self):
        import csv as _csv
        import io as _io
        plain = analyze(pd_signal(seed=6), fs=FS, epoch_sec=30.0)
        text = render_csv_summary([self.res, plain], comment=False)
        rows = list(_csv.reader(_io.StringIO(text)))
        i = rows[0].index("baseline_sec")
        self.assertNotEqual(rows[1][i], "")
        self.assertEqual(rows[2][i], "")
        self.assertEqual(len(rows[2]), len(rows[0]))


class TestContrastResultShape(unittest.TestCase):
    def test_q_defaults_to_nan_until_corrected(self):
        cr = welch_ttest([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        self.assertIsInstance(cr, ContrastResult)
        self.assertTrue(math.isnan(cr.q))


if __name__ == "__main__":
    unittest.main()
