"""Rendering and CLI tests."""

import json

import pytest

from table1.build import Options, build_table1
from table1.cli import main
from table1.dataio import Frame, load_frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _demo_table():
    rows = ([("A", 1, "F"), ("A", 2, "M"), ("A", 3, "F"), ("A", 4, "M")] +
            [("B", 5, "M"), ("B", 6, "F"), ("B", 7, "M"), ("B", 8, "M")])
    fr = _frame(["g", "x", "sex"], rows)
    opt = Options(group_col="g", display="mean")
    return build_table1(fr, opt), opt


def test_markdown_contains_expected():
    t, opt = _demo_table()
    md = render(t, opt, "md")
    assert "표 1" in md
    assert "x — 평균(SD)" in md
    assert "sex — n(%)" in md
    assert "SMD" in md            # two-group -> SMD column present
    assert md.count("|") > 10


def test_no_overall_column():
    t, opt = _demo_table()
    opt.overall = False
    md = render(t, opt, "md")
    assert "전체 (N=" not in md


def test_csv_formula_injection_escaped():
    # A group label starting with '=' must be neutralised in CSV output.
    rows = [("=cmd", 1), ("=cmd", 2), ("B", 3), ("B", 4)]
    fr = _frame(["g", "x"], rows)
    opt = Options(group_col="g", display="mean")
    t = build_table1(fr, opt)
    csv_text = render(t, opt, "csv")
    # header cell for the malicious group must be prefixed with a quote
    assert "'=cmd" in csv_text
    assert "\n=cmd" not in csv_text


def test_json_roundtrip():
    t, opt = _demo_table()
    obj = json.loads(render(t, opt, "json"))
    assert obj["groups"] == ["A", "B"]
    assert obj["group_sizes"] == [4, 4]
    names = [r["name"] for r in obj["rows"]]
    assert "x" in names and "sex" in names


def test_unknown_format_raises():
    t, opt = _demo_table()
    with pytest.raises(ValueError):
        render(t, opt, "xml")


# ---- CLI ------------------------------------------------------------------ #
def _write(tmp_path, text):
    p = tmp_path / "d.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_happy_path(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nA,2\nA,3\nB,4\nB,5\nB,6\n")
    rc = main([path, "--group", "g"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "표 1" in out


def test_cli_missing_file(capsys):
    rc = main(["/no/such.csv", "--group", "g"])
    assert rc == 2
    assert "찾을 수 없" in capsys.readouterr().err


def test_cli_bad_group(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nB,2\n")
    rc = main([path, "--group", "nope"])
    assert rc == 2


def test_cli_one_group(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nA,2\n")
    rc = main([path, "--group", "g"])
    assert rc == 2


def test_cli_writes_file(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nA,2\nA,3\nB,4\nB,5\nB,6\n")
    out_path = str(tmp_path / "t1.csv")
    rc = main([path, "--group", "g", "--format", "csv", "-o", out_path])
    assert rc == 0
    with open(out_path, encoding="utf-8") as fh:
        content = fh.read()
    assert "characteristic" in content


def test_cli_example_dataset(capsys):
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ex = os.path.join(here, "examples", "serene_baseline.csv")
    rc = main([ex, "--group", "arm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "age" in out and "SMD" in out


def test_golden_snapshot_example_md():
    # Full-string regression on the rendered example table: pins column order,
    # per-group missing suffix, footnote/warning layout and every number.
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ex = os.path.join(here, "examples", "serene_baseline.csv")
    golden = os.path.join(here, "tests", "golden", "serene_table.md")
    fr = load_frame(ex)
    opt = Options(group_col="arm")
    got = render(build_table1(fr, opt), opt, "md")
    with open(golden, encoding="utf-8") as fh:
        expected = fh.read()
    assert got == expected, (
        "Rendered example table drifted from tests/golden/serene_table.md. "
        "If the change is intentional, regenerate the golden file.")


def test_cli_bad_delimiter_multichar(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nA,2\nB,3\nB,4\n")
    rc = main([path, "--group", "g", "--delimiter", ";;"])
    assert rc == 2
    assert "구분자" in capsys.readouterr().err


def test_cli_tab_delimiter_alias(tmp_path, capsys):
    # A user typing --delimiter "\t" from a shell passes literal backslash+t;
    # the alias maps it to a real tab instead of crashing with a TypeError.
    p = tmp_path / "d.tsv"
    p.write_text("g\tx\nA\t1\nA\t2\nB\t3\nB\t4\n", encoding="utf-8")
    rc = main([str(p), "--group", "g", "--delimiter", "\\t"])
    assert rc == 0
    assert "x" in capsys.readouterr().out


def test_cli_alpha_norm_out_of_range(tmp_path, capsys):
    path = _write(tmp_path, "g,x\nA,1\nA,2\nB,3\nB,4\n")
    for bad in ("5", "0", "-0.1", "1"):
        rc = main([path, "--group", "g", "--alpha-norm", bad])
        assert rc == 2
    assert "alpha-norm" in capsys.readouterr().err
