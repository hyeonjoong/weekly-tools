"""Round-1 adversarial-hardening regression + property tests.

Covers the features and fixes added in the 2026-07-16 hardening round
(--test-cont, --binary-single/--ref, --pct-decimals, stdin, OSError handling,
--decimals/--pct-decimals bounds, type-conflict warning) plus previously
untested branches surfaced by the reviewer panel (--pct row rendering, the
3-group stat_undefined note, quantile n=1/n=2, delimiter aliases, CRLF, the
Fisher/chi-square expected==5 boundary, missing-as-level rendering) and a few
structural invariants (percentages sum to 100, mean within [min,max], no raw
cell data in notes).
"""

import io
import json
import os

import pytest

from table1.build import CategoricalRow, ContinuousRow, Options, build_table1
from table1.cli import main
from table1.dataio import Frame, load_frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _row(table, name):
    return next(r for r in table.rows if r.name == name)


# --------------------------------------------------------------------------- #
# --test-cont: explicit continuous-test control
# --------------------------------------------------------------------------- #
def _skewed_two_groups():
    # Non-normal (Shapiro would reject) so "auto" would pick Mann-Whitney.
    a = [1, 1, 1, 1, 1, 1, 2, 3, 50]
    b = [2, 4, 6, 8, 10, 12, 14, 16, 60]
    return _frame(["g", "x"], [("A", v) for v in a] + [("B", v) for v in b])


def test_test_cont_welch_forces_welch_even_when_nonnormal():
    fr = _skewed_two_groups()
    r = _row(build_table1(fr, Options(group_col="g", test_cont="welch")), "x")
    assert r.test_name == "Welch t"
    assert r.display == "mean"           # forcing parametric -> mean display


def test_test_cont_student_forces_student():
    # Grossly unequal variance would make "auto" pick Welch; "student" overrides.
    from table1.tests_stat import students_t
    a = (20, 21, 22, 23, 24, 25, 26, 27, 28, 29)
    b = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90)
    rows = [("A", v) for v in a] + [("B", v) for v in b]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", test_cont="student")), "x")
    assert r.test_name == "Student t"
    assert abs(r.pvalue - students_t(list(a), list(b)).pvalue) < 1e-12


def test_test_cont_nonparam_forces_mwu_on_normal_data():
    # Clean normal-ish data that "auto" would send to Student t.
    rows = ([("A", v) for v in range(10, 20)] +
            [("B", v) for v in range(12, 22)])
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", test_cont="nonparam")), "x")
    assert r.test_name == "Mann-Whitney U"
    assert r.display == "median"


def test_test_cont_nonparam_kruskal_three_groups():
    rows = ([("A", v) for v in range(10, 20)] +
            [("B", v) for v in range(12, 22)] +
            [("C", v) for v in range(14, 24)])
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", test_cont="nonparam")), "x")
    assert r.test_name == "Kruskal-Wallis"


def test_test_cont_welch_matches_direct_value():
    from table1.tests_stat import welch_t
    a = [1, 1, 1, 1, 1, 1, 2, 3, 50]
    b = [2, 4, 6, 8, 10, 12, 14, 16, 60]
    r = _row(build_table1(_skewed_two_groups(),
                          Options(group_col="g", test_cont="welch")), "x")
    assert abs(r.pvalue - welch_t(a, b).pvalue) < 1e-12


# --------------------------------------------------------------------------- #
# --binary-single / --ref
# --------------------------------------------------------------------------- #
def _binary_frame():
    rows = [("A", "F"), ("A", "M"), ("A", "M"), ("A", "F"),
            ("B", "M"), ("B", "M"), ("B", "F"), ("B", "M")]
    return _frame(["g", "sex"], rows)


def test_binary_single_collapses_to_one_row_md():
    opt = Options(group_col="g", binary_single=True)
    md = render(build_table1(_binary_frame(), opt), opt, "md")
    # single collapsed row shows the SECOND level (M) with p/SMD/test on it
    assert "sex = M — n(%)" in md
    # no separate F / M sub-rows and no blank header row
    assert "|  F |" not in md and "|  M |" not in md
    # p-value/test now sit on the collapsed row itself
    line = next(l for l in md.splitlines() if "sex = M" in l)
    assert "Pearson χ²" in line or "Fisher exact" in line


