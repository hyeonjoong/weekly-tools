"""End-to-end tests for --group-order, --trend / --trend-scores and --format latex."""

from __future__ import annotations

import json
import math

import pytest

from table1.build import Options, build_table1
from table1.cli import main
from table1.dataio import load_frame
from table1.render import render
from table1.trend import cochran_armitage, jonckheere_terpstra, linear_contrast

HEAD = "id,dose,age,resp,site\n"
# 3 ordered arms x 6 subjects; age rises with dose, resp (0/1) rises with dose.
BODY = (
    "1,placebo,50,0,A\n2,placebo,52,0,B\n3,placebo,49,0,A\n"
    "4,placebo,51,1,C\n5,placebo,48,0,B\n6,placebo,53,0,A\n"
    "7,low,55,0,A\n8,low,57,1,B\n9,low,54,1,C\n"
    "10,low,56,0,A\n11,low,58,1,B\n12,low,55,0,C\n"
    "13,high,61,1,A\n14,high,63,1,B\n15,high,60,1,C\n"
    "16,high,62,0,A\n17,high,64,1,B\n18,high,59,1,C\n"
)


def _write(tmp_path, text=HEAD + BODY, name="dose.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _frame(tmp_path, text=HEAD + BODY):
    return load_frame(_write(tmp_path, text))


# --------------------------------------------------------------------------- #
# --group-order
# --------------------------------------------------------------------------- #
def test_group_order_reorders_columns(tmp_path):
    f = _frame(tmp_path)
    default = build_table1(f, Options(group_col="dose"))
    assert default.groups == ["placebo", "low", "high"]  # first-seen
    ordered = build_table1(f, Options(group_col="dose",
                                      group_order=["high", "low", "placebo"]))
    assert ordered.groups == ["high", "low", "placebo"]
    assert ordered.group_sizes == [6, 6, 6]


def test_group_order_moves_the_data_not_just_the_labels(tmp_path):
    f = _frame(tmp_path)
    default = build_table1(f, Options(group_col="dose", var_cols=["age"]))
    flipped = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                      group_order=["high", "low", "placebo"]))
    means_default = {g: r.mean for g, r in
                     zip(default.groups, default.rows[0].per_group)}
    means_flipped = {g: r.mean for g, r in
                     zip(flipped.groups, flipped.rows[0].per_group)}
    assert means_default == means_flipped
    assert means_flipped["high"] > means_flipped["placebo"]


def test_group_order_partial_list_appends_the_rest_and_warns(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", group_order=["high"]))
    assert t.groups == ["high", "placebo", "low"]
    assert any("원래 순서대로" in w for w in t.warnings)


def test_group_order_unknown_label_warns_and_is_ignored(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose",
                             group_order=["high", "medium", "low", "placebo"]))
    assert t.groups == ["high", "low", "placebo"]
    assert any("찾을 수" in w for w in t.warnings)


def test_group_order_all_unknown_falls_back_to_file_order(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", group_order=["x", "y"]))
    assert t.groups == ["placebo", "low", "high"]


def test_group_order_ignores_duplicates_and_blanks(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose",
                             group_order=["high", "high", " ", "low",
                                          "placebo"]))
    assert t.groups == ["high", "low", "placebo"]


def test_group_order_via_cli(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose",
                 "--group-order", "high,low,placebo", "--vars", "age"]) == 0
    out = capsys.readouterr().out
    assert out.index("high (n=6)") < out.index("placebo (n=6)")


# --------------------------------------------------------------------------- #
# --trend: the numbers must match the standalone functions exactly
# --------------------------------------------------------------------------- #
def test_trend_continuous_parametric_matches_linear_contrast(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["age"], trend=True))
    row = t.rows[0]
    groups = [[float(st) for st in vals] for vals in (
        [50, 52, 49, 51, 48, 53], [55, 57, 54, 56, 58, 55],
        [61, 63, 60, 62, 64, 59])]
    assert row.trend_test == "linear contrast"
    assert row.trend_p == pytest.approx(linear_contrast(groups).pvalue,
                                        abs=1e-15)
    assert row.trend_p < row.pvalue  # ordered alternative is more powerful here


def test_trend_continuous_nonparametric_uses_jonckheere(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             nonnormal=["age"]))
    row = t.rows[0]
    groups = [[50.0, 52, 49, 51, 48, 53], [55.0, 57, 54, 56, 58, 55],
              [61.0, 63, 60, 62, 64, 59]]
    assert row.trend_test == "Jonckheere-Terpstra"
    assert row.trend_p == pytest.approx(jonckheere_terpstra(groups).pvalue,
                                        abs=1e-15)


def test_trend_binary_matches_cochran_armitage(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["resp"], trend=True))
    row = t.rows[0]
    assert row.trend_test == "Cochran-Armitage"
    # levels are ordered "0","1"; the index level is the second ("1")
    assert row.trend_p == pytest.approx(
        cochran_armitage([1, 3, 5], [6, 6, 6]).pvalue, abs=1e-15)


def test_trend_scores_change_the_parametric_result(tmp_path):
    f = _frame(tmp_path)
    equal = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                    trend=True))
    dose = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                   trend=True, trend_scores=[0.0, 10.0, 40.0]))
    assert equal.rows[0].trend_p != pytest.approx(dose.rows[0].trend_p)
    assert dose.meta["trend_scores"] == [0.0, 10.0, 40.0]


