"""`.xlsx` 를 표준 라이브러리(zipfile + ElementTree)만으로 읽습니다.

openpyxl 없이 읽는 이유는 의존성 0 을 지키기 위해서입니다. 서식·수식 결과·차트는
읽지 않고, **셀에 저장된 값**만 (시트, 행, 열) 좌표와 함께 뽑습니다. 그것이
수치 대조에 필요한 전부입니다.

`.xls`(구형 바이너리), 암호가 걸린 워크북, 손상된 파일은 **읽지 못했다고 자백**합니다.
"""

import io
import re
import xml.etree.ElementTree as ET
from typing import Iterator, List, Optional, Tuple

from . import zipsafe

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL_DOC = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_REL_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
MAX_CELLS_PER_SHEET = 400000


def read_xlsx(path: str, problems: Optional[List[str]] = None
              ) -> Iterator[Tuple[str, int, str, str]]:
    """(시트명, 행번호, 열문자, 셀문자열) 을 순서대로 내보냅니다.

    읽지 못한 시트가 있으면 `problems` 에 사유를 적습니다 — 워크북 전체를 버리는
    것보다 낫고, 조용히 빠뜨리는 것보다는 훨씬 낫습니다.
    """
    problems = problems if problems is not None else []
    zf = zipsafe.open_zip(path)
    try:
        names = set(zf.namelist())
        shared = _shared_strings(zf, names)
        for sheet_name, member in _sheets(zf, names):
            if member not in names:
                problems.append("시트 '%s' 의 내용이 파일 안에 없음" % sheet_name)
                continue
            data = zipsafe.guard_xml(zipsafe.read_member(zf, member), member)
            for row, col, text in _iter_sheet(data, shared):
                yield sheet_name, row, col, text
    finally:
        zf.close()


def _shared_strings(zf, names) -> List[str]:
    member = "xl/sharedStrings.xml"
    if member not in names:
        return []
    data = zipsafe.guard_xml(zipsafe.read_member(zf, member), member)
    out: List[str] = []
    try:
        for _, node in ET.iterparse(io.BytesIO(data), events=("end",)):
            if node.tag == NS_MAIN + "si":
                out.append("".join(t.text or "" for t in node.iter(NS_MAIN + "t")))
                node.clear()
    except ET.ParseError:
        raise zipsafe.ArchiveError("sharedStrings.xml 이 손상됨")
    return out


def _sheets(zf, names) -> List[Tuple[str, str]]:
    """워크북에 선언된 순서대로 (시트명, 내부 경로)."""
    member = "xl/workbook.xml"
    if member not in names:
        sheets = sorted(n for n in names
                        if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        if not sheets:
            # 암호가 걸린 워크북은 본문이 통째로 `EncryptedPackage` 한 덩어리입니다.
            # 여기서 조용히 빈 결과를 돌려주면 '읽었다'고 거짓말하게 됩니다.
            raise zipsafe.ArchiveError(
                "워크시트를 찾지 못함(암호가 걸렸거나 엑셀 워크북이 아님)")
        return [(n.rsplit("/", 1)[-1][:-4], n) for n in sheets]
    data = zipsafe.guard_xml(zipsafe.read_member(zf, member), member)
    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError:
        raise zipsafe.ArchiveError("workbook.xml 이 손상됨")
    rels = _workbook_rels(zf, names)
    out: List[Tuple[str, str]] = []
    fallback = 0
    for node in root.iter(NS_MAIN + "sheet"):
        name = node.get("name") or "Sheet"
        rid = node.get(NS_REL_DOC + "id")
        target = rels.get(rid) if rid else None
        if not target:
            fallback += 1
            target = "xl/worksheets/sheet%d.xml" % fallback
        out.append((name, target))
    return out


def _workbook_rels(zf, names) -> dict:
    member = "xl/_rels/workbook.xml.rels"
    if member not in names:
        return {}
    data = zipsafe.guard_xml(zipsafe.read_member(zf, member), member)
    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError:
        return {}
    out = {}
    for node in root.iter(NS_REL_PKG + "Relationship"):
        target = node.get("Target") or ""
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target.lstrip("./")
        out[node.get("Id")] = target
    return out


def _iter_sheet(data: bytes, shared: List[str]):
    seen = 0
    row_no = 0
    try:
        for _, node in ET.iterparse(io.BytesIO(data), events=("end",)):
            if node.tag == NS_MAIN + "row":
                node.clear()
                continue
            if node.tag != NS_MAIN + "c":
                continue
            seen += 1
            if seen > MAX_CELLS_PER_SHEET:
                node.clear()
                raise zipsafe.ArchiveError("시트의 셀이 상한(%d)을 넘음" % MAX_CELLS_PER_SHEET)
            ref = node.get("r") or ""
            match = _CELL_REF.match(ref)
            if match:
                col, row_no = match.group(1), int(match.group(2))
            else:
                col = "?"
            text = _cell_text(node, shared)
            node.clear()
            if text:
                yield row_no, col, text
    except ET.ParseError:
        raise zipsafe.ArchiveError("시트 XML 이 손상됨")


def _cell_text(node, shared: List[str]) -> str:
    kind = node.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in node.iter(NS_MAIN + "t")).strip()
    value = node.find(NS_MAIN + "v")
    if value is None or value.text is None:
        return ""
    raw = value.text.strip()
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind == "e":
        return ""                       # #DIV/0! 같은 오류 셀
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw
