"""Dependency-free .xlsx reading.

Workbooks are hand-built here with ``zipfile`` rather than openpyxl so the
suite needs no third-party package — and so we can craft the exact malformed /
adversarial shapes a real clinical export produces (sparse rows, rich text,
1904 epochs, bad zips) that a writer library would never emit.
"""

from __future__ import annotations

import zipfile

import pytest

from table1.build import Options, build_table1
from table1.dataio import load_frame
from table1.xlsx import _serial_to_iso, is_xlsx, load_xlsx_rows, sheet_names

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_CONTENT_TYPES = '<?xml version="1.0"?><Types/>'


def _workbook_xml(names, date1904=False):
    pr = f'<workbookPr date1904="{"1" if date1904 else "0"}"/>'
    sheets = "".join(
        f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, n in enumerate(names))
    return (f'<?xml version="1.0"?><workbook xmlns="{NS}" xmlns:r="{RNS}">'
            f'{pr}<sheets>{sheets}</sheets></workbook>')


def _rels_xml(count):
    rels = "".join(
        f'<Relationship Id="rId{i+1}" Target="worksheets/sheet{i+1}.xml" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/worksheet"/>' for i in range(count))
    return (f'<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            f'openxmlformats.org/package/2006/relationships">{rels}'
            f'</Relationships>')


def _sheet_xml(rows_xml):
    return (f'<?xml version="1.0"?><worksheet xmlns="{NS}"><sheetData>'
            f'{rows_xml}</sheetData></worksheet>')


def make_xlsx(tmp_path, sheets, shared=None, styles_xml=None, date1904=False,
              name="book.xlsx"):
    """sheets: [(sheet_name, rows_xml)]."""
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("xl/workbook.xml",
                   _workbook_xml([n for n, _ in sheets], date1904))
        z.writestr("xl/_rels/workbook.xml.rels", _rels_xml(len(sheets)))
        for i, (_, rows_xml) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", _sheet_xml(rows_xml))
        if shared is not None:
            si = "".join(f"<si><t>{s}</t></si>" for s in shared)
            z.writestr("xl/sharedStrings.xml",
                       f'<?xml version="1.0"?><sst xmlns="{NS}">{si}</sst>')
        if styles_xml is not None:
            z.writestr("xl/styles.xml", styles_xml)
    return str(p)


def _row(idx, cells):
    """cells: [(ref, xml)] -> a <row> element."""
    return f'<row r="{idx}">' + "".join(x for _, x in cells) + "</row>"


def _inline(ref, text):
    return (ref, f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')


def _num(ref, v, s=None):
    st = f' s="{s}"' if s is not None else ""
    return (ref, f'<c r="{ref}"{st}><v>{v}</v></c>')


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def test_is_xlsx_detects_container_not_extension(tmp_path):
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "x")]))],
                  name="actually_a_workbook.txt")
    assert is_xlsx(p) is True


def test_is_xlsx_false_for_csv_and_missing(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("a,b\n1,2\n")
    assert is_xlsx(str(csv)) is False
    assert is_xlsx(str(tmp_path / "nope.xlsx")) is False


def test_is_xlsx_false_for_zip_without_workbook(tmp_path):
    p = tmp_path / "z.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("hello.txt", "hi")
    assert is_xlsx(str(p)) is False


def test_corrupt_zip_gives_friendly_error(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"PK\x03\x04garbage-not-a-zip")
    # not detected as xlsx -> falls through to the CSV reader, which reports a
    # decoding problem rather than crashing
    assert is_xlsx(str(p)) is False
    with pytest.raises(ValueError):
        load_xlsx_rows(str(p))


# --------------------------------------------------------------------------- #
# cell types
# --------------------------------------------------------------------------- #
def test_reads_shared_inline_numeric_and_boolean(tmp_path):
    rows = (_row(1, [_inline("A1", "name"), _inline("B1", "n"),
                     _inline("C1", "flag")])
            + '<row r="2">'
              '<c r="A2" t="s"><v>0</v></c>'
              '<c r="B2"><v>42</v></c>'
              '<c r="C2" t="b"><v>1</v></c>'
              '</row>'
            + '<row r="3">'
              '<c r="A3" t="s"><v>1</v></c>'
              '<c r="B3"><v>3.5</v></c>'
              '<c r="C3" t="b"><v>0</v></c>'
              '</row>')
    p = make_xlsx(tmp_path, [("S", rows)], shared=["alpha", "beta"])
    assert load_xlsx_rows(p) == [["name", "n", "flag"],
                                 ["alpha", "42", "TRUE"],
                                 ["beta", "3.5", "FALSE"]]


def test_integer_valued_floats_render_without_trailing_zero(tmp_path):
    rows = _row(1, [_inline("A1", "v")]) + _row(2, [_num("A2", "42.00000")])
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p)[1] == ["42"]


