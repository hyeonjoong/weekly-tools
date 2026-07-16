"""End-to-end CLI tests: argv parsing, exit codes, stderr messages, dispatch."""

import json

import pytest

from agreestat.cli import main, _resolve_accept


def _write(tmp_path, text, name="d.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding) if isinstance(text, str) else text)
    return str(p)


def _good_csv(tmp_path):
    return _write(
        tmp_path,
        "x,y\n" + "\n".join(f"{i+0.1},{i+0.2}" for i in range(1, 13)) + "\n")


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------
def test_cli_basic_ok(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Bland" in out and "ICC" in out and "논문용 문장" in out


def test_cli_json_parses(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    obj = json.loads(out)
    assert obj["method_a"] == "x"
    assert obj["n"] == 12
    assert "acceptance" in obj
    assert "n_outside_loa" in obj["bland_altman"]


def test_cli_autodetect(tmp_path, capsys):
    rc = main([_good_csv(tmp_path)])
    assert rc == 0
    assert 'method A = "x"' in capsys.readouterr().out


def test_cli_name_override(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y",
               "--name-a", "센서", "--name-b", "기준"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "센서" in out and "기준" in out


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "agreestat" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Error paths -> exit 2 with a Korean message on stderr
# --------------------------------------------------------------------------
def test_cli_alpha_out_of_range(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "--alpha", "0"])
    assert rc == 2
    assert "alpha" in capsys.readouterr().err


def test_cli_missing_file(capsys):
    rc = main(["/no/such/file.csv"])
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


def test_cli_directory_is_handled(tmp_path, capsys):
    rc = main([str(tmp_path)])  # a directory, not a file -> IsADirectoryError
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


def test_cli_same_column_rejected(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "x"])
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


def test_cli_unusable_data(tmp_path, capsys):
    p = _write(tmp_path, "x,y\nNA,NA\n,,\n")
    rc = main([p, "-a", "x", "-b", "y"])
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


# --------------------------------------------------------------------------
# New flags
# --------------------------------------------------------------------------
def test_cli_accept_interchangeable(tmp_path, capsys):
    p = _write(tmp_path,
               "x,y\n" + "\n".join(f"{i},{i}" for i in range(1, 15)) + "\n")
    rc = main([p, "-a", "x", "-b", "y", "--accept", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "교환가능" in out


def test_cli_accept_not_interchangeable(tmp_path, capsys):
    p = _write(tmp_path,
               "x,y\n" + "\n".join(f"{i},{i*1.5}" for i in range(1, 15)) + "\n")
    rc = main([p, "-a", "x", "-b", "y", "--accept", "0.5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "교환 불가" in out or "벗어" in out


def test_cli_accept_conflict_rejected(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y",
               "--accept", "2", "--accept-lower", "-1"])
    assert rc == 2
    assert "허용한계" in capsys.readouterr().err


def test_cli_encoding_cp949(tmp_path, capsys):
    p = _write(tmp_path, "센서,밴드\n14.2,14.0\n15.1,14.8\n11.9,12.3\n13,13.1\n",
               encoding="cp949")
    rc = main([p])  # auto-detect encoding
    assert rc == 0
    assert "센서" in capsys.readouterr().out


def test_cli_encoding_explicit(tmp_path, capsys):
    p = _write(tmp_path, "센서,밴드\n14.2,14.0\n15.1,14.8\n11.9,12.3\n13,13.1\n",
               encoding="cp949")
    rc = main([p, "--encoding", "cp949"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "센서" in out  # explicit encoding decoded the Korean header


def test_cli_accept_nan_rejected(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--accept", "nan"])
    assert rc == 2
    assert "허용한계" in capsys.readouterr().err


def test_cli_accept_inf_rejected(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--accept", "inf"])
    assert rc == 2


def test_cli_huge_finite_does_not_crash(tmp_path, capsys):
    p = _write(tmp_path, "a,b\n1e308,1\n2,3\n4,5\n6,7\n")
    rc = main([p, "-a", "a", "-b", "b"])
    out = capsys.readouterr().out
    assert rc == 0  # dropped + counted, no OverflowError traceback
    assert "비정상 수치" in out or "무한대" in out


def test_cli_target_loa_hw(tmp_path, capsys):
    p = _write(tmp_path,
               "x,y\n" + "\n".join(f"{i+0.1},{i}" for i in range(1, 13)) + "\n")
    rc = main([p, "-a", "x", "-b", "y", "--target-loa-hw", "0.5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "필요 표본" in out


def test_cli_target_loa_hw_nonpositive_rejected(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--target-loa-hw", "0"])
    assert rc == 2


def test_cli_target_loa_hw_nonfinite_rejected(tmp_path, capsys):
    for bad in ("nan", "inf"):
        rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y",
                   "--target-loa-hw", bad])
        assert rc == 2
        assert "target-loa-hw" in capsys.readouterr().err


def test_cli_target_loa_hw_tiny_is_fast(tmp_path, capsys):
    # regression: 1e-9 used to hang ~3 minutes; now returns a clean message
    p = _write(tmp_path,
               "x,y\n" + "\n".join(f"{i},{i+ (i%2)}" for i in range(1, 13)) + "\n")
    rc = main([p, "-a", "x", "-b", "y", "--target-loa-hw", "1e-9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "매우 큼" in out


def test_cli_markdown_stdout(tmp_path, capsys):
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--markdown"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| Metric | Estimate |" in out


def test_cli_markdown_file(tmp_path, capsys):
    out_md = tmp_path / "out.md"
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y", "--markdown", str(out_md)])
    assert rc == 0
    assert out_md.read_text(encoding="utf-8").startswith("# agreestat")


def test_cli_plot_data_and_svg_files(tmp_path, capsys):
    pd = tmp_path / "pd.csv"
    svg = tmp_path / "ba.svg"
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y",
               "--plot-data", str(pd), "--svg", str(svg)])
    assert rc == 0
    assert "mean,diff,outside_loa" in pd.read_text(encoding="utf-8")
    text = svg.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "</svg>" in text


def test_cli_output_file_error(tmp_path, capsys):
    # writing into a non-existent directory -> OSError -> graceful exit 2
    rc = main([_good_csv(tmp_path), "-a", "x", "-b", "y",
               "--svg", str(tmp_path / "no_such_dir" / "x.svg")])
    assert rc == 2
    assert "출력 오류" in capsys.readouterr().err


# --------------------------------------------------------------------------
# _resolve_accept unit tests
# --------------------------------------------------------------------------
class _Args:
    def __init__(self, accept=None, lo=None, hi=None):
        self.accept = accept
        self.accept_lower = lo
        self.accept_upper = hi


def test_resolve_accept_symmetric():
    assert _resolve_accept(_Args(accept=2.0)) == (-2.0, 2.0)


def test_resolve_accept_asymmetric():
    assert _resolve_accept(_Args(lo=-1.0, hi=3.0)) == (-1.0, 3.0)


def test_resolve_accept_none():
    assert _resolve_accept(_Args()) is None


def test_resolve_accept_errors():
    assert _resolve_accept(_Args(accept=0.0)) == "error"
    assert _resolve_accept(_Args(accept=2.0, lo=-1.0)) == "error"
    assert _resolve_accept(_Args(lo=3.0, hi=-1.0)) == "error"  # lo >= hi
    assert _resolve_accept(_Args(lo=1.0)) == "error"  # incomplete
