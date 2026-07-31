"""Tests for --raters / --long loading and CLI routing."""

import json

import pytest

from agreestat.cli import main
from agreestat.dataio import (
    label_rater_rows,
    load_rater_table,
    numeric_rater_rows,
    reshape_long,
)

WIDE = """case,r1,r2,r3
1,4.1,4.3,4.0
2,5.2,5.0,5.4
3,3.3,3.6,3.1
4,6.8,6.5,6.9
5,4.4,4.2,4.6
6,7.1,7.4,7.0
"""

LONG = """subject,rater,score
S1,a,4.1
S1,b,4.3
S1,c,4.0
S2,a,5.2
S2,b,5.0
S2,c,5.4
S3,a,3.3
S3,b,3.6
S3,c,3.1
S4,a,6.8
S4,b,6.5
S4,c,6.9
"""

LONG_CAT = """subject,rater,grade
S1,a,mild
S1,b,mild
S1,c,moderate
S2,a,severe
S2,b,severe
S2,c,severe
S3,a,mild
S3,b,moderate
S3,c,mild
S4,a,moderate
S4,b,moderate
S4,c,moderate
"""


def _write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


# --------------------------------------------------------------------------
# reshape_long
# --------------------------------------------------------------------------
def test_reshape_long_basic():
    header = ["subject", "rater", "score"]
    data = [["S1", "a", "1"], ["S1", "b", "2"], ["S2", "a", "3"],
            ["S2", "b", "4"]]
    names, rows, ids, notes = reshape_long(header, data, "subject", "rater",
                                           "score")
    assert names == ["a", "b"]
    assert ids == ["S1", "S2"]
    assert rows == [["1", "2"], ["3", "4"]]
    assert notes == []


def test_reshape_long_keeps_first_appearance_order():
    header = ["id", "m", "v"]
    data = [["B", "z", "1"], ["B", "y", "2"], ["A", "z", "3"], ["A", "y", "4"]]
    names, rows, ids, _ = reshape_long(header, data, "id", "m", "v")
    assert names == ["z", "y"]
    assert ids == ["B", "A"]
    assert rows == [["1", "2"], ["3", "4"]]


def test_reshape_long_fills_missing_cells():
    header = ["id", "m", "v"]
    data = [["S1", "a", "1"], ["S1", "b", "2"], ["S2", "a", "3"]]
    _names, rows, _ids, _n = reshape_long(header, data, "id", "m", "v")
    assert rows == [["1", "2"], ["3", ""]]


def test_reshape_long_rejects_duplicate_pair():
    header = ["id", "m", "v"]
    data = [["S1", "a", "1"], ["S1", "a", "2"], ["S1", "b", "3"]]
    with pytest.raises(ValueError, match="중복된 행") as exc:
        reshape_long(header, data, "id", "m", "v")
    assert "S1" not in str(exc.value)      # never echo a patient identifier


def test_reshape_long_explicit_levels_filter_and_order():
    header = ["id", "m", "v"]
    data = [["S1", "a", "1"], ["S1", "b", "2"], ["S1", "c", "9"],
            ["S2", "a", "3"], ["S2", "b", "4"], ["S2", "c", "8"]]
    names, rows, _ids, notes = reshape_long(header, data, "id", "m", "v",
                                            ["b", "a"])
    assert names == ["b", "a"]
    assert rows == [["2", "1"], ["4", "3"]]
    assert any("--raters 로 지정하지 않은" in n for n in notes)


def test_reshape_long_unknown_level_is_an_error():
    header = ["id", "m", "v"]
    data = [["S1", "a", "1"], ["S1", "b", "2"]]
    with pytest.raises(ValueError, match="자료에 없습니다"):
        reshape_long(header, data, "id", "m", "v", ["a", "zzz"])


