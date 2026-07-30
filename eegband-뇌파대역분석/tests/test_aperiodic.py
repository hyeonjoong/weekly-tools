"""Aperiodic (1/f) parameterization: exponent recovery, band correction, peaks.

The estimator is validated from first principles: spectra with a *known* exponent are
synthesised analytically and from real Welch PSDs of coloured noise, and the recovered
exponent/offset are compared to the truth. Peak detection is checked for both
sensitivity (a real rhythm) and specificity (peakless 1/f noise).
"""

import math
import random
import unittest

from eegband.aperiodic import (
    FIT_MODES,
    fit_aperiodic,
    flattened_log_spectrum,
    flattened_peak,
    oscillatory_power,
    residual_psd,
)
from eegband.spectral import welch_psd

BANDS = [("delta", 0.5, 4.0), ("theta", 4.0, 8.0), ("alpha", 8.0, 13.0),
         ("beta", 13.0, 30.0), ("gamma", 30.0, 45.0)]


def analytic_psd(exponent, offset, df=0.25, f_max=45.0, bump=None, noise=0.0,
                 seed=0):
    """PSD = 10^offset * f^-exponent (+ optional Gaussian bump, lognormal noise)."""
    rng = random.Random(seed)
    freqs = [k * df for k in range(int(round(f_max / df)) + 1)]
    psd = []
    for f in freqs:
        if f <= 0:
            psd.append(0.0)
            continue
        v = 10.0 ** (offset - exponent * math.log10(f))
        if bump is not None:
            amp, ctr, width = bump
            v += amp * math.exp(-0.5 * ((f - ctr) / width) ** 2)
        if noise:
            v *= math.exp(rng.gauss(0.0, noise))
        psd.append(v)
    return freqs, psd


def colored_noise(fs, dur, exponent, seed, amp=200.0, f_max=45.0):
    """Sum-of-sinusoids noise whose expected PSD is proportional to f^-exponent."""
    rng = random.Random(seed)
    n = int(round(fs * dur))
    df = 1.0 / dur
    comps = [(k * df, (k * df) ** (-exponent / 2.0), rng.uniform(0, 2 * math.pi))
             for k in range(1, int(f_max / df) + 1)]
    norm = math.sqrt(math.fsum(a * a for _, a, _ in comps))
    out = []
    for i in range(n):
        t = i / fs
        out.append(amp * math.fsum(a * math.cos(2 * math.pi * f * t + ph)
                                   for f, a, ph in comps) / norm)
    return out


class TestExactRecovery(unittest.TestCase):
    """A noiseless power law must be recovered to machine precision."""

    def test_noiseless_exact(self):
        for exponent, offset in ((0.0, 1.0), (1.0, 2.0), (1.73, -0.5), (3.0, 4.0)):
            freqs, psd = analytic_psd(exponent, offset)
            for mode in FIT_MODES:
                fit = fit_aperiodic(freqs, psd, 0.5, 45.0, mode=mode)
                self.assertIsNotNone(fit)
                self.assertAlmostEqual(fit.exponent, exponent, places=9,
                                       msg=f"{mode} exponent")
                self.assertAlmostEqual(fit.offset, offset, places=9)
                self.assertAlmostEqual(fit.r2, 1.0, places=9)
                self.assertAlmostEqual(fit.psd_at(1.0), 10.0 ** offset, places=6)

    def test_psd_at_matches_model(self):
        freqs, psd = analytic_psd(1.5, 2.0)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        for f in (0.5, 1.0, 7.3, 45.0):
            self.assertAlmostEqual(fit.psd_at(f),
                                   10.0 ** (2.0 - 1.5 * math.log10(f)), places=6)
            self.assertAlmostEqual(fit.log_psd_at(f), math.log10(fit.psd_at(f)),
                                   places=9)

    def test_robust_beats_ols_with_a_peak(self):
        """A strong alpha bump biases plain OLS; the robust trim resists it."""
        freqs, psd = analytic_psd(1.6, 2.0, bump=(30.0, 10.3, 1.2), noise=0.05,
                                  seed=7)
        rob = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="robust")
        ols = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="ols")
        self.assertLess(abs(rob.exponent - 1.6), abs(ols.exponent - 1.6))
        self.assertLess(abs(rob.exponent - 1.6), 0.05)
        self.assertLess(rob.n_used, rob.n_total)      # peak bins were dropped
        self.assertEqual(ols.n_used, ols.n_total)     # ols keeps everything
        self.assertGreater(rob.r2, ols.r2)


