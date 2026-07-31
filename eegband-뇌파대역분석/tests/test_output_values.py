"""Every exported NUMBER must equal the value it claims to be.

Round-6 mutation testing showed the new `--csv-summary` / `--json` layer was tested
only for the *presence* of column names: swapping `p` with `q`, `ci_lo` with `ci_hi`,
or `mean_baseline` with `mean_post`, and even reporting the uncorrected p as the FDR
q, all passed the suite. Header-name assertions are not value assertions — these are.

Each test here pins an exported cell against the in-memory ``ContrastResult`` /
``LineNoiseReport`` it is derived from, so a transposition anywhere in the row builder
fails immediately.
"""

import csv
import io as _io
import json
import math
import random
import unittest

from eegband import linenoise as ln
from eegband.analyze import analyze
from eegband.report import (
    _BASELINE_KEYS,
    render_csv_summary,
    render_text,
    to_dict,
)
from eegband.stats import student_t_sf, t_quantile, welch_ttest

FS = 128.0
# gamma widened past the 50 Hz mains: with the default 30-45 Hz gamma the line peak
# falls outside every reported band and its per-band excess is correctly 0.
WIDE_BANDS = [("delta", 0.5, 4.0), ("theta", 4.0, 8.0), ("alpha", 8.0, 13.0),
              ("beta", 13.0, 30.0), ("gamma", 30.0, 63.0)]


def pd_signal(seconds=240.0, fs=FS, switch=120.0, mains=0.0, seed=3):
    rng = random.Random(seed)
    out = []
    for i in range(int(seconds * fs)):
        t = i / fs
        amp = 15.0 if t < switch else 30.0
        v = (amp * math.sin(2 * math.pi * 1.5 * t + 0.7)
             + 10.0 * math.sin(2 * math.pi * 10.0 * t) + 5.0 * rng.gauss(0.0, 1.0))
        if mains:
            v += mains * math.sin(2 * math.pi * 50.0 * t)
        out.append(v)
    return out


def _rows(text):
    return list(csv.reader(_io.StringIO(text)))


class TestCsvSummaryValues(unittest.TestCase):
    """Each `*_base_*` cell must equal the corresponding ContrastResult field."""

    @classmethod
    def setUpClass(cls):
        cls.res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cls.rows = _rows(render_csv_summary([cls.res], comment=False))
        cls.head, cls.row = cls.rows[0], cls.rows[1]

    def _cell(self, name):
        return self.row[self.head.index(name)]

    def test_every_baseline_field_matches_the_contrast_object(self):
        # The mapping the row builder must implement, spelled out independently.
        want = {
            "mean": lambda c: c.mean_a,
            "post_mean": lambda c: c.mean_b,
            "delta": lambda c: c.diff,
            "pct_change": lambda c: c.pct_change,
            "ci_lo": lambda c: c.ci_lo,
            "ci_hi": lambda c: c.ci_hi,
            "hedges_g": lambda c: c.hedges_g,
            "p": lambda c: c.p,
            "q_fdr": lambda c: c.q,
        }
        self.assertEqual(set(want), set(_BASELINE_KEYS))
        checked = 0
        for key, cr in self.res.baseline_contrasts.items():
            for stat, get in want.items():
                col = f"{key}_base_{stat}"
                if col not in self.head:
                    continue
                cell = self._cell(col)
                expect = get(cr)
                if cell == "":
                    self.assertFalse(math.isfinite(expect))
                    continue
                self.assertAlmostEqual(float(cell), expect, places=9,
                                       msg=f"{col} mismatch")
                checked += 1
        self.assertGreater(checked, 100)

    def test_p_and_q_are_not_interchanged(self):
        """q >= p always; a swap would break that for at least one endpoint."""
        differing = 0
        for key, cr in self.res.baseline_contrasts.items():
            p = float(self._cell(f"{key}_base_p"))
            q = float(self._cell(f"{key}_base_q_fdr"))
            self.assertGreaterEqual(q + 1e-15, p)
            if q > p + 1e-15:
                differing += 1
        self.assertGreater(differing, 0, "q identical to p everywhere — suspicious")

    def test_baseline_and_post_means_are_not_interchanged(self):
        cr = self.res.baseline_contrasts["swa_absolute_uv2"]
        base = float(self._cell("swa_absolute_uv2_base_mean"))
        post = float(self._cell("swa_absolute_uv2_base_post_mean"))
        self.assertLess(base, post)               # SWA rose after the switch
        self.assertAlmostEqual(post - base, cr.diff, places=9)
        self.assertAlmostEqual(base, cr.mean_a, places=9)

    def test_ci_limits_are_not_interchanged(self):
        for key in self.res.baseline_contrasts:
            lo = self._cell(f"{key}_base_ci_lo")
            hi = self._cell(f"{key}_base_ci_hi")
            if lo == "" or hi == "":
                continue
            self.assertLessEqual(float(lo), float(hi))

    def test_line_noise_columns_carry_real_values(self):
        # gamma must reach past 50 Hz or the peak overlaps no band and every
        # excess_uv2 is legitimately 0 (see the default-bands note in 사용법.md).
        res = analyze(pd_signal(mains=20.0), fs=FS, epoch_sec=30.0,
                      bands=WIDE_BANDS)
        rows = _rows(render_csv_summary([res], comment=False))
        head, row = rows[0], rows[1]
        lnr = res.overall.line_noise
        self.assertTrue(lnr.detected)
        self.assertEqual(row[head.index("line_detected")], "1")
        self.assertEqual(row[head.index("line_notched")], "0")
        self.assertAlmostEqual(float(row[head.index("line_freq_hz")]), lnr.f0)
        self.assertAlmostEqual(float(row[head.index("line_max_ratio")]),
                               lnr.max_ratio, places=9)
        excess = float(row[head.index("line_excess_uv2")])
        self.assertGreater(excess, 0.0)
        self.assertAlmostEqual(
            excess, sum(lnr.excess_in(lo, hi) for _, lo, hi in res.bands), places=9)

    def test_line_notched_flag_flips_with_notch(self):
        res = analyze(pd_signal(mains=20.0), fs=FS,
                      bands=[("delta", 0.5, 4.0), ("gamma", 30.0, 63.0)], notch=True)
        rows = _rows(render_csv_summary([res], comment=False))
        head, row = rows[0], rows[1]
        self.assertEqual(row[head.index("line_notched")], "1")
        self.assertEqual(row[head.index("line_detected")], "1")


class TestJsonValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = analyze(pd_signal(mains=20.0), fs=FS, epoch_sec=30.0,
                          baseline_sec=120.0, bands=WIDE_BANDS)
        cls.d = to_dict(cls.res)

    def test_contrast_block_matches_the_objects_field_by_field(self):
        eps = self.d["baseline_contrast"]["endpoints"]
        for key, cr in self.res.baseline_contrasts.items():
            got = eps[key]
            self.assertAlmostEqual(got["mean_baseline"], cr.mean_a, places=12)
            self.assertAlmostEqual(got["mean_post"], cr.mean_b, places=12)
            self.assertAlmostEqual(got["diff"], cr.diff, places=12)
            self.assertAlmostEqual(got["ci_lo"], cr.ci_lo, places=12)
            self.assertAlmostEqual(got["ci_hi"], cr.ci_hi, places=12)
            self.assertAlmostEqual(got["p_two_sided"], cr.p, places=15)
            self.assertAlmostEqual(got["q_bh_fdr"], cr.q, places=15)
            self.assertAlmostEqual(got["hedges_g"], cr.hedges_g, places=12)
            self.assertEqual(got["n_baseline"], cr.n_a)
            self.assertEqual(got["n_post"], cr.n_b)

    def test_q_is_the_corrected_value_not_a_copy_of_p(self):
        eps = self.d["baseline_contrast"]["endpoints"]
        m = self.d["baseline_contrast"]["bh_fdr_family_size"]
        self.assertGreater(m, 1)
        # At least one endpoint must have q strictly greater than p, or no correction
        # was applied at all.
        self.assertTrue(any(e["q_bh_fdr"] > e["p_two_sided"] + 1e-15
                            for e in eps.values()))
        for e in eps.values():
            self.assertGreaterEqual(e["q_bh_fdr"] + 1e-15, e["p_two_sided"])

    def test_line_noise_block_matches_the_report(self):
        blk = self.d["overall"]["line_noise"]
        lnr = self.res.overall.line_noise
        self.assertEqual(blk["fundamental_hz"], lnr.f0)
        self.assertEqual(blk["detected"], lnr.detected)
        self.assertEqual(len(blk["harmonics"]), len(lnr.peaks))
        for got, p in zip(blk["harmonics"], lnr.peaks):
            self.assertEqual(got["order"], p.order)
            self.assertAlmostEqual(got["freq_hz"], p.freq_hz, places=12)
            self.assertEqual(got["aliased"], p.aliased)
            self.assertAlmostEqual(got["ratio"], p.ratio, places=12)
            self.assertAlmostEqual(got["excess_uv2"], p.excess_uv2, places=12)
        for name, lo, hi in self.res.bands:
            self.assertAlmostEqual(blk["excess_uv2_by_band"][name],
                                   lnr.excess_in(lo, hi), places=12)
        # The detected band must carry a non-zero share, or the mapping is dead.
        self.assertGreater(max(blk["excess_uv2_by_band"].values()), 0.0)


class TestTextReportUnits(unittest.TestCase):
    def test_relative_endpoints_are_printed_as_percent(self):
        """A '%'-labelled cell must be the fraction x100, not the raw fraction."""
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        cr = res.baseline_contrasts["swa_relative"]
        self.assertLess(cr.mean_a, 1.0)                 # stored as a fraction
        text = render_text(res)
        sect = text[text.index("[7] 기저 대비 변화"):]
        line = next(l for l in sect.splitlines()
                    if l.strip().startswith("relative SWA"))
        printed = float(line.split()[2])
        self.assertAlmostEqual(printed, cr.mean_a * 100.0, places=2)
        self.assertGreater(printed, 1.0)


class TestAutocorrIsActuallyRequested(unittest.TestCase):
    """`_baseline_contrast` must ASK for the AR(1) adjustment, not just support it."""

    def _drifting(self):
        """A steadily drifting recording: consecutive epochs are strongly correlated."""
        rng = random.Random(21)
        out = []
        n = int(FS * 300)
        for i in range(n):
            t = i / FS
            amp = 10.0 + 20.0 * (t / 300.0)      # monotone ramp -> rho1 >> 0
            out.append(amp * math.sin(2 * math.pi * 1.5 * t)
                       + 8.0 * math.sin(2 * math.pi * 10.0 * t)
                       + 2.0 * rng.gauss(0.0, 1.0))
        return out

    def test_contrast_uses_the_adjusted_path(self):
        res = analyze(self._drifting(), fs=FS, epoch_sec=15.0, baseline_sec=120.0)
        base = [ep for ep in res.epochs if ep.end_sec <= 120.0 + 1e-9]
        post = [ep for ep in res.epochs if ep.end_sec > 120.0 + 1e-9]
        checked = 0
        for key, got in res.baseline_contrasts.items():
            attr = {"total_power_uv2": lambda sp: sp.total_power,
                    "swa_absolute_uv2": lambda sp: sp.swa_abs,
                    "swa_relative": lambda sp: sp.swa_rel}.get(key)
            if attr is None:
                continue
            a = [attr(ep.spectrum) for ep in base]
            b = [attr(ep.spectrum) for ep in post]
            adj = welch_ttest(a, b, adjust_autocorr=True)
            raw = welch_ttest(a, b, adjust_autocorr=False)
            if abs(adj.p - raw.p) <= 1e-12:
                continue                  # not distinguishable on this endpoint
            self.assertAlmostEqual(got.p, adj.p, places=12)
            self.assertAlmostEqual(got.se, adj.se, places=12)
            self.assertAlmostEqual(got.df, adj.df, places=12)
            self.assertNotAlmostEqual(got.p, raw.p, places=12)
            checked += 1
        self.assertGreater(checked, 0, "no endpoint distinguished the two paths")

    def test_reported_n_eff_is_below_n_when_epochs_are_correlated(self):
        res = analyze(self._drifting(), fs=FS, epoch_sec=15.0, baseline_sec=120.0)
        adjusted = [c for c in res.baseline_contrasts.values() if c.adjusted]
        self.assertTrue(adjusted)
        for c in adjusted:
            self.assertTrue(c.n_eff_a < c.n_a or c.n_eff_b < c.n_b)


