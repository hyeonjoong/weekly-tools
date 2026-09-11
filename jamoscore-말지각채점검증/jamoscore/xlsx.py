"""stdlib 만으로 XLSX 를 읽는 최소 리더 (읽기 전용).

openpyxl·pandas 를 쓰지 않는 이유는 두 가지다. (1) 이 저장소의 툴은 의존성 0 을
원칙으로 한다. (2) 이 툴은 **특정 시트를 아예 열지 않아야** 한다(식별정보 시트).
범용 로더는 통째로 읽어 버리므로 그 보장을 코드로 만들 수 없다.

쓰기 기능은 의도적으로 없다. 원본 워크북은 어떤 경로로도 수정되지 않는다.

한 가지 정직하게 적어 둘 것: 엑셀은 **모든 시트의 문자열을 하나의 공유 테이블**
(``xl/sharedStrings.xml``)에 담는다. 따라서 "시트를 열지 않았다"는 말은 그 시트의
셀 좌표·수식·서식을 읽지 않았다는 뜻이고, 문자열 자체는 공유 테이블을 통해 메모리에
올라온다. 산출물로는 나가지 않는다(인덱스를 푸는 것은 연 시트뿐이다).
"""

from __future__ import annotations

import re
import zipfile
from typing import Dict, Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: 압축 폭탄 방어 — 시트 하나의 압축 해제 크기 상한.
MAX_MEMBER_BYTES = 400 * 1024 * 1024
#: 전체 압축 해제 크기 상한.
MAX_TOTAL_BYTES = 1200 * 1024 * 1024
#: 시트 하나에서 읽을 최대 행 수(그 이상은 잘라내고 **사실을 보고한다**).
MAX_ROWS = 200_000


_COL_RE = re.compile(r"^([A-Z]+)(\d*)$")


class XlsxError(Exception):
    """워크북을 읽을 수 없을 때. CLI 가 한국어 메시지로 바꿔 exit 2 를 낸다."""


def column_index(ref: str) -> int:
    """``"BC12"`` → 55 (1-based 열 번호)."""
    m = _COL_RE.match(ref.upper())
    if not m:
        raise XlsxError("셀 주소를 알아볼 수 없습니다: %r" % (ref,))
    num = 0
    for ch in m.group(1):
        num = num * 26 + (ord(ch) - 64)
    return num