def test_trend_scores_do_not_affect_the_rank_based_test(tmp_path):
    f = _frame(tmp_path)
    a = build_table1(f, Options(group_col="dose", var_cols=["age"], trend=True,
                                nonnormal=["age"]))
    b = build_table1(f, Options(group_col="dose", var_cols=["age"], trend=True,
                                nonnormal=["age"],
                                trend_scores=[0.0, 10.0, 40.0]))
    assert a.rows[0].trend_p == pytest.approx(b.rows[0].trend_p, abs=1e-15)


def test_trend_follows_group_order(tmp_path):
    """Reversing the arms flips the trend direction but not the two-sided p;
    a NON-monotone ordering makes the trend weaker than the monotone one."""
    f = _frame(tmp_path)
    up = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                 trend=True))
    down = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                   trend=True,
                                   group_order=["high", "low", "placebo"]))
    scrambled = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                        trend=True,
                                        group_order=["low", "placebo",
                                                     "high"]))
    assert up.rows[0].trend_p == pytest.approx(down.rows[0].trend_p, abs=1e-15)
    assert scrambled.rows[0].trend_p > up.rows[0].trend_p


def test_trend_blank_for_multilevel_nominal_with_note(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["site"], trend=True))
    assert t.rows[0].trend_p is None
    assert any("경향" in n for n in t.rows[0].notes)


def test_trend_does_not_disturb_the_omnibus_p_or_test_name(tmp_path):
    f = _frame(tmp_path)
    plain = build_table1(f, Options(group_col="dose"))
    trended = build_table1(f, Options(group_col="dose", trend=True))
    for a, b in zip(plain.rows, trended.rows):
        assert a.pvalue == b.pvalue
        assert a.test_name == b.test_name


def test_trend_two_groups_warns_and_matches_the_pairwise_test(tmp_path):
    text = HEAD + "".join(l + "\n" for l in BODY.splitlines()
                          if ",low," not in l)
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             test_cont="student"))
    assert any("2개뿐" in w for w in t.warnings)
    assert t.rows[0].trend_p == pytest.approx(t.rows[0].pvalue, abs=1e-12)


def test_trend_suppressed_and_warned_under_weighting(tmp_path):
    text = "id,dose,age,w\n1,a,50,1\n2,a,52,2\n3,b,55,1\n4,b,57,1\n5,c,60,2\n6,c,62,1\n"
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             weight_col="w"))
    assert t.meta["trend"] is False
    assert t.rows[0].trend_p is None
    assert any("가중" in w and "경향" in w for w in t.warnings)


def test_trend_without_a_group_column_is_a_hard_error(tmp_path):
    with pytest.raises(ValueError):
        build_table1(_frame(tmp_path), Options(group_col=None, trend=True))


def test_trend_scores_length_mismatch_is_a_hard_error(tmp_path):
    with pytest.raises(ValueError) as exc:
        build_table1(_frame(tmp_path),
                     Options(group_col="dose", trend=True,
                             trend_scores=[0.0, 1.0]))
    assert "trend-scores" in str(exc.value)


def test_trend_does_not_mutate_the_caller_options(tmp_path):
    opt = Options(group_col="dose", var_cols=["age"], trend=True,
                  weight_col=None)
    build_table1(_frame(tmp_path), opt)
    assert opt.trend is True


def test_trend_survives_a_constant_variable(tmp_path):
    """Zero within-group variance leaves both the contrast and the omnibus
    test undefined — the row must still render, with an explanatory note."""
    text = HEAD + "\n".join(
        ",".join([p[0], p[1], "50", p[3], p[4]])
        for p in (l.split(",") for l in BODY.strip().splitlines())) + "\n"
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             continuous=["age"]))
    assert t.rows[0].trend_p is None       # zero variance -> undefined
    assert any("경향성 검정을 계산" in n for n in t.rows[0].notes)


def test_trend_handles_an_all_zero_binary_variable(tmp_path):
    text = "id,dose,flag\n1,a,0\n2,a,0\n3,b,0\n4,b,0\n5,c,0\n6,c,0\n"
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["flag"], trend=True))
    assert t.rows[0].trend_p is None       # constant outcome -> undefined


def test_trend_with_missing_cells(tmp_path):
    text = HEAD + BODY.replace("1,placebo,50,0,A", "1,placebo,,0,A")
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["age"], trend=True))
    assert t.rows[0].trend_p is not None
    assert t.rows[0].n_missing_total == 1


# --------------------------------------------------------------------------- #
# rendering the trend column
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fmt", ["md", "csv", "tsv", "html", "latex"])
def test_trend_column_present_in_every_text_format(tmp_path, fmt):
    opt = Options(group_col="dose", var_cols=["age", "resp", "site"],
                  trend=True)
    t = build_table1(_frame(tmp_path), opt)
    text = render(t, opt, fmt=fmt)
    assert ("p_trend" in text) or ("p(경향)" in text)


def test_trend_column_absent_when_not_requested(tmp_path):
    opt = Options(group_col="dose", var_cols=["age"])
    t = build_table1(_frame(tmp_path), opt)
    assert "경향" not in render(t, opt, fmt="md")


def test_every_rendered_row_has_a_consistent_column_count(tmp_path):
    opt = Options(group_col="dose", var_cols=["age", "resp", "site"],
                  trend=True, padjust="holm")
    t = build_table1(_frame(tmp_path), opt)
    lines = [l for l in render(t, opt, fmt="md").splitlines()
             if l.startswith("|")]
    widths = {l.count("|") for l in lines}
    assert len(widths) == 1


def test_trend_in_json_output(tmp_path):
    opt = Options(group_col="dose", var_cols=["age", "site"], trend=True)
    t = build_table1(_frame(tmp_path), opt)
    obj = json.loads(render(t, opt, fmt="json"))
    assert obj["meta"]["trend"] is True
    assert obj["rows"][0]["trend_test"] == "linear contrast"
    assert 0.0 <= obj["rows"][0]["p_trend"] <= 1.0
    assert obj["rows"][1]["p_trend"] is None     # nominal 3-level


