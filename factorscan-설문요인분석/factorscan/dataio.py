"""CSV 로딩 · 문항 선택 · 역문항 처리 · 결측(NaN) 처리."""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np


class DataError(Exception):
    """데이터 로딩/전처리 관련 오류."""


# 결측으로 간주할 문자열(대소문자 무시)
_NA_STRINGS = {"", "na", "n/a", "nan", "null", "none", ".", "-", "missing"}

# 자릿수 구분 쉼표(en-US/한국식)를 '명확한' 경우에만 인식한다:
#  (A) 소수점이 있으면 쉼표는 자릿수구분이 확실  예: "1,000.5", "12,345.60"
#  (B) 쉼표 그룹이 2개 이상이면 확실           예: "1,000,000"
# 단일 그룹·소수점 없음("1,000" vs 유럽식 "1,234"=1.234)은 모호하므로 건드리지 않고
# 결측(강제변환 경고)으로 남겨 조용한 1000배 오독을 막는다.
_THOUSANDS = re.compile(
    r"^[+-]?\d{1,3}(,\d{3})+\.\d+$"      # (A) 그룹+소수
    r"|^[+-]?\d{1,3}(,\d{3}){2,}$")      # (B) 2그룹 이상


@dataclass
class Dataset:
    names: List[str]        # 문항(열) 이름
    data: np.ndarray        # (n_raw, p) 실수 행렬, 결측은 NaN
    # 숫자로 해석되지 않아 결측처리된 비어있지 않은 토큰 수(문항명 -> 개수).
    coercion: Dict[str, int] = field(default_factory=dict)
    # 자동선택에서 제외된 후보 열(열이름 -> 사유). 조용히 사라지지 않게 보고용으로 남긴다.
    dropped: Dict[str, str] = field(default_factory=dict)


def _to_float(token: str, extra_na: Sequence[str]) -> float:
    t = token.strip()
    if t.lower() in _NA_STRINGS or t in extra_na:
        return np.nan
    if _THOUSANDS.match(t):
        t = t.replace(",", "")  # 자릿수 구분 쉼표 제거 후 파싱
    try:
        return float(t)
    except ValueError:
        return np.nan


def _is_na_token(token: str, extra_na: Sequence[str]) -> bool:
    t = token.strip()
    return t.lower() in _NA_STRINGS or t in extra_na


def load_csv(path: str, na_values: Optional[Sequence[str]] = None,
             encoding: str = "utf-8-sig", delimiter: str = ",") -> Dict[str, np.ndarray]:
    """CSV/TSV를 열 이름 -> 열 벡터(object) 로 읽는다. 숫자 변환은 뒤에서 수행.

    encoding 기본값은 BOM을 자동 제거하는 utf-8-sig. 한국어 엑셀이 흔히 쓰는
    CP949/EUC-KR 파일은 decode에 실패하므로 친절한 안내와 함께 오류를 낸다.
    delimiter로 탭 구분(TSV) 등 다른 구분자도 읽는다.
    """
    try:
        with open(path, newline="", encoding=encoding) as fh:
            return _read_rows(fh, delimiter=delimiter)
    except LookupError:
        raise DataError(f"알 수 없는 인코딩입니다: '{encoding}'. 예: utf-8, cp949, euc-kr.")
    except UnicodeDecodeError as exc:
        raise DataError(
            f"파일을 '{encoding}'로 읽을 수 없습니다({exc.reason}). "
            f"한국어 엑셀에서 저장한 CP949/EUC-KR 파일일 수 있습니다 — "
            f"--encoding cp949 (또는 euc-kr)로 다시 시도하거나 UTF-8로 저장하세요. "
            f"엑셀 .xlsx 파일이라면 그대로 넘기면 자동으로 읽습니다.")


