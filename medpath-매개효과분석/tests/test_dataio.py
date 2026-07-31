"""CSV loading and design construction, including the messy-export cases."""

import pytest

from helpers import write_csv
from medpath.dataio import DataError, build_design, load_table, parse_float


# --------------------------------------------------------------------------
# parse_float — strictness is the whole point
# --------------------------------------------------------------------------
@pytest.mark.parametrize("token,expected", [
    ("1", 1.0), ("1.5", 1.5), (".5", 0.5), ("-2.25", -2.25), ("+3", 3.0),
    ("1e3", 1000.0), ("2.5E-2", 0.025), (" 7 ", 7.0), ('"8.5"', 8.5),
    ("1,234", 1234.0), ("1,234.5", 1234.5), ("12%", 12.0),
])
def test_parse_float_accepts_benign_noise(token, expected):
    assert parse_float(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", [
    "", "NA", "N/A", "na", ".", "-", "#N/A", "결측", "없음", "null",
    "1,5",          # European decimal comma — ambiguous, must NOT become 15 or 1.5
    "inf", "-inf", "nan", "1_000", "１２３", "abc", "12 34", "5.5.5", "--3",
])
def test_parse_float_rejects_ambiguous_or_non_numeric(token):
    assert parse_float(token) is None


# --------------------------------------------------------------------------
# load_table
# --------------------------------------------------------------------------
def test_loads_basic_csv(tmp_path):
    p = write_csv(tmp_path / "a.csv", ["x", "y"], [[1, 2], [3, 4]])
    t = load_table(p)
    assert t.header == ["x", "y"]
    assert len(t) == 2
    assert t.column("y") == ["2", "4"]


def test_detects_tab_and_semicolon_delimiters(tmp_path):
    for sep, name in ((";", "semi.csv"), ("\t", "tab.tsv"), ("|", "pipe.csv")):
        p = tmp_path / name
        p.write_text(sep.join(["a", "b", "c"]) + "\n" + sep.join(["1", "2", "3"]) + "\n",
                     encoding="utf-8")
        t = load_table(str(p))
        assert t.header == ["a", "b", "c"], name
        assert t.column("b") == ["2"]