class TestTwoSidedPWithoutScipy(unittest.TestCase):
    """Pin p as TWO-sided using only the standard library.

    Halving the p-value (dropping the factor 2) doubles the false-positive rate and
    propagates into BH q and the significance star; scipy was the only thing catching
    it, so a scipy-less environment had no guarantee at all.
    """

    def test_p_is_exactly_twice_the_upper_tail(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0]
        b = [5.0, 6.0, 7.0, 6.0, 8.0, 9.0, 7.0]
        cr = welch_ttest(a, b, adjust_autocorr=False)
        self.assertAlmostEqual(cr.p, 2.0 * student_t_sf(abs(cr.t), cr.df), places=15)

    def test_p_matches_a_published_two_sample_t_result(self):
        """Equal n and equal variance -> Welch reduces to the pooled t-test.

        x = 1..5, y = 6..10: both SD = sqrt(2.5), diff = 5, df = 8,
        t = 5 / sqrt(2*2.5/5) = 5 / sqrt(1) = 5.0 exactly.
        """
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [6.0, 7.0, 8.0, 9.0, 10.0]
        cr = welch_ttest(x, y, adjust_autocorr=False)
        self.assertAlmostEqual(cr.t, 5.0, places=12)
        self.assertAlmostEqual(cr.df, 8.0, places=12)
        # Published two-sided p for t=5, df=8 is 0.001053 (4 s.f.).
        self.assertAlmostEqual(cr.p, 0.001053, places=6)
        self.assertLess(cr.p, 0.5)

    def test_the_95_ci_excludes_zero_exactly_when_p_below_005(self):
        rng = random.Random(17)
        for _ in range(40):
            n = rng.randint(3, 12)
            a = [rng.gauss(0.0, 1.0) for _ in range(n)]
            b = [rng.gauss(rng.uniform(0.0, 1.5), 1.0) for _ in range(n)]
            cr = welch_ttest(a, b, adjust_autocorr=False)
            if not math.isfinite(cr.p):
                continue
            excludes_zero = cr.ci_lo > 0.0 or cr.ci_hi < 0.0
            self.assertEqual(excludes_zero, cr.p < 0.05)


class TestHedgesGUnequalN(unittest.TestCase):
    def test_pooled_variance_is_df_weighted_not_a_plain_average(self):
        """With unequal n the two formulas differ; equal-n tests cannot tell them apart."""
        a = [1.0, 2.0, 3.0]
        b = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0]
        cr = welch_ttest(a, b, adjust_autocorr=False)
        na, nb = len(a), len(b)
        ma, mb = sum(a) / na, sum(b) / nb
        var_a = sum((v - ma) ** 2 for v in a) / (na - 1)
        var_b = sum((v - mb) ** 2 for v in b) / (nb - 1)
        df_pool = na + nb - 2
        pooled = ((na - 1) * var_a + (nb - 1) * var_b) / df_pool
        naive = 0.5 * (var_a + var_b)
        self.assertNotAlmostEqual(pooled, naive, places=3)   # formulas must differ
        g = ((cr.mean_b - cr.mean_a) / math.sqrt(pooled)
             * (1.0 - 3.0 / (4.0 * df_pool - 1.0)))
        self.assertAlmostEqual(cr.hedges_g, g, places=12)

    def test_degenerate_pooled_variance_gives_nan_g(self):
        cr = welch_ttest([2.0] * 5, [3.0, 3.0, 3.0, 3.0, 3.1])
        self.assertIsNotNone(cr)
        self.assertTrue(math.isfinite(cr.hedges_g))
        # Both constant -> no contrast at all.
        self.assertIsNone(welch_ttest([2.0] * 5, [3.0] * 5))


