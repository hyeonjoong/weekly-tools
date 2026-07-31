"""CLI-level behavior: exit codes, output formats, and graceful error handling.

Exercises eegband.cli.main(argv) directly (no subprocess needed) so exit codes,
stderr messages, --json/--csv formats, and the Round-1 crash fixes are all locked in.
"""

import io
import json
import math
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from eegband.cli import main


class _TmpCSV:
    def __init__(self, text, encoding="utf-8"):
        self.text = text
        self.encoding = encoding

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.text.encode(self.encoding))
        return self.path

    def __exit__(self, *exc):
        os.remove(self.path)


def _sine_csv(fs, dur, f, amp, header="eeg_uv"):
    n = int(round(fs * dur))
    body = "\n".join(f"{amp * math.sin(2 * math.pi * f * k / fs)}" for k in range(n))
    return f"{header}\n{body}\n"


def _run(argv):
    """Run main(argv), capturing (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestExitCodes(unittest.TestCase):
    def test_success_exit_0(self):
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            code, out, _ = _run([p, "--fs", "128"])
            self.assertEqual(code, 0)
            self.assertIn("Band power", out)

    def test_missing_file_exit_2(self):
        code, _, err = _run(["/no/such/file_xyz.csv", "--fs", "128"])
        self.assertEqual(code, 2)
        self.assertIn("입력 오류", err)

    def test_directory_path_exit_2(self):
        # Regression: IsADirectoryError must be caught, not crash.
        with tempfile.TemporaryDirectory() as d:
            code, _, err = _run([d, "--fs", "128"])
            self.assertEqual(code, 2)
            self.assertIn("입력 오류", err)

    def test_bad_sef_exit_2(self):
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            self.assertEqual(_run([p, "--fs", "128", "--sef", "150"])[0], 2)
            self.assertEqual(_run([p, "--fs", "128", "--sef", "0"])[0], 2)

    def test_bad_bands_exit_2(self):
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            code, _, err = _run([p, "--fs", "128", "--bands", "junk"])
            self.assertEqual(code, 2)
            self.assertIn("입력 오류", err)

    def test_non_increasing_time_falls_back_to_fs(self):
        # Regression: resolve_fs raised ValueError outside the try/except -> crash.
        # Now the unusable time column is ignored and the analysis proceeds.
        body = "\n".join("0.0,%f" % (5 * math.sin(2 * math.pi * 10 * k / 128.0))
                          for k in range(512))
        with _TmpCSV("time_s,eeg_uv\n" + body + "\n") as p:
            code, out, err = _run([p, "--fs", "128"])
            self.assertEqual(code, 0)
            self.assertIn("unusable", out)
            self.assertIn("alpha", out)

    def test_json_and_csv_mutually_exclusive(self):
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            self.assertEqual(_run([p, "--fs", "128", "--json", "--csv"])[0], 2)

    def test_no_numeric_values_hint(self):
        # A value column with no parseable numbers gives a delimiter/encoding hint.
        with _TmpCSV("eeg_uv\napple\nbanana\ncherry\n") as p:
            code, _, err = _run([p, "--fs", "128"])
            self.assertEqual(code, 2)
            self.assertIn("no numeric values", err)
            self.assertIn("delimiter", err)


class TestNonUtf8(unittest.TestCase):
    def test_cp949_decodes_not_crash(self):
        # Regression: a non-UTF8 file must not raise UnicodeDecodeError uncaught.
        with _TmpCSV(_sine_csv(128.0, 8.0, 1.5, 10.0), encoding="cp949") as p:
            code, out, _ = _run([p, "--fs", "128"])
            self.assertEqual(code, 0)
            self.assertIn("Band power", out)


class TestJsonOutput(unittest.TestCase):
    def test_json_schema_and_nonfinite_null(self):
        # Constant signal -> ratios are NaN/inf -> must serialize to JSON null.
        with _TmpCSV("eeg_uv\n" + "3.0\n" * 512) as p:
            code, out, _ = _run([p, "--fs", "128", "--json"])
            self.assertEqual(code, 0)
            d = json.loads(out)
            for key in ("tool", "version", "fs_hz", "welch", "bands", "overall",
                        "warnings"):
                self.assertIn(key, d)
            self.assertEqual(d["tool"], "eegband")
            for v in d["overall"]["ratios"].values():
                self.assertTrue(v is None or isinstance(v, (int, float)))

    def test_json_epochs_present(self):
        with _TmpCSV(_sine_csv(128.0, 40.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10", "--json"])
            d = json.loads(out)
            self.assertEqual(len(d["epochs"]), 4)
            self.assertIn("relative_sum", d["overall"])

    def test_json_provenance_block(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--sef", "90", "--json"])
            prov = json.loads(out)["provenance"]
            self.assertEqual(prov["sef_percent"], 90.0)
            self.assertEqual(prov["n_interpolated_samples"], 0)
            self.assertIn("input_encoding", prov)


class TestCsvProvenance(unittest.TestCase):
    def test_comment_is_single_field(self):
        # Regression: provenance must be ONE csv field, not four fake headers.
        import csv as _csv
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--csv"])
            first = next(_csv.reader(io.StringIO(out)))
            self.assertEqual(len(first), 1)
            self.assertTrue(first[0].startswith("# eegband"))
            self.assertIn("nperseg=", first[0])
            self.assertIn("bands=", first[0])


class TestCsvOutput(unittest.TestCase):
    def test_csv_overall_one_row(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            code, out, _ = _run([p, "--fs", "128", "--csv"])
            self.assertEqual(code, 0)
            lines = [l for l in out.splitlines() if l and not l.startswith("#")]
            self.assertEqual(len(lines), 2)          # header + one overall row
            self.assertIn("delta_abs_uv2", lines[0])
            # series/source_file columns are ALWAYS present so that appending several
            # single-series exports into one file stays unambiguous
            self.assertTrue(lines[0].startswith("series,source_file,epoch,"))
            self.assertIn(",overall,", lines[1])

    def test_csv_epochs_rows(self):
        with _TmpCSV(_sine_csv(128.0, 40.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10", "--csv"])
            data = [l for l in out.splitlines() if l and not l.startswith("#")]
            self.assertEqual(len(data), 1 + 4)       # header + 4 epochs
            # every data row parses and delta_rel is a finite number in [0,1]
            import csv as _csv
            rows = list(_csv.DictReader(data))
            for r in rows:
                self.assertTrue(0.0 <= float(r["delta_rel"]) <= 1.0)


class TestNewCliOptions(unittest.TestCase):
    def test_detrend_and_average_flow(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 10.0, 5.0)) as p:
            code, out, _ = _run([p, "--fs", "128", "--detrend", "linear",
                                 "--average", "median"])
            self.assertEqual(code, 0)
            self.assertIn("detrend = linear, average = median", out)

    def test_bad_detrend_choice_exit_2(self):
        # argparse rejects invalid choices by raising SystemExit(2).
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            for bad in (["--detrend", "cubic"], ["--average", "max"]):
                with self.assertRaises(SystemExit) as cm:
                    _run([p, "--fs", "128", *bad])
                self.assertEqual(cm.exception.code, 2)

    def test_signal_quality_section_rendered(self):
        with _TmpCSV(_sine_csv(128.0, 10.0, 10.0, 5.0)) as p:
            _, out, _ = _run([p, "--fs", "128"])
            self.assertIn("신호 품질", out)
            self.assertIn("RMS", out)
            self.assertIn("clipped(rail)", out)

    def test_entropy_and_iaf_in_summary(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 10.0, 20.0)) as p:
            _, out, _ = _run([p, "--fs", "128"])
            self.assertIn("spectral entropy", out)
            self.assertIn("alpha peak (IAF)", out)

    def test_json_has_quality_entropy_bandpeak(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 10.0, 20.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--json"])
            d = json.loads(out)
            self.assertIn("signal_quality", d)
            self.assertIn("n_clipped", d["signal_quality"])
            self.assertIsNotNone(d["overall"]["spectral_entropy"])
            self.assertIn("peak_freq_hz", d["overall"]["band_power"][0])
            self.assertEqual(d["welch"]["detrend"], "constant")

    def test_json_epoch_summary_ci(self):
        with _TmpCSV(_sine_csv(128.0, 40.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10", "--json"])
            d = json.loads(out)
            es = d["epoch_summary"]["swa_relative"]
            self.assertLessEqual(es["ci_lo"], es["mean"])
            self.assertLessEqual(es["mean"], es["ci_hi"])
            self.assertIn("median", es)
            self.assertIn("q1", es)

    def test_csv_has_entropy_and_bandpeak_columns(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--csv"])
            header = [l for l in out.splitlines() if l and not l.startswith("#")][0]
            self.assertIn("entropy", header)
            self.assertIn("delta_peak_hz", header)
            self.assertIn("slowing_ratio", header)
            self.assertIn("theta_alpha_ratio", header)
            # provenance carries detrend/average now
            first = out.splitlines()[0]
            self.assertIn("detrend=constant", first)
            self.assertIn("average=mean", first)


def _multi_epoch_artifact_csv():
    """5 epochs of 10 s @128 Hz delta; epoch 2 has a huge artifact."""
    import math as _m
    rows = ["eeg_uv"]
    for e in range(5):
        for k in range(1280):
            v = 40 * _m.sin(2 * _m.pi * 1.5 * k / 128)
            if e == 2:
                v += 500
            rows.append(f"{v}")
    return "\n".join(rows) + "\n"


class TestArtifactRejectionCli(unittest.TestCase):
    def test_max_amp_rejects_epoch_text(self):
        with _TmpCSV(_multi_epoch_artifact_csv()) as p:
            code, out, _ = _run([p, "--fs", "128", "--epoch", "10",
                                 "--max-amp", "150"])
            self.assertEqual(code, 0)
            self.assertIn("✗REJ", out)
            self.assertIn("artifact rejection", out)
            self.assertIn("kept 4/5", out)

    def test_max_amp_json(self):
        with _TmpCSV(_multi_epoch_artifact_csv()) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10",
                              "--max-amp", "150", "--json"])
            d = json.loads(out)
            self.assertEqual(d["artifact_rejection"]["n_rejected"], 1)
            self.assertEqual(d["artifact_rejection"]["n_kept"], 4)
            self.assertTrue(d["epochs"][2]["rejected"])
            self.assertEqual(d["epoch_summary"]["n"], 4)
            self.assertIn("within-recording", d["epoch_summary"]["note"])

    def test_max_amp_csv_columns(self):
        with _TmpCSV(_multi_epoch_artifact_csv()) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10",
                              "--max-amp", "150", "--csv"])
            data = [l for l in out.splitlines() if l and not l.startswith("#")]
            self.assertIn("peak_amp_uv", data[0])
            self.assertIn("rejected", data[0])
            import csv as _csv
            rows = list(_csv.DictReader(data))
            self.assertEqual(rows[2]["rejected"], "1")
            self.assertEqual(rows[0]["rejected"], "0")


class TestNoComment(unittest.TestCase):
    def test_no_comment_omits_provenance_line(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--csv", "--no-comment"])
            self.assertFalse(out.startswith("#"))
            # first line is the header, directly parseable by base-R/SAS
            self.assertTrue(out.splitlines()[0].startswith("series,source_file,"))

    def test_comment_present_by_default(self):
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--csv"])
            self.assertTrue(out.startswith("# eegband"))

    def test_max_amp_without_epoch_no_rej_columns(self):
        # --max-amp without --epoch is a no-op; the overall CSV must NOT gain empty
        # peak_amp_uv/rejected columns.
        with _TmpCSV(_sine_csv(128.0, 20.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--max-amp", "150", "--csv"])
            header = [l for l in out.splitlines() if l and not l.startswith("#")][0]
            self.assertNotIn("peak_amp_uv", header)
            self.assertNotIn("rejected", header)


class TestManyEpochCI(unittest.TestCase):
    def test_df_over_30_ci_reasonable(self):
        # 40 epochs (df=39) -> t-crit ~2.02, CI must be finite and ordered, and
        # the df>30 Cornish-Fisher path must NOT collapse to 1.96 discontinuously.
        with _TmpCSV(_sine_csv(128.0, 400.0, 1.5, 40.0)) as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "10", "--json"])
            d = json.loads(out)
            self.assertEqual(len(d["epochs"]), 40)
            es = d["epoch_summary"]["swa_relative"]
            self.assertLessEqual(es["ci_lo"], es["mean"])
            self.assertLessEqual(es["mean"], es["ci_hi"])


class TestEpochConstantPeakNa(unittest.TestCase):
    def test_constant_epoch_shows_na_not_zero(self):
        # first 2 s constant, rest a sine -> first epoch has no power -> n/a not 0.00
        import math as _m
        rows = ["eeg_uv"] + ["3.0"] * 256 + [
            f"{5 * _m.sin(2 * _m.pi * 10 * k / 128)}" for k in range(256)]
        with _TmpCSV("\n".join(rows) + "\n") as p:
            _, out, _ = _run([p, "--fs", "128", "--epoch", "2"])
            # the constant epoch row should contain n/a for peak & SEF
            ep_lines = [l for l in out.splitlines() if l.strip().startswith("0")
                        and "delta" not in l]
            self.assertTrue(any("n/a" in l for l in ep_lines))




def _pd_csv(fs=128.0, seconds=240.0, switch=120.0, mains=0.0, mains_f=60.0,
            seed=3):
    """CSV of a recording whose slow-wave amplitude doubles at ``switch`` seconds."""
    import random
    rng = random.Random(seed)
    rows = ["eeg_uv"]
    for i in range(int(seconds * fs)):
        t = i / fs
        amp = 15.0 if t < switch else 30.0
        v = (amp * math.sin(2 * math.pi * 1.5 * t + 0.7)
             + 10.0 * math.sin(2 * math.pi * 10.0 * t)
             + 5.0 * rng.gauss(0.0, 1.0))
        if mains:
            v += mains * math.sin(2 * math.pi * mains_f * t + 0.3)
        rows.append(f"{v:.5f}")
    return "\n".join(rows) + "\n"


class TestLineNoiseCLI(unittest.TestCase):
    def test_default_run_reports_the_line_noise_check(self):
        with _TmpCSV(_pd_csv(mains=20.0)) as path:
            code, out, _ = _run([path, "--fs", "128"])
        self.assertEqual(code, 0)
        self.assertIn("mains line noise", out)

    def test_notch_removes_it_and_says_so(self):
        with _TmpCSV(_pd_csv(mains=20.0)) as path:
            code, out, _ = _run([path, "--fs", "128", "--notch",
                                 "--bands", "delta:0.5-4,gamma:30-63"])
            self.assertEqual(code, 0)
            self.assertIn("REMOVED", out)
            code2, out2, _ = _run([path, "--fs", "128",
                                   "--bands", "delta:0.5-4,gamma:30-63"])
        # Gamma must be much smaller once the 60 Hz peak is gone.
        def gamma(text):
            for line in text.splitlines():
                if line.strip().startswith("gamma"):
                    return float(line.split()[2])
            raise AssertionError("no gamma row")
        self.assertLess(gamma(out), 0.5 * gamma(out2))

    def test_line_freq_off_disables_the_section(self):
        with _TmpCSV(_pd_csv(mains=20.0)) as path:
            code, out, _ = _run([path, "--fs", "128", "--line-freq", "off"])
        self.assertEqual(code, 0)
        self.assertNotIn("mains line noise", out)

    def test_explicit_line_freq_is_used(self):
        with _TmpCSV(_pd_csv(mains=20.0, mains_f=50.0)) as path:
            code, out, _ = _run([path, "--fs", "128", "--line-freq", "50",
                                 "--json"])
        self.assertEqual(code, 0)
        blk = json.loads(out)["overall"]["line_noise"]
        self.assertEqual(blk["fundamental_hz"], 50.0)
        self.assertEqual(blk["source"], "user")
        self.assertTrue(blk["detected"])

    def test_bad_line_freq_is_a_usage_error(self):
        with _TmpCSV(_pd_csv(seconds=20.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--line-freq", "sixty"])
        self.assertEqual(code, 2)
        self.assertIn("--line-freq", err)

    def test_notch_with_line_freq_off_is_rejected(self):
        with _TmpCSV(_pd_csv(seconds=20.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--line-freq", "off",
                                 "--notch"])
        self.assertEqual(code, 2)
        self.assertIn("--notch", err)

    def test_bad_line_bw_is_a_usage_error(self):
        with _TmpCSV(_pd_csv(seconds=20.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--line-bw", "0"])
            self.assertEqual(code, 2)
            code2, _, err2 = _run([path, "--fs", "128", "--line-bw", "nan"])
        self.assertEqual(code2, 2)
        self.assertIn("--line-bw", err + err2)

    def test_psd_csv_export_matches_the_notched_report(self):
        with _TmpCSV(_pd_csv(mains=20.0, seconds=60.0)) as path:
            code, out, _ = _run([path, "--fs", "128", "--notch", "--psd-csv",
                                 "--no-comment"])
        self.assertEqual(code, 0)
        rows = [r.split(",") for r in out.strip().splitlines()[1:]]
        at60 = [float(r[3]) for r in rows if abs(float(r[2]) - 60.0) < 0.4]
        near56 = [float(r[3]) for r in rows if 55.0 <= float(r[2]) <= 56.5]
        self.assertTrue(at60 and near56)
        # After notching, the 60 Hz bins are interpolated background, not a spike.
        self.assertLess(max(at60), 10.0 * max(near56))


class TestBaselineCLI(unittest.TestCase):
    def test_baseline_section_appears(self):
        with _TmpCSV(_pd_csv()) as path:
            code, out, _ = _run([path, "--fs", "128", "--epoch", "30",
                                 "--baseline", "120"])
        self.assertEqual(code, 0)
        self.assertIn("기저 대비 변화", out)
        self.assertIn("q(FDR)", out)

    def test_baseline_json_block(self):
        with _TmpCSV(_pd_csv()) as path:
            code, out, _ = _run([path, "--fs", "128", "--epoch", "30",
                                 "--baseline", "120", "--json"])
        self.assertEqual(code, 0)
        blk = json.loads(out)["baseline_contrast"]
        self.assertEqual(blk["n_baseline"], 4)
        self.assertLess(blk["endpoints"]["swa_absolute_uv2"]["q_bh_fdr"], 0.05)

    def test_baseline_without_epoch_is_a_usage_error(self):
        with _TmpCSV(_pd_csv(seconds=60.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--baseline", "20"])
        self.assertEqual(code, 2)
        self.assertIn("--baseline", err)

    def test_non_positive_baseline_is_a_usage_error(self):
        with _TmpCSV(_pd_csv(seconds=60.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--epoch", "10",
                                 "--baseline", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--baseline", err)

    def test_non_finite_baseline_is_a_usage_error(self):
        with _TmpCSV(_pd_csv(seconds=60.0)) as path:
            code, _, err = _run([path, "--fs", "128", "--epoch", "10",
                                 "--baseline", "inf"])
        self.assertEqual(code, 2)
        self.assertIn("baseline", err)

    def test_csv_summary_carries_the_contrast(self):
        with _TmpCSV(_pd_csv()) as path:
            code, out, _ = _run([path, "--fs", "128", "--epoch", "30",
                                 "--baseline", "120", "--csv-summary",
                                 "--no-comment"])
        self.assertEqual(code, 0)
        head = out.splitlines()[0].split(",")
        row = out.splitlines()[1].split(",")
        self.assertEqual(len(head), len(row))
        i = head.index("swa_absolute_uv2_base_pct_change")
        self.assertGreater(float(row[i]), 100.0)


if __name__ == "__main__":
    unittest.main()
