"""표(CSV/TSV) 읽기 — 인코딩 추정, 구분자 추정, 읽기 전용.

입력 파일은 언제나 `rb` 로만 열립니다. 이 모듈에는 쓰기 경로가 없습니다.
"""

from __future__ import annotations

import csv
import io
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# 한 셀이 이보다 길면 잘라서 검사하고, 잘랐다는 사실을 자백합니다.
MAX_CELL_SCAN = 20000
# 파일 하나가 이보다 크면 읽지 않고 건너뜁니다(자백 대상).
DEFAULT_MAX_BYTES = 200 * 1024 * 1024

_CANDIDATE_DELIMS = [",", "\t", ";", "|"]

# 아주 긴 셀 하나 때문에 파일 전체를 못 읽는 일이 없도록 상한을 올립니다.
# (기본 131,072자 — 자유기술 칸 하나가 그걸 넘으면 파일이 통째로 판정불가가 됩니다.)
try:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
except (OverflowError, ValueError):  # pragma: no cover - 플랫폼 차이
    csv.field_size_limit(2**27)


@dataclass
class Table:
    """검사 대상 표 하나(파일 = CSV 한 장, 또는 XLSX 시트 한 장)."""

    file: str
    sheet: str
    columns: List[str]
    rows: List[List[str]]
    path: Optional[Path] = None
    hidden_sheet: bool = False
    hidden_columns: Set[int] = field(default_factory=set)
    hidden_rows: Set[int] = field(default_factory=set)
    encoding: str = ""
    delimiter: str = ""
    notes: List[str] = field(default_factory=list)
    truncated_cells: int = 0
    # 데이터 행 인덱스 → 원본 파일의 물리 행 번호(1-기반). 빈 줄이 섞여 있어도
    # 리포트의 행 번호가 실제 파일에서 찾아갈 수 있는 번호가 되도록 유지합니다.
    source_rows: List[int] = field(default_factory=list)
    # 헤더보다 열이 많아 잘려 나간 셀들(그래도 스캔은 합니다).
    overflow_cells: List[str] = field(default_factory=list)
    # 헤더 위의 제목/서문 행들. 버리지 않고 그대로 스캔합니다 —
    # "2026년 참여자 명단 (담당 김철수 010-1234-5678)" 같은 줄이 실제로 있습니다.
    preheader_cells: List[str] = field(default_factory=list)
    # 중복 제거 전 원래 헤더(--drop-columns 가 `이름#2` 를 놓치지 않도록).
    original_columns: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.file}!{self.sheet}" if self.sheet else self.file

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.columns)

    @property
    def n_cells(self) -> int:
        return self.n_rows * self.n_cols

    def column_index(self, name: str) -> Optional[int]:
        """열 이름으로 인덱스를 찾습니다(유니코드 정규화 + 공백/대소문자 무시)."""
        target = norm_key(name)
        for i, col in enumerate(self.columns):
            if norm_key(col) == target:
                return i
        return None

    def column_values(self, index: int) -> List[str]:
        return [row[index] if index < len(row) else "" for row in self.rows]

    def cell(self, row_index: int, col_index: int) -> str:
        row = self.rows[row_index]
        return row[col_index] if col_index < len(row) else ""


@dataclass
class LoadResult:
    """파일 하나를 읽은 결과."""

    file: str
    tables: List[Table] = field(default_factory=list)
    structural: List = field(default_factory=list)  # Finding 목록(XLSX 숨은 내용 등)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (대상, 사유)
    fatal: Optional[str] = None  # 읽기 자체가 불가능한 경우의 사유
    unreadable_sheets: int = 0  # 비어 있는 게 아니라 **읽지 못한** 시트 수


