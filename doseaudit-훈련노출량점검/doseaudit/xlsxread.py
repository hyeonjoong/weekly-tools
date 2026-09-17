"""표준 라이브러리만으로 .xlsx 를 읽는다 (pandas·openpyxl 없이).

`.xlsx` 는 XML 몇 장이 든 zip 이다. 이 툴이 필요한 것은 "시트 이름과 셀의
문자열 값"뿐이므로 서식·수식·차트는 전부 무시한다.

읽기 전용이다. 이 모듈에는 쓰기 함수가 없다 (`test_강제장치.py` 가 AST 로 고정).
"""

import os
import zipfile
import xml.etree.ElementTree as ET

from doseaudit.errors import ReadError

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_RNS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: zip 폭탄 방어 — 압축 해제 총량 상한(바이트)과 시트 하나의 상한.
#: 실측 워크북은 전부 1 MB 미만이고, 확증임상에서 N 이 10배가 되어도 여유가 크다.
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_MEMBER_UNCOMPRESSED = 128 * 1024 * 1024
MAX_ROWS_PER_SHEET = 1_000_000
#: 엑셀의 최대 열은 XFD = 16384. 셀 참조 `r="AAAAAAA3"` 하나로 임의 크기 할당을
#: 시킬 수 있으므로(1.5 KB 파일이 3.9 GB 를 먹었다) 여기서 끊는다.
MAX_COLUMNS = 16_384
#: 시트 하나에서 만들어 낼 셀 총량의 상한. 열 번호만 막으면 `r="XFD1"` 을 3만 행에
#: 흩뿌려 16,384칸짜리 행을 3만 개 만들 수 있다(156 KB 파일 → 2.0 GB).
MAX_CELLS_PER_SHEET = 5_000_000


def _col_index(cell_ref):
    """`'AB12'` → 0-기반 열 번호 27. 열 문자가 없으면 -1."""
    n = 0
    seen = False
    for ch in cell_ref:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
            seen = True
        elif "a" <= ch <= "z":
            n = n * 26 + (ord(ch) - 96)
            seen = True
        else:
            break
    return n - 1 if seen else -1


def _text_of(elem):
    """`<is>`/`<si>` 아래 흩어진 `<t>` 조각을 이어 붙인다."""
    return "".join(t.text or "" for t in elem.iter(_NS + "t"))


def _read_shared_strings(zf):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return [_text_of(si) for si in root]


def _sheet_targets(zf):
    """워크북에 적힌 순서대로 `(시트이름, zip 내부 경로)` 목록을 돌려준다.

    시트 순서와 이름은 `xl/workbook.xml` 이, 실제 파일 위치는 rels 가 쥐고 있다.
    `sheet1.xml` 이 첫 시트라는 보장은 없으므로 rels 를 반드시 거친다.
    """
    try:
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except KeyError as exc:
        raise ReadError("워크북 관계 파일(xl/_rels/workbook.xml.rels)이 없습니다") from exc
    rels = {}
    for rel in rels_root.iter("{%s}Relationship" % _PKG_RNS):
        target = (rel.get("Target") or "").replace("\\", "/")
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        # `xl/worksheets/../worksheets/sheet1.xml` 같은 우회 경로를 정규화한다.
        target = os.path.normpath(target).replace(os.sep, "/")
        rels[rel.get("Id")] = target

    try:
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError as exc:
        raise ReadError("워크북 본문(xl/workbook.xml)이 없습니다") from exc

    names = zf.namelist()
    out = []
    for sheet in wb_root.iter(_NS + "sheet"):
        rid = sheet.get(_RNS + "id")
        target = rels.get(rid)
        if target is None or target not in names:
            # 시트가 선언돼 있는데 알맹이가 없다 — 추론하지 않고 못 읽었다고 말한다.
            raise ReadError(
                "시트 '%s' 의 내용을 워크북 안에서 찾지 못했습니다" % (sheet.get("name") or "?")
            )
        out.append((sheet.get("name") or "", target))
    if not out:
        raise ReadError("시트가 하나도 없습니다")
    return out


def _read_sheet(zf, path, shared):
    """한 시트를 `[(엑셀행번호, [셀문자열, ...]), ...]` 로 읽는다.

    빈 행은 건너뛴다. 행 번호는 **엑셀에 보이는 그대로**라 리포트에서 사람이
    바로 찾아갈 수 있다.
    """
    root = ET.fromstring(zf.read(path))
    data = root.find(_NS + "sheetData")
    if data is None:
        return []
    rows = []
    total_cells = 0
    for row_idx, row in enumerate(data.iter(_NS + "row")):
        if row_idx >= MAX_ROWS_PER_SHEET:
            raise ReadError("시트 행 수가 상한(%d)을 넘었습니다" % MAX_ROWS_PER_SHEET)
        cells = {}
        for cell in row.iter(_NS + "c"):
            col = _col_index(cell.get("r") or "")
            if col < 0:
                continue
            if col >= MAX_COLUMNS:
                raise ReadError("셀 참조의 열 번호가 엑셀 한계(XFD)를 넘었습니다")
            ctype = cell.get("t")
            value_el = cell.find(_NS + "v")
            inline_el = cell.find(_NS + "is")
            if ctype == "s" and value_el is not None:
                try:
                    value = shared[int(value_el.text or "0")]
                except (ValueError, IndexError):
                    value = ""
            elif ctype == "inlineStr" and inline_el is not None:
                value = _text_of(inline_el)
            elif value_el is not None:
                value = value_el.text or ""
            else:
                value = ""
            cells[col] = value
        if not cells or not any(v.strip() for v in cells.values()):
            continue
        width = max(cells) + 1
        total_cells += width
        if total_cells > MAX_CELLS_PER_SHEET:
            raise ReadError("시트가 만들어 내는 셀 수가 상한(%d)을 넘었습니다"
                            % MAX_CELLS_PER_SHEET)
        try:
            excel_row = int(row.get("r") or (row_idx + 1))
        except ValueError:
            excel_row = row_idx + 1
        rows.append((excel_row, [cells.get(i, "") for i in range(width)]))
    return rows


def read_workbook(path):
    """`.xlsx` 한 개를 `[(시트이름, [(행번호, 셀들), ...]), ...]` 로 읽는다.

    실패는 전부 `ReadError` 다 — 호출부가 '못 읽은 파일'로 자백에 싣는다.
    """
    if not os.path.isfile(path):
        raise ReadError("파일이 아닙니다")
    try:
        with zipfile.ZipFile(path) as zf:
            total = 0
            for info in zf.infolist():
                if info.file_size > MAX_MEMBER_UNCOMPRESSED:
                    raise ReadError("워크북 내부 파일이 비정상적으로 큽니다")
                total += info.file_size
                if total > MAX_TOTAL_UNCOMPRESSED:
                    raise ReadError("워크북 압축 해제 총량이 상한을 넘었습니다")
            shared = _read_shared_strings(zf)
            return [(name, _read_sheet(zf, target, shared))
                    for name, target in _sheet_targets(zf)]
    except ReadError:
        raise
    except zipfile.BadZipFile as exc:
        raise ReadError("xlsx(zip) 형식이 아닙니다 — .xls 구형식이면 엑셀에서 .xlsx 로 저장하세요") from exc
    except ET.ParseError as exc:
        raise ReadError("워크북 내부 XML 이 깨졌습니다") from exc
    except (OSError, KeyError, ValueError, RecursionError, MemoryError,
            OverflowError) as exc:
        raise ReadError("읽는 중 오류: %s" % type(exc).__name__) from exc
