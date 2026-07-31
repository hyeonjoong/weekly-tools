"""Mains line-noise detection and PSD-domain removal.

The key cross-check is against **first principles**: a pure sinusoid of amplitude A
carries A²/2 µV² of power, so injecting ``A·sin(2πf₀t)`` into a recording must make
the reported ``excess_uv2`` at f₀ equal A²/2, and notching it must remove exactly that
much from whichever band contains f₀.
"""

import math
import random
import unittest

from eegband import linenoise as ln
from eegband.analyze import analyze
from eegband.report import render_text, to_dict
from eegband.spectral import welch_psd

FS = 256.0
BANDS = [("delta", 0.5, 4.0), ("theta", 4.0, 8.0), ("alpha", 8.0, 13.0),
         ("beta", 13.0, 30.0), ("gamma", 30.0, 80.0)]


def synth(seconds=40.0, fs=FS, mains=0.0, mains_f=60.0, alpha_amp=20.0,
          noise=8.0, seed=1):
    rng = random.Random(seed)
    n = int(seconds * fs)
    out = []
    for i in range(n):
        t = i / fs
        v = alpha_amp * math.sin(2 * math.pi * 10.0 * t)
        if noise:
            v += noise * rng.gauss(0.0, 1.0)
        if mains:
            v += mains * math.sin(2 * math.pi * mains_f * t + 0.3)
        out.append(v)
    return out


class TestAliasFreq(unittest.TestCase):
    def test_below_nyquist_is_unchanged(self):
        self.assertEqual(ln.alias_freq(60.0, 256.0), (60.0, False))

    def test_folds_above_nyquist(self):
        # 60 Hz sampled at 100 Hz is indistinguishable from 40 Hz.
        f, aliased = ln.alias_freq(60.0, 100.0)
        self.assertTrue(aliased)
        self.assertAlmostEqual(f, 40.0, places=9)

    def test_folds_third_harmonic(self):
        # 180 Hz at fs=256: 180 > 128, so it folds to 256 - 180 = 76.
        f, aliased = ln.alias_freq(180.0, 256.0)
        self.assertTrue(aliased)
        self.assertAlmostEqual(f, 76.0, places=9)

    def test_multiple_wraps(self):
        # 300 Hz at fs=100: 300 mod 100 = 0 -> DC image.
        f, aliased = ln.alias_freq(300.0, 100.0)
        self.assertTrue(aliased)
        self.assertAlmostEqual(f, 0.0, places=9)

    def test_rejects_bad_fs(self):
        with self.assertRaises(ValueError):
            ln.alias_freq(50.0, 0.0)


class TestDetection(unittest.TestCase):
    def test_clean_signal_reports_no_line_noise(self):
        res = analyze(synth(mains=0.0), fs=FS, bands=BANDS)
        lnr = res.overall.line_noise
        self.assertIsNotNone(lnr)
        self.assertFalse(lnr.detected)
        self.assertLess(lnr.max_ratio, ln.RATIO_THRESHOLD)

    def test_detects_60hz_and_picks_it_over_50(self):
        res = analyze(synth(mains=30.0, mains_f=60.0), fs=FS, bands=BANDS)
        lnr = res.overall.line_noise
        self.assertTrue(lnr.detected)
        self.assertEqual(lnr.f0, 60.0)
        self.assertEqual(lnr.source, "auto")

    def test_detects_50hz_and_picks_it_over_60(self):
        res = analyze(synth(mains=30.0, mains_f=50.0), fs=FS, bands=BANDS)
        self.assertEqual(res.overall.line_noise.f0, 50.0)

    def test_excess_power_matches_sinusoid_amplitude(self):
        # A pure tone of amplitude A has power A^2/2. That is what a notch removes.
        amp = 30.0
        res = analyze(synth(mains=amp, mains_f=60.0), fs=FS, bands=BANDS)
        peak = res.overall.line_noise.peaks[0]
        self.assertEqual(peak.order, 1)
        self.assertAlmostEqual(peak.excess_uv2, amp ** 2 / 2.0,
                               delta=0.05 * amp ** 2 / 2.0)

    def test_user_frequency_overrides_detection(self):
        res = analyze(synth(mains=30.0, mains_f=60.0), fs=FS, bands=BANDS,
                      line_freq=50.0)
        lnr = res.overall.line_noise
        self.assertEqual(lnr.f0, 50.0)
        self.assertEqual(lnr.source, "user")
        self.assertFalse(lnr.detected)     # nothing is actually at 50 Hz

    def test_off_skips_the_check_entirely(self):
        res = analyze(synth(mains=30.0), fs=FS, bands=BANDS, line_freq=None)
        self.assertIsNone(res.overall.line_noise)
        self.assertIsNone(res.line_freq_mode)

    def test_low_nyquist_reports_alias(self):
        # 60 Hz mains sampled at 100 Hz shows up at 40 Hz, inside gamma.
        fs = 100.0
        sig = synth(seconds=40.0, fs=fs, mains=30.0, mains_f=60.0)
        res = analyze(sig, fs=fs, bands=[("delta", 0.5, 4.0), ("alpha", 8.0, 13.0),
                                         ("gamma", 30.0, 49.0)],
                      line_freq=60.0)
        lnr = res.overall.line_noise
        self.assertTrue(lnr.detected)
        peak = lnr.detected_peaks()[0]
        self.assertTrue(peak.aliased)
        self.assertAlmostEqual(peak.freq_hz, 40.0, delta=0.5)
        self.assertTrue(any("ALIAS" in w or "aliased" in w for w in res.warnings))


