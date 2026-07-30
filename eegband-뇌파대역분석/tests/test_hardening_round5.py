"""Regressions for the round-5 review findings, and mutation-killing tests.

Every test here corresponds to a specific defect found by the adversarial panels or to
a mutation that survived the panel's 68-mutation campaign. Each one is written so that
reverting the fix (or re-applying the mutation) fails it. Grouped by the module the
defect lived in.
"""

import csv
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from eegband.analyze import analyze, resolve_fs, signal_quality
from eegband.aperiodic import (
    fit_aperiodic,
    flattened_log_spectrum,
    flattened_peak,
    half_range_exponents,
    oscillatory_power,
    residual_psd,
)
from eegband.cli import main
from eegband.dataio import load_signal, load_signals
from eegband.edf import read_edf_channel, read_edf_info
from eegband.report import (
    render_csv,
    render_csv_summary,
    render_text,
    to_dict,
)
from eegband.spectral import DEFAULT_BANDS, integrate_psd, welch_psd
from eegband.stats import (
    MAX_EXACT_TREND_N,
    effective_n,
    summary_stats,
    t_crit,
    theil_sen_slope,
    trend,
)

from edf_fixtures import _fixed, _num, sine, write_edf

FS = 128.0


def _sine(fs, dur, f, amp, phase=0.0):
    n = int(round(fs * dur))
    return [amp * math.sin(2 * math.pi * f * k / fs + phase) for k in range(n)]


def _pink(fs, dur, chi, seed, rms=30.0, f_max=45.0):
    """1/f noise with RANDOM (Rayleigh) amplitudes, so the periodogram is genuinely
    chi-square distributed — a sum of sinusoids with fixed amplitudes has almost no
    spectral variability and cannot measure a false-positive rate."""
    import random
    rng = random.Random(seed)
    n = int(round(fs * dur))
    df = 1.0 / dur
    comps = []
    for k in range(1, int(f_max / df) + 1):
        f = k * df
        amp = f ** (-chi / 2.0) * math.sqrt(-2.0 * math.log(rng.random()))
        comps.append((f, amp, rng.uniform(0, 2 * math.pi)))
    norm = math.sqrt(math.fsum(a * a for _, a, _ in comps) / 2.0)
    return [rms * math.fsum(a * math.cos(2 * math.pi * f * (i / fs) + ph)
                            for f, a, ph in comps) / norm for i in range(n)]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _rows(csv_text):
    lines = [ln for ln in csv_text.splitlines() if ln and not ln.startswith("#")]
    return list(csv.reader(lines))


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="eegband-r5-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text, encoding="utf-8"):
        p = self.path(name)
        with open(p, "wb") as fh:
            fh.write(text.encode(encoding))
        return p

    def write_bytes(self, name, blob):
        p = self.path(name)
        with open(p, "wb") as fh:
            fh.write(blob)
        return p


# ---------------------------------------------------------------- dataio ----------

