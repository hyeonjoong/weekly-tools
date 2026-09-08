"""문서 읽기 — `.docx`(본문 + 표 인라인) · `.md` · `.txt` · `.pdf`(텍스트 레이어만).

외부 의존성 없이 `zipfile` + `xml.etree` 로 직접 파싱합니다. `.hwp`/`.hwpx` 와
스캔 PDF 는 읽지 않고 **"읽지 못한 문서"로 셉니다** — 조용히 건너뛰면 "치명 0건"이
거짓말이 되기 때문입니다.

표는 문단 흐름 안에 순서대로 인라인됩니다(`w:tbl` 이 나오는 자리에 행 단위로).
셀 사이는 **탭**으로 잇고 셀 안의 탭·개행은 공백으로 바꿉니다 — 셀 안의 `|` 가
표를 뭉개던 `deidaudit` 사고를 반복하지 않기 위해 구분자로 `|` 를 쓰지 않습니다.
"""
from __future__ import annotations

import os
import re
import zipfile
import zlib
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

from . import textnorm
from .model import Doc, Para

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_WRAPPERS = (W + "sdt", W + "sdtContent", W + "customXml", W + "smartTag", W + "fldSimple", W + "hyperlink")
READABLE_EXT = (".docx", ".md", ".txt", ".pdf")
UNREADABLE_EXT = (".hwp", ".hwpx", ".doc", ".rtf", ".odt", ".pages")
#: 원고 확장자 — 읽지 않지만 폴더에서 **수집은** 해서 강제 장치 1(원고 감지 → exit 2)이 발동되게 합니다.
MANUSCRIPT_EXT = (".tex",)
#: 이 크기를 넘는 문서는 읽지 않습니다 (10MB 단일 문단 방어).
MAX_BYTES = 50 * 1024 * 1024
MAX_PARAS = 200_000

_HEADING_RX = re.compile(
    r"^(?:제\s*[0-9]+\s*[장절조항]\s*|[0-9]+(?:\.[0-9]+)*[.)]?\s+|[IVXivx]+[.)]\s+|[가-힣][.)]\s+|\([0-9가-힣]+\)\s*|[①-⑳]\s*)?(\S.{0,38})$")


def sanitize_name(name: str) -> str:
    """파일명의 개행·제어문자를 가시 문자로 바꿉니다 (가짜 [치명] 줄 방지)."""
    out = []
    for ch in textnorm.nfc(name):
        code = ord(ch)
        if ch in "\r\n":
            out.append("␤")
        elif code < 32 or code == 127 or 0x80 <= code < 0xA0 or ch in "\u2028\u2029":
            out.append("�")
        else:
            out.append(ch)
    return "".join(out)


def _is_heading(text: str, style: str) -> bool:
    if style and (style.lower().startswith("heading") or style.startswith("제목")):
        return True
    t = text.strip()
    if not t or len(t) > 40 or t.endswith(("다.", "요.", "함.", "음.", "됨.")):
        return False
    if "\t" in t:
        return False
    m = _HEADING_RX.match(t)
    if not m:
        return False
    body = m.group(1)
    return bool(re.match(r"^(?:제\s*[0-9]+\s*[장절조항]|[0-9]+(?:\.[0-9]+)*[.)]?\s|[IVXivx]+[.)]\s|[가-힣][.)]\s|\([0-9가-힣]+\)|[①-⑳])", t)) or (
        len(body) <= 20 and not re.search(r"[0-9]", body) and body.endswith(("기준", "방법", "절차", "목적", "배경", "설계", "보상", "연락처", "정보", "동의", "항목", "일정", "기간")))


def _para_text(p: ET.Element) -> Tuple[str, bool]:
    """문단 텍스트와 '변경 추적 표식이 있었는지'. 삭제(w:del) 텍스트는 제외."""
    parts: List[str] = []
    tracked = False
    for el in p.iter():
        tag = el.tag
        if tag == W + "del":
            tracked = True
        if tag == W + "ins":
            tracked = True
        if tag == W + "t":
            parts.append(el.text or "")
        elif tag == W + "tab":
            parts.append(" ")
        elif tag in (W + "br", W + "cr"):
            parts.append(" ")
        elif tag == W + "noBreakHyphen":
            parts.append("-")
        elif tag == W + "softHyphen":
            pass
        elif tag == W + "sym":
            parts.append("·")
        elif tag == W + "delText":
            pass
    return "".join(parts), tracked


def _strip_deleted(root: ET.Element) -> None:
    """변경 추적을 '모두 수락' 상태로 만듭니다 — w:del 텍스트, 삭제 표시된 표 행(w:trPr/w:del),
    이동 원본(w:moveFrom), 그리고 mc:Fallback(텍스트상자 등의 중복 사본)을 제거합니다."""
    for parent in root.iter():
        for child in list(parent):
            if child.tag in (W + "del", W + "moveFrom", MC + "Fallback"):
                parent.remove(child)
            elif child.tag == W + "tr":
                trpr = child.find(W + "trPr")
                if trpr is not None and trpr.find(W + "del") is not None:
                    parent.remove(child)
            elif child.tag == W + "tc":
                tcpr = child.find(W + "tcPr")
                if tcpr is not None and tcpr.find(W + "cellDel") is not None:
                    parent.remove(child)


