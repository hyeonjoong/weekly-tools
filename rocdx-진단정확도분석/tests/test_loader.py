"""Reading messy clinical CSVs: encodings, separators, missing codes, labels."""

import pytest

from rocdx.loader import (
    LoadError,
    parse_label_column,
    parse_number,
    read_table,
    resolve_column,
)


def write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


# --- numbers ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("12", 12.0), ("12.5", 12.5), ("-3.25", -3.25), (" 7 ", 7.0),
    ("1,024.0", 1024.0),     # thousands separator
    ("1,024", 1024.0),
    ("3,5", 3.5),            # European decimal comma
    ("1.2e-3", 0.0012),
    ("(3.4)", -3.4),         # accounting negative
    ("+8", 8.0),
])
def test_parse_number_clean_values(raw, expected):
    val, note = parse_number(raw)
    assert val == pytest.approx(expected)
    assert note is None


def test_percent_values_are_converted_and_flagged():
    # A "%" cell is silently 100x different from a bare number in the same
    # column, so the conversion has to be visible to the caller.
    val, note = parse_number("12%")
    assert val == pytest.approx(0.12) and note == "percent"


def test_없음_is_a_negative_outcome_level_not_a_missing_value():
    res = parse_label_column(["있음", "없음", "있음", "없음"])
    assert res.positive_label == "있음"
    assert res.values == [True, False, True, False]
    assert res.n_missing == 0


def test_positive_label_records_the_levels_it_folded_into_the_negatives():
    res = parse_label_column(["재발", "무재발", "판정보류", "재발"], positive_label="재발")
    assert res.values == [True, False, False, True]
    assert res.n_folded_negative == 2
    assert set(res.folded_negative) == {"무재발", "판정보류"}


def test_explicit_negative_label_does_not_fold_anything():
    res = parse_label_column(["재발", "무재발", "판정보류"], positive_label="재발",
                             negative_label="무재발")
    assert res.n_folded_negative == 0


@pytest.mark.parametrize("raw", ["", "  ", "NA", "n/a", "NULL", ".", "-", "?",
                                 "미측정", "결측", "#N/A", "nan"])
def test_parse_number_missing_codes(raw):
    val, note = parse_number(raw)
    assert val is None and note == "missing"


@pytest.mark.parametrize("raw,expected", [("<0.05", 0.05), (">100", 100.0),
                                          ("≤0.5", 0.5), ("≥ 20", 20.0)])
def test_parse_number_detection_limits_are_flagged(raw, expected):
    val, note = parse_number(raw)
    assert val == pytest.approx(expected)
    assert note == "censored"


@pytest.mark.parametrize("raw", ["abc", "12 mg/L", "high", "3.4.5", "--5"])
def test_parse_number_rejects_non_numbers(raw):
    val, note = parse_number(raw)
    assert val is None and note == "unparsed"


def test_comma_precedence_is_documented_and_deterministic():
    # A comma is genuinely ambiguous. The documented rule: it is a thousands
    # separator only when it groups digits in exact threes, otherwise it is read
    # as a European decimal comma. Both readings are deterministic, and anything
    # that fits neither is refused rather than guessed.
    assert parse_number("12,345")[0] == pytest.approx(12345.0)   # threes → thousands
    assert parse_number("12,3456")[0] == pytest.approx(12.3456)  # not threes → decimal
    assert parse_number("1,2,3")[0] is None                      # neither → refused


# --- labels -------------------------------------------------------------------

@pytest.mark.parametrize("cells,expected_pos", [
    (["1", "0", "1"], "1"),
    (["Yes", "No"], "Yes"),
    (["positive", "negative"], "positive"),
    (["case", "control"], "case"),
    (["양성", "음성"], "양성"),
    (["질환", "정상"], "질환"),
])
def test_label_vocabularies(cells, expected_pos):
    res = parse_label_column(cells)
    assert res.positive_label == expected_pos
    assert res.values == [c == expected_pos for c in cells]


def test_label_missing_cells_become_none():
    res = parse_label_column(["Yes", "", "No", "N/A"])
    assert res.values == [True, None, False, None]
    assert res.n_missing == 2


def test_label_numeric_forms_are_canonicalised():
    res = parse_label_column(["1.0", "0", "1", "0.0"])
    assert res.values == [True, False, True, False]


