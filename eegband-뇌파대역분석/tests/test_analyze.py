"""End-to-end analysis: band dominance, epoching, SWA, and adversarial inputs."""

import math
import os
import unittest

from eegband import load_signal, render_text, to_dict
from eegband.analyze import analyze, resolve_fs

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

    def test_fs_mismatch_warning(self):
        times = [k / 128.0 for k in range(256)]
        fs, source, warns = resolve_fs(100.0, times)
        self.assertAlmostEqual(fs, 128.0, delta=0.1)
        self.assertEqual(source, "inferred (user mismatch)")
        self.assertTrue(any("disagrees" in w for w in warns))

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


if __name__ == "__main__":
    unittest.main()