class TestBackgroundAndMeasure(unittest.TestCase):
    def test_background_needs_enough_shoulder_bins(self):
        freqs = [0.0, 1.0, 2.0]
        psd = [1.0, 1.0, 1.0]
        self.assertIsNone(ln.local_background(freqs, psd, 1.0, 1.0))

    def test_background_is_a_median_not_a_mean(self):
        """One huge shoulder bin must not inflate the background.

        The shoulder for (f=10, bw=1, shoulder=4) is 1 < |g-10| <= 4, i.e. the bins
        {6,7,8,12,13,14} — the outlier MUST be inside that set or the test proves
        nothing (a mean would give the same answer as a median).
        """
        freqs = [float(i) for i in range(0, 21)]
        psd = [1.0] * 21
        psd[13] = 1e6                      # inside the shoulder window
        self.assertIn(13.0, [g for g in freqs if 1.0 < abs(g - 10.0) <= 4.0])
        bg = ln.local_background(freqs, psd, 10.0, 1.0)
        self.assertAlmostEqual(bg, 1.0)    # median: unmoved.  mean would be ~1.7e5
        self.assertLess(bg, 100.0)

    def test_zero_background_gives_no_ratio_instead_of_infinity(self):
        freqs = [float(i) for i in range(0, 21)]
        psd = [0.0] * 21
        psd[10] = 5.0
        ratio, bg, peak, excess = ln.measure_peak(freqs, psd, 10.0, 1.0)
        self.assertIsNone(ratio)
        self.assertIsNone(excess)
        self.assertEqual(peak, 5.0)

    def test_rejects_non_positive_bandwidth(self):
        with self.assertRaises(ValueError):
            ln.local_background([0.0, 1.0], [1.0, 1.0], 0.5, 0.0)
        with self.assertRaises(ValueError):
            ln.analyse_line_noise([0.0, 1.0], [1.0, 1.0], 10.0, bw=-1.0)


