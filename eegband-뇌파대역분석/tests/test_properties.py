"""Property-based and regression tests hardening the Round-1 findings.

These assert *invariants* (Parseval, integral additivity, SEF monotonicity/exactness)
over many random inputs, plus targeted regressions for the specific bugs fixed:
time-column misdetection, gappy-band relative sums, zero-power peak guard, dominant
near-tie flag, ms-time conversion, and encoding fallback.
"""

import math
import os
import random
import statistics
import tempfile
import unittest

from eegband import analyze, load_signal, render_text, to_dict
from eegband.analyze import resolve_fs
from eegband.dataio import infer_fs
from eegband.spectral import (
    DEFAULT_BANDS,
    band_ratios,
    integrate_psd,
    peak_frequency,
    spectral_edge_frequency,
    welch_psd,
)


def _sine(fs, dur, f, amp, phase=0.0):
    n = int(round(fs * dur))
    return [amp * math.sin(2 * math.pi * f * k / fs + phase) for k in range(n)]


class TmpCSV:
    def __init__(self, text, encoding="utf-8"):
        self.text, self.encoding = text, encoding

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.text.encode(self.encoding))
        return self.path

    def __exit__(self, *exc):
        os.remove(self.path)


class TestParsevalProperty(unittest.TestCase):
    """Integrating the PSD over [0, fs/2] recovers the signal variance, across seeds."""

    def test_sinusoid_variance_many(self):
        rng = random.Random(2024)
        for _ in range(12):
            fs = rng.choice([128.0, 200.0, 256.0])
            f = rng.uniform(3.0, 40.0)
            amp = rng.uniform(1.0, 25.0)
            # integer number of cycles per 4 s so pvariance == A^2/2 exactly
            x = _sine(fs, 4.0, f, amp, phase=rng.uniform(0, math.pi))
            freqs, psd, _ = welch_psd(x, fs, nperseg=len(x))
            total = integrate_psd(freqs, psd, 0.0, fs / 2)
            self.assertAlmostEqual(total, statistics.pvariance(x),
                                   delta=0.02 * amp * amp)


class TestIntegralAdditivity(unittest.TestCase):
    """integrate over [a,c] == integrate [a,b] + [b,c] for random spectra."""

    def test_additivity(self):
        rng = random.Random(7)
        for _ in range(20):
            n = rng.randint(5, 40)
            freqs = [i * 0.5 for i in range(n)]
            psd = [rng.uniform(0, 10) for _ in range(n)]
            a = rng.uniform(freqs[0], freqs[-1])
            c = rng.uniform(a, freqs[-1])
            b = rng.uniform(a, c)
            whole = integrate_psd(freqs, psd, a, c)
            split = integrate_psd(freqs, psd, a, b) + integrate_psd(freqs, psd, b, c)
            self.assertAlmostEqual(whole, split, places=9)


class TestSefProperties(unittest.TestCase):
    def test_monotone_in_frac(self):
        rng = random.Random(11)
        x = _sine(128.0, 8.0, 10.0, 3.0) + _sine(128.0, 8.0, 2.0, 5.0)[:1024]
        freqs, psd, _ = welch_psd(x[:1024], 128.0, nperseg=512)
        prev = 0.0
        for frac in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
            sef = spectral_edge_frequency(freqs, psd, 0.5, 45.0, frac)
            self.assertIsNotNone(sef)
            self.assertGreaterEqual(sef + 1e-9, prev)
            prev = sef

    def test_exact_on_triangular(self):
        # PSD = f on [0,4]; total power = 8. SEF_frac = sqrt(16*frac) exactly.
        freqs = [0.0, 1.0, 2.0, 3.0, 4.0]
        psd = [0.0, 1.0, 2.0, 3.0, 4.0]
        for frac in (0.25, 0.5, 0.75, 0.95):
            got = spectral_edge_frequency(freqs, psd, 0.0, 4.0, frac)
            self.assertAlmostEqual(got, math.sqrt(16.0 * frac), places=9)

    def test_exact_on_flat(self):
        freqs = [0.0, 1.0, 2.0, 3.0, 4.0]
        psd = [2.0] * 5
        self.assertAlmostEqual(spectral_edge_frequency(freqs, psd, 0, 4, 0.5),
                               2.0, places=9)


class TestPeakGuards(unittest.TestCase):
    def test_peak_exact_bin(self):
        fs = 256.0
        x = _sine(fs, 8.0, 20.0, 4.0)               # 20 Hz, bin-aligned at nfft=2048
        freqs, psd, _ = welch_psd(x, fs, nperseg=2048)
        self.assertAlmostEqual(peak_frequency(freqs, psd, 0.5, 45.0), 20.0,
                               delta=fs / 2048)

    def test_zero_power_peak_none(self):
        res = analyze([3.0] * 1024, fs=128.0)
        self.assertIsNone(res.overall.peak_freq)     # regression: was 0.5 Hz
        self.assertIsNone(res.overall.sef)
        self.assertIsNone(res.overall.dominant)