class TestLineNoiseInternals(unittest.TestCase):
    """Pin the measurement internals a 5%-tolerance amplitude check cannot see."""

    def test_excess_subtracts_the_background(self):
        # Flat background of 2.0 µV²/Hz with a 1-bin spike; excess must be the spike
        # ABOVE the background, not the raw integral of the window.
        freqs = [float(i) * 0.5 for i in range(60)]     # 0..29.5 Hz, df = 0.5
        psd = [2.0] * 60
        peak_i = freqs.index(10.0)
        psd[peak_i] = 20.0
        ratio, bg, peak, excess = ln.measure_peak(freqs, psd, 10.0, 1.0)
        self.assertAlmostEqual(bg, 2.0)
        self.assertAlmostEqual(peak, 20.0)
        self.assertAlmostEqual(ratio, 10.0)
        raw_integral = 2.0 * 2.0 + (20.0 - 2.0) * 0.5   # bg over 2 Hz + spike area
        self.assertAlmostEqual(excess, raw_integral - bg * 2.0, places=9)
        self.assertLess(excess, raw_integral)           # background WAS subtracted

    def test_window_edge_is_inclusive(self):
        """A bin sitting exactly at ±bw is inside the window (real fs/nfft spacing)."""
        freqs = [8.0, 9.0, 10.0, 11.0, 12.0]
        psd = [1.0, 5.0, 9.0, 5.0, 1.0]
        out, n = ln.notch_psd(freqs, psd, [10.0], 1.0)
        self.assertEqual(n, 3)                          # 9, 10 and 11 all replaced
        self.assertEqual(out[0], 1.0)
        self.assertEqual(out[4], 1.0)
        for i in (1, 2, 3):
            self.assertAlmostEqual(out[i], 1.0, places=9)

    def test_default_bandwidth_is_one_hz(self):
        """--line-bw defaults to ±1 Hz; widening it deletes more spectrum."""
        self.assertEqual(ln.DEFAULT_BW, 1.0)
        freqs = [float(i) * 0.25 for i in range(200)]
        psd = [1.0] * 200
        _, n1 = ln.notch_psd(freqs, psd, [10.0], ln.DEFAULT_BW)
        self.assertEqual(n1, 9)                         # 9.0..11.0 at df=0.25
        _, n2 = ln.notch_psd(freqs, psd, [10.0], 2.0)
        self.assertEqual(n2, 17)                        # 8.0..12.0

    def test_background_shoulder_span_is_four_bandwidths(self):
        """Bins beyond 4*bw must NOT enter the background estimate."""
        freqs = [float(i) for i in range(0, 31)]
        psd = [1.0] * 31
        psd[15] = 1e6                                   # |g-10| = 5 > 4*bw -> excluded
        self.assertAlmostEqual(ln.local_background(freqs, psd, 10.0, 1.0), 1.0)
        psd = [1.0] * 31
        for g in (6, 7, 8, 12, 13, 14):                 # the whole shoulder set
            psd[g] = 9.0
        self.assertAlmostEqual(ln.local_background(freqs, psd, 10.0, 1.0), 9.0)

    def test_notch_at_the_low_edge_uses_the_right_hand_anchor(self):
        freqs = [0.0, 1.0, 2.0, 3.0]
        psd = [11.0, 9.0, 7.0, 5.0]
        out, n = ln.notch_psd(freqs, psd, [0.0], 0.5)
        self.assertEqual(n, 1)
        self.assertEqual(out[0], 9.0)
        self.assertEqual(out[1:], [9.0, 7.0, 5.0])