def test_explicit_delimiter_overrides_sniffing(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")
    t = load_table(str(p), delimiter=",")
    assert t.header == ["a;b"]


def test_strips_bom(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿name,val\n가,1\n".encode("utf-8"))
    t = load_table(str(p))
    assert t.header == ["name", "val"]
    assert t.column("name") == ["가"]


def test_reads_korean_excel_cp949(tmp_path):
    p = tmp_path / "cp949.csv"
    p.write_bytes("군,값\n대조,1\n처치,2\n".encode("cp949"))
    t = load_table(str(p))
    assert t.header == ["군", "값"]
    assert t.column("군") == ["대조", "처치"]
    assert any("cp949" in n for n in t.notes)


def test_ragged_rows_are_padded_and_truncated_with_notes(tmp_path):
    p = tmp_path / "ragged.csv"
    p.write_text("a,b,c\n1,2\n3,4,5,6\n7,8,9\n", encoding="utf-8")
    t = load_table(str(p))
    assert len(t) == 3
    assert t.column("c") == ["", "5", "9"]
    joined = " ".join(t.notes)
    assert "부족한" in joined and "많은" in joined


def test_duplicate_headers_use_leftmost_and_warn(tmp_path):
    p = tmp_path / "dup.csv"
    p.write_text("x,x,y\n1,99,3\n", encoding="utf-8")
    t = load_table(str(p))
    assert t.column("x") == ["1"]
    assert any("같은 이름" in n for n in t.notes)


def test_blank_lines_are_skipped(tmp_path):
    p = tmp_path / "blank.csv"
    p.write_text("a,b\n\n1,2\n\n\n3,4\n", encoding="utf-8")
    assert len(load_table(str(p))) == 2


def test_missing_file_raises_data_error(tmp_path):
    with pytest.raises(DataError, match="찾을 수 없습니다"):
        load_table(str(tmp_path / "nope.csv"))


def test_empty_file_raises(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("   \n", encoding="utf-8")
    with pytest.raises(DataError):
        load_table(str(p))


def test_header_only_raises(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(DataError, match="데이터 행이 없습니다"):
        load_table(str(p))


def test_binary_file_is_rejected_clearly(tmp_path):
    p = tmp_path / "book.xlsx"
    p.write_bytes(b"PK\x03\x04\x00\x00binary junk\x00\x00")
    with pytest.raises(DataError, match="CSV"):
        load_table(str(p))


def test_unknown_column_lists_available_columns(tmp_path):
    p = write_csv(tmp_path / "a.csv", ["x", "y"], [[1, 2]])
    with pytest.raises(DataError) as exc:
        load_table(p).column("z")
    assert "x, y" in str(exc.value)


# --------------------------------------------------------------------------
# build_design
# --------------------------------------------------------------------------
def _table(tmp_path, header, rows, name="d.csv"):
    return load_table(write_csv(tmp_path / name, header, rows))


def test_numeric_x_is_used_as_is(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"],
               [[i, i + 0.5, 2 * i + 1] for i in range(1, 11)])
    d = build_design(t, "x", ["m"], "y")
    assert d.x_kind == "numeric"
    assert d.n_used == 10
    assert d.x[:3] == [1.0, 2.0, 3.0]


def test_two_level_categorical_x_prefers_control_looking_reference(tmp_path):
    rows = [["device", i, i] for i in range(6)] + [["sham", i, i] for i in range(5)]
    t = _table(tmp_path, ["arm", "m", "y"], rows)
    d = build_design(t, "arm", ["m"], "y")
    # 'device' is more frequent, but 'sham' is recognised as the control arm
    assert d.x_reference == "sham" and d.x_comparison == "device"
    assert d.x[0] == 1.0 and d.x[-1] == 0.0
    assert any("sham" in n for n in d.notes)


def test_reference_flag_overrides_automatic_choice(tmp_path):
    rows = [["device", i, i] for i in range(6)] + [["sham", i, i] for i in range(5)]
    t = _table(tmp_path, ["arm", "m", "y"], rows)
    d = build_design(t, "arm", ["m"], "y", reference="device")
    assert d.x_reference == "device" and d.x_comparison == "sham"


def test_unknown_reference_level_raises(tmp_path):
    rows = [["a", i, i] for i in range(4)] + [["b", i, i] for i in range(4)]
    t = _table(tmp_path, ["arm", "m", "y"], rows)
    with pytest.raises(DataError, match="reference"):
        build_design(t, "arm", ["m"], "y", reference="c")


def test_three_level_x_requires_explicit_levels(tmp_path):
    rows = [[lv, i, i] for lv in ("low", "mid", "high") for i in range(4)]
    t = _table(tmp_path, ["dose", "m", "y"], rows)
    with pytest.raises(DataError, match="x-levels"):
        build_design(t, "dose", ["m"], "y")
    d = build_design(t, "dose", ["m"], "y", x_levels=["low", "high"])
    assert d.n_used == 8                      # 'mid' rows dropped from the contrast
    assert d.x_reference == "low"


def test_single_level_x_raises(tmp_path):
    t = _table(tmp_path, ["arm", "m", "y"], [["a", i, i] for i in range(5)])
    with pytest.raises(DataError, match="한 종류"):
        build_design(t, "arm", ["m"], "y")


def test_categorical_covariate_is_dummy_coded(tmp_path):
    rows = [[i, i + 1, i + 2, lv] for i, lv in
            enumerate(["남", "여", "남", "여", "기타", "남", "여", "기타"])]
    t = _table(tmp_path, ["x", "m", "y", "sex"], rows)
    d = build_design(t, "x", ["m"], "y", ["sex"])
    names = [nm for nm, _ in d.covariates]
    assert len(names) == 2                    # 3 levels -> 2 dummies
    assert all(nm.startswith("sex=") for nm in names)
    assert any("기준" in n for n in d.covariate_notes)


def test_high_cardinality_covariate_is_rejected(tmp_path):
    rows = [[i, i + 1, i + 2, "id%02d" % i] for i in range(25)]
    t = _table(tmp_path, ["x", "m", "y", "pid"], rows)
    with pytest.raises(DataError, match="수준이"):
        build_design(t, "x", ["m"], "y", ["pid"])


def test_listwise_deletion_counts_missing_per_column(tmp_path):
    rows = [[1, 2, 3], [2, "", 4], [3, 4, "NA"], [4, 5, 6], [5, 6, 7]]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    d = build_design(t, "x", ["m"], "y")
    assert d.n_total == 5 and d.n_used == 3
    assert dict(d.missing_by_column) == {"m": 1, "y": 1}
    assert d.row_ids == [1, 4, 5]


def test_all_rows_missing_raises(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"], [[1, "", 3], [2, 4, ""]])
    with pytest.raises(DataError, match="하나도 없습니다"):
        build_design(t, "x", ["m"], "y")


def test_overlapping_roles_raise(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"], [[i, i + 1, i + 2] for i in range(5)])
    with pytest.raises(DataError, match="서로 다른 열"):
        build_design(t, "x", ["x"], "y")
    with pytest.raises(DataError, match="서로 다른 열"):
        build_design(t, "x", ["m"], "x")
    with pytest.raises(DataError, match="공변량"):
        build_design(t, "x", ["m"], "y", ["m"])


def test_duplicate_mediators_raise(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"], [[i, i + 1, i + 2] for i in range(5)])
    with pytest.raises(DataError, match="중복"):
        build_design(t, "x", ["m", "m"], "y")


def test_no_mediator_raises(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"], [[i, i + 1, i + 2] for i in range(5)])
    with pytest.raises(DataError, match="매개변수"):
        build_design(t, "x", [], "y")


def test_text_mediator_raises_with_helpful_message(tmp_path):
    rows = [[i, "높음" if i % 2 else "낮음", i + 2] for i in range(8)]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    with pytest.raises(DataError, match="연속형"):
        build_design(t, "x", ["m"], "y")


def test_constant_outcome_raises(tmp_path):
    t = _table(tmp_path, ["x", "m", "y"], [[i, i + 1, 5] for i in range(6)])
    with pytest.raises(DataError, match="상수"):
        build_design(t, "x", ["m"], "y")


def test_binary_numeric_x_is_labelled_as_such(tmp_path):
    rows = [[i % 2, i + 1, i + 2] for i in range(8)]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    d = build_design(t, "x", ["m"], "y")
    assert d.x_kind == "binary"
    assert "2수준" in d.x_label


def test_duplicate_covariates_are_deduplicated(tmp_path):
    rows = [[i, i + 1, i + 2, i * 2] for i in range(8)]
    t = _table(tmp_path, ["x", "m", "y", "age"], rows)
    d = build_design(t, "x", ["m"], "y", ["age", "age"])
    assert len(d.covariates) == 1
    assert any("중복" in n for n in d.notes)