class TestWelchRecovery(unittest.TestCase):
    """Exponent recovery from real Welch PSDs of synthesised coloured noise."""

    def test_recovers_exponent_from_welch(self):
        fs = 128.0
        for exponent in (0.5, 1.0, 2.0):
            errs = []
            for seed in range(3):
                x = colored_noise(fs, 16.0, exponent, seed + 11 * int(exponent * 2))
                freqs, psd, _ = welch_psd(x, fs, nperseg=512)
                fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
                errs.append(abs(fit.exponent - exponent))
            self.assertLess(max(errs), 0.15, f"exponent {exponent}: {errs}")

    def test_amplitude_scaling_moves_offset_not_exponent(self):
        """Scaling the signal by k scales the PSD by k²: offset += 2log10 k, χ same."""
        fs = 128.0
        x = colored_noise(fs, 16.0, 1.4, 3)
        f1, p1, _ = welch_psd(x, fs, nperseg=512)
        f2, p2, _ = welch_psd([10.0 * v for v in x], fs, nperseg=512)
        a = fit_aperiodic(f1, p1, 0.5, 45.0)
        b = fit_aperiodic(f2, p2, 0.5, 45.0)
        self.assertAlmostEqual(a.exponent, b.exponent, places=9)
        self.assertAlmostEqual(b.offset - a.offset, 2.0, places=9)


class TestDegenerate(unittest.TestCase):
    def test_too_few_bins_returns_none(self):
        self.assertIsNone(fit_aperiodic([1.0, 2.0], [1.0, 0.5], 0.5, 45.0))
        freqs, psd = analytic_psd(1.0, 1.0)
        self.assertIsNone(fit_aperiodic(freqs, psd, 10.0, 10.4))  # < 3 bins in range

    def test_zero_and_nonfinite_bins_are_skipped(self):
        freqs, psd = analytic_psd(1.5, 2.0)
        psd = list(psd)
        psd[4] = 0.0
        psd[5] = float("nan")
        psd[6] = float("-inf")
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        self.assertIsNotNone(fit)
        # excluded: f=0, the 0.25 Hz bin below the 0.5 Hz fit edge, and 3 bad bins
        self.assertEqual(fit.n_total, len(freqs) - 2 - 3)
        self.assertAlmostEqual(fit.exponent, 1.5, places=6)

    def test_constant_zero_psd_returns_none(self):
        freqs = [k * 0.25 for k in range(100)]
        self.assertIsNone(fit_aperiodic(freqs, [0.0] * 100, 0.5, 20.0))

    def test_flat_white_psd_gives_zero_exponent(self):
        freqs = [k * 0.25 for k in range(1, 100)]
        fit = fit_aperiodic(freqs, [3.0] * len(freqs), 0.5, 20.0)
        self.assertAlmostEqual(fit.exponent, 0.0, places=9)
        self.assertAlmostEqual(fit.offset, math.log10(3.0), places=9)
        self.assertAlmostEqual(fit.r2, 1.0, places=9)

    def test_reversed_and_invalid_arguments(self):
        freqs, psd = analytic_psd(1.0, 1.0)
        a = fit_aperiodic(freqs, psd, 45.0, 0.5)   # reversed range is tolerated
        b = fit_aperiodic(freqs, psd, 0.5, 45.0)
        self.assertAlmostEqual(a.exponent, b.exponent, places=12)
        with self.assertRaises(ValueError):
            fit_aperiodic(freqs, psd, 0.5, 45.0, mode="nope")
        with self.assertRaises(ValueError):
            fit_aperiodic(freqs, psd, 0.5, 45.0, max_iter=-1)

    def test_edge_tolerance_includes_bins_from_inferred_fs(self):
        """A bin a hair below the requested edge (inferred fs) must still be used."""
        df = 45.0 / 180 * (1 - 1e-12)
        freqs = [k * df for k in range(181)]
        psd = [0.0 if f <= 0 else 10 ** (2 - 1.5 * math.log10(f)) for f in freqs]
        lo = freqs[2] * (1 + 1e-12)
        fit = fit_aperiodic(freqs, psd, lo, 45.0)
        self.assertEqual(fit.fit_lo, freqs[2])


class TestOscillatoryPower(unittest.TestCase):
    def test_pure_power_law_has_no_oscillatory_power(self):
        freqs, psd = analytic_psd(1.5, 2.0)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        rf, rp = residual_psd(freqs, psd, fit)
        self.assertTrue(all(v >= 0.0 for v in rp))
        for _, lo, hi in BANDS:
            self.assertLess(oscillatory_power(rf, rp, lo, hi), 1e-6)

    def test_bump_power_lands_in_its_own_band(self):
        freqs, psd = analytic_psd(1.5, 2.0, bump=(50.0, 10.0, 0.8))
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        rf, rp = residual_psd(freqs, psd, fit)
        by = {name: oscillatory_power(rf, rp, lo, hi) for name, lo, hi in BANDS}
        self.assertGreater(by["alpha"], 50.0)
        for name in ("delta", "theta", "beta", "gamma"):
            self.assertLess(by[name], 0.5 * by["alpha"])
        # analytic integral of the Gaussian bump inside the band: amp*width*sqrt(2pi)
        self.assertAlmostEqual(by["alpha"], 50.0 * 0.8 * math.sqrt(2 * math.pi),
                               delta=8.0)

    def test_band_outside_fit_range_is_none(self):
        freqs, psd = analytic_psd(1.5, 2.0)
        fit = fit_aperiodic(freqs, psd, 8.0, 13.0)
        rf, rp = residual_psd(freqs, psd, fit)
        self.assertIsNone(oscillatory_power(rf, rp, 30.0, 45.0))
        self.assertIsNone(oscillatory_power([], [], 8.0, 13.0))

    def test_residual_never_exceeds_total_band_power(self):
        fs = 128.0
        x = colored_noise(fs, 16.0, 1.4, 5)
        x = [v + 15.0 * math.sin(2 * math.pi * 10.0 * i / fs)
             for i, v in enumerate(x)]
        freqs, psd, _ = welch_psd(x, fs, nperseg=512)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        rf, rp = residual_psd(freqs, psd, fit)
        from eegband.spectral import integrate_psd
        for _, lo, hi in BANDS:
            osc = oscillatory_power(rf, rp, lo, hi)
            total = integrate_psd(freqs, psd, lo, hi)
            self.assertLessEqual(osc, total + 1e-9)