class TestDegenerateContrastPaths(unittest.TestCase):
    def test_no_contrastable_endpoint_warns_instead_of_silence(self):
        """A perfectly constant recording has nothing to contrast."""
        res = analyze([5.0] * int(FS * 240), fs=FS, epoch_sec=30.0,
                      baseline_sec=120.0)
        self.assertEqual(res.baseline_contrasts, {})
        self.assertTrue(any("no endpoint could be contrasted" in w
                            for w in res.warnings))

    def test_json_stays_strict_on_that_recording(self):
        res = analyze([5.0] * int(FS * 240), fs=FS, epoch_sec=30.0,
                      baseline_sec=120.0)
        json.dumps(to_dict(res), allow_nan=False)


if __name__ == "__main__":
    unittest.main()


class TestRound6EdgeCaseFixes(unittest.TestCase):
    """Regressions for the defects the round-6 edge-case panel found."""

    def _line60(self, fs=128.0, seconds=40.0):
        return [25.0 * math.sin(2 * math.pi * 60.0 * i / fs)
                + 10.0 * math.sin(2 * math.pi * 10.0 * i / fs)
                for i in range(int(fs * seconds))]

    # --- 1. huge amplitudes crashed with OverflowError inside math.fsum ----------
    def test_astronomical_amplitudes_do_not_crash(self):
        sig = [1e150 * math.sin(2 * math.pi * 10.0 * i / FS) + 1e148 * (i % 7)
               for i in range(int(FS * 240))]
        res = analyze(sig, fs=FS, epoch_sec=30.0, baseline_sec=60.0)
        self.assertTrue(res.warnings)
        json.dumps(to_dict(res), allow_nan=False)

    def test_overflowed_variance_yields_no_contrast_rather_than_a_wrong_one(self):
        from eegband.stats import lag1_autocorr, summary_stats
        big = [1e300, 1.1e300, 0.9e300, 1.05e300]
        self.assertIsNone(lag1_autocorr(big))
        self.assertFalse(math.isfinite(summary_stats(big)["sd"]))
        self.assertIsNone(welch_ttest(big, [2e300, 2.1e300, 1.9e300]))

    # --- 2/3. --notch that removes nothing must say so, and say WHY -------------
    def test_notch_noop_is_reported_with_the_real_reason(self):
        wide = analyze(self._line60(), fs=128.0, notch=True, line_bw=20.0)
        msg = [w for w in wide.warnings if "removed NOTHING" in w]
        self.assertTrue(msg)
        self.assertIn("fits inside", msg[0])
        txt = render_text(wide)
        self.assertNotIn("zero-power or constant signal", txt)

    def test_bandwidth_below_bin_spacing_is_named_as_the_cause(self):
        narrow = analyze(self._line60(), fs=128.0, notch=True, line_bw=0.001)
        msg = [w for w in narrow.warnings if "removed NOTHING" in w]
        self.assertTrue(msg)
        self.assertIn("below the frequency resolution", msg[0])
        self.assertIn("bin spacing", render_text(narrow))

    def test_a_real_notch_does_not_emit_the_noop_warning(self):
        ok = analyze(self._line60(), fs=128.0, notch=True,
                     bands=[("delta", 0.5, 4.0), ("gamma", 30.0, 63.0)])
        self.assertTrue(ok.overall.line_noise.removed)
        self.assertFalse([w for w in ok.warnings if "removed NOTHING" in w])

    # --- 4. epoch blocks claimed removals that never happened -------------------
    def test_epoch_line_noise_agrees_with_the_recording(self):
        fs = 128.0
        sig = [30.0 * math.sin(2 * math.pi * 8.0 * i / fs)
               + 25.0 * math.sin(2 * math.pi * 60.0 * i / fs)
               + 10.0 * math.sin(2 * math.pi * 2.0 * i / fs)
               for i in range(int(fs * 60))]
        bands = [("delta", 0.5, 4.0), ("theta", 4.0, 8.5), ("alpha", 8.5, 13.0)]
        res = analyze(sig, fs=fs, bands=bands, epoch_sec=10.0, notch=True)
        top = res.overall.line_noise
        self.assertEqual(top.source, "auto")
        for ep in res.epochs:
            lnr = ep.spectrum.line_noise
            self.assertEqual(lnr.source, top.source)
            # The 8 Hz alias of the 2nd harmonic must not be flagged in an epoch when
            # the recording spared it — and nothing at 8 Hz was ever notched.
            for p in lnr.peaks:
                if abs(p.freq_hz - 8.0) < 0.6:
                    self.assertFalse(p.detected)
            self.assertAlmostEqual(lnr.excess_in(4.0, 8.5), 0.0)

    # --- 6. a ratio test alone called floating-point round-off "mains noise" ----
    def test_numerically_pure_signal_is_not_called_mains_noise(self):
        fs = 128.0
        pure = [10.0 * math.sin(2 * math.pi * 10.0 * i / fs)
                for i in range(int(fs * 40))]
        res = analyze(pure, fs=fs, notch=True)
        lnr = res.overall.line_noise
        self.assertFalse(lnr.detected)
        self.assertEqual(lnr.targets(), [])
        self.assertEqual(lnr.suspect_aliases(), [])
        # ...and the alpha power it does carry is untouched.
        alpha = next(b.absolute for b in res.overall.band_powers if b.name == "alpha")
        self.assertAlmostEqual(alpha, 50.0, delta=1.0)

    def test_the_power_floor_does_not_suppress_real_mains(self):
        res = analyze(self._line60(), fs=128.0,
                      bands=[("delta", 0.5, 4.0), ("gamma", 30.0, 63.0)])
        self.assertTrue(res.overall.line_noise.detected)

    # --- 7. percent change is meaningless off the ratio scale -------------------
    def test_log_and_exponent_endpoints_report_no_percent_change(self):
        res = analyze(pd_signal(), fs=FS, epoch_sec=30.0, baseline_sec=120.0)
        for key in ("swa_absolute_log10", "aperiodic_exponent", "spectral_entropy"):
            self.assertTrue(math.isnan(res.baseline_contrasts[key].pct_change),
                            f"{key} should have no percent change")
        # ...while genuine ratio-scale endpoints still do.
        self.assertTrue(
            math.isfinite(res.baseline_contrasts["swa_absolute_uv2"].pct_change))
        self.assertIn("n/a", render_text(res))

    # --- 8. a band named like a core endpoint produced duplicate columns --------
    def test_band_named_swa_does_not_duplicate_columns(self):
        bands = [("swa", 0.5, 4.0), ("delta", 0.5, 4.0), ("alpha", 8.0, 13.0)]
        res = analyze(pd_signal(), fs=FS, bands=bands, epoch_sec=30.0,
                      baseline_sec=120.0)
        head = _rows(render_csv_summary([res], comment=False))[0]
        self.assertEqual(len(head), len(set(head)), "duplicate column names")
        rows = _rows(render_csv_summary([res], comment=False))
        self.assertEqual(len(rows[1]), len(head))

    # --- provenance must distinguish differently-cleaned exports ----------------
    def test_provenance_records_notch_and_baseline(self):
        a = analyze(self._line60(), fs=128.0, epoch_sec=10.0,
                    bands=[("delta", 0.5, 4.0), ("gamma", 30.0, 63.0)])
        b = analyze(self._line60(), fs=128.0, epoch_sec=10.0, notch=True,
                    baseline_sec=20.0,
                    bands=[("delta", 0.5, 4.0), ("gamma", 30.0, 63.0)])
        pa = render_csv_summary([a], comment=True).splitlines()[0]
        pb = render_csv_summary([b], comment=True).splitlines()[0]
        self.assertNotEqual(pa, pb)
        self.assertIn("notch=0", pa)
        self.assertIn("notch=1", pb)
        self.assertIn("baseline=none", pa)
        self.assertIn("baseline=20s", pb)
        self.assertIn("epoch=10s", pa)