def test_trend_csv_carries_the_test_name(tmp_path):
    opt = Options(group_col="dose", var_cols=["resp"], trend=True)
    t = build_table1(_frame(tmp_path), opt)
    lines = render(t, opt, fmt="csv").splitlines()
    assert lines[0].endswith("p_trend,trend_test")
    assert "Cochran-Armitage" in lines[1]


def test_trend_legend_names_custom_scores(tmp_path):
    opt = Options(group_col="dose", var_cols=["age"], trend=True,
                  trend_scores=[0.0, 10.0, 40.0])
    t = build_table1(_frame(tmp_path), opt)
    assert "placebo=0, low=10, high=40" in render(t, opt, fmt="md")


def test_trend_cli_roundtrip(tmp_path, capsys):
    rc = main([_write(tmp_path), "-g", "dose", "--group-order",
               "placebo,low,high", "--trend", "--trend-scores", "0,10,40",
               "--vars", "age,resp"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "p(경향)" in out


def test_cli_rejects_trend_scores_without_trend(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--trend-scores", "1,2,3"]) == 2
    assert "--trend" in capsys.readouterr().err


def test_cli_rejects_non_numeric_trend_scores(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--trend",
                 "--trend-scores", "low,mid,high"]) == 2
    assert "숫자" in capsys.readouterr().err


def test_cli_rejects_non_finite_trend_scores(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--trend",
                 "--trend-scores", "0,10,inf"]) == 2
    assert "유한" in capsys.readouterr().err


def test_cli_reports_score_count_mismatch_without_a_traceback(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--trend",
                 "--trend-scores", "0,10"]) == 2
    assert "분석 오류" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# --format latex
# --------------------------------------------------------------------------- #
def _latex(tmp_path, text=HEAD + BODY, group_col="dose", **kw):
    opt = Options(group_col=group_col, **kw)
    return render(build_table1(_frame(tmp_path, text), opt), opt, fmt="latex")


def test_latex_has_a_wellformed_booktabs_skeleton(tmp_path):
    out = _latex(tmp_path, var_cols=["age", "site"])
    for token in (r"\begin{table}", r"\begin{tabular}", r"\toprule",
                  r"\midrule", r"\bottomrule", r"\end{tabular}",
                  r"\end{table}", "booktabs"):
        assert token in out
    assert out.count(r"\begin{tabular}") == out.count(r"\end{tabular}") == 1


def test_latex_rows_all_have_the_same_ampersand_count(tmp_path):
    out = _latex(tmp_path, var_cols=["age", "resp", "site"], trend=True,
                 effect=False)
    body = [l for l in out.splitlines() if l.rstrip().endswith(r"\\")]
    assert len({l.count(" & ") for l in body}) == 1
    # the {lrrr...} column spec must be exactly as wide as the rows
    spec = [l for l in out.splitlines() if r"\begin{tabular}" in l][0]
    colspec = spec.split("{")[-1].split("}")[0]
    assert set(colspec) <= {"l", "r"}
    assert len(colspec) == body[0].count(" & ") + 1


def test_latex_escapes_tex_special_characters_in_data(tmp_path):
    text = ("id,dose,pct_%,note\n"
            "1,a,10,x_1\n2,a,20,y&z\n3,b,30,#100\n4,b,40,50%\n"
            "5,c,50,a$b\n6,c,60,~c^d\n")
    out = _latex(tmp_path, text, var_cols=["pct_%", "note"],
                 categorical=["note"])
    assert r"\_" in out and r"\%" in out
    assert r"\&" in out and r"\#" in out and r"\$" in out
    assert r"\textasciitilde{}" in out and r"\textasciicircum{}" in out
    # no naked special left in a data cell
    for line in out.splitlines():
        if line.strip().startswith((r"\quad", "  pct", "  note")):
            assert "_" not in line.replace(r"\_", "")


def test_latex_maps_chi_square_and_dashes_to_math(tmp_path):
    out = _latex(tmp_path, var_cols=["site"], lang="en")
    assert r"$\chi^2$" in out
    assert "χ" not in out
    assert "—" not in out and "---" in out


def test_latex_backslash_in_data_does_not_break_the_table(tmp_path):
    text = "id,dose,note\n1,a,x\\y\n2,a,p\n3,b,q\n4,b,r\n5,c,s\n6,c,t\n"
    out = _latex(tmp_path, text, var_cols=["note"], categorical=["note"])
    assert r"\textbackslash{}" in out
    # the escape itself must not be re-escaped into \textbackslash{}textbackslash
    assert "textbackslash{}textbackslash" not in out


def test_latex_level_rows_are_indented(tmp_path):
    out = _latex(tmp_path, var_cols=["site"])
    assert r"\quad A" in out


def test_latex_notes_and_warnings_are_emitted(tmp_path):
    out = _latex(tmp_path, var_cols=["age", "site"], trend=True)
    assert r"\textsuperscript{1}" in out
    assert r"\begin{minipage}" in out and r"\end{minipage}" in out


def test_latex_via_cli_writes_a_file(tmp_path, capsys):
    dest = tmp_path / "t1.tex"
    rc = main([_write(tmp_path), "-g", "dose", "--vars", "age",
               "-f", "latex", "-o", str(dest)])
    assert rc == 0
    assert r"\bottomrule" in dest.read_text(encoding="utf-8")


