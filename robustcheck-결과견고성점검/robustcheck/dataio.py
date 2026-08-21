"""CSV 읽기 — 인코딩 자동 처리 · 피험자 1행 강제.

`joinaudit` 의 `merged.csv`(UTF-8 BOM, 첫 열 `subject_id`)를 그대로 받는다.
한글 임상 CSV 는 cp949 로 저장되는 일이 흔해 BOM → utf-8 → cp949 순으로
시도하고, **어느 인코딩으로 읽었는지 리포트에 남긴다**(조용히 고르지 않는다).

이 툴은 **와이드(1행 = 1피험자)** 만 받는다. long 포맷(시점별 여러 행)이
들어오면 첫 행을 몰래 고르지 않고 exit 2 로 거절하면서, 시점 열 후보와
`--timepoint 열=값` 사용법을 알려 준다.
"""

import csv
import io
import math
import os
import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "InputError",
    "Table",
    "read_table",
    "parse_number",
    "MAX_BYTES",
]

# 피험자 단위 분석용 표다. 200MB 를 넘기면 입력이 잘못된 것이다.
MAX_BYTES = 200 * 1024 * 1024
# 사람이 손으로 만든 분석표의 현실적 상한. 넘으면 잘라 읽지 않고 거절한다.
MAX_ROWS = 200_000

_ENCODINGS: Tuple[Tuple[str, bytes], ...] = (
    ("utf-8-sig", b"\xef\xbb\xbf"),
    ("utf-16", b"\xff\xfe"),
    ("utf-16", b"\xfe\xff"),
)
_FALLBACKS = ("utf-8", "cp949", "euc-kr")

# 결측으로 볼 문자열. 대소문자 무시.
_NA_TOKENS = {
    "", "na", "n/a", "nan", "none", "null", "nil", ".", "-", "--",
    "결측", "미측정", "없음", "#n/a", "#null!",
}

# 천단위 구분 콤마만 허용한다. 유럽식 소수점(`3,14`)을 콤마째 지워 314 로 읽으면
# 결론이 조용히 100배 어긋난다 — 추측하지 말고 결측으로 두는 편이 낫다.
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_SPACES = re.compile(r"[\s\u00a0\u3000]")


class InputError(Exception):
    """입력·인자 오류 (종료코드 2)."""


class Table:
    """헤더 + 행. 값은 전부 문자열이고 숫자 변환은 필요할 때만 한다."""

    __slots__ = ("columns", "rows", "encoding", "path", "delimiter", "ragged")

    def __init__(
        self,
        columns: List[str],
        rows: List[List[str]],
        encoding: str,
        path: str,
        delimiter: str,
        ragged: int = 0,
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.encoding = encoding
        self.path = path
        self.delimiter = delimiter
        # 헤더와 칸 수가 다른 행의 개수. 채우거나 자르되 **조용히 하지 않는다**
        # — 따옴표 안 넣은 콤마 하나가 피험자를 통째로 지울 수 있다.
        self.ragged = ragged

    def index_of(self, name: str) -> int:
        try:
            return self.columns.index(name)
        except ValueError:
            raise InputError(
                "열 '%s' 이(가) 파일에 없습니다. 이 파일의 열: %s"
                % (name, ", ".join(self.columns) or "(없음)")
            )

    def column(self, name: str) -> List[str]:
        idx = self.index_of(name)
        return [row[idx] if idx < len(row) else "" for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)


def _decode(raw: bytes) -> Tuple[str, str]:
    """(텍스트, 인코딩 이름). 실패하면 InputError."""
    for name, bom in _ENCODINGS:
        if raw.startswith(bom):
            try:
                return raw.decode(name), name
            except UnicodeDecodeError:
                continue
    for name in _FALLBACKS:
        try:
            return raw.decode(name), name
        except UnicodeDecodeError:
            continue
    raise InputError(
        "파일 인코딩을 알 수 없습니다(utf-8 / cp949 / euc-kr / utf-16 모두 실패). "
        "CSV 를 UTF-8 로 다시 저장해 주세요."
    )


# 눈에 보이지 않으면서 문자열을 갈라놓는 문자들(제로폭·양방향 서식).
_INVISIBLE = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
                                      0x2060, 0xFEFF, 0x180E))


def _norm(cell: str) -> str:
    """셀 하나를 정규화 — NFC + 비가시 문자 제거 + 공백 정리."""
    text = unicodedata.normalize("NFC", cell)
    if any(ch in text for ch in _INVISIBLE):
        text = "".join(ch for ch in text if ch not in _INVISIBLE)
    return text.strip()


