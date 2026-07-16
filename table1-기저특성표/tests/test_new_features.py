"""Integration tests for --effect, --padjust and the HTML output format."""

import csv as _csv
import io
import json
from xml.dom import minidom

import pytest

from table1.build import Options, build_table1
from table1.cli import main
from table1.dataio import Frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _two_group():
    rows = ([("A", 1, "F"), ("A", 2, "M"), ("A", 3, "F"), ("A", 4, "M")] +
            [("B", 5, "M"), ("B", 6, "F"), ("B", 7, "M"), ("B", 8, "M")])
    return _frame(["g", "x", "sex"], rows)


# --------------------------------------------------------------------------- #
# --effect
# --------------------------------------------------------------------------- #
def test_effect_column_present_only_when_requested():
    fr = _two_group()
    o0 = Options(group_col="g", effect=False)
    assert "차이" not in render(build_table1(fr, o0), o0, "md")
    o1 = Options(group_col="g", effect=True)
    assert "차이 (95% CI)" in render(build_table1(fr, o1), o1, "md")


def test_effect_direction_is_group1_minus_group2():
    # x: A mean = 2.5, B mean = 6.5 -> difference (A - B) = -4.0.
    fr = _two_group()
    o = Options(group_col="g", effect=True)
    t = build_table1(fr, o)
    xrow = [r for r in t.rows if r.name == "x"][0]
    assert xrow.effect.estimate == pytest.approx(2.5 - 6.5)


def test_effect_absent_for_three_groups_but_padjust_present():
    rows = [("A", 1), ("A", 2), ("A", 3), ("B", 5), ("B", 6), ("B", 7),
            ("C", 9), ("C", 10), ("C", 11)]
    fr = _frame(["g", "x"], rows)
    o = Options(group_col="g", effect=True, padjust="holm")
    md = render(build_table1(fr, o), o, "md")
    assert "차이" not in md          # effect only defined for 2 groups
    assert "p(보정)" in md


def test_effect_matches_reported_test_kind():
    # A continuous var summarized parametrically -> mean_diff; the effect kind
    # must line up with the test actually chosen.
    fr = _two_group()
    o = Options(group_col="g", effect=True)
    t = build_table1(fr, o)
    xrow = [r for r in t.rows if r.name == "x"][0]
    assert xrow.test_name in ("Student t", "Welch t")
    assert xrow.effect.kind == "mean_diff"


def test_risk_difference_index_is_second_level_by_default():
    fr = _two_group()
    o = Options(group_col="g", effect=True)
    t = build_table1(fr, o)
    sex = [r for r in t.rows if r.name == "sex"][0]
    # levels sorted [F, M], index = M by default.
    # p_M(A) = 2/4 = 0.5, p_M(B) = 3/4 = 0.75 -> RD = -0.25.
    assert sex.effect.kind == "risk_diff"
    assert sex.effect.estimate == pytest.approx(0.5 - 0.75)


def test_ref_flips_risk_difference_sign():
    fr = _two_group()
    o_def = Options(group_col="g", effect=True)
    o_ref = Options(group_col="g", effect=True, ref={"sex": "M"})
    e_def = [r for r in build_table1(fr, o_def).rows if r.name == "sex"][0].effect
    e_ref = [r for r in build_table1(fr, o_ref).rows if r.name == "sex"][0].effect
    assert e_def.estimate == pytest.approx(-e_ref.estimate)


def test_risk_difference_shown_in_percentage_points():
    fr = _two_group()
    o = Options(group_col="g", effect=True)
    md = render(build_table1(fr, o), o, "md")
    assert "%p" in md               # risk difference rendered in percentage pts


def test_effect_on_binary_single_row():
    fr = _two_group()
    o = Options(group_col="g", effect=True, binary_single=True)
    md = render(build_table1(fr, o), o, "md")
    # the collapsed sex row carries the effect on the same line
    line = [l for l in md.splitlines() if l.startswith("| sex = ")][0]
    assert "%p" in line


# --------------------------------------------------------------------------- #
# --padjust
# --------------------------------------------------------------------------- #
def test_padjust_column_and_values():
    fr = _two_group()
    o = Options(group_col="g", padjust="bonferroni")
    t = build_table1(fr, o)
    # two testable variables -> bonferroni multiplies by 2 (capped at 1).
    for r in t.rows:
        if r.pvalue is not None:
            assert r.p_adjusted == pytest.approx(min(1.0, r.pvalue * 2))
    assert "p(보정)" in render(t, o, "md")


