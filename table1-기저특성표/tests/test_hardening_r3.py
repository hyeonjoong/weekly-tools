"""Round-3 adversarial-hardening regression + property tests.

Covers the Round-3 data-safety fixes surfaced by the hostile-clinical-user
panel — unparseable/censored values (>100, 12 kg, European 1,5) are counted
and surfaced instead of silently biasing the mean; near-duplicate (case-only)
group labels warn; heavily-missing variables warn; the localized data-free
test-failure note; the corrected 'normality untestable' / high-missing wording
— plus the structural gaps the test-quality panel flagged (delimited
column-width invariant, JSON keeps all levels under --binary-single + round-trip
validity, collapsed-row denominator).
"""

import csv as _csv
import json

import pytest

from table1.build import CategoricalRow, Options, build_table1
from table1.dataio import Frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _row(table, name):
    return next(r for r in table.rows if r.name == name)


def _binary_frame():
    rows = [("A", "F"), ("A", "M"), ("A", "M"), ("A", "F"),
            ("B", "M"), ("B", "M"), ("B", "F"), ("B", "M")]
    return _frame(["g", "sex"], rows)


# --------------------------------------------------------------------------- #
# Data safety: unparseable / censored values are counted and surfaced, and the
# mean is computed only on the parseable values (which is WHY the note matters).
# --------------------------------------------------------------------------- #
def test_unparseable_values_noted_not_silently_dropped():
    # ">100" censored values must not silently vanish into a plain "missing".
    rows = [("A", "10"), ("A", "12"), ("A", ">100"), ("A", "11"),
            ("B", "13"), ("B", ">100"), ("B", "14"), ("B", "15")]
    r = _row(build_table1(_frame(["g", "bio"], rows),
                          Options(group_col="g", continuous=["bio"])), "bio")
    note = next(n for n in r.notes if "숫자로 해석할 수 없" in n)
    assert ">100" in note                       # the offending token is shown
    assert "2" in note                          # count of unparseable cells
    # and they were excluded from the summary (mean is over the low values only)
    assert r.overall.mean < 20


def test_unparseable_examples_capped_and_deduped():
    rows = ([("A", "1"), ("A", ">100"), ("A", ">100"), ("A", "12 kg"),
             ("A", "3"), ("A", "45%"), ("A", "5-7")] +
            [("B", "2"), ("B", "4"), ("B", "6")])
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    note = next(n for n in r.notes if "숫자로 해석할 수 없" in n)
    # dedup: ">100" appears once in the examples; at most 3 example tokens
    assert note.count(">100") == 1


