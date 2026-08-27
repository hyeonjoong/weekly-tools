"""XLSX 를 zipfile + ElementTree 로 직접 읽습니다(외부 의존성 0).

openpyxl/pandas 를 쓰지 않는 이유는 의존성 회피만이 아닙니다. 이 툴이
찾아야 하는 것 중 상당수는 **일반적인 리더가 보여 주지 않는 것들**입니다:

* 숨김 시트(`state="hidden"` / `"veryHidden"`)
* 숨김 열 / 숨김 행
* 셀 주석(레거시 comments, 스레드 주석)
* `docProps/core.xml` 의 작성자·최종수정자, `docProps/app.xml` 의 회사·관리자
* 정의된 이름(defined names) — 삭제한 줄 알았던 범위가 남아 있는 자리

이것들은 엑셀로 열어봐서는 구조적으로 눈에 띄지 않습니다.
"""

from __future__ import annotations

import datetime as _dt
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

from .findings import Finding, WARNING
from .masking import mask_generic
from .tabular import LoadResult, Table, _dedupe_header, pick_header_index

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_NS_DC = "{http://purl.org/dc/elements/1.1/}"
_NS_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_NS_EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

# 압축 해제 후 총 크기가 이보다 크면 읽지 않습니다(zip bomb 방어).
MAX_UNCOMPRESSED = 600 * 1024 * 1024
MAX_SHARED_STRINGS = 2_000_000
# 엑셀 자체의 상한. 셀 참조가 이걸 넘으면 잘못된 참조로 보고 무시합니다.
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLS = 16_384
# 엑셀 기본 날짜 서식 ID.
_BUILTIN_DATE_FMTS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}

_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def col_letters_to_index(letters: str) -> int:
    """엑셀 열 문자(A, B, …, XFD)를 0-기반 인덱스로 바꿉니다.

    엑셀 상한(XFD = 16,384열)을 넘는 참조는 -1 을 돌려줍니다. 이 상한이 없으면
    `<c r="AAAAAAA1">` 하나로 32억 열짜리 격자를 만들려다 메모리를 다 씁니다.
    """
    if not letters or len(letters) > 3:
        return -1
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    if index > EXCEL_MAX_COLS:
        return -1
    return index - 1


def index_to_col_letters(index: int) -> str:
    """0-기반 인덱스를 엑셀 열 문자로 바꿉니다."""
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _text_of(elem) -> str:
    """<si>/<is> 요소의 텍스트를 런(run)까지 합쳐 돌려줍니다."""
    parts = []
    for node in elem.iter():
        if node.tag == _NS_MAIN + "t":
            parts.append(node.text or "")
    return "".join(parts)


