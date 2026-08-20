"""출력 번들 수집 — 분석 산출물 폴더에서 '수치 셀'을 좌표와 함께 색인합니다.

번들은 SPSS 든 R 이든 엑셀이든 상관없이 **숫자가 들어 있는 텍스트 파일**이면
받습니다(.csv/.tsv/.json/.xlsx/.md/.txt). 연구실마다 출력 형태가 다르고, 그
다양성을 감당하지 못하면 이 툴은 아무 데서도 안 돌아가기 때문입니다.

읽지 못한 파일은 **절대 조용히 넘어가지 않습니다.** 개수와 사유를 리포트에
자백합니다 — 못 읽은 파일이 있는 채로 '출처 없음'을 선언하면 그건 거짓말입니다.
"""

import csv
import json
import io
import os
import re
import stat
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from . import zipsafe
from .manuscript import decode_bytes
from .numbers import cell_numbers
from .safety import InputError, resolve
from .textnorm import normalize_simple
from .xlsxread import read_xlsx

READABLE = (".csv", ".tsv", ".json", ".xlsx", ".md", ".txt", ".tab", ".text",
            ".markdown")
KNOWN_UNREADABLE = {
    ".xls": "구형 `.xls` 바이너리는 읽지 않습니다(엑셀에서 .xlsx 로 저장하세요)",
    ".pdf": "PDF 는 읽지 않습니다(README 한계 절)",
    ".doc": "구형 `.doc` 는 읽지 않습니다",
    ".sav": "SPSS `.sav` 는 읽지 않습니다(SPSS 에서 CSV 로 내보내세요)",
    ".rdata": "R `.RData` 는 읽지 않습니다(write.csv 로 내보내세요)",
    ".rds": "R `.rds` 는 읽지 않습니다(write.csv 로 내보내세요)",
    ".dta": "Stata `.dta` 는 읽지 않습니다",
    ".xlsm": "매크로 워크북(.xlsm)은 읽지 않습니다(.xlsx 로 저장하세요)",
    ".zip": "압축 파일은 풀어서 지정하세요",
    ".docx": "원고 형식(.docx)은 번들에서 제외합니다",
}

CSV_FIELD_LIMIT = 1 << 20
MAX_JSON_DEPTH = 40
MAX_JSON_CELLS = 200_000


@dataclass(frozen=True)
class Cell:
    """번들 안의 수치 하나 — 어디서 왔는지 끝까지 들고 다닙니다."""
    file: str                # 표시용 경로 (번들 폴더명 포함)
    rel: str                 # 번들 루트 기준 상대 경로 (번들 간 '같은 자리' 비교용)
    sheet: str               # 시트명(엑셀) 또는 ''
    row: Optional[int]
    col: str                 # 열 이름 / 키 경로 / 열 문자
    ordinal: int             # 한 셀 안에서 몇 번째 숫자인지 (`11.68 (4.08)` → 0, 1)
    raw: str                 # 셀 원문
    value: Decimal
    decimals: int
    is_percent: bool
    from_label: bool = False  # `phq9_change` 처럼 글자에 붙어 있던 숫자

    @property
    def loc(self) -> str:
        parts = []
        if self.sheet:
            parts.append(self.sheet)
        if self.row is not None:
            parts.append("%d행" % self.row)
        if self.col:
            parts.append("%s열" % self.col if self.row is not None else self.col)
        return "·".join(parts) if parts else "-"

    @property
    def coord(self) -> Tuple[str, str, Optional[int], str, int]:
        """현재 번들과 이전 번들에서 '같은 자리'를 찾을 때 쓰는 좌표.

        번들 폴더 이름은 날짜가 달라 매번 바뀌므로 **루트 기준 상대 경로**를 씁니다.
        (`분석출력_2026-08-18/statwise_sws.csv` 와 `분석출력_2026-08-03/statwise_sws.csv`
        가 같은 자리로 인식돼야 '현재 번들의 같은 위치 값'을 보여 줄 수 있습니다.)

        셀 하나에 `11.68 (4.08)` 처럼 값이 둘이면 순번까지 봐야 합니다 — 안 그러면
        SD 를 물어봤는데 평균을 "현재 값"이라고 내놓습니다.
        """
        return (self.rel, self.sheet, self.row, self.col, self.ordinal)