def test_latex_single_group_descriptive_table(tmp_path):
    opt = Options(group_col=None, var_cols=["age", "site"])
    out = render(build_table1(_frame(tmp_path), opt), opt, fmt="latex")
    assert r"\toprule" in out
    assert "p (trend)" not in out and "p(경향)" not in out


def test_latex_weighted_table(tmp_path):
    text = "id,g,age,w\n1,a,50,1\n2,a,52,2\n3,b,55,1\n4,b,57,1\n"
    opt = Options(group_col="g", var_cols=["age"], weight_col="w")
    out = render(build_table1(_frame(tmp_path, text), opt), opt, fmt="latex")
    assert "ESS=" in out


def test_unknown_format_still_raises(tmp_path):
    opt = Options(group_col="dose", var_cols=["age"])
    with pytest.raises(ValueError):
        render(build_table1(_frame(tmp_path), opt), opt, fmt="rtf")


# --------------------------------------------------------------------------- #
# Review round 6 regressions
# --------------------------------------------------------------------------- #
def test_trend_test_follows_the_test_family_not_the_display(tmp_path):
    """--display only changes the summary shown. A row displayed as median but
    tested parametrically must still get the linear contrast (and vice versa),
    which is what the legend now promises."""
    f = _frame(tmp_path)
    shown_median = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                           trend=True, display="median"))
    assert shown_median.rows[0].display == "median"
    assert shown_median.rows[0].trend_test == "linear contrast"

    rank_tested = build_table1(f, Options(group_col="dose", var_cols=["age"],
                                          trend=True, test_cont="nonparam",
                                          display="mean"))
    assert rank_tested.rows[0].display == "mean"
    assert rank_tested.rows[0].trend_test == "Jonckheere-Terpstra"
    assert shown_median.rows[0].trend_p != pytest.approx(
        rank_tested.rows[0].trend_p)


def test_trend_legend_does_not_claim_unused_scores(tmp_path):
    """Jonckheere-Terpstra ignores --trend-scores; the legend pasted into a
    manuscript must not assert a dose axis no p-value was computed on."""
    opt = Options(group_col="dose", var_cols=["age"], trend=True,
                  nonnormal=["age"], trend_scores=[0.0, 10.0, 40.0])
    md = render(build_table1(_frame(tmp_path), opt), opt, fmt="md")
    assert "placebo=0" not in md
    assert "적용되지 않았습니다" in md


def test_trend_legend_flags_partial_score_use(tmp_path):
    opt = Options(group_col="dose", var_cols=["age", "resp"], trend=True,
                  nonnormal=["age"], trend_scores=[0.0, 10.0, 40.0])
    md = render(build_table1(_frame(tmp_path), opt), opt, fmt="md")
    assert "placebo=0, low=10, high=40" in md      # resp used them
    assert "적용되지 않음" in md                    # age did not


def test_trend_legend_lists_scores_plainly_when_all_rows_used_them(tmp_path):
    opt = Options(group_col="dose", var_cols=["age", "resp"], trend=True,
                  trend_scores=[0.0, 10.0, 40.0])
    md = render(build_table1(_frame(tmp_path), opt), opt, fmt="md")
    assert "(점수: placebo=0, low=10, high=40)" in md


def test_latex_escapes_angle_brackets(tmp_path):
    """'<' and '>' are not TeX specials but typeset as inverted punctuation
    under OT1 — a censored ">100" would silently print as garbage."""
    text = "id,g,lab\n1,a,>100\n2,a,50\n3,b,<5\n4,b,60\n"
    out = _latex(tmp_path, text, group_col="g", var_cols=["lab"],
                 categorical=["lab"])
    assert r"\textgreater{}100" in out and r"\textless{}5" in out
    body = [l for l in out.splitlines() if l.rstrip().endswith(r"\\")]
    assert not any("<" in l or ">" in l for l in body)


def test_latex_p_value_thresholds_are_escaped(tmp_path):
    text = ("id,g,x\n" + "".join(f"{i},a,{i}\n" for i in range(1, 11))
            + "".join(f"{i},b,{i + 500}\n" for i in range(11, 21)))
    out = _latex(tmp_path, text, group_col="g", var_cols=["x"],
                 continuous=["x"])
    assert r"\textless{}0.001" in out
    assert "<0.001" not in out


def test_markdown_notes_and_warnings_carry_no_escaped_entities(tmp_path):
    """Tool-authored notes/warnings go through the markdown data escaper, so
    they must not contain literal '<'/'>' that would surface as '&lt;'."""
    text = ("id,dose,x,flag\n1,a,1,0\n2,a,2,0\n3,b,3,1\n"
            "4,b,4,1\n5,c,5,1\n6,c,6,1\n")
    opt = Options(group_col="dose", var_cols=["x", "flag"], trend=True)
    md = render(build_table1(_frame(tmp_path, text), opt), opt, fmt="md")
    body = md.split("**경고**")[-1] + md.split("**주석**")[-1]
    assert "&lt;" not in body and "&gt;" not in body


def test_no_message_template_contains_a_bare_angle_bracket():
    """Regression guard for the whole catalog, not just today's strings."""
    from table1.build import _MSG
    for lang, table in _MSG.items():
        for key, tmpl in table.items():
            assert "<" not in tmpl and ">" not in tmpl, f"{lang}/{key}"


def test_two_group_trend_note_does_not_claim_exact_equality(tmp_path):
    text = HEAD + "".join(l + "\n" for l in BODY.splitlines()
                          if ",low," not in l)
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             test_cont="nonparam"))
    note = [w for w in t.warnings if "2개뿐" in w][0]
    assert "연속성" in note              # names the Mann-Whitney discrepancy
    # ...and the numbers really do differ, which is why the wording changed
    assert t.rows[0].trend_p != pytest.approx(t.rows[0].pvalue, abs=1e-9)


