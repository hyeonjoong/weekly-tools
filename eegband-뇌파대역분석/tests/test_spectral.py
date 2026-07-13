"""Welch PSD and band-power correctness.

Two anchors:
  * an analytic pure-sinusoid check (Parseval + correct band) that needs no
    third-party library, and
  * a guarded cross-check against scipy.signal.welch to ~1e-9 when SciPy is present.
"""

import math
import statistics
import unittest

from eegband import spectral
from eegband.spectral import (
    band_powers,
    integrate_psd,
    peak_frequency,
    spectral_edge_frequency,
    welch_psd,
)

try:
    import numpy as _np
    from scipy import signal as _sig
    HAVE_SCIPY = True
except ImportError:  # pragma: no cover
    HAVE_SCIPY = False


def _sine(fs, dur, f, amp, phase=0.0):
    n = int(round(fs * dur))
    return [amp * math.sin(2 * math.pi * f * k / fs + phase) for k in range(n)]


class TestParsevalSinusoid(unittest.TestCase):
    """Integrating the PSD recovers the signal variance; power lands in one band."""

    def test_total_power_equals_variance(self):
        fs, f, amp = 256.0, 10.0, 3.0        # 10 Hz alpha, integer cycles/segment
        x = _sine(fs, 8.0, f, amp)
        freqs, psd, meta = welch_psd(x, fs, nperseg=512)
        total_full = integrate_psd(freqs, psd, 0.0, fs / 2)
        expected = amp * amp / 2.0           # 4.5
        self.assertAlmostEqual(total_full, expected, delta=0.02)   # <0.5%
        # population variance of an integer-cycle sinusoid is exactly A²/2
        self.assertAlmostEqual(statistics.pvariance(x), expected, delta=1e-6)

    def test_power_lands_in_alpha_band(self):
        fs, f, amp = 256.0, 10.0, 3.0
        x = _sine(fs, 8.0, f, amp)
        freqs, psd, _ = welch_psd(x, fs, nperseg=512)
        bp = dict((name, absv) for name, lo, hi, absv in band_powers(freqs, psd))
        total = sum(bp.values())
        self.assertGreater(bp["alpha"] / total, 0.99)      # ~all power in alpha
        for other in ("delta", "theta", "beta", "gamma"):
            self.assertLess(bp[other] / total, 0.01)
        self.assertAlmostEqual(peak_frequency(freqs, psd, 0.5, 45.0), f,
                               delta=fs / 512)              # within one bin

    def test_delta_sinusoid_lands_in_delta(self):
        fs, f, amp = 128.0, 1.5, 5.0
        x = _sine(fs, 32.0, f, amp)
        freqs, psd, _ = welch_psd(x, fs, nperseg=512)
        bp = dict((name, absv) for name, lo, hi, absv in band_powers(freqs, psd))
        self.assertEqual(max(bp, key=bp.get), "delta")


class TestIntegrationHelpers(unittest.TestCase):
    def test_flat_psd_integral(self):
        # PSD = 2.0 everywhere on 0..10 Hz -> integral over [2,5] = 2*3 = 6
        freqs = [float(k) for k in range(0, 11)]
        psd = [2.0] * len(freqs)
        self.assertAlmostEqual(integrate_psd(freqs, psd, 2.0, 5.0), 6.0, places=9)

    def test_edge_interpolation(self):
        # triangular PSD; integral over a sub-interval matches analytic trapz
        freqs = [0.0, 1.0, 2.0, 3.0, 4.0]
        psd = [0.0, 1.0, 2.0, 1.0, 0.0]
        # integrate [0.5, 2.5]: interp at 0.5 -> 0.5, at 2.5 -> 1.5
        # points (0.5,0.5),(1,1),(2,2),(2.5,1.5)
        got = integrate_psd(freqs, psd, 0.5, 2.5)
        expect = (0.5 * (0.5 + 1.0) * 0.5 + 0.5 * (1.0 + 2.0) * 1.0
                  + 0.5 * (2.0 + 1.5) * 0.5)
        self.assertAlmostEqual(got, expect, places=9)

    def test_sef_monotone_and_bounds(self):
        fs = 128.0
        x = _sine(fs, 16.0, 10.0, 2.0)
        freqs, psd, _ = welch_psd(x, fs, nperseg=512)
        sef95 = spectral_edge_frequency(freqs, psd, 0.5, 45.0, 0.95)
        sef50 = spectral_edge_frequency(freqs, psd, 0.5, 45.0, 0.50)
        self.assertIsNotNone(sef95)
        self.assertLessEqual(sef50, sef95)
        self.assertTrue(0.5 <= sef95 <= 45.0)

    def test_zero_power_sef_none(self):
        freqs = [0.0, 1.0, 2.0]
        psd = [0.0, 0.0, 0.0]
        self.assertIsNone(spectral_edge_frequency(freqs, psd, 0.0, 2.0, 0.95))