def _sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def read_table(path: str) -> Table:
    """CSV/TSV 한 장을 읽는다. 원본은 읽기 전용으로만 연다."""
    if not path:
        raise InputError("입력 파일 경로가 비었습니다.")
    if not os.path.exists(path):
        raise InputError("입력 파일이 없습니다: %s" % path)
    if os.path.isdir(path):
        raise InputError("입력이 폴더입니다(파일이어야 합니다): %s" % path)
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise InputError(
            "입력 파일이 너무 큽니다 (%.1f MB > %d MB). 피험자 단위 분석표가 맞습니까?"
            % (size / 1024.0 / 1024.0, MAX_BYTES // (1024 * 1024))
        )
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.strip():
        raise InputError("입력 파일이 비어 있습니다: %s" % path)
    text, encoding = _decode(raw)
    # 널 바이트가 섞인 파일은 csv 모듈이 예외를 던진다 — 먼저 정리한다.
    text = text.replace("\x00", "")
    # 클래식 맥(CR 만) 파일에는 "\n" 이 없어 파일 전체가 헤더로 잡힌다.
    first_line = re.split(r"\r\n|\r|\n", text, maxsplit=1)[0]
    delimiter = _sniff_delimiter(first_line)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise InputError("CSV 를 읽을 수 없습니다: %s" % exc)
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise InputError("데이터 행이 없습니다: %s" % path)
    header = [_norm(cell).lstrip("﻿") for cell in rows[0]]
    if len(header) != len(set(header)):
        dupes = sorted({c for c in header if header.count(c) > 1})
        raise InputError(
            "헤더에 같은 이름의 열이 두 번 이상 있습니다: %s. "
            "어느 쪽을 쓸지 추측하지 않습니다 — 열 이름을 구분해 주세요."
            % ", ".join(dupes)
        )
    body = rows[1:]
    if not body:
        raise InputError(
            "헤더만 있고 데이터 행이 없습니다: %s (열: %s)"
            % (path, ", ".join(header))
        )
    if len(body) > MAX_ROWS:
        raise InputError(
            "행이 %d 개입니다(상한 %d). 잘라 읽지 않고 멈춥니다 — "
            "피험자당 1행인 분석표가 맞습니까?" % (len(body), MAX_ROWS)
        )
    width = len(header)
    ragged = sum(1 for row in body if len(row) != width)
    # macOS 가 만든 한글 CSV 는 NFD(자모 분리)로 저장되는 일이 흔하다. 정규화하지
    # 않으면 화면에 똑같이 보이는 '치료군'이 **두 개의 군**이 되고, 같은 피험자가
    # 두 명으로 들어와 중복 ID 검사를 그대로 통과한다(실측).
    normalised = [[_norm(row[i]) if i < len(row) else "" for i in range(width)]
                  for row in body]
    return Table(header, normalised, encoding, path, delimiter, ragged)


def parse_number(text: str) -> Optional[float]:
    """숫자 문자열 → float. 결측·비숫자는 None.

    받는 것: 정수·소수·지수(`1e5`)·전각 숫자·천단위 콤마(`1,234.5`).
    **받지 않는 것**(전부 결측 처리 후 자백): 유럽식 소수점 `3,14`,
    파이썬 리터럴 `1_000`, `inf`/`nan`, 단위가 붙은 `12kg`.
    무엇을 뜻하는지 추측하는 순간 결론이 조용히 어긋나기 때문이다.
    """
    if text is None:
        return None
    stripped = text.strip()
    if stripped.lower() in _NA_TOKENS:
        return None
    # 전각 숫자·마이너스 정규화
    stripped = stripped.translate(
        str.maketrans("０１２３４５６７８９．－＋", "0123456789.-+")
    )
    cleaned = _SPACES.sub("", stripped)
    if not cleaned:
        return None
    if "," in cleaned:
        if not _THOUSANDS.match(cleaned):
            # `3,14` / `1,2,3` 등 — 무엇을 뜻하는지 추측하지 않는다.
            return None
        cleaned = cleaned.replace(",", "")
    if "_" in cleaned:
        # 파이썬 리터럴 규칙(1_000)은 CSV 관례가 아니다.
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def normalise_id(text: str) -> str:
    """피험자 ID 표기 정규화 — NFC/공백/전각만 정리한다. **퍼지 매칭은 없다.**"""
    cleaned = _norm(text or "").replace("　", " ")
    cleaned = cleaned.translate(
        str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
                      "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    )
    return re.sub(r"\s+", " ", cleaned)


def find_duplicate_ids(ids: Sequence[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in ids:
        counts[value] = counts.get(value, 0) + 1
    return {k: v for k, v in counts.items() if v > 1}