# --------------------------------------------------------------------------- #
# Correctness-review regressions (round 6)
# --------------------------------------------------------------------------- #
def test_group_order_matching_nothing_is_fatal_under_trend(tmp_path):
    """A typo that matches no arm would leave the dose axis at CSV row order
    while --trend-scores stayed bound to arbitrary columns."""
    with pytest.raises(ValueError) as exc:
        build_table1(_frame(tmp_path),
                     Options(group_col="dose", trend=True,
                             group_order=["Placebo", "Low", "High"],
                             trend_scores=[0.0, 10.0, 40.0]))
    assert "group-order" in str(exc.value)


def test_group_order_matching_nothing_is_only_a_warning_without_trend(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", group_order=["X", "Y"]))
    assert t.groups == ["placebo", "low", "high"]
    assert any("찾을 수" in w for w in t.warnings)


def test_partial_group_order_is_fatal_under_trend(tmp_path):
    """A half-specified axis puts the unlisted arms wherever the CSV happened
    to leave them, and the trend p would be computed on that mixture."""
    with pytest.raises(ValueError) as exc:
        build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             group_order=["placebo"]))
    assert "low" in str(exc.value) and "high" in str(exc.value)


def test_partial_group_order_is_fine_without_trend(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", group_order=["high"]))
    assert t.groups == ["high", "placebo", "low"]


def test_no_group_order_still_warns_about_the_trend_axis(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["age"], trend=True))
    assert any("낮음→높음" in w for w in t.warnings)


def test_complete_group_order_suppresses_the_trend_axis_warning(tmp_path):
    t = build_table1(_frame(tmp_path),
                     Options(group_col="dose", var_cols=["age"], trend=True,
                             group_order=["placebo", "low", "high"]))
    assert not any("낮음→높음" in w for w in t.warnings)


def test_empty_arm_truncates_the_dose_axis_with_a_note(tmp_path):
    """Dropping an empty arm's score is right, but must not be silent — the
    legend still advertises the full score set."""
    text = ("id,arm,x\n1,D0,1\n2,D0,2\n3,D10,3\n4,D10,4\n"
            "5,D20,\n6,D20,\n7,D40,9\n8,D40,10\n")
    opt = Options(group_col="arm", var_cols=["x"], continuous=["x"],
                  trend=True, group_order=["D0", "D10", "D20", "D40"],
                  trend_scores=[0.0, 10.0, 20.0, 40.0])
    t = build_table1(_frame(tmp_path, text), opt)
    row = t.rows[0]
    assert row.trend_p is not None
    note = " ".join(row.notes)
    assert "D20" in note and "D0=0, D10=10, D40=40" in note


def test_empty_arm_does_not_blank_the_omnibus_test(tmp_path):
    """A variable never collected in one arm must still be compared across the
    arms that do have it (verified against an independent one-way ANOVA)."""
    text = ("id,arm,x\n1,D0,1\n2,D0,2\n3,D10,3\n4,D10,4\n"
            "5,D20,\n6,D20,\n7,D40,9\n8,D40,10\n")
    opt = Options(group_col="arm", var_cols=["x"], continuous=["x"])
    t = build_table1(_frame(tmp_path, text), opt)
    row = t.rows[0]
    from table1.tests_stat import one_way_anova
    assert row.test_name == "One-way ANOVA"
    assert row.pvalue == pytest.approx(
        one_way_anova([[1.0, 2.0], [3.0, 4.0], [9.0, 10.0]]).pvalue, abs=1e-15)
    assert any("D20" in n for n in row.notes)
    assert not any("2개 미만" in n for n in row.notes)


def test_all_but_one_arm_empty_still_reports_the_old_skip_note(tmp_path):
    text = ("id,arm,x\n1,D0,1\n2,D0,2\n3,D10,\n4,D10,\n5,D20,\n6,D20,\n")
    opt = Options(group_col="arm", var_cols=["x"], continuous=["x"])
    row = build_table1(_frame(tmp_path, text), opt).rows[0]
    assert row.pvalue is None
    assert any("2개 미만" in n for n in row.notes)


def test_perfect_linear_trend_is_reported_not_blanked(tmp_path):
    """Zero within-group variance is the STRONGEST trend; one_way_anova already
    reports F=inf/p=0, so the trend column must not go blank there."""
    text = ("id,arm,dose_mg\n1,P,0\n2,P,0\n3,L,10\n4,L,10\n5,H,40\n6,H,40\n")
    opt = Options(group_col="arm", var_cols=["dose_mg"], trend=True,
                  continuous=["dose_mg"], group_order=["P", "L", "H"],
                  trend_scores=[0.0, 10.0, 40.0])
    row = build_table1(_frame(tmp_path, text), opt).rows[0]
    assert row.trend_p == 0.0
    assert row.trend_test == "linear contrast"


def test_single_level_categorical_gets_a_trend_note(tmp_path):
    text = "id,dose,flag\n1,a,Y\n2,a,Y\n3,b,Y\n4,b,Y\n5,c,Y\n6,c,Y\n"
    row = build_table1(_frame(tmp_path, text),
                       Options(group_col="dose", var_cols=["flag"],
                               trend=True)).rows[0]
    assert row.trend_p is None
    assert any("1개뿐" in n for n in row.notes)


def test_two_group_note_names_fisher_too(tmp_path):
    text = HEAD + "".join(l + "\n" for l in BODY.splitlines()
                          if ",low," not in l)
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="dose", var_cols=["resp"], trend=True))
    assert any("Fisher" in w for w in t.warnings)


