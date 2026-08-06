"""원고 파일을 읽어 줄(문단) 목록과 섹션 구조로 바꾸는 계층.

이 모듈은 draftcheck에서 **파일을 여는 유일한 곳**이며, 어떤 경로도 쓰기 모드로
열지 않는다(원본 원고는 절대 수정되지 않는다). 네트워크 호출도 없다.

지원 포맷
    .docx  stdlib ``zipfile`` + ``xml.etree.ElementTree`` 로 ``word/document.xml``
           (그리고 있으면 footnotes/endnotes)을 직접 파싱한다. 추적 변경의
           **삭제분(``w:del``)은 반드시 제외**한다 — 지워진 문장의 인용을 세면
           교차 대조가 통째로 틀리기 때문이다.
    .md    마크다운. ``|`` 로 시작하는 줄은 표로 표시한다.
    .tex   LaTeX. 주석(``%``)을 제거하고 table/figure 환경을 표로 표시한다.
    .txt   평문.

줄 번호
    텍스트 포맷에서는 실제 줄 번호, ``.docx``에서는 **문단 번호**(빈 문단 포함)다.
    두 경우 모두 1부터 시작한다.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 안전 한도 ────────────────────────────────────────────────────────────────
# zip bomb / 거대 파일 방어. 실제 원고는 수십 MB를 넘지 않는다.
MAX_FILE_BYTES = 120 * 1024 * 1024
# 압축 해제 크기 한도는 넉넉하면 의미가 없다. XML 트리는 원문 크기의 수십 배까지
# 부풀기 때문에(400MB를 허용하면 RSS 수 GB), 실제 원고 규모에 맞춰 좁게 잡는다.
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
MAX_LINES = 400_000
# 재귀 파서가 파이썬 재귀 한도에 부딪히기 전에 우리가 먼저 깔끔하게 거절한다.
MAX_XML_DEPTH = 200

SUPPORTED_SUFFIXES = (".docx", ".md", ".markdown", ".txt", ".tex")


class ManuscriptError(Exception):
    """원고를 읽을 수 없을 때. CLI가 사람이 읽을 메시지로 그대로 보여준다."""


# ── 자료형 ───────────────────────────────────────────────────────────────────


@dataclass
class Line:
    """원고의 한 줄(또는 .docx 한 문단)."""

    no: int
    text: str
    kind: str = "body"  # body | table | footnote

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
    field_citations: int = 0  # EndNote 등 필드 인용 감지 수
    deleted_runs: int = 0  # 제외한 추적변경 삭제 구간 수

    @property
    def line_label(self) -> str:
        return "문단" if self.fmt == "docx" else "줄"

    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


@dataclass
class Sections:
    """찾아낸 섹션 경계. 못 찾은 섹션은 빈 목록 + found 플래그 False."""

    title: str = ""
    title_line: int = 0
    abstract: List[Line] = field(default_factory=list)
    body: List[Line] = field(default_factory=list)
    references: List[Line] = field(default_factory=list)
    tail: List[Line] = field(default_factory=list)  # 참고문헌 뒤(그림 범례·표 등)
    captions: List[Line] = field(default_factory=list)
    headings: Dict[str, int] = field(default_factory=dict)  # key -> 줄번호
    abstract_found: bool = False
    references_found: bool = False
    notes: List[str] = field(default_factory=list)  # 구조 인식에 대한 자기 보고


# ── 파일 읽기 ────────────────────────────────────────────────────────────────

_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16", "latin-1")


def _decode(raw: bytes) -> Tuple[str, str]:
    """바이트를 텍스트로. 한국 연구자의 원고는 종종 CP949로 저장돼 있다."""
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
    # latin-1은 절대 실패하지 않으므로 여기 도달하지 않는다.
    return raw.decode("utf-8", errors="replace"), "utf-8(대체문자)"


def _read_bytes(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - 아래 open에서 같은 오류를 낸다
        raise ManuscriptError(f"파일 정보를 읽을 수 없습니다: {path} ({exc})") from exc
    if size > MAX_FILE_BYTES:
        raise ManuscriptError(
            f"파일이 너무 큽니다({size / 1e6:.0f} MB). 원고 파일이 맞는지 확인하세요."
        )
    try:
        with open(path, "rb") as fh:  # 읽기 전용 — 이 모듈은 절대 쓰지 않는다
            return fh.read()
    except OSError as exc:
        raise ManuscriptError(f"파일을 열 수 없습니다: {path} ({exc})") from exc


# ── .docx ────────────────────────────────────────────────────────────────────

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 텍스트에 포함하면 안 되는 하위 트리:
#   del/delText  추적변경으로 '삭제된' 문장 — 최종 원고에 없는 글자다
#   instrText    필드 코드(ADDIN EN.CITE ...) — 사람이 보는 글자가 아니다
#   Fallback     mc:AlternateContent 의 구형 렌더링 사본(중복 텍스트)
#   proofErr 등  속성 노드
_SKIP_TAGS = {"del", "delText", "instrText", "Fallback", "rPr", "pPr", "sectPr"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml(data: bytes, what: str) -> ET.Element:
    # DTD/엔티티는 정상 .docx에 존재하지 않는다. 있으면 폭탄일 가능성이 높으므로
    # 파싱 자체를 거부한다(billion laughs 방어).
    # 앞부분만 훑으면 긴 주석으로 창을 밀어내 우회할 수 있으므로 **전체**를 본다.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ManuscriptError(f"{what}: 비정상적인 XML 선언(DTD/ENTITY)이 있어 거부했습니다.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ManuscriptError(f"{what}: XML을 해석할 수 없습니다 ({exc}).") from exc


def _open_docx(raw: bytes) -> zipfile.ZipFile:
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ManuscriptError(
            ".docx 파일을 열 수 없습니다(손상되었거나 .doc 구형 포맷일 수 있습니다). "
            "Word에서 '다른 이름으로 저장 → .docx'로 다시 저장해 보세요."
        ) from exc
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ManuscriptError(".docx 내부 파일 수가 비정상적으로 많습니다.")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise ManuscriptError(".docx 압축 해제 크기가 비정상적으로 큽니다.")
    return zf


class _DocxText:
    """document.xml 한 파트를 문단 목록으로 펴는 워커."""

    def __init__(self) -> None:
        self.paragraphs: List[Tuple[str, str]] = []  # (text, kind)
        self.deleted_runs = 0
        self.field_citations = 0

    def _note_field(self, el: ET.Element) -> None:
        code = (el.text or "").upper()
        if "CITE" in code or "ADDIN" in code or "BIBLIOGRAPHY" in code:
            self.field_citations += 1

    def walk(self, el: ET.Element, in_table: bool = False, depth: int = 0) -> None:
        if depth > MAX_XML_DEPTH:
            raise ManuscriptError(
                ".docx 내부 구조가 비정상적으로 깊게 중첩되어 있습니다 "
                f"({MAX_XML_DEPTH}단계 초과). 정상적인 원고 파일이 맞는지 확인하세요."
            )
        tag = _local(el.tag)
        if tag == "del":
            self.deleted_runs += 1
            return
        if tag == "instrText":
            self._note_field(el)
            return
        if tag in _SKIP_TAGS:
            return
        if tag == "tbl":
            for child in el:
                self.walk(child, True, depth + 1)
            return
        if tag == "p":
            self.paragraphs.append((self._para_text(el), "table" if in_table else "body"))
            return
        for child in el:
            self.walk(child, in_table, depth + 1)

    def _para_text(self, para: ET.Element) -> str:
        parts: List[str] = []

        def collect(el: ET.Element, depth: int) -> None:
            if depth > MAX_XML_DEPTH:
                raise ManuscriptError(
                    ".docx 문단 구조가 비정상적으로 깊게 중첩되어 있습니다 "
                    f"({MAX_XML_DEPTH}단계 초과)."
                )
            tag = _local(el.tag)
            if tag == "del":
                self.deleted_runs += 1
                return
            if tag == "instrText":
                self._note_field(el)
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
                # 텍스트 상자(w:txbxContent) 안에는 문단이 중첩된다. 구분자를 넣지
                # 않으면 두 문단이 "…sleepFigure 2" 처럼 붙어 단어와 언급이 사라진다.
                parts.append(" ")
            for child in el:
                collect(child, depth + 1)

        for child in para:
            collect(child, 1)
        return "".join(parts).strip()


def _docx_lines(raw: bytes) -> Tuple[List[Line], List[str], int, int]:
    zf = _open_docx(raw)
    names = set(zf.namelist())
    if "word/document.xml" not in names:
        raise ManuscriptError(
            "Word 문서가 아닙니다(word/document.xml 없음). .docx 인지 확인하세요."
        )
    notes: List[str] = []
    lines: List[Line] = []
    deleted = 0
    fields = 0
    n = 0

    def add_part(member: str, kind_override: Optional[str] = None) -> None:
        """한 파트를 읽어 문단으로 편다.

        본문(``word/document.xml``)이 깨지면 점검 자체가 불가능하므로 오류를 올린다.
        각주·미주는 **선택 파트**이므로, 깨졌다면 그 사실만 남기고 본문 점검은 계속한다
        (각주 하나 때문에 원고 전체를 못 보는 일이 없도록).
        """
        nonlocal deleted, fields, n
        optional = member != "word/document.xml"
        try:
            data = zf.read(member)
        except (KeyError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            message = f"{member} 를 읽지 못해 건너뜀 ({exc})."
            if optional:
                notes.append(message)
                return
            raise ManuscriptError(
                f"본문(word/document.xml)을 읽을 수 없습니다 ({exc}). "
                "암호가 걸렸거나 손상된 파일일 수 있습니다."
            ) from exc
        try:
            root = _parse_xml(data, member)
            worker = _DocxText()
            worker.walk(root)
        except ManuscriptError:
            if not optional:
                raise
            notes.append(f"{member} 를 해석하지 못해 건너뛰었습니다(본문 점검은 계속합니다).")
            return
        deleted += worker.deleted_runs
        fields += worker.field_citations
        for text, kind in worker.paragraphs:
            n += 1
            if n > MAX_LINES:
                notes.append(f"문단이 {MAX_LINES}개를 넘어 이후는 잘랐습니다.")
                return
            lines.append(Line(n, text, kind_override or kind))

    add_part("word/document.xml")
    for member, label in (
        ("word/footnotes.xml", "각주"),
        ("word/endnotes.xml", "미주"),
    ):
        if member in names:
            before = len(lines)
            add_part(member, kind_override="footnote")
            added = sum(1 for ln in lines[before:] if ln.stripped)
            if added:
                notes.append(f"{label} {added}개 문단을 함께 읽었습니다(본문 계수에서는 제외).")
    if deleted:
        notes.append(
            f"추적 변경의 삭제 표시 {deleted}곳을 제외했습니다(최종본 기준으로 점검)."
        )
    if fields:
        notes.append(
            f"EndNote/Word 필드 인용 코드 {fields}개를 감지했습니다 — "
            "필드가 화면에 보여주는 결과 텍스트만 읽습니다."
        )
    return lines, notes, deleted, fields


# ── 텍스트 포맷 ──────────────────────────────────────────────────────────────

_TEX_COMMENT = re.compile(r"(?<!\\)%.*$")
_TEX_TABLE_BEGIN = re.compile(r"\\begin\{(table|tabular|figure|longtable)\*?\}")
_TEX_TABLE_END = re.compile(r"\\end\{(table|tabular|figure|longtable)\*?\}")


def _text_lines(text: str, fmt: str) -> Tuple[List[Line], List[str]]:
    notes: List[str] = []
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(raw_lines) > MAX_LINES:
        notes.append(f"줄이 {MAX_LINES}개를 넘어 이후는 잘랐습니다.")
        raw_lines = raw_lines[:MAX_LINES]
    lines: List[Line] = []
    in_tex_table = False
    in_code_fence = False
    for i, text_line in enumerate(raw_lines, start=1):
        kind = "body"
        out = text_line
        if fmt == "tex":
            out = _TEX_COMMENT.sub("", out)
            if _TEX_TABLE_BEGIN.search(out):
                in_tex_table = True
            if in_tex_table:
                kind = "table"
            if _TEX_TABLE_END.search(out):
                in_tex_table = False
        elif fmt == "md":
            if out.lstrip().startswith("```"):
                in_code_fence = not in_code_fence
            if in_code_fence:
                kind = "table"  # 코드/데이터 블록은 본문 계수에서 뺀다
            elif out.lstrip().startswith("|"):
                kind = "table"
        lines.append(Line(i, out, kind))
    return lines, notes


def read_manuscript(path) -> Manuscript:
    """원고 파일을 읽어 :class:`Manuscript` 로 돌려준다(읽기 전용)."""
    p = Path(path)
    if not p.exists():
        raise ManuscriptError(f"파일이 없습니다: {p}")
    if p.is_dir():
        raise ManuscriptError(f"폴더가 아니라 원고 파일을 지정하세요: {p}")
    suffix = p.suffix.lower()
    raw = _read_bytes(p)
    if suffix == ".docx" or (not suffix and raw[:2] == b"PK"):
        lines, notes, deleted, fields = _docx_lines(raw)
        return Manuscript(p, "docx", lines, notes, None, fields, deleted)
    if raw[:2] == b"PK":
        raise ManuscriptError(
            f"{p.name} 은 zip 형식(.docx/.xlsx 등)으로 보입니다. 확장자를 .docx 로 맞춰 주세요."
        )
    if raw[:5] == b"%PDF-":
        raise ManuscriptError(
            "PDF는 지원하지 않습니다. 투고 전 점검은 편집 가능한 원본(.docx/.md/.tex)에 하세요."
        )
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ManuscriptError(
            "구형 .doc 파일입니다. Word에서 .docx 로 다시 저장한 뒤 실행하세요."
        )
    fmt = {"md": "md", "markdown": "md", "tex": "tex"}.get(suffix.lstrip("."), "txt")
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        # 알 수 없는 확장자도 평문으로 읽어 준다(막지 않되, 알려는 준다).
        text, enc = _decode(raw)
        lines, notes = _text_lines(text, "txt")
        notes.insert(0, f"'{suffix}' 확장자는 평문으로 읽었습니다.")
        return Manuscript(p, "txt", lines, notes, enc)
    text, enc = _decode(raw)
    lines, notes = _text_lines(text, fmt)
    if enc not in ("utf-8", "utf-8-sig"):
        notes.append(f"{enc} 인코딩으로 읽었습니다.")
    return Manuscript(p, fmt, lines, notes, enc)


# ── 섹션 탐지 ────────────────────────────────────────────────────────────────

_HEADING_WORDS = {
    "abstract": {
        "abstract", "structured abstract", "summary", "초록", "요약", "국문초록",
    },
    "keywords": {"keywords", "key words", "주제어", "키워드"},
    "introduction": {"introduction", "background", "서론", "배경"},
    "methods": {
        "methods", "method", "materials and methods", "methods and materials",
        "patients and methods", "subjects and methods", "방법", "연구방법", "대상 및 방법",
    },
    "results": {"results", "result", "결과", "연구결과"},
    "discussion": {"discussion", "논의", "고찰"},
    "conclusion": {"conclusion", "conclusions", "결론"},
    "references": {
        "references", "reference", "reference list", "bibliography",
        "literature cited", "참고문헌", "인용문헌", "works cited",
    },
}

# 참고문헌 뒤에 오는(=참고문헌 목록을 끝내는) 섹션들
_TAIL_PREFIXES = (
    "acknowledg", "funding", "conflict", "competing interest", "declaration",
    "author contribution", "data availability", "ethics", "supplementary",
    "supporting information", "figure legend", "figure caption", "legends",
    "tables", "table legends", "figures", "appendix", "감사", "부록", "이해상충",
    "그림 설명", "표 목록", "figure and table",
)

_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")
_TEX_HEADING = re.compile(r"^\s*\\(?:sub){0,2}section\*?\s*\{(.+?)\}\s*$")
# "2. Methods", "2.1 Materials and Methods", "III. Results" 의 번호 접두사
_NUM_PREFIX = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVXivx]{1,5})[.)]?\s+")


# 실제 작업 중인 원고의 제목에는 메모가 붙어 있다. 사용자의 진짜 원고에서 관찰된 형태:
#   "References (Vancouver style; 번호 유지, 최종 정리 필요)"
#   "Introduction / ref26 to be deleted later"
#   "Results (Primary Endpoints)"
#   "Discussion 가설이 많아서 어렵지만 일단 굵직한 것만 적음"
# 이 메모 때문에 참고문헌 섹션을 못 찾으면 인용 교차 대조가 통째로 죽는다(실제로 죽었다).
_ANNOTATION_SPLITS = (
    re.compile(r"^(.{2,40}?)\s*[(（\[].*$"),          # Head (메모…)
    re.compile(r"^(.{2,40}?)\s*[/／].*$"),            # Head / 메모
    re.compile(r"^(.{2,40}?)\s*[—–]\s.*$"),           # Head — 메모
    re.compile(r"^([A-Za-z][A-Za-z\s]{1,38}?)\s+[가-힣].*$"),  # Head 한글메모
)


def _match_heading(candidate: str) -> Optional[str]:
    if not candidate or len(candidate.split()) > 6:
        return None
    low = candidate.lower()
    for key, names in _HEADING_WORDS.items():
        if low in names:
            return key
    for prefix in _TAIL_PREFIXES:
        if low.startswith(prefix):
            return "tail"
    return None


def heading_key(text: str) -> Optional[str]:
    """이 줄이 섹션 제목이면 그 키('methods' 등), 아니면 None.

    마크다운 ``#``, LaTeX ``\\section{}``/``\\begin{thebibliography}``, 볼드 ``**...**``,
    번호 접두사("2. Methods", "2.1 Materials and Methods")를 벗겨 낸 뒤 알려진 제목
    목록과 대조한다. 정확히 맞지 않으면 **뒤에 붙은 메모를 떼고 한 번 더** 대조한다.
    """
    t = text.strip()
    if not t or len(t) > 90:
        return None
    if re.match(r"^\s*\\begin\{thebibliography\}", t):
        return "references"
    m = _MD_HEADING.match(t)
    if m:
        t = m.group(1).strip()
    else:
        m = _TEX_HEADING.match(t)
        if m:
            t = m.group(1).strip()
    t = t.strip("*_ \t")
    t = _NUM_PREFIX.sub("", t)
    # 마침표로 끝나는지는 **다듬기 전에** 봐야 한다(아래 strip이 마침표를 떼어 간다).
    is_sentence = t.rstrip().endswith(".")
    t = t.strip(" :.\t*_")
    if not t:
        return None
    key = _match_heading(t)
    if key:
        return key
    # 메모가 붙은 제목 재시도. 마침표로 끝나는 줄은 문장이므로 제외한다
    # ("Results (Table 2) are shown below." 를 제목으로 오인하지 않기 위해).
    if is_sentence:
        return None
    for pattern in _ANNOTATION_SPLITS:
        m = pattern.match(t)
        if m:
            key = _match_heading(m.group(1).strip(" :.\t*_-"))
            if key:
                return key
    return None


_CAPTION_RE = re.compile(
    r"^(?:\*{0,2}|_{0,2}|\\textbf\{)?\s*"
    r"(?P<word>Supplementary\s+Figure|Supplementary\s+Table|Figure|Fig\.?|FIGURE|"
    r"Table|TABLE|그림|표)\s*"
    r"(?P<num>[0-9]{1,3}|[IVX]{1,5})"
    # 구분자. Springer("Table 1 Baseline…")·Nature("Figure 1 | Flow") 서식까지 받는다.
    # 마지막 갈래(구두점 없는 캡션)의 대문자 조건은 **대소문자를 구분해야** 한다.
    # re.IGNORECASE 아래에서는 [A-Z]가 소문자도 받아 "Table 1 shows the…"까지
    # 캡션으로 삼켰고, 그 줄이 언급 목록에서 빠져 표가 '본문 미언급'이 됐다.
    r"(?P<sep>\s*\*{0,2}[.:)\]．]|\s*[-–—]\s|\s*[|｜]\s*|\s*$|\s*\*{2}"
    r"|\s+(?=(?-i:[A-Z가-힣])))",
    re.IGNORECASE,
)


def caption_of(text: str) -> Optional[Tuple[str, int]]:
    """줄이 그림/표 캡션이면 ('figure'|'table', 번호), 아니면 None.

    구두점 없는 형태("Table 1 Baseline characteristics")도 캡션으로 받되,
    번호 뒤 첫 글자가 대문자/한글일 때만 인정한다. 그래야 본문 문장
    "Table 1 shows the baseline…"(소문자로 이어짐)을 캡션으로 오인하지 않는다.
    """
    m = _CAPTION_RE.match(text.strip())
    if not m:
        return None
    num = _to_int(m.group("num"))
    if num is None:
        return None
    word = m.group("word").lower().replace(" ", "")
    if word.startswith("supplementary"):
        return None  # 보충 자료는 본문 번호 체계 밖 — v1에서는 다루지 않는다
    kind = "table" if word.startswith(("table", "표")) else "figure"
    return kind, num


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def _to_int(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        value = int(token)
        return value if 0 < value <= 200 else None
    return _ROMAN.get(token.lower())


def _abstract_end(
    ms: Manuscript, marks: List[Tuple[int, str]], abstract_idx: int, naive_end: int
) -> int:
    """구조화 초록(Background/Methods/Results/Conclusions 소제목)을 살려 낸다.

    소제목이 줄 하나를 통째로 차지하면 그 자리에서 초록이 끊겨 **초록 0단어**가 되고,
    초록↔본문 표본수 대조가 통째로 사라진다. 그래서 초록 구간이 비었을 때만,
    "뒤에서 같은 키가 또 나오는 라벨"(= 진짜 Methods 절이 따로 있다는 증거)을 따라
    경계를 밀어 준다. 정상 원고에서는 초록 구간이 비지 않으므로 이 경로를 타지 않는다.
    """
    if any(ln.stripped for ln in ms.lines[abstract_idx + 1 : naive_end]):
        return naive_end
    after = [(idx, key) for idx, key in marks if idx > abstract_idx]
    label_keys = {"introduction", "methods", "results", "discussion", "conclusion"}
    end = naive_end
    for pos, (idx, key) in enumerate(after):
        if key not in label_keys:
            break
        later = [k for _, k in after[pos + 1 :]]
        if key not in later:
            break  # 뒤에 같은 절이 또 있지 않다면 이건 진짜 본문 제목이다
        end = after[pos + 1][0] if pos + 1 < len(after) else len(ms.lines)
    return end


def detect_sections(ms: Manuscript) -> Sections:
    """제목/초록/본문/참고문헌/캡션 경계를 찾는다."""
    sec = Sections()
    marks: List[Tuple[int, str]] = []  # (index in ms.lines, key)
    for idx, ln in enumerate(ms.lines):
        if ln.kind == "footnote":
            continue
        key = heading_key(ln.text)
        if key:
            marks.append((idx, key))
            sec.headings.setdefault(key, ln.no)

    # 제목: 첫 비어 있지 않은 줄(섹션 제목이 아니어야 함)
    for ln in ms.lines:
        if ln.kind != "body" or not ln.stripped:
            continue
        if heading_key(ln.text):
            continue
        sec.title = re.sub(r"^\s{0,3}#{1,6}\s*", "", ln.text).strip().strip("*_ ")
        sec.title_line = ln.no
        break

    def next_mark(after: int, skip: Tuple[str, ...] = ()) -> int:
        for idx, key in marks:
            if idx > after and key not in skip:
                return idx
        return len(ms.lines)

    abstract_idx = next((i for i, k in marks if k == "abstract"), None)
    refs_idx = None
    for i, k in marks:
        if k == "references":
            refs_idx = i  # 마지막 'references' 제목을 쓴다(목차 대비)
    if refs_idx is not None:
        sec.references_found = True

    abstract_end = None
    if abstract_idx is not None:
        sec.abstract_found = True
        abstract_end = _abstract_end(ms, marks, abstract_idx, next_mark(abstract_idx))
        sec.abstract = [
            ln for ln in ms.lines[abstract_idx + 1 : abstract_end] if ln.kind != "footnote"
        ]
        if not any(ln.stripped for ln in sec.abstract):
            # 제목은 찾았는데 내용이 비었다 = 우리가 경계를 잘못 잡았다는 뜻이다.
            # 조용히 '초록 0단어'로 통과시키지 않고 못 읽었다고 밝힌다.
            sec.abstract_found = False
            sec.abstract = []
            sec.notes.append(
                "초록 제목은 찾았지만 내용을 읽지 못했습니다 "
                "(구조화 초록의 소제목이 섹션 제목과 같은 경우 등) — "
                "초록 단어수·표본수 대조를 건너뜁니다."
            )

    body_start = 0
    if abstract_idx is not None:
        body_start = abstract_end if abstract_end is not None else next_mark(abstract_idx)
        kw = next((i for i, k in marks if k == "keywords" and i > abstract_idx), None)
        if kw is not None and kw < body_start:
            body_start = next_mark(kw)
    else:
        intro = next((i for i, k in marks if k == "introduction"), None)
        body_start = intro if intro is not None else 0

    body_end = refs_idx if refs_idx is not None else len(ms.lines)
    if body_end < body_start:
        body_end = len(ms.lines)
    sec.body = list(ms.lines[body_start:body_end])

    if refs_idx is not None:
        tail_idx = next((i for i, k in marks if k == "tail" and i > refs_idx), None)
        ref_end = tail_idx if tail_idx is not None else len(ms.lines)
        sec.references = list(ms.lines[refs_idx + 1 : ref_end])
        sec.tail = list(ms.lines[ref_end:])

    for ln in ms.lines:
        if ln.kind == "footnote":
            continue
        if caption_of(ln.text):
            sec.captions.append(ln)
    return sec