def pick_header_index(rows: List[Sequence[str]], look: int = 20) -> int:
    """헤더로 쓸 행의 인덱스를 고릅니다.

    "2026년 수면연구 참여 현황" 같은 제목 줄과 빈 줄이 표 위에 붙어 있는 파일이
    흔합니다. 무조건 첫 행을 헤더로 쓰면 열 이름이 전부 `열2`, `열3` 이 되고
    헤더 기반 규칙(이름·생년월일·`--quasi`·`--drop-columns`)이 통째로 죽습니다.

    규칙: 앞쪽 몇 행의 '값이 있는 칸 수'를 세어, 최대치에 처음 도달하는 행을
    헤더로 봅니다. 단, 그 앞의 행들이 **명백히 부실할 때만**(최대치의 절반 이하)
    건너뜁니다 — 헤더의 끝 칸이 비어 있는 정상적인 표를 망치지 않기 위해서입니다.
    """
    if not rows:
        return 0
    counts = [sum(1 for c in row if str(c or "").strip()) for row in rows[:look]]
    if not counts:
        return 0
    # **최빈 폭**을 기준으로 씁니다. 최대 폭을 쓰면 자유기술 칸의 쉼표 하나로
    # 열이 늘어난 행 때문에 진짜 헤더가 '제목 행'으로 분류되어 통째로 버려집니다.
    nonzero = [c for c in counts if c]
    if not nonzero:
        return 0
    target = max(set(nonzero), key=lambda v: (nonzero.count(v), v))
    if counts[0] >= target:
        return 0
    threshold = max(1, target // 2)
    for i, count in enumerate(counts):
        if count >= target:
            if all(counts[j] <= threshold for j in range(i)):
                return i
            return 0
    return 0


def norm_key(text: str) -> str:
    """열 이름 비교용 정규화 — NFC + 공백 제거 + 소문자 + BOM 제거."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFC", str(text))
    t = t.replace("﻿", "").replace("​", "")
    t = "".join(ch for ch in t if not ch.isspace())
    return t.casefold()


def decode_bytes(raw: bytes) -> Tuple[Optional[str], str]:
    """바이트를 텍스트로 디코드하고 사용한 인코딩을 함께 돌려줍니다.

    Returns:
        (text, encoding) — 어떤 인코딩으로도 못 읽으면 (None, "").
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        for enc in ("utf-32",):
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, UnicodeError):
                pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except (UnicodeDecodeError, UnicodeError):
            pass
    # BOM 없는 UTF-16 은 널 바이트가 촘촘히 섞여 있습니다. 그런데 널은 UTF-8 로도
    # 유효해서(U+0000), utf-8 을 먼저 시도하면 조용히 "성공"하고 표가 사라집니다.
    # (엑셀의 "유니코드 텍스트" 저장이 실제로 이 형태입니다.)
    if raw[:4096].count(0) > len(raw[:4096]) * 0.05:
        candidates = []
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = raw.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
            candidates.append((decode_plausibility(text), text, enc))
        if candidates:
            score, text, enc = max(candidates, key=lambda c: c[0])
            if score >= 0.90:
                return text, enc

    # cp1252/latin-1 과 느슨한 utf-16 은 **어떤 바이트열이든** 조용히 디코드해 버립니다.
    # 그래서 디코드가 성공했다는 사실만으로는 부족하고, 결과가 표처럼 보이는지
    # 점수를 매겨야 합니다. 그러지 않으면 모지바케를 읽고 "치명 0건"을 말합니다.
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        return text, enc
    best_text, best_enc, best_score = None, "", -1.0
    for enc in ("cp1252", "latin-1", "utf-16", "utf-16-be", "utf-16-le"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
        score = decode_plausibility(text)
        if score > best_score:
            best_text, best_enc, best_score = text, enc, score
    if best_text is not None and best_score >= 0.90:
        return best_text, best_enc
    return None, ""


def decode_plausibility(text: str) -> float:
    """디코드 결과가 '한국어 임상 데이터 표'로 그럴듯한 비율(0~1).

    "디코드가 예외 없이 끝났다"는 것만으로는 부족합니다. UTF-16LE 로 잘못 읽은
    UTF-16BE 바이트열은 예외 없이 한자 덩어리가 되는데, 그것도 전부 '출력 가능한
    문자'이기 때문입니다. 그래서 **기대되는 문자 집합**(ASCII·한글·일반 기호)에
    얼마나 들어맞는지로 점수를 냅니다.
    """
    if not text:
        return 0.0
    sample = text[:20000]
    good = 0
    for ch in sample:
        code = ord(ch)
        if ch in "\r\n\t":
            good += 1
        elif 0x20 <= code <= 0x7E:                 # ASCII 출력 가능
            good += 1
        elif 0xAC00 <= code <= 0xD7A3:             # 한글 음절
            good += 1
        elif 0x3130 <= code <= 0x318F:             # 한글 자모
            good += 1
        elif code in (0x00B0, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x00B7):
            good += 1                              # 흔히 쓰는 기호
        elif 0xFF01 <= code <= 0xFF5E:             # 전각 영숫자·기호
            good += 1
    return good / len(sample)


def sniff_delimiter(text: str, path_suffix: str = "") -> str:
    """구분자를 추정합니다.

    **일관성이 압도적으로 중요합니다.** 예전 점수식은 `일관성*10 + 최대열수` 라서,
    셀 하나 안에 있는 `두통|어지럼|불면|…` 이 헤더를 깔끔하게 3열로 나누는 쉼표를
    이겨 버렸습니다. 그러면 표 전체가 1열로 뭉개지고 이름·전화번호가 통째로
    검사 대상에서 빠지는데, 툴은 "치명 0건"이라고 말합니다.

    그래서: (1) 헤더가 2개 이상으로 쪼개져야 하고, (2) 모든 행의 열 수가 같은지를
    최우선으로 보고, (3) 동점일 때만 열 수가 많은 쪽을 고릅니다.
    """
    if path_suffix.lower() in (".tsv", ".tab"):
        return "\t"
    sample_lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    if not sample_lines:
        return ","
    best = ","
    best_score = (-1.0, -1.0, 0)
    for delim in _CANDIDATE_DELIMS:
        try:
            rows = list(csv.reader(io.StringIO("\n".join(sample_lines)), delimiter=delim))
        except csv.Error:
            continue
        counts = [len(r) for r in rows]
        if not counts:
            continue
        modal = max(set(counts), key=lambda v: (counts.count(v), v))
        if modal <= 1:
            continue
        # **첫 줄이 헤더라고 가정하지 않습니다.** 제목 줄이 맨 위에 있는 파일이
        # 흔한데, 첫 줄이 안 쪼개진다는 이유로 후보를 버리면 표 전체가 1열로
        # 뭉개져 이름·전화번호가 통째로 검사에서 빠집니다.
        consistency = counts.count(modal) / len(counts)
        majority = 1.0 if consistency >= 0.5 else 0.0
        score = (majority, consistency, min(modal, 64))
        if score > best_score:
            best_score = score
            best = delim
    return best


def _clean_cell(value: str, table_state: Dict[str, int]) -> str:
    if value is None:
        return ""
    text = value.replace("\x00", "")
    if len(text) > MAX_CELL_SCAN:
        table_state["truncated"] = table_state.get("truncated", 0) + 1
        text = text[:MAX_CELL_SCAN]
    return text


def load_csv(path: Path, display: Optional[str] = None, max_bytes: int = DEFAULT_MAX_BYTES) -> LoadResult:
    """CSV/TSV 한 장을 읽습니다(읽기 전용).

    Args:
        path: 입력 파일 경로.
        display: 리포트에 쓸 표시명(기본은 파일명).
        max_bytes: 이보다 큰 파일은 읽지 않고 건너뜁니다.
    """
    name = display or path.name
    result = LoadResult(file=name)
    try:
        size = path.stat().st_size
    except OSError as exc:
        result.fatal = f"파일 정보를 읽을 수 없음 ({exc.__class__.__name__})"
        return result
    if size > max_bytes:
        result.fatal = f"파일이 너무 큼 ({size:,} 바이트 > {max_bytes:,})"
        return result
    try:
        raw = path.read_bytes()
    except OSError as exc:
        result.fatal = f"파일을 열 수 없음 ({exc.__class__.__name__})"
        return result
    if not raw.strip():
        result.fatal = "빈 파일"
        return result

    text, encoding = decode_bytes(raw)
    if text is None:
        result.fatal = "인코딩을 판정할 수 없음(UTF-8/CP949/EUC-KR/UTF-16 모두 실패)"
        return result

    # 구형 Mac 의 CR 단독 개행을 정규화합니다(안 하면 csv 가 통째로 실패합니다).
    if "\r" in text and "\n" not in text:
        text = text.replace("\r", "\n")
    delimiter = sniff_delimiter(text, path.suffix)
    state: Dict[str, int] = {}
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        raw_rows = [row for row in reader]
    except csv.Error as exc:
        result.fatal = f"CSV 파싱 실패 ({exc})"
        return result

    numbered = [
        (i + 1, r) for i, r in enumerate(raw_rows) if any((c or "").strip() for c in r)
    ]
    if not numbered:
        result.fatal = "데이터 행이 없음"
        return result

    header_pos = pick_header_index([r for _, r in numbered])
    skipped_title_rows = header_pos
    preheader = [
        _clean_cell(c, state) for _, raw in numbered[:header_pos] for c in raw if str(c or "").strip()
    ]
    header_line_no, header_raw = numbered[header_pos]
    original_header = [_clean_cell(c, state).strip() for c in header_raw]
    header = _dedupe_header(original_header)
    width = len(header)
    rows: List[List[str]] = []
    source_rows: List[int] = []
    overflow: List[str] = []
    ragged = 0
    for line_no, raw_row in numbered[header_pos + 1:]:
        row = [_clean_cell(c, state) for c in raw_row]
        if len(row) != width:
            ragged += 1
            if len(row) < width:
                row = row + [""] * (width - len(row))
            else:
                # 잘라 버리면 그 안의 전화번호·이름이 영원히 검사되지 않습니다.
                overflow.extend(c for c in row[width:] if str(c).strip())
                row = row[:width]
        rows.append(row)
        source_rows.append(line_no)

    table = Table(
        file=name,
        sheet="",
        columns=header,
        rows=rows,
        path=path,
        encoding=encoding,
        delimiter=delimiter,
        truncated_cells=state.get("truncated", 0),
        source_rows=source_rows,
        overflow_cells=overflow,
        preheader_cells=preheader,
        original_columns=original_header,
    )
    if skipped_title_rows:
        table.notes.append(
            f"헤더 앞의 제목 행 {skipped_title_rows}개를 건너뛰고 파일의 {header_line_no}번째 줄을 헤더로 봤습니다"
        )
    if ragged:
        table.notes.append(
            f"열 수가 헤더와 다른 행 {ragged}개 — 모자란 칸은 빈칸으로 채우고, "
            f"넘치는 칸 {len(overflow)}개는 열 밖으로 밀렸지만 **그대로 스캔했습니다**"
        )
    if state.get("truncated"):
        table.notes.append(
            f"{state['truncated']}개 셀이 {MAX_CELL_SCAN:,}자를 넘어 앞부분만 검사했습니다"
        )
    result.tables.append(table)
    return result


def _dedupe_header(header: Sequence[str]) -> List[str]:
    """빈 헤더와 중복 헤더에 안정적인 이름을 부여합니다."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for i, name in enumerate(header):
        base = name.strip() or f"열{i + 1}"
        key = norm_key(base)
        if key in seen:
            seen[key] += 1
            base = f"{base}#{seen[key]}"
        else:
            seen[key] = 1
        out.append(base)
    return out