class TestNotch(unittest.TestCase):
    def test_notch_removes_the_tone_power_from_gamma(self):
        amp = 30.0
        clean = analyze(synth(mains=0.0), fs=FS, bands=BANDS)
        dirty = analyze(synth(mains=amp, mains_f=60.0), fs=FS, bands=BANDS)
        fixed = analyze(synth(mains=amp, mains_f=60.0), fs=FS, bands=BANDS,
                        notch=True)

        def gamma(r):
            return next(b.absolute for b in r.overall.band_powers
                        if b.name == "gamma")

        # The tone adds ~A^2/2 to gamma; notching brings it back near the clean value.
        self.assertAlmostEqual(gamma(dirty) - gamma(clean), amp ** 2 / 2.0,
                               delta=0.08 * amp ** 2 / 2.0)
        self.assertLess(abs(gamma(fixed) - gamma(clean)), 0.25 * gamma(clean))
        self.assertTrue(fixed.overall.line_noise.removed)
        self.assertTrue(fixed.notch)

    def test_notch_leaves_other_bands_alone(self):
        clean = analyze(synth(mains=0.0), fs=FS, bands=BANDS)
        fixed = analyze(synth(mains=30.0, mains_f=60.0), fs=FS, bands=BANDS,
                        notch=True)
        for name in ("delta", "theta", "alpha", "beta"):
            a = next(b.absolute for b in clean.overall.band_powers if b.name == name)
            b_ = next(b.absolute for b in fixed.overall.band_powers if b.name == name)
            self.assertAlmostEqual(a, b_, delta=max(1e-9, 0.01 * max(a, 1e-9)))

    def test_notch_interpolates_log_linearly(self):
        freqs = [0.0, 1.0, 2.0, 3.0, 4.0]
        psd = [1.0, 10.0, 1000.0, 1000.0, 10000.0]
        # notch at 2.0 with bw 0.5 replaces only the bin at 2.0
        out, n = ln.notch_psd(freqs, psd, [2.0], 0.5)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(out[2], 100.0)      # geometric mean of 10 and 1000
        self.assertEqual(out[:2], psd[:2])
        self.assertEqual(out[3:], psd[3:])

    def test_notch_at_the_spectrum_edge_uses_the_one_available_side(self):
        freqs = [0.0, 1.0, 2.0, 3.0]
        psd = [5.0, 7.0, 9.0, 11.0]
        out, n = ln.notch_psd(freqs, psd, [3.0], 0.5)
        self.assertEqual(n, 1)
        self.assertEqual(out[3], 9.0)

    def test_notch_of_nothing_is_a_no_op(self):
        freqs, psd = [0.0, 1.0], [1.0, 2.0]
        out, n = ln.notch_psd(freqs, psd, [], 1.0)
        self.assertEqual((out, n), ([1.0, 2.0], 0))

    def test_notch_handles_zero_valued_anchors(self):
        freqs = [0.0, 1.0, 2.0, 3.0, 4.0]
        psd = [1.0, 0.0, 500.0, 0.0, 1.0]
        out, n = ln.notch_psd(freqs, psd, [2.0], 0.5)
        self.assertEqual(n, 1)
        self.assertTrue(math.isfinite(out[2]))
        self.assertAlmostEqual(out[2], 0.0)

    def test_notch_requires_a_line_frequency(self):
        with self.assertRaises(ValueError):
            analyze(synth(), fs=FS, bands=BANDS, line_freq=None, notch=True)

    def test_epochs_use_the_same_fundamental_as_the_recording(self):
        res = analyze(synth(seconds=60.0, mains=30.0, mains_f=60.0), fs=FS,
                      bands=BANDS, epoch_sec=20.0, notch=True)
        self.assertEqual(res.overall.line_noise.f0, 60.0)
        for ep in res.epochs:
            self.assertIsNotNone(ep.spectrum.line_noise)
            self.assertEqual(ep.spectrum.line_noise.f0, 60.0)
            # The epoch inherits the fundamental as a NUMBER but keeps the recording's
            # "auto" semantics: labelling it "user" would switch off the aliased-
            # harmonic guard and let the epoch claim removals never performed.
            self.assertEqual(ep.spectrum.line_noise.source, "auto")


class TestReportingAndValidation(unittest.TestCase):
    def test_text_report_names_the_frequency_and_the_band(self):
        res = analyze(synth(mains=30.0, mains_f=60.0), fs=FS, bands=BANDS)
        txt = render_text(res)
        self.assertIn("mains line noise", txt)
        self.assertIn("60 Hz", txt)
        self.assertIn("gamma", txt)

    def test_json_carries_the_harmonic_table(self):
        res = analyze(synth(mains=30.0, mains_f=60.0), fs=FS, bands=BANDS)
        d = to_dict(res)
        block = d["overall"]["line_noise"]
        self.assertTrue(block["detected"])
        self.assertEqual(block["fundamental_hz"], 60.0)
        self.assertGreaterEqual(len(block["harmonics"]), 1)
        self.assertIn("gamma", block["excess_uv2_by_band"])
        self.assertEqual(d["provenance"]["line_freq_mode"], "auto")
        self.assertFalse(d["provenance"]["notch_applied"])

    def test_json_is_none_when_disabled(self):
        res = analyze(synth(), fs=FS, bands=BANDS, line_freq=None)
        self.assertIsNone(to_dict(res)["overall"]["line_noise"])

    def test_invalid_line_options_raise(self):
        with self.assertRaises(ValueError):
            analyze(synth(), fs=FS, bands=BANDS, line_freq=-5.0)
        with self.assertRaises(ValueError):
            analyze(synth(), fs=FS, bands=BANDS, line_bw=0.0)
        with self.assertRaises(ValueError):
            analyze(synth(), fs=FS, bands=BANDS, line_bw=float("nan"))

    def test_constant_signal_does_not_crash_the_detector(self):
        res = analyze([3.0] * 1024, fs=FS, bands=BANDS)
        # Nothing to measure, but the run must complete and say so honestly.
        lnr = res.overall.line_noise
        self.assertTrue(lnr is None or not lnr.detected)
        self.assertIn("전원잡음", render_text(res))

    def test_harmonics_skip_windows_that_run_off_the_spectrum(self):
        # fs = 130 -> Nyquist 65; 60 Hz fits, 120 Hz aliases to 10 Hz, 180 -> 50.
        freqs, psd, _ = welch_psd(synth(seconds=20.0, fs=130.0, mains=5.0,
                                        mains_f=60.0), 130.0, nperseg=512)
        rep = ln.analyse_line_noise(freqs, psd, 130.0, f0=60.0)
        self.assertIsNotNone(rep)
        for p in rep.peaks:
            self.assertGreater(p.freq_hz - rep.bandwidth, 0.0)
            self.assertLess(p.freq_hz + rep.bandwidth, 65.0)


class TestExcessAttribution(unittest.TestCase):
    def test_excess_goes_to_the_band_containing_the_peak_not_prorated(self):
        """Mains power sits AT the peak, not spread evenly over the ±bw window.

        Prorating across the window told a band that merely clips the skirt of the
        window that most of its power was electrical, when nothing was removed from it.
        """
        rep = ln.LineNoiseReport(f0=60.0, source="user", bandwidth=1.0,
                                 nyquist_hz=128.0)
        rep.peaks = [ln.HarmonicPeak(order=1, nominal_hz=60.0, freq_hz=60.0,
                                     aliased=False, ratio=10.0, background=1.0,
                                     peak_psd=10.0, excess_uv2=100.0, detected=True,
                                     significant=True)]
        # 60 Hz is the upper edge of the first band and inside the second: charged
        # wholly to the band that contains it, never split.
        self.assertAlmostEqual(rep.excess_in(30.0, 60.0), 0.0)
        self.assertAlmostEqual(rep.excess_in(60.0, 90.0), 100.0)
        self.assertAlmostEqual(rep.excess_in(59.0, 61.0), 100.0)
        self.assertAlmostEqual(rep.excess_in(0.0, 45.0), 0.0)

    def test_a_band_clipping_only_the_window_skirt_is_charged_nothing(self):
        """The g1/g2 split that exposed the prorating bug: edge at 60.5, peak at 60.

        The old code charged g2 86% of its power to mains even though notching removed
        nothing from it. The peak belongs wholly to whichever band contains 60 Hz.
        """
        rep = ln.LineNoiseReport(f0=60.0, source="user", bandwidth=1.0,
                                 nyquist_hz=128.0)
        rep.peaks = [ln.HarmonicPeak(order=1, nominal_hz=60.0, freq_hz=60.0,
                                     aliased=False, ratio=10.0, background=1.0,
                                     peak_psd=10.0, excess_uv2=100.0, detected=True,
                                     significant=True)]
        self.assertAlmostEqual(rep.excess_in(30.0, 60.5), 100.0)  # contains the peak
        self.assertAlmostEqual(rep.excess_in(60.5, 90.0), 0.0)    # only the skirt

    def test_reported_share_matches_the_power_actually_removed(self):
        """End-to-end: the 'share removed' claim must survive a with/without diff."""
        bands = [("delta", 0.5, 4.0), ("g1", 30.0, 60.5), ("g2", 60.5, 90.0)]
        sig = synth(seconds=40.0, fs=FS, mains=10.0, mains_f=60.0, noise=0.0,
                    alpha_amp=3.0)
        raw = analyze(sig, fs=FS, bands=bands)
        fixed = analyze(sig, fs=FS, bands=bands, notch=True)

        def band(r, n):
            return next(b.absolute for b in r.overall.band_powers if b.name == n)
        removed_g1 = band(raw, "g1") - band(fixed, "g1")
        removed_g2 = band(raw, "g2") - band(fixed, "g2")
        self.assertGreater(removed_g1, 40.0)          # ~A^2/2 = 50
        self.assertLess(abs(removed_g2), 0.5)         # nothing came out of g2
        lnr = raw.overall.line_noise
        self.assertAlmostEqual(lnr.excess_in(60.5, 90.0), 0.0)
        self.assertGreater(lnr.excess_in(30.0, 60.5), 40.0)

    def test_undetected_harmonics_contribute_nothing(self):
        rep = ln.LineNoiseReport(f0=60.0, source="user", bandwidth=1.0,
                                 nyquist_hz=128.0)
        rep.peaks = [ln.HarmonicPeak(order=1, nominal_hz=60.0, freq_hz=60.0,
                                     aliased=False, ratio=1.2, background=1.0,
                                     peak_psd=1.2, excess_uv2=100.0, detected=False,
                                     significant=False)]
        self.assertEqual(rep.excess_in(30.0, 90.0), 0.0)
        self.assertEqual(rep.targets(), [])