class TestBlankLinesAndParsing(TmpDir):
    def test_interior_blank_line_is_a_gap_not_a_deleted_sample(self):
        """A blank line in a single-column CSV is a MISSING SAMPLE. Dropping it shifted
        every later time stamp and reported 'interpolated = 0'."""
        rows = [str(v) for v in range(1, 11)]
        rows[4] = ""
        p = self.write("gap.csv", "eeg_uv\n" + "\n".join(rows) + "\n")
        sig = load_signal(p)
        self.assertEqual(sig.n, 10)
        self.assertEqual(sig.n_filled, 1)
        self.assertEqual(sig.values[4], 5.0)          # interpolated between 4 and 6
        self.assertTrue(any("interpolated" in w for w in sig.warnings))

    def test_trailing_blank_lines_are_not_samples(self):
        p = self.write("tail.csv", "eeg_uv\n1\n2\n3\n\n\n\n")
        sig = load_signal(p)
        self.assertEqual(sig.values, [1.0, 2.0, 3.0])
        self.assertEqual(sig.n_filled, 0)

    def test_cr_only_line_endings(self):
        p = self.write_bytes("cr.csv", b"eeg_uv\r1.0\r2.0\r3.0\r")
        self.assertEqual(load_signal(p).values, [1.0, 2.0, 3.0])

    def test_binary_and_gzip_inputs_exit_cleanly(self):
        import gzip
        binary = self.write_bytes("x.bin", bytes(range(256)) * 200)
        rc, _, err = _run([binary])
        self.assertEqual(rc, 2)
        self.assertIn("입력 오류", err)
        gz = self.path("y.csv.gz")
        with gzip.open(gz, "wb") as fh:
            fh.write(b"eeg_uv\n1\n2\n")
        rc, _, err = _run([gz])
        self.assertEqual(rc, 2)
        self.assertIn("gzip", err)

    def test_one_bad_file_does_not_abort_the_batch(self):
        good = self.write("good.csv", "eeg_uv\n"
                          + "\n".join(str(v) for v in _sine(FS, 8, 10.0, 20.0)))
        binary = self.write_bytes("bad.bin", bytes(range(256)) * 200)
        rc, out, err = _run([good, binary, "--fs", "128", "--csv"])
        self.assertEqual(rc, 1)              # partial failure, not a traceback
        self.assertIn("입력 오류", err)
        self.assertGreaterEqual(len(_rows(out)), 2)   # the good file was analysed

    def test_single_column_comma_csv_with_a_stray_comma(self):
        """Sniffing must not pick ';' for a 1-column comma CSV just because ';' looks
        'consistent' — that turned rows with an extra comma into missing samples."""
        p = self.write("stray.csv", "eeg_uv\n1\n2,\n3\n4\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, ",")
        self.assertEqual(sig.values, [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(sig.n_filled, 0)

    def test_duplicate_column_names_are_disambiguated(self):
        p = self.write("dup.csv", "eeg_uv,eeg_uv\n1,5\n2,6\n3,7\n")
        sigs = load_signals(p)
        self.assertEqual([s.value_col for s in sigs], ["eeg_uv", "eeg_uv#2"])
        self.assertEqual(sigs[1].values, [5.0, 6.0, 7.0])
        self.assertTrue(any("duplicate column name" in w for w in sigs[0].warnings))

    def test_korean_column_names_are_auto_detected(self):
        p = self.write("kr.csv", "시간,뇌파\n0,1.5\n0.5,2.5\n1.0,3.5\n",
                       encoding="cp949")
        sig = load_signal(p)
        self.assertEqual(sig.value_col, "뇌파")
        self.assertEqual(sig.time_col, "시간")
        self.assertEqual(sig.values, [1.5, 2.5, 3.5])


# ---------------------------------------------------------------- stats -----------

class TestStatsHardening(unittest.TestCase):
    def test_adjusted_ci_matches_the_closed_form(self):
        vals = [math.sin(i / 5.0) * 3 + 20 for i in range(25)]
        st = summary_stats(vals)
        n_eff = effective_n(len(vals), st["rho1"])
        half = t_crit(max(1, int(math.floor(n_eff - 1)))) * st["sd"] / math.sqrt(n_eff)
        self.assertAlmostEqual(st["ci_hi_adj"] - st["mean"], half, places=12)
        self.assertAlmostEqual(st["n_eff"], n_eff, places=12)

    def test_rho_is_capped_so_n_eff_never_collapses(self):
        """rho is capped at 0.999 and n_eff floored at 2, so a near-unit-root series
        cannot produce a zero (or negative) effective sample size."""
        self.assertEqual(effective_n(1000, 0.9999), 2.0)
        self.assertEqual(effective_n(1000, 1.0), 2.0)
        self.assertAlmostEqual(effective_n(1000, 1 / 3), 500.0, places=9)
        self.assertAlmostEqual(effective_n(30, 0.5), 10.0, places=9)

    def test_df_for_the_adjusted_ci_uses_the_floor(self):
        """floor (conservative, wider CI) not ceil: n_eff=5.9 -> df=4, not 5."""
        vals = [1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0, 9.0, 8.0, 11.0]
        st = summary_stats(vals)
        df = max(1, int(math.floor(st["n_eff"] - 1)))
        expect = t_crit(df) * st["sd"] / math.sqrt(st["n_eff"])
        self.assertAlmostEqual(st["ci_hi_adj"] - st["mean"], expect, places=12)

    def test_t_crit_hard_coded_references(self):
        """Independent of scipy: values from standard t tables."""
        for df, want in ((1, 12.706), (10, 2.228), (30, 2.042), (60, 2.0003),
                         (120, 1.9799), (1000, 1.9623)):
            self.assertAlmostEqual(t_crit(df), want, delta=1e-3, msg=f"df={df}")

    def test_default_trend_cap_admits_a_whole_night(self):
        """10 h of 30 s epochs = 1200; the cap must not silently disable that."""
        self.assertGreaterEqual(MAX_EXACT_TREND_N, 1200)
        tr = trend([float(i % 97) for i in range(1200)])
        self.assertIsNotNone(tr)
        self.assertTrue(tr.exact)

    def test_theil_sen_ci_ranks(self):
        """Sen rank interval indices: round((N-C)/2)-1 and round((N+C)/2)."""
        vals = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
        ts = theil_sen_slope(vals)
        slopes = sorted((vals[j] - vals[i]) / (j - i)
                        for i in range(len(vals)) for j in range(i + 1, len(vals)))
        from eegband.stats import _Z975, mann_kendall
        c = _Z975 * math.sqrt(mann_kendall(vals)["var_s"])
        m = len(slopes)
        lo = max(0, min(m - 1, int(round((m - c) / 2.0)) - 1))
        hi = max(0, min(m - 1, int(round((m + c) / 2.0))))
        self.assertEqual(ts["slope_lo"], slopes[lo])
        self.assertEqual(ts["slope_hi"], slopes[hi])


# ---------------------------------------------------------------- aperiodic ------

class TestAperiodicHardening(unittest.TestCase):
    def _power_law(self, chi=1.5, offset=2.0, df=0.25, f_max=45.0):
        freqs = [k * df for k in range(int(f_max / df) + 1)]
        psd = [0.0 if f <= 0 else 10 ** (offset - chi * math.log10(f)) for f in freqs]
        return freqs, psd

    def test_notch_contributes_zero_not_abs(self):
        """max(psd-fit, 0), never abs(): a 50/60 Hz notch or a filter roll-off must not
        be counted as oscillatory power."""
        freqs, psd = self._power_law()
        idx = freqs.index(10.25)
        psd = list(psd)
        psd[idx] *= 0.02                         # deep notch below the background
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        rf, rp = residual_psd(freqs, psd, fit)
        # the notch bin itself is clipped to exactly zero...
        self.assertEqual(rp[rf.index(10.25)], 0.0)
        # ...and the band integral is materially below what abs() would produce
        clipped = oscillatory_power(rf, rp, 8.0, 13.0)
        by_f = dict(zip(freqs, psd))
        abs_resid = [abs(by_f[f] - fit.psd_at(f)) for f in rf]
        abs_int = integrate_psd(rf, abs_resid, 8.0, 13.0)
        self.assertGreater(abs_int, clipped + 0.5)

    def test_band_outside_fit_range_is_none_both_sides(self):
        freqs, psd = self._power_law()
        fit = fit_aperiodic(freqs, psd, 8.0, 13.0)
        rf, rp = residual_psd(freqs, psd, fit)
        self.assertIsNone(oscillatory_power(rf, rp, 0.5, 4.0))    # below
        self.assertIsNone(oscillatory_power(rf, rp, 30.0, 45.0))  # above

    def test_partially_covered_band_is_none_not_a_truncated_value(self):
        """With --fit-range 2-45 the delta row must not carry 2–4 Hz power under the
        label 'delta 0.5–4' — that understated SWA by ~50x on sleep data."""
        res = analyze(_sine(FS, 20.0, 1.5, 40.0), fs=FS, fit_range=(2.0, 45.0))
        delta = [b for b in res.overall.band_powers if b.name == "delta"][0]
        self.assertIsNone(delta.osc_absolute)
        self.assertTrue(any("not fully inside the 1/f fit range" in w
                            for w in res.warnings))
        # a band fully inside the range still reports a value
        alpha = [b for b in res.overall.band_powers if b.name == "alpha"][0]
        self.assertIsNotNone(alpha.osc_absolute)

    def test_robust_trim_iterates_and_keeps_a_floor(self):
        import random
        rng = random.Random(3)
        freqs, psd = self._power_law(1.6, 2.0)
        psd = [p * math.exp(rng.gauss(0, 0.05)) if p else p for p in psd]
        psd = [p + (60.0 * math.exp(-0.5 * ((f - 10.3) / 2.0) ** 2) if f > 0 else 0)
               for f, p in zip(freqs, psd)]
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="robust")
        one = fit_aperiodic(freqs, psd, 0.5, 45.0, mode="robust", max_iter=1)
        self.assertGreaterEqual(fit.n_trim_iter, 2)            # it really iterates
        self.assertGreaterEqual(fit.n_used, max(3, int(0.25 * fit.n_total)))
        self.assertLessEqual(abs(fit.exponent - 1.6), abs(one.exponent - 1.6) + 1e-9)

    def test_exact_power_law_is_not_trimmed(self):
        freqs, psd = self._power_law(1.0, 1.0)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        self.assertEqual(fit.n_used, fit.n_total)
        self.assertEqual(fit.n_trim_iter, 0)
        self.assertAlmostEqual(fit.exponent, 1.0, places=12)

    def test_min_keep_boundary(self):
        freqs, psd = self._power_law()
        self.assertIsNotNone(fit_aperiodic(freqs, psd, freqs[4], freqs[6]))  # 3 bins
        self.assertIsNone(fit_aperiodic(freqs, psd, freqs[4], freqs[5]))     # 2 bins

    def test_fit_range_edge_tolerance_is_tight(self):
        freqs, psd = self._power_law()
        lo = 8.0
        just_inside = fit_aperiodic(freqs, psd, lo * (1 + 1e-10), 13.0)
        self.assertEqual(just_inside.fit_lo, 8.0)          # 1e-10 over -> included
        clearly_outside = fit_aperiodic(freqs, psd, lo * (1 + 1e-3), 13.0)
        self.assertGreater(clearly_outside.fit_lo, 8.0)    # 1e-3 over -> excluded

    def test_each_prominence_gate_is_load_bearing(self):
        flat_f = [8.0 + 0.25 * k for k in range(21)]

        def flat(peak, left, right, noise=0.0):
            v = [noise] * 21
            v[10], v[9], v[11] = peak, left, right
            return v
        # (i) wide and tall in sigma terms but below the min log10 height
        self.assertFalse(flattened_peak(flat_f, flat(0.05, 0.04, 0.04), 8.0, 13.0)[2])
        # (ii) tall but no width (neighbours at 15% of the peak)
        self.assertFalse(flattened_peak(flat_f, flat(1.0, 0.15, 0.15), 8.0, 13.0)[2])
        # (iii) neighbours sum to 0.6h but neither reaches 0.4h
        self.assertFalse(flattened_peak(flat_f, flat(1.0, 0.30, 0.30), 8.0, 13.0)[2])
        # (iv) a genuine bump passes
        self.assertTrue(flattened_peak(flat_f, flat(1.0, 0.6, 0.5), 8.0, 13.0)[2])

    def test_specificity_on_stochastic_pink_noise(self):
        """Measured on genuinely chi-square-distributed 1/f noise (not a fixed-amplitude
        sinusoid sum, which has no spectral variability and understates the rate)."""
        false_pos = total = 0
        for seed in range(8):
            x = _pink(FS, 20.0, 1.4, 4000 + seed)
            freqs, psd, _ = welch_psd(x, FS, nperseg=512)
            fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
            ff, fv = flattened_log_spectrum(freqs, psd, fit)
            for _, lo, hi in DEFAULT_BANDS:
                total += 1
                if flattened_peak(ff, fv, lo, hi)[2]:
                    false_pos += 1
        # honest bound: wide bands (beta/gamma) run a few percent, narrow ones ~0
        self.assertLessEqual(false_pos / total, 0.15,
                             f"{false_pos}/{total} false positives")

    def test_real_rhythm_on_stochastic_noise_is_still_found(self):
        x = _pink(FS, 20.0, 1.4, 4100)
        x = [v + 40.0 * math.sin(2 * math.pi * 10.0 * i / FS)
             for i, v in enumerate(x)]
        freqs, psd, _ = welch_psd(x, FS, nperseg=512)
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        ff, fv = flattened_log_spectrum(freqs, psd, fit)
        f, h, prom = flattened_peak(ff, fv, 8.0, 13.0)
        self.assertTrue(prom)
        self.assertAlmostEqual(f, 10.0, delta=0.3)

    def test_knee_is_detected_by_the_half_range_slopes(self):
        """A 3rd-order knee fits at R²≈0.92, so R² alone cannot expose it."""
        freqs = [k * 0.25 for k in range(1, 181)]
        psd = [1000.0 / (1.0 + (f / 5.0) ** 3) for f in freqs]
        fit = fit_aperiodic(freqs, psd, 0.5, 45.0)
        self.assertGreater(fit.r2, 0.85)                 # R² looks fine
        halves = half_range_exponents(freqs, psd, fit)
        self.assertIsNotNone(halves)
        self.assertGreater(abs(halves[1] - halves[0]), 0.75)
        res = analyze(_sine(FS, 20.0, 10.0, 20.0), fs=FS)   # single rhythm, poor fit
        self.assertTrue(any("R²" in w for w in res.warnings))


# ---------------------------------------------------------------- edf ------------

class TestEdfHardening(TmpDir):
    def test_fs_comes_from_record_duration_not_samples_per_record(self):
        for rec_dur, n_per in ((0.5, 64), (2.0, 256), (4.0, 512)):
            p = self.path(f"rd{rec_dur}.edf")
            write_edf(p, [("A", "uV", sine(FS, 8, 10.0, 20.0))], FS,
                      record_duration=rec_dur, phys_range=(-20.0, 20.0))
            info = read_edf_info(p)
            self.assertEqual(info.signals[0].n_per_record, n_per)
            self.assertAlmostEqual(info.signals[0].fs, FS, places=9)
            self.assertAlmostEqual(info.duration_sec, 8.0, places=6)
            sig, fs, _ = read_edf_channel(p, start_sec=1.25, duration_sec=2.0)
            self.assertEqual(fs, FS)
            self.assertEqual(len(sig.values), int(2.0 * FS))

    def test_start_inside_a_record_lands_on_the_right_sample(self):
        want = sine(FS, 10, 3.0, 25.0)      # 3 Hz: not zero at 0.25 s multiples
        p = self.path("win.edf")
        write_edf(p, [("A", "uV", want)], FS, phys_range=(-25.0, 25.0))
        sig, _, _ = read_edf_channel(p, start_sec=2.5, duration_sec=1.0)
        i0 = int(2.5 * FS)
        self.assertEqual(len(sig.values), int(FS))
        self.assertLessEqual(
            max(abs(a - b) for a, b in zip(sig.values, want[i0:i0 + int(FS)])),
            50.0 / 65535.0)
        # and it is NOT the next whole record
        self.assertGreater(
            max(abs(a - b) for a, b in zip(sig.values, want[int(3 * FS):])), 1.0)

    def test_duplicate_labels_read_distinct_signals(self):
        """--channels all must analyse every signal, not the first match twice."""
        p = self.path("dupl.edf")
        write_edf(p, [("C3", "uV", sine(FS, 8, 10.0, 20.0)),
                      ("C3", "uV", sine(FS, 8, 1.5, 60.0))], FS,
                  phys_range=(-60.0, 60.0))
        info = read_edf_info(p)
        self.assertEqual(info.duplicate_labels(), ["C3"])
        self.assertTrue(any("duplicate channel label" in w for w in info.warnings))
        rc, out, _ = _run([p, "--channels", "all", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        doms = [s["overall"]["dominant_band"] for s in d["series"]]
        self.assertEqual(doms, ["alpha", "delta"])     # both channels, not a clone

    def test_data_channel_after_an_annotation_channel(self):
        """Byte offset uses ALL preceding signals, including annotation channels with a
        different samples/record."""
        ns, n_rec = 2, 4
        eeg = sine(FS, n_rec, 10.0, 20.0)
        head = bytearray()
        head += _fixed("0", 8) + _fixed("X X X X", 80) + _fixed("Startdate X", 80)
        head += _fixed("01.01.85", 8) + _fixed("00.00.00", 8)
        head += _num(256 + 256 * ns, 8) + _fixed("EDF+C", 44)
        head += _num(n_rec, 8) + _num(1.0, 8) + _fixed(str(ns), 4)
        sh = bytearray()
        sh += _fixed("EDF Annotations", 16) + _fixed("Fp1", 16)
        sh += _fixed("", 80) * 2
        sh += _fixed("", 8) + _fixed("uV", 8)
        sh += _num(-1, 8) + _num(-20, 8)
        sh += _num(1, 8) + _num(20, 8)
        sh += _num(-32768, 8) * 2
        sh += _num(32767, 8) * 2
        sh += _fixed("", 80) * 2
        sh += _fixed("60", 8) + _fixed(str(int(FS)), 8)      # 60 vs 128 per record
        sh += _fixed("", 32) * 2
        body = bytearray()
        for r in range(n_rec):
            body += b"\x00\x00" * 60                          # annotation bytes
            for k in range(int(FS)):
                v = eeg[r * int(FS) + k]
                d = int(round(-32768 + (v + 20.0) * 65535 / 40.0))
                body += (max(-32768, min(32767, d)) & 0xFFFF).to_bytes(2, "little")
        p = self.path("annot_first.edf")
        with open(p, "wb") as fh:
            fh.write(bytes(head) + bytes(sh) + bytes(body))
        info = read_edf_info(p)
        self.assertTrue(info.signals[0].is_annotation)
        sig, fs, es = read_edf_channel(p, "Fp1", info=info)
        self.assertEqual((es.index, fs), (1, FS))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(sig.values, eeg)),
                             40.0 / 65535.0)

    def test_annotation_label_variants_are_recognised(self):
        p = self.path("annot2.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 4, 10.0)),
                      ("Annotations", "", [0.0] * int(FS * 4))], FS)
        info = read_edf_info(p)
        self.assertTrue(info.signals[1].is_annotation)
        self.assertEqual([s.label for s in info.data_signals], ["Fp1"])

    def test_physical_constant_channel_is_that_constant(self):
        """phys_min == phys_max: every sample IS that value; returning raw ADC codes
        fabricated a spectrum, and the old warning blamed the digital range."""
        p = self.path("physeq.edf")
        write_edf(p, [("A", "uV", [0.0] * int(FS * 2))], FS, phys_range=(5.0, 5.0))
        info = read_edf_info(p)
        self.assertEqual(info.signals[0].calibration, "phys_constant")
        sig, _, _ = read_edf_channel(p, info=info)
        self.assertEqual(set(sig.values), {5.0})
        self.assertTrue(any("physical min == max" in w for w in sig.warnings))
        self.assertFalse(any("digital min == max" in w for w in sig.warnings))

    def test_unreadable_channel_is_skipped_not_fatal(self):
        """A dud channel (0 samples/record) must not kill --channels all."""
        ns, n_rec = 2, 4
        good = sine(FS, n_rec, 10.0, 20.0)
        head = bytearray()
        head += _fixed("0", 8) + _fixed("X", 80) + _fixed("X", 80)
        head += _fixed("01.01.85", 8) + _fixed("00.00.00", 8)
        head += _num(256 + 256 * ns, 8) + _fixed("", 44)
        head += _num(n_rec, 8) + _num(1.0, 8) + _fixed(str(ns), 4)
        sh = bytearray()
        sh += _fixed("C3", 16) + _fixed("DEAD", 16)
        sh += _fixed("", 80) * 2
        sh += _fixed("uV", 8) * 2
        sh += _num(-20, 8) * 2
        sh += _num(20, 8) * 2
        sh += _num(-32768, 8) * 2
        sh += _num(32767, 8) * 2
        sh += _fixed("", 80) * 2
        sh += _fixed(str(int(FS)), 8) + _fixed("0", 8)
        sh += _fixed("", 32) * 2
        body = bytearray()
        for r in range(n_rec):
            for k in range(int(FS)):
                v = good[r * int(FS) + k]
                d = int(round(-32768 + (v + 20.0) * 65535 / 40.0))
                body += (max(-32768, min(32767, d)) & 0xFFFF).to_bytes(2, "little")
        p = self.path("dud.edf")
        with open(p, "wb") as fh:
            fh.write(bytes(head) + bytes(sh) + bytes(body))
        rc, out, err = _run([p, "--channels", "all", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["label"], "C3")
        self.assertTrue(any("skipped unreadable" in w for w in d["warnings"]))

    def test_control_characters_in_labels_are_sanitised(self):
        p = self.path("evil.edf")
        write_edf(p, [("\x07\x1b[31mRED", "uV", sine(FS, 4, 10.0, 20.0))], FS)
        info = read_edf_info(p)
        label = info.signals[0].label
        self.assertNotIn("\x1b", label)
        self.assertNotIn("\x07", label)
        rc, out, err = _run([p, "--list-channels"])
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b", out)
        rc, out, _ = _run([p])
        self.assertNotIn("\x1b", out)

    def test_header_bytes_are_not_echoed_in_error_messages(self):
        """A non-EDF file's bytes are file CONTENT; messages promise only names."""
        blob = b"0" + b"A" * 183 + b"SECRET!!" + b"B" * 64
        p = self.write_bytes("fake.edf", blob + b"C" * 4096)
        rc, _, err = _run([p])
        self.assertEqual(rc, 2)
        self.assertNotIn("SECRET", err)
        self.assertIn("입력 오류", err)

    def test_only_first_channel_analysed_is_announced(self):
        p = self.path("two.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 4, 10.0, 20.0)),
                      ("Cz", "uV", sine(FS, 4, 2.0, 40.0))], FS,
                  phys_range=(-40.0, 40.0))
        rc, out, _ = _run([p, "--json"])
        d = json.loads(out)
        self.assertTrue(any("only the first signal channel" in w
                            for w in d["warnings"]))


# ---------------------------------------------------------------- analyze --------

class TestAnalyzeHardening(unittest.TestCase):
    def test_swa_label_follows_the_actual_delta_band(self):
        bands = [("delta", 1.0, 4.0), ("theta", 4.0, 8.0), ("sigma", 12.0, 15.0)]
        res = analyze(_sine(FS, 20.0, 2.0, 30.0), fs=FS, bands=bands)
        self.assertEqual((res.overall.swa_lo, res.overall.swa_hi), (1.0, 4.0))
        txt = render_text(res)
        self.assertIn("SWA = 1–4 Hz", txt)
        self.assertNotIn("SWA = delta 0.5–4 Hz", txt)

    def test_no_delta_band_reports_swa_as_undefined(self):
        bands = [("low", 1.0, 6.0), ("high", 6.0, 40.0)]
        res = analyze(_sine(FS, 20.0, 2.0, 30.0), fs=FS, bands=bands)
        self.assertEqual(res.overall.swa_source, "undefined")
        txt = render_text(res)
        self.assertIn("SWA = n/a", txt)
        d = to_dict(res)
        self.assertIsNone(d["overall"]["swa"]["absolute_uv2"])

    def test_swa_band_option(self):
        bands = [("low", 1.0, 6.0), ("high", 6.0, 40.0)]
        res = analyze(_sine(FS, 20.0, 2.0, 30.0), fs=FS, bands=bands,
                      swa_band=(1.0, 4.0))
        self.assertEqual(res.overall.swa_source, "swa_band")
        self.assertGreater(res.overall.swa_abs, 100.0)
        self.assertIn("(--swa-band)", render_text(res))

    def test_coarse_epoch_resolution_is_warned(self):
        """A 0.25 s epoch cannot resolve a 3.5 Hz-wide delta band; the per-epoch SWA
        was ~1000x off with no warning at all."""
        res = analyze(_sine(FS, 20.0, 1.5, 40.0), fs=FS, epoch_sec=0.25)
        self.assertTrue(any("frequency resolution" in w for w in res.warnings))

    def test_nperseg_below_two_is_rejected(self):
        for bad in (1, 0, -5):
            with self.assertRaises(ValueError):
                analyze(_sine(FS, 4.0, 10.0, 5.0), fs=FS, nperseg=bad)

    def test_clamped_nperseg_and_noverlap_are_warned(self):
        res = analyze(_sine(FS, 4.0, 10.0, 5.0), fs=FS, nperseg=5000,
                      noverlap=99999)
        self.assertTrue(any("clamped" in w for w in res.warnings))
        self.assertTrue(any("noverlap" in w for w in res.warnings))

    def test_trend_x_is_epoch_start_seconds(self):
        """The JSON slope is per SECOND; using the epoch index instead scaled it by
        epoch_sec while still calling it per-second."""
        vals = []
        for e in range(8):
            amp = 60.0 * (0.85 ** e)
            vals += _sine(FS, 4.0, 1.5, amp)
        res = analyze(vals, fs=FS, epoch_sec=4.0)
        series = [ep.spectrum.swa_abs for ep in res.epochs]
        per_index = theil_sen_slope(series)["slope"]
        tr = res.epoch_trends["swa_absolute_uv2"]
        self.assertAlmostEqual(tr.slope, per_index / 4.0, places=9)
        d = to_dict(res)
        self.assertAlmostEqual(
            d["epoch_summary"]["trends"]["swa_absolute_uv2"]
            ["theil_sen_slope_per_sec"], tr.slope, places=12)

    def test_per_band_endpoints_are_summarised(self):
        """A custom sigma band must get the same summary/trend treatment as delta."""
        bands = [("delta", 0.5, 4.0), ("sigma", 12.0, 15.0)]
        vals = []
        for e in range(6):
            vals += _sine(FS, 4.0, 13.0, 10.0 + 5.0 * e)
        res = analyze(vals, fs=FS, bands=bands, epoch_sec=4.0)
        self.assertIn("sigma_absolute_uv2", res.epoch_summary)
        self.assertIn("sigma_relative", res.epoch_summary)
        tr = res.epoch_trends["sigma_absolute_uv2"]
        self.assertGreater(tr.slope, 0.0)
        self.assertLess(tr.p, 0.05)

    def test_log10_swa_endpoint(self):
        vals = []
        for e in range(4):                     # amplitudes 20, 40, 60, 80 uV
            vals += _sine(FS, 10.0, 1.5, 20.0 * (e + 1))
        res = analyze(vals, fs=FS, epoch_sec=10.0)
        st = res.epoch_summary["swa_absolute_log10"]
        lin = res.epoch_summary["swa_absolute_uv2"]
        self.assertAlmostEqual(st["mean"], math.fsum(
            math.log10(ep.spectrum.swa_abs) for ep in res.epochs) / 4.0, places=9)
        # the geometric (log) mean of a skewed series differs from log of the mean
        self.assertNotAlmostEqual(st["mean"], math.log10(lin["mean"]), places=3)

    def test_dominant_tie_threshold_is_one_percent(self):
        # two equal-power rhythms in different bands -> tie
        x = [a + b for a, b in zip(_sine(FS, 20.0, 2.0, 10.0),
                                   _sine(FS, 20.0, 10.0, 10.0))]
        res = analyze(x, fs=FS)
        self.assertTrue(res.overall.dominant_tie)
        # a clear 10:1 winner -> no tie
        y = [a + b for a, b in zip(_sine(FS, 20.0, 2.0, 30.0),
                                   _sine(FS, 20.0, 10.0, 3.0))]
        self.assertFalse(analyze(y, fs=FS).overall.dominant_tie)

    def test_band_overlap_and_gap_thresholds(self):
        gappy = analyze(_sine(FS, 20.0, 10.0, 10.0), fs=FS,
                        bands=[("a", 0.5, 4.0), ("b", 20.0, 45.0)])
        self.assertTrue(any("gaps" in w for w in gappy.warnings))
        over = analyze(_sine(FS, 20.0, 10.0, 10.0), fs=FS,
                       bands=[("a", 0.5, 20.0), ("b", 4.0, 45.0)])
        self.assertTrue(any("overlap" in w for w in over.warnings))

    def test_nyquist_clamped_band_and_safe_range(self):
        res = analyze([1.0, 2.0] * 64, fs=8.0, bands=[("g", 30.0, 45.0)],
                      aperiodic_mode=None)
        self.assertTrue(any("Nyquist" in w for w in res.warnings))
        txt = render_text(res)
        self.assertNotIn("30–4 ", txt)       # never a reversed span
        self.assertIn("not measured", txt)   # marked as unavailable, not 0

    def test_non_finite_band_edges_rejected(self):
        with self.assertRaises(ValueError):
            analyze(_sine(FS, 4.0, 10.0, 5.0), fs=FS,
                    bands=[("x", float("nan"), float("nan"))])

    def test_quantised_signal_is_not_called_flat_lined(self):
        """A healthy trace rounded to 5 µV steps was reported as 62% flat-lining."""
        x = [round(v / 5.0) * 5.0 for v in _sine(FS, 20.0, 10.0, 20.0)]
        q = signal_quality(x, fs=FS)
        self.assertFalse(any("리드 탈락" in f for f in q.flags))
        self.assertTrue(any("양자화" in f for f in q.flags))
        self.assertAlmostEqual(q.quant_step, 5.0, places=9)
        # a real dropout still flags
        y = _sine(FS, 20.0, 10.0, 20.0)
        for i in range(500, 1500):
            y[i] = 0.0
        q2 = signal_quality(y, fs=FS)
        self.assertTrue(any("리드 탈락" in f for f in q2.flags))

    def test_endpoint_dropped_from_summary_is_warned(self):
        """A constant epoch has no SEF/entropy/exponent; the endpoint must not silently
        vanish from the summary."""
        vals = _sine(FS, 8.0, 1.5, 40.0) + [0.0] * int(FS * 4)
        res = analyze(vals, fs=FS, epoch_sec=4.0)
        self.assertTrue(any("omitted from the epoch summary" in w
                            for w in res.warnings))


# ---------------------------------------------------------------- report --------

class TestReportHardening(unittest.TestCase):
    def test_csv_cells_are_exact_and_flags_have_the_right_polarity(self):
        x = [a + b for a, b in zip(_sine(FS, 20.0, 2.0, 10.0),
                                   _sine(FS, 20.0, 10.0, 10.0))]
        res = analyze(x, fs=FS)
        rows = _rows(render_csv(res, comment=False))
        cells = dict(zip(rows[0], rows[1]))
        self.assertTrue(res.overall.dominant_tie)
        self.assertEqual(cells["dominant_tie"], "1")
        self.assertEqual(cells["total_uv2"], repr(res.overall.total_power))
        gamma = [b for b in res.overall.band_powers if b.name == "gamma"][0]
        self.assertFalse(gamma.adj_peak_prominent)
        self.assertEqual(cells["gamma_adj_peak_hz"], "")
        self.assertEqual(cells["nyquist_hz"], repr(FS / 2))

    def test_text_peak_is_gated_on_prominence(self):
        """The peak(Hz) CELL must be n/a for a non-prominent band (a 1/f argmax)."""
        res = analyze(_pink(FS, 20.0, 1.4, 77), fs=FS)
        table = render_text(res).split("[1] ")[1].split("[2]")[0].splitlines()
        names = [b.name for b in res.overall.band_powers]
        cells = {}
        for line in table:
            parts = line.split("←")[0].split()      # drop the trailing '← SWA' marker
            if len(parts) >= 5 and parts[0] in names:
                cells[parts[0]] = parts[-1]
        for bp in res.overall.band_powers:
            if bp.name not in cells:
                continue
            if bp.peak_prominent:
                self.assertEqual(cells[bp.name], f"{bp.peak_freq:.2f}", bp.name)
            else:
                self.assertEqual(cells[bp.name], "n/a", bp.name)

    def test_relative_scale_and_trend_units_in_text(self):
        res = analyze(_sine(FS, 32.0, 1.5, 40.0), fs=FS, epoch_sec=4.0)
        txt = render_text(res)
        self.assertIn("relative SWA across epochs = 100.0", txt)
        self.assertIn("per s", txt)          # 32 s recording -> per second
        long_vals = []
        for e in range(80):
            long_vals += _sine(FS, 30.0, 1.5, 40.0 + 0.1 * e)
        long_res = analyze(long_vals, fs=FS, epoch_sec=30.0)
        self.assertIn("per h", render_text(long_res))   # 40 min -> per hour

    def test_nan_renders_as_nan_not_zero(self):
        res = analyze([0.0] * 512, fs=FS)     # constant: ratios are NaN
        txt = render_text(res)
        self.assertIn("NaN", txt)

    def test_extreme_magnitudes_use_scientific_notation(self):
        big = analyze([1e12 * v for v in _sine(FS, 8.0, 10.0, 1.0)], fs=FS)
        small = analyze([1e-12 * v for v in _sine(FS, 8.0, 10.0, 1.0)], fs=FS)
        self.assertIn("e+", render_text(big))
        self.assertIn("e-", render_text(small))

    def test_json_is_strictly_valid_even_with_nan_inputs(self):
        res = analyze(_sine(FS, 8.0, 1.5, 40.0), fs=FS, epoch_sec=4.0)
        payload = json.dumps(to_dict(res), allow_nan=False)   # must not raise
        self.assertNotIn("NaN", payload)
        self.assertNotIn("Infinity", payload)
        d = json.loads(payload)
        self.assertIsNone(d["epoch_summary"]["swa_relative"]["rho1"])

    def test_csv_summary_has_one_row_per_series_with_qc_and_trends(self):
        res = analyze(_sine(FS, 40.0, 1.5, 40.0), fs=FS, epoch_sec=5.0,
                      label="Fp1")
        rows = _rows(render_csv_summary([res], comment=False))
        self.assertEqual(len(rows), 2)
        cells = dict(zip(rows[0], rows[1]))
        self.assertEqual(cells["series"], "Fp1")
        self.assertEqual(cells["n_epochs"], "8")
        self.assertEqual(cells["qc_pass"], "1")
        self.assertAlmostEqual(float(cells["swa_absolute_uv2_mean"]),
                               res.epoch_summary["swa_absolute_uv2"]["mean"],
                               places=9)
        self.assertAlmostEqual(
            float(cells["swa_absolute_uv2_theil_sen_slope_per_sec"]),
            res.epoch_trends["swa_absolute_uv2"].slope, places=12)
        self.assertIn("swa_absolute_uv2_ci_lo_adj", cells)

    def test_formula_injection_is_neutralised_and_rectangle_kept(self):
        res = analyze(_sine(FS, 8.0, 10.0, 20.0), fs=FS,
                      label='=WEBSERVICE("http://x")')
        text = render_csv(res, comment=False)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len({len(r) for r in rows}), 1)      # clean rectangle
        self.assertTrue(rows[1][0].startswith("'="))