# ---------------------------------------------------------------- xlsx 읽기
_SSML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_SREL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# 엑셀의 실제 최대 열 수(XFD = 16384). 이 범위를 넘는 참조는 손상/악의적 파일이다.
_XLSX_MAX_COL = 16384


def _col_index(ref: str) -> int:
    """셀 참조("BC12")의 열 문자 부분을 0-based 열 번호로. 'A'->0, 'Z'->25, 'AA'->26.

    ASCII 문자만 자릿수로 인정하고 상한(XFD)을 넘으면 거부한다. ch.isalpha()로 받으면
    한글·CJK 확장 문자까지 자릿수가 되어(예: '𪘀1' → 195036) 행 하나를 20만 칸으로
    조밀 전개하게 된다 — 3KB 파일로 1GB를 먹는 메모리 증폭이 된다.
    """
    n = 0
    for ch in ref:
        if not ("A" <= ch <= "Z" or "a" <= ch <= "z"):
            break
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
        if n > _XLSX_MAX_COL:
            raise DataError(
                f"엑셀 셀 참조 '{ref}'의 열 번호가 최대치(XFD)를 넘습니다 — "
                f"파일이 손상되었거나 올바른 .xlsx가 아닙니다.")
    if n == 0:
        # 열 문자가 하나도 없는 참조(예: '1', '가1')는 규격 위반이다. 조용히 넘기면
        # 셀이 통째로 사라져 '빈 파일'처럼 보이므로 원인을 밝힌다.
        raise DataError(
            f"엑셀 셀 참조 '{ref}'를 해석할 수 없습니다(열 문자 없음) — "
            f"파일이 손상되었거나 올바른 .xlsx가 아닙니다.")
    return n - 1


def _si_text(si) -> str:
    """<si>/<is> 요소의 표시 텍스트. 리치텍스트(<r>)는 이어붙이고 <rPh>는 제외한다.

    <rPh>는 한중일 엑셀이 자동 생성하는 '음성 안내(후리가나/루비)' 서브트리다. 단순히
    si.iter('t')로 훑으면 이 루비 텍스트까지 열 이름에 섞여 'Q1' 이 'Q1PHONETIC'이 된다
    (한국어 엑셀에서 실제로 발생) — 직접 자식과 <r> 안의 <t>만 읽는다.
    """
    parts: List[str] = []
    for child in si:
        tag = child.tag
        if tag == f"{_SSML}t":
            parts.append(child.text or "")
        elif tag == f"{_SSML}r":            # 리치텍스트 런
            for t in child.findall(f"{_SSML}t"):
                parts.append(t.text or "")
        # f"{_SSML}rPh"(음성 안내)와 f"{_SSML}phoneticPr"은 표시 텍스트가 아니므로 건너뜀
    return "".join(parts)


# 압축을 푼 뒤의 XML 한 조각이 이보다 크면 읽지 않는다. 정상적인 설문 시트는 수 MB를
# 넘지 않으며, 이 상한이 없으면 몇 MB짜리 압축폭탄이 수 GB로 부풀어 메모리를 고갈시킨다.
_XLSX_MAX_MEMBER_BYTES = 256 * 1024 * 1024


