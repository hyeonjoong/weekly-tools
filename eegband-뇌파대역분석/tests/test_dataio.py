"""CSV loading, column auto-detection, NaN interpolation and fs inference."""

import os
import tempfile
import unittest

from eegband.dataio import infer_fs, load_signal, parse_float


class _TmpCSV:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.text)
        return self.path

    def __exit__(self, *exc):
        os.remove(self.path)


class TestParseFloat(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_float(" 1.5 "), 1.5)
        self.assertIsNone(parse_float(""))
        self.assertIsNone(parse_float("NA"))
        self.assertIsNone(parse_float("nan"))
        self.assertIsNone(parse_float("inf"))
        self.assertIsNone(parse_float("abc"))


class TestLoadSignal(unittest.TestCase):
    def test_value_only(self):
        with _TmpCSV("eeg_uv\n1.0\n2.0\n3.0\n") as p:
            sig = load_signal(p)
            self.assertEqual(sig.values, [1.0, 2.0, 3.0])
            self.assertIsNone(sig.times)
            self.assertEqual(sig.value_col, "eeg_uv")

    def test_value_and_time_autodetect(self):
        with _TmpCSV("time_s,eeg_uv\n0.0,10\n0.5,11\n1.0,12\n") as p:
            sig = load_signal(p)
            self.assertEqual(sig.value_col, "eeg_uv")
            self.assertEqual(sig.time_col, "time_s")
            self.assertEqual(sig.times, [0.0, 0.5, 1.0])

    def test_explicit_columns(self):
        with _TmpCSV("a,b\n1,10\n2,20\n") as p:
            sig = load_signal(p, value_col="b", time_col="a")
            self.assertEqual(sig.values, [10.0, 20.0])
            self.assertEqual(sig.times, [1.0, 2.0])

    def test_nan_interpolation(self):
        # NA markers denote missing samples (a fully-blank row is instead skipped,
        # matching the reference tool's trailing-newline robustness).
        with _TmpCSV("eeg_uv\n0\nNA\nNA\n3\n") as p:
            sig = load_signal(p)
            # gaps at index 1,2 linearly filled between 0 and 3 -> 1,2
            self.assertEqual(sig.n_filled, 2)
            self.assertAlmostEqual(sig.values[1], 1.0)
            self.assertAlmostEqual(sig.values[2], 2.0)

    def test_leading_trailing_gap_filled_with_nearest(self):
        with _TmpCSV("eeg_uv\nNA\n5\n7\nNA\n") as p:
            sig = load_signal(p)
            self.assertEqual(sig.values[0], 5.0)     # leading -> nearest
            self.assertEqual(sig.values[-1], 7.0)    # trailing -> nearest

    def test_numeric_header_rejected(self):
        with _TmpCSV("1.0\n2.0\n3.0\n") as p:
            with self.assertRaises(ValueError):
                load_signal(p)

    def test_empty_file(self):
        with _TmpCSV("") as p:
            with self.assertRaises(ValueError):
                load_signal(p)

    def test_ambiguous_columns_need_value(self):
        with _TmpCSV("x,y,z\n1,2,3\n4,5,6\n") as p:
            with self.assertRaises(ValueError):
                load_signal(p)              # no time-like name, 3 candidates


class TestInferFs(unittest.TestCase):
    def test_regular(self):
        times = [k / 128.0 for k in range(100)]
        fs, regular, _ = infer_fs(times)
        self.assertAlmostEqual(fs, 128.0, places=6)
        self.assertTrue(regular)

    def test_irregular_flagged(self):
        times = [0.0, 0.1, 0.25, 0.3, 0.7]
        fs, regular, _ = infer_fs(times)
        self.assertFalse(regular)

    def test_non_increasing(self):
        with self.assertRaises(ValueError):
            infer_fs([1.0, 1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
