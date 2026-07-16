"""CLI, CSV-loading and report-rendering tests for the categorical path."""

import json

import pytest

from agreestat.catanalyze import analyze_categorical
from agreestat.catreport import (
    render_cat_json,
    render_cat_markdown,
    render_cat_text,
)
from agreestat.cli import main
from agreestat.dataio import load_categorical_pairs


def _write(tmp_path, text, name="r.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


BASIC = """subject,reader1,reader2
S01,pos,pos
S02,pos,neg
S03,neg,neg
S04,neg,neg
S05,pos,pos
S06,neg,pos
"""


# --------------------------------------------------------------------------
# load_categorical_pairs
# --------------------------------------------------------------------------
def test_load_categorical_explicit_columns(tmp_path):
    d = load_categorical_pairs(_write(tmp_path, BASIC), "reader1", "reader2")
    assert d.n == 6
    assert d.a[:2] == ["pos", "pos"]
    assert d.b[:2] == ["pos", "neg"]
    assert d.dropped == 0


def test_load_categorical_auto_detects_two_rating_columns(tmp_path):
    d = load_categorical_pairs(_write(tmp_path, BASIC))
    # 'subject' is all-distinct -> not a rating column
    assert (d.name_a, d.name_b) == ("reader1", "reader2")


def test_load_categorical_drops_only_blank_cells_by_default(tmp_path):
    """Blank means missing; 'NA' does NOT — it may be a real category label."""
    csv = "a,b\npos,pos\n,neg\npos,NA\nneg,neg\nN/A,pos\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert d.n == 4
    assert d.dropped == 1          # only the blank row
    assert "NA" in d.b and "N/A" in d.a


def test_load_categorical_keeps_none_dash_dot_as_real_labels(tmp_path):
    """REGRESSION: 'None/Mild/Severe' and '+/-' are real clinical scales.
    Dropping them silently deleted the disagreeing rows and turned kappa=0.81
    into a perfect kappa=1.0."""
    csv = ("id,r1,r2\n1,None,None\n2,Mild,Mild\n3,Severe,Severe\n4,None,Mild\n"
           "5,None,None\n6,Mild,Mild\n7,Severe,Severe\n8,None,None\n")
    d = load_categorical_pairs(_write(tmp_path, csv), "r1", "r2")
    assert d.n == 8 and d.dropped == 0
    assert set(d.a) == {"None", "Mild", "Severe"}
    res = analyze_categorical(d.a, d.b)
    assert res.kappa.value == pytest.approx(0.8095, abs=1e-3)


def test_load_categorical_plus_minus_scale_survives(tmp_path):
    csv = "a,b\n+,+\n-,-\n+,-\n-,-\n+,+\n-,-\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert d.n == 6
    assert set(d.a) == {"+", "-"}


def test_load_categorical_na_option_declares_missing(tmp_path):
    csv = "a,b\npos,pos\nNA,neg\npos,NA\nneg,neg\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b", na_labels=["NA"])
    assert d.n == 2 and d.dropped == 2


def test_load_categorical_warns_when_na_like_label_kept(tmp_path):
    csv = "a,b\npos,pos\nNA,NA\npos,NA\nneg,neg\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert any("실제 범주" in n and "--na" in n for n in d.notes)


def test_load_categorical_no_na_warning_for_ordinary_labels(tmp_path):
    csv = "a,b\nNone,None\nMild,Mild\nSevere,None\nMild,Mild\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert not any("--na" in n for n in d.notes)


def test_load_categorical_rejects_too_many_categories(tmp_path):
    """Continuous data fed to --categorical would build a k x k matrix and
    exhaust memory; refuse instead."""
    rows = "\n".join(f"{i}.{i},{i}.{i}" for i in range(300))
    with pytest.raises(ValueError, match="너무 많습니다"):
        load_categorical_pairs(_write(tmp_path, "a,b\n" + rows + "\n"), "a", "b")


def test_load_categorical_rejects_duplicate_header_names(tmp_path):
    csv = "r,r\npos,pos\nneg,neg\npos,neg\n"
    with pytest.raises(ValueError, match="중복"):
        load_categorical_pairs(_write(tmp_path, csv), "r", "r")


def test_load_categorical_reads_subject_column(tmp_path):
    d = load_categorical_pairs(_write(tmp_path, BASIC), "reader1", "reader2",
                               subject_col="subject")
    assert d.subjects == ["S01", "S02", "S03", "S04", "S05", "S06"]