def _xlsx_read(zf, name: str) -> bytes:
    """zip 멤버를 크기 상한을 확인한 뒤 읽는다(압축폭탄 방어)."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise
    if info.file_size > _XLSX_MAX_MEMBER_BYTES:
        raise DataError(
            f"엑셀 내부 파일 '{name}'의 압축 해제 크기가 너무 큽니다"
            f"({info.file_size / 1e6:.0f}MB > {_XLSX_MAX_MEMBER_BYTES / 1e6:.0f}MB 상한) — "
            f"손상되었거나 비정상적으로 큰 파일입니다.")
    try:
        return zf.read(name)
    except (OverflowError, MemoryError) as exc:
        raise DataError(f"엑셀 내부 파일 '{name}'을 읽는 중 메모리가 부족했습니다: {exc}")


def _xlsx_shared_strings(zf) -> List[str]:
    """sharedStrings.xml의 문자열 테이블."""
    import xml.etree.ElementTree as ET
    try:
        data = _xlsx_read(zf, "xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    return [_si_text(si) for si in root.findall(f"{_SSML}si")]


# 엑셀 내장 날짜/시간 서식 ID(ECMA-376 18.8.30). 이 서식이 붙은 숫자 셀은 '날짜'다.
_BUILTIN_DATE_FMTS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})


def _is_date_format_code(code: str) -> bool:
    """사용자 지정 서식 코드가 날짜/시간인지. 리터럴/색상/따옴표 구간은 무시한다."""
    s = re.sub(r'\[[^\]]*\]', '', code or '')      # [빨강], [$-409] 등 제거
    s = re.sub(r'"[^"]*"', '', s)                  # "년" 같은 리터럴 제거
    s = re.sub(r'\\.', '', s)                      # 이스케이프 문자 제거
    return bool(re.search(r'[ymdhs]', s, re.I))


def _xlsx_date_styles(zf) -> set:
    """날짜 서식이 적용된 cellXfs 인덱스 집합(셀의 s= 속성이 가리키는 값)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(_xlsx_read(zf, "xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = {}
    for nf in root.iter(f"{_SSML}numFmt"):
        try:
            custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            continue
    out = set()
    xfs = root.find(f"{_SSML}cellXfs")
    if xfs is None:
        return out
    for i, xf in enumerate(xfs.findall(f"{_SSML}xf")):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            continue
        if fid in _BUILTIN_DATE_FMTS or (fid in custom and _is_date_format_code(custom[fid])):
            out.add(i)
    return out


def _serial_to_iso(value: str, date1904: bool) -> str:
    """엑셀 날짜 일련번호를 ISO 문자열로. 변환 불가면 원문을 그대로 돌려준다.

    숫자로 남겨 두면 '검사일' 같은 열이 43831 같은 리커트처럼 생긴 값으로 요인분석에
    들어간다(임상 엑셀에서 흔함). 날짜임을 드러내는 문자열로 바꿔 숫자 변환에서 걸러지게 한다.
    1900 체계는 엑셀의 가짜 윤일(1900-02-29) 때문에 기준일이 1899-12-30이다.
    """
    import datetime
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    # 엑셀 1900 체계는 존재하지 않는 1900-02-29(일련번호 60)를 날짜로 친다. 그래서
    # 60 이후는 하루가 밀려 있고(기준일 1899-12-30), 60 이전은 밀리지 않는다(1899-12-31).
    if not date1904 and f == 60:
        return "1900-02-29"         # 엑셀이 표시하는 값(실재하지 않는 날짜)을 그대로 둔다
    try:
        if date1904:
            dt = datetime.datetime(1904, 1, 1) + datetime.timedelta(days=f)
        elif f >= 61:
            dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=f)
        else:                       # 가짜 윤일 이전 구간: 1 → 1900-01-01
            dt = datetime.datetime(1899, 12, 31) + datetime.timedelta(days=f)
    except (OverflowError, ValueError):
        return value
    return dt.date().isoformat() if dt.time() == datetime.time(0, 0) else dt.isoformat(sep=" ")


def _xlsx_sheet_path(zf, sheet: Optional[str]) -> str:
    """워크북에서 대상 시트의 XML 경로를 찾는다(이름 지정 없으면 첫 시트)."""
    import xml.etree.ElementTree as ET
    try:
        wb = ET.fromstring(_xlsx_read(zf, "xl/workbook.xml"))
        rels = ET.fromstring(_xlsx_read(zf, "xl/_rels/workbook.xml.rels"))
    except KeyError:
        raise DataError("엑셀 파일 구조가 올바르지 않습니다(workbook.xml 없음). "
                        "정말 .xlsx 파일인지 확인하세요(.xls 구형식은 지원하지 않습니다).")
    rel_map = {r.get("Id"): r.get("Target") for r in rels}
    sheets = wb.find(f"{_SSML}sheets")
    entries = list(sheets) if sheets is not None else []
    if not entries:
        raise DataError("엑셀 파일에 시트가 없습니다.")
    names = [e.get("name") for e in entries]
    if sheet is None:
        target = entries[0]
    else:
        found = [e for e in entries if e.get("name") == sheet]
        if not found:
            raise DataError(f"'{sheet}' 시트를 찾을 수 없습니다. 이 파일의 시트: {', '.join(names)}")
        target = found[0]
    rid = target.get(f"{_SREL}id")
    path = rel_map.get(rid)
    if not path:
        raise DataError(f"시트 '{target.get('name')}'의 데이터를 찾을 수 없습니다.")
    path = path.lstrip("/")
    return path if path.startswith("xl/") else f"xl/{path}"


def load_xlsx(path: str, sheet: Optional[str] = None) -> Dict[str, np.ndarray]:
    """엑셀 .xlsx 파일의 한 시트를 열 이름 -> 열 벡터(object) 로 읽는다.

    임상 설문 자료는 CSV보다 엑셀로 오는 일이 훨씬 많아, CSV로 다시 저장하는 단계를
    없애려고 표준 라이브러리(zipfile + ElementTree)만으로 직접 파싱한다(pandas/openpyxl 불필요).
    첫 행을 헤더로 보고, 이후는 load_csv와 동일한 자료구조를 돌려주므로 뒤 단계가 그대로 붙는다.

    수식 셀은 계산된 값(<v>)을 쓴다. 값이 캐시되어 있지 않으면 결측으로 남는다.
    """
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DataError(
            f"엑셀(.xlsx) 파일로 열 수 없습니다: {path}. 파일이 손상되었거나, 확장자만 .xlsx인 "
            f"CSV/구형식(.xls)일 수 있습니다 — 엑셀에서 'Excel 통합 문서(.xlsx)'로 다시 저장하세요.")
    with zf:
        try:
            strings = _xlsx_shared_strings(zf)
            date_styles = _xlsx_date_styles(zf)
            date1904 = _xlsx_is_1904(zf)
            sheet_path = _xlsx_sheet_path(zf, sheet)
            root = ET.fromstring(_xlsx_read(zf, sheet_path))
        except ET.ParseError as exc:
            raise DataError(f"엑셀 파일의 XML을 해석할 수 없습니다: {exc}")
        except KeyError as exc:
            raise DataError(f"엑셀 파일에서 시트 데이터를 찾을 수 없습니다: {exc}")
        except (OverflowError, MemoryError) as exc:
            raise DataError(f"엑셀 파일이 너무 커서 읽을 수 없습니다: {exc}")

        rows: List[List[str]] = []
        for row in root.iter(f"{_SSML}row"):
            cells: Dict[int, str] = {}
            cursor = 0     # r 속성이 없는 셀의 위치(ECMA-376에서 r은 선택 사항)
            for c in row.findall(f"{_SSML}c"):
                ref = c.get("r") or ""
                if ref:
                    ci = _col_index(ref)
                else:
                    # len(cells)를 쓰면 희소 행에서 이미 채워진 열을 덮어써 값이 조용히
                    # 사라진다(A,C 다음의 r-없는 셀이 C를 덮어씀). 진행 커서를 따로 둔다.
                    ci = cursor
                if ci < 0:
                    continue
                cursor = ci + 1
                t = c.get("t")
                if t == "inlineStr":
                    is_el = c.find(f"{_SSML}is")
                    val = _si_text(is_el) if is_el is not None else ""
                else:
                    v = c.find(f"{_SSML}v")
                    val = v.text if v is not None and v.text is not None else ""
                    if t == "s":   # 공유 문자열 인덱스
                        try:
                            val = strings[int(val)]
                        except (ValueError, IndexError):
                            raise DataError(
                                "엑셀 파일의 공유 문자열 테이블이 손상되었습니다"
                                f"(참조 인덱스 '{val}'가 범위를 벗어남). 엑셀에서 다시 저장해 보세요.")
                    elif t is None or t == "n":
                        # 날짜 서식이 붙은 숫자 셀은 일련번호(43831)가 아니라 날짜다.
                        try:
                            sidx = int(c.get("s") or -1)
                        except ValueError:
                            sidx = -1
                        if sidx in date_styles and val != "":
                            val = _serial_to_iso(val, date1904)
                if ci in cells:
                    raise DataError(
                        f"엑셀 행 {row.get('r') or '?'}에 같은 열을 가리키는 셀이 둘 이상입니다 — "
                        f"파일이 손상되었을 수 있습니다.")
                cells[ci] = val
            if not cells:
                rows.append([])
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])

    return _rows_to_columns(rows)