def _direct(el: ET.Element, tag: str):
    """el 의 직계 tag 자식들 — 단, sdt/customXml 같은 래퍼는 투과합니다 (중첩 표 안으로는 안 들어감)."""
    for c in el:
        if c.tag == tag:
            yield c
        elif c.tag in _WRAPPERS:
            for x in _direct(c, tag):
                yield x


def read_docx(path: str) -> Tuple[List[Para], bool]:
    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo("word/document.xml")
        except KeyError:
            raise ValueError("word/document.xml 이 없습니다 — docx 가 아닙니다")
        if info.file_size > MAX_BYTES:
            raise ValueError("document.xml 이 {}MB 를 넘습니다".format(MAX_BYTES // (1024 * 1024)))
        data = zf.read("word/document.xml")
    root = ET.fromstring(data)
    tracked = bool(root.find(".//" + W + "ins") is not None or root.find(".//" + W + "del") is not None)
    _strip_deleted(root)
    body = root.find(W + "body")
    if body is None:
        return [], tracked
    paras: List[Para] = []
    section = ""
    table_no = 0

    def add(text: str, table: int = 0, row: int = 0, style: str = "") -> None:
        nonlocal section
        t = textnorm.basic(text).strip()
        if not t:
            return
        if len(paras) >= MAX_PARAS:
            raise ValueError("문단이 {}개를 넘습니다".format(MAX_PARAS))
        if not table and _is_heading(t, style):
            section = t[:40]
        paras.append(Para(idx=len(paras), text=t, section=section, table=table, row=row))

    def walk(container: ET.Element) -> None:
        nonlocal table_no
        for child in container:
            if child.tag == W + "p":
                style_el = child.find(W + "pPr/" + W + "pStyle")
                style = style_el.get(W + "val", "") if style_el is not None else ""
                text, _ = _para_text(child)
                add(text, style=style)
            elif child.tag == W + "tbl":
                table_no += 1
                my_no = table_no
                for r_i, tr in enumerate(_direct(child, W + "tr"), start=1):
                    cells: List[str] = []
                    for tc in _direct(tr, W + "tc"):
                        cell_parts: List[str] = []
                        for p in tc.iter(W + "p"):
                            text, _ = _para_text(p)
                            cell_parts.append(text)
                        cell = " ".join(x for x in cell_parts if x.strip())
                        cell = cell.replace("\t", " ").replace("\n", " ").strip()
                        cells.append(cell)
                    add("\t".join(cells), table=my_no, row=r_i)
            elif child.tag in _WRAPPERS:
                walk(child)
    walk(body)
    return paras, tracked


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("cp949")
    except UnicodeDecodeError:
        raise ValueError("utf-8 도 cp949 도 아닙니다 — 인코딩을 알 수 없습니다")


def read_text_file(path: str) -> List[Para]:
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise ValueError("파일이 {}MB 를 넘습니다".format(MAX_BYTES // (1024 * 1024)))
    with open(path, "rb") as fh:
        raw = fh.read()
    text = _decode_bytes(raw)
    paras: List[Para] = []
    section = ""
    table_no = 0
    in_table = False
    row = 0
    for line in text.splitlines():
        t = textnorm.basic(line).strip()
        if not t:
            in_table = False
            continue
        if len(paras) >= MAX_PARAS:
            raise ValueError("문단이 {}개를 넘습니다".format(MAX_PARAS))
        if t.startswith("|") and t.endswith("|"):
            cells = [c.strip() for c in t.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if not in_table:
                in_table = True
                table_no += 1
                row = 0
            row += 1
            paras.append(Para(idx=len(paras), text="\t".join(cells), section=section, table=table_no, row=row))
            continue
        in_table = False
        m = re.match(r"^#{1,6}\s+(.*)$", t)
        if m:
            section = m.group(1).strip()[:40]
            t = m.group(1).strip()
        elif _is_heading(t, ""):
            section = t[:40]
        paras.append(Para(idx=len(paras), text=t, section=section))
    return paras


# ----------------------------------------------------------------- PDF (텍스트 레이어)

_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_TEXTOBJ = re.compile(rb"BT(.*?)ET", re.DOTALL)
_PDF_STR = re.compile(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>")


def _pdf_literal(s: bytes) -> bytes:
    if s.startswith(b"<"):
        hexs = re.sub(rb"\s", b"", s[1:-1])
        if len(hexs) % 2:
            hexs += b"0"
        try:
            return bytes.fromhex(hexs.decode("ascii"))
        except ValueError:
            return b""
    body = s[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i:i + 1]
        if c == b"\\" and i + 1 < len(body):
            nxt = body[i + 1:i + 2]
            esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
                   b"(": b"(", b")": b")", b"\\": b"\\"}
            if nxt in esc:
                out += esc[nxt]
                i += 2
                continue
            if nxt.isdigit():
                oct_digits = body[i + 1:i + 4]
                m = re.match(rb"[0-7]{1,3}", oct_digits)
                if m:
                    out.append(int(m.group(0), 8) & 0xFF)
                    i += 1 + len(m.group(0))
                    continue
            i += 1
            continue
        out += c
        i += 1
    return bytes(out)


def read_pdf(path: str) -> List[Para]:
    """PDF 텍스트 레이어를 최선을 다해 추출합니다. 실패하면 ValueError."""
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("파일이 너무 큽니다")
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.startswith(b"%PDF"):
        raise ValueError("PDF 서명이 없습니다")
    if b"/Identity-H" in raw or b"/CIDFontType" in raw or b"/Identity-V" in raw:
        raise ValueError("CID 폰트(Identity-H) PDF — 텍스트 레이어가 글리프 번호라 읽을 수 없습니다. docx 로 변환해 주세요")
    chunks: List[str] = []
    total = 0
    n_lines = 0
    for m in _PDF_STREAM.finditer(raw):
        data = m.group(1)
        try:
            d = zlib.decompressobj()
            data = d.decompress(data, MAX_BYTES - total)
            if d.unconsumed_tail:
                raise ValueError("PDF 스트림이 누적 {}MB 를 넘습니다 (압축 해제 후)".format(MAX_BYTES // (1024 * 1024)))
        except zlib.error:
            pass
        total += len(data)
        if total > MAX_BYTES:
            raise ValueError("PDF 스트림이 누적 {}MB 를 넘습니다 (압축 해제 후)".format(MAX_BYTES // (1024 * 1024)))
        for tobj in _PDF_TEXTOBJ.finditer(data):
            line: List[bytes] = []
            for s in _PDF_STR.finditer(tobj.group(1)):
                line.append(_pdf_literal(s.group(0)))
            if line:
                joined = b"".join(line)
                for enc in ("utf-8", "utf-16-be", "cp949"):
                    try:
                        txt = joined.decode(enc)
                        break
                    except UnicodeDecodeError:
                        txt = ""
                if txt:
                    chunks.append(txt)
                    n_lines += txt.count("\n") + 1
                    if n_lines > MAX_PARAS:
                        raise ValueError("문단이 {}개를 넘습니다".format(MAX_PARAS))
    text = "\n".join(chunks)
    good = sum(1 for ch in text if ch.isalnum())
    words = len(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text))
    if good < 50 or words < 10:
        raise ValueError("텍스트 레이어를 읽지 못했습니다 (스캔 PDF 이거나 CID 폰트 인코딩) — docx 로 변환해 주세요")
    paras: List[Para] = []
    section = ""
    for line in text.splitlines():
        t = textnorm.basic(line).strip()
        if not t:
            continue
        if len(paras) >= MAX_PARAS:
            raise ValueError("문단이 {}개를 넘습니다".format(MAX_PARAS))
        if _is_heading(t, ""):
            section = t[:40]
        paras.append(Para(idx=len(paras), text=t, section=section))
    return paras


# ----------------------------------------------------------------- 진입점

def load(path: str) -> Doc:
    """파일 하나를 읽어 Doc 으로. 못 읽으면 readable=False + 사유 (예외를 밖으로 내지 않음)."""
    name = sanitize_name(os.path.basename(path))
    ext = os.path.splitext(path)[1].lower()
    doc = Doc(path=path, name=name, ext=ext)
    try:
        if os.path.islink(path):
            raise ValueError("심볼릭 링크는 읽지 않습니다")
        if not os.path.isfile(path):
            raise ValueError("파일이 아닙니다")
        if ext in MANUSCRIPT_EXT:
            raise ValueError(".tex 는 논문 원고 형식 — irbpack 은 원고를 읽지 않습니다")
        if ext in UNREADABLE_EXT:
            hint = "한글(HWP)은 v1 에서 읽지 않습니다 — 한글에서 '다른 이름으로 저장 → DOCX' 후 다시 실행" if ext in (".hwp", ".hwpx") else "지원하지 않는 형식 — DOCX/MD/TXT/PDF 로 변환"
            raise ValueError(hint)
        if ext == ".docx":
            doc.paras, doc.tracked_changes = read_docx(path)
        elif ext in (".md", ".txt"):
            doc.paras = read_text_file(path)
        elif ext == ".pdf":
            doc.paras = read_pdf(path)
        else:
            raise ValueError("지원하지 않는 확장자 {}".format(ext or "(없음)"))
        if not doc.paras:
            raise ValueError("본문이 비어 있습니다")
    except (ValueError, zipfile.BadZipFile, ET.ParseError, OSError, KeyError, RuntimeError,
            NotImplementedError, zlib.error, MemoryError, RecursionError) as exc:
        # RuntimeError = 암호 걸린 zip 멤버, NotImplementedError = 미지원 압축, zlib.error = 깨진 스트림
        doc.readable = False
        reason = str(exc).replace(os.path.abspath(path), name).replace(path, name)
        reason = re.sub(r"(?:/[^/\s'\"]+){2,}/", "…/", reason)  # 절대경로(홈 디렉터리 사용자명)가 리포트에 새지 않게
        doc.unread_reason = sanitize_name(reason)[:200] or exc.__class__.__name__
        doc.paras = []
    return doc