def test_load_categorical_strips_whitespace(tmp_path):
    csv = "a,b\n pos , pos\nneg,neg\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert d.a == ["pos", "neg"]


def test_load_categorical_treats_labels_case_and_spacing_literally(tmp_path):
    """'Pos' and 'pos' are different labels — we must not silently merge them."""
    csv = "a,b\nPos,pos\npos,pos\nneg,neg\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert set(d.a) == {"Pos", "pos", "neg"}


def test_load_categorical_rejects_identical_columns(tmp_path):
    with pytest.raises(ValueError, match="different columns"):
        load_categorical_pairs(_write(tmp_path, BASIC), "reader1", "reader1")


def test_load_categorical_rejects_missing_column(tmp_path):
    with pytest.raises(ValueError, match="not in header"):
        load_categorical_pairs(_write(tmp_path, BASIC), "nope", "reader2")


def test_load_categorical_rejects_all_rows_dropped(tmp_path):
    csv = "a,b\n,\n,x\ny,\n"
    with pytest.raises(ValueError, match="no usable rating pairs"):
        load_categorical_pairs(_write(tmp_path, csv), "a", "b")


def test_load_categorical_rejects_empty_file(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        load_categorical_pairs(_write(tmp_path, "\n"), "a", "b")


def test_load_categorical_auto_detect_fails_on_all_unique_columns(tmp_path):
    csv = "id,note\n1,alpha\n2,beta\n3,gamma\n"
    with pytest.raises(ValueError, match="could not auto-detect"):
        load_categorical_pairs(_write(tmp_path, csv))


def test_load_categorical_notes_when_many_categories(tmp_path):
    rows = "\n".join(f"v{i},v{i}" for i in range(25))
    csv = "a,b\n" + rows + "\nv0,v0\n"
    d = load_categorical_pairs(_write(tmp_path, csv), "a", "b")
    assert any("범주 수가 많습니다" in n for n in d.notes)


def test_load_categorical_reads_cp949_korean(tmp_path):
    csv = "판정A,판정B\n양성,양성\n음성,음성\n양성,음성\n"
    p = _write(tmp_path, csv, name="k.csv", encoding="cp949")
    d = load_categorical_pairs(p, "판정A", "판정B")
    assert d.a == ["양성", "음성", "양성"]


def test_load_categorical_notes_extra_candidate_columns(tmp_path):
    csv = "a,b,c\nx,x,x\ny,y,y\nx,y,x\n"
    d = load_categorical_pairs(_write(tmp_path, csv))
    assert any("3개 이상" in n for n in d.notes)


def test_numeric_autodetect_error_suggests_categorical(tmp_path):
    """A CSV of labels run through the continuous path should point the user
    at --categorical rather than just failing."""
    from agreestat.dataio import load_pairs
    csv = "a,b\npos,pos\nneg,neg\npos,neg\n"
    with pytest.raises(ValueError, match="--categorical"):
        load_pairs(_write(tmp_path, csv))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_categorical_text_output(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "-a", "reader1",
               "-b", "reader2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "범주형 일치도 리포트" in out
    assert "Cohen's kappa" in out
    assert "교차표" in out


def test_cli_categorical_json_is_valid_and_complete(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert d["analysis"] == "categorical"
    assert d["n"] == 6
    assert d["confusion_matrix"]["counts"] == [[2, 1], [1, 2]]
    assert d["coefficients"]["cohens_kappa"]["value"] == pytest.approx(1 / 3)


def test_cli_categorical_markdown_to_file(tmp_path, capsys):
    out = tmp_path / "o.md"
    rc = main([_write(tmp_path, BASIC), "--categorical", "--markdown", str(out)])
    assert rc == 0
    assert "Confusion matrix" in out.read_text(encoding="utf-8")


def test_cli_categorical_ordinal_with_categories(tmp_path, capsys):
    csv = "a,b\nmild,mild\nsevere,moderate\nmoderate,moderate\nmild,mild\n"
    rc = main([_write(tmp_path, csv), "--categorical", "--ordinal",
               "--categories", "mild,moderate,severe"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "weighted kappa (quadratic)" in out
    assert "순서형" in out


def test_cli_min_kappa_rejects_out_of_range(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "--min-kappa", "1.5"])
    assert rc == 2
    assert "min-kappa" in capsys.readouterr().err


def test_cli_categories_rejects_single_entry(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "--categories", "pos"])
    assert rc == 2
    assert "2개 이상" in capsys.readouterr().err


def test_cli_categories_rejects_duplicates(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical",
               "--categories", "pos,neg,pos"])
    assert rc == 2
    assert "중복" in capsys.readouterr().err


def test_cli_categories_missing_label_is_a_clean_error(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "--categories", "pos,x"])
    assert rc == 2
    assert "분석 오류" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [
    ["--percent"], ["--accept", "1"], ["--svg", "x.svg"],
    ["--plot-data", "x.csv"], ["--target-loa-hw", "1"], ["--at", "5"],
    ["--deming-lambda", "2"],
])
def test_cli_rejects_continuous_flags_in_categorical_mode(tmp_path, capsys, flag):
    rc = main([_write(tmp_path, BASIC), "--categorical"] + flag)
    assert rc == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_cli_subject_is_accepted_in_categorical_mode(tmp_path, capsys):
    """--subject drives the cluster bootstrap; it must NOT be rejected."""
    rc = main([_write(tmp_path, BASIC), "--categorical", "-s", "subject"])
    assert rc == 0