class TestWelchRobustness(unittest.TestCase):
    def test_non_power_of_two_nperseg(self):
        fs = 128.0
        x = _sine(fs, 10.0, 10.0, 1.0)
        freqs, psd, meta = welch_psd(x, fs, nperseg=300)
        self.assertEqual(meta["nfft"], 512)            # padded up from 300
        self.assertEqual(len(freqs), 512 // 2 + 1)
        self.assertTrue(all(math.isfinite(p) for p in psd))

    def test_short_signal_single_segment(self):
        fs = 128.0
        x = _sine(fs, 1.0, 10.0, 1.0)                  # 128 samples
        freqs, psd, meta = welch_psd(x, fs, nperseg=1024)
        self.assertEqual(meta["nperseg"], 128)         # clamped to signal length
        self.assertEqual(meta["n_seg"], 1)

    def test_constant_signal_zero_psd(self):
        freqs, psd, _ = welch_psd([5.0] * 512, 128.0, nperseg=256)
        self.assertTrue(all(abs(p) < 1e-12 for p in psd))


@unittest.skipUnless(HAVE_SCIPY, "scipy/numpy not available")
class TestWelchVsScipy(unittest.TestCase):
    def _check(self, x, fs, nperseg, noverlap):
        freqs, psd, meta = welch_psd(x, fs, nperseg=nperseg, noverlap=noverlap)
        win = _sig.get_window("hann", nperseg, fftbins=True)   # periodic
        f_ref, p_ref = _sig.welch(
            _np.asarray(x), fs=fs, window=win, nperseg=nperseg,
            noverlap=(nperseg // 2 if noverlap is None else noverlap),
            nfft=meta["nfft"], detrend="constant", return_onesided=True,
            scaling="density", average="mean")
        self.assertTrue(_np.allclose(freqs, f_ref, rtol=0, atol=1e-9))
        self.assertTrue(_np.allclose(psd, p_ref, rtol=1e-9, atol=1e-12),
                        f"max diff {float(_np.max(_np.abs(_np.array(psd)-p_ref)))}")

    def test_power_of_two_matches_scipy(self):
        rng = _np.random.RandomState(0)
        x = list(rng.randn(2048) * 10.0 + 3.0 * _np.sin(
            2 * _np.pi * 10.0 * _np.arange(2048) / 256.0))
        self._check(x, 256.0, nperseg=256, noverlap=128)

    def test_padded_nperseg_matches_scipy(self):
        rng = _np.random.RandomState(1)
        x = list(rng.randn(3000))
        # nperseg=500 -> our nfft=512 (padded); tell scipy nfft=512 too
        self._check(x, 128.0, nperseg=500, noverlap=250)

    def test_band_power_matches_scipy_trapz(self):
        rng = _np.random.RandomState(2)
        fs = 256.0
        t = _np.arange(4096) / fs
        x = list(3.0 * _np.sin(2 * _np.pi * 10 * t) + 0.5 * rng.randn(4096))
        freqs, psd, meta = welch_psd(x, fs, nperseg=512)
        f_ref, p_ref = _sig.welch(
            _np.asarray(x), fs=fs,
            window=_sig.get_window("hann", 512, fftbins=True),
            nperseg=512, noverlap=256, nfft=512, detrend="constant",
            scaling="density", average="mean")
        # integrate alpha band with numpy on scipy's PSD
        mask = (f_ref >= 8.0) & (f_ref <= 13.0)
        _trapz = getattr(_np, "trapezoid", getattr(_np, "trapz", None))
        ref_alpha = float(_trapz(p_ref[mask], f_ref[mask]))
        mine_alpha = integrate_psd(list(f_ref), list(p_ref), 8.0, 13.0)
        self.assertAlmostEqual(mine_alpha, ref_alpha, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
