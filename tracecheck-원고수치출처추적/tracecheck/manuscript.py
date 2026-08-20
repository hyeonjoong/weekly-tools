"""원고 파싱 — `.docx` / `.md` / `.tex` / `.txt` 를 '블록' 목록으로 읽습니다.

블록 하나는 문단 하나, 표 셀 하나, 캡션 한 줄입니다. 각 블록은 어느 **절**
(Abstract / Introduction / Methods / Results / Discussion / References)에
속하는지 분류돼 있고, 표 셀은 (표 번호, 행, 열) 좌표를 유지합니다.

절 분류가 중요한 이유: Introduction·Discussion 의 숫자는 대부분 **선행연구
인용값**이라 우리 분석 출력에 있을 리가 없습니다. 그걸 대조하면 첫 실행에
'출처 없음' 수십 건이 뜨고, 그 리포트는 두 번 다시 열리지 않습니다.

이 파서는 이 폴더 안에서 처음부터 다시 쓴 것입니다 — 같은 저장소의
numcheck·draftcheck·revcheck 파서를 import 하지도 복사하지도 않습니다.
서로 다른 목적(산술 검증 / 형식 점검 / 개정 대조)에 맞춰 각자 진화해야 하고,
공유하면 한쪽을 고칠 때 다른 쪽이 조용히 깨집니다.
"""

import io
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import zipsafe
from .safety import InputError
from .textnorm import normalize

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SECTIONS = ("abstract", "introduction", "methods", "results",
            "discussion", "references", "other")

SECTION_LABEL = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "methods": "Methods",
    "results": "Results",
    "discussion": "Discussion",
    "references": "References",
    "other": "기타",
    "tables": "표",
    "captions": "캡션",
}

_SECTION_PATTERNS = [
    ("abstract", r"(abstract|summary|초록|요약)"),
    ("introduction", r"(introduction|background|서\s*론|배\s*경)"),
    ("methods", r"(materials?\s+and\s+methods?|patients?\s+and\s+methods?|"
                r"methods?|methodology|연구\s*방법|대상\s*및\s*방법|방\s*법|재료\s*및\s*방법)"),
    ("results", r"(results?|findings|연구\s*결과|결\s*과)"),
    ("discussion", r"(discussion|conclusions?|comments?|고\s*찰|결\s*론|논\s*의)"),
    ("references", r"(references?|bibliography|literature\s+cited|참고\s*문헌|인용\s*문헌)"),
    ("other", r"(acknowledge?ments?|funding|conflicts?\s+of\s+interest|"
              r"author\s+contributions?|data\s+availability|supplementary|appendix|"
              r"keywords?|감사의\s*글|이해\s*상충|저자\s*기여|핵심어|주요어)"),
]

# 헤딩 앞머리의 번호("3.", "III.", "제 3 장")를 떼고 본문만 봅니다.
_HEAD_PREFIX = re.compile(r"^\s*(?:제?\s*\d+\s*[.)장절]?|[IVXivx]+\s*[.)])\s*")
_CAPTION = re.compile(
    r"^\s*(?:supplementary\s+)?(?:table|tbl\.?|figure|fig\.?|표|그림|부록\s*표)\s*"
    r"(?:s\s*)?\d+\s*[.:)\-–]?\s*", re.IGNORECASE)


@dataclass
class Block:
    """원고의 한 조각."""
    index: int                       # 0-based 블록 순번
    line: int                        # 줄 번호(.docx 는 문단 순번)
    section: str                     # SECTIONS 중 하나
    kind: str                        # 'para' | 'table' | 'caption' | 'heading'
    text: str                        # 원문 그대로
    norm: str = ""                   # 정규화본
    idx_map: List[int] = field(default_factory=list)
    table_no: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None

    def __post_init__(self):
        if not self.norm:
            self.norm, self.idx_map = normalize(self.text)

    @property
    def loc(self) -> str:
        if self.kind == "table" and self.table_no is not None:
            if self.row is not None and self.col is not None:
                return "표%d 셀(%d행,%d열)" % (self.table_no, self.row, self.col)
            return "표%d" % self.table_no
        return SECTION_LABEL.get(self.section, self.section)

    @property
    def target_key(self) -> str:
        """`--sections` 와 맞춰 볼 키."""
        if self.kind == "table":
            return "tables"
        if self.kind == "caption":
            return "captions"
        return self.section