def test_padjust_hidden_when_no_pvalue():
    fr = _two_group()
    o = Options(group_col="g", padjust="holm", show_pvalue=False)
    md = render(build_table1(fr, o), o, "md")
    assert "p(보정)" not in md and "p값" not in md


def test_padjust_legend_names_method_and_family_size():
    fr = _two_group()
    o = Options(group_col="g", padjust="bh")
    md = render(build_table1(fr, o), o, "md")
    assert "Benjamini-Hochberg" in md
    assert "변수 2개" in md          # x and sex are testable


def test_padjust_default_none_no_column():
    fr = _two_group()
    o = Options(group_col="g")
    md = render(build_table1(fr, o), o, "md")
    assert "p(보정)" not in md


# --------------------------------------------------------------------------- #
# HTML output
# --------------------------------------------------------------------------- #
def test_html_is_well_formed_xml():
    fr = _two_group()
    o = Options(group_col="g", effect=True, padjust="holm")
    h = render(build_table1(fr, o), o, "html")
    tbl = h[h.index("<table"):h.index("</table>") + len("</table>")]
    minidom.parseString(tbl)         # raises if malformed


def test_html_escapes_injection_in_data():
    rows = [("<img src=x onerror=alert(1)>", 1), ("<img src=x onerror=alert(1)>", 2),
            ("B", 3), ("B", 4)]
    fr = _frame(["g", "x"], rows)
    o = Options(group_col="g")
    h = render(build_table1(fr, o), o, "html")
    assert "<img" not in h
    assert "&lt;img" in h