def test_rich_text_runs_are_concatenated(tmp_path):
    p = tmp_path / "rt.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("xl/workbook.xml", _workbook_xml(["S"]))
        z.writestr("xl/_rels/workbook.xml.rels", _rels_xml(1))
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(
            _row(1, [_inline("A1", "h")])
            + '<row r="2"><c r="A2" t="s"><v>0</v></c></row>'))
        z.writestr("xl/sharedStrings.xml",
                   f'<?xml version="1.0"?><sst xmlns="{NS}"><si>'
                   f'<r><t>Hyper</t></r><r><t>tension</t></r></si></sst>')
    assert load_xlsx_rows(str(p))[1] == ["Hypertension"]


def test_cached_formula_result_is_read(tmp_path):
    rows = (_row(1, [_inline("A1", "v")])
            + '<row r="2"><c r="A2" t="str"><f>CONCAT("a")</f><v>a</v></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p)[1] == ["a"]


def test_out_of_range_shared_string_index_is_blank_not_crash(tmp_path):
    rows = (_row(1, [_inline("A1", "h")])
            + '<row r="2"><c r="A2" t="s"><v>99</v></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)], shared=["only"])
    assert load_xlsx_rows(p)[1] == [""]


# --------------------------------------------------------------------------- #
# sparse rows — the corruption-class bug
# --------------------------------------------------------------------------- #
def test_omitted_cells_do_not_shift_columns(tmp_path):
    """Excel omits empty cells entirely. Placing by position instead of by the
    r= reference would silently move every later value one column left."""
    rows = (_row(1, [_inline("A1", "a"), _inline("B1", "b"),
                     _inline("C1", "c"), _inline("D1", "d")])
            # only A and D present: B/C omitted
            + '<row r="2"><c r="A2"><v>1</v></c><c r="D2"><v>4</v></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p) == [["a", "b", "c", "d"], ["1", "", "", "4"]]


def test_multi_letter_column_references(tmp_path):
    rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>first</t></is></c>'
            '<c r="AA1" t="inlineStr"><is><t>col27</t></is></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)])
    out = load_xlsx_rows(p)
    assert len(out[0]) == 27
    assert out[0][0] == "first" and out[0][26] == "col27"


def test_rows_are_padded_to_a_rectangle(tmp_path):
    rows = (_row(1, [_inline("A1", "a"), _inline("B1", "b")])
            + _row(2, [_inline("A2", "1")]))          # short trailing row
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p) == [["a", "b"], ["1", ""]]


def test_cells_without_r_attribute_fall_back_to_position(tmp_path):
    rows = ('<row r="1"><c t="inlineStr"><is><t>a</t></is></c>'
            '<c t="inlineStr"><is><t>b</t></is></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p) == [["a", "b"]]


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
_STYLES = (f'<?xml version="1.0"?><styleSheet xmlns="{NS}">'
           '<numFmts count="1">'
           '<numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/>'
           '</numFmts>'
           '<cellXfs count="3">'
           '<xf numFmtId="0"/>'          # s=0 general
           '<xf numFmtId="14"/>'         # s=1 builtin date
           '<xf numFmtId="164"/>'        # s=2 custom date
           '</cellXfs></styleSheet>')


def test_date_serials_become_iso_dates(tmp_path):
    rows = (_row(1, [_inline("A1", "d"), _inline("B1", "c"), _inline("C1", "n")])
            # 45366 = 2024-03-15, builtin fmt 14 / custom 164 / no date style
            + _row(2, [_num("A2", "45366", s=1), _num("B2", "45366", s=2),
                       _num("C2", "45366", s=0)]))
    p = make_xlsx(tmp_path, [("S", rows)], styles_xml=_STYLES)
    assert load_xlsx_rows(p)[1] == ["2024-03-15", "2024-03-15", "45366"]


def test_1904_epoch_is_honoured(tmp_path):
    rows = (_row(1, [_inline("A1", "d")]) + _row(2, [_num("A2", "0", s=1)]))
    p = make_xlsx(tmp_path, [("S", rows)], styles_xml=_STYLES, date1904=True)
    assert load_xlsx_rows(p)[1] == ["1904-01-01"]


def test_excel_1900_leap_year_bug_is_reproduced():
    # Excel believes 1900-02-29 exists (serial 60). Serial 61 is 1900-03-01.
    assert _serial_to_iso(60, False) == "1900-02-29"
    assert _serial_to_iso(61, False) == "1900-03-01"
    assert _serial_to_iso(59, False) == "1900-02-28"
    assert _serial_to_iso(1, False) == "1900-01-01"


def test_datetime_serial_keeps_the_time(tmp_path):
    rows = (_row(1, [_inline("A1", "d")]) + _row(2, [_num("A2", "45366.5", s=1)]))
    p = make_xlsx(tmp_path, [("S", rows)], styles_xml=_STYLES)
    assert load_xlsx_rows(p)[1] == ["2024-03-15 12:00:00"]


def test_currency_format_is_not_mistaken_for_a_date(tmp_path):
    """A format code's 'm' inside a quoted literal must not make it a date."""
    styles = (f'<?xml version="1.0"?><styleSheet xmlns="{NS}">'
              '<numFmts count="1">'
              '<numFmt numFmtId="164" formatCode="#,##0&quot;m&quot;"/>'
              '</numFmts>'
              '<cellXfs count="1"><xf numFmtId="164"/></cellXfs></styleSheet>')
    rows = (_row(1, [_inline("A1", "v")]) + _row(2, [_num("A2", "45366", s=0)]))
    p = make_xlsx(tmp_path, [("S", rows)], styles_xml=styles)
    assert load_xlsx_rows(p)[1] == ["45366"]


def test_out_of_range_date_serial_falls_back_to_the_number(tmp_path):
    rows = (_row(1, [_inline("A1", "d")]) + _row(2, [_num("A2", "1e30", s=1)]))
    p = make_xlsx(tmp_path, [("S", rows)], styles_xml=_STYLES)
    assert load_xlsx_rows(p)[1] == ["1e+30"]


# --------------------------------------------------------------------------- #
# sheet selection
# --------------------------------------------------------------------------- #
def test_sheet_names_and_selection(tmp_path):
    p = make_xlsx(tmp_path, [
        ("기저", _row(1, [_inline("A1", "one")])),
        ("추적", _row(1, [_inline("A1", "two")])),
    ])
    assert sheet_names(p) == ["기저", "추적"]
    assert load_xlsx_rows(p) == [["one"]]              # default: first
    assert load_xlsx_rows(p, "추적") == [["two"]]       # by name
    assert load_xlsx_rows(p, "2") == [["two"]]         # by 1-based index


def test_unknown_sheet_lists_the_available_ones(tmp_path):
    p = make_xlsx(tmp_path, [("기저", _row(1, [_inline("A1", "x")]))])
    with pytest.raises(ValueError, match="기저"):
        load_xlsx_rows(p, "없는시트")


def test_out_of_range_sheet_index_errors(tmp_path):
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "x")]))])
    with pytest.raises(ValueError):
        load_xlsx_rows(p, "7")