# --------------------------------------------------------------------------- #
# Test-quality-review regressions (round 6): column POSITION, not just presence
# --------------------------------------------------------------------------- #
def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _busy_table(tmp_path):
    """Two groups with every optional column on at once, so a trend cell that
    drifted into the p(adj)/SMD/effect slot has somewhere wrong to land."""
    text = HEAD + "".join(l + "\n" for l in BODY.splitlines()
                          if ",low," not in l)
    opt = Options(group_col="dose", var_cols=["age", "resp"], trend=True,
                  padjust="holm", effect=True)
    return build_table1(_frame(tmp_path, text), opt), opt


def test_markdown_trend_value_sits_under_the_trend_header(tmp_path):
    from table1.render import _fmt_p
    t, opt = _busy_table(tmp_path)
    lines = [l for l in render(t, opt, fmt="md").splitlines()
             if l.startswith("|")]
    header = _cells(lines[0])
    col = header.index("p(경향)")
    assert header[col - 1] == "p값" and header[col + 1] == "p(보정)"
    body = [_cells(l) for l in lines[2:]]
    by_p = {r[col]: r for r in body}
    for row in t.rows:
        assert _fmt_p(row.trend_p) in by_p


def test_html_trend_value_sits_under_the_trend_header(tmp_path):
    import re
    from table1.render import _fmt_p
    t, opt = _busy_table(tmp_path)
    html = render(t, opt, fmt="html")
    headers = re.findall(r'<th scope="col">(.*?)</th>', html)
    col = headers.index("p(경향)")
    first = re.search(r"<tr><th scope=\"row\">.*?</th>(.*?)</tr>", html).group(1)
    cells = re.findall(r"<td>(.*?)</td>", first)
    import html as _html
    assert _html.unescape(cells[col]) == _fmt_p(t.rows[0].trend_p)


def test_latex_trend_value_sits_under_the_trend_header(tmp_path):
    from table1.render import _fmt_p, _tex
    t, opt = _busy_table(tmp_path)
    lines = [l for l in render(t, opt, fmt="latex").splitlines()
             if l.rstrip().endswith(r"\\")]
    header = [c.strip() for c in lines[0].rstrip()[:-2].split(" & ")]
    col = header.index(_tex("p(경향)"))
    first = [c.strip() for c in lines[1].rstrip()[:-2].split(" & ")]
    assert first[col] == _tex(_fmt_p(t.rows[0].trend_p))


def test_csv_trend_columns_are_indexed_not_merely_present(tmp_path):
    import csv as _csv
    import io as _io
    from table1.render import _fmt_p
    t, opt = _busy_table(tmp_path)
    rows = list(_csv.reader(_io.StringIO(render(t, opt, fmt="csv"))))
    head = rows[0]
    ip, it = head.index("p_trend"), head.index("trend_test")
    assert rows[1][ip] == _fmt_p(t.rows[0].trend_p)
    assert rows[1][it] == t.rows[0].trend_test


def test_csv_reports_p_and_smd_for_multilevel_categoricals(tmp_path):
    """The CSV export the README recommends used to blank these."""
    import csv as _csv
    import io as _io
    from table1.render import _fmt_p, _fmt_smd
    text = HEAD + "".join(l + "\n" for l in BODY.splitlines()
                          if ",low," not in l)
    opt = Options(group_col="dose", var_cols=["site"])
    t = build_table1(_frame(tmp_path, text), opt)
    rows = list(_csv.reader(_io.StringIO(render(t, opt, fmt="csv"))))
    head, hdr_row = rows[0], rows[1]
    assert hdr_row[head.index("p_value")] == _fmt_p(t.rows[0].pvalue)
    assert hdr_row[head.index("smd")] == _fmt_smd(t.rows[0].smd)
    assert hdr_row[head.index("p_value")] not in ("", "—")


# --- LaTeX escaping on the paths that carry raw patient values -------------
@pytest.mark.parametrize("payload", [
    r"\write18{rm -rf /}", r"\input{/etc/passwd}", "100%", "a{b}c",
    r"x\y", "a~b^c", "p&q#r$s_t", "<5", ">100", "a|b",
])
def test_latex_neutralizes_payloads_in_a_footnote_note(tmp_path, payload):
    """An unparseable cell is quoted verbatim into a note, which becomes a
    LaTeX footnote — the one path carrying raw patient text."""
    text = ("id,g,val\n1,a,10\n2,a,20\n3,a,30\n4,a,40\n"
            f"5,b,{payload}\n6,b,60\n7,b,70\n8,b,80\n")
    out = _latex(tmp_path, text, group_col="g", var_cols=["val"],
                 continuous=["val"])
    foot = "\n".join(l for l in out.splitlines()
                     if r"\textsuperscript{" in l or r"\par" in l)
    assert payload not in foot          # never verbatim
    _assert_tex_inert(foot)


def test_latex_neutralizes_payloads_in_group_labels(tmp_path):
    """Group labels reach the column headers, the warnings and the trend
    legend's score list."""
    bad = r"100%\bad{"
    text = (f"id,g,x\n1,{bad},1\n2,{bad},2\n3,mid,3\n4,mid,4\n"
            "5,hi,5\n6,hi,6\n")
    out = _latex(tmp_path, text, group_col="g", var_cols=["x"],
                 continuous=["x"], trend=True, trend_scores=[0.0, 10.0, 40.0])
    assert bad not in out
    assert r"100\%\textbackslash{}bad\{" in out     # header + legend
    _assert_tex_inert(out)