@dataclass
class Bundle:
    label: str
    roots: List[str]
    cells: List[Cell] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    unread: List[Tuple[str, str]] = field(default_factory=list)   # (파일, 사유)
    truncated: bool = False

    @property
    def file_count(self) -> int:
        return len(self.files_read)

    @property
    def cell_count(self) -> int:
        return len(self.cells)


def collect(roots: Iterable[str], label: str, *, max_files: int = 500,
            max_bytes: int = 20_000_000, max_cells: int = 200_000) -> Bundle:
    """번들 폴더(들)을 재귀 수집해 수치 셀을 색인합니다."""
    bundle = Bundle(label=label, roots=[resolve(r) for r in roots])
    targets: List[Tuple[str, str, str]] = []   # (표시 이름, 루트 기준 상대 경로, 실제 경로)
    walk_problems: List[Tuple[str, str]] = []
    for root in roots:
        real = resolve(root)
        if os.path.isfile(real):
            targets.append((os.path.basename(real), os.path.basename(real), real))
            continue
        base = os.path.basename(real.rstrip(os.sep)) or real

        def unreadable(error, _base=base, _real=real):
            # os.walk 는 기본적으로 권한 오류를 조용히 삼킵니다 — 하위 폴더가
            # 통째로 사라지면서 '읽지 못한 파일 0개'라고 말하게 됩니다.
            name = getattr(error, "filename", None) or _real
            walk_problems.append((os.path.join(_base,
                                               os.path.relpath(name, _real)),
                                  "폴더를 읽을 수 없습니다(%s)"
                                  % error.__class__.__name__))

        for dirpath, dirnames, filenames in os.walk(real, followlinks=False,
                                                    onerror=unreadable):
            keep = []
            for name in sorted(dirnames):
                if name.startswith(".") or name == "__MACOSX":
                    continue
                if os.path.islink(os.path.join(dirpath, name)):
                    walk_problems.append(
                        (os.path.join(base,
                                      os.path.relpath(os.path.join(dirpath, name),
                                                      real)),
                         "심볼릭 링크 폴더는 따라가지 않습니다"))
                    continue
                keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                if name.startswith(".") or name.startswith("~$"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, real)
                targets.append((os.path.join(base, rel), rel, full))
    bundle.unread.extend(walk_problems)
    seen_paths = set()
    for display, rel_path, full in targets:
        if len(bundle.files_read) >= max_files:
            bundle.unread.append((display, "파일 개수 상한(--max-files) 초과"))
            bundle.truncated = True
            continue
        ext = os.path.splitext(full)[1].lower()
        if os.path.islink(full):
            bundle.unread.append((display, "심볼릭 링크는 읽지 않습니다"))
            continue
        if ext in KNOWN_UNREADABLE:
            bundle.unread.append((display, KNOWN_UNREADABLE[ext]))
            continue
        if ext not in READABLE:
            # 조용히 넘기지 않습니다. `.html`/`.log`/`.out` 안에 든 값을 못 읽은 채
            # '출처 없음'을 선언하면, 그게 이 툴이 절대 하지 말아야 할 거짓말입니다.
            bundle.unread.append(
                (display, "지원하지 않는 형식(%s) — 읽지 않았습니다"
                 % (ext or "확장자 없음")))
            continue
        try:
            real = os.path.realpath(full)
            if real in seen_paths:
                bundle.unread.append((display, "같은 파일을 두 번 가리킵니다(중복 제외)"))
                continue
            info = os.stat(full)
            if not stat.S_ISREG(info.st_mode):
                # 이름이 `.csv` 인 FIFO 를 열면 영원히 멈춥니다(실제로 재현했습니다).
                bundle.unread.append((display, "일반 파일이 아닙니다(장치·파이프 등)"))
                continue
            size = info.st_size
        except OSError:
            bundle.unread.append((display, "파일 정보를 읽을 수 없습니다"))
            continue
        if size > max_bytes:
            bundle.unread.append(
                (display, "파일 크기 %.1fMB 가 상한(--max-bytes) 초과"
                 % (size / 1024 / 1024)))
            bundle.truncated = True
            continue
        if len(bundle.cells) >= max_cells:
            bundle.unread.append((display, "수치 셀 상한(--max-cells) 도달"))
            bundle.truncated = True
            continue
        problems: List[str] = []
        try:
            cells = list(_read_file(display, rel_path, full, ext, problems))
        except zipsafe.ArchiveError as exc:
            bundle.unread.append((display, str(exc)))
            continue
        except csv.Error as exc:
            bundle.unread.append(
                (display, "CSV 를 끝까지 읽지 못함(%s) — 셀 하나가 1MB 상한을 넘었거나 "
                          "따옴표가 닫히지 않았습니다" % exc.__class__.__name__))
            bundle.truncated = True
            continue
        except (OSError, ValueError) as exc:
            bundle.unread.append((display, "읽는 중 오류(%s)" % exc.__class__.__name__))
            bundle.truncated = True
            continue
        seen_paths.add(real)
        bundle.files_read.append(display)
        for problem in problems:
            bundle.unread.append((display, problem))
            bundle.truncated = True
        room = max_cells - len(bundle.cells)
        if len(cells) > room:
            cells = cells[:room]
            bundle.truncated = True
            bundle.unread.append((display, "수치 셀 상한(--max-cells)에 걸려 일부만 읽음"))
        bundle.cells.extend(cells)
    return bundle