# ---------------------------------------------------------------- cli -----------

class TestCliHardening(TmpDir):
    def _csv(self, name="x.csv", dur=20.0, freq=1.5, amp=40.0):
        body = "\n".join(str(v) for v in _sine(FS, dur, freq, amp))
        return self.write(name, "eeg_uv\n" + body + "\n")

    def test_warnings_reach_stderr_in_csv_mode(self):
        p = self._csv()
        rc, out, err = _run([p, "--fs", "128", "--epoch", "5", "--max-amp", "1",
                             "--csv"])
        self.assertEqual(rc, 0)
        self.assertIn("QC FAILURE", err)
        self.assertGreater(len(err), 0)

    def test_qc_failure_produces_no_summary_row_values(self):
        p = self._csv()
        rc, out, err = _run([p, "--fs", "128", "--epoch", "5", "--max-amp", "1",
                             "--json"])
        d = json.loads(out)
        self.assertFalse(d["qc"]["pass"])
        self.assertIsNone(d["swa_density"])
        self.assertNotIn("epoch_summary", d)

    def test_sef_label_matches_a_fractional_percentile(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--sef", "92.5"])
        self.assertIn("SEF92.5", out)

    def test_output_modes_are_mutually_exclusive(self):
        p = self._csv()
        for a, b in (("--json", "--csv"), ("--csv", "--csv-summary"),
                     ("--json", "--psd-csv"), ("--csv-summary", "--psd-csv")):
            rc, _, err = _run([p, "--fs", "128", a, b])
            self.assertEqual(rc, 2, f"{a} {b}")
            self.assertIn("입력 오류", err)

    def test_psd_csv_matches_the_reported_spectrum(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--psd-csv", "--no-comment"])
        self.assertEqual(rc, 0)
        rows = _rows(out)
        header, data = rows[0], rows[1:]
        self.assertEqual(header[:4], ["series", "source_file", "freq_hz",
                                      "psd_uv2_per_hz"])
        freqs, psd, _ = welch_psd(_sine(FS, 20.0, 1.5, 40.0), FS, nperseg=512)
        self.assertEqual(len(data), len(freqs))
        self.assertAlmostEqual(float(data[10][2]), freqs[10], places=12)
        self.assertAlmostEqual(float(data[10][3]), psd[10], places=12)

    def test_max_amp_without_epoch_is_flagged_and_not_claimed(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--max-amp", "5", "--csv"])
        self.assertEqual(rc, 0)
        prov = [l for l in out.splitlines() if l.startswith("#")][0]
        self.assertIn("max_amp= ", prov + " ")     # recorded as not applied
        rc, out2, _ = _run([p, "--fs", "128", "--max-amp", "5", "--json"])
        d = json.loads(out2)
        self.assertTrue(any("do nothing without --epoch" in w
                            for w in d["warnings"]))

    def test_start_duration_provenance_for_csv_and_edf(self):
        p = self._csv(dur=40.0)
        rc, out, _ = _run([p, "--fs", "128", "--start", "10", "--duration", "10",
                           "--csv"])
        prov = [l for l in out.splitlines() if l.startswith("#")][0]
        self.assertIn("window=10+10s", prov)
        e = self.path("w.edf")
        write_edf(e, [("A", "uV", sine(FS, 40, 10.0, 20.0))], FS,
                  phys_range=(-20.0, 20.0))
        rc, out, _ = _run([e, "--start", "10", "--duration", "10", "--json"])
        d = json.loads(out)
        self.assertEqual(d["provenance"]["analysis_start_sec"], 10.0)
        self.assertTrue(any("as requested by --start/--duration" in w
                            for w in d["warnings"]))

    def test_above_nyquist_bands_are_blank_not_zero_in_csv(self):
        body = "\n".join(str(v) for v in _sine(25.0, 20.0, 2.0, 30.0))
        p = self.write("slow.csv", "eeg_uv\n" + body + "\n")
        rc, out, _ = _run([p, "--fs", "25", "--csv", "--no-comment"])
        rows = _rows(out)
        cells = dict(zip(rows[0], rows[1]))
        self.assertEqual(cells["gamma_abs_uv2"], "")     # 30-45 Hz > 12.5 Hz Nyquist
        self.assertNotEqual(cells["delta_abs_uv2"], "")

    def test_explicit_fs_beats_a_bogus_time_column(self):
        rows = ["time_ms_unix,eeg_uv"]
        for k in range(1024):
            rows.append(f"{1700000000000 + k * 8},"
                        f"{20 * math.sin(2 * math.pi * 10 * k / FS)}")
        p = self.write("unix.csv", "\n".join(rows) + "\n")
        rc, out, _ = _run([p, "--fs", "128", "--time", "time_ms_unix", "--json"])
        d = json.loads(out)
        self.assertEqual(d["fs_hz"], 128.0)
        self.assertTrue(any("USING --fs" in w for w in d["warnings"]))


if __name__ == "__main__":
    unittest.main()