def test_html_cli_smoke(tmp_path, capsys):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("g,x,sex\nA,1,F\nA,2,M\nB,3,M\nB,4,F\n", encoding="utf-8")
    rc = main([str(csv_path), "--group", "g", "--effect", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "<table" in out and "차이 (95% CI)" in out


# --------------------------------------------------------------------------- #
# JSON structure
# --------------------------------------------------------------------------- #
def test_json_contains_effect_and_padjusted():
    fr = _two_group()
    o = Options(group_col="g", effect=True, padjust="holm")
    obj = json.loads(render(build_table1(fr, o), o, "json"))
    assert obj["meta"]["padjust"] == "holm"
    assert obj["meta"]["effect"] is True
    for r in obj["rows"]:
        assert "p_adjusted" in r and "effect" in r
    xrow = [r for r in obj["rows"] if r["name"] == "x"][0]
    assert xrow["effect"]["kind"] == "mean_diff"
    assert "ci_low" in xrow["effect"] and "ci_high" in xrow["effect"]


def test_csv_appends_effect_and_padjusted_columns():
    fr = _two_group()
    o = Options(group_col="g", effect=True, padjust="holm")
    text = render(build_table1(fr, o), o, "csv")
    recs = [r for r in _csv.reader(io.StringIO(text)) if r]
    assert "effect_95ci" in recs[0]
    assert "p_adjusted" in recs[0]
    # every row has the same number of fields as the header
    assert len({len(r) for r in recs}) == 1


def test_cli_padjust_and_effect_end_to_end(tmp_path, capsys):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("g,x,sex\nA,1,F\nA,2,M\nA,3,F\nB,5,M\nB,6,F\nB,7,M\n",
                        encoding="utf-8")
    rc = main([str(csv_path), "--group", "g", "--effect", "--padjust", "bh"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "차이 (95% CI)" in out and "p(보정)" in out


# --------------------------------------------------------------------------- #
# Round-2 hardening: no-group descriptive mode, index labels, inf, invariants
# --------------------------------------------------------------------------- #
def _multi_level():
    rows = [("A", 1, "x"), ("A", 2, "y"), ("A", 3, "z"),
            ("B", 5, "x"), ("B", 6, "y"), ("B", 7, "z")]
    return _frame(["g", "n", "grp3"], rows)


def test_no_group_descriptive_table():
    fr = _two_group()
    o = Options(group_col=None)
    t = build_table1(fr, o)
    assert t.meta["single_group"] is True
    assert t.groups == ["전체"] and t.group_sizes == [8]
    md = render(t, o, "md")
    # Single Overall column; no comparison columns at all.
    assert "전체 (N=8)" in md
    for token in ("검정", "p값", "SMD", "차이"):
        assert token not in md


def test_no_group_english_label():
    t = build_table1(_two_group(), Options(group_col=None, lang="en"))
    assert t.groups == ["Overall"]


def test_no_group_cli(tmp_path, capsys):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("x,sex\n1,F\n2,M\n3,F\n4,M\n", encoding="utf-8")
    rc = main([str(csv_path)])            # no --group
    assert rc == 0
    out = capsys.readouterr().out
    assert "전체 (N=4)" in out
    assert "Test" not in out and "검정" not in out


def test_no_group_all_formats_consistent():
    import csv as _csv
    fr = _two_group()
    o = Options(group_col=None)
    t = build_table1(fr, o)
    md = [l for l in render(t, o, "md").splitlines() if l.startswith("|")]
    assert len({l.count("|") for l in md}) == 1
    for fmt in ("csv", "tsv"):
        d = "," if fmt == "csv" else "\t"
        recs = [r for r in _csv.reader(io.StringIO(render(t, o, fmt)), delimiter=d) if r]
        assert len({len(r) for r in recs}) == 1
        assert "test" not in recs[0] and "p_value" not in recs[0]
    h = render(t, o, "html")
    minidom.parseString(h[h.index("<table"):h.index("</table>") + len("</table>")])
    json.loads(render(t, o, "json"))


def test_grouped_error_hints_at_no_group():
    # A group column with only one level errors, but points at the no-group mode.
    fr = _frame(["g", "x"], [("A", 1), ("A", 2), ("A", 3)])
    with pytest.raises(ValueError) as exc:
        build_table1(fr, Options(group_col="g"))
    assert "--group" in str(exc.value)


def test_md_column_invariant_multilevel_effect_padjust():
    # A ≥3-level categorical exercises header + level rows with all columns on.
    fr = _multi_level()
    o = Options(group_col="g", effect=True, padjust="holm")
    md = [l for l in render(build_table1(fr, o), o, "md").splitlines()
          if l.startswith("|")]
    assert len({l.count("|") for l in md}) == 1


def test_html_column_invariant_multilevel():
    import re
    fr = _multi_level()
    o = Options(group_col="g", effect=True, padjust="holm")
    h = render(build_table1(fr, o), o, "html")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", h, flags=re.S)
    counts = [r.count("<th") + r.count("<td") for r in rows]
    assert len(set(counts)) == 1


def test_risk_diff_index_level_shown_in_table():
    fr = _two_group()
    o = Options(group_col="g", effect=True)
    md = render(build_table1(fr, o), o, "md")
    # The binary sex row's effect cell must name the index level.
    sexline = [l for l in md.splitlines() if l.startswith("| sex ")][0]
    assert "M:" in sexline


def test_build_rejects_unknown_padjust_via_api():
    with pytest.raises(ValueError):
        build_table1(_two_group(), Options(group_col="g", padjust="sidak"))


def test_effect_none_renders_blank_not_crash():
    # A continuous column constant within both groups -> no effect, blank cell.
    rows = [("A", 5, ), ("A", 5), ("A", 5), ("B", 5), ("B", 5), ("B", 5)]
    fr = _frame(["g", "x"], rows)
    o = Options(group_col="g", effect=True)
    t = build_table1(fr, o)
    xrow = [r for r in t.rows if r.name == "x"][0]
    assert xrow.effect is None
    md = render(t, o, "md")           # must not raise
    assert "차이 (95% CI)" in md


def test_padjust_column_suppressed_when_no_testable_variable():
    # Every variable untestable (constant) -> no adjusted-p column or legend.
    rows = [("A", 5), ("A", 5), ("B", 5), ("B", 5)]
    fr = _frame(["g", "x"], rows)
    o = Options(group_col="g", padjust="bonferroni")
    md = render(build_table1(fr, o), o, "md")
    assert "p(보정)" not in md
    assert "변수 0개" not in md


def test_html_escapes_variable_name_and_level_label():
    rows = [("A", "<b>lvl</b>"), ("A", "<b>lvl</b>"), ("B", "z"), ("B", "z")]
    fr = _frame(["g", "<script>v</script>"], rows)
    o = Options(group_col="g")
    h = render(build_table1(fr, o), o, "html")
    assert "<script>" not in h and "<b>lvl</b>" not in h
    assert "&lt;script&gt;" in h


def test_md_escapes_variable_name_and_level_label():
    rows = [("A", "a|b"), ("A", "a|b"), ("B", "z"), ("B", "z")]
    fr = _frame(["g", "v"], rows)
    o = Options(group_col="g")
    md = render(build_table1(fr, o), o, "md")
    # a pipe inside a level label must be escaped so the table doesn't break
    assert "a\\|b" in md


def test_fmt_num_handles_infinity():
    from table1.render import _fmt_num
    assert _fmt_num(float("inf"), 1) == "∞"
    assert _fmt_num(float("-inf"), 1) == "-∞"
    assert _fmt_num(float("nan"), 1) == "—"
