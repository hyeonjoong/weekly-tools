"""Branch-selection coverage and the hardening-round features.

Covers: Welch vs Student selection, Fisher/chi-square switching, the ANOVA
Levene note, the numeric-ordinal foot-gun warning, the missing-as-level
sentinel-collision fix, the Shapiro n>5000 cap, and the render options
(--no-pvalue, --range, --lang en, --labels), plus JSON/Markdown safety.
"""

import json
import math

from table1.build import CategoricalRow, Options, build_table1
from table1.dataio import Frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _row(table, name):
    return next(r for r in table.rows if r.name == name)


# --------------------------------------------------------------------------- #
# continuous test selection
# --------------------------------------------------------------------------- #
def test_welch_selected_on_unequal_variance():
    # Both groups normal, grossly unequal variance -> Levene rejects -> Welch t.
    rows = [("A", v) for v in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29)] + \
           [("B", v) for v in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90)]
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    assert r.test_name == "Welch t"


def test_anova_levene_note_on_unequal_variance():
    rows = ([("A", v) for v in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29)] +
            [("B", v) for v in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90)] +
            [("C", v) for v in (100, 101, 102, 103, 104, 105, 106, 107, 108, 109)])
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    assert r.test_name == "One-way ANOVA"
    assert any("등분산" in n for n in r.notes)


def test_numeric_ordinal_warning():
    rows = [("A", v) for v in (1, 2, 3, 2, 1, 3)] + \
           [("B", v) for v in (2, 3, 4, 3, 2, 4)]
    t = build_table1(_frame(["g", "nyha"], rows),
                     Options(group_col="g", cat_max_levels=2))
    # auto-classified continuous (4 distinct) but flagged as a likely code
    assert any("nyha" in w and "정수값" in w for w in t.warnings)


def test_no_ordinal_warning_when_forced_continuous():
    rows = [("A", v) for v in (1, 2, 3, 2, 1, 3)] + \
           [("B", v) for v in (2, 3, 4, 3, 2, 4)]
    t = build_table1(_frame(["g", "nyha"], rows),
                     Options(group_col="g", continuous=["nyha"]))
    assert not any("정수값" in w for w in t.warnings)


def test_mann_whitney_selected_and_value():
    # Two strongly skewed groups -> Shapiro rejects -> the k=2 nonparametric
    # branch (build.py) selects Mann-Whitney; its p must equal the direct test.
    from table1.tests_stat import mann_whitney_u
    a = [1, 1, 1, 1, 1, 1, 2, 3, 50]
    b = [2, 4, 6, 8, 10, 12, 14, 16, 60]
    rows = [("A", v) for v in a] + [("B", v) for v in b]
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    assert r.test_name == "Mann-Whitney U"
    assert abs(r.pvalue - mann_whitney_u(a, b).pvalue) < 1e-12
    # cross-check the asymptotic value against SciPy 1.17.1
    assert abs(r.pvalue - 0.0060569342641845015) < 1e-9


def test_kruskal_selected_and_value():
    # Three skewed groups -> the k>=3 nonparametric branch selects Kruskal.
    from table1.tests_stat import kruskal_wallis
    g = [[1, 1, 1, 2, 3, 40], [2, 2, 3, 4, 50, 60], [1, 5, 5, 6, 7, 80]]
    rows = ([("A", v) for v in g[0]] + [("B", v) for v in g[1]] +
            [("C", v) for v in g[2]])
    r = _row(build_table1(_frame(["g", "x"], rows), Options(group_col="g")), "x")
    assert r.test_name == "Kruskal-Wallis"
    assert abs(r.pvalue - kruskal_wallis(g).pvalue) < 1e-12
    assert abs(r.pvalue - 0.1561902196876261) < 1e-9


def test_nonfinite_column_stays_continuous():
    # inf / -inf / overflow (1e999) are non-finite NUMBERS: they must be treated
    # as missing, NOT reclassified as categorical levels (which previously could
    # silently drop a real continuous variable as "too many levels").
    vals = [1.2, 3.4, 5.6, "inf", "-inf", 7.8, 9.0, "1e999", 2.1, 4.3, 6.5, 8.7]
    rows = [("A" if i % 2 else "B", v) for i, v in enumerate(vals)]
    t = build_table1(_frame(["g", "sbp"], rows), Options(group_col="g"))
    r = _row(t, "sbp")
    assert r.kind == "continuous"
    assert r.n_missing_total == 3            # inf, -inf, 1e999 counted as missing
    assert not any("ID/자유텍스트" in w for w in t.warnings)


# --------------------------------------------------------------------------- #
# categorical test switching
# --------------------------------------------------------------------------- #
def test_fisher_switch_on_small_expected():
    # 2x2 with a tiny expected cell -> Fisher exact chosen automatically.
    rows = [("A", "Y"), ("A", "N"), ("A", "N"), ("B", "N"), ("B", "N"), ("B", "N")]
    r = _row(build_table1(_frame(["g", "resp"], rows), Options(group_col="g")),
             "resp")
    assert r.test_name == "Fisher exact"