def _read_file(display: str, rel: str, path: str, ext: str,
               problems: List[str]) -> Iterable[Cell]:
    if ext == ".xlsx":
        return _read_xlsx_cells(display, rel, path, problems)
    if ext == ".json":
        return _read_json(display, rel, path, problems)
    if ext in (".csv", ".tsv", ".tab"):
        return _read_csv(display, rel, path, ext)
    return _read_textfile(display, rel, path, ext)


def _load_text(path: str) -> str:
    with open(path, "rb") as handle:
        data = handle.read()
    if b"\x00" in data[:4096]:
        raise ValueError("binary")
    text, _ = decode_bytes(data)
    return text


def _read_csv(display: str, rel: str, path: str, ext: str) -> List[Cell]:
    text = _load_text(path)
    delimiter = "\t" if ext in (".tsv", ".tab") else _sniff(text)
    old_limit = csv.field_size_limit()
    csv.field_size_limit(CSV_FIELD_LIMIT)
    try:
        # `text.splitlines()` 를 넘기면 따옴표 안의 줄바꿈이 사라지면서 위아래 값이
        # 구분자 없이 붙어 버립니다(`11\n22` → `1122`). 값이 통째로 사라지고
        # '출처 없음' 치명이 됩니다. StringIO 로 넘겨 csv 모듈이 직접 줄을 나누게 합니다.
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    finally:
        csv.field_size_limit(old_limit)
    if not rows:
        return []
    header = _header_labels(rows[0])
    cells: List[Cell] = []
    for row_no, row in enumerate(rows, start=1):
        for col_no, raw in enumerate(row):
            if not raw or not raw.strip():
                continue
            label = header[col_no] if col_no < len(header) else "열%d" % (col_no + 1)
            cells.extend(_cells_from(display, rel, "", row_no, label, raw))
    return cells


def _sniff(text: str) -> str:
    for line in text.splitlines():
        if not line.strip():
            continue
        counts = {d: line.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] > 0 else ","
    return ","


def _header_labels(row: List[str]) -> List[str]:
    labels: List[str] = []
    used: Dict[str, int] = {}
    for i, name in enumerate(row):
        clean = re.sub(r"\s+", " ", normalize_simple(name or "")).strip()
        if not clean or _is_numeric(clean):
            clean = "열%d" % (i + 1)
        if clean in used:
            used[clean] += 1
            clean = "%s#%d" % (clean, used[clean])
        else:
            used[clean] = 1
        labels.append(clean)
    return labels