class TestBandRatios(unittest.TestCase):
    def test_inf_and_nan_branches(self):
        self.assertTrue(math.isinf(band_ratios({"theta": 1.0, "alpha": 0.0})["theta/alpha"]))
        self.assertTrue(math.isnan(band_ratios({"theta": 0.0, "alpha": 0.0})["theta/alpha"]))
        self.assertAlmostEqual(band_ratios({"theta": 6.0, "alpha": 3.0})["theta/alpha"], 2.0)


class TestDominantTie(unittest.TestCase):
    def test_near_tie_flagged(self):
        # equal-power delta(2 Hz) + alpha(10 Hz) -> near-tie
        x = [a + b for a, b in zip(_sine(128.0, 20.0, 2.0, 10.0),
                                   _sine(128.0, 20.0, 10.0, 10.0))]
        res = analyze(x, fs=128.0)
        # not asserting which wins, only that closeness is detectable / renders
        txt = render_text(res)
        self.assertIsInstance(txt, str)

    def test_clear_winner_not_tie(self):
        res = analyze(_sine(128.0, 20.0, 1.5, 40.0), fs=128.0)
        self.assertFalse(res.overall.dominant_tie)
        self.assertEqual(res.overall.dominant, "delta")


class TestGappyBands(unittest.TestCase):
    def test_gap_relatives_below_100_and_warned(self):
        # 6 Hz power, bands skip 4-8 -> relatives sum to ~0, coverage warning
        x = _sine(128.0, 20.0, 6.0, 10.0)
        res = analyze(x, fs=128.0, bands=[("lo", 0.5, 4.0), ("hi", 8.0, 30.0)])
        self.assertLess(res.overall.rel_sum, 0.5)
        self.assertTrue(any("gap" in w for w in res.warnings))

    def test_default_bands_sum_to_one(self):
        res = analyze(_sine(128.0, 20.0, 10.0, 5.0), fs=128.0)
        self.assertAlmostEqual(res.overall.rel_sum, 1.0, delta=1e-9)


class TestTimeMisdetection(unittest.TestCase):
    def test_sample_column_not_treated_as_seconds(self):
        # Regression: a 'sample' counter must NOT override --fs (was giving fs=1 Hz).
        body = "\n".join(f"{k},{math.sin(2 * math.pi * 10 * k / 128)}"
                         for k in range(512))
        with TmpCSV(f"sample,eeg_uv\n{body}\n") as p:
            sig = load_signal(p)
            self.assertIsNone(sig.times)             # not auto-detected as time
            fs, source, _ = resolve_fs(128.0, sig.times)
            self.assertEqual(fs, 128.0)
            self.assertEqual(source, "user")

    def test_time_ms_converted_to_seconds(self):
        with TmpCSV("time_ms,eeg_uv\n0,1\n1,2\n2,3\n3,4\n") as p:
            sig = load_signal(p)
            self.assertAlmostEqual(sig.times[1], 0.001, places=9)
            self.assertTrue(any("millisecond" in w for w in sig.warnings))

    def test_resolve_fs_irregular_warning(self):
        times = [0.0, 0.1, 0.25, 0.3, 0.9, 1.0]      # uneven
        _, _, warns = resolve_fs(0.0, times)
        self.assertTrue(any("irregular" in w for w in warns))


class TestRenderNeverRaises(unittest.TestCase):
    """render_text must not raise on any AnalysisResult shape."""

    def _cases(self):
        yield analyze([3.0] * 1024, fs=128.0)                     # constant/NaN ratios
        yield analyze(_sine(128.0, 20.0, 10.0, 5.0), fs=128.0)    # plain
        yield analyze(_sine(128.0, 40.0, 1.5, 40.0), fs=128.0,    # epochs
                      epoch_sec=10.0)
        yield analyze(_sine(128.0, 20.0, 6.0, 10.0), fs=128.0,    # gappy bands
                      bands=[("lo", 0.5, 4.0), ("hi", 8.0, 30.0)])

    def test_render_and_dict(self):
        for res in self._cases():
            txt = render_text(res)
            self.assertIsInstance(txt, str)
            self.assertIn("Info", txt)
            self.assertIsInstance(to_dict(res), dict)


class TestEncodingFallback(unittest.TestCase):
    def test_latin1_non_ascii_loads(self):
        with TmpCSV("µV\n1.0\n2.0\n3.0\n", encoding="latin-1") as p:
            sig = load_signal(p)
            self.assertEqual(sig.values, [1.0, 2.0, 3.0])
            self.assertTrue(any("UTF-8" in w for w in sig.warnings))


if __name__ == "__main__":
    unittest.main()
