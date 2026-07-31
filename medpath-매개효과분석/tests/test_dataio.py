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
    "inf", "-inf", "nan", "1_000", "abc", "12 34", "5.5.5", "--3",
    "1e308",        # squares to inf in the residual sums -> refuse, don't crash
])
def test_parse_float_rejects_ambiguous_or_non_numeric(token):
    assert parse_float(token) is None


# Typographic look-alikes are *not* ambiguous — they have exactly one reading.
# Rejecting them was worse than accepting: an unparsed cell becomes "missing",
# so a column of Excel-autocorrected negatives silently deleted every negative
# row and biased the sample instead of raising anything.
@pytest.mark.parametrize("token,expected", [
    ("−2.11", -2.11),    # MINUS SIGN (Excel/Word autocorrect)
    ("–3.5", -3.5),      # EN DASH
    ("－4.5", -4.5),      # FULLWIDTH HYPHEN-MINUS
    ("１２３", 123.0),        # full-width digits
    ("１.５", 1.5),
    ("1 234.5", 1234.5),             # non-breaking space inside a number
    ("2 000.5", 2000.5),             # thin space
])
def test_parse_float_normalizes_typographic_lookalikes(token, expected):
    assert parse_float(token) == pytest.approx(expected)


def test_unicode_minus_does_not_silently_delete_negative_rows(tmp_path):
    """Regression: only the negative rows failed to parse and vanished.

    The surviving sample was systematically biased upward, which flipped the
    reported total effect from significant to null on otherwise identical data.
    """
    rows = []
    for i in range(20):
        y = i - 10
        token = ("−%g" % abs(y)) if y < 0 else "%g" % y   # unicode minus
        rows.append([i % 2, i * 1.5, token])
    t = load_table(write_csv(tmp_path / "u.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y")
    assert d.n_used == 20                       # nothing dropped
    assert min(d.y) == -10.0                    # negatives survived intact


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


# --------------------------------------------------------------------------
# Numeric X with explicit grouping flags
# (regression: --reference / --x-levels were silently ignored on numeric X,
#  so a user who asked to compare two dose levels got the full continuous
#  analysis over every row without any notice.)
# --------------------------------------------------------------------------
def test_x_levels_is_honoured_on_a_numeric_x(tmp_path):
    rows = [[dose, i, i * 2] for dose in (0, 1, 2) for i in range(5)]
    t = _table(tmp_path, ["dose", "m", "y"], rows)
    d = build_design(t, "dose", ["m"], "y", x_levels=["0", "2"])
    assert d.x_kind == "dummy"
    assert d.n_used == 10                    # the dose==1 rows are excluded
    assert d.x_reference == "0" and d.x_comparison == "2"
    assert set(d.x) == {0.0, 1.0}


def test_reference_is_honoured_on_a_numeric_two_level_x(tmp_path):
    rows = [[g, i, i * 2] for g in (0, 1) for i in range(5)]
    t = _table(tmp_path, ["grp", "m", "y"], rows)
    d = build_design(t, "grp", ["m"], "y", reference="1")
    assert d.x_kind == "dummy"
    assert d.x_reference == "1" and d.x_comparison == "0"
    assert d.n_used == 10


def test_unknown_numeric_x_level_is_rejected_not_ignored(tmp_path):
    rows = [[dose, i, i * 2] for dose in (0, 1, 2) for i in range(5)]
    t = _table(tmp_path, ["dose", "m", "y"], rows)
    with pytest.raises(DataError, match="없는 수준"):
        build_design(t, "dose", ["m"], "y", x_levels=["7", "9"])


def test_numeric_x_without_grouping_flags_stays_continuous(tmp_path):
    rows = [[dose, i, i * 2] for dose in (0, 1, 2) for i in range(5)]
    t = _table(tmp_path, ["dose", "m", "y"], rows)
    d = build_design(t, "dose", ["m"], "y")
    assert d.x_kind == "numeric"
    assert d.n_used == 15
    assert d.x_label.endswith("(연속형 그대로 사용)")


def test_grouping_a_numeric_x_is_recorded_in_the_notes(tmp_path):
    rows = [[dose, i, i * 2] for dose in (0, 1, 2) for i in range(5)]
    t = _table(tmp_path, ["dose", "m", "y"], rows)
    d = build_design(t, "dose", ["m"], "y", x_levels=["0", "2"])
    assert any("범주형" in n and "--x-levels" in n for n in d.notes)


# --------------------------------------------------------------------------
# Unparseable cells must be announced, not folded into the generic "결측" count
# --------------------------------------------------------------------------
def test_unparseable_cells_raise_a_warning_naming_the_column(tmp_path):
    rows = [[i % 2, i * 1.5, ("%d ms" % i) if i % 5 == 0 else i] for i in range(30)]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    d = build_design(t, "x", ["m"], "y")
    assert d.n_used == 24
    joined = " ".join(d.warnings)
    assert "'y'" in joined and "6/30" in joined
    assert "ms" in joined                      # a sample token is shown


def test_unparseable_warning_reaches_the_report_not_just_the_notes(tmp_path):
    """--brief hides notes, so this must be a warning or it disappears."""
    from medpath.mediation import analyze
    from medpath.report import render
    rows = [[i % 2, i * 1.5, ("%d ms" % i) if i % 5 == 0 else i] for i in range(40)]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    res = analyze(build_design(t, "x", ["m"], "y"), n_boot=0)
    assert any("숫자로 해석되지" in w for w in res.warnings)
    assert "숫자로 해석되지" in render(res, "x.csv", brief=True)


def test_unparseable_previews_are_truncated(tmp_path):
    """Cells can hold names or notes — diagnostics must not echo them whole."""
    secret = "환자김민수010223344556677889900"
    rows = [[i % 2, i * 1.5, secret if i % 6 == 0 else i] for i in range(30)]
    t = _table(tmp_path, ["x", "m", "y"], rows)
    d = build_design(t, "x", ["m"], "y")
    joined = " ".join(d.warnings)
    assert secret not in joined
    assert "…" in joined


def test_fully_non_numeric_column_error_reports_counts_not_every_value(tmp_path):
    names = ["김민수 010-2233-4455", "박준호 010-5566-7788",
             "이지연 010-9911-0022", "최다미 010-3344-1212"]
    rows = [[i % 2, i * 1.5, names[i % 4]] for i in range(20)]
    t = _table(tmp_path, ["x", "m", "note"], rows)
    with pytest.raises(DataError) as exc:
        build_design(t, "x", ["m"], "note")
    msg = str(exc.value)
    assert "20/20" in msg
    assert sum(1 for n in names if n in msg) == 0     # no full value echoed


def test_clean_data_produces_no_unparsed_warning(tmp_path):
    rows = [[i % 2, i * 1.5, i * 2.0] for i in range(20)]
    d = build_design(_table(tmp_path, ["x", "m", "y"], rows), "x", ["m"], "y")
    assert not any("숫자로 해석되지" in w for w in d.warnings)