class TestFlattenedPeak(unittest.TestCase):
    def test_detects_real_alpha_rhythm(self):
        fs = 128.0
        x = colored_noise(fs, 16.0, 1.4, 21)
        x = [v + 60.0 * math.sin(2 * math.pi * 10.0 * i / fs)
             for i, v in enumerate(x)]
        freqs, psd, _ = welch_psd(x, fs, nperseg=512)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        f, h, prom = flattened_peak(ff, fv, 8.0, 13.0)
        self.assertTrue(prom)
        self.assertAlmostEqual(f, 10.0, delta=0.3)
        self.assertGreater(h, 0.5)

    def test_specificity_on_peakless_noise(self):
        """Peakless 1/f noise must almost never yield a 'prominent' band peak."""
        fs = 128.0
        false_pos = 0
        total = 0
        for seed in range(6):
            x = colored_noise(fs, 16.0, 1.4, 500 + seed)
            freqs, psd, _ = welch_psd(x, fs, nperseg=512)
            fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
            ff, fv = flattened_log_spectrum(freqs, psd, fit)
            for _, lo, hi in BANDS:
                total += 1
                if flattened_peak(ff, fv, lo, hi)[2]:
                    false_pos += 1
        self.assertLessEqual(false_pos, 1, f"{false_pos}/{total} false positives")

    def test_single_bin_spike_is_not_a_peak(self):
        """A one-bin spike (mains line/glitch) has no width and must be rejected."""
        freqs, psd = analytic_psd(1.0, 2.0)
        idx = freqs.index(10.0)
        psd = list(psd)
        psd[idx] *= 100.0
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        f, h, prom = flattened_peak(ff, fv, 8.0, 13.0)
        self.assertEqual(f, 10.0)
        self.assertGreater(h, 1.0)
        self.assertFalse(prom)

    def test_edge_pinned_peak_is_not_prominent(self):
        freqs, psd = analytic_psd(1.0, 2.0, bump=(200.0, 8.0, 0.5))
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        f, _, prom = flattened_peak(ff, fv, 8.0, 13.0)
        self.assertEqual(f, 8.0)
        self.assertFalse(prom)      # sits exactly on the band edge

    def test_empty_band_returns_none(self):
        freqs, psd = analytic_psd(1.0, 2.0)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        self.assertEqual(flattened_peak(ff, fv, 60.0, 70.0), (None, None, False))

    def test_flattened_spectrum_is_log_ratio(self):
        freqs, psd = analytic_psd(1.5, 2.0, bump=(10.0, 20.0, 1.0))
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        for f, v in zip(ff, fv):
            i = freqs.index(f)
            self.assertAlmostEqual(v, math.log10(psd[i] / fit.psd_at(f)), places=9)


class TestAgainstNumpy(unittest.TestCase):
    """Cross-check the log-log OLS against numpy.polyfit when numpy is available."""

    def test_ols_matches_polyfit(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        freqs, psd = analytic_psd(1.4, 1.7, noise=0.2, seed=3)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="ols")
        xs = np.log10([f for f in freqs if 0.5 <= f <= 45.0])
        ys = np.log10([p for f, p in zip(freqs, psd) if 0.5 <= f <= 45.0])
        slope, intercept = np.polyfit(xs, ys, 1)
        self.assertAlmostEqual(fit.exponent, -slope, places=10)
        self.assertAlmostEqual(fit.offset, intercept, places=10)
        # R² against numpy's correlation coefficient
        r = np.corrcoef(xs, ys)[0, 1]
        self.assertAlmostEqual(fit.r2, r ** 2, places=10)

    def test_slope_se_matches_scipy_linregress(self):
        try:
            from scipy import stats as sps
        except ImportError:
            self.skipTest("scipy not installed")
        freqs, psd = analytic_psd(1.2, 2.2, noise=0.15, seed=9)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="ols")
        xs = [math.log10(f) for f in freqs if 0.5 <= f <= 45.0]
        ys = [math.log10(p) for f, p in zip(freqs, psd) if 0.5 <= f <= 45.0]
        lr = sps.linregress(xs, ys)
        self.assertAlmostEqual(fit.exponent_se, lr.stderr, places=10)


if __name__ == "__main__":
    unittest.main()