def test_binary_single_ref_selects_complement():
    # --ref sex=M means M is the reference -> show F instead.
    opt = Options(group_col="g", binary_single=True, ref={"sex": "M"})
    md = render(build_table1(_binary_frame(), opt), opt, "md")
    assert "sex = F — n(%)" in md
    assert "sex = M — n(%)" not in md


def test_binary_single_not_applied_to_three_levels():
    rows = [("A", "X"), ("A", "Y"), ("A", "Z"), ("B", "X"), ("B", "Y"), ("B", "Z")]
    opt = Options(group_col="g", binary_single=True, categorical=["c"])
    md = render(build_table1(_frame(["g", "c"], rows), opt), opt, "md")
    assert "c — n(%)" in md            # rendered normally (header + level rows)
    assert "c = " not in md


def test_binary_single_csv_carries_p_and_smd():
    import csv as _csv
    opt = Options(group_col="g", binary_single=True)
    t = build_table1(_binary_frame(), opt)
    csv_text = render(t, opt, "csv")
    rows = list(_csv.reader(csv_text.splitlines()))
    hdr = rows[0]
    m = next(r for r in rows if r[:2] == ["sex", "M"])
    assert len(m) == len(hdr)                       # no cell-count drift
    smd = m[hdr.index("smd")]
    assert smd not in ("", "—") and float(smd) >= 0.0
    pv = m[hdr.index("p_value")]
    assert pv in (">0.999", "<0.001") or 0.0 <= float(pv) <= 1.0
    # and they equal the underlying (non-collapsed) row stats (CSV shows 3 dp)
    r = _row(t, "sex")
    assert abs(float(smd) - r.smd) < 1e-3


def test_binary_single_disabled_with_missing_level():
    # A synthetic (결측) level makes three levels -> collapse must NOT trigger.
    rows = [("A", "Y"), ("A", "N"), ("A", ""), ("B", "Y"), ("B", "N"), ("B", "")]
    opt = Options(group_col="g", categorical=["x"], binary_single=True,
                  missing_as_level=True)
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "x = " not in md
    assert "x — n(%)" in md


# --------------------------------------------------------------------------- #
# --pct-decimals
# --------------------------------------------------------------------------- #
def test_pct_decimals_zero():
    opt = Options(group_col="g", pct_decimals=0)
    md = render(build_table1(_binary_frame(), opt), opt, "md")
    # integer percents: "2 (50)" not "2 (50.0)"
    assert "(50)" in md and "(50.0)" not in md


def test_pct_decimals_three():
    opt = Options(group_col="g", pct_decimals=3)
    md = render(build_table1(_binary_frame(), opt), opt, "md")
    assert "(50.000)" in md


# --------------------------------------------------------------------------- #
# --pct row rendering (previously untested)
# --------------------------------------------------------------------------- #
def test_pct_row_percentages_render():
    rows = [("A", "F"), ("A", "M"), ("A", "F"),
            ("B", "M"), ("B", "M"), ("B", "F")]
    opt = Options(group_col="g", categorical=["sex"], pct="row")
    md = render(build_table1(_frame(["g", "sex"], rows), opt), opt, "md")
    assert "행 기준" in md                          # legend switched to row-wise
    # F appears 2x in A, 1x in B -> row-wise 66.7 / 33.3
    assert "2 (66.7)" in md and "1 (33.3)" in md


def test_col_percentages_sum_to_100():
    rows = [("A", "F"), ("A", "M"), ("A", "F"),
            ("B", "M"), ("B", "M"), ("B", "F")]
    r = _row(build_table1(_frame(["g", "sex"], rows), Options(group_col="g")),
             "sex")
    for gi, denom in enumerate(r.denom_per_group):
        total = sum(l.counts[gi] for l in r.levels)
        assert abs(total / denom * 100 - 100.0) < 1e-9