@pytest.mark.parametrize("flag", [
    ["--ordinal"], ["--weights", "linear"], ["--categories", "a,b"],
    ["--min-kappa", "0.6"],
])
def test_cli_rejects_categorical_flags_without_mode(tmp_path, capsys, flag):
    csv = "a,b\n1,2\n3,4\n5,6\n"
    rc = main([_write(tmp_path, csv)] + flag)
    assert rc == 2
    assert "범주형 분석 전용" in capsys.readouterr().err


def test_cli_categorical_single_category_is_a_clean_error(tmp_path, capsys):
    csv = "a,b\npos,pos\npos,pos\npos,pos\n"
    rc = main([_write(tmp_path, csv), "--categorical", "-a", "a", "-b", "b"])
    assert rc == 2
    assert "분석 오류" in capsys.readouterr().err


def test_cli_categorical_too_few_rows(tmp_path, capsys):
    csv = "a,b\npos,neg\n"
    rc = main([_write(tmp_path, csv), "--categorical", "-a", "a", "-b", "b"])
    assert rc == 2
    assert "최소 2쌍" in capsys.readouterr().err


def test_cli_categorical_missing_file(tmp_path, capsys):
    rc = main([str(tmp_path / "nope.csv"), "--categorical"])
    assert rc == 2
    assert "입력 오류" in capsys.readouterr().err


def test_cli_categorical_alpha_affects_ci_width(tmp_path, capsys):
    args = [_write(tmp_path, BASIC), "--categorical", "--json"]
    main(args)
    d95 = json.loads(capsys.readouterr().out)
    main(args + ["--alpha", "0.20"])
    d80 = json.loads(capsys.readouterr().out)
    w95 = (d95["coefficients"]["cohens_kappa"]["ci"][1]
           - d95["coefficients"]["cohens_kappa"]["ci"][0])
    w80 = (d80["coefficients"]["cohens_kappa"]["ci"][1]
           - d80["coefficients"]["cohens_kappa"]["ci"][0])
    assert w80 < w95


