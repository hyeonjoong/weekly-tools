"""CSV 읽기 — 인코딩·구분자·깨진 행."""

from __future__ import annotations

from deidaudit.tabular import decode_bytes, load_csv, norm_key, sniff_delimiter


def test_utf8_bom_is_stripped(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes("이름,값\n김현중,1\n".encode("utf-8-sig"))
    table = load_csv(path).tables[0]
    assert table.columns == ["이름", "값"]


def test_cp949_is_read(tmp_path):
    path = tmp_path / "b.csv"
    path.write_bytes("이름,값\n김현중,1\n".encode("cp949"))
    table = load_csv(path).tables[0]
    assert table.columns == ["이름", "값"]
    assert table.rows == [["김현중", "1"]]
    assert table.encoding in ("cp949", "euc-kr")


def test_utf16_is_read(tmp_path):
    path = tmp_path / "c.csv"
    path.write_bytes("이름,값\n김현중,1\n".encode("utf-16"))
    table = load_csv(path).tables[0]
    assert table.rows == [["김현중", "1"]]


def test_undecodable_file_is_reported(tmp_path):
    path = tmp_path / "d.csv"
    path.write_bytes(bytes([0x80, 0x81, 0x82, 0xFE, 0xFD]) * 7)
    result = load_csv(path)
    assert result.fatal


def test_tab_and_semicolon_delimiters():
    assert sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a,b,c\n1,2,3\n") == ","


def test_tsv_extension_forces_tab():
    assert sniff_delimiter("a,b\n1,2\n", ".tsv") == "\t"


def test_ragged_rows_are_padded_and_confessed(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("a,b,c\n1,2\n3,4,5,6\n", encoding="utf-8")
    table = load_csv(path).tables[0]
    assert table.rows == [["1", "2", ""], ["3", "4", "5"]]
    assert any("열 수가 헤더와 다른" in note for note in table.notes)


def test_duplicate_headers_get_distinct_names(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("id,id,id\n1,2,3\n", encoding="utf-8")
    table = load_csv(path).tables[0]
    assert len(set(table.columns)) == 3


def test_empty_header_gets_placeholder(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("a,,c\n1,2,3\n", encoding="utf-8")
    table = load_csv(path).tables[0]
    assert table.columns[1] == "열2"


def test_empty_file_is_reported(tmp_path):
    path = tmp_path / "h.csv"
    path.write_text("   \n", encoding="utf-8")
    assert load_csv(path).fatal


def test_header_only_file_is_reported(tmp_path):
    path = tmp_path / "i.csv"
    path.write_text("a,b\n", encoding="utf-8")
    result = load_csv(path)
    assert result.tables[0].rows == []


def test_oversize_file_is_skipped(tmp_path):
    path = tmp_path / "j.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    result = load_csv(path, max_bytes=3)
    assert result.fatal and "너무 큼" in result.fatal


def test_nul_bytes_do_not_crash(tmp_path):
    path = tmp_path / "k.csv"
    path.write_bytes("a,b\n1,\x002\n".encode("utf-8"))
    table = load_csv(path).tables[0]
    assert table.rows == [["1", "2"]]


def test_quoted_newlines_are_preserved(tmp_path):
    path = tmp_path / "l.csv"
    path.write_text('a,b\n1,"두 줄\n짜리 메모"\n', encoding="utf-8")
    table = load_csv(path).tables[0]
    assert table.rows == [["1", "두 줄\n짜리 메모"]]


def test_norm_key_ignores_case_space_and_unicode_form():
    import unicodedata

    assert norm_key(" Subject_ID ") == norm_key("subject_id")
    assert norm_key(unicodedata.normalize("NFD", "성별")) == norm_key("성별")


def test_column_index_uses_normalized_matching(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("Subject ID,값\nS01,1\n", encoding="utf-8")
    table = load_csv(path).tables[0]
    assert table.column_index("subjectid") == 0


def test_decode_bytes_returns_encoding():
    text, encoding = decode_bytes("가나다".encode("utf-8"))
    assert text == "가나다" and encoding == "utf-8"
