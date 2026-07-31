"""The shipped example recordings must keep producing the documented answers.

These are end-to-end regressions over ``examples/``: the two single-channel CSVs whose
numbers appear in README.md, the multi-channel wide CSV, and the two-channel EDF. The
synthetic examples were built with a *known* 1/f exponent per channel, so they also
serve as an end-to-end accuracy check of the aperiodic estimator (file → report).
"""

import json
import math
import os
import unittest

from eegband import load_signal, load_signals, read_edf_channel, read_edf_info
from eegband.analyze import analyze, resolve_fs
from eegband.report import to_dict

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "examples")


def _analyse_csv(name, **kw):
    sig = load_signal(os.path.join(EX, name))
    fs, source, warns = resolve_fs(kw.pop("fs", 128.0), sig.times)
    return analyze(sig.values, fs=fs, warnings=warns, fs_source=source, **kw)


class TestSingleChannelExamples(unittest.TestCase):
    def test_delta_deep_sleep_numbers(self):
        res = _analyse_csv("delta_deep_sleep.csv")
        by = res.overall.power_by_name()
        self.assertAlmostEqual(by["delta"], 1072.564, places=2)
        self.assertAlmostEqual(res.overall.total_power, 1086.536, places=2)
        self.assertEqual(res.overall.dominant, "delta")
        self.assertAlmostEqual(res.overall.swa_rel * 100.0, 98.7, places=1)
        self.assertAlmostEqual(res.overall.peak_freq, 1.50, places=2)
        self.assertAlmostEqual(res.overall.sef, 1.89, places=2)

    def test_alpha_wake_numbers(self):
        res = _analyse_csv("alpha_wake.csv")
        self.assertEqual(res.overall.dominant, "alpha")
        rel = {bp.name: bp.relative for bp in res.overall.band_powers}
        self.assertAlmostEqual(rel["alpha"] * 100.0, 88.0, places=1)
        alpha = [bp for bp in res.overall.band_powers if bp.name == "alpha"][0]
        self.assertTrue(alpha.peak_prominent)
        self.assertAlmostEqual(alpha.peak_freq, 10.0, places=2)

    def test_examples_are_json_serialisable(self):
        for name in ("alpha_wake.csv", "delta_deep_sleep.csv"):
            res = _analyse_csv(name, epoch_sec=20.0)
            json.dumps(to_dict(res))       # must not raise


class TestMultiChannelExample(unittest.TestCase):
    """multichannel_wide.csv: Fp1 = 1/f 1.3 + alpha, Cz = 1/f 1.7 + delta,
    O1 = 1/f 1.2 + alpha (see examples/generate_examples.py)."""

    def setUp(self):
        self.path = os.path.join(EX, "multichannel_wide.csv")
        self.sigs = load_signals(self.path)

    def test_channels_and_shape(self):
        self.assertEqual([s.value_col for s in self.sigs], ["Fp1", "Cz", "O1"])
        for s in self.sigs:
            self.assertEqual(len(s.values), 2560)
            self.assertEqual(len(s.times), 2560)

    def test_dominant_bands_and_known_exponents(self):
        truth = {"Fp1": ("alpha", 1.3), "Cz": ("delta", 1.7), "O1": ("alpha", 1.2)}
        for sig in self.sigs:
            fs, source, warns = resolve_fs(128.0, sig.times)
            res = analyze(sig.values, fs=fs, fs_source=source, warnings=warns)
            want_band, want_exp = truth[sig.value_col]
            self.assertEqual(res.overall.dominant, want_band, sig.value_col)
            fit = res.overall.aperiodic
            self.assertIsNotNone(fit)
            self.assertGreater(fit.r2, 0.95, sig.value_col)
            # the true background exponent must be recovered within 0.25
            self.assertAlmostEqual(fit.exponent, want_exp, delta=0.25,
                                   msg=f"{sig.value_col}: {fit.exponent}")

    def test_alpha_rhythms_are_detected_as_prominent_peaks(self):
        for sig in self.sigs:
            if sig.value_col == "Cz":
                continue
            fs, _, _ = resolve_fs(128.0, sig.times)
            res = analyze(sig.values, fs=fs)
            alpha = [bp for bp in res.overall.band_powers if bp.name == "alpha"][0]
            self.assertTrue(alpha.adj_peak_prominent, sig.value_col)
            self.assertAlmostEqual(alpha.adj_peak_freq, 10.25, delta=0.6)