def _xlsx_is_1904(zf) -> bool:
    """워크북이 1904 날짜 체계(구 Mac 엑셀)를 쓰는지."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(_xlsx_read(zf, "xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return False
    pr = root.find(f"{_SSML}workbookPr")
    if pr is None:
        return False
    return (pr.get("date1904") or "").lower() in ("1", "true")


def _rows_to_columns(rows: List[List[str]]) -> Dict[str, np.ndarray]:
    """(헤더 포함) 행 리스트를 열 딕셔너리로. CSV 경로와 같은 검증을 적용한다."""
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise DataError("빈 파일입니다.")
    header = [str(h).strip() for h in rows[0]]
    # 엑셀은 뒤쪽에 빈 헤더 칸을 흘리는 일이 잦다 — 뒤의 빈 이름만 잘라낸다.
    while header and not header[-1]:
        header.pop()
    if not header:
        raise DataError("첫 행(헤더)에 열 이름이 없습니다.")
    if any(not h for h in header):
        raise DataError("헤더에 이름이 빈 열이 있습니다: 모든 열에 이름을 넣어 주세요.")
    if len(set(header)) != len(header):
        raise DataError("중복된 열 이름이 있습니다: 열 이름을 고유하게 만들어 주세요.")
    ncol = len(header)
    cols: List[List[str]] = [[] for _ in header]
    n_rows = 0
    for lineno, row in enumerate(rows[1:], start=2):
        if len(row) > ncol:
            if any(str(c).strip() for c in row[ncol:]):
                raise DataError(
                    f"{lineno}행의 열 개수({len(row)})가 헤더({ncol})보다 많습니다 — "
                    f"열 정렬이 어긋났을 수 있습니다. 파일을 확인하세요.")
            row = row[:ncol]
        elif len(row) < ncol:
            row = list(row) + [""] * (ncol - len(row))
        for i in range(ncol):
            cols[i].append(str(row[i]))
        n_rows += 1
    if n_rows == 0:
        raise DataError("데이터 행이 없습니다(헤더만 존재).")
    return {name: np.array(col, dtype=object) for name, col in zip(header, cols)}


def load_table(path: str, na_values: Optional[Sequence[str]] = None,
               encoding: str = "utf-8-sig", sheet: Optional[str] = None,
               delimiter: Optional[str] = None) -> Dict[str, np.ndarray]:
    """확장자로 형식을 판별해 CSV/TSV/XLSX를 읽는다(사용자가 형식을 신경 쓰지 않게).

    .xlsx/.xlsm → 엑셀, .tsv/.tab → 탭 구분, 그 외 → 쉼표(또는 delimiter 지정값).
    """
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return load_xlsx(path, sheet=sheet)
    if low.endswith(".xls"):
        raise DataError(
            "구형식 .xls 는 지원하지 않습니다 — 엑셀에서 'Excel 통합 문서(.xlsx)' 또는 "
            "'CSV UTF-8'로 다시 저장한 뒤 사용하세요.")
    if delimiter is None:
        delimiter = "\t" if low.endswith((".tsv", ".tab")) else ","
    return load_csv(path, na_values=na_values, encoding=encoding, delimiter=delimiter)


def _read_rows(fh, delimiter: str = ",") -> Dict[str, np.ndarray]:
    reader = csv.reader(fh, delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise DataError("빈 파일입니다.")
    header = [h.strip() for h in header]
    if len(set(header)) != len(header):
        raise DataError("중복된 열 이름이 있습니다: 열 이름을 고유하게 만들어 주세요.")
    ncol = len(header)
    cols: List[List[str]] = [[] for _ in header]
    n_rows = 0
    for lineno, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue  # 완전 빈 줄 건너뜀
        # 헤더보다 긴 행: 뒤쪽 빈 칸만 넘치는 것은 무해하므로 잘라내되,
        # 실제 값이 넘치면 열 정렬이 깨진 것이므로 조용히 버리지 않고 오류로 알린다.
        if len(row) > ncol:
            if any(cell.strip() for cell in row[ncol:]):
                raise DataError(
                    f"{lineno}행의 열 개수({len(row)})가 헤더({ncol})보다 많습니다 — "
                    f"열 정렬이 어긋났을 수 있습니다. 파일을 확인하세요.")
            row = row[:ncol]
        elif len(row) < ncol:
            row = row + [""] * (ncol - len(row))
        for i in range(ncol):
            cols[i].append(row[i])
        n_rows += 1
    if n_rows == 0:
        raise DataError("데이터 행이 없습니다(헤더만 존재).")
    return {name: np.array(col, dtype=object) for name, col in zip(header, cols)}


def select_items(columns: Dict[str, np.ndarray],
                 items: Optional[Sequence[str]] = None,
                 id_cols: Sequence[str] = (),
                 na_values: Optional[Sequence[str]] = None,
                 min_unique: int = 2) -> Dataset:
    """분석할 문항 열을 골라 실수 행렬로 변환.

    items가 주어지면 그 열만, 아니면 id_cols를 제외한 '숫자형' 열 전체를 사용한다.
    분산이 0(모두 동일값)이거나 유효값이 없는 열은 제외한다.
    """
    extra_na = list(na_values or [])
    all_names = list(columns.keys())

    if items:
        dups = [c for c in set(items) if list(items).count(c) > 1]
        if dups:
            raise DataError(f"--items/설정에 중복 지정된 문항: {', '.join(sorted(dups))}")
        missing = [c for c in items if c not in columns]
        if missing:
            raise DataError(f"CSV에 없는 문항 열: {', '.join(missing)}")
        candidates = list(items)
    else:
        candidates = [c for c in all_names if c not in set(id_cols)]

    names: List[str] = []
    vectors: List[np.ndarray] = []
    coercion: Dict[str, int] = {}
    dropped: Dict[str, str] = {}
    for name in candidates:
        raw = [str(v) for v in columns[name]]
        vec = np.array([_to_float(v, extra_na) for v in raw], dtype=float)
        # 비어있지 않은데 숫자로 못 읽어 결측이 된 토큰 수(오타·문자 혼입·이상한 구분자).
        bad = sum(1 for v, f in zip(raw, vec)
                  if not np.isfinite(f) and not _is_na_token(v, extra_na))
        finite = vec[np.isfinite(vec)]
        if items is None:
            # 자동 선택: 유효 숫자값이 거의 없으면(문자열/ID 열) 건너뜀.
            # 다만 '왜' 빠졌는지는 남긴다 — 숫자로 보이는데 파싱이 안 되는 열
            # (예: 자릿수구분 쉼표 "1,234", 캐시 없는 수식 셀)이 조용히 사라지면
            # 사용자는 문항 하나가 통째로 분석에서 빠진 걸 눈치채지 못한다.
            if finite.size < max(2, int(0.5 * vec.size)):
                if bad:
                    dropped[name] = f"숫자로 읽을 수 없는 값 {bad}개(유효값 {finite.size}개)"
                elif finite.size == 0:
                    dropped[name] = "유효한 숫자값이 없음"
                continue
            if finite.size and np.unique(finite).size < min_unique:
                # 상수 열은 상관계산 불가 → 자동선택에서 제외(사유는 남김)
                dropped[name] = "값이 모두 동일(분산 0)"
                continue
        else:
            if finite.size == 0:
                raise DataError(f"문항 '{name}'에 유효한 숫자값이 없습니다.")
            if np.unique(finite).size < min_unique:
                # 명시적으로 지정한 문항이 상수면 조용히 빠뜨리지 않고 오류로 알린다
                raise DataError(
                    f"문항 '{name}'은 값이 모두 동일(분산 0)하여 요인분석에 쓸 수 없습니다. "
                    f"입력 오류가 아닌지 확인하거나 --items 에서 제외하세요.")
        names.append(name)
        vectors.append(vec)
        if bad:
            coercion[name] = bad

    if not names:
        raise DataError("분석할 숫자형 문항 열을 찾지 못했습니다. --items 로 열을 지정해 보세요.")

    # 선택된 문항에 대해서만 변환 실패를 보고(자동선택에서 버려진 텍스트 열은 dropped로).
    coercion = {k: v for k, v in coercion.items() if k in names}
    return Dataset(names=names, data=np.column_stack(vectors), coercion=coercion,
                   dropped=dropped)


def reverse_range_violations(ds: Dataset, reverse: Sequence[str],
                             scale_min: float, scale_max: float) -> Dict[str, int]:
    """역문항 재점수화 전에 선언된 [min, max] 범위를 벗어나는 값의 개수를 문항별로 센다."""
    idx = {name: i for i, name in enumerate(ds.names)}
    out: Dict[str, int] = {}
    for r in reverse:
        if r not in idx:
            continue
        col = ds.data[:, idx[r]]
        finite = col[np.isfinite(col)]
        bad = int(np.sum((finite < scale_min) | (finite > scale_max)))
        if bad:
            out[r] = bad
    return out


def apply_reverse(ds: Dataset, reverse: Sequence[str], scale_min: float, scale_max: float) -> Dataset:
    """역문항 재점수화: x -> (scale_min + scale_max) - x."""
    if not reverse:
        return ds
    idx = {name: i for i, name in enumerate(ds.names)}
    unknown = [r for r in reverse if r not in idx]
    if unknown:
        raise DataError(f"역문항 목록에 없는 문항: {', '.join(unknown)}")
    data = ds.data.copy()
    const = scale_min + scale_max
    for r in reverse:
        col = data[:, idx[r]]
        data[:, idx[r]] = const - col
    return Dataset(names=list(ds.names), data=data, coercion=dict(ds.coercion),
                   dropped=dict(getattr(ds, "dropped", {})))


@dataclass
class Prepared:
    names: List[str]
    matrix: np.ndarray   # (n, p) 결측제거 완료
    n_total: int
    n_used: int
    n_dropped: int
    coercion: Dict[str, int] = field(default_factory=dict)
    # 자동선택에서 제외된 후보 열(열이름 -> 사유).
    dropped: Dict[str, str] = field(default_factory=dict)
    # 원자료(raw) 행 중 결측제거 후 살아남은 행의 불리언 마스크 — ID 정렬·점수 내보내기용.
    row_mask: Optional[np.ndarray] = None
    # 결측제거 '전' 원자료 (n_total, p). 결측 진단·listwise 편향 점검에 쓴다.
    raw: Optional[np.ndarray] = None


def listwise(ds: Dataset) -> Prepared:
    """행 단위 결측 제거(listwise deletion)."""
    mask = np.all(np.isfinite(ds.data), axis=1)
    used = ds.data[mask]
    return Prepared(
        names=list(ds.names),
        matrix=used,
        n_total=ds.data.shape[0],
        n_used=int(used.shape[0]),
        n_dropped=int(ds.data.shape[0] - used.shape[0]),
        coercion=dict(getattr(ds, "coercion", {})),
        dropped=dict(getattr(ds, "dropped", {})),
        row_mask=mask,
        raw=ds.data.copy(),
    )


def missing_report(raw: np.ndarray, names: Sequence[str]) -> Dict:
    """결측 구조 진단: 문항별 결측 수·비율, 행별 결측 분포.

    임상 설문 CSV는 결측이 흔하고, listwise 삭제는 '한 문항만 빠져도' 그 응답자를 통째로
    버리기 때문에 표본이 급격히 줄 수 있다. 어느 문항이 손실을 유발하는지 짚어 주면
    그 문항을 빼고 다시 돌릴지 판단할 수 있다.

    반환: per_item(문항별 결측 수), per_item_prop, n_complete, n_incomplete,
          worst_item(결측이 가장 많은 문항명 또는 None).
    """
    n_rows = int(raw.shape[0])
    miss = ~np.isfinite(raw)
    per_item = miss.sum(axis=0).astype(int)
    row_complete = ~miss.any(axis=1)
    prop = (per_item / n_rows) if n_rows else np.zeros_like(per_item, dtype=float)
    worst = int(np.argmax(per_item)) if per_item.size and per_item.max() > 0 else None
    return {
        "per_item": per_item.tolist(),
        "per_item_prop": [float(v) for v in prop],
        "n_complete": int(row_complete.sum()),
        "n_incomplete": int(n_rows - row_complete.sum()),
        "worst_item": names[worst] if worst is not None else None,
    }


def listwise_bias_check(raw: np.ndarray, names: Sequence[str],
                        min_group: int = 5) -> List[Dict]:
    """listwise 삭제가 응답 분포를 바꾸는지(MCAR 위배 신호) 문항별로 점검한다.

    각 문항 i에 대해, **완전응답자**의 i 값 분포와 **일부 결측이 있어 버려질 응답자**의
    i 값 분포(그 응답자가 i는 답한 경우)를 비교해 표준화 평균차(Cohen's d)를 낸다.
    두 분포가 크게 다르면 삭제된 표본이 무작위가 아니어서(MAR/MNAR) 요인분석 결과가
    편향될 수 있다는 신호다 — Little의 MCAR 검정과 같은 취지의 문항별 간이 진단.

    각 군의 유효 표본이 min_group 미만이거나 분산이 0이면 그 문항은 건너뛴다.
    반환: [{"item", "d", "mean_complete", "mean_dropped", "n_dropped_obs"}, ...]
    """
    miss = ~np.isfinite(raw)
    complete = ~miss.any(axis=1)
    dropped = ~complete
    out: List[Dict] = []
    if not dropped.any():
        return out
    for i, name in enumerate(names):
        obs = np.isfinite(raw[:, i])
        a = raw[complete & obs, i]
        b = raw[dropped & obs, i]      # 버려질 응답자 중 이 문항은 답한 사람
        if a.size < min_group or b.size < min_group:
            continue
        va, vb = a.var(ddof=1), b.var(ddof=1)
        # 합동표준편차(pooled SD). 두 군 모두 분산 0이면 d 정의 불가.
        pooled = math.sqrt(((a.size - 1) * va + (b.size - 1) * vb)
                           / max(a.size + b.size - 2, 1))
        if not np.isfinite(pooled) or pooled <= 1e-12:
            continue
        out.append({
            "item": name,
            "d": float((a.mean() - b.mean()) / pooled),
            "mean_complete": float(a.mean()),
            "mean_dropped": float(b.mean()),
            "n_dropped_obs": int(b.size),
        })
    return out
