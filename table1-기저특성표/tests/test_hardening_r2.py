"""Round-2 adversarial-hardening regression + property tests.

Adds the structural invariant the panel flagged as the biggest gap (every
rendered markdown row has the header's column count), collapsed-vs-full
equivalence, the CSV/EN/k>=3 paths for Round-1 features, and regressions for
the Round-2 fixes: --test-cont welch/student no longer emits the contradictory
Levene note for k>=3, --ref to an absent level warns, and the
--missing-as-level percent-base legend is accurate.
"""

import csv as _csv
import io

import pytest

from table1.build import Options, build_table1
from table1.cli import main
from table1.dataio import Frame, load_frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _row(table, name):
    return next(r for r in table.rows if r.name == name)


def _binary_frame():
    rows = [("A", "F"), ("A", "M"), ("A", "M"), ("A", "F"),
            ("B", "M"), ("B", "M"), ("B", "F"), ("B", "M")]
    return _frame(["g", "sex"], rows)


def _mixed_frame():
    # continuous + binary + 3-level categorical, with some missing
    rows = [("A", 1.2, "F", "X"), ("A", 2.4, "M", "Y"), ("A", 3.1, "M", "Z"),
            ("A", "", "F", "X"), ("B", 5.5, "M", "Y"), ("B", 6.1, "F", "Z"),
            ("B", 7.0, "M", ""), ("B", 4.2, "M", "X")]
    return _frame(["g", "x", "sex", "site"], rows)


# --------------------------------------------------------------------------- #
# G1 (highest value): every rendered markdown row has the header's pipe count.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("overall", [True, False])
@pytest.mark.parametrize("show_p", [True, False])
@pytest.mark.parametrize("binary_single", [True, False])
def test_md_every_row_matches_header_column_count(overall, show_p, binary_single):
    opt = Options(group_col="g", overall=overall, show_pvalue=show_p,
                  binary_single=binary_single)
    md = render(build_table1(_mixed_frame(), opt), opt, "md")
    lines = [l for l in md.splitlines() if l.startswith("|")]
    n = lines[0].count("|")                       # header row
    assert all(l.count("|") == n for l in lines), \
        "a rendered row has a different column count than the header"


def test_md_column_count_three_groups_and_missing_level():
    rows = [("A", "Y"), ("A", "N"), ("A", ""), ("B", "Y"), ("B", "N"),
            ("C", "Y"), ("C", "N"), ("C", "")]
    opt = Options(group_col="g", categorical=["x"], missing_as_level=True)
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    lines = [l for l in md.splitlines() if l.startswith("|")]
    n = lines[0].count("|")
    assert all(l.count("|") == n for l in lines)


# --------------------------------------------------------------------------- #
# G2: collapsed binary row's p/SMD equal the non-collapsed row's.
# --------------------------------------------------------------------------- #
def test_binary_single_pvalue_smd_match_noncollapsed():
    fr = _binary_frame()
    r1 = _row(build_table1(fr, Options(group_col="g")), "sex")
    r2 = _row(build_table1(fr, Options(group_col="g", binary_single=True)), "sex")
    assert r1.pvalue == r2.pvalue
    assert r1.smd == r2.smd
    assert r1.test_name == r2.test_name


# --------------------------------------------------------------------------- #
# G3: stdin non-UTF-8 -> clean error (ValueError / exit 2), never a traceback.
# --------------------------------------------------------------------------- #
def test_load_frame_stdin_non_utf8(monkeypatch):
    class _S:
        buffer = io.BytesIO(b"g,x\nA,\xff\xfe\n")
    monkeypatch.setattr("sys.stdin", _S())
    with pytest.raises(ValueError, match="stdin|표준입력"):
        load_frame("-")


def test_cli_stdin_non_utf8_exit2(monkeypatch, capsys):
    class _S:
        buffer = io.BytesIO(b"g,x\nA,\xff\xfe\n")
    monkeypatch.setattr("sys.stdin", _S())
    rc = main(["-", "--group", "g"])
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# G4: --ref complement honored in CSV (not just markdown).
# --------------------------------------------------------------------------- #
def test_binary_single_ref_complement_csv():
    opt = Options(group_col="g", binary_single=True, ref={"sex": "M"})
    csv_text = render(build_table1(_binary_frame(), opt), opt, "csv")
    lines = csv_text.splitlines()
    assert any(l.startswith("sex,F,") for l in lines)
    assert not any(l.startswith("sex,M,") for l in lines)


# --------------------------------------------------------------------------- #
# G5: pct-decimals honored in the CSV path.
# --------------------------------------------------------------------------- #
def test_pct_decimals_csv():
    opt = Options(group_col="g", pct_decimals=3)
    csv_text = render(build_table1(_binary_frame(), opt), opt, "csv")
    assert "(50.000)" in csv_text


