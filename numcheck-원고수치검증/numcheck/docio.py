"""원고 파일 → 줄 목록. numcheck 에서 **파일을 여는 유일한 곳**이다.

어떤 경로도 쓰기 모드로 열지 않는다 — 원본 원고는 절대 수정되지 않는다.
네트워크 호출도 없다.

지원 포맷
    ``.docx``  stdlib ``zipfile`` + ``xml.etree.ElementTree`` 로 ``word/document.xml``
               (있으면 각주·미주도)을 직접 읽는다. **표는 행 단위로 한 줄**이 되며
               셀은 ``|`` 로 이어 붙인다 — ``23`` 과 ``48`` 이 서로 다른 셀에 있어도
               `23/48 (47.9%)` 같은 claim 을 한 줄 안에서 볼 수 있어야 하기 때문이다.
               추적 변경의 삭제분(``w:del``)은 최종본에 없는 글자이므로 제외한다.
    ``.md``    ``|`` 로 시작하는 줄은 표로 표시.
    ``.tex``   주석 제거, table/figure 환경은 표로 표시.
    ``.txt``   평문.

줄 번호는 텍스트 포맷에서는 실제 줄, ``.docx`` 에서는 문단(표는 행) 번호이며
둘 다 1 부터 센다.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = [
    "Line",
    "Manuscript",
    "ManuscriptError",
    "read_manuscript",
    "manuscript_from_text",
    "SUPPORTED_SUFFIXES",
]

# ── 안전 한도 (zip bomb / 폭주 방어) ─────────────────────────────────────────
MAX_FILE_BYTES = 120 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
MAX_LINES = 400_000
MAX_XML_DEPTH = 200
MAX_LINE_CHARS = 20_000  # 표 한 행이 비정상적으로 길 때 잘라 낸다
# XML **노드 수** 상한. 압축 해제 크기만 재면 방어가 안 된다 — 60 KB 짜리
# .docx 에 빈 <w:t/> 를 680만 개 넣으면 ElementTree 가 800 MB 를 쓴다.
# 실제 원고(100쪽)는 수만~20만 노드 수준이다.
MAX_XML_TAGS = 1_000_000
# 파트 하나의 크기 상한. 노드 수만 세면 **속성**으로 우회된다 — `<` 6개짜리
# 32 MB 파트(속성 400만 개)로 RSS 1.5 GB 를 쓰게 할 수 있었다.
MAX_PART_BYTES = 12 * 1024 * 1024

SUPPORTED_SUFFIXES = (".docx", ".md", ".markdown", ".txt", ".tex")


class ManuscriptError(Exception):
    """원고를 읽을 수 없을 때. CLI 가 사람이 읽을 메시지로 그대로 보여준다."""


@dataclass
class Line:
    """원고의 한 줄(.docx 는 한 문단 또는 표 한 행)."""

    no: int
    text: str
    kind: str = "body"  # body | table | footnote
    section: str = ""  # 나중에 sections.py 가 채운다

    @property
    def stripped(self) -> str:
        return self.text.strip()


@dataclass
class Manuscript:
    path: Path
    fmt: str  # docx | md | tex | txt
    lines: List[Line]
    notes: List[str] = field(default_factory=list)
    encoding: Optional[str] = None
    table_rows: int = 0
    deleted_runs: int = 0
    # 원고의 일부를 잘라 냈는가. 노트에 적는 것만으로는 종료코드 0 을 되돌릴 수
    # 없다 — 잘린 뒷부분에 있던 오류는 '검사했는데 없었다' 와 구분되지 않는다.
    truncated: bool = False

    @property
    def line_label(self) -> str:
        return "문단" if self.fmt == "docx" else "줄"

    @property
    def word_count(self) -> int:
        return sum(len(ln.text.split()) for ln in self.lines if ln.kind == "body")

    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


# ── 바이트 읽기 / 디코딩 ─────────────────────────────────────────────────────

# utf-16 은 **BOM 이 있을 때만** 시도한다. 목록에 넣어 두면 짝수 길이의 거의 모든
# 단일바이트 파일이 utf-16 으로 "성공적으로" 디코드돼, 파일 크기의 홀짝에 따라
# 읽기 결과가 달라지고 인코딩 메모가 거짓말을 한다.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1")


def _decode(raw: bytes) -> Tuple[str, str]:
    """한국 연구자의 원고는 종종 CP949 로 저장돼 있다."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(대체문자)"


def _read_bytes(path: Path) -> bytes:
    # FIFO·/dev/zero 는 st_size 가 0 이라 크기 상한을 통과한 뒤 read() 에서
    # 영원히 멈춘다. `numcheck <(pandoc ...)` 로 평범하게 도달할 수 있다.
    try:
        if not path.is_file():
            raise ManuscriptError(
                f"{path.name} 은 일반 파일이 아닙니다(파이프·장치 등). "
                "원고 파일을 먼저 저장한 뒤 그 경로를 지정하세요."
            )
    except OSError as exc:
        raise ManuscriptError(f"파일 정보를 읽을 수 없습니다: {path.name} ({exc})") from exc
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ManuscriptError(f"파일 정보를 읽을 수 없습니다: {path.name} ({exc})") from exc
    if size > MAX_FILE_BYTES:
        raise ManuscriptError(
            f"파일이 너무 큽니다({size / 1e6:.0f} MB). 원고 파일이 맞는지 확인하세요."
        )
    try:
        with open(path, "rb") as fh:  # 읽기 전용 — 이 모듈은 절대 쓰지 않는다
            data = fh.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ManuscriptError(
                    "파일이 너무 큽니다. 원고 파일이 맞는지 확인하세요."
                )
            return data
    except OSError as exc:
        raise ManuscriptError(f"파일을 열 수 없습니다: {path.name} ({exc})") from exc


# ── .docx ────────────────────────────────────────────────────────────────────

# 텍스트에 넣으면 안 되는 하위 트리
#   del/delText  추적변경으로 삭제된 글자 — 최종본에 없다
#   instrText    필드 코드(ADDIN EN.CITE …) — 사람이 보는 글자가 아니다
#   Fallback     mc:AlternateContent 의 구형 렌더링 사본(텍스트 중복)
_SKIP_TAGS = {"del", "delText", "instrText", "Fallback", "rPr", "pPr", "sectPr", "tblPr"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml(data: bytes, what: str) -> ET.Element:
    # 정상 .docx 에 DTD/ENTITY 는 없다. 있으면 billion-laughs 류로 보고 거부한다.
    # 정상 .docx 파트는 UTF-8 이고, UTF-8 XML 에는 NUL 바이트가 없다. UTF-16 은
    # BOM 이 없어도 expat 이 바이트 패턴으로 알아채므로, **BOM 만 막으면 뚫린다**
    # (DTD 폭탄이 바이트 검사를 그대로 우회한다). NUL 이 있으면 거부한다.
    if b"\x00" in data[:4096] or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise ManuscriptError(
            f"{what}: UTF-8 이 아닌(UTF-16 등) XML 파트는 지원하지 않습니다."
        )
    if len(data) > MAX_PART_BYTES:
        raise ManuscriptError(
            f"{what}: XML 파트가 너무 큽니다({len(data) / 1e6:.0f} MB > "
            f"{MAX_PART_BYTES / 1e6:.0f} MB)."
        )
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ManuscriptError(f"{what}: 비정상적인 XML 선언(DTD/ENTITY)이 있어 거부했습니다.")
    # 요소 수 + 속성 수. 둘 다 ElementTree 의 메모리를 선형으로 늘린다.
    nodes = data.count(b"<") + data.count(b'="')
    if nodes > MAX_XML_TAGS:
        raise ManuscriptError(
            f"{what}: XML 요소가 비정상적으로 많습니다({nodes:,}개 > {MAX_XML_TAGS:,}). "
            "정상적인 원고 파일이 맞는지 확인하세요."
        )
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ManuscriptError(f"{what}: XML 을 해석할 수 없습니다 ({exc}).") from exc


def _open_docx(raw: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ManuscriptError(
            ".docx 파일을 열 수 없습니다(손상되었거나 구형 .doc 일 수 있습니다). "
            "Word 에서 '다른 이름으로 저장 → .docx' 로 다시 저장해 보세요."
        ) from exc
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ManuscriptError(".docx 내부 파일 수가 비정상적으로 많습니다.")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise ManuscriptError(".docx 압축 해제 크기가 비정상적으로 큽니다.")
    return zf


class _DocxWalker:
    """document.xml 한 파트를 (텍스트, 종류) 목록으로 편다.

    표는 ``w:tr`` 한 개가 한 줄이 되고 셀은 ``|`` 로 이어 붙는다.
    """

    def __init__(self) -> None:
        self.units: List[Tuple[str, str]] = []
        self.deleted_runs = 0
        self.table_rows = 0

    # -- 텍스트 수집 ---------------------------------------------------------
    def _text_of(self, el: ET.Element, depth: int = 0) -> str:
        parts: List[str] = []
        self._collect(el, parts, depth)
        return re.sub(r"[ \t]+", " ", "".join(parts)).strip()

    def _collect(self, el: ET.Element, parts: List[str], depth: int) -> None:
        if depth > MAX_XML_DEPTH:
            raise ManuscriptError(
                f".docx 내부 구조가 비정상적으로 깊습니다({MAX_XML_DEPTH}단계 초과)."
            )
        tag = _local(el.tag)
        if tag == "del":
            self.deleted_runs += 1
            return
        if tag in _SKIP_TAGS:
            return
        if tag == "t":
            parts.append(el.text or "")
        elif tag in ("tab", "br", "cr"):
            parts.append(" ")
        elif tag == "noBreakHyphen":
            parts.append("-")
        elif tag == "p" and parts:
            parts.append(" ")  # 텍스트 상자·셀 안 문단 경계
        for child in el:
            self._collect(child, parts, depth + 1)

    # -- 구조 순회 -----------------------------------------------------------
    def walk(self, el: ET.Element, depth: int = 0) -> None:
        if depth > MAX_XML_DEPTH:
            raise ManuscriptError(
                f".docx 내부 구조가 비정상적으로 깊습니다({MAX_XML_DEPTH}단계 초과)."
            )
        tag = _local(el.tag)
        if tag == "del":
            self.deleted_runs += 1
            return
        if tag in _SKIP_TAGS:
            return
        if tag == "tbl":
            self._walk_table(el, depth)
            return
        if tag == "p":
            self.units.append((self._text_of(el, depth), "body"))
            return
        for child in el:
            self.walk(child, depth + 1)

    def _walk_table(self, tbl: ET.Element, depth: int) -> None:
        for row in tbl:
            if _local(row.tag) != "tr":
                continue
            cells: List[str] = []
            for cell in row:
                if _local(cell.tag) != "tc":
                    continue
                cells.append(self._text_of(cell, depth + 1))
            text = " | ".join(c for c in cells if c is not None).strip()
            self.table_rows += 1
            # 자르기는 add_part 한 곳에서만 한다 — 여기서 미리 자르면 잘렸다는
            # 사실이 기록되지 않아 리포트가 조용히 거짓말을 한다.
            self.units.append((text, "table"))


def _docx_lines(raw: bytes) -> Tuple[List[Line], List[str], int, int]:
    zf = _open_docx(raw)
    names = set(zf.namelist())
    if "word/document.xml" not in names:
        raise ManuscriptError("Word 문서가 아닙니다(word/document.xml 없음).")
    notes: List[str] = []
    lines: List[Line] = []
    deleted = 0
    table_rows = 0
    counter = 0
    truncated = False
    long_units: List[int] = []

    def add_part(member: str, kind_override: Optional[str] = None) -> None:
        """한 파트를 읽어 줄로 편다.

        본문이 깨지면 검증 자체가 불가능하므로 오류를 올린다. 각주·미주는 선택
        파트이므로 깨졌다면 그 사실만 남기고 본문 검증은 계속한다.
        """
        nonlocal deleted, table_rows, counter, truncated
        optional = member != "word/document.xml"
        try:
            # `zf.read(member)` 는 zip 중앙 디렉터리에 **적힌** 크기를 믿는다.
            # 그 값은 공격자가 정하므로, 148바이트라고 선언한 1MB 파일이 실제로는
            # 1GB 로 풀려도 상한 검사를 그냥 통과한다(RSS 2GB 를 확인했다).
            # 선언값이 아니라 실제로 읽는 바이트에 상한을 건다.
            with zf.open(member) as fh:
                data = fh.read(MAX_PART_BYTES + 1)
            if len(data) > MAX_PART_BYTES:
                raise ManuscriptError(
                    f"{member}: XML 파트가 너무 큽니다(압축 해제 상한 "
                    f"{MAX_PART_BYTES / 1e6:.0f} MB 초과). .docx 가 맞는지 확인하세요."
                )
        except ManuscriptError:
            raise
        except (KeyError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            if optional:
                notes.append(f"{member} 를 읽지 못해 건너뜀 ({exc}).")
                return
            raise ManuscriptError(
                f"본문(word/document.xml)을 읽을 수 없습니다 ({exc}). "
                "암호가 걸렸거나 손상된 파일일 수 있습니다."
            ) from exc
        try:
            worker = _DocxWalker()
            worker.walk(_parse_xml(data, member))
        except ManuscriptError:
            if not optional:
                raise
            notes.append(f"{member} 를 해석하지 못해 건너뛰었습니다(본문 검증은 계속).")
            return
        deleted += worker.deleted_runs
        table_rows += worker.table_rows
        for text, kind in worker.units:
            if counter >= MAX_LINES:
                truncated = True
                return
            counter += 1
            if len(text) > MAX_LINE_CHARS:
                long_units.append(counter)
            lines.append(Line(counter, text[:MAX_LINE_CHARS], kind_override or kind))

    add_part("word/document.xml")
    for member, label in (("word/footnotes.xml", "각주"), ("word/endnotes.xml", "미주")):
        if member in names:
            before = len(lines)
            add_part(member, kind_override="footnote")
            added = sum(1 for ln in lines[before:] if ln.stripped)
            if added:
                notes.append(f"{label} {added}개 문단도 함께 읽었습니다.")
    if truncated:
        notes.append(f"문단이 {MAX_LINES}개를 넘어 이후는 잘랐습니다.")
    if long_units:
        notes.append(
            f"{MAX_LINE_CHARS:,}자를 넘는 문단/표 행 {len(long_units)}개의 뒷부분을 "
            "잘랐습니다 — 그 뒤의 숫자는 **검사되지 않았습니다.**"
        )
    if deleted:
        notes.append(f"추적 변경의 삭제 표시 {deleted}곳을 제외했습니다(최종본 기준).")
    if table_rows:
        notes.append(f"표 {table_rows}행을 셀까지 읽어 한 줄로 이어 붙였습니다.")
    return lines, notes, deleted, table_rows, truncated or bool(long_units)


# ── 텍스트 포맷 ──────────────────────────────────────────────────────────────

_TEX_COMMENT = re.compile(r"(?<!\\)%.*$")
_TEX_TABLE_BEGIN = re.compile(r"\\begin\{(table|tabular|figure|longtable)\*?\}")
_TEX_TABLE_END = re.compile(r"\\end\{(table|tabular|figure|longtable)\*?\}")

# LaTeX 에서 백분율은 **반드시** `\%` 로 쓴다. 이걸 풀지 않으면 .tex 원고의
# 백분율이 전부 보이지 않고, 게다가 "건너뜀"으로도 안 잡혀 커버리지 자백이
# 거짓말이 된다(적대적 검토에서 나온 결함). 주석 제거 **뒤에** 푼다.
_TEX_UNESCAPE = (
    ("\\%", "%"), ("\\&", "&"), ("\\_", "_"), ("\\#", "#"), ("\\$", "$"),
    ("$\\pm$", "±"), ("\\pm", "±"), ("$\\times$", "×"), ("\\times", "×"),
    ("\\chi^2", "χ2"), ("$\\chi^2$", "χ2"), ("\\leq", "≤"), ("\\geq", "≥"),
    ("~", " "),
)


def _tex_unescape(text: str) -> str:
    for bad, good in _TEX_UNESCAPE:
        text = text.replace(bad, good)
    return text


# 문장이 끝났음을 알리는 꼬리. 여기서 끝나지 않은 줄은 다음 줄과 이어 붙인다.
_SENT_END = re.compile(r"(?:[.!?。！？:;]|\\\\|<br\s*/?>)[\"'”’\)\]]*\s*$")
# 새 블록의 시작 — 절대 앞줄에 이어 붙이지 않는다
_BLOCK_START = re.compile(
    r"^\s*(?:#{1,6}\s|[-*+]\s|\d{1,3}[.)]\s|>|\||\\(?:section|subsection|subsubsection|"
    r"begin|end|item|caption)\b)"
)
MAX_JOIN_LINES = 25


def _join_wrapped(lines: List[Line]) -> Tuple[List[Line], int]:
    """하드 랩(자동 줄바꿈)으로 잘린 문장을 한 줄로 되붙인다.

    ``t(44) = 3.05,`` 에서 줄이 끊기고 다음 줄이 ``p = .004.`` 로 시작하면, 줄
    단위로만 보는 검사는 **둘 다 놓친다** — 통계량도 p 도 짝을 못 찾기 때문이다.
    조용히 놓치는 것이 이 툴의 최악의 실패 모드이므로, 문장이 끝나지 않은 줄은
    다음 줄과 이어 붙인다.

    문장이 끝난 줄은 잇지 않는다. 그래야 지적의 줄번호가 실제 그 문장의 줄을
    가리킨다(원고에서 바로 찾을 수 있어야 한다).
    """
    out: List[Line] = []
    joined = 0
    i = 0
    while i < len(lines):
        current = lines[i]
        if current.kind != "body" or not current.stripped:
            out.append(current)
            i += 1
            continue
        text = current.text
        count = 1
        while (
            i + 1 < len(lines)
            and count < MAX_JOIN_LINES
            and not _SENT_END.search(text)
            and lines[i + 1].kind == "body"
            and lines[i + 1].stripped
            and not _BLOCK_START.match(lines[i + 1].text)
            and not _BLOCK_START.match(text)
        ):
            i += 1
            text = text.rstrip() + " " + lines[i].text.lstrip()
            count += 1
            joined += 1
        out.append(Line(current.no, text[:MAX_LINE_CHARS], current.kind))
        i += 1
    return out, joined


def _text_lines(text: str, fmt: str) -> Tuple[List[Line], List[str], int]:
    notes: List[str] = []
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    line_limit_hit = len(raw_lines) > MAX_LINES
    if line_limit_hit:
        notes.append(f"줄이 {MAX_LINES}개를 넘어 이후는 잘랐습니다.")
        raw_lines = raw_lines[:MAX_LINES]
    lines: List[Line] = []
    table_rows = 0
    truncated = 0
    in_tex_table = False
    in_code_fence = False
    for i, one in enumerate(raw_lines, start=1):
        kind = "body"
        if len(one) > MAX_LINE_CHARS:
            truncated += 1
        out = one[:MAX_LINE_CHARS]
        if fmt == "tex":
            out = _tex_unescape(_TEX_COMMENT.sub("", out))
            if _TEX_TABLE_BEGIN.search(out):
                in_tex_table = True
            if in_tex_table:
                kind = "table"
            if _TEX_TABLE_END.search(out):
                in_tex_table = False
        elif fmt == "md":
            if out.lstrip().startswith("```"):
                in_code_fence = not in_code_fence
                kind = "table"
            elif in_code_fence or out.lstrip().startswith("|"):
                kind = "table"
        if kind == "table":
            table_rows += 1
        lines.append(Line(i, out, kind))
    if truncated:
        notes.append(
            f"{MAX_LINE_CHARS:,}자를 넘는 줄 {truncated}개의 뒷부분을 잘랐습니다 — "
            "그 뒤의 숫자는 **검사되지 않았고 건너뜀 집계에도 없습니다.**"
        )
    lines, joined = _join_wrapped(lines)
    if joined:
        notes.append(
            f"문장 도중에 줄바꿈된 {joined}줄을 앞줄에 이어 붙여 읽었습니다"
            " (줄번호는 그 문장이 시작하는 줄)."
        )
    return lines, notes, table_rows, line_limit_hit or bool(truncated)


def manuscript_from_text(text: str, fmt: str = "md", name: str = "(문자열)") -> Manuscript:
    """파일 없이 문자열을 원고로 다룬다(라이브러리 사용·테스트용)."""
    if fmt not in ("md", "tex", "txt"):
        raise ManuscriptError(f"문자열에서 만들 수 있는 형식은 md/tex/txt 입니다: {fmt!r}")
    lines, notes, rows, cut = _text_lines(text, fmt)
    return Manuscript(Path(name), fmt, lines, notes, "utf-8", rows, truncated=cut)


def read_manuscript(path) -> Manuscript:
    """원고 파일을 읽어 :class:`Manuscript` 로 (읽기 전용)."""
    p = Path(path)
    try:
        exists = p.exists()
    except OSError as exc:
        raise ManuscriptError(f"경로를 확인할 수 없습니다 ({exc}).") from exc
    if not exists:
        raise ManuscriptError(f"파일이 없습니다: {p}")
    if p.is_dir():
        raise ManuscriptError(f"폴더가 아니라 원고 파일 하나를 지정하세요: {p}")
    suffix = p.suffix.lower()
    raw = _read_bytes(p)
    if suffix == ".docx" or (not suffix and raw[:2] == b"PK"):
        lines, notes, deleted, rows, cut = _docx_lines(raw)
        return Manuscript(p, "docx", lines, notes, None, rows, deleted, truncated=cut)
    if raw[:2] == b"PK":
        raise ManuscriptError(
            f"{p.name} 은 zip 형식(.docx/.xlsx 등)으로 보입니다. 확장자를 .docx 로 맞춰 주세요."
        )
    if raw[:5] == b"%PDF-":
        raise ManuscriptError(
            "PDF 는 지원하지 않습니다(v1). 편집 가능한 원본(.docx/.md/.tex/.txt)에 실행하세요. "
            "PDF 에서 뽑은 텍스트는 표가 뒤섞여 '검사했다'는 말 자체가 거짓이 됩니다."
        )
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ManuscriptError("구형 .doc 파일입니다. Word 에서 .docx 로 다시 저장하세요.")
    fmt = {"md": "md", "markdown": "md", "tex": "tex"}.get(suffix.lstrip("."), "txt")
    text, enc = _decode(raw)
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        lines, notes, rows, cut = _text_lines(text, "txt")
        notes.insert(0, f"'{suffix}' 확장자는 평문으로 읽었습니다.")
        return Manuscript(p, "txt", lines, notes, enc, rows, truncated=cut)
    lines, notes, rows, cut = _text_lines(text, fmt)
    if enc not in ("utf-8", "utf-8-sig"):
        notes.append(f"{enc} 인코딩으로 읽었습니다.")
    return Manuscript(p, fmt, lines, notes, enc, rows, truncated=cut)
