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
    median_bias,
    peak_frequency,
    spectral_edge_frequency,
    spectral_entropy,
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


class TestSpectralEntropy(unittest.TestCase):
    def test_flat_spectrum_is_one(self):
        freqs = [float(k) for k in range(10)]
        psd = [3.0] * 10
        self.assertAlmostEqual(spectral_entropy(freqs, psd, 0.0, 9.0), 1.0, places=12)

    def test_single_bin_is_low(self):
        freqs = [float(k) for k in range(10)]
        psd = [1e-9] * 10
        psd[3] = 100.0
        h = spectral_entropy(freqs, psd, 0.0, 9.0)
        self.assertLess(h, 0.05)

    def test_bounds_and_hand_value(self):
        # two equal bins -> entropy = ln2, normalized by ln2 -> 1.0
        freqs = [1.0, 2.0]
        self.assertAlmostEqual(spectral_entropy(freqs, [5.0, 5.0], 1.0, 2.0), 1.0, 12)
        # unnormalized ln2 for two equal bins
        self.assertAlmostEqual(
            spectral_entropy(freqs, [5.0, 5.0], 1.0, 2.0, normalize=False),
            math.log(2), places=12)

    def test_too_few_bins_or_zero_power_is_none(self):
        self.assertIsNone(spectral_entropy([1.0], [3.0], 0.0, 5.0))
        self.assertIsNone(spectral_entropy([1.0, 2.0], [0.0, 0.0], 0.0, 5.0))

    def test_normalized_by_total_bins_not_positive_only(self):
        # 4 bins in band, two equal positive + two zero: entropy = ln2 / ln4 = 0.5,
        # NOT 1.0 (which the positive-only denominator bug would give).
        freqs = [1.0, 2.0, 3.0, 4.0]
        psd = [5.0, 5.0, 0.0, 0.0]
        self.assertAlmostEqual(spectral_entropy(freqs, psd, 1.0, 4.0), 0.5, places=12)


class TestPeakProminence(unittest.TestCase):
    def test_real_peak_is_prominent(self):
        from eegband.spectral import peak_frequency_prominent
        # sharp interior peak far above the band median
        freqs = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
        psd = [0.5, 0.5, 40.0, 0.5, 0.5, 0.5]
        f, prom = peak_frequency_prominent(freqs, psd, 8.0, 13.0)
        self.assertEqual(f, 10.0)
        self.assertTrue(prom)

    def test_monotonic_slope_not_prominent(self):
        from eegband.spectral import peak_frequency_prominent
        freqs = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
        psd = [10.0, 8.0, 6.0, 4.0, 2.0, 1.0]   # argmax at low edge
        f, prom = peak_frequency_prominent(freqs, psd, 8.0, 13.0)
        self.assertEqual(f, 8.0)
        self.assertFalse(prom)

    def test_shallow_bump_not_prominent(self):
        from eegband.spectral import peak_frequency_prominent
        # interior max but only ~1.3x the median -> below the 3x threshold
        freqs = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
        psd = [1.0, 1.0, 1.3, 1.0, 1.0, 1.0]
        _, prom = peak_frequency_prominent(freqs, psd, 8.0, 13.0)
        self.assertFalse(prom)

    def test_empty_band_none(self):
        from eegband.spectral import peak_frequency_prominent
        self.assertEqual(peak_frequency_prominent([1.0, 2.0], [1.0, 1.0],
                                                  100.0, 200.0), (None, False))


class TestPeakFrequencyEmptyRange(unittest.TestCase):
    def test_no_bins_returns_none(self):
        freqs = [0.0, 1.0, 2.0]
        psd = [1.0, 2.0, 3.0]
        self.assertIsNone(peak_frequency(freqs, psd, 100.0, 200.0))


