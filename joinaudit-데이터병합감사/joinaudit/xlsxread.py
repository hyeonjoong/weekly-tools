"""엑셀 .xlsx 워크시트를 문자열 행 목록으로 읽는다 — 표준 라이브러리만.

임상 데이터는 CSV가 아니라 엑셀로 온다. "엑셀에서 CSV로 다시 저장하세요"는
이 툴이 없애려는 바로 그 수작업이므로, `zipfile` + `xml.etree`로 직접 읽는다.

이 모듈이 신경 쓰는 것
---------------------
* **희소 행** — 엑셀은 빈 셀을 XML에서 통째로 생략한다. 위치가 아니라 `r="C7"`
  참조로 열을 배치하지 않으면, 빈 셀 하나 뒤의 모든 값이 한 칸씩 왼쪽으로
  밀린다. 병합 툴에서 이 실수는 곧 "다른 사람의 값을 그 사람 것으로 붙이는"
  사고다.
* **날짜 시리얼** — 날짜 서식이 걸린 숫자 셀은 ISO 문자열로 되돌린다.
  1900/1904 에포크와 엑셀이 일부러 남겨 둔 1900-02-29(존재하지 않는 날짜)를
  모두 처리한다.
* **공유 문자열의 리치 텍스트** — 셀 일부만 굵게 칠해도 텍스트가 여러 `<r>`
  런으로 쪼개진다. 전부 이어 붙이지 않으면 ID의 앞 두 글자만 읽힌다.
* **압축 폭탄 방어** — .xlsx는 zip이다. 헤더가 선언한 크기를 믿지 않고 실제
  해제 바이트를 세면서 상한을 넘으면 중단한다.

읽기 전용이며 아무것도 실행하거나 가져오지 않는다.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional, Tuple

__all__ = ["looks_like_xlsx", "looks_like_legacy_xls", "read_sheet", "sheet_names"]

# 워크북 하나에서 풀 수 있는 총 바이트. 정상 임상 워크북은 이 아래로 한참
# 내려가고, 압축 폭탄은 이 위로 한참 올라간다.
_MAX_INFLATE = 256 * 1024 * 1024
_MAX_ROWS = 1_048_576          # 엑셀 자체 행 한계
_MAX_COLS = 16_384             # 엑셀 자체 열 한계 (XFD)

_REF_RE = re.compile(r"^([A-Za-z]{1,3})(\d+)$")
_INDEX_RE = re.compile(r"^[0-9]{1,7}$")

# 날짜/시간을 뜻하는 내장 numFmtId (ECMA-376 18.8.30).
_BUILTIN_DATE_IDS = frozenset(list(range(14, 23)) + list(range(45, 48)))
# 사용자 정의 서식에서 따옴표 리터럴/대괄호 블록/이스케이프를 걷어낸 뒤에도
# y/m/d/h/s 토큰이 남으면 날짜 서식이다. (통화 리터럴 "월" 같은 건 걷힌다.)
_LITERAL_RE = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')
_DATE_TOKEN_RE = re.compile(r"[ymdhs]", re.IGNORECASE)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class XlsxError(ValueError):
    """엑셀 파일을 읽을 수 없을 때 — 메시지는 사람이 조치할 수 있는 한국어."""


def _tag(elem_tag: str) -> str:
    """'{ns}row' -> 'row'."""
    return elem_tag.rsplit("}", 1)[-1]


def looks_like_xlsx(path: str) -> bool:
    """확장자가 아니라 컨테이너를 실제로 확인한다."""
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"PK":
                return False
        with zipfile.ZipFile(path) as zf:
            return "xl/workbook.xml" in set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def looks_like_legacy_xls(path: str) -> bool:
    """구형 OLE2(.xls)인가 — 그렇다면 인코딩 탓을 하는 대신 재저장을 안내한다."""
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == _OLE2_MAGIC
    except OSError:
        return False


class _Budget:
    """아카이브 전체에서 풀 수 있는 바이트를 세는 예산."""

    __slots__ = ("left",)

    def __init__(self, total: int = _MAX_INFLATE) -> None:
        self.left = total

    def spend(self, n: int) -> None:
        self.left -= n
        if self.left < 0:
            raise XlsxError(
                "엑셀 파일의 압축을 풀면 너무 커집니다"
                f"(> {_MAX_INFLATE // (1024 * 1024)}MB). "
                "손상되었거나 비정상적인 파일일 수 있습니다.")


def _read_entry(zf: zipfile.ZipFile, name: str, budget: _Budget) -> bytes:
    """엔트리 하나를 예산 안에서 스트리밍으로 읽는다."""
    chunks: List[bytes] = []
    if name not in zf.NameToInfo:
        raise XlsxError(
            f"엑셀 파일 안에 '{name}' 이 없습니다. 올바른 .xlsx 워크북이 "
            "아니거나 손상되었습니다 — 엑셀에서 다시 저장해 주세요.")
    try:
        with zf.open(name) as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                budget.spend(len(chunk))
                chunks.append(chunk)
    except NotImplementedError:
        raise XlsxError(
            "지원하지 않는 압축 방식으로 저장된 엑셀 파일입니다"
            "(암호화되었거나 특수 도구로 만든 파일일 수 있습니다). "
            "엑셀에서 다시 .xlsx 또는 'CSV UTF-8'로 저장해 주세요.")
    except RuntimeError as exc:
        low = str(exc).lower()
        if "encrypt" in low or "password" in low:
            raise XlsxError(
                "암호로 보호된 엑셀 파일은 읽을 수 없습니다. "
                "엑셀에서 암호를 해제하고 저장한 뒤 다시 실행하세요.")
        raise XlsxError(f"엑셀 파일을 읽을 수 없습니다: {exc}")
    except (zipfile.BadZipFile, EOFError, OSError) as exc:
        raise XlsxError(f"엑셀 파일이 손상되었습니다: {exc}")
    return b"".join(chunks)


def _xml(data: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XlsxError(f"엑셀 내부 XML({what})을 해석할 수 없습니다: {exc}")


def _column_of(ref: str) -> Optional[int]:
    """'C7' -> 2 (0-based). 해석 불가면 None."""
    m = _REF_RE.match(ref.strip())
    if not m:
        return None
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _workbook(zf: zipfile.ZipFile, budget: _Budget
              ) -> Tuple[List[Tuple[str, str]], bool]:
    """[(시트이름, zip경로)] (탭 순서) 와 date1904 여부."""
    root = _xml(_read_entry(zf, "xl/workbook.xml", budget), "workbook")

    date1904 = False
    for node in root:
        if _tag(node.tag) == "workbookPr":
            raw = node.get("date1904") or node.get("date1904Compat") or "0"
            date1904 = str(raw).strip().lower() in ("1", "true")

    # 시트를 실제 XML 파트에 묶는 것은 관계(rels)다. workbook.xml의 순서는
    # 사용자가 보는 탭 순서이므로 "N번째 시트"는 이 순서로 센다.
    rels: Dict[str, str] = {}
    names = set(zf.namelist())
    if "xl/_rels/workbook.xml.rels" in names:
        try:
            rel_root = _xml(
                _read_entry(zf, "xl/_rels/workbook.xml.rels", budget), "rels")
        except XlsxError:
            rel_root = []  # type: ignore[assignment]
        for rel in rel_root:
            rid, target = rel.get("Id"), (rel.get("Target") or "")
            if not rid or not target:
                continue
            target = target.replace("\\", "/")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rels[rid] = target

    sheets: List[Tuple[str, str]] = []
    for node in root:
        if _tag(node.tag) != "sheets":
            continue
        for sh in node:
            if _tag(sh.tag) != "sheet":
                continue
            rid = next((v for k, v in sh.attrib.items() if _tag(k) == "id"), "")
            target = rels.get(rid, "")
            if target in names:
                sheets.append((sh.get("name") or "", target))
    return sheets, date1904


def sheet_names(path: str) -> List[str]:
    """워크시트 이름을 탭 순서로."""
    budget = _Budget()
    with zipfile.ZipFile(path) as zf:
        sheets, _ = _workbook(zf, budget)
    return [name for name, _ in sheets]


def _shared_strings(zf: zipfile.ZipFile, budget: _Budget) -> List[str]:
    if "xl/sharedStrings.xml" not in set(zf.namelist()):
        return []
    root = _xml(_read_entry(zf, "xl/sharedStrings.xml", budget), "sharedStrings")
    out: List[str] = []
    for si in root:
        if _tag(si.tag) != "si":
            continue
        # 단일 <t> 이거나, 리치 텍스트라면 여러 <r> 런 각각의 <t>. 문서 순서대로
        # 전부 이어 붙인다.
        out.append("".join(n.text or "" for n in si.iter() if _tag(n.tag) == "t"))
    return out


def _is_date_code(code: str) -> bool:
    return bool(_DATE_TOKEN_RE.search(_LITERAL_RE.sub("", code or "")))


def _date_styles(zf: zipfile.ZipFile, budget: _Budget) -> Dict[int, bool]:
    """cellXfs 인덱스 -> 그 스타일이 숫자를 날짜로 보여 주는가."""
    if "xl/styles.xml" not in set(zf.namelist()):
        return {}
    root = _xml(_read_entry(zf, "xl/styles.xml", budget), "styles")

    custom: Dict[int, bool] = {}
    for node in root:
        if _tag(node.tag) != "numFmts":
            continue
        for nf in node:
            if _tag(nf.tag) != "numFmt":
                continue
            try:
                custom[int(nf.get("numFmtId", "-1"))] = _is_date_code(
                    nf.get("formatCode", ""))
            except ValueError:
                continue

    styles: Dict[int, bool] = {}
    for node in root:
        if _tag(node.tag) != "cellXfs":
            continue
        for i, xf in enumerate(n for n in node if _tag(n.tag) == "xf"):
            try:
                fid = int(xf.get("numFmtId", "0"))
            except ValueError:
                fid = 0
            styles[i] = custom.get(fid, fid in _BUILTIN_DATE_IDS)
    return styles


def serial_to_iso(serial: float, date1904: bool = False) -> Optional[str]:
    """엑셀 날짜 시리얼 -> 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM:SS'.

    1900 체계에서 시리얼 60은 존재하지 않는 1900-02-29이고, 그 위의 모든
    시리얼은 실제 달력보다 하루 밀려 있다(엑셀이 일부러 남긴 호환 버그).
    """
    if not math.isfinite(serial):
        return None
    if date1904:
        epoch, days = _dt.datetime(1904, 1, 1), serial
    elif serial >= 61:
        epoch, days = _dt.datetime(1899, 12, 31), serial - 1
    elif serial == 60:
        return "1900-02-29"     # 엑셀이 실제로 표시하는 값을 그대로 보존
    else:
        epoch, days = _dt.datetime(1899, 12, 31), serial
    try:
        dt = epoch + _dt.timedelta(days=days)
    except (OverflowError, ValueError):
        return None
    # 시리얼은 float이라 23:40:00이 23:39:59.999999로 저장된다. 엑셀은 반올림한
    # 값을 보여 주므로, 잘라 버리면 하루 귀속이 통째로 바뀔 수 있다(자정 근처).
    if dt.microsecond:
        try:
            dt = (dt + _dt.timedelta(microseconds=500_000)).replace(microsecond=0)
        except (OverflowError, ValueError):
            return None
    if dt.hour or dt.minute or dt.second:
        return dt.isoformat(sep=" ", timespec="seconds")
    return dt.strftime("%Y-%m-%d")


def _number_text(raw: str) -> str:
    """숫자 셀을 군더더기 없이 ('42.0' -> '42'). 비유한값은 원문 그대로."""
    try:
        v = float(raw)
    except ValueError:
        return raw
    if not math.isfinite(v):
        return raw
    if v == int(v) and abs(v) < 1e16:
        return str(int(v))
    return repr(v)


def _cell_text(cell: ET.Element, strings: List[str],
               styles: Dict[int, bool], date1904: bool) -> str:
    ctype = cell.get("t") or "n"
    vtext: Optional[str] = None
    inline: Optional[str] = None
    for child in cell:
        name = _tag(child.tag)
        if name == "v":
            vtext = child.text or ""
        elif name == "is":
            inline = "".join(n.text or "" for n in child.iter()
                             if _tag(n.tag) == "t")

    if ctype == "inlineStr":
        return (inline or "").strip()
    if ctype == "s":
        try:
            return strings[int(vtext or "")].strip()
        except (ValueError, IndexError):
            return ""
    if ctype in ("str", "e"):        # 수식 캐시 결과 / 오류 텍스트(#N/A 등)
        return (vtext or "").strip()
    if ctype == "b":
        return "TRUE" if (vtext or "").strip() in ("1", "true", "TRUE") else "FALSE"

    if vtext is None or not vtext.strip():
        return ""
    try:
        style_idx = int(cell.get("s") or "-1")
    except ValueError:
        style_idx = -1
    if styles.get(style_idx):
        try:
            iso = serial_to_iso(float(vtext), date1904)
        except ValueError:
            iso = None
        if iso is not None:
            return iso
    return _number_text(vtext.strip())


def read_sheet(path: str, sheet: Optional[str] = None) -> List[List[str]]:
    """워크시트 하나를 문자열 행 목록으로 읽는다(빈 셀 -> '').

    `sheet`는 이름, 또는 1-기반 인덱스를 담은 정수 문자열("2"). 생략하면 첫 시트.
    """
    budget = _Budget()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise XlsxError(
            f"'{path}' 은(는) 올바른 엑셀(.xlsx) 파일이 아닙니다. "
            "구형 .xls 라면 엑셀에서 .xlsx 또는 'CSV UTF-8'로 저장하세요.")
    with zf:
        sheets, date1904 = _workbook(zf, budget)
        if not sheets:
            raise XlsxError(f"'{path}' 에 워크시트가 없습니다.")

        target = None
        if sheet is None:
            target = sheets[0][1]
        else:
            wanted = sheet.strip()
            for name, tgt in sheets:
                if name == wanted:
                    target = tgt
                    break
            # 순수 ASCII 정수만 인덱스로 본다. str.isdigit()은 '²'에도 True라서
            # int()가 뒤늦게 터진다.
            if target is None and _INDEX_RE.match(wanted):
                idx = int(wanted)
                if 1 <= idx <= len(sheets):
                    target = sheets[idx - 1][1]
            if target is None:
                raise XlsxError(
                    f"시트 '{sheet}' 을(를) 찾을 수 없습니다. "
                    f"이 파일의 시트: {', '.join(n for n, _ in sheets)}")

        strings = _shared_strings(zf, budget)
        styles = _date_styles(zf, budget)
        data = _read_entry(zf, target, budget)

    root = _xml(data, "worksheet")
    rows: List[List[str]] = []
    for sheetdata in root.iter():
        if _tag(sheetdata.tag) != "sheetData":
            continue
        for row in sheetdata:
            if _tag(row.tag) != "row":
                continue
            if len(rows) >= _MAX_ROWS:
                raise XlsxError("엑셀 시트의 행이 너무 많습니다.")
            cells: Dict[int, str] = {}
            running = 0
            for cell in row:
                if _tag(cell.tag) != "c":
                    continue
                col = _column_of(cell.get("r") or "")
                if col is None:          # r= 를 생략하는 생성기 폴백
                    col = running
                running = col + 1
                if col >= _MAX_COLS:
                    continue
                text = _cell_text(cell, strings, styles, date1904)
                if text:
                    cells[col] = text
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)]
                        if cells else [])
        break

    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]
