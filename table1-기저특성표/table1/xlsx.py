"""Minimal, dependency-free reader for Excel .xlsx workbooks.

Clinical data almost never arrives as a tidy CSV — it arrives as an Excel
workbook, and "re-save it as CSV UTF-8 first" is exactly the manual step this
tool exists to remove. This module reads a worksheet into the same
list-of-string-rows shape ``dataio.load_frame`` already works with, using only
the standard library (``zipfile`` + ``xml.etree``), so table1 keeps its zero
third-party dependencies.

What it handles
---------------
- shared strings (including rich-text runs, whose fragments are concatenated),
  inline strings, cached formula results, booleans and error cells;
- **sparse rows** — Excel omits empty cells entirely, so cells are placed by
  their ``r="C7"`` reference rather than by position (getting this wrong
  silently shifts every value after a blank cell into the wrong column, which
  would corrupt a whole table);
- **dates** — a serial number under a date-formatted style becomes an ISO
  ``YYYY-MM-DD`` string, honouring both the 1900 and 1904 epochs and Excel's
  fictitious 1900-02-29;
- selecting a sheet by name or by 1-based index.

Numbers are emitted with ``repr``-grade round-tripping precision and trailing
``.0`` stripped, so an integer-valued cell reads as ``42`` rather than
``42.0`` and a categorical code keeps the label the researcher expects.

Safety
------
.xlsx is a zip archive, so it is a natural zip-bomb vector. Entries are read
through a bounded streaming reader that aborts past ``_MAX_UNCOMPRESSED``
rather than trusting the header's declared size, and the sheet/row/column
counts are capped. All parsing is offline; nothing is executed or fetched
(external entities are not resolved by ``xml.etree``'s parser).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional

__all__ = ["is_xlsx", "is_legacy_xls", "load_xlsx_rows", "sheet_names"]

# Bound the total bytes we will decompress from any single archive entry, and
# the whole workbook. A legitimate Table-1 workbook is far below this; a zip
# bomb is far above it.
_MAX_UNCOMPRESSED = 512 * 1024 * 1024
_MAX_ROWS = 1_048_576      # Excel's own row ceiling
_MAX_COLS = 16_384         # Excel's own column ceiling (XFD)

_CELL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
# A --sheet value that is a 1-based index (ASCII digits only, see load_xlsx_rows).
_INDEX_RE = re.compile(r"^[0-9]+$")

# Built-in numFmtId values that denote a date and/or time (ECMA-376 18.8.30).
_BUILTIN_DATE_FMTS = set(range(14, 23)) | set(range(45, 48))
# A custom format is a date format if it uses date/time tokens outside of
# quoted literals, bracketed colour/condition blocks, or escapes.
_FMT_STRIP_RE = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')
_FMT_DATE_TOKEN_RE = re.compile(r"[ymdhs]", re.IGNORECASE)


def _local(tag: str) -> str:
    """Strip an XML namespace: '{ns}row' -> 'row'."""
    return tag.rsplit("}", 1)[-1]


def is_xlsx(path: str) -> bool:
    """True if the path looks like a readable .xlsx/.xlsm workbook.

    Sniffs the actual container rather than trusting the extension: a
    mis-named file should get the honest error, and a genuine workbook saved as
    ``data.txt`` should still work.
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"PK":
                return False
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        return "xl/workbook.xml" in names
    except (OSError, zipfile.BadZipFile):
        return False