class TestNotAssessableMessages(unittest.TestCase):
    """When nothing can be measured, say WHICH reason — not a plausible wrong one."""

    def test_constant_signal_blames_the_missing_background(self):
        res = analyze([3.0] * 1024, fs=FS, bands=BANDS)
        self.assertIsNone(res.overall.line_noise)
        txt = render_text(res)
        self.assertIn("확인 불가", txt)
        self.assertIn("zero-power or constant", txt)
        self.assertNotIn("Nyquist", txt.split("[1]")[0])

    def test_low_sample_rate_still_reports_over_the_aliased_positions(self):
        """fs=80 puts both candidates above Nyquist — but silence is the worst answer.

        A report must still be built over the folded positions (nothing flagged), so
        the "suspected mains alias" warning can fire. Reporting nothing would present
        a folded 50/60 Hz peak as brain activity.
        """
        sig = synth(seconds=20.0, fs=80.0, mains=0.0)
        res = analyze(sig, fs=80.0, bands=[("delta", 0.5, 4.0), ("alpha", 8.0, 13.0)])
        lnr = res.overall.line_noise
        self.assertIsNotNone(lnr)
        self.assertTrue(all(p.aliased for p in lnr.peaks))
        self.assertFalse(lnr.detected)
        self.assertEqual(lnr.targets(), [])

    def test_aliased_mains_below_nyquist_is_never_silent(self):
        # fs=100 folds 60 Hz mains onto 40 Hz — dead centre of gamma.
        rng = random.Random(0)
        fs = 100.0
        x = [rng.gauss(0, 10) + 15.0 * math.sin(2 * math.pi * 60.0 * i / fs)
             for i in range(6000)]
        res = analyze(x, fs=fs)
        lnr = res.overall.line_noise
        self.assertIsNotNone(lnr)
        self.assertEqual([p.freq_hz for p in lnr.suspect_aliases()], [40.0])
        self.assertFalse(lnr.detected)          # never flagged on our own initiative
        hits = [w for w in res.warnings if "ALIAS" in w]
        self.assertTrue(hits)
        self.assertIn("gamma", hits[0])


