"""문서 읽기 — .docx / .md / .txt / .pdf(텍스트 레이어) 를 블록 목록으로.

표준 라이브러리만 쓴다. `.docx` 는 zipfile + xml.etree 로 직접 파싱하고,
표(`w:tbl`)를 문단 흐름에 **순서대로 인라인**한다 (CRF 는 거의 전부 표이고,
연구계획서도 버전·책임자·대상자수가 표 셀 안에 있다).

읽지 못한 문서는 조용히 넘어가지 않는다 — `read_ok=False` 로 남아 커버리지
자백과 종료코드 3에 반영된다.
"""

import os
import re
import stat
import zipfile
import xml.etree.ElementTree as ET

from .normalize import canon
from .safeio import mask_then_truncate
from .pdfread import extract_pdf_text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

TEXT_EXTS = {".md", ".markdown", ".txt", ".text"}
SUPPORTED_EXTS = {".docx", ".pdf"} | TEXT_EXTS
# 안내만 하고 읽지는 않는 확장자 (v1 범위 밖 — 조용히 무시하지 않는다)
KNOWN_UNREADABLE = {
    ".hwp": "한글(.hwp)은 v1에서 읽지 않습니다 — 한글에서 '다른 이름으로 저장 > Word(.docx)' 로 변환 후 다시 돌려주세요.",
    ".hwpx": "한글(.hwpx)은 v1에서 읽지 않습니다 — .docx 로 변환 후 다시 돌려주세요.",
    ".doc": "구형 Word(.doc)는 읽지 않습니다 — Word에서 .docx 로 저장 후 다시 돌려주세요.",
    ".hwt": "한글 서식 파일(.hwt)은 문서가 아닙니다.",
}
# 산출물 이름 — 출력 폴더를 패킷 안에 두어도 자기 리포트를 문서로 읽지 않는다.
ARTIFACT_NAMES = {"정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv"}
# 패킷 폴더에 같이 들어 있어도 점검 대상이 아닌 것들 (경고 없이 건너뛴다)
IGNORED_EXTS = {
    ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".zip", ".png", ".jpg", ".jpeg",
    ".gif", ".tif", ".tiff", ".mp3", ".wav", ".mp4", ".mov", ".json", ".xml",
    ".py", ".r", ".sav", ".dta", ".db", ".sqlite", ".ds_store",
}

MAX_BYTES = 80 * 1024 * 1024      # 80MB 이상은 읽지 않는다(패킷 문서로 비정상)
MAX_BLOCKS = 200000               # 블록 폭주 방어
MAX_BLOCK_CHARS = 400000          # 한 블록이 이보다 길면 잘라 쓴다(뒤는 별도 블록)
MAX_XML_BYTES = 200 * 1024 * 1024  # 압축 해제된 본문 XML 상한 (zip 폭탄 방어)


class Block(object):
    """문단 하나 또는 표의 한 행. 근거 위치는 (문서, block.idx, section) 로 찍는다."""

    __slots__ = ("idx", "kind", "text", "section")

    def __init__(self, idx, kind, text, section=""):
        self.idx = idx
        self.kind = kind          # 'p' 문단 · 't' 표 행
        self.text = text
        self.section = section

    def where(self):
        if self.section:
            return "%s %d번째 %s" % (self.section, self.idx, "표행" if self.kind == "t" else "문단")
        return "%d번째 %s" % (self.idx, "표행" if self.kind == "t" else "문단")

    def __repr__(self):  # pragma: no cover - 디버깅용
        return "Block(%d, %r, %r)" % (self.idx, self.kind, self.text[:40])


class Document(object):
    """읽어들인 문서 하나."""

    def __init__(self, path, fmt="", blocks=None, read_ok=True, error="", notes=None):
        self.path = path
        self.name = os.path.basename(str(path).rstrip("/\\"))
        self.fmt = fmt
        self.blocks = blocks or []
        self.read_ok = read_ok
        self.error = error
        self.notes = notes or []
        self.role = ""
        self.role_reason = ""
        self.role_source = ""     # '자동' | '--role 지정'

    @property
    def text(self):
        return "\n".join(block.text for block in self.blocks)

    def head_text(self, chars=1500):
        return self.text[:chars]

    def __repr__(self):  # pragma: no cover
        return "Document(%r, role=%r, blocks=%d)" % (self.name, self.role, len(self.blocks))


