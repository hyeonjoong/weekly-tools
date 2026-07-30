"""End-to-end analysis: band dominance, epoching, SWA, and adversarial inputs."""

import math
import os
import unittest

from eegband import load_signal, render_text, to_dict
from eegband.analyze import analyze, resolve_fs, signal_quality

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def _sine(fs, dur, f, amp):
    n = int(round(fs * dur))
    return [amp * math.sin(2 * math.pi * f * k / fs) for k in range(n)]


class TestBandDominance(unittest.TestCase):
    def test_alpha_signal_alpha_dominant(self):
        x = _sine(128.0, 20.0, 10.0, 20.0)
        res = analyze(x, fs=128.0)
        self.assertEqual(res.overall.dominant, "alpha")
        self.assertAlmostEqual(res.overall.peak_freq, 10.0, delta=0.5)

    def test_delta_signal_swa_dominant(self):
        x = _sine(128.0, 20.0, 1.5, 40.0)
        res = analyze(x, fs=128.0)
        self.assertEqual(res.overall.dominant, "delta")
        # SWA is the strongest band -> high relative delta
        self.assertGreater(res.overall.swa_rel, 0.9)

    def test_relative_powers_sum_to_one(self):
        x = _sine(128.0, 20.0, 10.0, 5.0)
        res = analyze(x, fs=128.0)
        s = sum(bp.relative for bp in res.overall.band_powers)
        self.assertAlmostEqual(s, 1.0, delta=1e-6)


class TestBundledExamples(unittest.TestCase):
    def test_alpha_example(self):
        p = os.path.join(EXAMPLES, "alpha_wake.csv")
        sig = load_signal(p)
        fs, source, _ = resolve_fs(128.0, sig.times)
        res = analyze(sig.values, fs=fs, fs_source=source)
        self.assertEqual(res.overall.dominant, "alpha")
        self.assertEqual(source, "inferred")
        self.assertAlmostEqual(fs, 128.0, delta=0.1)

    def test_delta_example(self):
        p = os.path.join(EXAMPLES, "delta_deep_sleep.csv")
        sig = load_signal(p)
        res = analyze(sig.values, fs=128.0)
        self.assertEqual(res.overall.dominant, "delta")
        self.assertGreater(res.overall.swa_rel, 0.8)
        # report + json render without error
        self.assertIn("SWA", render_text(res))
        d = to_dict(res)
        self.assertEqual(d["overall"]["dominant_band"], "delta")


class TestEpoching(unittest.TestCase):
    def test_epochs_and_swa_density(self):
        x = _sine(128.0, 40.0, 1.5, 40.0)      # delta-dominant, 40 s
        res = analyze(x, fs=128.0, epoch_sec=10.0)
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.swa_density, 1.0)  # all epochs delta-dominant
        self.assertEqual(res.epochs[0].start_sec, 0.0)
        self.assertAlmostEqual(res.epochs[0].end_sec, 10.0)

    def test_epoch_longer_than_signal(self):
        x = _sine(128.0, 5.0, 10.0, 5.0)        # 5 s signal
        res = analyze(x, fs=128.0, epoch_sec=30.0)
        self.assertEqual(len(res.epochs), 1)
        self.assertTrue(any("whole signal" in w for w in res.warnings))

    def test_trailing_samples_dropped_warning(self):
        x = _sine(128.0, 25.0, 10.0, 5.0)       # 25 s, epoch 10 -> 2 epochs + 5 s tail
        res = analyze(x, fs=128.0, epoch_sec=10.0)
        self.assertEqual(len(res.epochs), 2)
        self.assertTrue(any("trailing" in w for w in res.warnings))