@dataclass
class Manuscript:
    path: str
    fmt: str
    blocks: List[Block]
    line_kind: str                   # '줄 번호' | '문단 번호'
    table_count: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def paragraph_count(self) -> int:
        return sum(1 for b in self.blocks if b.kind in ("para", "caption", "heading"))


def classify_heading(text: str) -> Optional[str]:
    """헤딩 문자열 → 절 이름. 절 헤딩이 아니면 None."""
    body = _HEAD_PREFIX.sub("", text or "").strip()
    body = body.strip("*_#·-—– \t:.")
    if not body or len(body) > 60:
        return None
    lowered = body.lower()
    for name, pattern in _SECTION_PATTERNS:
        if re.match(r"^%s\b" % pattern, lowered, re.IGNORECASE):
            return name
        if re.match(r"^%s$" % pattern, lowered, re.IGNORECASE):
            return name
    return None


def is_caption(text: str) -> bool:
    return bool(_CAPTION.match((text or "").strip()))


def read_manuscript(path: str) -> Manuscript:
    """확장자로 형식을 정해 원고를 읽습니다."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _read_docx(path)
    if ext in (".md", ".markdown"):
        return _read_text(path, "md")
    if ext == ".tex":
        return _read_text(path, "tex")
    if ext in (".txt", ".text", ""):
        return _read_text(path, "txt")
    if ext == ".doc":
        raise InputError(
            "구형 `.doc` 는 읽지 않습니다 — 워드에서 `.docx` 로 저장한 뒤 다시 실행하세요.")
    if ext == ".pdf":
        raise InputError(
            "PDF 는 읽지 않습니다(README 한계 절 참조) — `.docx`/`.md`/`.tex`/`.txt` 를 쓰세요.")
    raise InputError("지원하지 않는 원고 형식입니다: %s (.docx/.md/.tex/.txt)" % ext)


# --------------------------------------------------------------------------- #
# 텍스트 계열
# --------------------------------------------------------------------------- #

def decode_bytes(data: bytes) -> Tuple[str, Optional[str]]:
    """인코딩을 순서대로 시도합니다. (문자열, 경고) 를 돌려줍니다."""
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16"):
        try:
            return data.decode(enc), (None if enc.startswith("utf-8") else
                                      "인코딩을 %s 로 읽었습니다" % enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace"), \
        "인코딩을 판별하지 못해 깨진 문자를 치환했습니다(대조 정확도가 떨어질 수 있음)"


def _read_text(path: str, fmt: str) -> Manuscript:
    with open(path, "rb") as handle:
        data = handle.read()
    text, note = decode_bytes(data)
    notes = [note] if note else []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if fmt == "md":
        blocks, tables = _parse_md(lines)
    elif fmt == "tex":
        blocks, tables = _parse_tex(lines)
    else:
        blocks, tables = _parse_plain(lines)
    return Manuscript(path=path, fmt=fmt, blocks=blocks, line_kind="줄 번호",
                      table_count=tables, notes=notes)


def _walk_sections(blocks: List[Block]) -> None:
    """헤딩을 만나면 그 아래 블록의 절을 갈아 끼웁니다(표·캡션 포함)."""
    current = "other"
    for block in blocks:
        if block.kind == "heading":
            found = classify_heading(block.text)
            if found:
                current = found
            block.section = current
        else:
            block.section = current


def _parse_md(lines: List[str]) -> Tuple[List[Block], int]:
    blocks: List[Block] = []
    table_no = 0
    i = 0
    idx = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            blocks.append(Block(idx, i + 1, "other", "heading",
                                re.sub(r"^#{1,6}\s+", "", stripped)))
            idx += 1
            i += 1
            continue
        # 파이프 표: 헤더 줄 + 구분 줄(---)
        if stripped.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", lines[i + 1]) and \
                "|" in lines[i + 1]:
            table_no += 1
            row_no = 0
            while i < len(lines) and lines[i].strip().startswith("|"):
                line = lines[i].strip()
                if re.match(r"^\|?[\s:|-]*-[\s:|-]*\|?$", line):
                    i += 1
                    continue
                row_no += 1
                cells = _split_md_row(line)
                for col_no, cell in enumerate(cells, start=1):
                    if cell.strip():
                        blocks.append(Block(idx, i + 1, "other", "table", cell.strip(),
                                            table_no=table_no, row=row_no, col=col_no))
                        idx += 1
                i += 1
            continue
        kind = "caption" if is_caption(stripped) else "para"
        blocks.append(Block(idx, i + 1, "other", kind, stripped))
        idx += 1
        i += 1
    _walk_sections(blocks)
    return blocks, table_no


def _split_md_row(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    # 이스케이프된 파이프(\|)는 셀 구분이 아닙니다.
    parts = re.split(r"(?<!\\)\|", body)
    return [p.replace("\\|", "|") for p in parts]


_TEX_ENV = re.compile(r"\\(begin|end)\s*\{([^}]*)\}")
_TEX_CMD = re.compile(r"\\(?:section|subsection|subsubsection|paragraph)\*?\s*\{")
_TEX_CAPTION = re.compile(r"\\caption\s*\{")


def _parse_tex(lines: List[str]) -> Tuple[List[Block], int]:
    blocks: List[Block] = []
    table_no = 0
    idx = 0
    in_table = False
    table_row = 0
    pending: List[Tuple[int, str]] = []

    def flush():
        nonlocal idx
        if not pending:
            return
        line_no = pending[0][0]
        text = " ".join(t for _, t in pending).strip()
        pending.clear()
        if not text:
            return
        kind = "caption" if is_caption(text) else "para"
        blocks.append(Block(idx, line_no, "other", kind, text))
        idx += 1

    for i, raw in enumerate(lines):
        line = _strip_tex_comment(raw)
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        env = _TEX_ENV.search(stripped)
        if env:
            name = env.group(2).strip("*")
            if name in ("tabular", "tabularx", "longtable", "array"):
                flush()
                if env.group(1) == "begin":
                    in_table = True
                    table_no += 1
                    table_row = 0
                else:
                    in_table = False
                continue
            if name == "abstract":
                flush()
                blocks.append(Block(idx, i + 1, "other", "heading", "Abstract"))
                idx += 1
                continue
            if env.group(1) == "end" and name in ("table", "figure"):
                flush()
                continue
        if in_table:
            body = re.sub(r"\\hline|\\toprule|\\midrule|\\bottomrule", "", stripped)
            for chunk in body.split(r"\\"):
                cells = [c for c in re.split(r"(?<!\\)&", chunk)]
                if not any(c.strip() for c in cells):
                    continue
                table_row += 1
                for col_no, cell in enumerate(cells, start=1):
                    text = _clean_tex(cell)
                    if text:
                        blocks.append(Block(idx, i + 1, "other", "table", text,
                                            table_no=table_no, row=table_row,
                                            col=col_no))
                        idx += 1
            continue
        head = _TEX_CMD.search(stripped)
        if head:
            flush()
            title = _brace_body(stripped, head.end() - 1)
            blocks.append(Block(idx, i + 1, "other", "heading", _clean_tex(title)))
            idx += 1
            continue
        cap = _TEX_CAPTION.search(stripped)
        if cap:
            flush()
            body = _brace_body(stripped, cap.end() - 1)
            blocks.append(Block(idx, i + 1, "other", "caption", _clean_tex(body)))
            idx += 1
            continue
        cleaned = _clean_tex(stripped)
        if cleaned:
            pending.append((i + 1, cleaned))
    flush()
    _walk_sections(blocks)
    return blocks, table_no


def _strip_tex_comment(line: str) -> str:
    out = []
    escaped = False
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == "%":
            break
        out.append(ch)
    return "".join(out)


def _brace_body(text: str, open_pos: int) -> str:
    depth = 0
    out = []
    for ch in text[open_pos:]:
        if ch == "{":
            depth += 1
            if depth == 1:
                continue
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        if depth >= 1:
            out.append(ch)
    return "".join(out)


def _clean_tex(text: str) -> str:
    text = re.sub(r"\\(?:label|ref|cite[a-z]*|footnote|index)\s*\{[^}]*\}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\s*\*?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = text.replace("\\%", "%").replace("~", " ").replace("&", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_plain(lines: List[str]) -> Tuple[List[Block], int]:
    blocks: List[Block] = []
    idx = 0
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if classify_heading(stripped) and len(stripped) <= 60:
            blocks.append(Block(idx, i + 1, "other", "heading", stripped))
        elif is_caption(stripped):
            blocks.append(Block(idx, i + 1, "other", "caption", stripped))
        else:
            blocks.append(Block(idx, i + 1, "other", "para", stripped))
        idx += 1
    _walk_sections(blocks)
    return blocks, 0


# --------------------------------------------------------------------------- #
# .docx
# --------------------------------------------------------------------------- #

def _read_docx(path: str) -> Manuscript:
    try:
        zf = zipsafe.open_zip(path)
    except zipsafe.ArchiveError as exc:
        raise InputError("원고 `.docx` 를 열 수 없습니다: %s" % exc)
    notes: List[str] = []
    try:
        names = zf.namelist()
        member = "word/document.xml"
        if member not in names:
            candidates = [n for n in names if re.match(r"word/document\d*\.xml$", n)]
            if not candidates:
                raise InputError("`.docx` 안에 word/document.xml 이 없습니다(워드 파일이 맞나요?).")
            member = candidates[0]
        data = zipsafe.guard_xml(zipsafe.read_member(zf, member), "document.xml")
    except zipsafe.ArchiveError as exc:
        raise InputError("원고 `.docx` 를 읽을 수 없습니다: %s" % exc)
    finally:
        zf.close()
    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError:
        raise InputError("`.docx` 의 XML 이 손상돼 파싱할 수 없습니다.")

    body = root.find(W + "body")
    if body is None:
        raise InputError("`.docx` 에 본문(body)이 없습니다.")

    blocks: List[Block] = []
    counter = [0]
    table_no = [0]
    _docx_walk(body, blocks, counter, table_no, depth=0)
    _walk_sections(blocks)
    if table_no[0] == 0:
        notes.append("표를 하나도 찾지 못했습니다 — 표가 이미지이면 읽을 수 없습니다")
    return Manuscript(path=path, fmt="docx", blocks=blocks,
                      line_kind="문단 번호", table_count=table_no[0], notes=notes)


def _docx_walk(parent, blocks, counter, table_no, depth):
    for child in parent:
        tag = child.tag
        if tag == W + "p":
            text = _docx_paragraph_text(child)
            if not text.strip():
                continue
            counter[0] += 1
            style = _docx_style(child)
            if style and re.match(r"(heading|제목|title)", style, re.IGNORECASE):
                kind = "heading"
            elif style and re.match(r"caption", style, re.IGNORECASE):
                kind = "caption"
            elif is_caption(text):
                kind = "caption"
            elif classify_heading(text) and len(text.strip()) <= 60:
                kind = "heading"
            else:
                kind = "para"
            blocks.append(Block(len(blocks), counter[0], "other", kind, text.strip()))
        elif tag == W + "tbl":
            if depth >= 3:
                continue                     # 3중 이상 중첩 표는 좌표가 의미 없습니다
            table_no[0] += 1
            _docx_table(child, blocks, counter, table_no, depth)
        elif tag in (W + "sdt", W + "sdtContent"):
            _docx_walk(child, blocks, counter, table_no, depth)


def _docx_table(tbl, blocks, counter, table_no, depth):
    my_no = table_no[0]
    row_no = 0
    for tr in tbl.findall(W + "tr"):
        row_no += 1
        col_no = 0
        for tc in tr.findall(W + "tc"):
            col_no += 1
            span = _docx_gridspan(tc)
            text = " ".join(
                _docx_paragraph_text(p) for p in tc.findall(W + "p")).strip()
            nested = tc.findall(W + "tbl")
            if nested and depth < 2:
                for inner in nested:
                    table_no[0] += 1
                    _docx_table(inner, blocks, counter, table_no, depth + 1)
            if text:
                counter[0] += 1
                blocks.append(Block(len(blocks), counter[0], "other", "table", text,
                                    table_no=my_no, row=row_no, col=col_no))
            col_no += span - 1


def _docx_gridspan(tc) -> int:
    pr = tc.find(W + "tcPr")
    if pr is None:
        return 1
    span = pr.find(W + "gridSpan")
    if span is None:
        return 1
    try:
        return max(1, int(span.get(W + "val", "1")))
    except (TypeError, ValueError):
        return 1


def _docx_style(paragraph) -> str:
    pr = paragraph.find(W + "pPr")
    if pr is None:
        return ""
    style = pr.find(W + "pStyle")
    if style is None:
        return ""
    return style.get(W + "val", "") or ""


def _docx_paragraph_text(paragraph) -> str:
    parts: List[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == W + "t":
            parts.append(node.text or "")
        elif tag == W + "tab":
            parts.append(" ")
        elif tag in (W + "br", W + "cr"):
            parts.append(" ")
        elif tag == W + "delText":
            continue                         # 변경내용 추적: 삭제된 글자는 버립니다
    return "".join(parts)
