"""Per-variable --nonnormal, plus CLI wiring for the new options."""

from __future__ import annotations

import json
import zipfile

import pytest

from table1.build import Options, build_table1
from table1.cli import main
from table1.dataio import Frame
from table1.render import render

from test_xlsx import _inline, _num, _row, make_xlsx


def _skewed_frame():
    # x is wildly skewed in group a; y is clean and normal-ish.
    header = ["arm", "x", "y"]
    rows = [
        ["a", "1", "10"], ["a", "1", "11"], ["a", "2", "12"],
        ["a", "2", "13"], ["a", "300", "14"],
        ["b", "1", "20"], ["b", "2", "21"], ["b", "2", "22"],
        ["b", "3", "23"], ["b", "3", "24"],
    ]
    return Frame(header, rows)


# --------------------------------------------------------------------------- #
# --nonnormal semantics
# --------------------------------------------------------------------------- #
def test_nonnormal_forces_median_and_rank_test():
    t = build_table1(_skewed_frame(), Options(group_col="arm", nonnormal=["y"]))
    y = [r for r in t.rows if r.name == "y"][0]
    assert y.display == "median"
    assert y.test_name == "Mann-Whitney U"


def test_nonnormal_only_affects_the_named_variable():
    t = build_table1(_skewed_frame(), Options(group_col="arm", nonnormal=["y"]))
    by = {r.name: r for r in t.rows}
    assert by["y"].display == "median"
    # x is untouched by the flag and keeps its auto-selected treatment
    assert by["x"].name == "x"


def test_nonnormal_beats_global_test_cont():
    """Per-variable is more specific than the global switch: naming a variable
    as skewed and still getting a t-test would ignore the instruction."""
    t = build_table1(_skewed_frame(),
                     Options(group_col="arm", nonnormal=["y"],
                             test_cont="welch"))
    by = {r.name: r for r in t.rows}
    assert by["y"].test_name == "Mann-Whitney U"
    assert by["x"].test_name == "Welch t"     # not named -> global rule applies


def test_nonnormal_skips_the_normality_pretest_note():
    """A forced variable never consults Shapiro-Wilk, so its 'untestable' note
    would be noise."""
    header = ["arm", "v"]
    rows = [["a", "1"], ["a", "2"], ["b", "3"], ["b", "4"]]   # n<3 per group
    t = build_table1(Frame(header, rows),
                     Options(group_col="arm", nonnormal=["v"]))
    v = t.rows[0]
    assert not any("정규성" in n or "normality" in n for n in v.notes)
    # without the flag the untestable note IS emitted
    t2 = build_table1(Frame(header, rows), Options(group_col="arm"))
    assert any("정규성" in n for n in t2.rows[0].notes)


def test_nonnormal_with_explicit_display_override():
    """--display mean still controls the TEXT; --nonnormal controls the test."""
    t = build_table1(_skewed_frame(),
                     Options(group_col="arm", nonnormal=["y"], display="mean"))
    y = [r for r in t.rows if r.name == "y"][0]
    assert y.display == "mean"
    assert y.test_name == "Mann-Whitney U"


def test_nonnormal_three_groups_uses_kruskal():
    header = ["arm", "v"]
    rows = [["a", "1"], ["a", "2"], ["b", "3"], ["b", "4"],
            ["c", "5"], ["c", "6"]]
    t = build_table1(Frame(header, rows),
                     Options(group_col="arm", nonnormal=["v"]))
    assert t.rows[0].test_name == "Kruskal-Wallis"


def test_nonnormal_unknown_column_is_harmless():
    t = build_table1(_skewed_frame(),
                     Options(group_col="arm", nonnormal=["does_not_exist"]))
    assert [r.name for r in t.rows] == ["x", "y"]


def test_nonnormal_renders_median_notation():
    opt = Options(group_col="arm", nonnormal=["y"])
    t = build_table1(_skewed_frame(), opt)
    md = render(t, opt, fmt="md")
    assert "y — 중앙값[IQR]" in md


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def _write_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("arm,age,sex,w\n"
                 "device,50,M,1\ndevice,60,F,3\n"
                 "sham,40,M,2\nsham,70,F,2\n", encoding="utf-8")
    return str(p)


def test_cli_weights_option(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "--group", "arm", "--weights", "w"]) == 0
    out = capsys.readouterr().out
    assert "ESS=" in out
    assert "| p값 |" not in out
    assert "SMD" in out


def test_cli_weights_short_flag(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "-g", "arm", "-w", "w"]) == 0
    assert "ESS=" in capsys.readouterr().out


def test_cli_weights_missing_column_errors(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "--group", "arm", "--weights", "no"]) == 2
    assert "가중치 열" in capsys.readouterr().err


def test_cli_nonnormal_option(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "--group", "arm",
                 "--nonnormal", "age", "--format", "json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    age = [r for r in obj["rows"] if r["name"] == "age"][0]
    assert age["display"] == "median"
    assert age["test"] == "Mann-Whitney U"


def test_cli_xlsx_input(tmp_path, capsys):
    header = _row(1, [_inline("A1", "arm"), _inline("B1", "age")])
    body = "".join(
        _row(i, [_inline(f"A{i}", arm), _num(f"B{i}", age)])
        for i, (arm, age) in enumerate(
            [("device", 50), ("device", 60), ("sham", 40), ("sham", 70)], start=2))
    p = make_xlsx(tmp_path, [("기저", header + body)])
    assert main([p, "--group", "arm"]) == 0
    assert "age" in capsys.readouterr().out


def test_cli_sheet_option(tmp_path, capsys):
    def book(name, base):
        return _row(1, [_inline("A1", "arm"), _inline("B1", "age")]) + "".join(
            _row(i, [_inline(f"A{i}", arm), _num(f"B{i}", base + j)])
            for i, (j, arm) in enumerate(
                [(0, "device"), (1, "device"), (2, "sham"), (3, "sham")], start=2))
    p = make_xlsx(tmp_path, [("first", book("first", 10)),
                             ("second", book("second", 90))])
    assert main([p, "--group", "arm", "--sheet", "second"]) == 0
    out = capsys.readouterr().out
    assert "9" in out


def test_cli_sheet_on_csv_errors(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "--group", "arm", "--sheet", "S"]) == 2
    assert "엑셀" in capsys.readouterr().err


def test_cli_delimiter_on_xlsx_errors(tmp_path, capsys):
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "a")]))])
    assert main([p, "--delimiter", ","]) == 2
    assert "CSV" in capsys.readouterr().err


def test_cli_unknown_sheet_errors(tmp_path, capsys):
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "a")]))])
    assert main([p, "--sheet", "nope"]) == 2
    assert "시트" in capsys.readouterr().err


def test_cli_weights_and_effect_warns_not_crashes(tmp_path, capsys):
    assert main([_write_csv(tmp_path), "--group", "arm", "--weights", "w",
                 "--effect", "--padjust", "holm"]) == 0
    out = capsys.readouterr().out
    assert "차이 (95% CI)" not in out
    assert "p(보정)" not in out