class TestMedianBias(unittest.TestCase):
    def test_n1_is_one(self):
        self.assertEqual(median_bias(1), 1.0)
        self.assertEqual(median_bias(2), 1.0)

    def test_matches_reference_formula(self):
        # closed-form reference: 1 + Σ_{i=1}^{(n-1)//2} (1/(2i+1) - 1/(2i))
        for n in range(1, 40):
            ref = 1.0
            for i in range(1, (n - 1) // 2 + 1):
                ref += 1.0 / (2 * i + 1) - 1.0 / (2 * i)
            self.assertAlmostEqual(median_bias(n), ref, places=12)


class TestLinearDetrend(unittest.TestCase):
    def test_removes_pure_ramp(self):
        # a pure linear ramp has no spectral content after linear detrend
        fs = 128.0
        x = [0.01 * i for i in range(1280)]
        freqs, psd, _ = welch_psd(x, fs, nperseg=256, detrend="linear")
        self.assertTrue(all(p < 1e-12 for p in psd))

    def test_none_keeps_dc(self):
        # detrend='none' leaves the DC term (huge PSD at 0 Hz) unlike 'constant'
        x = [5.0 + math.sin(2 * math.pi * 10 * i / 128) for i in range(512)]
        _, psd_c, _ = welch_psd(x, 128.0, nperseg=256, detrend="constant")
        _, psd_n, _ = welch_psd(x, 128.0, nperseg=256, detrend="none")
        self.assertGreater(psd_n[0], psd_c[0] + 1.0)

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            welch_psd([1.0, 2.0, 3.0, 4.0], 128.0, detrend="quadratic")
        with self.assertRaises(ValueError):
            welch_psd([1.0, 2.0, 3.0, 4.0], 128.0, average="mode")


@unittest.skipUnless(HAVE_SCIPY, "scipy/numpy not available")
class TestMedianAndDetrendVsScipy(unittest.TestCase):
    def _mk(self, seed):
        rng = _np.random.RandomState(seed)
        t = _np.arange(4096) / 128.0
        return list(3.0 * _np.sin(2 * _np.pi * 10 * t) + rng.randn(4096)
                    + 0.02 * _np.arange(4096))

    def test_median_matches_scipy(self):
        x = self._mk(7)
        freqs, psd, meta = welch_psd(x, 128.0, nperseg=256, noverlap=128,
                                     average="median")
        _, p_ref = _sig.welch(
            _np.asarray(x), fs=128.0,
            window=_sig.get_window("hann", 256, fftbins=True),
            nperseg=256, noverlap=128, nfft=256, detrend="constant",
            scaling="density", average="median")
        self.assertTrue(_np.allclose(psd, p_ref, rtol=1e-9, atol=1e-12),
                        float(_np.max(_np.abs(_np.array(psd) - p_ref))))

    def test_linear_detrend_matches_scipy(self):
        for average in ("mean", "median"):
            x = self._mk(11)
            freqs, psd, meta = welch_psd(x, 128.0, nperseg=256, noverlap=128,
                                         detrend="linear", average=average)
            _, p_ref = _sig.welch(
                _np.asarray(x), fs=128.0,
                window=_sig.get_window("hann", 256, fftbins=True),
                nperseg=256, noverlap=128, nfft=256, detrend="linear",
                scaling="density", average=average)
            self.assertTrue(_np.allclose(psd, p_ref, rtol=1e-8, atol=1e-12),
                            f"{average}: "
                            f"{float(_np.max(_np.abs(_np.array(psd) - p_ref)))}")

    def test_entropy_matches_manual(self):
        x = self._mk(3)
        freqs, psd, _ = welch_psd(x, 128.0, nperseg=256)
        h = spectral_entropy(freqs, psd, 0.5, 45.0)
        # manual over the same bins
        ps = _np.array([p for f, p in zip(freqs, psd) if 0.5 <= f <= 45.0 and p > 0])
        q = ps / ps.sum()
        ref = float(-(q * _np.log(q)).sum() / _np.log(len(ps)))
        self.assertAlmostEqual(h, ref, places=12)


if __name__ == "__main__":
    unittest.main()