def test_cli_name_overrides_apply(tmp_path, capsys):
    main([_write(tmp_path, BASIC), "--categorical", "--name-a", "PSG",
          "--name-b", "기기", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["rater_a"] == "PSG" and d["rater_b"] == "기기"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _res(counts=((20, 5), (10, 15)), **kw):
    cats = kw.pop("cats", ["neg", "pos"])
    a, b = [], []
    for i in range(len(counts)):
        for j in range(len(counts)):
            a.extend([cats[i]] * counts[i][j])
            b.extend([cats[j]] * counts[i][j])
    return analyze_categorical(a, b, **kw)


def test_render_text_contains_every_section():
    txt = render_cat_text(_res())
    for section in ("[1] 데이터 요약", "[2] 교차표", "[3] 일치도 계수",
                    "[4] kappa 역설 진단", "[5] 범주별 일치도",
                    "[6] 주변 동질성 검정", "논문용 문장"):
        assert section in txt


def test_render_text_matrix_rows_are_width_aligned():
    """CJK labels are double-width; the rendered table must still line up."""
    import unicodedata

    def width(s):
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
                   for c in s if not unicodedata.combining(c))

    res = _res(cats=["음성", "양성"], name_a="판독의1", name_b="판독의2")
    lines = [l for l in render_cat_text(res).splitlines() if " | " in l]
    assert len({width(l) for l in lines}) == 1


def test_render_text_reports_paradox_when_present():
    txt = render_cat_text(_res(counts=((118, 5), (2, 0))))
    assert "역설 감지" in txt
    assert "Gwet's AC1" in txt


def test_render_json_is_nan_safe_when_kappa_undefined():
    """Both raters used only 'a' (with 'b' declared): pe=1 -> kappa is NaN.
    JSON must still be emitted, with null rather than a literal NaN."""
    res = analyze_categorical(["a"] * 30, ["a"] * 30, categories=["a", "b"])
    raw = render_cat_json(res)
    assert "NaN" not in raw
    d = json.loads(raw)
    assert d["coefficients"]["cohens_kappa"]["value"] is None
    assert d["confusion_matrix"]["observed_agreement"] == 1.0


def test_render_json_nan_becomes_null():
    d = json.loads(render_cat_json(_res(counts=((10, 1, 0), (1, 10, 1),
                                                (0, 1, 10)),
                                        cats=["a", "b", "c"])))
    assert d["paradox_diagnostics"]["prevalence_index"] is None


def test_render_markdown_escapes_pipes_in_names():
    res = _res(name_a="a|b", name_b="c|d")
    md = render_cat_markdown(res)
    assert "a\\|b" in md
    header = [l for l in md.splitlines() if l.startswith("# ")][0]
    assert header.count("|") == header.count("\\|")


def test_render_markdown_table_row_counts():
    md = render_cat_markdown(_res())
    assert md.count("| **") == 3          # 2 category rows + Total
    assert "## Per-category agreement" in md


def test_sentence_mentions_ci_lower_grade_not_point_grade():
    """Honesty: the pasteable sentence must grade from the CI lower bound."""
    res = _res(counts=((8, 2), (2, 8)))
    txt = render_cat_text(res)
    sentence = txt.split("[논문용 문장 / Ready-to-paste sentence]")[1]
    assert "신뢰구간 하한" in sentence


def test_sentence_reports_paradox_and_ac1():
    res = _res(counts=((118, 5), (2, 0)))
    sentence = render_cat_text(res).split("Ready-to-paste sentence]")[1]
    assert "AC1" in sentence and "역설" in sentence


# --------------------------------------------------------------------------
# Markdown must carry the warnings (it is the paste-into-the-paper path)
# --------------------------------------------------------------------------
def test_markdown_includes_warnings_section():
    """REGRESSION: markdown used to drop every caveat — kappa paradox, assumed
    category order, failed acceptance — exactly the text a reader needs."""
    res = _res(counts=((85, 5), (5, 5)), min_kappa=0.6)
    md = render_cat_markdown(res)
    assert "## 주의 / Warnings" in md
    assert any("역설" in line for line in md.splitlines())
    assert res.warnings and all(
        any(w[:20] in line for line in md.splitlines()) for w in res.warnings)


def test_markdown_has_no_warnings_section_when_there_are_none():
    res = _res(counts=((500, 5), (5, 500)), cats=["0", "1"])
    md = render_cat_markdown(res)
    if not res.warnings:
        assert "## 주의" not in md


def test_markdown_escapes_pipes_inside_warning_text():
    res = analyze_categorical(["a|b"] * 10 + ["c"] * 10,
                              ["a|b"] * 9 + ["c"] * 11)
    md = render_cat_markdown(res)
    for line in md.splitlines():
        if line.startswith("- "):
            assert "|" not in line or "\\|" in line


def test_continuous_markdown_includes_warnings_section():
    """The continuous report had the same gap."""
    from agreestat import analyze
    from agreestat.report import render_markdown
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    b = [1.1, 2.3, 3.6, 4.9, 6.2, 7.6]      # proportional bias -> warnings
    res = analyze(a, b)
    md = render_markdown(res)
    assert res.warnings
    assert "## 주의 / Warnings" in md


def test_cli_markdown_file_contains_warnings(tmp_path):
    csv = "a,b\n" + "\n".join(["pos,pos"] * 85 + ["pos,neg"] * 5
                              + ["neg,pos"] * 5 + ["neg,neg"] * 5) + "\n"
    out = tmp_path / "o.md"
    rc = main([_write(tmp_path, csv), "--categorical", "--min-kappa", "0.6",
               "--markdown", str(out)])
    assert rc == 0
    assert "Warnings" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Cluster bootstrap via the CLI
# --------------------------------------------------------------------------
CLUSTERED = "subject,r1,r2\n" + "".join(
    f"S{s:02d},{c},{c if (s + i) % 4 else ('x' if c == 'y' else 'y')}\n"
    for s in range(12) for i, c in enumerate(["x", "y", "x", "y", "x", "y"]))


def test_cli_cluster_bootstrap_reports_and_is_json_complete(tmp_path, capsys):
    main([_write(tmp_path, CLUSTERED), "--categorical", "-s", "subject",
          "--bootstrap", "200", "--json"])
    d = json.loads(capsys.readouterr().out)
    cb = d["cluster_bootstrap"]
    assert cb["available"] is True
    assert cb["n_subjects"] == 12
    assert cb["replicates"] == 200
    assert cb["ci"][0] <= cb["value"] <= cb["ci"][1]
    assert d["acceptance"]["judged_on"] == "cluster bootstrap CI lower bound"


def test_cli_cluster_text_section_present(tmp_path, capsys):
    main([_write(tmp_path, CLUSTERED), "--categorical", "-s", "subject",
          "--bootstrap", "200"])
    out = capsys.readouterr().out
    assert "[3b] 군집 보정 신뢰구간" in out
    assert "설계효과" in out
    assert "naive CI" in out


def test_cli_seed_makes_cluster_ci_reproducible(tmp_path, capsys):
    args = [_write(tmp_path, CLUSTERED), "--categorical", "-s", "subject",
            "--bootstrap", "200", "--json"]
    main(args + ["--seed", "1"])
    d1 = json.loads(capsys.readouterr().out)
    main(args + ["--seed", "1"])
    d2 = json.loads(capsys.readouterr().out)
    main(args + ["--seed", "2"])
    d3 = json.loads(capsys.readouterr().out)
    assert d1["cluster_bootstrap"]["ci"] == d2["cluster_bootstrap"]["ci"]
    assert d1["cluster_bootstrap"]["ci"] != d3["cluster_bootstrap"]["ci"]


def test_cli_rejects_bad_bootstrap_count(tmp_path, capsys):
    rc = main([_write(tmp_path, CLUSTERED), "--categorical", "-s", "subject",
               "--bootstrap", "5"])
    assert rc == 2
    assert "bootstrap" in capsys.readouterr().err


def test_cli_na_option_declares_missing(tmp_path, capsys):
    csv = "a,b\npos,pos\nNA,neg\npos,NA\nneg,neg\npos,pos\nneg,neg\n"
    main([_write(tmp_path, csv), "--categorical", "--na", "NA", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["n"] == 4 and d["dropped"] == 2
    assert "NA" not in d["categories"]


def test_cli_rejects_empty_na_list(tmp_path, capsys):
    rc = main([_write(tmp_path, BASIC), "--categorical", "--na", " , "])
    assert rc == 2
    assert "--na" in capsys.readouterr().err


def test_cli_too_many_categories_is_a_clean_error(tmp_path, capsys):
    """Explicit columns: the k ceiling refuses before allocating a k x k table."""
    rows = "\n".join(f"{i}.5,{i}.7" for i in range(250))
    path = _write(tmp_path, "a,b\n" + rows + "\n")
    rc = main([path, "--categorical", "-a", "a", "-b", "b"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "너무 많습니다" in err and "--categorical 없이" in err


def test_cli_continuous_data_autodetect_says_it_looks_continuous(tmp_path, capsys):
    """Auto-detect finds no rating column but two numeric ones — say so, rather
    than the unhelpful 'could not auto-detect two rating columns'."""
    rows = "\n".join(f"{i}.5,{i}.7" for i in range(250))
    rc = main([_write(tmp_path, "a,b\n" + rows + "\n"), "--categorical"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "연속형" in err and "--categorical 없이" in err


def test_huge_category_label_does_not_wreck_the_table(tmp_path, capsys):
    big = "L" * 5000
    csv = f"a,b\n{big},{big}\nx,x\n{big},x\nx,x\n"
    rc = main([_write(tmp_path, csv), "--categorical", "-a", "a", "-b", "b"])
    out = capsys.readouterr().out
    assert rc == 0
    assert max(len(line) for line in out.splitlines()) < 300