# --------------------------------------------------------------------------- #
# G6: type-conflict warning localizes to English.
# --------------------------------------------------------------------------- #
def test_type_conflict_warns_english():
    rows = [("A", 1), ("A", 2), ("B", 3), ("B", 4)]
    t = build_table1(_frame(["g", "x"], rows),
                     Options(group_col="g", continuous=["x"],
                             categorical=["x"], lang="en"))
    assert any("both --continuous and --categorical" in w for w in t.warnings)
    assert not any(any("가" <= c <= "힣" for c in w) for w in t.warnings)


# --------------------------------------------------------------------------- #
# G7: --test-cont welch/student with k>=3 -> ANOVA, and NO Levene caution note.
# --------------------------------------------------------------------------- #
def _three_group_unequal_var():
    return _frame(
        ["g", "x"],
        [("A", v) for v in (20, 21, 22, 23, 24)] +
        [("B", v) for v in (0, 20, 40, 60, 80)] +
        [("C", v) for v in (100, 101, 102, 103, 104)])


def test_test_cont_welch_three_groups_anova_no_levene_note():
    fr = _three_group_unequal_var()
    r = _row(build_table1(fr, Options(group_col="g", test_cont="welch")), "x")
    assert r.test_name == "One-way ANOVA"
    # the contradictory "consider Welch" Levene note must be suppressed
    assert not any("등분산" in n for n in r.notes)


def test_test_cont_auto_three_groups_keeps_levene_note():
    # sanity: auto mode still emits the Levene caution when variance is unequal
    fr = _three_group_unequal_var()
    r = _row(build_table1(fr, Options(group_col="g", test_cont="auto")), "x")
    assert r.test_name == "One-way ANOVA"
    assert any("등분산" in n for n in r.notes)


# --------------------------------------------------------------------------- #
# Round-2 fix: --ref to a level absent from the data warns.
# --------------------------------------------------------------------------- #
def test_ref_missing_level_warns():
    opt = Options(group_col="g", categorical=["sex"], binary_single=True,
                  ref={"sex": "ZZZ"})
    t = build_table1(_binary_frame(), opt)
    assert any("ZZZ" in w and ("무시" in w) for w in t.warnings)
    # and it still renders (falls back to the default shown level)
    md = render(t, opt, "md")
    assert "sex = M — n(%)" in md


def test_ref_missing_level_warns_english():
    opt = Options(group_col="g", categorical=["sex"], binary_single=True,
                  ref={"sex": "ZZZ"}, lang="en")
    t = build_table1(_binary_frame(), opt)
    assert any("ZZZ" in w and "ignored" in w for w in t.warnings)


# --------------------------------------------------------------------------- #
# Round-2 fix: forced-test legend does not claim a pre-test that didn't happen.
# --------------------------------------------------------------------------- #
def test_forced_test_legend_no_pretest_claim():
    rows = [("A", v) for v in range(10, 20)] + [("B", v) for v in range(12, 22)]
    opt = Options(group_col="g", continuous=["x"], test_cont="welch")
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "사전검정 없이" in md and "항상 Welch t" in md
    assert "정규성·등분산 점검 후" not in md


def test_nonparam_legend_wording():
    rows = [("A", v) for v in range(10, 20)] + [("B", v) for v in range(12, 22)]
    opt = Options(group_col="g", continuous=["x"], test_cont="nonparam")
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "정규성과 무관하게" in md and "Mann-Whitney U" in md


# --------------------------------------------------------------------------- #
# Round-2 fix: --missing-as-level percent-base legend is accurate.
# --------------------------------------------------------------------------- #
def test_missing_as_level_legend_says_incl_missing():
    rows = [("A", "Y"), ("A", ""), ("B", "N"), ("B", "")]
    opt = Options(group_col="g", categorical=["x"], missing_as_level=True)
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "'(결측)' 수준 포함" in md      # exact incl-missing legend wording
    assert "비결측" not in md            # the misleading non-missing wording is gone


def test_missing_as_level_legend_english():
    rows = [("A", "Y"), ("A", ""), ("B", "N"), ("B", "")]
    opt = Options(group_col="g", categorical=["x"], missing_as_level=True,
                  lang="en")
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "including the '(missing)' level" in md
    assert "% of non-missing" not in md


def test_no_missing_as_level_legend_still_non_missing():
    # default (no --missing-as-level): legend keeps the non-missing wording
    opt = Options(group_col="g")
    md = render(build_table1(_binary_frame(), opt), opt, "md")
    assert "비결측" in md