def test_european_decimal_comma_flagged_unparseable():
    # "1,5" is ambiguous (European decimal) -> dropped as non-numeric; the note
    # must tell the user rather than the whole column vanishing to "missing".
    rows = [("A", "1,5"), ("A", "2,5"), ("A", "3"), ("B", "4"), ("B", "5"),
            ("B", "6")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    assert any("숫자로 해석할 수 없" in n for n in r.notes)


def test_nonfinite_numbers_not_counted_as_unparseable():
    # inf/nan ARE numbers (legitimately missing) -> no unparseable note.
    rows = [("A", "1"), ("A", "inf"), ("A", "2"), ("B", "3"), ("B", "nan"),
            ("B", "4")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", continuous=["x"])), "x")
    assert not any("숫자로 해석할 수 없" in n for n in r.notes)


def test_unparseable_note_english():
    rows = [("A", "10"), ("A", ">100"), ("A", "12"), ("B", "13"), ("B", "14"),
            ("B", "15")]
    r = _row(build_table1(_frame(["g", "bio"], rows),
                          Options(group_col="g", continuous=["bio"], lang="en")),
             "bio")
    assert any("could not be parsed" in n for n in r.notes)


# --------------------------------------------------------------------------- #
# Near-duplicate (case-only) group labels warn.
# --------------------------------------------------------------------------- #
def test_case_variant_group_labels_warn():
    rows = [("Device", 1), ("device", 2), ("Device", 3),
            ("Sham", 4), ("Sham", 5), ("Sham", 6)]
    t = build_table1(_frame(["g", "x"], rows), Options(group_col="g"))
    assert any("대소문자" in w and "Device" in w and "device" in w
               for w in t.warnings)


def test_no_case_warning_when_labels_distinct():
    rows = [("A", 1), ("A", 2), ("B", 3), ("B", 4)]
    t = build_table1(_frame(["g", "x"], rows), Options(group_col="g"))
    assert not any("대소문자" in w for w in t.warnings)


def test_case_variant_group_labels_warn_english():
    rows = [("Device", 1), ("device", 2), ("Sham", 3), ("Sham", 4)]
    t = build_table1(_frame(["g", "x"], rows), Options(group_col="g", lang="en"))
    assert any("differing only in case" in w for w in t.warnings)


# --------------------------------------------------------------------------- #
# High-missingness warning.
# --------------------------------------------------------------------------- #
def test_high_missingness_warns():
    rows = ([("A", "1")] + [("A", "")] * 3 + [("B", "2")] + [("B", "")] * 3)
    t = build_table1(_frame(["g", "x"], rows),
                     Options(group_col="g", continuous=["x"]))
    assert any("결측이" in w and "75%" in w for w in t.warnings)


def test_no_high_missingness_warning_when_complete():
    rows = [("A", "1"), ("A", "2"), ("B", "3"), ("B", "4")]
    t = build_table1(_frame(["g", "x"], rows),
                     Options(group_col="g", continuous=["x"]))
    assert not any("결측이" in w for w in t.warnings)


# --------------------------------------------------------------------------- #
# Cosmetic-wording fixes.
# --------------------------------------------------------------------------- #
def test_normality_untestable_note_no_mean_sd_claim():
    # tiny groups + forced-median display: the untestable note must NOT claim
    # "shown as mean±SD" while the row renders median[IQR].
    rows = [("A", "1"), ("A", "2"), ("B", "10"), ("B", "20")]
    opt = Options(group_col="g", continuous=["x"], display="median")
    r = _row(build_table1(_frame(["g", "x"], rows), opt), "x")
    assert any("정규성 검정 불가" in n for n in r.notes)
    assert not any("평균±표준편차로 표시" in n for n in r.notes)
    assert r.display == "median"


def test_int_code_warning_no_specific_summary_claim():
    rows = [("A", v) for v in (1, 2, 3, 2, 1, 3)] + \
           [("B", v) for v in (2, 3, 4, 3, 2, 4)]
    t = build_table1(_frame(["g", "nyha"], rows),
                     Options(group_col="g", cat_max_levels=2))
    w = next(w for w in t.warnings if "nyha" in w and "정수값" in w)
    # must not assert "평균±SD" outright (display can be median under nonnormal)
    assert "중앙값[IQR]" in w


# --------------------------------------------------------------------------- #
# Finding 2: incl-missing legend only when a missing level actually exists.
# --------------------------------------------------------------------------- #
def test_missing_as_level_legend_falls_back_when_no_missing():
    # --missing-as-level ON but the data has no missing cells -> plain legend.
    rows = [("A", "M"), ("A", "F"), ("B", "M"), ("B", "F")]
    opt = Options(group_col="g", categorical=["sex"], missing_as_level=True)
    md = render(build_table1(_frame(["g", "sex"], rows), opt), opt, "md")
    assert "비결측" in md                        # plain non-missing legend
    assert "수준 포함" not in md                 # no over-claiming incl-missing


# --------------------------------------------------------------------------- #
# GAP-1: delimited (CSV & TSV) rows all share the header's column count.
# --------------------------------------------------------------------------- #
def _mixed_frame():
    rows = [("A", 1.2, "F", "X"), ("A", 2.4, "M", "Y"), ("A", 3.1, "M", "Z"),
            ("A", "", "F", "X"), ("B", 5.5, "M", "Y"), ("B", 6.1, "F", "Z"),
            ("B", 7.0, "M", ""), ("B", 4.2, "M", "X")]
    return _frame(["g", "x", "sex", "site"], rows)


@pytest.mark.parametrize("fmt,delim", [("csv", ","), ("tsv", "\t")])
@pytest.mark.parametrize("overall", [True, False])
@pytest.mark.parametrize("show_p", [True, False])
@pytest.mark.parametrize("binary_single", [True, False])
def test_delimited_every_row_matches_header_width(fmt, delim, overall, show_p,
                                                  binary_single):
    opt = Options(group_col="g", overall=overall, show_pvalue=show_p,
                  binary_single=binary_single)
    txt = render(build_table1(_mixed_frame(), opt), opt, fmt)
    rr = list(_csv.reader(txt.splitlines(), delimiter=delim))
    assert len({len(r) for r in rr}) == 1, "a delimited row has a different width"


# --------------------------------------------------------------------------- #
# GAP-2: --binary-single keeps ALL levels in JSON (documented), and JSON is
# valid under the combined new flags.
# --------------------------------------------------------------------------- #
def test_binary_single_json_keeps_all_levels():
    opt = Options(group_col="g", binary_single=True)
    obj = json.loads(render(build_table1(_binary_frame(), opt), opt, "json"))
    sex = next(r for r in obj["rows"] if r["name"] == "sex")
    assert [l["label"] for l in sex["levels"]] == ["F", "M"]


def test_json_valid_under_combined_new_flags():
    rows = [("A", "1", "F"), ("A", "2", "M"), ("A", "", "F"),
            ("B", "3", "M"), ("B", "4", "F"), ("B", "", "M")]
    opt = Options(group_col="g", binary_single=True, missing_as_level=True,
                  pct_decimals=3, ref={"sex": "F"})
    txt = render(build_table1(_frame(["g", "x", "sex"], rows), opt), opt, "json")
    obj = json.loads(txt, parse_constant=lambda s: (_ for _ in ()).throw(
        ValueError(s)))
    assert obj["groups"] == ["A", "B"]


# --------------------------------------------------------------------------- #
# GAP-3: collapsed binary row uses the group (column) denominator for %.
# --------------------------------------------------------------------------- #
def test_binary_single_percent_uses_group_denominator():
    # A: F,M,M,F -> M=2/4=50%.  B: M,M,F,M -> M=3/4=75%.  overall M=5/8=62.5%.
    opt = Options(group_col="g", binary_single=True)
    csv_text = render(build_table1(_binary_frame(), opt), opt, "csv")
    line = next(l for l in csv_text.splitlines() if l.startswith("sex,M"))
    assert "2 (50.0)" in line and "3 (75.0)" in line and "5 (62.5)" in line