# --------------------------------------------------------------------------- #
# 3-group constant -> stat_undefined note + suppressed p (not test_failed)
# --------------------------------------------------------------------------- #
def test_three_group_constant_pvalue_suppressed():
    rows = ([("A", 5), ("A", 5), ("A", 5)] + [("B", 5), ("B", 5), ("B", 5)] +
            [("C", 5), ("C", 5), ("C", 5)])
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    assert r.pvalue is None
    assert any("정의되지 않음" in n for n in r.notes)


# --------------------------------------------------------------------------- #
# quantile boundaries n=1 and n=2, and mean-within-[min,max] invariant
# --------------------------------------------------------------------------- #
def test_quantile_single_and_pair_groups():
    rows = [("A", "5"), ("A", ""), ("A", ""),
            ("B", "2"), ("B", "4"), ("B", "")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    a, b = r.per_group
    assert a.median == a.q1 == a.q3 == 5.0            # n=1
    assert abs(b.median - 3.0) < 1e-12               # n=2 interpolation
    assert abs(b.q1 - 2.5) < 1e-12 and abs(b.q3 - 3.5) < 1e-12


def test_mean_within_min_max_invariant():
    import random
    random.seed(7)
    rows = [("A" if i % 2 else "B", f"{random.gauss(3, 2):.4f}")
            for i in range(200)]
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    for st in [r.overall, *r.per_group]:
        assert st.vmin <= st.mean <= st.vmax
        assert st.vmin <= st.median <= st.vmax
        assert st.q1 <= st.median <= st.q3


# --------------------------------------------------------------------------- #
# Fisher/chi-square switch at the exact expected==5 boundary
# --------------------------------------------------------------------------- #
def test_expected_exactly_five_uses_chi_square():
    # 2x2 with every expected count exactly 5 -> min_expected==5 is NOT < 5,
    # so chi-square (not Fisher) is chosen.
    rows = ([("A", "Y")] * 5 + [("A", "N")] * 5 +
            [("B", "Y")] * 5 + [("B", "N")] * 5)
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    from table1.cat_tests import min_expected
    assert min_expected([[5, 5], [5, 5]]) == 5.0
    assert r.test_name == "Pearson χ²"


# --------------------------------------------------------------------------- #
# --missing-as-level markdown rendering
# --------------------------------------------------------------------------- #
def test_missing_as_level_rendered_in_markdown():
    rows = [("A", "Y"), ("A", "N"), ("A", ""), ("A", "Y"),
            ("B", "N"), ("B", "N"), ("B", ""), ("B", "Y")]
    opt = Options(group_col="g", categorical=["x"], missing_as_level=True)
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "결측" in md                    # a synthetic missing level row appears
    # its cells are rendered as counts, not blank
    miss_line = next(l for l in md.splitlines()
                     if "결측)" in l and "(" in l.split("결측)")[-1])
    assert "1" in miss_line


# --------------------------------------------------------------------------- #
# type-conflict warning
# --------------------------------------------------------------------------- #
def test_type_conflict_warns():
    rows = [("A", 1), ("A", 2), ("B", 3), ("B", 4)]
    t = build_table1(_frame(["g", "x"], rows),
                     Options(group_col="g", continuous=["x"], categorical=["x"]))
    # must be the type-conflict message specifically (mentions both flags/연속형)
    w = next(w for w in t.warnings if "continuous" in w)
    assert "categorical" in w and "연속형" in w
    assert _row(t, "x").kind == "continuous"


# --------------------------------------------------------------------------- #
# security: notes never echo a raw cell value
# --------------------------------------------------------------------------- #
def test_test_failed_note_has_no_raw_cell_value():
    # A zero-variance 2-group column hits the test_failed path; the note must be
    # a localized, data-free message — never the raw exception text or a cell
    # value (defense in depth for PII).
    rows = [("A", "5"), ("A", "5"), ("A", "5"), ("B", "5"), ("B", "5"), ("B", "5")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    for n in r.notes:
        assert "division by zero" not in n    # raw English exception never leaks
        assert "5" not in n                    # the raw cell value must never leak
    assert any("계산할 수 없" in n or "정의되지" in n or "정규성" in n
               for n in r.notes)


# --------------------------------------------------------------------------- #
# CSV loading: CRLF line endings, delimiter aliases
# --------------------------------------------------------------------------- #
def test_crlf_line_endings(tmp_path):
    p = tmp_path / "crlf.csv"
    p.write_bytes(b"g,x\r\nA,1\r\nA,2\r\nB,3\r\nB,4\r\n")
    fr = load_frame(str(p))
    assert fr.header == ["g", "x"]
    assert fr.nrows == 4
    assert "\r" not in "".join(fr.column("x"))       # no stray CR in cells


def test_cli_delimiter_alias_word_tab(tmp_path, capsys):
    p = tmp_path / "d.tsv"
    p.write_text("g\tx\nA\t1\nA\t2\nB\t3\nB\t4\n", encoding="utf-8")
    for alias in ("tab", "TAB"):
        rc = main([str(p), "--group", "g", "--delimiter", alias])
        assert rc == 0
    assert "x" in capsys.readouterr().out


def test_cli_delimiter_alias_pipe(tmp_path, capsys):
    p = tmp_path / "d.psv"
    p.write_text("g|x\nA|1\nA|2\nB|3\nB|4\n", encoding="utf-8")
    rc = main([str(p), "--group", "g", "--delimiter", "\\|"])
    assert rc == 0
    assert "x" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# stdin support
# --------------------------------------------------------------------------- #
def test_load_frame_stdin(monkeypatch):
    class _Stdin:
        buffer = io.BytesIO("g,x\nA,1\nA,2\nB,3\nB,4\n".encode("utf-8-sig"))
    monkeypatch.setattr("sys.stdin", _Stdin())
    fr = load_frame("-")
    assert fr.header == ["g", "x"] and fr.nrows == 4


def test_cli_stdin_end_to_end(monkeypatch, capsys):
    class _Stdin:
        buffer = io.BytesIO(b"g,x\nA,1\nA,2\nA,3\nB,4\nB,5\nB,6\n")
    monkeypatch.setattr("sys.stdin", _Stdin())
    rc = main(["-", "--group", "g"])
    assert rc == 0
    assert "표 1" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# OSError / input-path robustness (no raw traceback reaches the user)
# --------------------------------------------------------------------------- #
def test_cli_directory_input_clean_error(tmp_path, capsys):
    rc = main([str(tmp_path), "--group", "g"])
    assert rc == 2
    assert "폴더" in capsys.readouterr().err


def test_cli_decimals_upper_bound(tmp_path, capsys):
    p = tmp_path / "d.csv"
    p.write_text("g,x\nA,1\nA,2\nB,3\nB,4\n", encoding="utf-8")
    rc = main([str(p), "--group", "g", "--decimals", "999"])
    assert rc == 2
    assert "decimals" in capsys.readouterr().err


def test_cli_pct_decimals_bounds(tmp_path, capsys):
    p = tmp_path / "d.csv"
    p.write_text("g,x\nA,1\nA,2\nB,3\nB,4\n", encoding="utf-8")
    rc = main([str(p), "--group", "g", "--pct-decimals", "-1"])
    assert rc == 2
    assert "pct-decimals" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# property: SMD is non-negative and p in [0,1] over randomized inputs
# --------------------------------------------------------------------------- #
def test_smd_nonneg_and_p_in_unit_interval():
    import random
    random.seed(11)
    for trial in range(25):
        n = random.randint(6, 40)
        rows = [("A" if random.random() < 0.5 else "B",
                 f"{random.gauss(0, 1):.3f}") for _ in range(n)]
        # guarantee both groups present
        rows += [("A", "0.1"), ("A", "0.2"), ("B", "0.3"), ("B", "0.4")]
        t = build_table1(_frame(["g", "x"], rows), Options(group_col="g"))
        r = _row(t, "x")
        if r.smd is not None:
            assert r.smd >= 0.0
        if r.pvalue is not None:
            assert 0.0 <= r.pvalue <= 1.0