# ---------------------------------------------------------------- 섹션 추적

# 문장처럼 끝나면 제목이 아니다. '연구 개요'(요로 끝남)를 문장으로 오인하지 않도록
# 종결어미 + 마침표 또는 명확한 종결형만 문장으로 본다.
_SENTENCE_END = re.compile(r"[가-힣](?:다|요|음|함|까)[.!?]$|(?:니다|습니다|한다|된다|이다|있다|없다|겠다|였다|했다)$")


def _is_heading(text):
    """제목처럼 보이는 짧은 문단인가 (근거 위치의 '소속 절' 로 쓴다)."""
    text = text.strip()
    if not text or len(text) > 40 or "|" in text:
        return False
    if _SENTENCE_END.search(text):
        return False
    if text.count(" ") > 6:
        return False
    return bool(re.match(r"^[\[\(<]?\s*(\d+[.)]|[IVXivx]+[.)]|제?\s*\d+\s*(장|절|조)|[가-힣A-Za-z])", text))


def _finalize(raw_blocks):
    """(kind, text) 목록 → Block 목록 (섹션 추적 + 길이 제한)."""
    blocks = []
    section = ""
    for kind, text in raw_blocks[:MAX_BLOCKS]:
        text = canon(text)
        if not text:
            continue
        while len(text) > MAX_BLOCK_CHARS:
            blocks.append(Block(len(blocks), kind, text[:MAX_BLOCK_CHARS], section))
            text = text[MAX_BLOCK_CHARS:]
        block = Block(len(blocks), kind, text, section)
        blocks.append(block)
        if kind == "p" and _is_heading(text):
            # 라벨은 산출물에 그대로 실리므로, **자르기 전에** 마스킹한다.
            section = mask_then_truncate(text, 24, "...")
    return blocks


# ---------------------------------------------------------------- .docx

def _para_text(node):
    """문단 하나의 텍스트. 삭제된(w:del) 런은 제외 — '수정 반영 최종본' 상태로 읽는다."""
    parts = []
    for child in node.iter():
        if child.tag == W + "delText":
            continue
        if child.tag == W + "t":
            parts.append(child.text or "")
        elif child.tag == W + "tab":
            parts.append(" ")
        elif child.tag in (W + "br", W + "cr"):
            parts.append(" ")
    return "".join(parts)


def _iter_body(node, out, depth=0):
    """본문 자식들을 순서대로 훑는다. 표는 행 단위로 인라인."""
    if depth > 12:                                   # 중첩 표 폭주 방어
        return
    for child in list(node):
        tag = child.tag
        if tag == W + "p":
            out.append(("p", _para_text(child)))
        elif tag == W + "tbl":
            for row in child.findall(W + "tr"):
                cells = []
                for cell in row.findall(W + "tc"):
                    inner = []
                    _iter_body(cell, inner, depth + 1)
                    cell_text = " ".join(text for _, text in inner if text.strip())
                    # 셀 안의 '|' 는 표를 뭉개므로 치환한다 (deidaudit 사고 재발 방지)
                    cells.append(cell_text.replace("|", "/").strip())
                out.append(("t", " | ".join(cells)))
        elif tag in (W + "sdt", W + "ins", W + "smartTag", W + "customXml"):
            _iter_body(child, out, depth + 1)
        elif tag == W + "sdtContent":
            _iter_body(child, out, depth + 1)