class TestAliasFalsePositiveGuard(unittest.TestCase):
    """An aliased harmonic must never be auto-flagged as line noise.

    At fs=80 Hz the 3rd harmonic of 50 Hz mains folds onto exactly 10 Hz — where the
    alpha rhythm lives. Auto-detection must NOT call a genuine 10 Hz rhythm "96.7%
    electrical", and --notch must not delete it. Only an explicit --line-freq may.
    """
    FS = 80.0
    BANDS = [("delta", 0.5, 4.0), ("alpha", 8.0, 13.0), ("gamma", 30.0, 39.0)]

    def _sig(self):
        return synth(seconds=20.0, fs=self.FS, mains=0.0, alpha_amp=25.0)

    def test_auto_does_not_flag_the_alpha_rhythm(self):
        res = analyze(self._sig(), fs=self.FS, bands=self.BANDS)
        lnr = res.overall.line_noise
        self.assertIsNotNone(lnr)
        self.assertFalse(lnr.detected)
        self.assertEqual(lnr.targets(), [])

    def test_auto_notch_leaves_the_alpha_rhythm_intact(self):
        plain = analyze(self._sig(), fs=self.FS, bands=self.BANDS)
        notched = analyze(self._sig(), fs=self.FS, bands=self.BANDS, notch=True)

        def alpha(r):
            return next(b.absolute for b in r.overall.band_powers
                        if b.name == "alpha")
        self.assertAlmostEqual(alpha(plain), alpha(notched), places=9)

    def test_the_suspect_alias_is_still_reported_as_a_warning(self):
        res = analyze(self._sig(), fs=self.FS, bands=self.BANDS, line_freq=50.0)
        # With --line-freq given, the user has asserted the mains frequency.
        lnr = res.overall.line_noise
        suspects = [p for p in lnr.peaks if p.aliased and p.ratio
                    and p.ratio >= lnr.threshold]
        self.assertTrue(suspects)
        # Auto mode instead files them as suspects and warns.
        auto = analyze(self._sig(), fs=self.FS, bands=self.BANDS)
        self.assertIsNotNone(auto.overall.line_noise)
        self.assertTrue(auto.overall.line_noise.suspect_aliases())
        self.assertTrue(any("ALIAS" in w for w in auto.warnings))

    def test_explicit_line_freq_may_flag_an_alias(self):
        res = analyze(self._sig(), fs=self.FS, bands=self.BANDS, line_freq=50.0)
        lnr = res.overall.line_noise
        self.assertEqual(lnr.source, "user")
        self.assertTrue(lnr.detected)
        self.assertTrue(all(p.aliased for p in lnr.detected_peaks()))

    def test_auto_never_flags_when_no_fundamental_is_in_band(self):
        # fs = 80 -> Nyquist 40; neither 50 nor 60 Hz is in band. A report is still
        # produced (over the folded positions) but NOTHING may be flagged or notched.
        freqs, psd, _ = welch_psd(self._sig(), self.FS, nperseg=512)
        rep = ln.analyse_line_noise(freqs, psd, self.FS)
        self.assertIsNotNone(rep)
        self.assertFalse(rep.detected)
        self.assertEqual(rep.targets(), [])
        self.assertTrue(all(p.aliased for p in rep.peaks))

    def test_a_real_in_band_peak_is_still_caught_at_the_same_fs(self):
        # Sanity: the guard must not disable detection when the mains IS in band.
        sig = synth(seconds=20.0, fs=200.0, mains=25.0, mains_f=60.0)
        res = analyze(sig, fs=200.0, bands=[("delta", 0.5, 4.0),
                                            ("gamma", 30.0, 90.0)])
        self.assertTrue(res.overall.line_noise.detected)
        self.assertFalse(res.overall.line_noise.detected_peaks()[0].aliased)