def _assert_tex_inert(text):
    """Structural TeX-safety check on emitted output.

    Rather than stripping known commands (fragile), assert the invariants that
    actually make a payload harmless: every '%' is escaped (an unescaped one
    comments out the rest of the line), every backslash starts a real command
    or an escape, and braces balance on every line.
    """
    import re as _re
    allowed = {
        "textbackslash", "textasciitilde", "textasciicircum", "textless",
        "textgreater", "textbar", "textsuperscript", "textbf", "quad", "par",
        "chi", "pm", "cdot", "ge", "le", "ne", "rightarrow", "infty", "times",
        "begin", "end", "toprule", "midrule", "bottomrule", "centering",
        "caption", "footnotesize", "linewidth", "table", "tabular", "minipage",
    }
    scan = "\n".join(l for l in text.splitlines()
                      if not l.lstrip().startswith("%"))
    for cmd in _re.findall(r"\\([A-Za-z]+)", scan):
        assert cmd in allowed, f"unexpected LaTeX command: \\{cmd}"
    for line in text.splitlines():
        if line.lstrip().startswith("%"):
            continue           # the tool's own "% requires booktabs" comment
        for i, ch in enumerate(line):
            if ch == "%":
                assert i > 0 and line[i - 1] == "\\", f"bare % in: {line}"
            if ch == "\\":
                nxt = line[i + 1:i + 2]
                assert nxt.isalpha() or nxt in "&%$#_{}\\", \
                    f"stray backslash in: {line}"
        depth = 0
        for j, ch in enumerate(line):
            if ch in "{}" and j > 0 and line[j - 1] == "\\":
                continue       # escaped brace, not a group delimiter
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                assert depth >= 0, f"unbalanced }} in: {line}"
        assert depth == 0, f"unbalanced {{ in: {line}"


def test_latex_escape_table_covers_braces_and_newlines():
    from table1.render import _tex
    assert _tex("{a}") == r"\{a\}"
    assert _tex("a\nb\r\nc") == "a b  c"
    assert "\n" not in _tex("x\ny")


# --- Cochran-Armitage wiring branches --------------------------------------
def test_trend_scores_reach_the_cochran_armitage_path(tmp_path):
    f = _frame(tmp_path)
    equal = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                    trend=True))
    dose = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                   trend=True,
                                   trend_scores=[0.0, 10.0, 40.0]))
    assert equal.rows[0].trend_p == pytest.approx(
        cochran_armitage([1, 3, 5], [6, 6, 6], [1, 2, 3]).pvalue, abs=1e-15)
    assert dose.rows[0].trend_p == pytest.approx(
        cochran_armitage([1, 3, 5], [6, 6, 6], [0, 10, 40]).pvalue, abs=1e-15)
    assert equal.rows[0].trend_p != pytest.approx(dose.rows[0].trend_p)


def test_cochran_armitage_denominator_excludes_missing_cells(tmp_path):
    """Missing must not inflate the group totals -- it would shrink every
    proportion and change the trend p."""
    text = ("id,dose,resp\n1,a,1\n2,a,0\n3,a,\n4,b,1\n5,b,1\n6,b,\n"
            "7,c,1\n8,c,1\n9,c,1\n")
    row = build_table1(_frame(tmp_path, text),
                       Options(group_col="dose", var_cols=["resp"],
                               trend=True)).rows[0]
    assert row.trend_p == pytest.approx(
        cochran_armitage([1, 2, 3], [2, 2, 3]).pvalue, abs=1e-15)
    assert row.trend_p != pytest.approx(
        cochran_armitage([1, 2, 3], [3, 3, 3]).pvalue)


def test_missing_as_level_does_not_enter_the_trend_test(tmp_path):
    """The synthetic '(결측)' level is displayed but must stay out of the test,
    exactly as it stays out of the chi-square."""
    text = ("id,dose,resp\n1,a,1\n2,a,0\n3,a,\n4,b,1\n5,b,1\n6,b,\n"
            "7,c,1\n8,c,1\n9,c,1\n")
    f = _frame(tmp_path, text)
    plain = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                    trend=True)).rows[0]
    shown = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                    trend=True,
                                    missing_as_level=True)).rows[0]
    assert shown.trend_p == pytest.approx(plain.trend_p, abs=1e-15)
    assert shown.trend_test == "Cochran-Armitage"


def test_trend_index_level_is_the_one_the_table_shows(tmp_path):
    """--ref flips which level --effect/--binary-single display; the trend row
    must follow so the legend's 'index level' claim stays true."""
    f = _frame(tmp_path)
    default = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                      trend=True, effect=False))
    flipped = build_table1(f, Options(group_col="dose", var_cols=["resp"],
                                      trend=True, ref={"resp": "1"}))
    # two-sided p is invariant, but both paths must still produce a number
    assert default.rows[0].trend_p == pytest.approx(flipped.rows[0].trend_p)
    assert flipped.rows[0].trend_test == "Cochran-Armitage"


# --- markdown legend escaping ----------------------------------------------
def test_markdown_legend_escapes_data_derived_group_labels(tmp_path):
    """The legend was the one markdown data path that skipped escaping."""
    bad = "<img src=x onerror=alert(1)>"
    text = (f"id,g,x\n1,{bad},1\n2,{bad},2\n3,low,3\n4,low,4\n"
            "5,high,5\n6,high,6\n")
    opt = Options(group_col="g", var_cols=["x"], continuous=["x"], trend=True,
                  trend_scores=[0.0, 10.0, 40.0])
    md = render(build_table1(_frame(tmp_path, text), opt), opt, fmt="md")
    assert bad not in md
    assert "&lt;img src=x onerror=alert(1)&gt;=0" in md