def test_reshape_long_blank_keys_are_dropped():
    header = ["id", "m", "v"]
    data = [["", "a", "1"], ["S1", "", "2"], ["S1", "a", "3"], ["S1", "b", "4"]]
    _names, rows, ids, notes = reshape_long(header, data, "id", "m", "v")
    assert ids == ["S1"] and rows == [["3", "4"]]
    assert any("비어 있는 2행" in n for n in notes)


def test_reshape_long_needs_two_methods():
    header = ["id", "m", "v"]
    data = [["S1", "a", "1"], ["S2", "a", "2"]]
    with pytest.raises(ValueError, match="2개 이상"):
        reshape_long(header, data, "id", "m", "v")


def test_reshape_long_missing_column_message_names_the_flag():
    with pytest.raises(ValueError, match="--method-col"):
        reshape_long(["id", "v"], [["S1", "1"]], "id", "nope", "v")


def test_reshape_long_rejects_same_column_twice():
    with pytest.raises(ValueError, match="서로 다른"):
        reshape_long(["id", "v"], [["S1", "1"]], "id", "id", "v")


def test_reshape_long_too_many_methods():
    header = ["id", "m", "v"]
    data = [[f"S{i}", f"m{i}", "1"] for i in range(150)]
    with pytest.raises(ValueError, match="확인하세요"):
        reshape_long(header, data, "id", "m", "v")


# --------------------------------------------------------------------------
# load_rater_table / parsing
# --------------------------------------------------------------------------
def test_load_rater_table_wide(tmp_path):
    path = _write(tmp_path, "w.csv", WIDE)
    t = load_rater_table(path, ["r1", "r2", "r3"])
    assert t.names == ["r1", "r2", "r3"] and t.n == 6 and t.k == 3
    rows, ids, dropped, nonfinite = numeric_rater_rows(t)
    assert len(rows) == 6 and dropped == 0 and nonfinite == 0 and ids == []


def test_load_rater_table_wide_missing_column(tmp_path):
    path = _write(tmp_path, "w.csv", WIDE)
    with pytest.raises(ValueError, match="헤더에 없습니다"):
        load_rater_table(path, ["r1", "nope"])


def test_load_rater_table_long(tmp_path):
    path = _write(tmp_path, "l.csv", LONG)
    t = load_rater_table(path, None, long_format=True, id_col="subject",
                         method_col="rater", value_col="score")
    assert t.names == ["a", "b", "c"]
    assert t.subjects == ["S1", "S2", "S3", "S4"]


def test_load_rater_table_long_needs_all_three_cols(tmp_path):
    path = _write(tmp_path, "l.csv", LONG)
    with pytest.raises(ValueError, match="--id-col"):
        load_rater_table(path, None, long_format=True, id_col="subject")


def test_load_rater_table_duplicate_header(tmp_path):
    path = _write(tmp_path, "d.csv", "a,a,b\n1,2,3\n")
    with pytest.raises(ValueError, match="중복"):
        load_rater_table(path, ["a", "b"])


def test_numeric_rater_rows_listwise_deletes(tmp_path):
    path = _write(tmp_path, "m.csv", "a,b,c\n1,2,3\n4,,6\n7,8,x\n9,10,11\n")
    t = load_rater_table(path, ["a", "b", "c"])
    rows, _ids, dropped, nonfinite = numeric_rater_rows(t)
    assert len(rows) == 2 and dropped == 2 and nonfinite == 0


def test_numeric_rater_rows_counts_nonfinite(tmp_path):
    path = _write(tmp_path, "m.csv", "a,b,c\n1,2,3\n4,inf,6\n7,8,9\n")
    t = load_rater_table(path, ["a", "b", "c"])
    _rows, _ids, dropped, nonfinite = numeric_rater_rows(t)
    assert dropped == 1 and nonfinite == 1


def test_numeric_rater_rows_all_missing_raises(tmp_path):
    path = _write(tmp_path, "m.csv", "a,b,c\n1,,3\n,2,\n")
    t = load_rater_table(path, ["a", "b", "c"])
    with pytest.raises(ValueError, match="숫자가 채워진"):
        numeric_rater_rows(t)