def test_unknown_two_level_column_needs_an_explicit_positive_label():
    with pytest.raises(LoadError) as exc:
        parse_label_column(["A", "B", "A"])
    assert "--positive-label" in str(exc.value)


def test_explicit_positive_label_makes_everything_else_negative():
    res = parse_label_column(["A", "B", "C"], positive_label="A")
    assert res.values == [True, False, False]
    assert res.n_unparsed == 0


def test_explicit_pair_of_labels_drops_third_levels():
    res = parse_label_column(["A", "B", "C"], positive_label="A", negative_label="B")
    assert res.values == [True, False, None]
    assert res.n_unparsed == 1


def test_positive_label_not_present_is_an_error_not_an_all_negative_column():
    with pytest.raises(LoadError) as exc:
        parse_label_column(["Yes", "No"], positive_label="양성")
    assert "positive-label" in str(exc.value)


def test_three_levels_without_a_choice_is_an_error():
    with pytest.raises(LoadError) as exc:
        parse_label_column(["Yes", "No", "Maybe"])
    assert "3" in str(exc.value)


def test_single_level_outcome_is_an_error():
    with pytest.raises(LoadError):
        parse_label_column(["Yes", "Yes", "Yes"])


def test_label_matching_is_case_and_space_insensitive():
    res = parse_label_column([" YES ", "no", "Yes"], positive_label="yes")
    assert res.values == [True, False, True]


# --- files --------------------------------------------------------------------

def test_reads_utf8_bom_and_commas(tmp_path):
    path = write(tmp_path, "a.csv", "﻿id,score,dx\n1,3.2,Yes\n2,1.1,No\n")
    t = read_table(path)
    assert t.headers == ["id", "score", "dx"]
    assert t.delimiter == ","
    assert t.column("score") == ["3.2", "1.1"]


def test_reads_semicolon_and_cp949(tmp_path):
    path = write(tmp_path, "b.csv", "대상자;점수;진단\nS1;22;양성\nS2;28;음성\n",
                 encoding="cp949")
    t = read_table(path)
    assert t.headers == ["대상자", "점수", "진단"]
    assert t.delimiter == ";"
    assert t.column("진단") == ["양성", "음성"]


def test_reads_tab_separated(tmp_path):
    path = write(tmp_path, "c.tsv", "id\tscore\tdx\n1\t3.2\tYes\n2\t1.1\tNo\n")
    t = read_table(path)
    assert t.delimiter == "\t"
    assert t.headers == ["id", "score", "dx"]


def test_blank_and_ragged_rows_are_handled_and_reported(tmp_path):
    path = write(tmp_path, "d.csv", "id,score,dx\n1,3.2,Yes\n\n2,1.1\n3,2.0,No,extra\n")
    t = read_table(path)
    assert len(t.rows) == 3          # the blank line is dropped
    assert all(len(r) == 3 for r in t.rows)
    assert t.notes and "ragged" in t.notes[0]


def test_duplicate_headers_are_disambiguated(tmp_path):
    path = write(tmp_path, "e.csv", "id,score,score\n1,1,2\n")
    t = read_table(path)
    assert t.headers == ["id", "score", "score.1"]


def test_empty_file_raises(tmp_path):
    path = write(tmp_path, "f.csv", "   \n")
    with pytest.raises(LoadError):
        read_table(path)


def test_missing_file_raises():
    with pytest.raises(LoadError):
        read_table("/nonexistent/definitely-not-here.csv")


def test_resolve_column_by_name_case_and_index(tmp_path):
    path = write(tmp_path, "g.csv", "Patient ID,CRP_mg_L,Dx\n1,3.2,Yes\n")
    t = read_table(path)
    assert resolve_column(t, "CRP_mg_L") == "CRP_mg_L"
    assert resolve_column(t, "crp mg l") == "CRP_mg_L"
    assert resolve_column(t, "#2") == "CRP_mg_L"
    with pytest.raises(LoadError) as exc:
        resolve_column(t, "nope")
    assert "CRP_mg_L" in str(exc.value)   # error lists the real columns
    with pytest.raises(LoadError):
        resolve_column(t, "#9")


def test_forced_delimiter_and_encoding_are_respected(tmp_path):
    path = write(tmp_path, "h.csv", "id|score|dx\n1|3.2|Yes\n")
    t = read_table(path, encoding="utf-8", delimiter="|")
    assert t.headers == ["id", "score", "dx"]
    assert t.encoding == "utf-8"
