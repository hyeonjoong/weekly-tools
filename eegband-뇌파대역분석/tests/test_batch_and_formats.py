"""Multi-channel / multi-file batching and messy-input formats, end to end.

Covers the CLI surfaces added for real clinical workflows: ``--channels``,
several INPUTs at once, ``--list-channels``, ``--start/--duration`` cropping, EDF
input, the aperiodic options, ``--max-grad`` rejection, and the tidy CSV/JSON shapes
those produce. Also exercises the CSV/TSV reader on semicolon + decimal-comma,
tab-separated, ragged and non-UTF8 files.
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

from eegband.cli import main
from eegband.dataio import list_columns, load_signal, load_signals, parse_float

from edf_fixtures import sine, write_edf

FS = 128.0


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _rows(csv_text):
    lines = [ln for ln in csv_text.splitlines() if ln and not ln.startswith("#")]
    return list(csv.reader(lines))


class TempFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="eegband-batch-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(text.encode(encoding))
        return path

    def path(self, name):
        return os.path.join(self.dir, name)

    def wide_csv(self, name="wide.csv", dur=8.0):
        n = int(FS * dur)
        lines = ["time_s,Fp1,Cz,O1"]
        for k in range(n):
            t = k / FS
            lines.append(
                f"{t:.6f},{20 * math.sin(2 * math.pi * 10 * t):.4f},"
                f"{40 * math.sin(2 * math.pi * 2 * t):.4f},"
                f"{5 * math.sin(2 * math.pi * 20 * t):.4f}")
        return self.write(name, "\n".join(lines) + "\n")


class TestDelimitersAndDecimals(TempFiles):
    def test_semicolon_with_decimal_comma(self):
        lines = ["time_s;eeg_uv"]
        for k in range(256):
            t = k / FS
            v = 20 * math.sin(2 * math.pi * 10 * t)
            lines.append(f"{t:.6f};{v:.4f}".replace(".", ","))
        p = self.write("eu.csv", "\n".join(lines) + "\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, ";")
        self.assertTrue(sig.decimal_comma)
        self.assertEqual(sig.value_col, "eeg_uv")
        self.assertEqual(sig.time_col, "time_s")
        self.assertEqual(len(sig.values), 256)
        for k in (0, 3, 17, 255):      # values decoded from '12,3'-style cells
            self.assertAlmostEqual(sig.values[k],
                                   20 * math.sin(2 * math.pi * 10 * k / FS),
                                   delta=0.001)
        self.assertAlmostEqual(sig.times[1], 1 / FS, places=6)
        rc, out, _ = _run([p, "--fs", "128"])
        self.assertEqual(rc, 0)
        self.assertIn("alpha", out)

    def test_tab_separated(self):
        p = self.write("t.tsv", "time_s\teeg_uv\n0\t1.5\n0.0078125\t2.5\n"
                                "0.015625\t3.5\n0.0234375\t4.5\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, "\t")
        self.assertEqual(sig.values, [1.5, 2.5, 3.5, 4.5])
        self.assertFalse(sig.decimal_comma)

    def test_pipe_separated(self):
        p = self.write("p.csv", "time_s|eeg_uv\n0|1\n0.1|2\n0.2|3\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, "|")
        self.assertEqual(sig.values, [1.0, 2.0, 3.0])

    def test_plain_comma_still_wins(self):
        p = self.write("c.csv", "time_s,eeg_uv\n0,1.5\n0.1,2.5\n0.2,3.5\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, ",")
        self.assertEqual(sig.values, [1.5, 2.5, 3.5])

    def test_single_column_file_is_unaffected(self):
        p = self.write("one.csv", "eeg_uv\n1.5\n2.5\n3.5\n")
        sig = load_signal(p)
        self.assertEqual(sig.delimiter, ",")
        self.assertIsNone(sig.times)
        self.assertEqual(sig.values, [1.5, 2.5, 3.5])

    def test_decimal_comma_not_applied_to_comma_csv(self):
        """In a comma-separated file a comma can only be a separator."""
        p = self.write("amb.csv", "a,b\n1,234\n2,345\n3,456\n")
        sig = load_signal(p, value_col="b")
        self.assertEqual(sig.values, [234.0, 345.0, 456.0])
        self.assertFalse(sig.decimal_comma)

    def test_thousands_grouped_number_is_not_mangled(self):
        p = self.write("g.csv", "t;v\n0;1,5\n1;2,5\n2;1.234,5\n3;3,5\n")
        sig = load_signal(p, value_col="v")
        self.assertTrue(sig.decimal_comma)
        # "1.234,5" has both marks -> unparseable -> interpolated between 2.5 and 3.5
        self.assertEqual(sig.n_filled, 1)
        self.assertAlmostEqual(sig.values[2], 3.0, places=9)

    def test_parse_float_decimal_comma_rules(self):
        self.assertEqual(parse_float("12,3", decimal_comma=True), 12.3)
        self.assertIsNone(parse_float("1,234,567", decimal_comma=True))
        self.assertIsNone(parse_float("1.234,5", decimal_comma=True))
        self.assertIsNone(parse_float("12,3"))
        self.assertEqual(parse_float(" -0,5 ", decimal_comma=True), -0.5)

    def test_ragged_rows_and_na_labels(self):
        p = self.write("r.csv", "time_s,eeg_uv\n0,1.0\n0.1,\n0.2,NA\n0.3\n0.4,5.0\n")
        sig = load_signal(p)
        self.assertEqual(len(sig.values), 5)
        self.assertEqual(sig.n_filled, 3)
        self.assertAlmostEqual(sig.values[0], 1.0)
        self.assertAlmostEqual(sig.values[4], 5.0)

    def test_cp949_semicolon_file(self):
        text = "시간;뇌파\n0;1,5\n1;2,5\n2;3,5\n"
        p = self.write("k.csv", text, encoding="cp949")
        sig = load_signal(p, value_col="뇌파")
        self.assertEqual(sig.encoding, "cp949")
        self.assertTrue(sig.decimal_comma)
        self.assertEqual(sig.values, [1.5, 2.5, 3.5])

    def test_list_columns(self):
        p = self.wide_csv()
        values, tcol, delim, enc = list_columns(p)
        self.assertEqual(values, ["Fp1", "Cz", "O1"])
        self.assertEqual(tcol, "time_s")
        self.assertEqual(delim, ",")
        self.assertEqual(enc, "utf-8-sig")


class TestMultiChannelLoading(TempFiles):
    def test_all_channels(self):
        p = self.wide_csv()
        sigs = load_signals(p)
        self.assertEqual([s.value_col for s in sigs], ["Fp1", "Cz", "O1"])
        for s in sigs:
            self.assertEqual(len(s.values), int(FS * 8))
            self.assertIsNotNone(s.times)
            self.assertEqual(s.source_file, p)

    def test_named_channels_in_requested_order(self):
        p = self.wide_csv()
        sigs = load_signals(p, ["O1", "Fp1"])
        self.assertEqual([s.value_col for s in sigs], ["O1", "Fp1"])

    def test_duplicate_request_is_deduplicated(self):
        p = self.wide_csv()
        sigs = load_signals(p, ["Cz", "Cz"])
        self.assertEqual([s.value_col for s in sigs], ["Cz"])

    def test_unknown_channel_raises(self):
        p = self.wide_csv()
        with self.assertRaises(ValueError) as ctx:
            load_signals(p, ["Fp9"])
        self.assertIn("Fp9", str(ctx.exception))

    def test_time_column_can_be_analysed_if_named_explicitly(self):
        p = self.wide_csv()
        sigs = load_signals(p, ["time_s"])
        self.assertEqual([s.value_col for s in sigs], ["time_s"])
        self.assertIsNone(sigs[0].times)     # it is the value column now

    def test_text_column_is_skipped_with_a_warning(self):
        p = self.write("mix.csv",
                       "time_s,eeg_uv,stage\n0,1.0,N2\n0.1,2.0,N3\n0.2,3.0,N3\n")
        sigs = load_signals(p)
        self.assertEqual([s.value_col for s in sigs], ["eeg_uv"])
        self.assertTrue(any("skipped non-numeric" in w for w in sigs[0].warnings))

    def test_all_columns_non_numeric_raises(self):
        p = self.write("bad.csv", "time_s,note\n0,a\n0.1,b\n")
        with self.assertRaises(ValueError):
            load_signals(p)


class TestCliChannelsAndBatch(TempFiles):
    def test_channels_all_text_report_and_comparison(self):
        p = self.wide_csv()
        rc, out, err = _run([p, "--channels", "all"])
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn("Series comparison", out)
        for name in ("Fp1", "Cz", "O1"):
            self.assertIn(name, out)
        self.assertEqual(out.count("[1] 대역파워"), 3)

    def test_channels_csv_has_series_columns(self):
        p = self.wide_csv()
        rc, out, _ = _run([p, "--channels", "all", "--epoch", "4", "--csv"])
        self.assertEqual(rc, 0)
        rows = _rows(out)
        header = rows[0]
        self.assertEqual(header[:2], ["series", "source_file"])
        self.assertEqual(len(rows), 1 + 3 * 2)          # 3 channels × 2 epochs
        self.assertEqual({r[0] for r in rows[1:]}, {"Fp1", "Cz", "O1"})
        # every row has the same width -> a clean rectangle
        self.assertEqual({len(r) for r in rows}, {len(header)})

    def test_channels_json_is_a_series_list(self):
        p = self.wide_csv()
        rc, out, _ = _run([p, "--channels", "Fp1,Cz", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["n_series"], 2)
        self.assertEqual([s["label"] for s in d["series"]], ["Fp1", "Cz"])
        self.assertEqual(d["series"][0]["overall"]["dominant_band"], "alpha")
        self.assertEqual(d["series"][1]["overall"]["dominant_band"], "delta")

    def test_single_channel_json_stays_flat(self):
        p = self.wide_csv()
        rc, out, _ = _run([p, "--value", "Fp1", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertNotIn("series", d)
        self.assertEqual(d["tool"], "eegband")

    def test_multiple_files_labelled_by_stem(self):
        a = self.write("subjA.csv", "eeg_uv\n" + "\n".join(
            f"{20 * math.sin(2 * math.pi * 10 * k / FS)}" for k in range(1024)))
        b = self.write("subjB.csv", "eeg_uv\n" + "\n".join(
            f"{40 * math.sin(2 * math.pi * 2 * k / FS)}" for k in range(1024)))
        rc, out, _ = _run([a, b, "--fs", "128", "--csv"])
        self.assertEqual(rc, 0)
        rows = _rows(out)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[0] for r in rows[1:]], ["subjA", "subjB"])
        self.assertEqual([os.path.basename(r[1]) for r in rows[1:]],
                         ["subjA.csv", "subjB.csv"])

    def test_multi_file_multi_channel_label_includes_both(self):
        a = self.wide_csv("s1.csv", dur=4.0)
        b = self.wide_csv("s2.csv", dur=4.0)
        rc, out, _ = _run([a, b, "--channels", "Fp1,Cz", "--csv"])
        self.assertEqual(rc, 0)
        labels = [r[0] for r in _rows(out)[1:]]
        self.assertEqual(labels, ["s1:Fp1", "s1:Cz", "s2:Fp1", "s2:Cz"])

    def test_partial_failure_keeps_good_series_and_exits_1(self):
        good = self.wide_csv("good.csv", dur=4.0)
        rc, out, err = _run([good, self.path("missing.csv"), "--channels", "Fp1"])
        self.assertEqual(rc, 1)
        self.assertIn("입력 오류", err)
        self.assertIn("missing.csv", err)
        self.assertIn("[1] 대역파워", out)

    def test_all_inputs_failing_exits_2(self):
        rc, out, err = _run([self.path("a.csv"), self.path("b.csv")])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")

    def test_list_channels_csv_and_edf(self):
        p = self.wide_csv()
        e = self.path("n.edf")
        write_edf(e, [("Fp1", "uV", sine(FS, 4, 10.0)),
                      ("EDF Annotations", "", [0.0] * int(FS * 4))], FS)
        rc, out, _ = _run([p, e, "--list-channels"])
        self.assertEqual(rc, 0)
        self.assertIn("Fp1", out)
        self.assertIn("time column", out)
        self.assertIn("annotation", out)
        self.assertIn("EDF", out)
        self.assertNotIn("[1] 대역파워", out)      # no analysis was run

    def test_list_channels_missing_file_exits_2(self):
        rc, _, err = _run([self.path("nope.csv"), "--list-channels"])
        self.assertEqual(rc, 2)
        self.assertIn("입력 오류", err)


class TestCliEdf(TempFiles):
    def _edf(self, name="rec.edf", dur=16.0):
        p = self.path(name)
        write_edf(p, [("Fp1", "uV", sine(FS, dur, 10.0, 20.0)),
                      ("Cz", "uV", sine(FS, dur, 1.5, 60.0))], FS,
                  phys_range=(-60.0, 60.0))
        return p

    def test_edf_is_detected_and_fs_comes_from_the_header(self):
        p = self._edf()
        rc, out, _ = _run([p, "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["fs_hz"], FS)
        self.assertEqual(d["fs_source"], "edf header")
        self.assertEqual(d["label"], "Fp1")
        self.assertEqual(d["overall"]["dominant_band"], "alpha")

    def test_edf_without_extension_is_detected_by_magic(self):
        p = self._edf("recording_no_ext")
        rc, out, _ = _run([p, "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["fs_source"], "edf header")

    def test_edf_fs_mismatch_warns(self):
        p = self._edf()
        rc, out, _ = _run([p, "--fs", "256", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["fs_hz"], FS)
        self.assertTrue(any("EDF header" in w for w in d["warnings"]))

    def test_edf_channel_selection_and_bad_name(self):
        p = self._edf()
        rc, out, _ = _run([p, "--channels", "Cz", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["overall"]["dominant_band"], "delta")
        rc, _, err = _run([p, "--channels", "Pz"])
        self.assertEqual(rc, 2)
        self.assertIn("Pz", err)

    def test_edf_value_time_options_are_reported_as_ignored(self):
        p = self._edf()
        rc, out, _ = _run([p, "--time", "time_s", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(any("ignored" in w for w in json.loads(out)["warnings"]))

    def test_edf_window(self):
        p = self._edf(dur=16.0)
        rc, out, _ = _run([p, "--start", "4", "--duration", "8", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["n_samples"], int(8 * FS))
        self.assertAlmostEqual(d["duration_sec"], 8.0, places=6)

    def test_edf_start_beyond_end_exits_2(self):
        p = self._edf(dur=8.0)
        rc, _, err = _run([p, "--start", "60"])
        self.assertEqual(rc, 2)
        self.assertIn("입력 오류", err)


class TestCliWindowing(TempFiles):
    def _csv(self, dur=16.0):
        n = int(FS * dur)
        body = "\n".join(f"{20 * math.sin(2 * math.pi * 10 * k / FS)}"
                         for k in range(n))
        return self.write("w.csv", "eeg_uv\n" + body + "\n")

    def test_csv_window_crops_samples(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--start", "2", "--duration", "4",
                           "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["n_samples"], int(4 * FS))
        self.assertTrue(any("analysed samples" in w for w in d["warnings"]))

    def test_duration_only(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--duration", "3", "--json"])
        self.assertEqual(json.loads(out)["n_samples"], int(3 * FS))
        self.assertEqual(rc, 0)

    def test_window_too_short_exits_2(self):
        p = self._csv()
        rc, _, err = _run([p, "--fs", "128", "--start", "1", "--duration", "0.005"])
        self.assertEqual(rc, 2)
        self.assertIn("입력 오류", err)

    def test_negative_start_and_zero_duration_rejected(self):
        p = self._csv()
        self.assertEqual(_run([p, "--fs", "128", "--start", "-1"])[0], 2)
        self.assertEqual(_run([p, "--fs", "128", "--duration", "0"])[0], 2)


class TestCliAperiodicOptions(TempFiles):
    def _csv(self):
        n = 2048
        body = "\n".join(
            f"{20 * math.sin(2 * math.pi * 10 * k / FS) + 5 * math.sin(2 * math.pi * 1.5 * k / FS)}"
            for k in range(n))
        return self.write("ap.csv", "eeg_uv\n" + body + "\n")

    def test_aperiodic_on_by_default(self):
        rc, out, _ = _run([self._csv(), "--fs", "128", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["provenance"]["aperiodic_mode"], "robust")
        ap = d["overall"]["aperiodic"]
        self.assertIsNotNone(ap)
        self.assertIn("exponent", ap)
        self.assertEqual(ap["mode"], "robust")
        self.assertIn("aperiodic", out)

    def test_aperiodic_off(self):
        rc, out, _ = _run([self._csv(), "--fs", "128", "--json",
                           "--aperiodic", "off"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertIsNone(d["provenance"]["aperiodic_mode"])
        self.assertIsNone(d["overall"]["aperiodic"])
        for bp in d["overall"]["band_power"]:
            self.assertIsNone(bp["oscillatory_uv2"])

    def test_aperiodic_off_text_and_csv_drop_the_columns(self):
        p = self._csv()
        rc, out, _ = _run([p, "--fs", "128", "--aperiodic", "off"])
        self.assertEqual(rc, 0)
        self.assertNotIn("비주기", out)
        rc, out, _ = _run([p, "--fs", "128", "--aperiodic", "off", "--csv"])
        header = _rows(out)[0]
        self.assertNotIn("ap_exponent", header)
        self.assertNotIn("delta_osc_uv2", header)

    def test_fit_range_is_respected(self):
        rc, out, _ = _run([self._csv(), "--fs", "128", "--fit-range", "2-40",
                           "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["provenance"]["aperiodic_fit_range_hz"], [2.0, 40.0])
        ap = d["overall"]["aperiodic"]
        self.assertGreaterEqual(ap["fit_lo_hz"], 2.0)
        self.assertLessEqual(ap["fit_hi_hz"], 40.0)

    def test_bad_fit_range_exits_2(self):
        p = self._csv()
        for bad in ("40-2", "0-40", "abc", "2:40", "2-40-9"):
            rc, _, err = _run([p, "--fs", "128", "--fit-range", bad])
            self.assertEqual(rc, 2, bad)
            self.assertIn("입력 오류", err)

    def test_fit_range_with_aperiodic_off_is_an_error(self):
        rc, _, err = _run([self._csv(), "--fs", "128", "--aperiodic", "off",
                           "--fit-range", "2-40"])
        self.assertEqual(rc, 2)
        self.assertIn("입력 오류", err)

    def test_ols_mode(self):
        rc, out, _ = _run([self._csv(), "--fs", "128", "--aperiodic", "ols",
                           "--json"])
        d = json.loads(out)
        self.assertEqual(d["overall"]["aperiodic"]["mode"], "ols")
        self.assertEqual(d["overall"]["aperiodic"]["n_bins_used"],
                         d["overall"]["aperiodic"]["n_bins_total"])


class TestCliGradientRejection(TempFiles):
    def _spiky_csv(self):
        """4 epochs of clean 10 Hz alpha; epoch 2 gets a one-sample glitch."""
        n = int(FS * 8)
        vals = [20 * math.sin(2 * math.pi * 10 * k / FS) for k in range(n)]
        vals[int(FS * 4) + 10] += 80.0        # a sharp step, |amp| stays < 150
        body = "\n".join(f"{v}" for v in vals)
        return self.write("spike.csv", "eeg_uv\n" + body + "\n")

    def test_gradient_rejects_the_spiky_epoch(self):
        p = self._spiky_csv()
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "2", "--max-grad", "30",
                           "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        rej = [ep for ep in d["epochs"] if ep["rejected"]]
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["index"], 2)
        self.assertIn("Δamp", rej[0]["reject_reason"])
        self.assertEqual(d["artifact_rejection"]["max_gradient_uv"], 30.0)
        self.assertEqual(d["artifact_rejection"]["n_kept"], 3)
        self.assertEqual(d["epoch_summary"]["n"], 3)

    def test_amp_limit_alone_misses_it(self):
        p = self._spiky_csv()
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "2", "--max-amp", "150",
                           "--json"])
        d = json.loads(out)
        self.assertEqual([ep["rejected"] for ep in d["epochs"]], [False] * 4)

    def test_both_criteria_are_reported(self):
        p = self._spiky_csv()
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "2", "--max-amp", "50",
                           "--max-grad", "30"])
        self.assertEqual(rc, 0)
        self.assertIn("✗REJ", out)
        self.assertIn("|Δ|", out)

    def test_csv_columns_appear_only_with_epochs(self):
        p = self._spiky_csv()
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "2", "--max-grad", "30",
                           "--csv"])
        header = _rows(out)[0]
        self.assertIn("max_grad_uv", header)
        self.assertIn("rejected", header)
        rc, out, _ = _run([p, "--fs", "128", "--max-grad", "30", "--csv"])
        header = _rows(out)[0]
        self.assertNotIn("max_grad_uv", header)


class TestCliTrendOutput(TempFiles):
    def test_declining_swa_trend_is_reported(self):
        """Amplitude decaying across epochs must produce a negative, significant
        Mann–Kendall trend for absolute SWA."""
        n_ep, ep_len = 8, int(FS * 4)
        vals = []
        for e in range(n_ep):
            amp = 60.0 * (0.85 ** e)
            vals += [amp * math.sin(2 * math.pi * 1.5 * k / FS)
                     for k in range(ep_len)]
        p = self.write("decay.csv", "eeg_uv\n" + "\n".join(f"{v}" for v in vals))
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "4", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        tr = d["epoch_summary"]["trends"]["swa_absolute_uv2"]
        self.assertLess(tr["theil_sen_slope_per_sec"], 0.0)
        self.assertLess(tr["p_two_sided"], 0.01)
        self.assertAlmostEqual(tr["kendall_tau_b"], -1.0, places=9)
        self.assertEqual(tr["x_unit"], "sec")
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "4"])
        self.assertIn("시간 추세", out)
        self.assertIn("Mann–Kendall", out)

    def test_summary_has_autocorrelation_fields(self):
        n_ep, ep_len = 6, int(FS * 4)
        vals = []
        for e in range(n_ep):
            amp = 50.0 + 2.0 * e
            vals += [amp * math.sin(2 * math.pi * 1.5 * k / FS)
                     for k in range(ep_len)]
        p = self.write("ac.csv", "eeg_uv\n" + "\n".join(f"{v}" for v in vals))
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "4", "--json"])
        es = json.loads(out)["epoch_summary"]["swa_absolute_uv2"]
        for key in ("rho1", "n_eff", "sem_adj", "ci_lo_adj", "ci_hi_adj"):
            self.assertIn(key, es)
        self.assertLessEqual(es["n_eff"], es["n"])
        self.assertLessEqual(es["ci_lo_adj"], es["ci_lo"] + 1e-9)

    def test_epoch_exponent_summary_present(self):
        n = 4096
        body = "\n".join(f"{20 * math.sin(2 * math.pi * 10 * k / FS)}"
                         for k in range(n))
        p = self.write("x.csv", "eeg_uv\n" + body + "\n")
        rc, out, _ = _run([p, "--fs", "128", "--epoch", "8", "--json"])
        d = json.loads(out)
        self.assertIn("aperiodic_exponent", d["epoch_summary"])
        self.assertEqual(d["epoch_summary"]["aperiodic_exponent"]["n"], 4)


if __name__ == "__main__":
    unittest.main()
