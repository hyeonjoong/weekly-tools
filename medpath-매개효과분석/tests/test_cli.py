"""CLI behaviour: the documented invocation must actually work.

These tests drive `main()` the way a user does, and additionally run the real
`python3 -m medpath` subprocess so that a missing `__main__.py` cannot pass.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from random import Random

import pytest

from helpers import write_csv
from medpath.cli import _resolve_conf, build_parser, main, make_console_safe
from medpath.dataio import DataError

REPO = Path(__file__).resolve().parent.parent


def _csv(tmp_path, name="d.csv", n=90, seed=5):
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = "device" if i % 2 else "sham"
        cov = round(rng.gauss(50, 10), 2)
        m = round(10 + 2.0 * (i % 2) + 0.05 * cov + rng.gauss(0, 1.5), 4)
        m2 = round(4 + 1.0 * (i % 2) + rng.gauss(0, 1.0), 4)
        y = round(5 + 0.8 * m + 0.3 * m2 + 1.0 * (i % 2) + rng.gauss(0, 2.0), 4)
        rows.append([x, m, m2, y, cov])
    return write_csv(tmp_path / name, ["arm", "m1", "m2", "y", "age"], rows)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def test_python_dash_m_medpath_runs():
    """`python3 -m medpath` is the documented command — it must exist.

    Regression: the package had no __main__.py, so the only working form was
    `python3 -m medpath.cli`, which no doc or help string mentioned.
    """
    r = subprocess.run([sys.executable, "-m", "medpath", "--version"],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "medpath" in r.stdout


def test_python_dash_m_medpath_analyses_the_bundled_example():
    example = REPO / "examples" / "sleep_breathing_hrv.csv"
    r = subprocess.run(
        [sys.executable, "-m", "medpath", str(example), "--x", "arm",
         "--m", "rmssd_ms", "--y", "sws_min", "--bootstrap", "200"],
        cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "간접효과" in r.stdout


def test_help_does_not_advertise_an_uninstalled_console_script():
    """The epilog used to tell people to type `medpath ...`, which fails."""
    r = subprocess.run([sys.executable, "-m", "medpath", "--help"],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0
    for line in r.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("medpath ") and "-m medpath" not in stripped:
            pytest.fail("help advertises a bare `medpath` command: %r" % line)


# --------------------------------------------------------------------------
# Exit codes and error handling
# --------------------------------------------------------------------------
def test_missing_file_exits_nonzero_without_a_traceback(tmp_path, capsys):
    code = main([str(tmp_path / "nope.csv"), "--x", "a", "--m", "b", "--y", "c"])
    assert code == 1
    err = capsys.readouterr().err
    assert "오류:" in err
    assert "Traceback" not in err


def test_unknown_column_exits_nonzero(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "ghost", "--y", "y",
                 "--bootstrap", "0"])
    assert code == 1
    assert "오류:" in capsys.readouterr().err


def test_serial_with_one_mediator_is_rejected(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--serial", "--bootstrap", "0"])
    assert code == 1
    assert "오류:" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [["--bootstrap", "-5"], ["--conf", "1.5"],
                                 ["--conf", "0.2"], ["--jobs", "0"],
                                 ["--digits", "99"]])
def test_out_of_range_options_are_rejected(tmp_path, capsys, bad):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y"] + bad)
    assert code == 1
    assert "오류:" in capsys.readouterr().err


def test_bca_without_bootstrap_is_rejected(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--ci", "bca", "--bootstrap", "0"])
    assert code == 1
    assert "오류:" in capsys.readouterr().err


def test_resolve_conf_accepts_both_scales():
    assert _resolve_conf(95.0) == pytest.approx(0.95)
    assert _resolve_conf(0.95) == pytest.approx(0.95)
    assert _resolve_conf(99.0) == pytest.approx(0.99)
    with pytest.raises(DataError):
        _resolve_conf(150.0)


# --------------------------------------------------------------------------
# Output modes
# --------------------------------------------------------------------------
def test_list_columns_lists_every_header(tmp_path, capsys):
    code = main([_csv(tmp_path), "--list-columns"])
    assert code == 0
    out = capsys.readouterr().out
    for col in ("arm", "m1", "m2", "y", "age"):
        assert col in out


def test_json_output_is_valid_json(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1,m2", "--y", "y",
                 "--covariates", "age", "--bootstrap", "200", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"]["x"] == "arm"
    assert payload["model"]["mediators"] == ["m1", "m2"]
    assert payload["sample"]["n_analysed"] == 90
    assert len(payload["effects"]) >= 4


def test_json_is_strictly_valid_when_intervals_are_missing(tmp_path, capsys):
    """--bootstrap 0 leaves NaNs; JSON has no NaN literal.

    Regression: allow_nan=True emitted bare `NaN`, which Python's own loads()
    accepts but every strict parser (JSON.parse, jq -e, Go, R jsonlite)
    rejects — one missing interval made the entire file unloadable.
    """
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--bootstrap", "0", "--json"])
    assert code == 0
    raw = capsys.readouterr().out
    assert "NaN" not in raw and "Infinity" not in raw
    payload = json.loads(raw, parse_constant=_reject_constant)
    eff = [e for e in payload["effects"] if e["kind"] == "indirect"][0]
    assert eff["ci_lo"] is None and eff["ci_hi"] is None
    assert eff["tested"] is False


def _reject_constant(name):
    raise AssertionError("non-finite JSON constant emitted: %s" % name)


def test_json_is_strictly_valid_for_a_normal_run(tmp_path, capsys):
    main([_csv(tmp_path), "--x", "arm", "--m", "m1,m2", "--y", "y",
          "--bootstrap", "300", "--json"])
    json.loads(capsys.readouterr().out, parse_constant=_reject_constant)


def test_json_safe_converts_non_finite_floats():
    from medpath.cli import json_safe
    got = json_safe({"a": float("nan"), "b": [float("inf"), 1.5],
                     "c": {"d": float("-inf")}, "e": "x", "f": 3})
    assert got == {"a": None, "b": [None, 1.5], "c": {"d": None}, "e": "x", "f": 3}


def test_out_file_is_written_and_summary_printed(tmp_path, capsys):
    dest = tmp_path / "report.txt"
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--bootstrap", "200", "--out", str(dest)])
    assert code == 0
    text = dest.read_text(encoding="utf-8")
    assert "매개효과" in text and text.endswith("\n")
    assert "결과를 저장했습니다" in capsys.readouterr().out


def test_out_to_missing_directory_exits_nonzero(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--bootstrap", "0", "--out", str(tmp_path / "nodir" / "r.txt")])
    assert code == 1
    assert "오류:" in capsys.readouterr().err


def test_markdown_mode_emits_markdown_tables(tmp_path, capsys):
    code = main([_csv(tmp_path), "--x", "arm", "--m", "m1", "--y", "y",
                 "--bootstrap", "200", "--markdown"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.lstrip().startswith("# ")
    assert "|:---" in out or "|---:" in out


def test_comma_and_repeated_mediator_flags_agree(tmp_path, capsys):
    path = _csv(tmp_path)
    main([path, "--x", "arm", "--m", "m1,m2", "--y", "y", "--bootstrap", "100",
          "--json"])
    a = json.loads(capsys.readouterr().out)
    main([path, "--x", "arm", "--m", "m1", "--m", "m2", "--y", "y",
          "--bootstrap", "100", "--json"])
    b = json.loads(capsys.readouterr().out)
    assert a["model"]["mediators"] == b["model"]["mediators"] == ["m1", "m2"]
    assert a["effects"][0]["estimate"] == pytest.approx(b["effects"][0]["estimate"])


# --------------------------------------------------------------------------
# Narrow console encodings (Korean Windows is cp949)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("encoding", ["cp949", "ascii", "latin-1"])
def test_report_survives_a_non_utf8_console(tmp_path, encoding):
    """A console that cannot encode '—' must not destroy the whole report.

    Regression: print() raised UnicodeEncodeError under PYTHONIOENCODING=cp949
    and the user got a traceback instead of any results at all.
    """
    example = REPO / "examples" / "sleep_breathing_hrv.csv"
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    r = subprocess.run(
        [sys.executable, "-m", "medpath", str(example), "--x", "arm",
         "--m", "rmssd_ms", "--y", "sws_min", "--bootstrap", "100"],
        cwd=str(REPO), capture_output=True, env=env)
    assert r.returncode == 0
    assert b"Traceback" not in r.stderr
    assert b"UnicodeEncodeError" not in r.stderr
    assert len(r.stdout.splitlines()) > 50


def test_json_survives_a_non_utf8_console(tmp_path):
    example = REPO / "examples" / "sleep_breathing_hrv.csv"
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    r = subprocess.run(
        [sys.executable, "-m", "medpath", str(example), "--x", "arm",
         "--m", "rmssd_ms", "--y", "sws_min", "--bootstrap", "100", "--json"],
        cwd=str(REPO), capture_output=True, env=env)
    assert r.returncode == 0
    assert b"Traceback" not in r.stderr


def test_out_file_is_utf8_even_on_a_narrow_console(tmp_path):
    """--out is the documented escape hatch, so it must stay full UTF-8."""
    example = REPO / "examples" / "sleep_breathing_hrv.csv"
    dest = tmp_path / "r.txt"
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    r = subprocess.run(
        [sys.executable, "-m", "medpath", str(example), "--x", "arm",
         "--m", "rmssd_ms", "--y", "sws_min", "--bootstrap", "100",
         "--out", str(dest)],
        cwd=str(REPO), capture_output=True, env=env)
    assert r.returncode == 0
    text = dest.read_text(encoding="utf-8")
    assert "—" in text or "→" in text     # the real glyphs survived to the file
    assert "간접효과" in text


def test_make_console_safe_leaves_utf8_streams_alone():
    assert make_console_safe() is None      # pytest's captured streams are UTF-8


def test_parser_defaults_match_the_documented_ones():
    args = build_parser().parse_args(["f.csv"])
    assert args.bootstrap == 5000
    assert args.ci == "percentile"
    assert args.conf == 95.0
    assert args.robust == "none"
    assert args.jobs == 1
    assert args.digits == 3
    assert args.serial is False