class TestEdfExample(unittest.TestCase):
    """sleep_2ch.edf: Fpz-Cz = 1/f 1.8 + 1.2 Hz slow waves + 12.5 Hz spindle,
    Pz-Oz = 1/f 1.2 + 9.5 Hz alpha (see examples/generate_examples.py)."""

    def setUp(self):
        self.path = os.path.join(EX, "sleep_2ch.edf")

    def test_header(self):
        info = read_edf_info(self.path)
        self.assertEqual(info.kind, "EDF")
        self.assertEqual([s.label for s in info.signals],
                         ["EEG Fpz-Cz", "EEG Pz-Oz"])
        self.assertEqual(info.n_records, 60)
        self.assertAlmostEqual(info.duration_sec, 60.0)
        self.assertEqual([s.fs for s in info.signals], [100.0, 100.0])
        self.assertTrue(all(s.unit_known for s in info.signals))

    def test_fpz_is_delta_dominant_with_a_spindle(self):
        sig, fs, _ = read_edf_channel(self.path, "EEG Fpz-Cz")
        res = analyze(sig.values, fs=fs, epoch_sec=20.0)
        self.assertEqual(res.overall.dominant, "delta")
        self.assertGreater(res.overall.swa_rel, 0.9)
        alpha = [bp for bp in res.overall.band_powers if bp.name == "alpha"][0]
        self.assertTrue(alpha.adj_peak_prominent)
        self.assertAlmostEqual(alpha.adj_peak_freq, 12.5, delta=0.6)
        self.assertAlmostEqual(res.overall.aperiodic.exponent, 1.8, delta=0.25)
        self.assertEqual(len(res.epochs), 3)
        self.assertEqual(res.swa_density, 1.0)

    def test_pz_is_alpha_dominant(self):
        sig, fs, _ = read_edf_channel(self.path, "EEG Pz-Oz")
        res = analyze(sig.values, fs=fs)
        self.assertEqual(res.overall.dominant, "alpha")
        alpha = [bp for bp in res.overall.band_powers if bp.name == "alpha"][0]
        self.assertAlmostEqual(alpha.adj_peak_freq, 9.5, delta=0.6)
        self.assertAlmostEqual(res.overall.aperiodic.exponent, 1.2, delta=0.25)

    def test_amplitudes_are_physiological(self):
        """A calibrated read must land in the tens-of-µV range, not volts or ADC
        counts — the classic symptom of a unit/calibration mistake."""
        for label in ("EEG Fpz-Cz", "EEG Pz-Oz"):
            sig, _, _ = read_edf_channel(self.path, label)
            rms = math.sqrt(sum(v * v for v in sig.values) / len(sig.values))
            self.assertGreater(rms, 5.0, label)
            self.assertLess(rms, 200.0, label)




class TestDoseSessionExample(unittest.TestCase):
    """dose_session.csv: 2 min baseline, then slow-wave amplitude x2, plus 60 Hz mains.

    This is the recording 실행.command demonstrates, so the claims it prints on screen
    ("60 Hz contaminates gamma", "--notch removes it", "SWA rises ~+250%") are locked
    in here rather than trusted.
    """
    BANDS = [("delta", 0.5, 4.0), ("theta", 4.0, 8.0), ("alpha", 8.0, 13.0),
             ("beta", 13.0, 30.0), ("gamma", 30.0, 90.0)]

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(EX, "dose_session.csv")
        cls.sig = load_signal(cls.path)

    def _analyze(self, **kw):
        return analyze(self.sig.values, fs=200.0, bands=self.BANDS,
                       times=self.sig.times, **kw)

    def test_file_exists_and_parses(self):
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(len(self.sig.values), 48000)

    def test_sixty_hz_mains_is_detected_in_gamma(self):
        res = self._analyze()
        lnr = res.overall.line_noise
        self.assertTrue(lnr.detected)
        self.assertEqual(lnr.f0, 60.0)
        self.assertGreater(lnr.excess_in(30.0, 90.0), 0.0)

    def test_notch_removes_most_of_gamma_power(self):
        raw = self._analyze()
        fixed = self._analyze(notch=True)

        def gamma(r):
            return next(b.absolute for b in r.overall.band_powers
                        if b.name == "gamma")
        self.assertLess(gamma(fixed), 0.5 * gamma(raw))

    def test_baseline_contrast_finds_the_injected_swa_rise(self):
        res = self._analyze(epoch_sec=30.0, baseline_sec=120.0, notch=True)
        self.assertEqual((res.n_baseline, res.n_post), (4, 4))
        cr = res.baseline_contrasts["swa_absolute_uv2"]
        # Injected: amplitude x2 -> power x4 (+300%), diluted by the 1/f background.
        self.assertGreater(cr.pct_change, 150.0)
        self.assertLess(cr.pct_change, 350.0)
        self.assertLess(cr.q, 0.05)

    def test_alpha_is_unchanged_across_the_dose(self):
        res = self._analyze(epoch_sec=30.0, baseline_sec=120.0, notch=True)
        cr = res.baseline_contrasts["alpha_absolute_uv2"]
        self.assertLess(abs(cr.pct_change), 15.0)
        self.assertGreater(cr.q, 0.05)


if __name__ == "__main__":
    unittest.main()