def test_numeric_sheet_name_wins_over_index(tmp_path):
    """A sheet literally named '2' must be matched by name, not read as an
    index into the sheet list."""
    p = make_xlsx(tmp_path, [("first", _row(1, [_inline("A1", "a")])),
                             ("2", _row(1, [_inline("A1", "b")]))])
    assert load_xlsx_rows(p, "2") == [["b"]]   # same answer either way here
    p2 = make_xlsx(tmp_path, [("2", _row(1, [_inline("A1", "named")])),
                              ("other", _row(1, [_inline("A1", "idx")]))],
                   name="b2.xlsx")
    assert load_xlsx_rows(p2, "2") == [["named"]]


# --------------------------------------------------------------------------- #
# integration through load_frame / build_table1
# --------------------------------------------------------------------------- #
def _clinical_book(tmp_path):
    header = _row(1, [_inline("A1", "arm"), _inline("B1", "age"),
                      _inline("C1", "sex")])
    body = ""
    for i, (arm, age, sex) in enumerate(
            [("device", 54, "M"), ("sham", 61, "F"), ("device", 47, "F"),
             ("sham", 59, "M"), ("device", 63, "M"), ("sham", 44, "F")], start=2):
        body += _row(i, [_inline(f"A{i}", arm), _num(f"B{i}", age),
                         _inline(f"C{i}", sex)])
    return make_xlsx(tmp_path, [("Sheet1", header + body)])