def test_label_rater_rows_drops_single_rating_subjects(tmp_path):
    path = _write(tmp_path, "c.csv",
                  "a,b,c\nmild,mild,severe\nmild,,\n,,severe\nmild,mild,mild\n")
    t = load_rater_table(path, ["a", "b", "c"])
    rows, dropped, _notes = label_rater_rows(t)
    assert dropped == 2 and len(rows) == 2


def test_label_rater_rows_na_declaration(tmp_path):
    path = _write(tmp_path, "c.csv",
                  "a,b,c\nmild,NA,severe\nmild,mild,mild\nNA,NA,mild\n")
    t = load_rater_table(path, ["a", "b", "c"])
    rows, dropped, notes = label_rater_rows(t, ["NA"])
    assert dropped == 1                       # third row keeps only 1 rating
    assert rows[0] == ["mild", "", "severe"]
    assert not any("실제 범주" in n for n in notes)
    rows2, _d, notes2 = label_rater_rows(t)   # without --na, NA is a category
    assert rows2[0] == ["mild", "NA", "severe"]
    assert any("실제 범주" in n for n in notes2)


# --------------------------------------------------------------------------
# CLI routing
# --------------------------------------------------------------------------
def test_cli_multi_continuous_wide(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2,r3"]) == 0
    out = capsys.readouterr().out
    assert "Multi-rater agreement (continuous)" in out
    assert "ICC(2,3)" in out


def test_cli_multi_continuous_json(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2,r3", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_raters"] == 3 and data["raters"] == ["r1", "r2", "r3"]


def test_cli_long_continuous(tmp_path, capsys):
    path = _write(tmp_path, "l.csv", LONG)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "score", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["raters"] == ["a", "b", "c"] and data["n_subjects"] == 4


def test_cli_long_categorical(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--categorical",
                 "--bootstrap", "200", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["analysis"] == "multi_rater_categorical"
    assert data["n_raters"] == 3
    assert set(data["categories"]) == {"mild", "moderate", "severe"}


def test_cli_two_raters_falls_back_to_pairwise(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "bland_altman" in data
    assert data["method_a"] == "r1" and data["method_b"] == "r2"
    assert data["n"] == 6


def test_cli_two_raters_from_long_categorical(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--raters", "a,b",
                 "--categorical", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rater_a"] == "a" and data["rater_b"] == "b"
    assert data["analysis"] == "categorical"
    assert "cohens_kappa" in data["coefficients"]


def test_cli_long_requires_the_three_columns(tmp_path, capsys):
    path = _write(tmp_path, "l.csv", LONG)
    assert main([path, "--long", "--id-col", "subject"]) == 2
    assert "--method-col" in capsys.readouterr().err


def test_cli_long_cols_without_long_flag(tmp_path, capsys):
    path = _write(tmp_path, "l.csv", LONG)
    assert main([path, "--id-col", "subject"]) == 2
    assert "--long 과 함께" in capsys.readouterr().err


def test_cli_raters_conflicts_with_ab(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2,r3", "-a", "r1"]) == 2
    assert "-a/-b" in capsys.readouterr().err


def test_cli_raters_conflicts_with_subject(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2,r3", "-s", "case"]) == 2
    assert "--subject" in capsys.readouterr().err


def test_cli_multi_rejects_two_method_only_flags(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r2,r3", "--accept", "1.0"]) == 2
    err = capsys.readouterr().err
    assert "--accept" in err and "두 방법" in err
    assert main([path, "--raters", "r1,r2,r3", "--percent"]) == 2


def test_cli_raters_needs_two_names(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1"]) == 2
    assert "2개 이상" in capsys.readouterr().err


def test_cli_raters_rejects_duplicates(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    assert main([path, "--raters", "r1,r1,r2"]) == 2
    assert "중복" in capsys.readouterr().err


def test_cli_multi_markdown_to_file(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", WIDE)
    out = tmp_path / "out.md"
    assert main([path, "--raters", "r1,r2,r3", "--markdown", str(out)]) == 0
    assert "ICC(1,1)" in out.read_text(encoding="utf-8")
    assert "저장했습니다" in capsys.readouterr().err


def test_cli_multi_categorical_ordinal_categories(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--categorical", "--ordinal",
                 "--categories", "mild,moderate,severe", "--bootstrap", "200",
                 "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["categories"] == ["mild", "moderate", "severe"]
    assert data["krippendorff_alpha"]["metric"] == "ordinal"
    assert data["weights"] == "quadratic"


def test_cli_multi_categorical_bad_categories(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--categorical",
                 "--categories", "mild,mild"]) == 2
    assert "중복" in capsys.readouterr().err


def test_cli_multi_categorical_bad_bootstrap(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--categorical",
                 "--bootstrap", "3"]) == 2
    assert "--bootstrap" in capsys.readouterr().err


def test_cli_multi_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.csv"), "--raters", "a,b,c"]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_cli_multi_too_few_complete_rows(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", "a,b,c\n1,2,3\n4,,6\n")
    assert main([path, "--raters", "a,b,c"]) == 2
    assert "최소 2건" in capsys.readouterr().err


def test_cli_long_accepts_cp949(tmp_path, capsys):
    text = "대상,판독자,값\nS1,갑,1.0\nS1,을,1.1\nS1,병,0.9\n" \
           "S2,갑,2.0\nS2,을,2.2\nS2,병,1.9\n" \
           "S3,갑,3.0\nS3,을,3.1\nS3,병,2.8\n"
    path = _write(tmp_path, "k.csv", text, encoding="cp949")
    assert main([path, "--long", "--id-col", "대상", "--method-col", "판독자",
                 "--value-col", "값", "--encoding", "cp949", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["raters"] == ["갑", "을", "병"]


def test_cli_examples_multi_files_run(capsys):
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wide = os.path.join(here, "examples", "lesion_grade_three_readers.csv")
    lng = os.path.join(here, "examples", "tumor_size_long.csv")
    assert main([wide, "--raters", "reader_A,reader_B,reader_C",
                 "--categorical", "--ordinal",
                 "--categories", "mild,moderate,severe",
                 "--bootstrap", "200"]) == 0
    assert main([lng, "--long", "--id-col", "subject_id", "--method-col",
                 "reader", "--value-col", "size_mm"]) == 0
    out = capsys.readouterr().out
    assert "Fleiss' kappa" in out and "ICC(2,3)" in out


def test_cli_two_raters_from_table_honours_categories(tmp_path, capsys):
    """--categories must not be silently ignored on the --raters/2-column path."""
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--raters", "a,b",
                 "--categorical", "--ordinal",
                 "--categories", "mild,moderate,severe", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["categories"] == ["mild", "moderate", "severe"]


def test_cli_two_raters_from_table_rejects_bad_categories(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--raters", "a,b",
                 "--categorical", "--categories", "mild"]) == 2
    assert "2개 이상" in capsys.readouterr().err


def test_cli_two_raters_from_table_validates_bootstrap(tmp_path, capsys):
    path = _write(tmp_path, "lc.csv", LONG_CAT)
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "grade", "--raters", "a,b",
                 "--categorical", "--bootstrap", "1"]) == 2
    assert "--bootstrap" in capsys.readouterr().err


def test_cli_two_raters_from_table_supports_accept_and_svg(tmp_path, capsys):
    path = _write(tmp_path, "l.csv", LONG)
    svg = tmp_path / "p.svg"
    assert main([path, "--long", "--id-col", "subject", "--method-col",
                 "rater", "--value-col", "score", "--raters", "a,b",
                 "--accept", "2", "--svg", str(svg), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["acceptance"]["lower"] == -2.0
    assert svg.read_text(encoding="utf-8").startswith("<?xml")