def _is_numeric(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


def _read_json(display: str, rel: str, path: str,
               problems: List[str]) -> List[Cell]:
    text = _load_text(path)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("JSON 형식이 아님")
    cells: List[Cell] = []
    dropped: List[str] = []
    _walk_json(display, rel, data, "", cells, 0, dropped)
    if dropped:
        problems.append("JSON 일부를 읽지 못함(%s)" % " · ".join(sorted(set(dropped))))
    return cells


def _walk_json(display: str, rel: str, node, path: str, cells: List[Cell],
               depth: int, dropped: List[str]) -> None:
    if depth > MAX_JSON_DEPTH:
        dropped.append("중첩 %d단계 초과" % MAX_JSON_DEPTH)
        return
    if len(cells) > MAX_JSON_CELLS:
        dropped.append("수치 %d개 초과" % MAX_JSON_CELLS)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (path, key) if path else str(key)
            _walk_json(display, rel, value, child, cells, depth + 1, dropped)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_json(display, rel, value, "%s[%d]" % (path, i), cells,
                       depth + 1, dropped)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        raw = repr(node) if isinstance(node, float) else str(node)
        cells.extend(_cells_from(display, rel, "", None, path or "(root)", raw))
    elif isinstance(node, str):
        cells.extend(_cells_from(display, rel, "", None, path or "(root)", node))


_MD_ROW = re.compile(r"^\s*\|")
_MD_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _read_textfile(display: str, rel: str, path: str, ext: str) -> List[Cell]:
    text = _load_text(path)
    cells: List[Cell] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    table_row = 0
    in_table = False
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            in_table = False
            table_row = 0
            continue
        if ext in (".md", ".markdown") and _MD_ROW.match(line):
            if _MD_SEP.match(line):
                continue
            if not in_table:
                in_table = True
                table_row = 0
            table_row += 1
            body = line.strip().strip("|")
            for col_no, cell in enumerate(re.split(r"(?<!\\)\|", body), start=1):
                if cell.strip():
                    cells.extend(_cells_from(display, rel, "", line_no,
                                             "%d열" % col_no, cell.strip()))
            continue
        in_table = False
        cells.extend(_cells_from(display, rel, "", line_no, "", line.strip()))
    return cells


def _read_xlsx_cells(display: str, rel: str, path: str,
                     problems: List[str]) -> List[Cell]:
    cells: List[Cell] = []
    for sheet, row, col, text in read_xlsx(path, problems):
        cells.extend(_cells_from(display, rel, sheet, row, col, text))
    return cells


def _cells_from(file: str, rel: str, sheet: str, row: Optional[int], col: str,
                raw: str) -> List[Cell]:
    text = normalize_simple(raw)
    out: List[Cell] = []
    for i, (value, decimals, _token, is_percent, in_label) in \
            enumerate(cell_numbers(text)):
        out.append(Cell(file=file, rel=rel, sheet=sheet, row=row, col=col,
                        ordinal=i, raw=raw.strip()[:120], value=value,
                        decimals=decimals, is_percent=is_percent,
                        from_label=in_label))
    return out


def require_outputs(outputs: Optional[List[str]]) -> None:
    """번들 없이 도는 순간 이 툴은 numcheck 의 열등한 재탕입니다 — 그래서 막습니다."""
    if not outputs:
        raise InputError(
            "`--outputs` 가 없습니다. tracecheck 는 원고 숫자를 **분석 출력 파일과**\n"
            "  대조하는 툴이라, 번들 없이는 아무 판정도 하지 않습니다.\n"
            "  예) tracecheck 원고.docx --outputs 분석출력_2026-08-18/\n"
            "  (원고 안의 산술 검증만 필요하면 numcheck 를, 형식 점검은 draftcheck 를 쓰세요.)")