def test_chi_square_on_large_counts():
    rows = ([("A", "Y")] * 40 + [("A", "N")] * 40 +
            [("B", "Y")] * 30 + [("B", "N")] * 50)
    r = _row(build_table1(_frame(["g", "resp"], rows), Options(group_col="g")),
             "resp")
    assert r.test_name == "Pearson χ²"


def test_force_fisher():
    rows = ([("A", "Y")] * 40 + [("A", "N")] * 40 +
            [("B", "Y")] * 30 + [("B", "N")] * 50)
    r = _row(build_table1(_frame(["g", "resp"], rows),
                          Options(group_col="g", force_fisher=True)), "resp")
    assert r.test_name == "Fisher exact"


# --------------------------------------------------------------------------- #
# missing-as-level sentinel collision (regression)
# --------------------------------------------------------------------------- #
def test_missing_as_level_sentinel_collision():
    # A real category literally named "(결측)" must NOT merge with the synthetic
    # missing row. Data: real "(결측)" x2, "Y" x2, and 2 truly blank cells.
    rows = [("A", "(결측)"), ("A", "Y"), ("A", ""),
            ("B", "Y"), ("B", "(결측)"), ("B", "")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", categorical=["x"],
                                  missing_as_level=True)), "x")
    assert isinstance(r, CategoricalRow)
    labels = [l.label for l in r.levels]
    # exactly one real "(결측)" level and one distinct synthetic missing level
    assert labels.count("(결측)") == 1
    real = next(l for l in r.levels if l.label == "(결측)")
    assert real.overall == 2                    # the real values, not the blanks
    synth = next(l for l in r.levels if l.label != "(결측)" and "결측" in l.label)
    assert synth.overall == 2                   # the two blank cells
    assert r.n_missing_total == 2


def test_missing_as_level_excluded_from_test():
    rows = [("A", "Y"), ("A", "N"), ("A", ""), ("A", "Y"),
            ("B", "N"), ("B", "N"), ("B", ""), ("B", "Y")]
    r = _row(build_table1(_frame(["g", "x"], rows),
                          Options(group_col="g", categorical=["x"],
                                  missing_as_level=True)), "x")
    # a synthetic missing level is shown...
    assert any("결측" in l.label for l in r.levels)
    # ...but the test still runs on the 2 observed levels (Y/N)
    assert r.test_name in ("Pearson χ²", "Fisher exact")


# --------------------------------------------------------------------------- #
# Shapiro cap
# --------------------------------------------------------------------------- #
def test_shapiro_cap_note_above_5000():
    import random
    random.seed(0)
    rows = [("A" if i % 2 else "B", f"{random.gauss(0, 1):.4f}")
            for i in range(12000)]
    r = _row(build_table1(_frame(["g", "v"], rows), Options(group_col="g")), "v")
    assert any("5000" in n for n in r.notes)


# --------------------------------------------------------------------------- #
# render options
# --------------------------------------------------------------------------- #
def _demo():
    rows = [("A", 1, "F"), ("A", 2, "M"), ("A", 3, "F"), ("A", 4, "M"),
            ("B", 5, "M"), ("B", 6, "F"), ("B", 7, "M"), ("B", 8, "M")]
    return _frame(["g", "x", "sex"], rows)


def test_no_pvalue_hides_column():
    opt = Options(group_col="g", display="mean", show_pvalue=False)
    t = build_table1(_demo(), opt)
    md = render(t, opt, "md")
    assert "p값" not in md and "| p |" not in md
    # SMD still present (the recommended RCT balance metric)
    assert "SMD" in md
    csv_text = render(t, opt, "csv")
    assert "p_value" not in csv_text


def test_range_appends_min_max():
    opt = Options(group_col="g", display="mean", show_range=True)
    t = build_table1(_demo(), opt)
    md = render(t, opt, "md")
    assert "–" in md  # en-dash range separator present


def test_lang_en_labels():
    opt = Options(group_col="g", display="mean", lang="en",
                  labels={"x": "X score (units)"})
    t = build_table1(_demo(), opt)
    md = render(t, opt, "md")
    assert "Table 1. Baseline characteristics" in md
    assert "Characteristic" in md and "Overall (N=8)" in md
    assert "X score (units) — Mean (SD)" in md
    assert "Mean (SD)" in md


def test_tsv_render():
    opt = Options(group_col="g", display="mean")
    t = build_table1(_demo(), opt)
    tsv = render(t, opt, "tsv")
    assert "\t" in tsv
    assert "," not in tsv.splitlines()[0]  # header uses tabs, not commas


def test_csv_injection_all_triggers():
    # Level labels beginning with each of = + - @ must be neutralised.
    rows = [("A", "=EVIL"), ("A", "+1"), ("B", "-2"), ("B", "@SUM")]
    opt = Options(group_col="g", categorical=["x"])
    t = build_table1(_frame(["g", "x"], rows), opt)
    csv_text = render(t, opt, "csv")
    for payload in ("'=EVIL", "'+1", "'-2", "'@SUM"):
        assert payload in csv_text


