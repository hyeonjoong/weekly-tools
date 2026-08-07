"""표 파일(CSV/TSV/XLSX)을 읽어 `Frame` 으로 만든다 — 표준 라이브러리만.

실제로 받는 파일은 깨끗하지 않다. 이 모듈은 그 중 **조용히 틀리게 만드는**
것들만 골라서 처리한다.

* **인코딩** — 엑셀이 내보낸 한글 CSV는 cp949, 설문 플랫폼은 UTF-8 BOM,
  가끔 UTF-16. 잘못 읽으면 피험자 ID가 깨져서 "매칭 안 됨"으로 보인다.
* **구분자** — 쉼표/탭/세미콜론. 세미콜론 CSV를 쉼표로 읽으면 전체가 한 열이
  되어 "키 열을 못 찾음"이 된다.
* **시트 앞 안내문 / 빈 줄** — 헤더가 1행이 아닌 파일. 첫 행을 무조건 헤더로
  삼으면 열 이름이 "2026년 3월 측정 결과"가 된다.
* **중복 열 이름** — 조용히 하나만 살아남으면 값이 통째로 바뀐다. 이름을
  구분해 두고 보고한다.
* **숫자 표기** — `1,234`(천단위) 와 `12,5`(유럽식 소수점)는 정반대의 뜻인데
  둘 다 쉼표다. **열 단위로** 판정하고, 판정 결과를 리포트에 문장으로 남긴다.

읽기 전용이다. 원본 파일은 어떤 경우에도 수정하지 않는다.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import stat as _stat
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .xlsxread import XlsxError, looks_like_legacy_xls, looks_like_xlsx, read_sheet

__all__ = [
    "Frame",
    "LoadError",
    "load_table",
    "normalize_numeric_columns",
    "parse_number",
    "is_missing",
    "MISSING_TOKENS",
    "MAX_ROWS",
]

# 결측으로 볼 토큰(대소문자 무시). SAS/SPSS 내보내기의 '.' 포함.
MISSING_TOKENS = frozenset(
    {"", "NA", "N/A", "NAN", "NULL", "NONE", ".", "?", "-", "--", "MISSING",
     "없음", "해당없음", "#N/A", "#NULL!", "#VALUE!", "#DIV/0!"})

MAX_ROWS = 2_000_000
_MAX_COLS = 20_000
# 디코딩 전에 거는 바이트 상한. 이게 없으면 `/dev/zero` 같은 입력에서 메모리가
# 무한히 늘어난다(MAX_ROWS 는 이미 다 읽은 뒤에야 걸린다).
MAX_BYTES = 512 * 1024 * 1024
# 인코딩/구분자 판정을 위해 앞부분만 읽는다.
_SNIFF_BYTES = 256 * 1024

_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_EURO_DECIMAL_RE = re.compile(r"^[+-]?\d+,\d+$")
_PLAIN_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class LoadError(ValueError):
    """파일을 읽을 수 없을 때 — 메시지는 사람이 조치할 수 있는 한국어."""


def is_missing(token: str) -> bool:
    return token.strip().upper() in MISSING_TOKENS


def parse_number(token: str, decimal_comma: bool = False) -> Optional[float]:
    """셀을 유한 실수로. 결측/비숫자/비유한값은 None.

    `decimal_comma=True` 면 쉼표를 소수점으로 읽는다(유럽식). 기본값에서는
    쉼표가 **명백한 천단위 구분자일 때만** 제거한다 — 애매한 `1,5` 를 15로
    바꿔 버리는 것이 이 함수가 저지를 수 있는 최악의 실수다.
    """
    t = token.strip()
    if t.upper() in MISSING_TOKENS:
        return None
    if decimal_comma and _EURO_DECIMAL_RE.match(t):
        t = t.replace(",", ".")
    elif _THOUSANDS_RE.match(t):
        t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


@dataclass
class ColumnNote:
    """열 하나에 적용된 판정/변환 기록. 리포트에 그대로 문장으로 나간다."""

    column: str
    kind: str          # 'decimal_comma' | 'thousands' | 'renamed' | 'blank_name'
    detail: str
    count: int = 0


@dataclass
class Frame:
    """헤더 + 문자열 셀로 이루어진 단순한 표."""

    path: str
    label: str                                   # 화면·리포트용 짧은 이름
    header: List[str]
    rows: List[List[str]]
    header_row_index: int = 0                    # 0-기반, 원본 파일 기준
    encoding: str = "utf-8"
    delimiter: str = ","
    sheet: Optional[str] = None
    notes: List[ColumnNote] = field(default_factory=list)
    index: Dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.index = {name: i for i, name in enumerate(self.header)}

    @property
    def nrows(self) -> int:
        return len(self.rows)

    def has(self, column: str) -> bool:
        return column in self.index

    def cell(self, row: Sequence[str], column: str) -> str:
        i = self.index[column]
        return row[i] if i < len(row) else ""

    def column(self, column: str) -> List[str]:
        i = self.index[column]
        return [row[i] if i < len(row) else "" for row in self.rows]

    def source_line(self, row_index: int) -> int:
        """0-기반 데이터 행 -> 사람이 파일에서 볼 1-기반 행 번호."""
        return self.header_row_index + 2 + row_index


# --------------------------------------------------------------------------
# 인코딩 / 구분자
# --------------------------------------------------------------------------

def _decode(raw: bytes) -> Tuple[str, str]:
    """바이트를 텍스트로. (텍스트, 사용한 인코딩 이름)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        for enc in ("utf-32",):
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    # BOM 없는 UTF-16: ASCII 본문이면 NUL 바이트가 규칙적으로 섞인다.
    head = raw[:4096]
    if head.count(b"\x00") > len(head) // 4:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
    for enc in ("utf-8", "cp949", "cp932", "cp1252"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 마지막 수단 — 절대 실패하지 않지만 정확하지도 않다. 호출부가 경고한다.
    return raw.decode("latin-1", errors="replace"), "latin-1(추정 실패)"


def _sniff_delimiter(sample: str, path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return "\t"
    first = sample.split("\n", 1)[0]
    try:
        return csv.Sniffer().sniff(sample[:8192], delimiters=",\t;|").delimiter
    except csv.Error:
        pass
    # Sniffer가 포기하면 첫 줄에서 가장 많이 나오는 후보를 쓴다.
    counts = {d: first.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


# --------------------------------------------------------------------------
# 헤더 찾기 / 이름 정리
# --------------------------------------------------------------------------

def _find_header_row(rows: List[List[str]]) -> int:
    """헤더로 볼 행의 0-기반 인덱스.

    시트 앞 안내문("2026년 3월 측정 결과")과 빈 줄을 건너뛴다. 규칙은 단순하고
    설명 가능해야 한다 — 뒤따르는 데이터 행들의 대표 폭(중앙값)의 절반 이상을
    채운 **첫 번째** 행이 헤더다.
    """
    filled = [sum(1 for c in r if c.strip()) for r in rows]
    if not filled:
        return 0

    # 시트 앞 안내문은 흔히 **빈 줄 하나로** 본문과 분리된다. 앞부분에 "내용 →
    # 빈 줄 → 내용" 이 나타나면 빈 줄 뒤부터가 진짜 표다. 폭만 보는 규칙은
    # 안내문이 데이터만큼 넓을 때 안내문을 헤더로 골라 버린다.
    scan = min(len(filled), 10)
    start = 0
    for i in range(scan):
        if filled[i] == 0 and any(filled[:i]) and any(filled[i + 1:]):
            start = i + 1
    if start:
        offset = _find_header_row(rows[start:])
        return start + offset
    body = sorted(n for n in filled if n > 0)
    if not body:
        return 0
    typical = body[len(body) // 2]
    threshold = max(2, (typical + 1) // 2)
    for i, n in enumerate(filled):
        if n >= threshold:
            return i
    return next((i for i, n in enumerate(filled) if n > 0), 0)


def _clean_header(raw: Sequence[str]) -> Tuple[List[str], List[ColumnNote]]:
    """열 이름을 정리한다: 공백 정돈, 빈 이름 채우기, 중복 이름 구분."""
    notes: List[ColumnNote] = []
    names: List[str] = []
    seen: Dict[str, int] = {}
    for i, cell in enumerate(raw):
        name = re.sub(r"\s+", " ", cell.replace("﻿", "")).strip()
        if not name:
            name = f"열{i + 1}"
            notes.append(ColumnNote(name, "blank_name",
                                    f"{i + 1}번째 열에 이름이 없어 '{name}' 으로 채움"))
        if name in seen:
            # pandas 와 같은 표기(`비고`, `비고.1`, `비고.2`). 새로 만든 이름이
            # 또 원본에 있을 수도 있으므로 빈 자리를 찾을 때까지 올린다.
            original = name
            while name in seen:
                seen[original] += 1
                name = f"{original}.{seen[original] - 1}"
            notes.append(ColumnNote(
                name, "renamed",
                f"열 이름 '{original}' 이 중복되어 '{name}' 으로 구분함 "
                "(원본에서 어느 쪽이 맞는지 확인 필요)"))
            seen[name] = 1
        else:
            seen[name] = 1
        names.append(name)
    return names, notes


# --------------------------------------------------------------------------
# 적재
# --------------------------------------------------------------------------

def load_table(path: str, label: Optional[str] = None,
               sheet: Optional[str] = None,
               header_row: Optional[int] = None) -> Frame:
    """CSV/TSV/XLSX 한 개를 `Frame` 으로. 실패는 `LoadError`.

    `header_row` 는 1-기반(사람이 세는 방식). 생략하면 자동 탐지한다.
    """
    if not os.path.exists(path):
        raise LoadError(f"파일을 찾을 수 없습니다: {path}")
    if os.path.isdir(path):
        raise LoadError(f"'{path}' 은(는) 폴더입니다. 파일 경로를 지정하세요.")

    label = label or os.path.basename(path)

    # 이름있는 파이프나 장치 파일(`/dev/zero`)을 그냥 열면 **영원히 멈춘다.**
    # 크기도 미리 본다 — 다 읽은 뒤에 상한을 확인하면 이미 늦다.
    try:
        info = os.stat(path)
    except OSError as exc:
        raise LoadError(f"'{label}' 의 정보를 읽을 수 없습니다: {exc}")
    if not _stat.S_ISREG(info.st_mode):
        raise LoadError(
            f"'{label}' 은(는) 일반 파일이 아닙니다(파이프·장치 파일 등). "
            "표 파일의 경로를 지정하세요.")
    if info.st_size > MAX_BYTES:
        raise LoadError(
            f"'{label}' 이 너무 큽니다({info.st_size / 1024 / 1024:.0f}MB > "
            f"{MAX_BYTES // 1024 // 1024}MB). 파일을 나누어 처리하세요.")
    encoding, delimiter = "-", "-"

    if looks_like_legacy_xls(path):
        raise LoadError(
            f"'{label}' 은(는) 구형 엑셀(.xls)입니다. 엑셀에서 열어 "
            ".xlsx 또는 'CSV UTF-8'로 저장한 뒤 다시 실행하세요.")

    if not looks_like_xlsx(path) and \
            os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm"):
        # 이름은 엑셀인데 내용이 zip 이 아니다. CSV 로 읽어 넘기면 "키 열을 못
        # 찾음" 같은 엉뚱한 오류가 나와 사람이 원인을 못 찾는다.
        raise LoadError(
            f"'{label}' 은(는) 이름은 .xlsx 지만 올바른 엑셀 파일이 아닙니다"
            "(손상되었거나 다른 형식을 확장자만 바꾼 파일). "
            "엑셀에서 열어 .xlsx 또는 'CSV UTF-8'로 다시 저장하세요.")

    if looks_like_xlsx(path):
        try:
            raw_rows = read_sheet(path, sheet)
        except XlsxError as exc:
            raise LoadError(f"'{label}': {exc}")
        encoding, delimiter = "xlsx", "-"
    else:
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise LoadError(
                        f"'{label}' 이 너무 큽니다"
                        f"(> {MAX_BYTES // 1024 // 1024}MB).")
        except OSError as exc:
            raise LoadError(f"'{label}' 을(를) 열 수 없습니다: {exc}")
        if not data.strip():
            raise LoadError(f"'{label}' 이 비어 있습니다.")
        text, encoding = _decode(data)
        delimiter = _sniff_delimiter(text[:_SNIFF_BYTES], path)
        try:
            reader = csv.reader(io.StringIO(text, newline=""),
                                delimiter=delimiter)
            raw_rows = []
            for row in reader:
                if len(raw_rows) > MAX_ROWS:
                    raise LoadError(
                        f"'{label}' 의 행이 너무 많습니다(> {MAX_ROWS:,}). "
                        "파일을 나누어 처리하세요.")
                raw_rows.append(row)
        except csv.Error as exc:
            raise LoadError(f"'{label}' 을(를) CSV로 해석할 수 없습니다: {exc}")

    if not raw_rows:
        raise LoadError(f"'{label}' 에 읽을 내용이 없습니다.")
    if max((len(r) for r in raw_rows), default=0) > _MAX_COLS:
        raise LoadError(f"'{label}' 의 열이 너무 많습니다(> {_MAX_COLS:,}).")

    if header_row is not None:
        hidx = header_row - 1
        if not 0 <= hidx < len(raw_rows):
            raise LoadError(
                f"'{label}': --header-row {header_row} 는 범위를 벗어납니다 "
                f"(이 파일은 {len(raw_rows)}행).")
    else:
        hidx = _find_header_row(raw_rows)

    header, notes = _clean_header(raw_rows[hidx])
    width = len(header)
    if width == 0:
        raise LoadError(f"'{label}' 의 헤더 행이 비어 있습니다.")

    rows: List[List[str]] = []
    for row in raw_rows[hidx + 1:]:
        if not any(c.strip() for c in row):
            continue                      # 완전 빈 줄은 데이터가 아니다
        if len(row) < width:
            row = list(row) + [""] * (width - len(row))
        elif len(row) > width:
            # 헤더보다 긴 행: 넘치는 칸에 값이 있으면 알려야 한다(조용히 자르면
            # 값이 사라진다). 열 이름을 만들어 붙여 살려 둔다.
            extra = len(row) - width
            for k in range(extra):
                # 이미 있는 이름과 겹치면 `Frame.index` 가 뒤엣것만 가리켜
                # **원래 열의 값이 통째로 사라진다.** 빈 자리를 찾아 붙인다.
                name = f"열{width + k + 1}"
                if name in header:
                    n = 1
                    while f"{name}.{n}" in header:
                        n += 1
                    name = f"{name}.{n}"
                header.append(name)
                notes.append(ColumnNote(
                    name, "blank_name",
                    f"헤더보다 긴 행이 있어 '{name}' 열을 추가함"))
            width = len(header)
            rows = [r + [""] * (width - len(r)) for r in rows]
        rows.append([c.replace("﻿", "") for c in row])

    rows = [r + [""] * (width - len(r)) for r in rows]
    return Frame(path=os.path.abspath(path), label=label, header=header,
                 rows=rows, header_row_index=hidx, encoding=encoding,
                 delimiter=delimiter, sheet=sheet, notes=notes)


def _classify_comma_style(tokens: Sequence[str]) -> Optional[str]:
    """쉼표가 섞인 숫자 열의 표기 판정: 'thousands' | 'decimal_comma' | None.

    판정이 애매하면 None(=건드리지 않는다). 이 열이 무엇인지 확신할 수 없을 때
    값을 바꾸는 것보다 그대로 두는 쪽이 언제나 안전하다.
    """
    with_comma = [t for t in tokens if "," in t]
    if not with_comma:
        return None
    if all(_THOUSANDS_RE.match(t) for t in with_comma):
        return "thousands"
    if all(_EURO_DECIMAL_RE.match(t) for t in with_comma):
        # `1,234` 는 두 해석이 모두 가능하다. 소수부가 3자리가 아닌 토큰이
        # 하나라도 있어야 유럽식으로 확정한다.
        if any(len(t.split(",", 1)[1]) != 3 for t in with_comma):
            return "decimal_comma"
        return "thousands"
    return None


def normalize_numeric_columns(frame: Frame,
                              exclude: Sequence[str] = ()) -> List[ColumnNote]:
    """숫자 열의 쉼표 표기를 표준 소수점 표기로 정규화한다.

    `1.234,5`(유럽식) 나 `1,234.5`(천단위)를 그대로 두면 하류 통계 툴이 전부
    결측으로 읽는다. **열 단위로** 판정하고, 판정과 변경 건수를 기록으로 남긴다.
    ID·날짜·시점 열은 `exclude` 로 제외한다(숫자처럼 생긴 ID를 건드리면 안 된다).
    """
    excluded = {c for c in exclude if c}
    applied: List[ColumnNote] = []

    for ci, name in enumerate(frame.header):
        if name in excluded:
            continue
        tokens = [row[ci].strip() for row in frame.rows
                  if ci < len(row) and not is_missing(row[ci])]
        if not tokens:
            continue
        style = _classify_comma_style(tokens)
        if style is None:
            continue
        # 쉼표가 없는 나머지 토큰까지 전부 숫자여야 이 열을 숫자 열로 본다.
        rest = [t for t in tokens if "," not in t]
        if rest and not all(_PLAIN_NUMBER_RE.match(t) for t in rest):
            continue

        changed = 0
        for row in frame.rows:
            if ci >= len(row):
                continue
            t = row[ci].strip()
            if "," not in t:
                continue
            new = (t.replace(",", ".") if style == "decimal_comma"
                   else t.replace(",", ""))
            if _PLAIN_NUMBER_RE.match(new):
                row[ci] = new
                changed += 1
        if changed:
            note = ColumnNote(
                name, style,
                ("유럽식 소수점(쉼표)을 점으로 바꿈" if style == "decimal_comma"
                 else "천단위 쉼표를 제거함"),
                changed)
            frame.notes.append(note)
            applied.append(note)
    return applied