class TestAdversarial(unittest.TestCase):
    def test_empty_signal(self):
        with self.assertRaises(ValueError):
            analyze([], fs=128.0)

    def test_non_finite_input_rejected(self):
        # a direct library caller passing NaN/inf must get a clear error, not a
        # silently all-NaN spectrum (CLI path interpolates via load_signal).
        with self.assertRaises(ValueError):
            analyze([1.0, 2.0, float("nan"), 4.0] * 40, fs=128.0)
        with self.assertRaises(ValueError):
            analyze([1.0, float("inf"), 3.0] * 40, fs=128.0)

    def test_constant_signal(self):
        res = analyze([3.0] * 1024, fs=128.0)
        self.assertEqual(res.overall.total_power, 0.0)
        self.assertEqual(res.overall.swa_rel, 0.0)
        self.assertIsNone(res.overall.dominant)
        self.assertTrue(any("constant" in w for w in res.warnings))
        # ratios are 0/0 -> NaN, must not raise
        self.assertTrue(math.isnan(res.overall.ratios["theta/alpha"]))
        render_text(res)  # should not raise
        to_dict(res)

    def test_band_edge_above_nyquist_warns(self):
        # fs=20 -> Nyquist 10 Hz; gamma up to 45 exceeds it
        res = analyze(_sine(20.0, 20.0, 3.0, 5.0), fs=20.0)
        self.assertTrue(any("Nyquist" in w for w in res.warnings))

    def test_explicit_fs_wins_over_a_disagreeing_time_column(self):
        """An explicit --fs is a statement of fact; a guessed rate is not. A time
        column in the wrong unit (Unix ms, a counter) must not silently override it."""
        times = [k / 128.0 for k in range(256)]
        fs, source, warns = resolve_fs(100.0, times)
        self.assertEqual(fs, 100.0)
        self.assertEqual(source, "user (time column disagreed)")
        self.assertTrue(any("USING --fs" in w for w in warns))
        # ...but with no --fs the inferred rate is used, with no spurious warning
        fs2, source2, warns2 = resolve_fs(None, times)
        self.assertAlmostEqual(fs2, 128.0, delta=1e-9)
        self.assertEqual(source2, "inferred")
        self.assertFalse(any("disagrees" in w for w in warns2))

    def test_inferred_fs_is_snapped_to_a_round_value(self):
        """A 6-dp time column gives 127.999998 Hz; that noise must not leak into
        every reported frequency."""
        times = [round(k / 128.0, 6) for k in range(2000)]
        fs, source, warns = resolve_fs(None, times)
        self.assertEqual(fs, 128.0)
        self.assertTrue(any("snapped" in w for w in warns))
        # a genuinely different rate is NOT snapped
        fs2, _, _ = resolve_fs(None, [k / 127.0 for k in range(200)])
        self.assertAlmostEqual(fs2, 127.0, delta=1e-9)

    def test_unusable_time_column_falls_back_instead_of_failing(self):
        fs, source, warns = resolve_fs(256.0, [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(fs, 256.0)
        self.assertEqual(source, "user (time column ignored)")
        self.assertTrue(any("unusable" in w for w in warns))

    def test_custom_bands(self):
        bands = [("low", 0.5, 8.0), ("mid", 8.0, 20.0), ("high", 20.0, 45.0)]
        res = analyze(_sine(128.0, 20.0, 10.0, 5.0), fs=128.0, bands=bands)
        self.assertEqual([bp.name for bp in res.overall.band_powers],
                         ["low", "mid", "high"])
        self.assertEqual(res.overall.dominant, "mid")

    def test_nan_free_finite_outputs(self):
        res = analyze(_sine(128.0, 20.0, 10.0, 5.0), fs=128.0)
        for bp in res.overall.band_powers:
            self.assertTrue(math.isfinite(bp.absolute))
            self.assertTrue(math.isfinite(bp.relative))


class TestSignalQuality(unittest.TestCase):
    def test_amplitude_and_rms(self):
        q = signal_quality([-2.0, 0.0, 2.0, 0.0])
        self.assertEqual(q.v_min, -2.0)
        self.assertEqual(q.v_max, 2.0)
        self.assertEqual(q.ptp, 4.0)
        self.assertAlmostEqual(q.mean, 0.0, places=12)
        self.assertAlmostEqual(q.rms, math.sqrt((4 + 0 + 4 + 0) / 4), places=12)

    def test_clipping_detected(self):
        # 6 samples pinned at max rail (=10) -> saturation
        vals = [10.0, 10.0, 10.0, 1.0, -3.0, 10.0, 10.0, 10.0, 2.0, -3.0]
        q = signal_quality(vals)
        # rails are -3 (min, 2x, repeats) and 10 (max, 6x) -> 8 clipped
        self.assertEqual(q.n_clipped, 8)
        self.assertGreater(q.frac_clipped, 0.02)
        self.assertTrue(any("clipping" in f for f in q.flags))

    def test_no_clipping_false_positive_on_continuous_signal(self):
        # Regression: the lone global min+max of a continuous trace (each unique)
        # must NOT count as clipping, even for short signals (n<100).
        import random as _r
        rng = _r.Random(3)
        for n in (30, 50, 80, 99):
            vals = [rng.gauss(0, 1) for _ in range(n)]
            q = signal_quality(vals)
            self.assertEqual(q.n_clipped, 0, f"n={n}")
            self.assertFalse(any("clipping" in f for f in q.flags), f"n={n}")

    def test_clipping_requires_repeat(self):
        # A single unique max does not count; a repeated rail does.
        self.assertEqual(signal_quality([1.0, 2.0, 3.0, 9.0]).n_clipped, 0)
        # max 9 repeats twice -> counts (min 1 unique -> 0)
        self.assertEqual(signal_quality([1.0, 9.0, 3.0, 9.0]).n_clipped, 2)

    def test_flat_run_detected(self):
        # run of four 5.0s out of 8 samples (50%) -> flag fires (non-constant signal)
        vals = [1.0, 2.0, 5.0, 5.0, 5.0, 5.0, 3.0, 4.0]
        q = signal_quality(vals)
        self.assertEqual(q.n_flat, 4)
        self.assertTrue(any("flat-lining" in f or "평탄" in f for f in q.flags))
        # a run of exactly 2 does NOT count and does not flag
        q2 = signal_quality([1.0, 2.0, 2.0, 3.0])
        self.assertEqual(q2.n_flat, 0)
        self.assertFalse(any("flat-lining" in f or "평탄" in f for f in q2.flags))

    def test_constant_signal_is_flagged_not_clipped(self):
        q = signal_quality([4.0] * 100)
        self.assertEqual(q.ptp, 0.0)
        self.assertEqual(q.n_clipped, 0)      # not "clipping"; it's flat
        self.assertEqual(q.n_flat, 100)
        self.assertTrue(any("상수" in f or "constant" in f for f in q.flags))

    def test_interpolation_fraction_flag(self):
        q = signal_quality([1.0, 2.0, 3.0, 4.0, 5.0], n_interpolated=2)
        self.assertAlmostEqual(q.frac_interpolated, 0.4)
        self.assertTrue(any("interpolation" in f for f in q.flags))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            signal_quality([])

    def test_quality_attached_to_result(self):
        res = analyze(_sine(128.0, 10.0, 10.0, 5.0), fs=128.0)
        self.assertIsNotNone(res.quality)
        self.assertEqual(res.quality.n_samples, 1280)


class TestNewSpectrumFeatures(unittest.TestCase):
    def test_per_band_peak_iaf(self):
        # 10 Hz alpha sinusoid -> alpha band peak ~10 Hz (Individual Alpha Freq)
        res = analyze(_sine(128.0, 20.0, 10.0, 20.0), fs=128.0)
        alpha = next(bp for bp in res.overall.band_powers if bp.name == "alpha")
        self.assertIsNotNone(alpha.peak_freq)
        self.assertAlmostEqual(alpha.peak_freq, 10.0, delta=0.5)

    def test_entropy_low_for_pure_tone(self):
        res = analyze(_sine(128.0, 20.0, 10.0, 20.0), fs=128.0)
        self.assertIsNotNone(res.overall.entropy)
        self.assertLess(res.overall.entropy, 0.5)   # concentrated spectrum

    def test_constant_signal_entropy_none(self):
        res = analyze([3.0] * 1024, fs=128.0)
        self.assertIsNone(res.overall.entropy)
        for bp in res.overall.band_powers:
            self.assertIsNone(bp.peak_freq)

    def test_detrend_average_threaded(self):
        x = _sine(128.0, 20.0, 10.0, 5.0)
        res = analyze(x, fs=128.0, detrend="linear", average="median")
        self.assertEqual(res.detrend, "linear")
        self.assertEqual(res.average, "median")
        d = to_dict(res)
        self.assertEqual(d["welch"]["detrend"], "linear")
        self.assertEqual(d["welch"]["average"], "median")

    def test_bad_detrend_average_raise(self):
        with self.assertRaises(ValueError):
            analyze(_sine(128.0, 5.0, 10.0, 5.0), fs=128.0, detrend="cubic")
        with self.assertRaises(ValueError):
            analyze(_sine(128.0, 5.0, 10.0, 5.0), fs=128.0, average="max")

    def test_linear_detrend_removes_drift_from_delta(self):
        # sinusoid + strong linear drift; linear detrend must cut delta leakage
        base = _sine(128.0, 40.0, 10.0, 5.0)
        drift = [base[i] + 0.05 * i for i in range(len(base))]
        res_c = analyze(drift, fs=128.0, detrend="constant")
        res_l = analyze(drift, fs=128.0, detrend="linear")
        delta_c = next(bp for bp in res_c.overall.band_powers if bp.name == "delta")
        delta_l = next(bp for bp in res_l.overall.band_powers if bp.name == "delta")
        self.assertLess(delta_l.absolute, delta_c.absolute)

    def test_linear_detrend_short_segment_warns(self):
        res = analyze(_sine(128.0, 10.0, 10.0, 5.0), fs=128.0, nperseg=2,
                      detrend="linear")
        self.assertTrue(any("degrees of freedom" in w for w in res.warnings))

    def test_peak_prominence_flag(self):
        # pure 10 Hz alpha -> alpha peak is a genuine prominent rhythm
        res = analyze(_sine(128.0, 20.0, 10.0, 20.0), fs=128.0)
        alpha = next(bp for bp in res.overall.band_powers if bp.name == "alpha")
        self.assertTrue(alpha.peak_prominent)
        self.assertAlmostEqual(alpha.peak_freq, 10.0, delta=0.5)
        # a constant signal has no prominent peak in any band
        res0 = analyze([3.0] * 1024, fs=128.0)
        self.assertFalse(any(bp.peak_prominent for bp in res0.overall.band_powers))


class TestOverlapAndShortEpoch(unittest.TestCase):
    def test_overlapping_bands_warn(self):
        res = analyze(_sine(128.0, 20.0, 10.0, 5.0), fs=128.0,
                      bands=[("a", 4.0, 13.0), ("b", 8.0, 20.0)])
        self.assertGreater(res.overall.rel_sum, 1.0)
        self.assertTrue(any("overlap" in w for w in res.warnings))

    def test_epoch_too_short_skipped(self):
        res = analyze(_sine(128.0, 10.0, 10.0, 5.0), fs=128.0, epoch_sec=0.005)
        self.assertEqual(res.epochs, [])
        self.assertTrue(any("too short" in w for w in res.warnings))


class TestArtifactRejection(unittest.TestCase):
    def _delta_with_artifact(self):
        import random as _r
        rng = _r.Random(0)
        vals = []
        for e in range(5):
            for k in range(1280):
                v = 40 * math.sin(2 * math.pi * 1.5 * k / 128) + rng.gauss(0, 2)
                if e == 2:
                    v += 400  # artifact epoch (huge amplitude)
                vals.append(v)
        return vals

    def test_epoch_rejected_and_summary_excludes(self):
        vals = self._delta_with_artifact()
        res = analyze(vals, fs=128.0, epoch_sec=10.0, max_amp=150.0)
        self.assertEqual(len(res.epochs), 5)
        self.assertTrue(res.epochs[2].rejected)
        self.assertFalse(res.epochs[0].rejected)
        self.assertEqual(res.n_epochs_rejected, 1)
        self.assertEqual(res.n_epochs_kept, 4)
        # swa_density computed over kept epochs only (4)
        self.assertIsNotNone(res.swa_density)
        self.assertTrue(any("artifact rejection" in w for w in res.warnings))

    def test_no_rejection_when_threshold_absent(self):
        vals = self._delta_with_artifact()
        res = analyze(vals, fs=128.0, epoch_sec=10.0)
        self.assertEqual(res.n_epochs_rejected, 0)
        self.assertFalse(any(ep.rejected for ep in res.epochs))
        # peak_amp is still recorded even without a threshold
        self.assertGreater(res.epochs[2].peak_amp, 300.0)

    def test_all_rejected_reports_qc_failure_and_no_summary(self):
        """A recording where every epoch fails QC must NOT produce the same numbers
        as a clean one: no summary, no density, no trend, qc_pass False."""
        res = analyze(_sine(128.0, 40.0, 1.5, 40.0), fs=128.0, epoch_sec=10.0,
                      max_amp=1.0)   # everything exceeds 1 µV
        self.assertEqual(res.n_epochs_kept, 0)
        self.assertEqual(res.n_epochs_rejected, 4)
        self.assertFalse(res.qc_pass)
        self.assertIsNone(res.swa_density)
        self.assertEqual(res.epoch_summary, {})
        self.assertEqual(res.epoch_trends, {})
        self.assertTrue(any("QC FAILURE" in w for w in res.warnings))
        # the per-epoch rows are still there, all flagged
        self.assertTrue(all(ep.rejected for ep in res.epochs))

    def test_partial_rejection_summarises_only_kept_epochs(self):
        clean = _sine(128.0, 30.0, 1.5, 40.0)
        spiky = list(clean)
        for i in range(128 * 10, 128 * 20):        # epoch 1 gets huge amplitudes
            spiky[i] *= 10.0
        res = analyze(spiky, fs=128.0, epoch_sec=10.0, max_amp=200.0)
        self.assertEqual(res.n_epochs_kept, 2)
        self.assertTrue(res.qc_pass)
        self.assertEqual(res.epoch_summary["swa_absolute_uv2"]["n"], 2.0)
        kept = [ep for ep in res.epochs if not ep.rejected]
        self.assertAlmostEqual(
            res.epoch_summary["swa_absolute_uv2"]["mean"],
            sum(ep.spectrum.swa_abs for ep in kept) / 2.0, places=9)


if __name__ == "__main__":
    unittest.main()
