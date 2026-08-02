"""Minimal, dependency-free reader for Excel ``.xlsx`` workbooks.

Clinical data almost never arrives as a tidy CSV.  It arrives as an Excel
workbook someone maintained by hand, and "export it to CSV first" is where the
Korean text gets mangled, the visit dates turn into ``45231``, and the trailing
empty rows sneak in.  This module reads the sheet directly so that step can be
skipped.

Only what a data table needs is implemented — an ``.xlsx`` is a zip of XML:

``xl/workbook.xml``       sheet names, order and visibility
``xl/sharedStrings.xml``  the string pool most text cells point into
``xl/styles.xml``         number formats, used only to tell dates from numbers
``xl/worksheets/*.xml``   the cells themselves

Deliberately **not** supported, and each raises or degrades loudly rather than
guessing: the legacy binary ``.xls`` (a completely different format), ``.xlsb``,
encrypted workbooks, and formulas whose cached result Excel did not store.
Formulas that *do* carry a cached value read as that value, which is what the
user sees on screen.

Dates are converted to ISO ``YYYY-MM-DD`` (or ``YYYY-MM-DD HH:MM:SS``) using the
workbook's own epoch, including the 1904 Mac epoch and Excel's fictional
29 Feb 1900.  Everything else is handed to the same string parsing the CSV path
uses, so a value behaves identically whichever file it came from.
"""

from __future__ import annotations

import datetime as _dt
import re
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

__all__ = ["is_xlsx", "read_xlsx", "sheet_names", "XlsxError"]

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Guards against a zip bomb: a 2 MB workbook must not expand to 8 GB of XML.
MAX_UNCOMPRESSED = 400 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_COLS = 4096

# Built-in numFmtIds that mean "date" or "time" (ECMA-376 §18.8.30).
_DATE_BUILTINS = frozenset(list(range(14, 23)) + [27, 30, 36, 45, 46, 47, 50,
                                                  57, 58])
_CELL_RE = re.compile(r"^([A-Z]+)")
# y/m/d/h/s outside quoted literals and outside colour/condition brackets.
_FMT_STRIP = re.compile(r'\[[^\]]*\]|"[^"]*"|\\.|_.')


class XlsxError(ValueError):
    """Raised for a workbook this reader cannot honestly interpret."""


