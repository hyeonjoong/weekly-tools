"""세 파일(제출본·개정본·응답서)을 읽는 **유일한** 곳.

어떤 경로도 쓰기 모드로 열지 않는다 — 원본은 절대 수정되지 않는다.
네트워크 호출도 없다(`socket`/`urllib`/`http` 를 import 하지 않으며, 테스트가
소스 전체를 정적으로 검사한다).

지원 포맷
    ``.docx``  stdlib ``zipfile`` + ``xml.etree.ElementTree`` 로 ``word/document.xml``
               을 직접 읽는다. **표는 행 하나가 한 문단**이 되고 셀은 ``|`` 로 잇는다.
               **변경내용 추적(``w:ins``/``w:del``)이 켜져 있으면 감지해서**
               '수락본 기준' 인지 '원본 기준' 인지를 리포트 첫머리에 반드시 적는다.
    ``.md``    빈 줄로 나뉜 블록. ``#`` 제목, ``|`` 표 행, ``>`` 인용은 각각 한 문단.
    ``.tex``   주석 제거, ``\\section{...}`` 은 제목으로.
    ``.txt``   빈 줄로 나뉜 블록.

문단 번호(``Para.no``)는 1부터 센다. ``.md``/``.tex``/``.txt`` 는 **실제 줄 번호**를
``line_start``/``line_end`` 에 함께 담는다(위치 참조 검증에 쓴다).
``.docx`` 에는 줄 번호가 파일 안에 존재하지 않으므로 담지 않는다 — 추정하지 않는다.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = [
    "Document",
    "DocumentError",
    "Para",
    "Tracked",
    "SUPPORTED_SUFFIXES",
    "read_document",
    "document_from_text",
]

# ── 안전 한도 (zip bomb / XML bomb / 폭주 방어) ─────────────────────────────
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_ZIP_MEMBERS = 4000
MAX_PART_BYTES = 12 * 1024 * 1024
MAX_XML_TAGS = 1_000_000  # '<' 개수. 압축 해제 크기만 재면 방어가 안 된다.
MAX_PARAS = 200_000
MAX_PARA_CHARS = 20_000
MAX_TABLE_DEPTH = 8
MAX_XML_DEPTH = 120  # 문단 하나를 감싼 태그가 이보다 깊으면 정상 워드 파일이 아니다

SUPPORTED_SUFFIXES = (".docx", ".md", ".markdown", ".txt", ".tex")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 한국어 원고의 인코딩. utf-16 은 BOM 이 있을 때만 시도한다 — 목록에 넣어 두면
# 짝수 길이의 거의 모든 파일이 "성공적으로" utf-16 으로 디코드된다.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1")

_SECTION_KEYWORDS = (
    ("abstract", "Abstract"), ("초록", "Abstract"), ("요약", "Abstract"),
    ("introduction", "Introduction"), ("서론", "Introduction"), ("배경", "Introduction"),
    ("method", "Methods"), ("materials and methods", "Methods"), ("방법", "Methods"),
    ("result", "Results"), ("결과", "Results"),
    ("discussion", "Discussion"), ("고찰", "Discussion"), ("논의", "Discussion"),
    ("conclusion", "Conclusion"), ("결론", "Conclusion"),
    ("reference", "References"), ("참고문헌", "References"), ("bibliography", "References"),
    ("acknowledg", "Acknowledgements"), ("감사", "Acknowledgements"),
    ("limitation", "Discussion"),
)

# 숫자가 조용히 바뀌면 가장 값비싼 절.
NUMERIC_SENSITIVE_SECTIONS = ("Results", "Abstract")


class DocumentError(Exception):
    """파일을 읽을 수 없을 때. CLI 가 사람이 읽을 메시지로 그대로 보여 준다.

    메시지에는 **원고 본문을 넣지 않는다** — 오류 메시지가 로그·이슈로 옮겨
    다니면서 미공개 원고 문장을 흘리는 일이 없어야 한다.
    """


@dataclass
class Para:
    """문단 하나(``.docx`` 는 문단 또는 표의 한 행)."""

    no: int
    text: str
    kind: str = "body"  # body | heading | table | quote
    line_start: int = 0  # 텍스트 포맷에서만 의미 있음(0 = 없음)
    line_end: int = 0
    italic: bool = False
    section: str = ""

    @property
    def stripped(self) -> str:
        return self.text.strip()


@dataclass
class Tracked:
    """변경내용 추적 흔적."""

    ins: int = 0
    dele: int = 0
    mode: str = "accept"  # accept(수락본) | reject(원본) | n/a

    @property
    def present(self) -> bool:
        return bool(self.ins or self.dele)

    @property
    def state_label(self) -> str:
        return "모두 수락된 상태" if self.mode == "accept" else "모두 거절된(원본) 상태"


@dataclass
class Document:
    path: Path
    fmt: str  # docx | md | tex | txt
    paras: List[Para]
    role: str = ""  # 제출본 / 개정본 / 응답서
    notes: List[str] = field(default_factory=list)
    encoding: Optional[str] = None
    tracked: Tracked = field(default_factory=Tracked)
    truncated: bool = False
    total_lines: int = 0

    @property
    def has_line_numbers(self) -> bool:
        """``.docx`` 에는 줄 번호가 없다. 있는 척하지 않는다."""
        return self.fmt in ("md", "tex", "txt")

    @property
    def body_paras(self) -> List[Para]:
        return [p for p in self.paras if p.kind in ("body", "quote")]

    def word_count(self, section: Optional[str] = None) -> int:
        words = 0
        for para in self.paras:
            if para.kind == "heading":
                continue
            if section is not None and para.section != section:
                continue
            if section is None and para.section == "References":
                continue
            words += len(para.text.split())
        return words

    def text(self) -> str:
        return "\n".join(p.text for p in self.paras)


# ── 바이트 읽기 / 디코딩 ────────────────────────────────────────────────────


def _decode(raw: bytes) -> Tuple[str, str]:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(대체문자)"


# 확장자만 바꿔 놓은 파일(워드 파일을 .md 로 저장, PDF 를 .txt 로 저장)을 그대로
# 읽으면 깨진 글자가 리포트에 찍히고, 그 깨진 글자의 숫자가 "조용히 바뀐 숫자"로
# 지적된다. 매직바이트로 먼저 막는다.
_MAGIC = (
    (b"PK\x03\x04", ".docx/.xlsx 같은 압축 파일"),
    (b"%PDF", "PDF 파일"),
    (b"\xd0\xcf\x11\xe0", "옛 워드(.doc) 파일"),
    (b"{\\rtf", "RTF 파일"),
    (b"\x7fELF", "실행 파일"),
)


def _reject_binary(raw: bytes, path: Path) -> None:
    for magic, label in _MAGIC:
        if raw.startswith(magic):
            raise DocumentError(
                f"{path.name} 파일은 실제로는 {label}입니다(확장자만 다릅니다). "
                "워드에서 열어 '다른 이름으로 저장'으로 형식을 맞춰 주세요."
            )
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return  # UTF-16 텍스트는 NUL 이 절반이다 — BOM 이 있으면 그대로 믿는다
    sample = raw[:8192]
    if not sample:
        return
    binary = sum(1 for b in sample if b < 9 or (13 < b < 32))
    if binary * 20 > len(sample):  # 5% 넘게 제어문자면 텍스트가 아니다
        raise DocumentError(
            f"{path.name} 파일은 텍스트 파일로 보이지 않습니다(제어문자가 너무 많습니다) — "
            "형식을 확인하세요."
        )


def _read_bytes(path: Path) -> bytes:
    # FIFO·/dev/zero 는 st_size 가 0 이라 크기 상한을 통과한 뒤 read() 에서 멈춘다.
    try:
        if not path.exists():
            raise DocumentError(
                f"파일을 찾을 수 없습니다: {path.name} — 경로와 파일명을 확인하세요."
            )
        if not path.is_file():
            raise DocumentError(
                f"{path.name} 파일이 일반 파일이 아닙니다(파이프·장치·폴더 등). "
                "파일을 먼저 저장한 뒤 그 경로를 지정하세요."
            )
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentError(f"파일 정보를 읽을 수 없습니다: {path.name} ({exc.strerror})") from exc
    if size > MAX_FILE_BYTES:
        raise DocumentError(
            f"{path.name} 파일이 너무 큽니다({size / 1e6:.0f} MB). 원고 파일이 맞는지 확인하세요."
        )
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise DocumentError(f"파일을 열 수 없습니다: {path.name} ({exc.strerror})") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise DocumentError(f"{path.name} 파일이 너무 큽니다. 원고 파일이 맞는지 확인하세요.")
    return raw


# ── .docx ───────────────────────────────────────────────────────────────────


def _zip_part(zf: zipfile.ZipFile, name: str, display: str) -> Optional[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise DocumentError(
            f"{display} 안의 {name} 이 비정상적으로 큽니다({info.file_size / 1e6:.0f} MB)."
        )
    with zf.open(info, "r") as fh:
        data = fh.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES:
        raise DocumentError(f"{display} 안의 {name} 이 비정상적으로 큽니다.")
    if data.count(b"<") > MAX_XML_TAGS:
        raise DocumentError(f"{display} 안의 XML 이 비정상적으로 복잡합니다(노드 과다).")
    # ElementTree 는 내부 엔티티 확장(billion laughs)에 취약하다. DTD 가 들어 있는
    # 워드 파일은 정상 파일이 아니므로 파싱 전에 거절한다.
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data[:65536].lower():
        raise DocumentError(f"{display} 안의 XML 에 DTD/엔티티가 있어 읽지 않았습니다.")
    return data


def _run_is_italic(run: ET.Element) -> bool:
    rpr = run.find(W + "rPr")
    if rpr is None:
        return False
    it = rpr.find(W + "i")
    if it is None:
        return False
    val = it.get(W + "val")
    return val not in ("0", "false", "off")


def _paragraph_text(p_elem: ET.Element, mode: str) -> Tuple[str, bool, int, int, bool]:
    """문단 XML → (텍스트, 이탤릭단락여부, w:ins 수, w:del 수).

    ``mode="accept"`` 는 삽입분을 살리고 삭제분을 버린다(= 변경 수락본).
    ``mode="reject"`` 는 그 반대(= 원본).
    """
    parts: List[str] = []
    italic_flags: List[bool] = []
    ins_count = 0
    del_count = 0
    too_deep = False

    def walk(node: ET.Element, in_ins: bool, in_del: bool, italic: bool, depth: int = 0) -> None:
        nonlocal ins_count, del_count, too_deep
        if depth > MAX_XML_DEPTH:
            too_deep = True  # 조용히 버리지 않고 리포트에 자백한다
            return
        tag = node.tag
        if tag == W + "ins":
            ins_count += 1
            in_ins = True
        elif tag == W + "del":
            del_count += 1
            in_del = True
        elif tag == W + "r":
            italic = _run_is_italic(node)
        elif tag == W + "t":
            keep = (not in_del) if mode == "accept" else (not in_ins and not in_del)
            if keep and node.text:
                parts.append(node.text)
                italic_flags.append(italic)
        elif tag == W + "delText":
            if mode == "reject" and not in_ins and node.text:
                parts.append(node.text)
                italic_flags.append(italic)
        elif tag in (W + "tab", W + "br", W + "cr"):
            parts.append(" ")
        for child in node:
            walk(child, in_ins, in_del, italic, depth + 1)

    walk(p_elem, False, False, False)
    text = "".join(parts)
    meaningful = [flag for flag, part in zip(italic_flags, parts) if part.strip()]
    is_italic = bool(meaningful) and all(meaningful)
    return text, is_italic, ins_count, del_count, too_deep


def _is_heading(p_elem: ET.Element) -> bool:
    ppr = p_elem.find(W + "pPr")
    if ppr is None:
        return False
    style = ppr.find(W + "pStyle")
    if style is None:
        return False
    val = (style.get(W + "val") or "").lower()
    return val.startswith("heading") or val in ("title", "subtitle") or "제목" in val


def _read_docx(path: Path, mode: str) -> Document:
    _read_bytes(path)  # 크기·일반파일 여부를 먼저 확인한다(바이트는 버린다)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentError(
            f"{path.name} 파일을 .docx 로 열 수 없습니다({type(exc).__name__}). "
            "워드에서 '다른 이름으로 저장 → .docx' 로 다시 저장해 보세요."
        ) from exc
    try:
        return _read_docx_parts(zf, p_or_path=path, mode=mode)
    except DocumentError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError) as exc:
        # 암호 걸린 워드 파일, 깨진 압축 스트림, 지원하지 않는 압축 방식 등.
        raise DocumentError(
            f"{path.name} 파일을 읽을 수 없습니다({type(exc).__name__}) — "
            "암호가 걸려 있거나 파일이 손상됐을 수 있습니다."
        ) from exc


def _read_docx_parts(zf: zipfile.ZipFile, p_or_path: Path, mode: str) -> Document:
    path = p_or_path
    notes: List[str] = []
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise DocumentError(f"{path.name} 안의 파일 수가 비정상적으로 많습니다.")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise DocumentError(
                f"{path.name} 의 압축 해제 크기가 비정상적으로 큽니다"
                f"({total / 1e6:.0f} MB) — 읽지 않았습니다."
            )
        data = _zip_part(zf, "word/document.xml", path.name)
        if data is None:
            raise DocumentError(
                f"{path.name} 안에 word/document.xml 이 없습니다 — "
                ".docx 가 아니거나 손상된 파일입니다(.doc 이면 .docx 로 저장하세요)."
            )
        has_footnotes = any(
            i.filename in ("word/footnotes.xml", "word/endnotes.xml") and i.file_size > 2048
            for i in infos
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocumentError(
            f"{path.name} 의 XML 을 해석할 수 없습니다(줄 {exc.position[0]} 부근)."
        ) from exc

    body = root.find(W + "body")
    if body is None:
        raise DocumentError(f"{path.name} 에 본문(w:body)이 없습니다.")

    paras: List[Para] = []
    tracked = Tracked(mode=mode)
    truncated = False

    def add(text: str, kind: str, italic: bool = False) -> bool:
        nonlocal truncated
        text = text.replace("\u0000", " ")
        if len(text) > MAX_PARA_CHARS:
            text = text[:MAX_PARA_CHARS]
            truncated = True
        if not text.strip():
            return True
        if len(paras) >= MAX_PARAS:
            truncated = True
            return False
        paras.append(Para(len(paras) + 1, text.strip(), kind, italic=italic))
        return True

    def walk_block(node: ET.Element, depth: int) -> bool:
        nonlocal truncated
        for child in node:
            tag = child.tag
            if tag == W + "p":
                text, italic, ins, dele, deep = _paragraph_text(child, mode)
                tracked.ins += ins
                tracked.dele += dele
                truncated = truncated or deep
                kind = "heading" if _is_heading(child) else "body"
                if not add(text, kind, italic):
                    return False
            elif tag == W + "tbl":
                if depth >= MAX_TABLE_DEPTH:
                    continue
                for row in child.findall(W + "tr"):
                    cells: List[str] = []
                    for cell in row.findall(W + "tc"):
                        chunks: List[str] = []
                        for para in cell.iter(W + "p"):
                            text, _italic, ins, dele, deep = _paragraph_text(para, mode)
                            truncated = truncated or deep
                            tracked.ins += ins
                            tracked.dele += dele
                            if text.strip():
                                chunks.append(text.strip())
                        cells.append(" ".join(chunks))
                    if not add(" | ".join(cells), "table"):
                        return False
            elif tag in (W + "sdt", W + "sdtContent", W + "smartTag"):
                if depth < MAX_TABLE_DEPTH and not walk_block(child, depth + 1):
                    return False
        return True

    walk_block(body, 0)

    # 변경내용 추적 배너는 리포트 첫머리(engine)가 찍는다 — 여기서 또 적으면
    # 같은 문장이 두 줄 나온다.
    if has_footnotes:
        notes.append("각주/미주는 비교 대상에서 제외했습니다(본문만 비교).")
    if truncated:
        notes.append("문서가 너무 커서 일부만 읽었습니다 — 결과를 '이상 없음'으로 보지 마세요.")

    return Document(
        path=path,
        fmt="docx",
        paras=paras,
        notes=notes,
        tracked=tracked,
        truncated=truncated,
        total_lines=0,
    )


# ── 텍스트 포맷 ─────────────────────────────────────────────────────────────

_TEX_SECTION = re.compile(r"\\(?:sub)*section\*?\s*\{([^{}]*)\}")
_TEX_SKIP = re.compile(
    r"^\s*\\(?:documentclass|usepackage|begin|end|label|bibliography|bibliographystyle"
    r"|newcommand|renewcommand|maketitle|title|author|date|includegraphics|caption\*?)\b"
)
_TEX_COMMENT = re.compile(r"(?<!\\)%.*$")
# 목록 항목(참고문헌 ``1. ...`` / ``[1] ...`` / 글머리표)은 그 자체로 한 문단이다.
# 이렇게 하지 않으면 참고문헌 40줄이 문단 하나로 뭉쳐 증감 대조가 불가능해진다.
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]\s+|\[?\d{1,3}[\]).]\s+)\S")
# 참고문헌 절 안에서는 줄 하나가 항목 하나다. 이렇게 하지 않으면 번호 없는
# (APA/Harvard) 목록이 문단 하나로 뭉쳐 증감 대조가 통째로 불가능해진다.
_REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*|\\(?:sub)*section\*?\s*\{)?\s*"
    r"(?:references|bibliography|literature cited|참고\s*문헌|인용\s*문헌)\s*\}?\s*:?\s*$",
    re.IGNORECASE,
)
# 참고문헌 뒤에 오는 다른 절(감사의 글·연구비·저자 기여·부록). ``.txt`` 에는
# ``#`` 도 ``\section`` 도 없으므로 이 짧은 제목 줄로 구간을 닫는다.
_TAIL_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:acknowledg\w*|funding|author contributions?|"
    r"conflicts? of interest|competing interests?|data availability|appendix|"
    r"supplementary[\w\s]*|감사의?\s*글|연구비|저자\s*기여|이해\s*상충|부록|보충\s*자료)"
    r"\s*:?\s*$",
    re.IGNORECASE,
)
_OTHER_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s+|\\(?:sub)*section\*?\s*\{)"
    r"(?!\s*(?:references|bibliography|참고\s*문헌))",
    re.IGNORECASE,
)


def _lines_from_text(
    text: str, fmt: str, split_lines: bool = False
) -> Tuple[List[Para], int, bool]:
    """텍스트 → 문단 목록.

    ``split_lines=True`` 는 **줄 하나를 문단 하나로** 만든다. 응답서에 쓴다:
    ``Comment 1-1: ...`` 과 ``Response: ...`` 가 빈 줄 없이 이어 붙어 있어도
    코멘트 표지를 줄 단위로 찾아야 하기 때문이다.
    """
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    total_lines = len(raw_lines)
    paras: List[Para] = []
    buffer: List[str] = []
    buf_start = 0
    truncated = False
    in_references = False

    def flush(end_line: int) -> None:
        nonlocal buffer, buf_start
        if buffer:
            joined = " ".join(part.strip() for part in buffer if part.strip()).strip()
            if joined:
                _note_truncation(len(joined))
                paras.append(
                    Para(len(paras) + 1, joined[:MAX_PARA_CHARS], "body", buf_start, end_line)
                )
        buffer = []
        buf_start = 0

    def _note_truncation(length: int) -> None:
        nonlocal truncated
        if length > MAX_PARA_CHARS:
            truncated = True

    def emit(text_line: str, kind: str, lineno: int) -> None:
        flush(lineno - 1 if lineno > 1 else lineno)
        stripped = text_line.strip()
        if stripped:
            _note_truncation(len(stripped))
            paras.append(
                Para(len(paras) + 1, stripped[:MAX_PARA_CHARS], kind, lineno, lineno)
            )

    for idx, line in enumerate(raw_lines, start=1):
        if len(paras) >= MAX_PARAS:
            truncated = True
            break
        if fmt == "tex":
            line = _TEX_COMMENT.sub("", line)
            m = _TEX_SECTION.search(line)
            if m:
                # 절 이름을 보고 참고문헌 구간을 열고 닫는다 — 이 판정을 건너뛰면
                # .tex 참고문헌 40줄이 문단 하나로 뭉쳐 증감 대조가 죽는다.
                in_references = bool(_REF_HEADING.match(m.group(1).strip()))
                emit(m.group(1), "heading", idx)
                continue
            if _TEX_SKIP.match(line):
                flush(idx - 1 if idx > 1 else idx)
                continue
        stripped = line.strip()
        if not stripped:
            flush(idx - 1 if idx > 1 else idx)
            continue
        if _REF_HEADING.match(stripped):
            in_references = True
        elif in_references and (
            _OTHER_HEADING.match(stripped) or _TAIL_HEADING.match(stripped)
        ):
            in_references = False
        if fmt in ("md", "markdown"):
            if stripped.startswith("#"):
                emit(stripped.lstrip("#").strip(), "heading", idx)
                continue
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if all(set(c) <= set("-: ") for c in cells):
                    continue  # |---|---| 구분선
                # .docx 표와 **같은 모양**으로 만든다(셀을 " | " 로 연결).
                emit(" | ".join(cells), "table", idx)
                continue
            if stripped.startswith(">"):
                emit(stripped.lstrip(">").strip(), "quote", idx)
                continue
        if split_lines or in_references or _LIST_ITEM.match(line):
            emit(stripped, "body", idx)
            continue
        if not buffer:
            buf_start = idx
        buffer.append(line)
    flush(total_lines)
    return paras, total_lines, truncated


def document_from_text(
    text: str, fmt: str = "md", name: str = "<text>", split_lines: bool = False
) -> Document:
    """테스트·파이프용: 문자열에서 바로 Document 를 만든다."""
    fmt = "md" if fmt == "markdown" else fmt
    paras, total, truncated = _lines_from_text(text, fmt, split_lines)
    doc = Document(
        path=Path(name), fmt=fmt, paras=paras, encoding="utf-8", total_lines=total,
        truncated=truncated,
    )
    tag_sections(doc)
    return doc


# ── 절(section) 태깅 ────────────────────────────────────────────────────────


def _canonical_section(title: str) -> str:
    low = title.strip().lower()
    # "3. Results" / "Ⅲ. 결과" 처럼 앞에 번호가 붙는다.
    low = re.sub(r"^[\dⅰ-ⅹⅠ-Ⅹivx]+[.)\s]+", "", low).strip()
    for key, canon in _SECTION_KEYWORDS:
        if low.startswith(key) or key in low:
            return canon
    return title.strip()[:40]


_TXT_HEADING = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(abstract|introduction|methods?|materials and methods|results?|"
    r"discussion|conclusions?|references|초록|요약|서론|방법|연구방법|결과|고찰|논의|결론|참고문헌)"
    r"\s*:?\s*$",
    re.IGNORECASE,
)


# 참고문헌 뒤에 오는 표/그림 캡션. 원고는 흔히 References 다음에 표를 붙인다 —
# 이걸 참고문헌으로 세면 "참고문헌 6편 추가" 같은 거짓말이 나온다.
_CAPTION_LINE = re.compile(
    r"^\s*(?:table|tbl|figure|fig|표|그림)\.?\s*\d{1,3}\b", re.IGNORECASE
)
FIGURE_SECTION = "Tables and Figures"


def tag_sections(doc: Document) -> None:
    """제목 문단을 기준으로 각 문단에 절 이름을 붙인다(단순 규칙)."""
    current = ""
    for para in doc.paras:
        # 참고문헌 절이 끝나고 표/그림 블록이 시작되는 자리를 잡는다.
        if current in ("References", FIGURE_SECTION) and (
            para.kind == "table" or (para.kind != "heading" and _CAPTION_LINE.match(para.text))
        ):
            current = FIGURE_SECTION
            para.section = current
            continue
        is_head = para.kind == "heading"
        if not is_head and para.kind == "body" and _TXT_HEADING.match(para.text):
            is_head = True
            para.kind = "heading"
        if is_head:
            current = _canonical_section(para.text)
        para.section = current


# ── 공개 진입점 ─────────────────────────────────────────────────────────────


def read_document(
    path, role: str = "", tracked_mode: str = "accept", split_lines: bool = False
) -> Document:
    """파일 하나를 읽어 ``Document`` 로 돌려준다. 쓰기 모드로 열지 않는다.

    ``split_lines`` 는 텍스트 포맷에서 줄 하나를 문단 하나로 만든다(응답서용).
    """
    p = Path(path)
    if p.is_symlink():
        # 심볼릭 링크는 의도치 않은 위치(예: /etc/passwd)를 가리킬 수 있다.
        raise DocumentError(
            f"{p.name} 파일은 심볼릭 링크입니다 — 실제 파일 경로를 지정하세요."
        )
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError(
            f"{p.name}: 지원하지 않는 형식입니다({suffix or '확장자 없음'}). "
            f"지원 형식은 {', '.join(SUPPORTED_SUFFIXES)} 입니다. "
            "PDF 는 읽지 않습니다 — 워드/텍스트 원본을 쓰세요."
        )
    if suffix == ".docx":
        if tracked_mode not in ("accept", "reject"):
            raise DocumentError("--tracked 는 accept 또는 reject 만 됩니다.")
        doc = _read_docx(p, tracked_mode)
    else:
        raw = _read_bytes(p)
        _reject_binary(raw, p)
        text, encoding = _decode(raw)
        fmt = {".md": "md", ".markdown": "md", ".tex": "tex", ".txt": "txt"}[suffix]
        paras, total, truncated = _lines_from_text(text, fmt, split_lines)
        doc = Document(
            path=p, fmt=fmt, paras=paras, encoding=encoding, total_lines=total,
            tracked=Tracked(mode="n/a"), truncated=truncated,
        )
        if encoding not in ("utf-8", "utf-8-sig"):
            doc.notes.append(f"{encoding} 로 디코딩했습니다.")
        if truncated:
            doc.notes.append(
                "문단이 너무 길거나 많아 일부만 읽었습니다 — 결과를 '이상 없음'으로 보지 마세요."
            )
    doc.role = role
    tag_sections(doc)
    if not doc.paras:
        raise DocumentError(
            f"{p.name} 에서 읽을 수 있는 문단이 하나도 없습니다"
            f"{'(' + role + ')' if role else ''} — 빈 파일이거나 형식이 다릅니다."
        )
    return doc
