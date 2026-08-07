"""엑셀 리더 — 여기서 한 칸 밀리면 남의 값이 그 사람 것이 된다."""

from __future__ import annotations

import datetime as _dt
import zipfile

import pytest

from conftest import write_bytes, write_xlsx
from joinaudit.xlsxread import (XlsxError, looks_like_legacy_xls,
                                looks_like_xlsx, read_sheet, serial_to_iso,
                                sheet_names)

_MINIMAL_WB = (
    '<?xml version="1.0"?><workbook '
    'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="S1" sheetId="1" r:id="rId1"/></sheets></workbook>')
_WB_RELS = (
    '<?xml version="1.0"?><Relationships '
    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/></Relationships>')


def _handmade(path, sheet_xml, extra=None):
    """시트 XML 을 직접 지정해 만든 최소 워크북."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", _MINIMAL_WB)
        zf.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return str(path)


# --------------------------------------------------------------------------
# 시리얼 ↔ 날짜
# --------------------------------------------------------------------------

def test_serial_to_iso_known_values():
    assert serial_to_iso(46091) == "2026-03-10"
    assert serial_to_iso(1) == "1900-01-01"
    # 엑셀이 일부러 남겨 둔 존재하지 않는 날짜.
    assert serial_to_iso(60) == "1900-02-29"
    assert serial_to_iso(61) == "1900-03-01"


def test_serial_to_iso_1904_epoch():
    assert serial_to_iso(0, date1904=True) == "1904-01-01"


def test_serial_with_time_is_rounded_not_truncated():
    """23:40 이 23:39:59.999999 로 저장되면 자정 근처에서 하루가 밀린다."""
    serial = 46091 + (23 * 3600 + 40 * 60) / 86400.0
    assert serial_to_iso(serial) == "2026-03-10 23:40:00"


def test_serial_rejects_non_finite():
    assert serial_to_iso(float("inf")) is None
    assert serial_to_iso(float("nan")) is None


# --------------------------------------------------------------------------
# 컨테이너 판별
# --------------------------------------------------------------------------

def test_looks_like_xlsx_checks_the_container_not_the_extension(tmp_path):
    fake = write_bytes(str(tmp_path / "fake.xlsx"), b"id,v\nS01,1\n")
    assert looks_like_xlsx(fake) is False
    real = str(tmp_path / "real.xlsx")
    write_xlsx(real, [["id"], ["S01"]])
    assert looks_like_xlsx(real) is True


def test_legacy_xls_is_recognised(tmp_path):
    path = write_bytes(str(tmp_path / "a.xls"),
                       b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    assert looks_like_legacy_xls(path) is True


def test_a_zip_that_is_not_a_workbook_is_rejected(tmp_path):
    path = str(tmp_path / "a.xlsx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a workbook")
    with pytest.raises(XlsxError):
        read_sheet(path)


# --------------------------------------------------------------------------
# 셀 읽기
# --------------------------------------------------------------------------

def test_rich_text_runs_are_concatenated(tmp_path):
    """셀 일부만 굵게 칠하면 텍스트가 여러 런으로 쪼개진다."""
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>'
             '</worksheet>')
    strings = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               "<si><r><t>BELL-</t></r><r><t>001-</t></r><r><t>07</t></r></si></sst>")
    path = _handmade(tmp_path / "a.xlsx", sheet,
                     {"xl/sharedStrings.xml": strings})
    assert read_sheet(path) == [["BELL-001-07"]]


def test_sparse_cells_are_placed_by_reference_not_position(tmp_path):
    """빈 셀을 생략한 행에서 값이 왼쪽으로 밀리면 안 된다."""
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>'
             '<row r="1"><c r="A1" t="str"><v>id</v></c>'
             '<c r="B1" t="str"><v>a</v></c><c r="C1" t="str"><v>b</v></c></row>'
             '<row r="2"><c r="A2" t="str"><v>S01</v></c>'
             '<c r="C2" t="str"><v>세번째칸</v></c></row>'
             "</sheetData></worksheet>")
    path = _handmade(tmp_path / "a.xlsx", sheet)
    assert read_sheet(path) == [["id", "a", "b"], ["S01", "", "세번째칸"]]


def test_error_cells_are_kept_as_text(tmp_path):
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="e"><v>#N/A</v></c></row>'
             "</sheetData></worksheet>")
    path = _handmade(tmp_path / "a.xlsx", sheet)
    assert read_sheet(path) == [["#N/A"]]


def test_inline_strings(tmp_path):
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="inlineStr">'
             "<is><t>S01</t></is></c></row></sheetData></worksheet>")
    path = _handmade(tmp_path / "a.xlsx", sheet)
    assert read_sheet(path) == [["S01"]]


def test_numbers_lose_the_trailing_zero(tmp_path):
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["v"], [42.0], [42.5]])
    assert read_sheet(path) == [["v"], ["42"], ["42.5"]]


# --------------------------------------------------------------------------
# 시트 선택
# --------------------------------------------------------------------------

def test_sheet_names_and_selection(tmp_path):
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["id"], ["S01"]], sheet_name="3월측정")
    assert sheet_names(path) == ["3월측정"]
    assert read_sheet(path, "3월측정") == [["id"], ["S01"]]
    assert read_sheet(path, "1") == [["id"], ["S01"]]


def test_unknown_sheet_lists_the_available_ones(tmp_path):
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["id"], ["S01"]], sheet_name="3월측정")
    with pytest.raises(XlsxError) as exc:
        read_sheet(path, "없는시트")
    assert "3월측정" in str(exc.value)


def test_non_ascii_digit_sheet_name_does_not_crash(tmp_path):
    """`'²'.isdigit()` 은 True 라서 순진한 인덱스 처리는 int() 에서 터진다."""
    path = str(tmp_path / "a.xlsx")
    write_xlsx(path, [["id"], ["S01"]])
    with pytest.raises(XlsxError):
        read_sheet(path, "²")


# --------------------------------------------------------------------------
# 방어
# --------------------------------------------------------------------------

def test_zip_bomb_is_stopped_by_the_inflate_budget(tmp_path):
    path = str(tmp_path / "bomb.xlsx")
    payload = b"<x>" + b"A" * (300 * 1024 * 1024) + b"</x>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", _MINIMAL_WB)
        zf.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        zf.writestr("xl/worksheets/sheet1.xml", payload)
    with pytest.raises(XlsxError) as exc:
        read_sheet(path)
    assert "너무 커집니다" in str(exc.value)


def test_broken_internal_xml_is_reported_not_swallowed(tmp_path):
    path = _handmade(tmp_path / "a.xlsx", "<worksheet><sheetData><row>")
    with pytest.raises(XlsxError):
        read_sheet(path)