def is_legacy_xls(path: str) -> bool:
    """True if the file is a legacy OLE2 (BIFF) .xls workbook.

    A real .xls is not a zip, so ``is_xlsx`` rejects it and the CSV reader then
    blames the encoding — advice that cannot work. Detect the OLE2 compound-file
    magic so the researcher gets the one instruction that will: re-save it.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    except OSError:
        return False


def _read_bounded(zf: zipfile.ZipFile, name: str, budget: List[int]) -> bytes:
    """Read one archive entry, aborting if it blows the decompression budget.

    Streams and counts actual decompressed bytes instead of trusting
    ``ZipInfo.file_size`` (which a hostile archive controls).
    """
    chunks: List[bytes] = []
    try:
        with zf.open(name) as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                budget[0] -= len(chunk)
                if budget[0] < 0:
                    raise ValueError(
                        "엑셀 파일의 압축을 풀면 너무 커집니다"
                        f"(> {_MAX_UNCOMPRESSED // (1024 * 1024)}MB). "
                        "손상되었거나 비정상적인 파일일 수 있습니다.")
                chunks.append(chunk)
    except NotImplementedError:
        # zipfile refuses a compression method it cannot decode (AES/method 99
        # from a "protected" export, implode, or bzip2/lzma on a stripped
        # build). Never let this reach the researcher as a traceback.
        raise ValueError(
            "이 엑셀 파일은 지원하지 않는 압축 방식으로 저장되었습니다"
            "(암호화되었거나 특수 도구로 만든 파일일 수 있습니다). "
            "엑셀에서 다시 '.xlsx' 또는 'CSV UTF-8'로 저장해 주세요.")
    except RuntimeError as exc:
        # zipfile raises RuntimeError for an encrypted (password-protected)
        # entry. Its message names the internal part, which means nothing to a
        # researcher — replace it with actionable advice.
        if "encrypted" in str(exc).lower() or "password" in str(exc).lower():
            raise ValueError(
                "암호로 보호된 엑셀 파일은 읽을 수 없습니다. 엑셀에서 암호를 "
                "해제하고 저장한 뒤 다시 실행하세요.")
        raise ValueError(f"엑셀 파일을 읽을 수 없습니다: {exc}")
    except (zipfile.BadZipFile, EOFError) as exc:
        raise ValueError(f"엑셀 파일이 손상되었습니다: {exc}")
    return b"".join(chunks)


def _parse_xml(data: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"엑셀 내부 XML({what})을 해석할 수 없습니다: {exc}")


def _col_index(ref: str) -> Optional[int]:
    """'C' -> 2 (0-based). None if the reference is not parseable."""
    m = _CELL_RE.match(ref.strip())
    if not m:
        return None
    letters = m.group(1).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _sheet_targets(zf: zipfile.ZipFile, budget: List[int]):
    """Return [(sheet_name, zip_path)] in workbook order, plus date1904."""
    wb = _parse_xml(_read_bounded(zf, "xl/workbook.xml", budget), "workbook")

    date1904 = False
    for pr in wb:
        if _local(pr.tag) == "workbookPr":
            v = pr.get("date1904") or pr.get("date1904Compat") or "0"
            date1904 = str(v).lower() in ("1", "true")

    # rId -> target path (relationships are what actually bind a sheet to its
    # XML part; sheet order in workbook.xml is the user-visible tab order).
    rels: Dict[str, str] = {}
    try:
        rel_root = _parse_xml(
            _read_bounded(zf, "xl/_rels/workbook.xml.rels", budget), "rels")
        for rel in rel_root:
            rid = rel.get("Id")
            target = rel.get("Target") or ""
            if not rid or not target:
                continue
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rels[rid] = target.replace("\\", "/")
    except (KeyError, ValueError):
        rels = {}

    out = []
    names = set(zf.namelist())
    for sheets in wb:
        if _local(sheets.tag) != "sheets":
            continue
        for sh in sheets:
            if _local(sh.tag) != "sheet":
                continue
            name = sh.get("name") or ""
            rid = None
            for k, v in sh.attrib.items():
                if _local(k) == "id":
                    rid = v
            target = rels.get(rid or "")
            if target and target in names:
                out.append((name, target))
    return out, date1904


def sheet_names(path: str) -> List[str]:
    """Worksheet names in tab order (used for a helpful error message)."""
    budget = [_MAX_UNCOMPRESSED]
    with zipfile.ZipFile(path) as zf:
        targets, _ = _sheet_targets(zf, budget)
    return [n for n, _ in targets]


def _shared_strings(zf: zipfile.ZipFile, budget: List[int]) -> List[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = _parse_xml(_read_bounded(zf, "xl/sharedStrings.xml", budget),
                      "sharedStrings")
    out: List[str] = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        # A shared string is either a single <t>, or rich text: several <r>
        # runs each holding a <t>. Concatenate every <t> in document order so
        # a partially-bolded cell keeps its full text.
        parts = [(node.text or "") for node in si.iter()
                 if _local(node.tag) == "t"]
        out.append("".join(parts))
    return out


def _is_date_format(code: str) -> bool:
    stripped = _FMT_STRIP_RE.sub("", code or "")
    # 'General' and pure-numeric codes have no date tokens; the 'm' in a
    # currency literal is already removed by the quote/bracket strip above.
    return bool(_FMT_DATE_TOKEN_RE.search(stripped))


def _date_styles(zf: zipfile.ZipFile, budget: List[int]) -> Dict[int, bool]:
    """style index -> True if that style formats its number as a date/time."""
    if "xl/styles.xml" not in zf.namelist():
        return {}
    root = _parse_xml(_read_bounded(zf, "xl/styles.xml", budget), "styles")
    custom: Dict[int, bool] = {}
    for numfmts in root:
        if _local(numfmts.tag) != "numFmts":
            continue
        for nf in numfmts:
            if _local(nf.tag) != "numFmt":
                continue
            try:
                fid = int(nf.get("numFmtId", "-1"))
            except ValueError:
                continue
            custom[fid] = _is_date_format(nf.get("formatCode", ""))

    styles: Dict[int, bool] = {}
    for cellxfs in root:
        if _local(cellxfs.tag) != "cellXfs":
            continue
        for i, xf in enumerate(cellxfs):
            if _local(xf.tag) != "xf":
                continue
            try:
                fid = int(xf.get("numFmtId", "0"))
            except ValueError:
                fid = 0
            styles[i] = custom.get(fid, fid in _BUILTIN_DATE_FMTS)
    return styles


def _serial_to_iso(serial: float, date1904: bool) -> Optional[str]:
    """Excel date serial -> 'YYYY-MM-DD' (or ISO datetime when it has a time).

    Handles Excel's deliberate 1900 leap-year bug: in the 1900 system serial 60
    is the non-existent 1900-02-29, and every serial above it is shifted by one
    day relative to a true calendar count.
    """
    if serial != serial or serial in (float("inf"), float("-inf")):
        return None
    if date1904:
        epoch = _dt.datetime(1904, 1, 1)
        days = serial
    else:
        epoch = _dt.datetime(1899, 12, 31)
        if serial >= 61:
            days = serial - 1     # skip the fictitious 1900-02-29
        elif serial == 60:
            return "1900-02-29"   # preserve the value Excel itself displays
        else:
            days = serial
    try:
        dt = epoch + _dt.timedelta(days=days)
    except (OverflowError, ValueError):
        return None
    # An Excel date serial is a float, so a whole-minute timestamp is typically
    # stored as e.g. 45657.57291666666 -> 13:44:59.999999. Excel itself
    # DISPLAYS the rounded second, so truncating (what isoformat(timespec=...)
    # does) would report 13:44:59 for a 13:45:00 appointment and could even
    # split one instant into two distinct category levels. Round to the nearest
    # second before deciding whether a time component exists at all.
    if dt.microsecond:
        try:
            dt = (dt + _dt.timedelta(microseconds=500000)).replace(microsecond=0)
        except (OverflowError, ValueError):
            return None
    if dt.hour or dt.minute or dt.second:
        return dt.isoformat(sep=" ", timespec="seconds")
    return dt.strftime("%Y-%m-%d")


def _fmt_number(text: str) -> str:
    """Render a numeric cell without gratuitous float noise ('42.0' -> '42').

    Non-finite values (inf/-inf/nan, or an overflowing literal like 1e999) are
    passed through as their literal text: ``int()`` would raise on them, and
    downstream ``parse_float`` already treats a non-finite number as missing.
    """
    try:
        v = float(text)
    except ValueError:
        return text
    if not math.isfinite(v):
        return text
    if v == int(v) and abs(v) < 1e16:
        return str(int(v))
    return repr(v)


def load_xlsx_rows(path: str, sheet: Optional[str] = None) -> List[List[str]]:
    """Read one worksheet into rows of strings (blank cells -> '').

    ``sheet`` selects a worksheet by name, or by 1-based index when given as a
    plain integer string ("2"); the default is the first worksheet.
    """
    budget = [_MAX_UNCOMPRESSED]
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError(
            f"'{path}' 은(는) 올바른 엑셀(.xlsx) 파일이 아닙니다. "
            "구형 .xls 라면 엑셀에서 .xlsx 또는 'CSV UTF-8'로 저장하세요.")
    with zf:
        targets, date1904 = _sheet_targets(zf, budget)
        if not targets:
            raise ValueError(f"'{path}' 에 워크시트가 없습니다.")

        target = None
        if sheet is None:
            target = targets[0][1]
        else:
            for name, tgt in targets:
                if name == sheet:
                    target = tgt
                    break
            # Only a plain ASCII integer is an index. str.isdigit() is True for
            # superscripts ('²') and other Unicode digits that int() then
            # rejects, which would surface a raw Python ValueError text.
            if target is None and _INDEX_RE.match(sheet.strip()):
                idx = int(sheet.strip())
                if 1 <= idx <= len(targets):
                    target = targets[idx - 1][1]
            if target is None:
                raise ValueError(
                    f"시트 '{sheet}' 을(를) 찾을 수 없습니다. "
                    f"이 파일의 시트: {', '.join(n for n, _ in targets)}")

        strings = _shared_strings(zf, budget)
        styles = _date_styles(zf, budget)
        data = _read_bounded(zf, target, budget)

    root = _parse_xml(data, "worksheet")
    rows: List[List[str]] = []
    for sheetdata in root.iter():
        if _local(sheetdata.tag) != "sheetData":
            continue
        for row in sheetdata:
            if _local(row.tag) != "row":
                continue
            if len(rows) >= _MAX_ROWS:
                raise ValueError("엑셀 시트의 행이 너무 많습니다.")
            cells: Dict[int, str] = {}
            next_col = 0
            for c in row:
                if _local(c.tag) != "c":
                    continue
                # Place by the cell reference so omitted (empty) cells do not
                # shift later values left; fall back to running position when
                # a writer omits r=.
                ci = _col_index(c.get("r") or "")
                if ci is None:
                    ci = next_col
                next_col = ci + 1
                if ci >= _MAX_COLS:
                    continue
                val = _cell_text(c, strings, styles, date1904)
                if val:
                    cells[ci] = val
            if not cells:
                rows.append([])
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        break

    # Normalize to a rectangle so a short trailing row cannot look ragged.
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _cell_text(c: ET.Element, strings: List[str], styles: Dict[int, bool],
               date1904: bool) -> str:
    ctype = c.get("t") or "n"
    vtext: Optional[str] = None
    inline: Optional[str] = None
    for child in c:
        tag = _local(child.tag)
        if tag == "v":
            vtext = child.text or ""
        elif tag == "is":
            inline = "".join((n.text or "") for n in child.iter()
                             if _local(n.tag) == "t")

    if ctype == "inlineStr":
        return (inline or "").strip()
    if ctype == "s":                      # shared string index
        try:
            return strings[int(vtext or "")].strip()
        except (ValueError, IndexError):
            return ""
    if ctype in ("str", "e"):             # cached formula result / error text
        return (vtext or "").strip()
    if ctype == "b":
        return "TRUE" if (vtext or "").strip() in ("1", "true", "TRUE") else "FALSE"

    # numeric (t="n" or absent) — may be a date under a date-formatted style
    if vtext is None or not vtext.strip():
        return ""
    try:
        sidx = int(c.get("s") or "-1")
    except ValueError:
        sidx = -1
    if styles.get(sidx):
        try:
            iso = _serial_to_iso(float(vtext), date1904)
        except ValueError:
            iso = None
        if iso is not None:
            return iso
    return _fmt_number(vtext.strip())
