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

    def test_non_increasing_time_exit_2(self):
        # Regression: resolve_fs raised ValueError outside the try/except -> crash.
        with _TmpCSV("time_s,eeg_uv\n0.0,1\n0.0,2\n0.0,3\n") as p:
            code, _, err = _run([p])
            self.assertEqual(code, 2)
            self.assertIn("입력 오류", err)

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
            self.assertTrue(lines[1].startswith("overall,"))

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


if __name__ == "__main__":
    unittest.main()