def _serial_to_datetime(serial: float, date1904: bool) -> Optional[_dt.datetime]:
    """엑셀 날짜 일련번호를 datetime 으로 바꿉니다."""
    if serial < 0 or serial > 2_958_465:  # 9999-12-31 근처
        return None
    epoch = _dt.datetime(1904, 1, 1) if date1904 else _dt.datetime(1899, 12, 30)
    # 1900 윤년 버그: 1900-02-29(일련번호 60)는 존재하지 않는 날짜입니다.
    if not date1904 and serial < 60:
        epoch = _dt.datetime(1899, 12, 31)
    # 엑셀은 시각을 하루의 분수로 저장하므로 그대로 더하면 23:40 이 23:39:59 가 됩니다.
    # 초 단위로 반올림해야 원본 표기를 되살릴 수 있습니다.
    whole = int(serial // 1)
    seconds = int(round((float(serial) - whole) * 86400))
    if seconds >= 86400:
        whole += 1
        seconds -= 86400
    try:
        return epoch + _dt.timedelta(days=whole, seconds=seconds)
    except (OverflowError, ValueError):
        return None


def _format_datetime(value: _dt.datetime, has_time: bool) -> str:
    if has_time and (value.hour or value.minute or value.second):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value.strftime("%Y-%m-%d")


class _Styles:
    """cellXfs → 날짜 서식 여부 판정."""

    def __init__(self, xf_is_date: List[bool], xf_has_time: List[bool]):
        self.xf_is_date = xf_is_date
        self.xf_has_time = xf_has_time

    def is_date(self, style_index: Optional[int]) -> bool:
        if style_index is None or style_index >= len(self.xf_is_date):
            return False
        return self.xf_is_date[style_index]

    def has_time(self, style_index: Optional[int]) -> bool:
        if style_index is None or style_index >= len(self.xf_has_time):
            return False
        return self.xf_has_time[style_index]


def _parse_styles(zf: zipfile.ZipFile) -> _Styles:
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return _Styles([], [])
    custom: Dict[int, str] = {}
    for numfmt in root.iter(_NS_MAIN + "numFmt"):
        try:
            fmt_id = int(numfmt.get("numFmtId", "-1"))
        except ValueError:
            continue
        custom[fmt_id] = numfmt.get("formatCode", "")

    is_date: List[bool] = []
    has_time: List[bool] = []
    cell_xfs = root.find(_NS_MAIN + "cellXfs")
    if cell_xfs is not None:
        for xf in cell_xfs.findall(_NS_MAIN + "xf"):
            try:
                fmt_id = int(xf.get("numFmtId", "0"))
            except ValueError:
                fmt_id = 0
            code = custom.get(fmt_id)
            if code is not None:
                stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
                date_like = bool(re.search(r"[yYdD]", stripped)) or bool(
                    re.search(r"m{3,}", stripped)
                )
                time_like = bool(re.search(r"[hHsS]", stripped))
                is_date.append(date_like or time_like)
                has_time.append(time_like)
            else:
                is_date.append(fmt_id in _BUILTIN_DATE_FMTS)
                has_time.append(fmt_id in {18, 19, 20, 21, 22, 45, 46, 47})
    return _Styles(is_date, has_time)


def _parse_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: List[str] = []
    for si in root.findall(_NS_MAIN + "si"):
        out.append(_text_of(si))
        if len(out) >= MAX_SHARED_STRINGS:
            break
    return out


def _parse_rels(zf: zipfile.ZipFile, part: str) -> Dict[str, str]:
    rels_path = str(Path(part).parent / "_rels" / (Path(part).name + ".rels"))
    rels_path = rels_path.replace("\\", "/")
    try:
        root = ET.fromstring(zf.read(rels_path))
    except (KeyError, ET.ParseError):
        return {}
    out = {}
    for rel in root.findall(_NS_PKG_REL + "Relationship"):
        rid = rel.get("Id")
        target = rel.get("Target", "")
        if rid:
            out[rid] = target
    return out


def _normalize(path: str) -> str:
    parts: List[str] = []
    for piece in path.replace("\\", "/").split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if parts:
                parts.pop()
            continue
        parts.append(piece)
    return "/".join(parts)


class _SheetIssues:
    """시트를 읽으면서 만난 문제들(자백 대상)."""

    __slots__ = ("bad_refs", "unresolved_shared", "unreadable", "row_numbers")

    def __init__(self, bad_refs: int, unresolved_shared: int, unreadable: str = ""):
        self.bad_refs = bad_refs
        self.unresolved_shared = unresolved_shared
        # 비어 있는 것과 **읽지 못한 것**은 완전히 다릅니다. 읽지 못했으면
        # 그 시트에 대해서는 아무 말도 할 수 없습니다(판정불가).
        self.unreadable = unreadable
        # 격자의 각 행이 실제 스프레드시트의 몇 번째 행인지.
        self.row_numbers: List[int] = []


def _parse_sheet(
    zf: zipfile.ZipFile,
    part: str,
    shared: List[str],
    styles: _Styles,
    date1904: bool,
) -> Tuple[List[List[str]], Set[int], Set[int], int]:
    """시트 XML 을 densely 채운 문자열 격자로 바꿉니다.

    Returns:
        (grid, hidden_col_indices, hidden_row_numbers, issues)
    """
    try:
        data = zf.read(part)
    except KeyError:
        return [], set(), set(), _SheetIssues(0, 0, unreadable="시트 XML 을 찾을 수 없음")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return [], set(), set(), _SheetIssues(0, 0, unreadable=f"시트 XML 파싱 실패 ({exc})")

    hidden_cols: Set[int] = set()
    cols = root.find(_NS_MAIN + "cols")
    if cols is not None:
        for col in cols.findall(_NS_MAIN + "col"):
            if col.get("hidden") == "1" or col.get("hidden") == "true":
                try:
                    lo = int(col.get("min", "1"))
                    hi = int(col.get("max", col.get("min", "1")))
                except ValueError:
                    continue
                hi = min(hi, lo + 16384)
                for c in range(lo, hi + 1):
                    hidden_cols.add(c - 1)

    hidden_rows: Set[int] = set()
    grid: Dict[int, Dict[int, str]] = {}
    max_col = -1
    bad_refs = 0
    unresolved_shared = 0
    sheet_data = root.find(_NS_MAIN + "sheetData")
    if sheet_data is None:
        return [], hidden_cols, hidden_rows, _SheetIssues(0, 0)

    implicit_row = 0
    for row in sheet_data.findall(_NS_MAIN + "row"):
        try:
            row_num = int(row.get("r", "0")) or (implicit_row + 1)
        except ValueError:
            row_num = implicit_row + 1
        implicit_row = row_num
        if row_num > EXCEL_MAX_ROWS:
            bad_refs += 1
            continue
        if row.get("hidden") in ("1", "true"):
            hidden_rows.add(row_num)
        row_map = grid.setdefault(row_num, {})
        implicit_col = -1
        for cell in row.findall(_NS_MAIN + "c"):
            ref = cell.get("r") or ""
            m = _CELL_REF_RE.match(ref)
            if m:
                col_index = col_letters_to_index(m.group(1))
                if col_index < 0:
                    bad_refs += 1
                    continue
            else:
                col_index = implicit_col + 1
            if col_index >= EXCEL_MAX_COLS:
                bad_refs += 1
                continue
            implicit_col = col_index
            value = _cell_value(cell, shared, styles, date1904)
            if value == "":
                # 서식만 있고 값이 없는 셀은 격자를 넓히지 않습니다.
                if cell.get("t") == "s" and not shared:
                    unresolved_shared += 1
                continue
            max_col = max(max_col, col_index)
            row_map[col_index] = value

    grid = {r: m for r, m in grid.items() if m}
    if not grid:
        return [], hidden_cols, hidden_rows, _SheetIssues(bad_refs, unresolved_shared)
    # **값이 있는 행만** 만듭니다. `1..max_row` 를 전부 만들면 시트 맨 아래의
    # 값 하나(엑셀에서 행 전체를 선택하면 생깁니다)로 6KB 파일이 2GB 를 먹습니다.
    # 실제 행 번호는 `row_numbers` 로 따로 들고 갑니다.
    width = max_col + 1
    out: List[List[str]] = []
    numbers: List[int] = []
    for r in sorted(grid):
        if r > EXCEL_MAX_ROWS:
            bad_refs += 1
            continue
        row_map = grid[r]
        out.append([row_map.get(c, "") for c in range(width)])
        numbers.append(r)
    issues = _SheetIssues(bad_refs, unresolved_shared)
    issues.row_numbers = numbers
    return out, hidden_cols, hidden_rows, issues


def _cell_value(cell, shared: List[str], styles: _Styles, date1904: bool) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        node = cell.find(_NS_MAIN + "is")
        return _text_of(node) if node is not None else ""
    value_node = cell.find(_NS_MAIN + "v")
    raw = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type in ("str", "e"):
        return raw
    if cell_type == "b":
        return "TRUE" if raw in ("1", "true", "TRUE") else "FALSE"
    if raw == "":
        return ""
    try:
        style_index = int(cell.get("s", "-1"))
    except ValueError:
        style_index = -1
    if style_index >= 0 and styles.is_date(style_index):
        try:
            serial = float(raw)
        except ValueError:
            return raw
        converted = _serial_to_datetime(serial, date1904)
        if converted is not None:
            return _format_datetime(converted, styles.has_time(style_index))
    return raw


def _parse_comments(zf: zipfile.ZipFile, sheet_part: str) -> List[Tuple[str, str, str]]:
    """(셀참조, 작성자, 본문) 목록. 레거시 주석 + 스레드 주석."""
    out: List[Tuple[str, str, str]] = []
    rels = _parse_rels(zf, sheet_part)
    for target in rels.values():
        part = _normalize(str(Path(sheet_part).parent / target))
        if "comments" not in Path(part).name.lower():
            continue
        try:
            root = ET.fromstring(zf.read(part))
        except (KeyError, ET.ParseError):
            continue
        authors = [a.text or "" for a in root.iter(_NS_MAIN + "author")]
        comment_list = root.find(_NS_MAIN + "commentList")
        if comment_list is not None:
            for comment in comment_list.findall(_NS_MAIN + "comment"):
                ref = comment.get("ref", "")
                try:
                    author = authors[int(comment.get("authorId", "0"))]
                except (ValueError, IndexError):
                    author = ""
                out.append((ref, author, _text_of(comment)))
        # 스레드 주석(Office 365)
        for tc in root.iter():
            if tc.tag.endswith("}threadedComment"):
                ref = tc.get("ref", "")
                body = "".join(n.text or "" for n in tc.iter() if n.tag.endswith("}text"))
                out.append((ref, "", body))
    return out


def load_xlsx(path: Path, display: Optional[str] = None, max_bytes: int = 200 * 1024 * 1024) -> LoadResult:
    """XLSX 한 개를 읽고, 숨은 내용에 대한 Finding 을 함께 돌려줍니다."""
    name = display or path.name
    result = LoadResult(file=name)
    try:
        if path.stat().st_size > max_bytes:
            result.fatal = "파일이 너무 큼"
            return result
    except OSError as exc:
        result.fatal = f"파일 정보를 읽을 수 없음 ({exc.__class__.__name__})"
        return result

    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result.fatal = f"XLSX(zip)로 열 수 없음 — .xls 구형식이거나 암호가 걸렸을 수 있음 ({exc.__class__.__name__})"
        return result

    with zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNCOMPRESSED:
            result.fatal = f"압축 해제 크기가 너무 큼 ({total:,} 바이트)"
            return result

        try:
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        except (KeyError, ET.ParseError) as exc:
            result.fatal = f"workbook.xml 을 읽을 수 없음 ({exc.__class__.__name__})"
            return result

        date1904 = False
        wb_pr = wb_root.find(_NS_MAIN + "workbookPr")
        if wb_pr is not None and wb_pr.get("date1904") in ("1", "true"):
            date1904 = True

        shared = _parse_shared_strings(zf)
        styles = _parse_styles(zf)
        rels = _parse_rels(zf, "xl/workbook.xml")

        result.structural.extend(_document_metadata_findings(zf, name))
        result.structural.extend(_defined_name_findings(wb_root, name))

        sheets_node = wb_root.find(_NS_MAIN + "sheets")
        sheet_elems = sheets_node.findall(_NS_MAIN + "sheet") if sheets_node is not None else []
        if not sheet_elems:
            result.fatal = "시트가 하나도 없음"
            return result

        for sheet in sheet_elems:
            sheet_name = sheet.get("name", "(이름없음)")
            state = (sheet.get("state") or "visible").lower()
            rid = sheet.get(_NS_REL + "id")
            target = rels.get(rid or "", "")
            if not target:
                result.skipped.append((f"{name}!{sheet_name}", "시트 관계(rels)를 찾을 수 없음"))
                continue
            part = _normalize(str(Path("xl") / target)) if not target.startswith("/") else target.lstrip("/")
            if part not in zf.namelist():
                alt = _normalize(target)
                part = alt if alt in zf.namelist() else part
            grid, hidden_cols, hidden_rows, issues = _parse_sheet(zf, part, shared, styles, date1904)
            if issues.unreadable:
                result.skipped.append((f"{name}!{sheet_name}", issues.unreadable))
                result.unreadable_sheets += 1
                continue
            if issues.bad_refs:
                result.skipped.append(
                    (f"{name}!{sheet_name}", f"엑셀 범위를 벗어난 셀 참조 {issues.bad_refs}개를 무시했습니다")
                )
            if issues.unresolved_shared:
                result.skipped.append(
                    (
                        f"{name}!{sheet_name}",
                        f"공유 문자열(xl/sharedStrings.xml)이 없어 문자 셀 {issues.unresolved_shared}개를 읽지 못했습니다",
                    )
                )
            hidden_sheet = state in ("hidden", "veryhidden")

            if hidden_sheet:
                result.structural.append(
                    Finding(
                        severity=WARNING,
                        kind="숨김 시트",
                        file=name,
                        sheet=sheet_name,
                        evidence=f"state={state}",
                        note=(
                            f"엑셀에서 열어도 보이지 않는 시트입니다(데이터 {max(len(grid) - 1, 0)}행). "
                            "이 파일을 그대로 보내면 이 시트도 함께 나갑니다."
                        ),
                    )
                )
            if not grid:
                result.skipped.append((f"{name}!{sheet_name}", "빈 시트"))
                continue

            # 제목 줄·빈 줄이 위에 있는 시트가 흔합니다. 첫 행을 무조건 헤더로 쓰면
            # 표 전체의 열 이름이 "2026년 수면연구 참여 현황"/"열2"/"열3" 이 되고,
            # 헤더 기반 규칙(이름·생년월일·--quasi·--drop-columns)이 전부 죽습니다.
            if not any(any(str(c).strip() for c in row) for row in grid):
                result.skipped.append((f"{name}!{sheet_name}", "값이 있는 행이 없음"))
                continue
            first = pick_header_index(grid)
            if first:
                result.skipped.append(
                    (
                        f"{name}!{sheet_name}",
                        f"헤더 앞의 제목/빈 행 {first}개를 건너뛰고 {first + 1}행을 헤더로 봤습니다",
                    )
                )
            sheet_row_numbers = issues.row_numbers or list(range(1, len(grid) + 1))
            header_cells = [str(c).strip() for c in grid[first]]
            header = _dedupe_header(header_cells)
            preheader = [
                str(c) for i in range(first) for c in grid[i] if str(c).strip()
            ]
            body = []
            row_numbers = []
            for i in range(first + 1, len(grid)):
                raw_row = grid[i]
                if not any(str(c).strip() for c in raw_row):
                    continue
                body.append(raw_row)
                row_numbers.append(sheet_row_numbers[i] if i < len(sheet_row_numbers) else i + 1)
            rows = [
                list(r) + [""] * (len(header) - len(r)) if len(r) < len(header) else list(r[: len(header)])
                for r in body
            ]
            table = Table(
                file=name,
                sheet=sheet_name,
                columns=header,
                rows=rows,
                path=path,
                hidden_sheet=hidden_sheet,
                hidden_columns={i for i in hidden_cols if i < len(header)},
                hidden_rows={r for r in hidden_rows if r >= 2},
                encoding="xlsx",
                delimiter="",
                source_rows=row_numbers,
                preheader_cells=preheader,
                original_columns=header_cells,
            )
            result.tables.append(table)

            for idx in sorted(table.hidden_columns):
                result.structural.append(
                    Finding(
                        severity=WARNING,
                        kind="숨김 열",
                        file=name,
                        sheet=sheet_name,
                        column=header[idx] if idx < len(header) else index_to_col_letters(idx),
                        evidence=f"{index_to_col_letters(idx)} 열",
                        note="화면에서 숨겨져 있을 뿐 파일에는 값이 그대로 들어 있습니다.",
                    )
                )
            if table.hidden_rows:
                result.structural.append(
                    Finding(
                        severity=WARNING,
                        kind="숨김 행",
                        file=name,
                        sheet=sheet_name,
                        evidence=f"{len(table.hidden_rows)}개 행",
                        note="숨겨진 행도 파일에는 값이 그대로 들어 있습니다.",
                    )
                )
            for ref, author, body in _parse_comments(zf, part):
                result.structural.append(
                    Finding(
                        severity=WARNING,
                        kind="셀 주석",
                        file=name,
                        sheet=sheet_name,
                        evidence=f"{ref or '(위치불명)'} · 본문 {mask_generic(body)}",
                        note=(
                            "셀 주석은 인쇄에도 화면에도 잘 드러나지 않지만 파일에 남습니다"
                            + (f" (작성자 {mask_generic(author)})" if author else "")
                        ),
                    )
                )
    return result


def _document_metadata_findings(zf: zipfile.ZipFile, name: str) -> List[Finding]:
    """docProps 의 작성자·최종수정자·회사·관리자를 지적합니다."""
    out: List[Finding] = []
    fields = [
        ("docProps/core.xml", _NS_DC + "creator", "작성자"),
        ("docProps/core.xml", _NS_CP + "lastModifiedBy", "최종수정자"),
        ("docProps/app.xml", _NS_EP + "Company", "회사"),
        ("docProps/app.xml", _NS_EP + "Manager", "관리자"),
    ]
    cache: Dict[str, Optional[ET.Element]] = {}
    for part, tag, label in fields:
        if part not in cache:
            try:
                cache[part] = ET.fromstring(zf.read(part))
            except (KeyError, ET.ParseError):
                cache[part] = None
        root = cache[part]
        if root is None:
            continue
        node = root.find(tag)
        if node is None or not (node.text or "").strip():
            continue
        out.append(
            Finding(
                severity=WARNING,
                kind=f"문서 메타데이터({label})",
                file=name,
                evidence=f"{part} → {label} {mask_generic(node.text)}",
                note="엑셀 파일 속성에 남는 값입니다. 파일을 열어 봐도 표에는 나타나지 않습니다.",
            )
        )
    return out


def _defined_name_findings(wb_root: ET.Element, name: str) -> List[Finding]:
    """정의된 이름(defined names)을 지적합니다."""
    out: List[Finding] = []
    node = wb_root.find(_NS_MAIN + "definedNames")
    if node is None:
        return out
    for dn in node.findall(_NS_MAIN + "definedName"):
        dn_name = dn.get("name", "")
        if dn_name.startswith("_xlnm."):
            continue  # 인쇄 영역 등 엑셀 내부 이름
        # 정의된 이름은 사용자가 짓습니다 — `연락처_01023456789` 처럼 이름 자체가
        # 식별자인 경우가 실제로 있으므로 원문을 리포트에 싣지 않습니다.
        target = (dn.text or "").split("!")[0].strip("'=") if dn.text else ""
        out.append(
            Finding(
                severity=WARNING,
                kind="정의된 이름",
                file=name,
                sheet=target,
                evidence=f"이름 {mask_generic(dn_name)} ({len(dn_name)}자)",
                note=(
                    "이름이 가리키는 범위가 숨김 시트를 참조할 수 있고, 이름 자체에 식별자가 "
                    "들어 있는 경우도 있어 원문은 싣지 않습니다. 엑셀 → 수식 → 이름 관리자에서 확인하세요."
                ),
            )
        )
    return out