class TestSuspectAliasReporting(unittest.TestCase):
    """A loud aliased harmonic of an in-band fundamental is reported, never removed."""

    def _sig(self):
        rng = random.Random(2)
        fs = 200.0
        # A real 20 Hz beta rhythm sits exactly where 60 Hz mains' 3rd harmonic
        # (180 Hz) aliases at fs = 200 Hz, plus a genuine but modest 60 Hz mains.
        return [30.0 * math.sin(2 * math.pi * 20.0 * i / fs)
                + 2.0 * math.sin(2 * math.pi * 60.0 * i / fs)
                + 5.0 * rng.gauss(0.0, 1.0) for i in range(int(fs * 30))]

    BANDS = [("delta", 0.5, 4.0), ("beta", 13.0, 30.0), ("gamma", 30.0, 90.0)]

    def test_alias_is_a_suspect_not_a_detection(self):
        res = analyze(self._sig(), fs=200.0, bands=self.BANDS)
        lnr = res.overall.line_noise
        self.assertEqual(lnr.f0, 60.0)
        self.assertTrue(lnr.detected)                       # the 60 Hz fundamental
        self.assertNotIn(20.0, lnr.targets())               # but NOT the 20 Hz alias
        self.assertEqual([p.freq_hz for p in lnr.suspect_aliases()], [20.0])

    def test_notch_does_not_touch_the_beta_rhythm(self):
        plain = analyze(self._sig(), fs=200.0, bands=self.BANDS)
        notched = analyze(self._sig(), fs=200.0, bands=self.BANDS, notch=True)

        def beta(r):
            return next(b.absolute for b in r.overall.band_powers if b.name == "beta")
        self.assertAlmostEqual(beta(plain), beta(notched), places=9)

    def test_the_report_and_warnings_name_it(self):
        res = analyze(self._sig(), fs=200.0, bands=self.BANDS)
        txt = render_text(res)
        self.assertIn("에일리어싱 의심", txt)
        self.assertIn("suspected mains alias", txt)
        self.assertTrue(any("ALIAS" in w and "beta" in w for w in res.warnings))

    def test_explicit_line_freq_does_remove_it(self):
        res = analyze(self._sig(), fs=200.0, bands=self.BANDS, line_freq=60.0,
                      notch=True)
        self.assertIn(20.0, res.overall.line_noise.targets())


class TestPerEpochConsistency(unittest.TestCase):
    """Epochs must inherit the recording's removal decision, not re-decide it.

    A short epoch averages few Welch segments, so a peak/background ratio of 3 is
    crossed by chance in a large fraction of CLEAN epochs. Re-deciding per epoch would
    notch a random subset and make epochs incomparable with each other.
    """
    FS = 256.0
    BANDS = [("delta", 0.5, 4.0), ("alpha", 8.0, 13.0), ("gamma", 30.0, 90.0)]

    def test_clean_short_epochs_are_never_notched(self):
        rng = random.Random(4)
        n = int(self.FS * 120)
        sig = [rng.gauss(0.0, 10.0) for _ in range(n)]   # white noise, no mains
        plain = analyze(sig, fs=self.FS, bands=self.BANDS, epoch_sec=10.0)
        notched = analyze(sig, fs=self.FS, bands=self.BANDS, epoch_sec=10.0,
                          notch=True)
        self.assertFalse(plain.overall.line_noise.detected)
        # No epoch may differ: the recording-level decision was "nothing to remove".
        for a, b in zip(plain.epochs, notched.epochs):
            self.assertAlmostEqual(a.spectrum.total_power, b.spectrum.total_power,
                                   places=12)
            for pa, pb in zip(a.spectrum.band_powers, b.spectrum.band_powers):
                self.assertAlmostEqual(pa.absolute, pb.absolute, places=12)

    def test_real_mains_is_removed_from_every_epoch(self):
        sig = synth(seconds=120.0, fs=self.FS, mains=25.0, mains_f=60.0)
        plain = analyze(sig, fs=self.FS, bands=self.BANDS, epoch_sec=10.0)
        notched = analyze(sig, fs=self.FS, bands=self.BANDS, epoch_sec=10.0,
                          notch=True)
        self.assertTrue(plain.overall.line_noise.detected)

        def gamma(ep):
            return next(b.absolute for b in ep.spectrum.band_powers
                        if b.name == "gamma")
        for a, b in zip(plain.epochs, notched.epochs):
            self.assertLess(gamma(b), 0.5 * gamma(a))


if __name__ == "__main__":
    unittest.main()