def read_docx(path):
    """.docx → Document. 손상·암호화·비어 있음은 전부 read_ok=False 로 자백."""
    notes = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                if "EncryptedPackage" in names or "EncryptionInfo" in names:
                    return Document(path, "docx", read_ok=False, error="암호가 걸린 문서입니다")
                return Document(path, "docx", read_ok=False, error="Word 문서 구조가 아닙니다(word/document.xml 없음)")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                return Document(path, "docx", read_ok=False,
                                error="본문 XML 이 비정상적으로 큽니다(%d MB) — 압축 폭탄일 수 있어 읽지 않습니다"
                                      % (info.file_size // (1024 * 1024)))
            raw = archive.read("word/document.xml")
    except zipfile.BadZipFile:
        return Document(path, "docx", read_ok=False, error="파일이 손상되었거나 .docx 가 아닙니다")
    except (OSError, RuntimeError) as exc:
        return Document(path, "docx", read_ok=False, error="파일을 열지 못했습니다(%s)" % exc)
    except (OverflowError, MemoryError):
        return Document(path, "docx", read_ok=False, error="문서가 너무 커서 읽지 못했습니다(메모리 한계)")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return Document(path, "docx", read_ok=False, error="문서 XML 을 해석하지 못했습니다(%s)" % exc)
    except (OverflowError, MemoryError, ValueError) as exc:
        return Document(path, "docx", read_ok=False, error="문서 XML 이 너무 커서 읽지 못했습니다(%s)" % exc)

    if b"<w:ins " in raw or b"<w:del " in raw:
        notes.append("변경내용 추적이 켜진 문서 — **수정 반영(최종본) 상태**로 읽었습니다")

    body = root.find(W + "body")
    raw_blocks = []
    if body is not None:
        _iter_body(body, raw_blocks)
    blocks = _finalize(raw_blocks)
    if not blocks:
        return Document(path, "docx", blocks=[], read_ok=False,
                        error="본문에서 글자를 찾지 못했습니다(빈 문서이거나 이미지 스캔본)", notes=notes)
    return Document(path, "docx", blocks=blocks, notes=notes)


# ---------------------------------------------------------------- 텍스트/마크다운

def _decode(data):
    """cp949 / utf-8-sig / utf-8 자동 처리. 마지막 수단은 손실 복구."""
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(복구)"


def read_textlike(path):
    """.md / .txt → Document. 마크다운 표 행은 표 블록으로 본다."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        return Document(path, "text", read_ok=False, error="파일을 열지 못했습니다(%s)" % exc)
    if len(data) > MAX_BYTES:
        return Document(path, "text", read_ok=False, error="파일이 너무 큽니다(80MB 초과)")

    text, encoding = _decode(data)
    if text and (text.count("\ufffd") / float(len(text))) > 0.05:
        return Document(path, "text", read_ok=False,
                        error="글자로 해독되지 않는 바이트가 많습니다(텍스트 파일이 아닌 것 같습니다)")
    notes = [] if encoding in ("utf-8", "utf-8-sig") else ["%s 인코딩으로 읽었습니다" % encoding]
    raw_blocks = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
                continue                              # 마크다운 구분선
            raw_blocks.append(("t", " | ".join(cells)))
        elif stripped.startswith("#"):
            raw_blocks.append(("p", stripped.lstrip("#").strip()))
        else:
            raw_blocks.append(("p", stripped))
    blocks = _finalize(raw_blocks)
    if not blocks:
        return Document(path, "text", read_ok=False, error="파일에 글자가 없습니다", notes=notes)
    return Document(path, "text", blocks=blocks, notes=notes)


# ---------------------------------------------------------------- .pdf

def read_pdf(path):
    """.pdf → Document (텍스트 레이어만). 스캔본·암호화는 읽지 못한 문서로 센다."""
    lines, error, note = extract_pdf_text(path)
    if error:
        return Document(path, "pdf", read_ok=False, error=error)
    blocks = _finalize([("p", line) for line in lines])
    notes = [note] if note else []
    if not blocks:
        return Document(path, "pdf", read_ok=False,
                        error="텍스트 레이어가 없습니다(스캔 이미지 PDF로 보입니다)", notes=notes)
    return Document(path, "pdf", blocks=blocks, notes=notes)


# ---------------------------------------------------------------- 진입점

def read_document(path):
    """확장자에 맞는 리더로 읽는다. 지원하지 않으면 read_ok=False."""
    ext = os.path.splitext(str(path))[1].lower()
    if not os.path.exists(path):
        return Document(path, ext.lstrip("."), read_ok=False, error="파일이 없습니다")
    if os.path.islink(path):
        return Document(path, ext.lstrip("."), read_ok=False,
                        error="심볼릭 링크는 읽지 않습니다 — 패킷 폴더 밖의 내용이 리포트에 섞이는 것을 막습니다")
    if os.path.isdir(path):
        return Document(path, "dir", read_ok=False, error="폴더입니다")
    if not stat.S_ISREG(os.lstat(path).st_mode):
        return Document(path, ext.lstrip("."), read_ok=False,
                        error="일반 파일이 아닙니다(파이프·장치 파일 등) — 읽지 않습니다")
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return Document(path, ext.lstrip("."), read_ok=False, error="파일 정보를 읽지 못했습니다(%s)" % exc)
    if size > MAX_BYTES:
        return Document(path, ext.lstrip("."), read_ok=False, error="파일이 너무 큽니다(80MB 초과)")
    if ext in KNOWN_UNREADABLE:
        return Document(path, ext.lstrip("."), read_ok=False, error=KNOWN_UNREADABLE[ext])
    if ext == ".docx":
        return read_docx(path)
    if ext == ".pdf":
        return read_pdf(path)
    if ext in TEXT_EXTS:
        return read_textlike(path)
    return Document(path, ext.lstrip(".") or "?", read_ok=False,
                    error="지원하지 않는 형식입니다(.docx/.pdf/.md/.txt 만 읽습니다)")


def disambiguate(documents):
    """파일명이 겹치는 문서들에 상위 폴더 이름을 붙여 서로 다른 문서로 구분한다.

    이름이 곧 문서의 정체이므로(mention·표·CSV 전부 이름으로 묶인다), 이걸 안 하면
    다른 폴더의 같은 이름 문서 두 개가 한 문서로 뭉쳐 값이 엉뚱하게 귀속된다.
    """
    counts = {}
    for doc in documents:
        counts[doc.name] = counts.get(doc.name, 0) + 1
    for doc in documents:
        if counts.get(doc.name, 0) > 1:
            parent = os.path.basename(os.path.dirname(os.path.abspath(doc.path))) or "?"
            doc.name = "%s [%s]" % (doc.name, parent)     # basename 처리에도 살아남는 형태
    return documents


def scan(inputs):
    """(파일 경로 목록, 안내문 목록). 안내문은 커버리지 자백에 그대로 실린다."""
    paths = collect_paths(inputs)
    notes = []
    for item in inputs:
        item = os.path.expanduser(item)
        if not os.path.exists(item):
            notes.append("입력 경로를 찾지 못했습니다: %s" % os.path.basename(item.rstrip("/")))
            continue
        if not os.path.isdir(item):
            continue
        for name in sorted(os.listdir(item)):
            full = os.path.join(item, name)
            if name.startswith(".") or not os.path.isdir(full):
                continue
            inner = [child for child in os.listdir(full)
                     if not child.startswith(".")
                     and os.path.splitext(child)[1].lower() not in IGNORED_EXTS
                     and os.path.isfile(os.path.join(full, child))]
            if inner:
                notes.append("하위 폴더 '%s' 안의 파일 %d개는 보지 않았습니다 — 패킷은 폴더 하나에 "
                             "평평하게 모아 주세요" % (name, len(inner)))
    return paths, notes


def collect_paths(inputs):
    """폴더 또는 파일 목록 → 점검 대상 파일 경로 목록 (정렬, 숨김파일 제외)."""
    paths = []
    for item in inputs:
        item = os.path.expanduser(item)
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                if name.startswith("."):
                    continue
                full = os.path.join(item, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in IGNORED_EXTS or name in ARTIFACT_NAMES:
                    continue
                # 확장자를 모르는 파일도 버리지 않는다 — read_document 가 '읽지 못한 문서'로
                # 만들어 커버리지 자백과 종료코드 3에 반영한다(조용히 사라지는 것이 가장 위험하다).
                paths.append(full)
        else:
            paths.append(item)
    seen = set()
    unique = []
    for path in paths:
        key = os.path.abspath(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique
