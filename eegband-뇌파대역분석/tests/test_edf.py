"""EDF / EDF+ / BDF reading: header parsing, calibration, cropping, PHI hygiene.

Files are written by ``tests/edf_fixtures.write_edf`` (an independent implementation
of the format) so a reader bug cannot be hidden by a matching writer bug. The
round-trip tolerance is the quantisation step of the declared physical range, computed
from first principles.
"""

import math
import os
import shutil
import tempfile
import unittest

from eegband.analyze import analyze
from eegband.edf import (
    is_edf_path,
    looks_like_edf,
    read_edf_channel,
    read_edf_info,
)

from edf_fixtures import sine, write_edf

FS = 128.0


class EdfTempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="eegband-edf-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)


class TestHeader(EdfTempDir):
    def test_reads_technical_header(self):
        p = self.path("a.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 8, 10.0, 20.0)),
                      ("Cz", "uV", sine(FS, 8, 2.0, 40.0))], FS)
        info = read_edf_info(p)
        self.assertEqual(info.kind, "EDF")
        self.assertEqual(info.bytes_per_sample, 2)
        self.assertEqual(info.n_records, 8)
        self.assertEqual(info.record_duration, 1.0)
        self.assertEqual(info.duration_sec, 8.0)
        self.assertTrue(info.continuous)
        self.assertEqual([s.label for s in info.signals], ["Fp1", "Cz"])
        self.assertEqual([s.fs for s in info.signals], [FS, FS])
        self.assertEqual(info.header_bytes, 256 + 2 * 256)

    def test_no_patient_identification_is_exposed(self):
        """PHI in the header must never reach any object the reader returns."""
        p = self.path("phi.edf")
        secret = "PT12345 M 01-JAN-1980 KIM_HYEONJOONG"
        write_edf(p, [("Fp1", "uV", sine(FS, 4, 10.0))], FS,
                  patient=secret, recording="Startdate 02-FEB-2026 CLINIC_A")
        info = read_edf_info(p)
        blob = repr(info)
        for token in ("PT12345", "KIM_HYEONJOONG", "1980", "CLINIC_A", "02-FEB"):
            self.assertNotIn(token, blob)
        sig, _, _ = read_edf_channel(p, info=info)
        self.assertNotIn("PT12345", repr(sig.warnings) + repr(sig.value_col))

    def test_annotation_channel_is_flagged_and_excluded(self):
        p = self.path("annot.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 4, 10.0)),
                      ("EDF Annotations", "", [0.0] * int(FS * 4))], FS)
        info = read_edf_info(p)
        self.assertTrue(info.signals[1].is_annotation)
        self.assertEqual([s.label for s in info.data_signals], ["Fp1"])
        with self.assertRaises(ValueError):
            read_edf_channel(p, "EDF Annotations")

    def test_find_by_case_and_substring(self):
        p = self.path("find.edf")
        write_edf(p, [("EEG Fpz-Cz", "uV", sine(FS, 4, 10.0)),
                      ("EOG horizontal", "uV", sine(FS, 4, 1.0))], FS)
        info = read_edf_info(p)
        self.assertEqual(info.find("EEG Fpz-Cz").index, 0)
        self.assertEqual(info.find("eeg fpz-cz").index, 0)
        self.assertEqual(info.find("Fpz").index, 0)          # unique substring
        self.assertIsNone(info.find("nope"))

    def test_rejects_non_edf_files(self):
        p = self.path("plain.csv")
        with open(p, "w") as fh:
            fh.write("eeg_uv\n1.0\n2.0\n")
        self.assertFalse(looks_like_edf(p))
        with self.assertRaises(ValueError):
            read_edf_info(p)
        short = self.path("short.edf")
        with open(short, "wb") as fh:
            fh.write(b"0" + b" " * 20)
        with self.assertRaises(ValueError):
            read_edf_info(short)

    def test_path_and_magic_detection(self):
        self.assertTrue(is_edf_path("night.EDF"))
        self.assertTrue(is_edf_path("a.bdf"))
        self.assertTrue(is_edf_path("b.rec"))
        self.assertFalse(is_edf_path("b.csv"))
        self.assertFalse(looks_like_edf(self.path("missing.edf")))

    def test_unknown_record_count_is_derived(self):
        p = self.path("unknown.edf")
        write_edf(p, [("A", "uV", sine(FS, 5, 3.0))], FS, n_records_field=-1)
        info = read_edf_info(p)
        self.assertEqual(info.n_records, 5)
        self.assertTrue(any("unknown number" in w for w in info.warnings))

    def test_truncated_file_is_clamped_with_a_warning(self):
        p = self.path("trunc.edf")
        write_edf(p, [("A", "uV", sine(FS, 5, 3.0))], FS, truncate_records=2)
        info = read_edf_info(p)
        self.assertEqual(info.n_records, 3)
        self.assertTrue(any("truncated" in w for w in info.warnings))
        sig, _, _ = read_edf_channel(p)
        self.assertEqual(len(sig.values), 3 * int(FS))

    def test_edf_plus_discontinuous_warns(self):
        p = self.path("d.edf")
        write_edf(p, [("A", "uV", sine(FS, 4, 3.0))], FS, reserved="EDF+D")
        info = read_edf_info(p)
        self.assertFalse(info.continuous)
        self.assertTrue(any("EDF+D" in w for w in info.warnings))