def test_markdown_weighted_legend_escapes_the_column_name(tmp_path):
    text = "id,g,x,w<b>\n1,a,1,1\n2,a,2,2\n3,b,3,1\n4,b,4,1\n"
    opt = Options(group_col="g", var_cols=["x"], weight_col="w<b>")
    md = render(build_table1(_frame(tmp_path, text), opt), opt, fmt="md")
    assert "'w&lt;b&gt;'" in md and "'w<b>'" not in md


def test_golden_snapshot_dose_trend_md():
    """Full-string regression on the trend example the README and 실행.command
    both paste: pins the trend column's position, the score legend, the
    per-row nominal-categorical note and every number."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ex = os.path.join(here, "examples", "dose_trend.csv")
    golden = os.path.join(here, "tests", "golden", "dose_trend_table.md")
    opt = Options(group_col="dose", group_order=["placebo", "low", "high"],
                  trend=True, trend_scores=[0.0, 10.0, 40.0],
                  nonnormal=["crp"],
                  var_cols=["age", "sex", "bmi", "sbp", "crp", "ae_serious",
                            "site"])
    got = render(build_table1(load_frame(ex), opt), opt, "md")
    with open(golden, encoding="utf-8") as fh:
        expected = fh.read()
    assert got == expected, (
        "Rendered dose-trend table drifted from "
        "tests/golden/dose_trend_table.md. Regenerate if intentional.")


# --------------------------------------------------------------------------- #
# --encoding (cp949 is the default Excel export on a Korean Windows machine)
# --------------------------------------------------------------------------- #
def test_cp949_csv_reads_with_encoding_flag(tmp_path, capsys):
    p = tmp_path / "cp949.csv"
    p.write_bytes("아이디,군,연령\n1,가,50\n2,가,52\n3,나,60\n4,나,62\n"
                  .encode("cp949"))
    assert main([str(p), "-g", "군", "--encoding", "cp949"]) == 0
    assert "연령" in capsys.readouterr().out


def test_cp949_csv_without_the_flag_says_what_to_do(tmp_path, capsys):
    p = tmp_path / "cp949.csv"
    p.write_bytes("아이디,군,연령\n1,가,50\n2,가,52\n3,나,60\n4,나,62\n"
                  .encode("cp949"))
    assert main([str(p), "-g", "군"]) == 2
    assert "cp949" in capsys.readouterr().err


def test_unknown_encoding_is_a_clean_error(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--encoding", "no-such"]) == 2
    assert "인코딩" in capsys.readouterr().err


def test_encoding_rejected_for_xlsx(tmp_path, capsys):
    import zipfile
    p = tmp_path / "book.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    assert main([str(p), "-g", "g", "--encoding", "cp949"]) == 2
    assert "--encoding" in capsys.readouterr().err


def test_row_with_more_fields_than_the_header_is_rejected(tmp_path, capsys):
    p = tmp_path / "ragged.csv"
    p.write_text("id,g,note\n1,a,ok\n2,a,has,comma\n3,b,ok\n4,b,ok\n",
                 encoding="utf-8")
    assert main([str(p), "-g", "g"]) == 2
    err = capsys.readouterr().err
    assert "헤더" in err and "쉼표" in err


def test_short_rows_are_still_padded_as_missing(tmp_path):
    text = "id,g,x\n1,a,1\n2,a\n3,b,3\n4,b,4\n"
    t = build_table1(_frame(tmp_path, text),
                     Options(group_col="g", var_cols=["x"], continuous=["x"]))
    assert t.rows[0].n_missing_total == 1


def test_cli_rejects_empty_and_degenerate_trend_scores(tmp_path, capsys):
    assert main([_write(tmp_path), "-g", "dose", "--trend",
                 "--trend-scores", ""]) == 2
    assert "비어" in capsys.readouterr().err
    assert main([_write(tmp_path), "-g", "dose", "--trend",
                 "--trend-scores", "5,5,5"]) == 2
    assert "모두 같습니다" in capsys.readouterr().err


def test_huge_scores_do_not_expand_in_the_legend(tmp_path):
    opt = Options(group_col="dose", var_cols=["age"], trend=True,
                  trend_scores=[0.0, 1e100, 2e100])
    md = render(build_table1(_frame(tmp_path), opt), opt, fmt="md")
    assert "1e+100" in md
    assert "0000000000000000000" not in md


def test_extreme_scores_do_not_break_the_contrast(tmp_path):
    """t = L/SE is scale-free in the coefficients; c*c used to overflow."""
    small = build_table1(_frame(tmp_path),
                         Options(group_col="dose", var_cols=["age"],
                                 trend=True, trend_scores=[0.0, 1.0, 2.0]))
    huge = build_table1(_frame(tmp_path),
                        Options(group_col="dose", var_cols=["age"],
                                trend=True,
                                trend_scores=[0.0, 1e160, 2e160]))
    assert huge.rows[0].trend_p == pytest.approx(small.rows[0].trend_p,
                                                 rel=1e-9)


def test_one_huge_value_does_not_abort_the_whole_table(tmp_path):
    rows = ["id,arm,age,huge"]
    for i in range(20):
        rows.append(f"{i},{'A' if i % 2 else 'B'},{50 + i % 9},"
                    f"{'1e300' if i == 3 else i}")
    t = build_table1(_frame(tmp_path, "\n".join(rows) + "\n"),
                     Options(group_col="arm"))
    names = [r.name for r in t.rows]
    assert "age" in names and "huge" not in names
    assert any("1e308" in w for w in t.warnings)