def test_load_frame_reads_xlsx(tmp_path):
    f = load_frame(_clinical_book(tmp_path))
    assert f.header == ["arm", "age", "sex"]
    assert f.nrows == 6
    assert f.column("age") == ["54", "61", "47", "59", "63", "44"]


def test_build_table1_from_xlsx(tmp_path):
    f = load_frame(_clinical_book(tmp_path))
    t = build_table1(f, Options(group_col="arm"))
    assert t.groups == ["device", "sham"]
    age = [r for r in t.rows if r.name == "age"][0]
    assert age.per_group[0].mean == pytest.approx((54 + 47 + 63) / 3)


def test_xlsx_header_rules_match_csv(tmp_path):
    """Duplicate headers must be rejected identically to the CSV path."""
    rows = (_row(1, [_inline("A1", "age"), _inline("B1", "age")])
            + _row(2, [_num("A2", "1"), _num("B2", "2")]))
    p = make_xlsx(tmp_path, [("S", rows)])
    with pytest.raises(ValueError, match="중복"):
        load_frame(p)


def test_xlsx_with_only_a_header_errors(tmp_path):
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "age")]))])
    with pytest.raises(ValueError):
        load_frame(p)


def test_empty_sheet_errors(tmp_path):
    p = make_xlsx(tmp_path, [("S", "")])
    with pytest.raises(ValueError):
        load_frame(p)


def test_delimiter_rejected_for_xlsx(tmp_path):
    with pytest.raises(ValueError, match="CSV"):
        load_frame(_clinical_book(tmp_path), delimiter=",")


def test_sheet_rejected_for_csv(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("arm,age\ndevice,1\nsham,2\n")
    with pytest.raises(ValueError, match="엑셀"):
        load_frame(str(csv), sheet="Sheet1")


def test_blank_rows_are_skipped(tmp_path):
    rows = (_row(1, [_inline("A1", "arm"), _inline("B1", "age")])
            + '<row r="2"/>'
            + _row(3, [_inline("A3", "device"), _num("B3", "50")])
            + _row(4, [_inline("A4", "sham"), _num("B4", "60")]))
    p = make_xlsx(tmp_path, [("S", rows)])
    f = load_frame(p)
    assert f.nrows == 2


# --------------------------------------------------------------------------- #
# resource / robustness bounds
# --------------------------------------------------------------------------- #
def test_zip_bomb_is_rejected(tmp_path):
    """The decompression budget counts ACTUAL bytes, not the header's declared
    size. Without a test, this defense is one careless refactor from silently
    disappearing (and it is invisible in normal use)."""
    p = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("xl/workbook.xml", _workbook_xml(["S"]))
        z.writestr("xl/_rels/workbook.xml.rels", _rels_xml(1))
        # ~600MB of zeros compresses to a few hundred KB.
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (600 * 1024 * 1024))
    assert p.stat().st_size < 2 * 1024 * 1024      # tiny on disk
    with pytest.raises(ValueError, match="너무 커집니다"):
        load_xlsx_rows(str(p))


def test_unsupported_compression_is_friendly_not_a_traceback(tmp_path):
    """zipfile raises NotImplementedError for a method it cannot decode
    (AES/99, implode). is_xlsx passes because namelist() never decompresses."""
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "a")]))])
    raw = bytearray(p_bytes := open(p, "rb").read())
    # Patch every compression-method field to 99 (AES) — local headers and the
    # central directory.
    for sig, off in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        i = 0
        while (i := raw.find(sig, i)) != -1:
            raw[i + off:i + off + 2] = (99).to_bytes(2, "little")
            i += 4
    p2 = tmp_path / "aes.xlsx"
    p2.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="압축 방식"):
        load_xlsx_rows(str(p2))


def test_encrypted_entry_is_friendly_not_a_traceback(tmp_path):
    """zipfile raises RuntimeError for a password-flagged entry."""
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "a")]))])
    raw = bytearray(open(p, "rb").read())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = 0
        while (i := raw.find(sig, i)) != -1:
            flags = int.from_bytes(raw[i + off:i + off + 2], "little") | 1
            raw[i + off:i + off + 2] = flags.to_bytes(2, "little")
            i += 4
    p2 = tmp_path / "enc.xlsx"
    p2.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="암호"):
        load_xlsx_rows(str(p2))


def test_non_finite_numeric_cell_does_not_crash(tmp_path):
    """int() raises OverflowError on inf and ValueError on nan; both must be
    passed through as text (parse_float later treats them as missing)."""
    rows = (_row(1, [_inline("A1", "v")])
            + _row(2, [_num("A2", "1e999")]) + _row(3, [_num("A3", "NaN")])
            + _row(4, [_num("A4", "-inf")]))
    p = make_xlsx(tmp_path, [("S", rows)])
    out = load_xlsx_rows(p)
    assert out[1] == ["1e999"] and out[2] == ["NaN"] and out[3] == ["-inf"]


def test_error_cells_are_read_as_their_text(tmp_path):
    rows = (_row(1, [_inline("A1", "v")])
            + '<row r="2"><c r="A2" t="e"><v>#DIV/0!</v></c></row>')
    p = make_xlsx(tmp_path, [("S", rows)])
    assert load_xlsx_rows(p)[1] == ["#DIV/0!"]


def test_missing_workbook_rels_yields_no_sheets(tmp_path):
    """Without rels nothing binds a sheet to its XML part."""
    p = tmp_path / "norels.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("xl/workbook.xml", _workbook_xml(["S"]))
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(""))
    with pytest.raises(ValueError, match="워크시트가 없습니다"):
        load_xlsx_rows(str(p))


def test_legacy_xls_is_detected_and_gets_actionable_advice(tmp_path):
    """A real OLE2 .xls is not a zip, so without this it would fall to the CSV
    reader and be blamed on the encoding — advice that cannot possibly work."""
    from table1.dataio import load_frame
    from table1.xlsx import is_legacy_xls
    p = tmp_path / "old.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    assert is_legacy_xls(str(p)) is True
    assert is_legacy_xls(str(tmp_path)) is False
    with pytest.raises(ValueError, match=r"구형 엑셀"):
        load_frame(str(p))


def test_csv_misnamed_as_xlsx_still_reads_as_csv(tmp_path):
    """Detection is by container, so a CSV called .xlsx is simply read as CSV."""
    from table1.dataio import load_frame
    p = tmp_path / "fake.xlsx"
    p.write_text("arm,age\ndevice,50\nsham,60\n", encoding="utf-8")
    f = load_frame(str(p))
    assert f.header == ["arm", "age"]


def test_sheet_index_accepts_only_ascii_digits(tmp_path):
    """'²'.isdigit() is True but int('²') raises — that ValueError text must
    never reach the user."""
    p = make_xlsx(tmp_path, [("S", _row(1, [_inline("A1", "a")]))])
    for bogus in ("²", "++2", "٣"):
        with pytest.raises(ValueError, match="시트"):
            load_xlsx_rows(p, bogus)