class TestCalibration(EdfTempDir):
    def _max_err(self, got, want):
        return max(abs(a - b) for a, b in zip(got, want))

    def test_edf_roundtrip_within_quantisation(self):
        p = self.path("q.edf")
        want = sine(FS, 4, 10.0, 20.0)
        write_edf(p, [("Fp1", "uV", want)], FS, phys_range=(-20.0, 20.0))
        sig, fs, _ = read_edf_channel(p)
        self.assertEqual(fs, FS)
        self.assertEqual(len(sig.values), len(want))
        step = 40.0 / 65535.0
        self.assertLessEqual(self._max_err(sig.values, want), step)

    def test_bdf_is_24_bit_precise(self):
        p = self.path("q.bdf")
        want = sine(FS, 4, 7.0, 30.0)
        write_edf(p, [("O1", "uV", want)], FS, bdf=True, phys_range=(-30.0, 30.0))
        info = read_edf_info(p)
        self.assertEqual(info.kind, "BDF")
        self.assertEqual(info.bytes_per_sample, 3)
        sig, _, _ = read_edf_channel(p)
        step = 60.0 / (2 ** 24 - 1)
        self.assertLessEqual(self._max_err(sig.values, want), step)

    def test_millivolt_channel_is_converted_to_microvolt(self):
        p = self.path("mv.edf")
        want_uv = sine(FS, 4, 5.0, 50.0)
        write_edf(p, [("Cz", "mV", [v / 1000.0 for v in want_uv])], FS,
                  phys_range=(-0.05, 0.05))
        sig, _, esig = read_edf_channel(p, "Cz")
        self.assertEqual(esig.unit_scale_uv, 1000.0)
        self.assertLessEqual(self._max_err(sig.values, want_uv),
                             1000.0 * 0.1 / 65535.0)
        self.assertTrue(any("converted to µV" in w for w in sig.warnings))

    def test_volt_and_nanovolt_scales(self):
        # separate files: each unit needs its own physical range to stay resolvable
        pv, pn = self.path("v.edf"), self.path("n.edf")
        write_edf(pv, [("V1", "V", [1e-6] * int(FS))], FS,
                  phys_range=(-0.0001, 0.0001))
        write_edf(pn, [("N1", "nV", [1000.0] * int(FS))], FS,
                  phys_range=(-2000.0, 2000.0))
        v, _, _ = read_edf_channel(pv, "V1")
        n, _, _ = read_edf_channel(pn, "N1")
        self.assertAlmostEqual(v.values[0], 1.0, delta=0.01)     # 1 µV
        self.assertAlmostEqual(n.values[0], 1.0, delta=0.01)     # 1000 nV = 1 µV

    def test_unknown_unit_warns_and_passes_through(self):
        p = self.path("odd.edf")
        write_edf(p, [("Resp", "mmHg", [3.0] * int(FS))], FS, phys_range=(-5, 5))
        sig, _, esig = read_edf_channel(p, "Resp")
        self.assertFalse(esig.unit_known)
        self.assertAlmostEqual(sig.values[0], 3.0, delta=0.01)
        self.assertTrue(any("not a recognised voltage unit" in w
                            for w in sig.warnings))

    def test_negative_and_asymmetric_range(self):
        """A channel whose physical range is entirely negative still calibrates."""
        p = self.path("neg.edf")
        want = [-30.0 - 10.0 * math.sin(2 * math.pi * 3 * k / FS)
                for k in range(int(FS * 2))]
        write_edf(p, [("A", "uV", want)], FS, phys_range=(-45.0, -15.0))
        sig, _, _ = read_edf_channel(p)
        self.assertLessEqual(self._max_err(sig.values, want), 30.0 / 65535.0)


class TestWindowing(EdfTempDir):
    def setUp(self):
        super().setUp()
        self.want = sine(FS, 10, 4.0, 25.0)
        self.p = self.path("win.edf")
        write_edf(self.p, [("A", "uV", self.want)], FS, phys_range=(-25.0, 25.0))

    def test_start_and_duration(self):
        sig, _, _ = read_edf_channel(self.p, start_sec=2.0, duration_sec=3.0)
        self.assertEqual(len(sig.values), int(3 * FS))
        i0 = int(2 * FS)
        self.assertLessEqual(
            max(abs(a - b) for a, b in zip(sig.values, self.want[i0:i0 + 384])),
            50.0 / 65535.0)

    def test_start_inside_a_record(self):
        """A start time that is not a whole record still lands on the right sample."""
        sig, _, _ = read_edf_channel(self.p, start_sec=2.5, duration_sec=1.0)
        i0 = int(2.5 * FS)
        self.assertEqual(len(sig.values), int(FS))
        self.assertAlmostEqual(sig.values[0], self.want[i0], delta=50.0 / 65535.0)

    def test_duration_beyond_end_is_clamped(self):
        sig, _, _ = read_edf_channel(self.p, start_sec=9.0, duration_sec=60.0)
        self.assertEqual(len(sig.values), int(FS))

    def test_start_beyond_end_raises(self):
        with self.assertRaises(ValueError):
            read_edf_channel(self.p, start_sec=99.0)
        with self.assertRaises(ValueError):
            read_edf_channel(self.p, start_sec=-1.0)
        with self.assertRaises(ValueError):
            read_edf_channel(self.p, duration_sec=0.0)


class TestMixedSampleRates(EdfTempDir):
    def test_channels_with_different_rates(self):
        """EDF allows a different sampling rate per channel; both must read right."""
        p = self.path("mixed.edf")
        fast = sine(256.0, 4, 10.0, 20.0)
        slow = sine(64.0, 4, 2.0, 40.0)
        # write_edf needs one fs, so build the file by hand with two records/sec
        from edf_fixtures import _fixed, _num
        ns = 2
        n_rec = 4
        header = bytearray()
        header += _fixed("0", 8) + _fixed("X X X X", 80) + _fixed("Startdate X", 80)
        header += _fixed("01.01.85", 8) + _fixed("00.00.00", 8)
        header += _num(256 + 256 * ns, 8) + _fixed("", 44)
        header += _num(n_rec, 8) + _num(1.0, 8) + _fixed(str(ns), 4)
        sh = bytearray()
        sh += _fixed("FAST", 16) + _fixed("SLOW", 16)
        sh += _fixed("t", 80) * 2
        sh += _fixed("uV", 8) * 2
        sh += _num(-20, 8) + _num(-40, 8)
        sh += _num(20, 8) + _num(40, 8)
        sh += _num(-32768, 8) * 2
        sh += _num(32767, 8) * 2
        sh += _fixed("", 80) * 2
        sh += _fixed("256", 8) + _fixed("64", 8)
        sh += _fixed("", 32) * 2
        body = bytearray()
        for r in range(n_rec):
            for vals, n_per, lo, hi in ((fast, 256, -20.0, 20.0),
                                        (slow, 64, -40.0, 40.0)):
                for k in range(n_per):
                    v = vals[r * n_per + k]
                    d = int(round(-32768 + (v - lo) * 65535 / (hi - lo)))
                    body += (max(-32768, min(32767, d)) & 0xFFFF).to_bytes(2, "little")
        with open(p, "wb") as fh:
            fh.write(bytes(header) + bytes(sh) + bytes(body))

        info = read_edf_info(p)
        self.assertEqual([s.fs for s in info.signals], [256.0, 64.0])
        f, fs_f, _ = read_edf_channel(p, "FAST")
        s, fs_s, _ = read_edf_channel(p, "SLOW")
        self.assertEqual((fs_f, fs_s), (256.0, 64.0))
        self.assertEqual((len(f.values), len(s.values)), (1024, 256))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(f.values, fast)),
                             40.0 / 65535.0)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(s.values, slow)),
                             80.0 / 65535.0)


class TestEndToEnd(EdfTempDir):
    def test_analysis_of_an_edf_channel(self):
        """A 10 Hz EDF channel must come out alpha-dominant with the right power."""
        p = self.path("e2e.edf")
        amp = 20.0
        write_edf(p, [("Fp1", "uV", sine(FS, 16, 10.0, amp))], FS,
                  phys_range=(-amp, amp))
        sig, fs, _ = read_edf_channel(p)
        res = analyze(sig.values, fs=fs, epoch_sec=4.0)
        self.assertEqual(res.overall.dominant, "alpha")
        # Parseval: a sinusoid of amplitude A has variance A²/2 = total power
        self.assertAlmostEqual(res.overall.total_power, amp * amp / 2.0, delta=0.05)
        self.assertAlmostEqual(res.overall.peak_freq, 10.0, delta=0.3)
        self.assertEqual(len(res.epochs), 4)


if __name__ == "__main__":
    unittest.main()


class TestPhiAcrossEveryOutputMode(EdfTempDir):
    """The PHI check must cover the RENDERED output, not just the parsed objects.

    A regression that put the patient field into the CSV provenance line, the JSON
    provenance block, the text [정보] section or --list-channels would not be caught by
    inspecting repr(EdfInfo) alone — those four are where a leak would actually surface.
    """

    def test_no_phi_reaches_any_rendered_output(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from eegband.cli import main
        # tokens must be impossible to hit by chance: a bare year like "1972" occurs
        # inside ordinary floating-point output, which is not a leak.
        tokens = ["MRN00998877", "PARK_SOOJIN", "SEOULCLINIC", "14-MAR", "TECH_LEE"]
        p = self.path("phi2.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 8, 10.0, 20.0)),
                      ("Cz", "uV", sine(FS, 8, 2.0, 40.0))], FS,
                  phys_range=(-40.0, 40.0),
                  patient="MRN00998877 F 14-MAR-1972 PARK_SOOJIN",
                  recording="Startdate 07-JUL-2026 SEOULCLINIC ROOM3 TECH_LEE")
        with open(p, "rb") as fh:
            raw = fh.read()
        for tok in tokens:                      # the PHI really is in the file
            self.assertIn(tok.encode(), raw)
        argvs = ([p], [p, "--json"], [p, "--csv"], [p, "--csv-summary"],
                 [p, "--psd-csv"], [p, "--epoch", "2"],
                 [p, "--epoch", "2", "--csv"], [p, "--list-channels"],
                 [p, "--channels", "all"], [p, "--channels", "all", "--json"])
        for argv in argvs:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(argv)
            blob = out.getvalue() + err.getvalue()
            for tok in tokens:
                self.assertNotIn(tok, blob, f"{tok} leaked via {argv}")

    def test_no_phi_in_parsed_objects(self):
        p = self.path("phi3.edf")
        write_edf(p, [("Fp1", "uV", sine(FS, 4, 10.0))], FS,
                  patient="MRN123 M 01-JAN-1980 KIM", recording="Startdate CLINIC_X")
        info = read_edf_info(p)
        sig, _, es = read_edf_channel(p, info=info)
        blob = repr(info) + repr(sig) + repr(es)
        for tok in ("MRN123", "KIM", "CLINIC_X"):
            self.assertNotIn(tok, blob)