def test_json_non_finite_is_null():
    # An empty group -> NaN stats; must serialize as null (strict JSON), not NaN.
    rows = [("A", "1"), ("A", "2"), ("A", "3"), ("B", ""), ("B", "NA")]
    opt = Options(group_col="g", continuous=["x"])
    t = build_table1(_frame(["g", "x"], rows), opt)
    text = render(t, opt, "json")
    assert "NaN" not in text and "Infinity" not in text
    # strictly valid JSON (parse_constant would fire on NaN/Infinity)
    obj = json.loads(text, parse_constant=lambda s: (_ for _ in ()).throw(
        ValueError(s)))
    assert obj["groups"] == ["A", "B"]


def test_markdown_html_injection_escaped():
    rows = [("A", "<img src=x onerror=alert(1)>"), ("A", "Y"),
            ("B", "Y"), ("B", "Y")]
    opt = Options(group_col="g", categorical=["x"])
    t = build_table1(_frame(["g", "x"], rows), opt)
    md = render(t, opt, "md")
    assert "<img" not in md
    assert "&lt;img" in md


def test_markdown_pipe_still_escaped():
    rows = [("A", "a|b"), ("A", "Y"), ("B", "Y"), ("B", "Y")]
    opt = Options(group_col="g", categorical=["x"])
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "a\\|b" in md


# --------------------------------------------------------------------------- #
# per-group missing breakdown + non-missing % base
# --------------------------------------------------------------------------- #
def test_per_group_missing_breakdown_rendered():
    # 3 missing in A, 1 in B -> the label must expose the differential split.
    rows = ([("A", "1"), ("A", ""), ("A", ""), ("A", ""), ("A", "5"), ("A", "6")]
            + [("B", "2"), ("B", ""), ("B", "4"), ("B", "5"), ("B", "6"), ("B", "7")])
    opt = Options(group_col="g", continuous=["x"])
    md = render(build_table1(_frame(["g", "x"], rows), opt), opt, "md")
    assert "결측 4 (A 3, B 1)" in md


def test_pct_base_labeled_non_missing():
    opt = Options(group_col="g")
    rows = [("A", "1", "F"), ("A", "2", "M"), ("B", "3", "F"), ("B", "4", "M")]
    md = render(build_table1(_frame(["g", "x", "sex"], rows), opt), opt, "md")
    assert "비결측" in md  # legend clarifies the % denominator excludes missing


# --------------------------------------------------------------------------- #
# --lang en fully localizes notes AND warnings (no Korean leaks)
# --------------------------------------------------------------------------- #
def _has_hangul(s):
    return any("가" <= ch <= "힣" for ch in s)


def test_lang_en_localizes_warnings():
    # subject_id triggers the too-many-levels warning; in English it must be
    # English, not Korean.
    rows = [("A", "S%02d" % i, i * 1.0) for i in range(30)] + \
           [("B", "S%02d" % (100 + i), i * 1.1) for i in range(30)]
    opt = Options(group_col="g", lang="en")
    t = build_table1(_frame(["g", "sid", "x"], rows), opt)
    assert t.warnings and not any(_has_hangul(w) for w in t.warnings)
    assert any("distinct values" in w for w in t.warnings)


def test_lang_en_localizes_notes():
    # A tiny group forces the normality-untestable note; must be English.
    rows = [("A", "1"), ("A", "2"), ("B", "3"), ("B", "4")]
    opt = Options(group_col="g", continuous=["x"], lang="en")
    r = _row(build_table1(_frame(["g", "x"], rows), opt), "x")
    assert r.notes and not any(_has_hangul(n) for n in r.notes)


def test_lang_en_full_output_has_no_hangul():
    rows = [("A", str(i), i * 1.0, "Y" if i % 2 else "N") for i in range(6)] + \
           [("B", str(100 + i), i * 1.3, "Y") for i in range(6)]
    opt = Options(group_col="g", lang="en")
    md = render(build_table1(_frame(["g", "sid", "x", "resp"], rows), opt),
                opt, "md")
    assert not _has_hangul(md)


# --------------------------------------------------------------------------- #
# CSV: negative tool-generated stat cells are NOT formula-escaped, but
# data-derived labels still are.
# --------------------------------------------------------------------------- #
def test_csv_negative_stat_not_escaped_but_label_is():
    rows = [("A", "-3.5", "=EVIL"), ("A", "-4.1", "=EVIL"), ("A", "-2.9", "x"),
            ("B", "-6.0", "-2"), ("B", "-5.5", "-2"), ("B", "-6.3", "x")]
    opt = Options(group_col="g", continuous=["z"], categorical=["cat"])
    csv_text = render(build_table1(_frame(["g", "z", "cat"], rows), opt),
                      opt, "csv")
    # tool-generated negative mean cell: no stray leading apostrophe
    assert "z,평균(SD),'-" not in csv_text
    assert ",-4.7 " in csv_text or "-4.7 (" in csv_text
    # data-derived labels beginning with a trigger: still neutralized
    assert "'=EVIL" in csv_text
    assert "'-2" in csv_text