def is_xlsx(path: str) -> bool:
    """True when *path* looks like an OOXML workbook (by content, not name).

    Sniffing the bytes matters: clinical exports are routinely named ``.csv``
    while actually being Excel files, and vice versa.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return False
    if magic[:2] != b"PK":
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and any(
        n.startswith("xl/") for n in names)


def _col_number(ref: str) -> int:
    """``"AB12"`` → 27 (0-based column index)."""
    m = _CELL_RE.match(ref)
    if not m:
        return -1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _check_size(zf: zipfile.ZipFile) -> None:
    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_UNCOMPRESSED:
        raise XlsxError(
            f"엑셀 파일의 압축을 풀면 {total // 1024 // 1024} MB 로 너무 큽니다 "
            "— 필요한 시트만 남겨 다시 저장하세요.")


def _read(zf: zipfile.ZipFile, name: str) -> Optional[bytes]:
    try:
        return zf.read(name)
    except KeyError:
        return None


def _parse(data: bytes, what: str) -> ET.Element:
    try:
        # ElementTree's parser has entity expansion disabled by default in the
        # C accelerator, so a billion-laughs payload raises rather than hangs.
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XlsxError(f"엑셀 파일의 {what} 을(를) 읽을 수 없습니다: {exc}") from None


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    data = _read(zf, "xl/sharedStrings.xml")
    if data is None:
        return []
    root = _parse(data, "문자열 목록")
    out: List[str] = []
    for si in root.findall(f"{_NS}si"):
        # Rich text splits one logical string across several <r><t> runs.
        parts = [t.text or "" for t in si.iter(f"{_NS}t")]
        # <rPh> holds furigana; it also contains <t>, so drop those runs.
        ph = {id(t) for rph in si.iter(f"{_NS}rPh") for t in rph.iter(f"{_NS}t")}
        if ph:
            parts = [t.text or "" for t in si.iter(f"{_NS}t") if id(t) not in ph]
        out.append("".join(parts))
    return out


def _date_style_ids(zf: zipfile.ZipFile) -> frozenset:
    """Indices into ``cellXfs`` whose number format renders a date/time."""
    data = _read(zf, "xl/styles.xml")
    if data is None:
        return frozenset()
    root = _parse(data, "서식")
    custom: Dict[int, str] = {}
    for nf in root.iter(f"{_NS}numFmt"):
        try:
            custom[int(nf.get("numFmtId", "-1"))] = nf.get("formatCode", "")
        except ValueError:
            continue
    out = set()
    xfs = root.find(f"{_NS}cellXfs")
    if xfs is None:
        return frozenset()
    for i, xf in enumerate(xfs.findall(f"{_NS}xf")):
        try:
            fid = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        if fid in _DATE_BUILTINS:
            out.add(i)
            continue
        code = custom.get(fid)
        if code:
            # Strip quoted literals/colour codes first: a currency format such
            # as ``"₩"#,##0`` must not be called a date because of a stray
            # letter inside the quotes.  "General" and plain numeric codes
            # never contain y/m/d/h.
            bare = _FMT_STRIP.sub("", code)
            if any(ch in bare for ch in "ymd") or "h" in bare:
                out.add(i)
    return frozenset(out)


def _serial_to_text(serial: float, epoch_1904: bool) -> str:
    """Excel serial number → ISO date text.

    Excel pretends 1900 was a leap year, so serials ≥ 61 are one day ahead of a
    naive count from 1899-12-31; serial 60 *is* the fictional 29 Feb 1900 and is
    returned verbatim rather than silently shifted onto a real date.
    """
    if epoch_1904:
        base = _dt.date(1904, 1, 1)
        days = int(serial)
        frac = serial - days
    else:
        if serial < 1:
            # Time-only cell (0 ≤ serial < 1): no date part to report.
            frac = serial
            secs = int(round(frac * 86400))
            secs %= 86400
            return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
        if 60 <= serial < 61:
            return "1900-02-29"
        days = int(serial)
        frac = serial - days
        base = _dt.date(1899, 12, 31)
        days -= 1 if serial >= 61 else 0
    try:
        day = base + _dt.timedelta(days=days)
    except (OverflowError, ValueError):
        return repr(serial)
    secs = int(round(frac * 86400))
    if secs >= 86400:                      # rounded up past midnight
        day += _dt.timedelta(days=1)
        secs = 0
    if secs:
        return (f"{day.isoformat()} {secs // 3600:02d}:"
                f"{secs % 3600 // 60:02d}:{secs % 60:02d}")
    return day.isoformat()


def _number_text(raw: str) -> str:
    """Render a numeric cell without Excel's float noise.

    ``18`` stored as ``18.000000000000004`` must not reach the number parser as
    that, or a visit label stops matching itself across rows.
    """
    try:
        val = float(raw)
    except ValueError:
        return raw
    if val == int(val) and abs(val) < 1e15:
        return str(int(val))
    trimmed = repr(round(val, 10))
    return trimmed


def _sheet_targets(zf: zipfile.ZipFile) -> List[Tuple[str, str, bool]]:
    """``[(name, zip path, visible), ...]`` in workbook order."""
    data = _read(zf, "xl/workbook.xml")
    if data is None:
        raise XlsxError("엑셀 파일에 워크북 정보(xl/workbook.xml)가 없습니다.")
    root = _parse(data, "워크북")
    rels_data = _read(zf, "xl/_rels/workbook.xml.rels")
    rels: Dict[str, str] = {}
    if rels_data is not None:
        for rel in _parse(rels_data, "워크북 연결").findall(f"{_PKG_REL}Relationship"):
            target = rel.get("Target", "")
            if target.startswith("/"):
                target = target[1:]
            elif not target.startswith("xl/"):
                target = "xl/" + target.lstrip("./")
            rels[rel.get("Id", "")] = target
    out: List[Tuple[str, str, bool]] = []
    sheets = root.find(f"{_NS}sheets")
    for i, sh in enumerate(sheets.findall(f"{_NS}sheet") if sheets is not None
                           else []):
        rid = sh.get(f"{_RNS}id", "")
        path = rels.get(rid) or f"xl/worksheets/sheet{i + 1}.xml"
        out.append((sh.get("name", f"Sheet{i + 1}"), path,
                    sh.get("state", "visible") == "visible"))
    if not out:
        raise XlsxError("엑셀 파일에 시트가 없습니다.")
    return out


def sheet_names(path: str) -> List[str]:
    """Sheet names in workbook order (hidden sheets marked)."""
    with zipfile.ZipFile(path) as zf:
        return [n if vis else f"{n} (숨김)" for n, _, vis in _sheet_targets(zf)]


def _pick(targets: Sequence[Tuple[str, str, bool]], sheet: Optional[str]
          ) -> Tuple[str, str]:
    if sheet is None:
        for name, path, visible in targets:
            if visible:
                return name, path
        return targets[0][0], targets[0][1]
    wanted = sheet.strip()
    for name, path, _ in targets:
        if name == wanted:
            return name, path
    lowered = [t for t in targets if t[0].lower() == wanted.lower()]
    if len(lowered) == 1:
        return lowered[0][0], lowered[0][1]
    if wanted.isdigit():
        idx = int(wanted)
        if 1 <= idx <= len(targets):
            return targets[idx - 1][0], targets[idx - 1][1]
        raise XlsxError(
            f"--sheet {idx} 번 시트가 없습니다 (이 파일에는 {len(targets)}개).")
    raise XlsxError(
        f"'{sheet}' 시트를 찾을 수 없습니다. 사용 가능한 시트: "
        + ", ".join(n for n, _, _ in targets))


def read_xlsx(path: str, sheet: Optional[str] = None,
              notes: Optional[List[str]] = None) -> List[List[str]]:
    """Read one worksheet into a rectangular list of string cells.

    Every cell is returned as text so the rest of longistat treats an Excel
    file and a CSV identically — the number/missing-token parsing lives in one
    place (:func:`longistat.dataio.parse_number`) rather than two.
    """
    notes = notes if notes is not None else []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise XlsxError(
            "엑셀 파일로 열 수 없습니다 — 오래된 .xls 형식이거나 암호가 걸려 "
            "있을 수 있습니다. Excel에서 .xlsx 또는 CSV 로 다시 저장하세요."
        ) from None
    with zf:
        _check_size(zf)
        targets = _sheet_targets(zf)
        name, sheet_path = _pick(targets, sheet)
        if sheet is None and len(targets) > 1:
            notes.append(
                f"엑셀 시트 '{name}' 을(를) 읽었습니다 (총 {len(targets)}개 — "
                "다른 시트는 --sheet 로 지정하세요).")
        data = _read(zf, sheet_path)
        if data is None:
            raise XlsxError(f"시트 '{name}' 의 내용을 찾을 수 없습니다.")
        shared = _shared_strings(zf)
        date_styles = _date_style_ids(zf)
        wb = _parse(_read(zf, "xl/workbook.xml") or b"<x/>", "워크북")
        pr = wb.find(f"{_NS}workbookPr")
        epoch_1904 = bool(pr is not None
                          and pr.get("date1904", "0") in ("1", "true"))
        root = _parse(data, f"시트 '{name}'")

    rows: List[List[str]] = []
    truncated = False
    sheet_data = root.find(f"{_NS}sheetData")
    for r in (sheet_data.findall(f"{_NS}row") if sheet_data is not None else []):
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        cells: List[str] = []
        for c in r.findall(f"{_NS}c"):
            idx = _col_number(c.get("r", ""))
            if idx < 0:
                idx = len(cells)
            if idx >= MAX_COLS:
                truncated = True
                continue
            while len(cells) < idx:
                cells.append("")
            cells.append(_cell_text(c, shared, date_styles, epoch_1904))
        rows.append(cells)
    if truncated:
        notes.append(
            f"시트가 매우 커서 앞의 {MAX_ROWS}행 / {MAX_COLS}열까지만 "
            "읽었습니다.")

    # Excel keeps formatted-but-empty rows and columns; drop the trailing ones
    # so a sheet with styling down to row 5000 is not "4900 blank subjects".
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        raise XlsxError(f"시트 '{name}' 에 데이터가 없습니다.")
    width = max(len(r) for r in rows)
    while width > 1 and all(len(r) < width or not r[width - 1].strip()
                            for r in rows):
        width -= 1
    return [(r + [""] * width)[:width] for r in rows]


def _cell_text(c: ET.Element, shared: Sequence[str], date_styles: frozenset,
               epoch_1904: bool) -> str:
    ctype = c.get("t", "n")
    if ctype == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{_NS}t"))
    v = c.find(f"{_NS}v")
    if v is None or v.text is None:
        return ""
    raw = v.text
    if ctype == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "TRUE" if raw not in ("0", "", "false") else "FALSE"
    if ctype == "e":
        # #N/A, #DIV/0! … — MISSING_TOKENS already treats these as missing.
        return raw
    if ctype in ("str", "d"):
        return raw
    try:
        style = int(c.get("s", "-1"))
    except ValueError:
        style = -1
    if style in date_styles:
        try:
            return _serial_to_text(float(raw), epoch_1904)
        except ValueError:
            return raw
    return _number_text(raw)