def column_name(index: int) -> str:
    """1 → ``"A"``, 55 → ``"BC"``. 리포트에서 사람이 엑셀을 열어 찾아갈 때 쓴다."""
    if index < 1:
        raise ValueError("열 번호는 1 이상이어야 합니다")
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _safe_read(zf: zipfile.ZipFile, name: str, budget: List[int]) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise XlsxError("워크북 안에 %s 가 없습니다 — 손상된 파일로 보입니다." % name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise XlsxError(
            "워크북 내부 파일이 너무 큽니다(%s, %.0f MB) — 압축 폭탄 방어로 중단합니다."
            % (name, info.file_size / 1048576)
        )
    budget[0] -= info.file_size
    if budget[0] < 0:
        raise XlsxError("워크북 전체 압축 해제 크기가 상한을 넘었습니다 — 중단합니다.")
    data = zf.read(name)
    # **전체**를 본다. 앞 N바이트만 보면 그만큼의 주석으로 밀어내 우회할 수 있다
    # (라운드 1에서 4KB → 4MB 로 넓혔더니 5MB 주석으로 다시 뚫렸다).
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise XlsxError("워크북 XML 에 DTD/엔티티 선언이 있습니다 — 안전을 위해 거절합니다.")
    return data


def _parse_xml(data: bytes, name: str) -> ET.Element:
    """XML 파싱 실패를 트레이스백 대신 한국어 :class:`XlsxError` 로 바꾼다.

    동기화가 덜 된 클라우드 드라이브 파일에서 실제로 일어난다.
    """
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XlsxError(
            "워크북 내부 XML(%s)이 손상되어 읽을 수 없습니다: %s\n"
            "  클라우드 드라이브 동기화가 끝났는지 확인하고, 엑셀에서 한 번 "
            "열었다가 '다른 이름으로 저장' 해 보세요." % (name, exc))


class Workbook:
    """열린 XLSX 하나. 시트 이름 목록만 먼저 주고, 본문은 요청할 때만 읽는다."""

    def __init__(self, path: str):
        self.path = path
        try:
            self._zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise XlsxError(
                "XLSX 로 열리지 않습니다. .xls(구형)·.numbers 라면 엑셀에서 "
                "'.xlsx'로 다시 저장한 뒤 넣어 주세요."
            )
        except OSError as exc:
            raise XlsxError("파일을 열 수 없습니다: %s" % exc)
        self._budget = [MAX_TOTAL_BYTES]
        self._shared: Optional[List[str]] = None
        self._targets: Dict[str, str] = {}
        self.sheet_names: List[str] = []
        #: 이름이 겹쳐 읽지 못한 시트(두 번째 이후).
        self.duplicate_sheets: List[str] = []
        #: ``{시트: 잘라낸 행 수}`` — MAX_ROWS 를 넘어 읽지 못한 행.
        self.truncated: Dict[str, int] = {}
        self._load_index()

    # -- 인덱스 -------------------------------------------------------
    def _load_index(self) -> None:
        wb = _parse_xml(_safe_read(self._zf, "xl/workbook.xml", self._budget),
                        "xl/workbook.xml")
        rels = _parse_xml(
            _safe_read(self._zf, "xl/_rels/workbook.xml.rels", self._budget),
            "xl/_rels/workbook.xml.rels")
        relmap = {c.get("Id"): c.get("Target") for c in rels}
        sheets = wb.find(MAIN_NS + "sheets")
        if sheets is None:
            raise XlsxError("워크북에 시트 목록이 없습니다.")
        for node in sheets:
            name = node.get("name")
            rid = node.get(REL_NS + "id")
            target = relmap.get(rid)
            if name is None or target is None:
                continue
            if name in self._targets:
                # 같은 이름의 시트가 둘 이상 — 엑셀은 허용하지 않지만 생성기가 만들 수
                # 있다. 조용히 버리지 않고 기록해 두고 리포트에서 자백한다.
                self.duplicate_sheets.append(name)
                continue
            self._targets[name] = self._normalize_target(target)
            self.sheet_names.append(name)

    @staticmethod
    def _normalize_target(target: str) -> str:
        target = target.replace("\\", "/")
        if target.startswith("/"):
            return target.lstrip("/")
        if target.startswith("xl/"):
            return target
        return "xl/" + target.lstrip("./")

    # -- 공유 문자열 ---------------------------------------------------
    def _shared_strings(self) -> List[str]:
        if self._shared is not None:
            return self._shared
        if "xl/sharedStrings.xml" not in self._zf.namelist():
            self._shared = []
            return self._shared
        # 파싱에 실패하면 **캐시하지 않는다.** 빈 목록을 캐시하면 그 뒤로 모든
        # 시트가 문자열 셀을 조용히 잃고, 정상 시트가 '블록 없음'으로 건너뛰어진다.
        root = _parse_xml(
            _safe_read(self._zf, "xl/sharedStrings.xml", self._budget),
            "xl/sharedStrings.xml")
        shared: List[str] = []
        for si in root:
            # <rPh> 는 일본어 후리가나로 본문이 아니다. 본문 <t> 만 모은다.
            parts = []
            for child in si:
                if child.tag == MAIN_NS + "t":
                    parts.append(child.text or "")
                elif child.tag == MAIN_NS + "r":
                    for sub in child:
                        if sub.tag == MAIN_NS + "t":
                            parts.append(sub.text or "")
            shared.append("".join(parts))
        self._shared = shared
        return self._shared

    # -- 시트 본문 -----------------------------------------------------
    def has_sheet(self, name: str) -> bool:
        return name in self._targets

    def read_sheet(self, name: str) -> Dict[int, Dict[int, str]]:
        """``{행번호: {열번호: 문자열}}``. 빈 셀은 아예 넣지 않는다.

        이 메서드를 **부르지 않으면 그 시트의 바이트는 읽히지 않는다** — 식별정보
        시트를 '열지 않았다'고 말할 수 있는 근거다.
        """
        if name not in self._targets:
            raise XlsxError("시트 %r 가 없습니다." % (name,))
        shared = self._shared_strings()
        data = _safe_read(self._zf, self._targets[name], self._budget)
        root = _parse_xml(data, self._targets[name])
        rows: Dict[int, Dict[int, str]] = {}
        auto_row = 0
        skipped = 0
        for row in root.iter(MAIN_NS + "row"):
            raw_r = row.get("r")
            if raw_r and raw_r.isdigit():
                rnum = int(raw_r)
            else:
                rnum = auto_row + 1
            auto_row = rnum
            if rnum > MAX_ROWS:
                # 행 하나가 거대한 r 값을 들고 있다고 시트 전체를 버리면 안 된다
                # (`<row r="999999999">` 하나로 시트가 통째로 사라지던 사고).
                # **셀이 실제로 있는 행만** 잃어버린 것으로 센다 — 엑셀이 아래쪽에
                # 남기는 빈 서식 행까지 세면 멀쩡한 파일이 '판정 불가'가 된다.
                if any(c.tag == MAIN_NS + "c" and _has_value(c) for c in row):
                    skipped += 1
                continue
            cells: Dict[int, str] = {}
            auto_col = 0
            for cell in row:
                if cell.tag != MAIN_NS + "c":
                    continue
                ref = cell.get("r")
                if ref:
                    try:
                        cnum = column_index(ref)
                    except XlsxError:
                        cnum = auto_col + 1
                else:
                    cnum = auto_col + 1
                auto_col = cnum
                text = self._cell_text(cell, shared)
                if text is not None and text != "":
                    cells[cnum] = text
            if cells:
                rows[rnum] = cells
        if skipped:
            self.truncated[name] = skipped
        return rows

    @staticmethod
    def _cell_text(cell: ET.Element, shared: List[str]) -> Optional[str]:
        ctype = cell.get("t")
        if ctype == "inlineStr":
            node = cell.find(MAIN_NS + "is")
            if node is None:
                return None
            return "".join(t.text or "" for t in node.iter(MAIN_NS + "t"))
        value = cell.find(MAIN_NS + "v")
        if value is None or value.text is None:
            return None
        raw = value.text
        if ctype == "s":
            try:
                return shared[int(raw)]
            except (ValueError, IndexError):
                return None
        if ctype == "b":
            return "TRUE" if raw.strip() == "1" else "FALSE"
        if ctype == "e":
            # #DIV/0! 등 수식 오류. 숨기지 않고 그대로 올려보낸다.
            return raw
        return raw

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> "Workbook":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _has_value(cell: ET.Element) -> bool:
    if cell.get("t") == "inlineStr":
        return cell.find(MAIN_NS + "is") is not None
    value = cell.find(MAIN_NS + "v")
    return value is not None and bool((value.text or "").strip())


def iter_cells(rows: Dict[int, Dict[int, str]]) -> Iterator[Tuple[int, int, str]]:
    for rnum in sorted(rows):
        for cnum in sorted(rows[rnum]):
            yield rnum, cnum, rows[rnum][cnum]
