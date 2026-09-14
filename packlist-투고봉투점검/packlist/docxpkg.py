"""`.docx` 패키지(zip)를 직접 열어 미디어·관계·본문을 읽는다.

`python-docx` 를 쓰지 않는다. 이 툴이 보는 것은 문단 API 가 아니라
``word/media/*`` 전수 해시와 ``*.xml.rels`` 역추적이기 때문이다.
읽기만 하며 zip 을 쓰기 모드로 열지 않는다.

고아 판정의 핵심 규칙: ``word/document.xml`` 만 보면 머리글/바닥글의
로고와 각주 이미지가 전부 고아로 잡힌다. 그래서 패키지 안의 **모든**
``.rels`` 를 훑어 실제로 참조되는 미디어를 모은 뒤에 고아를 정한다.
"""

import hashlib
import re
import unicodedata
import zipfile
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CORE = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"

IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

#: 압축 해제 총량 상한(zip bomb 방어). 실무 원고는 수십 MB 다.
MAX_UNCOMPRESSED = 2 * 1024 ** 3

#: XML 파트 하나의 상한. 실제 원고의 document.xml 은 몇 MB 다.
#: 이 벽이 없으면 306 KB 짜리 docx 가 파싱 중 수 GB 의 메모리를 먹는다.
MAX_PART_BYTES = 64 * 1024 ** 2

#: zipfile 이 던질 수 있는, '못 읽었다'로 묶어야 하는 예외들.
#: 암호 걸린 docx(RuntimeError)와 지원하지 않는 압축 방식
#: (NotImplementedError)이 여기 들어온다 — 잡지 않으면 traceback 이 난다.
_READ_ERRORS = (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError,
                EOFError, ValueError, MemoryError)

_REL_ID_RE = re.compile(r"""r:(?:embed|link|id|pict|dm|lo|qs|cs|href)=["']([^"']+)["']""")


class DocxUnreadable(Exception):
    """다 읽지 못했다. 종료코드 3(판정 불가)으로 이어진다."""


@dataclass
class Media:
    name: str            # 패키지 내부 경로 (word/media/image3.png)
    size: int
    sha256: str

    @property
    def basename(self) -> str:
        return PurePosixPath(self.name).name


@dataclass
class Rel:
    source_part: str     # 이 관계를 소유한 파트 (word/document.xml)
    rel_id: str
    rel_type: str
    target: str
    external: bool
    resolved: Optional[str]   # 내부 대상의 패키지 경로 (외부면 None)
    referenced: bool          # 소유 파트 XML 이 실제로 이 Id 를 부르는가

    @property
    def is_image(self) -> bool:
        return self.rel_type == IMAGE_REL_TYPE


@dataclass
class Paragraph:
    index: int
    text: str
    has_drawing: bool


@dataclass
class DocxInfo:
    """원고 한 개를 읽은 결과. 모든 판정의 원재료."""

    filename: str
    file_size: int
    media: Dict[str, Media] = field(default_factory=dict)
    rels: List[Rel] = field(default_factory=list)
    body_anchored: Set[str] = field(default_factory=set)
    other_anchored: Dict[str, List[str]] = field(default_factory=dict)
    paragraphs: List[Paragraph] = field(default_factory=list)
    raw_paragraph_count: int = 0
    comment_count: int = 0
    insertions: int = 0
    deletions: int = 0
    title: str = ""
    creator: str = ""
    last_modified_by: str = ""
    parts_unparsed: List[str] = field(default_factory=list)
    duplicate_rel_ids: List[str] = field(default_factory=list)

    # ---- 파생 속성 -------------------------------------------------
    @property
    def text(self) -> str:
        """문단을 줄바꿈으로 이어 붙인 본문.

        구분자 없이 붙이면 앞 문단 끝과 뒤 문단 첫 글자가 한 단어로
        붙어 버려(``EerolaManuscript``) 단어 경계 정규식이 전부 어긋난다.
        """
        return "\n".join(p.text for p in self.paragraphs)

    @property
    def anchored(self) -> Set[str]:
        return self.body_anchored | set(self.other_anchored)

    @property
    def orphans(self) -> List[Media]:
        return [m for name, m in sorted(self.media.items()) if name not in self.anchored]

    @property
    def broken_image_rels(self) -> List[Rel]:
        return [
            r for r in self.rels
            if r.referenced and r.is_image and not r.external
            and (r.resolved is None or r.resolved not in self.media)
        ]

    @property
    def external_image_rels(self) -> List[Rel]:
        return [r for r in self.rels if r.referenced and r.is_image and r.external]

    @property
    def media_bytes(self) -> int:
        return sum(m.size for m in self.media.values())

    def duplicate_groups(self) -> List[List[Media]]:
        """같은 바이트, 다른 이름. Word 가 흔히 만든다 — 경고 고정."""
        buckets: Dict[str, List[Media]] = {}
        for name in sorted(self.media):
            buckets.setdefault(self.media[name].sha256, []).append(self.media[name])
        return [g for g in buckets.values() if len(g) > 1]

    def duplicate_waste(self) -> int:
        """중복으로 낭비되는 바이트(각 그룹에서 한 벌만 남긴다고 볼 때)."""
        return sum(sum(m.size for m in g[1:]) for g in self.duplicate_groups())

    def orphan_waste(self) -> int:
        return sum(m.size for m in self.orphans)


def _iter_skipping_fallback(element):
    """``mc:AlternateContent`` 의 Fallback 가지는 건너뛰며 트리를 훑는다.

    건너뛰지 않으면 같은 캡션 문구가 Choice/Fallback 양쪽에서 두 번 잡혀
    'Fig. 4 인용 횟수'가 부풀려진다. 재귀가 아니라 스택으로 도는 이유는
    깊이 1,000 을 넘는 문단 중첩에서 ``RecursionError`` 가 나기 때문이다.
    """
    stack = [element]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node):
            if child.tag == MC + "Fallback":
                continue
            stack.append(child)


def _source_part_of_rels(rels_path: str) -> str:
    parts = rels_path.split("/")
    name = parts[-1][: -len(".rels")]
    prefix = parts[:-2]          # '_rels' 제거
    return "/".join(prefix + [name]) if name else "/".join(prefix)


def _resolve(source_part: str, target: str) -> Optional[str]:
    # OOXML 의 Target 은 URI 참조다 — Word 는 공백과 한글을 퍼센트 인코딩한다.
    # 풀어 주지 않으면 `media/%EA%B7%B8%EB%A6%BC1.png` 가 없는 파일로 보여
    # 멀쩡한 그림 하나가 '고아 + 깨진 참조' 두 건의 치명으로 잡힌다.
    # macOS 의 zip 은 항목 이름을 분해형(NFD)으로 담기도 한다. 정규화하지
    # 않으면 `그림1.png` 가 서로 다른 두 문자열이 되어 멀쩡한 그림이
    # '고아 + 깨진 참조' 두 건의 치명으로 잡힌다.
    target = unicodedata.normalize("NFC", unquote(target.split("#", 1)[0]))
    if not target:
        return None
    if target.startswith("/"):
        return target.lstrip("/")
    base = PurePosixPath(source_part).parent if source_part else PurePosixPath(".")
    try:
        resolved = (base / target)
    except ValueError:
        return None
    out: List[str] = []
    for piece in resolved.parts:
        if piece == ".":
            continue
        if piece == "..":
            if out:
                out.pop()
            continue
        out.append(piece)
    return "/".join(out)


def _read_part(zf: zipfile.ZipFile, name: str, docx_name: str) -> bytes:
    """파트 하나를 크기 상한 안에서 읽는다. 못 읽으면 :class:`DocxUnreadable`."""
    try:
        size = zf.getinfo(name).file_size
    except KeyError:
        raise DocxUnreadable(f"{docx_name}: {name} 가 패키지에 없습니다")
    if size > MAX_PART_BYTES:
        raise DocxUnreadable(
            f"{docx_name}: {name} 이 비정상적으로 큽니다({size} 바이트) — 열지 않았습니다")
    try:
        return zf.read(name)
    except _READ_ERRORS as exc:
        raise DocxUnreadable(f"{docx_name}: {name} 를 읽지 못했습니다 ({_reason(exc)})")


def _reason(exc: Exception) -> str:
    text = str(exc)
    if "encrypted" in text or "password" in text:
        return "암호가 걸려 있습니다"
    if isinstance(exc, NotImplementedError):
        return "지원하지 않는 압축 방식입니다"
    if isinstance(exc, MemoryError):
        return "메모리가 부족합니다"
    return text[:120] or exc.__class__.__name__


def _hash_entry(zf: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with zf.open(name) as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_docx(path, filename: Optional[str] = None) -> DocxInfo:
    """`.docx` 하나를 읽는다. 읽지 못하면 :class:`DocxUnreadable`."""
    import os

    name = filename or os.path.basename(str(path))
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise DocxUnreadable(f"{name}: 파일 크기를 읽을 수 없습니다 ({exc})")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocxUnreadable(f"{name}: docx 압축이 손상되어 열 수 없습니다")
    except _READ_ERRORS as exc:
        raise DocxUnreadable(f"{name}: 파일을 열 수 없습니다 ({_reason(exc)})")

    with zf:
        try:
            names = zf.namelist()
            total = sum(i.file_size for i in zf.infolist())
        except _READ_ERRORS as exc:
            raise DocxUnreadable(f"{name}: 목록을 읽지 못했습니다 ({_reason(exc)})")
        if total > MAX_UNCOMPRESSED:
            raise DocxUnreadable(
                f"{name}: 압축 해제 크기가 비정상적으로 큽니다({total} 바이트) — 열지 않았습니다"
            )
        if "word/document.xml" not in names:
            raise DocxUnreadable(f"{name}: word/document.xml 이 없습니다 — docx 가 아닙니다")

        info = DocxInfo(filename=name, file_size=size)

        for entry in zf.infolist():
            if entry.is_dir() or not entry.filename.startswith("word/media/"):
                continue
            try:
                digest = _hash_entry(zf, entry.filename)
            except _READ_ERRORS as exc:
                raise DocxUnreadable(
                    f"{name}: 미디어 {entry.filename} 를 읽지 못했습니다 ({_reason(exc)})")
            key = unicodedata.normalize("NFC", entry.filename)
            info.media[key] = Media(key, entry.file_size, digest)

        _read_relationships(zf, names, info, name)
        _read_body(zf, info, name)
        _read_comments(zf, names, info)
        _read_core_props(zf, names, info)

    return info


def _referenced_ids(xml: str) -> Set[str]:
    """이 파트가 실제로 부르는 관계 Id 들.

    관계 네임스페이스가 ``r:`` 이 아닌 접두사로 묶여 있어도 찾아낸다 —
    못 찾으면 모든 이미지가 고아가 되어 **거짓 치명**이 쏟아진다.
    접두사가 아무리 많아도 전체 문서는 **한 번만** 훑는다(접두사마다 다시
    훑으면 86 KB 짜리 파일이 30초를 먹는다).
    """
    prefixes = set(re.findall(
        r'xmlns:([A-Za-z_][\w.\-]*)\s*=\s*["\']'
        r'http://schemas\.openxmlformats\.org/officeDocument/2006/relationships["\']', xml))
    prefixes.add("r")
    alternation = "|".join(sorted((re.escape(p) for p in prefixes), key=len, reverse=True))
    pattern = re.compile(
        r"(?:" + alternation + r"):(?:embed|link|id|pict|dm|lo|qs|cs|href)"
        r"\s*=\s*[\"\']([^\"\']+)[\"\']")
    return set(pattern.findall(xml))


def _read_relationships(zf, names, info: DocxInfo, name: str) -> None:
    rels_paths = sorted(n for n in names
                        if n.endswith(".rels") and ("/_rels/" in n or n.startswith("_rels/")))
    for rels_path in rels_paths:
        source_part = _source_part_of_rels(rels_path)
        raw = _read_part(zf, rels_path, name)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise DocxUnreadable(f"{name}: {rels_path} 를 해석하지 못했습니다 ({exc})")

        part_exists = source_part in names
        referenced_ids: Set[str] = set()
        if part_exists and source_part.endswith(".xml"):
            xml = _read_part(zf, source_part, name).decode("utf-8", errors="replace")
            referenced_ids = _referenced_ids(xml)

        # 같은 Id 가 두 번 선언되면 소비자(Word)는 하나만 쓴다. 둘 다 앵커로
        # 치면 진짜 고아가 가려진다 — 마지막 선언만 살린다.
        declared = {}
        order = []
        for rel in root:
            if not rel.tag.endswith("Relationship"):
                continue
            rel_id = rel.get("Id") or ""
            if rel_id in declared:
                info.duplicate_rel_ids.append(f"{source_part or '(패키지 루트)'} :: {rel_id}")
            else:
                order.append(rel_id)
            declared[rel_id] = rel

        for rel_id in order:
            rel = declared[rel_id]
            rel_type = rel.get("Type") or ""
            target = rel.get("Target") or ""
            external = (rel.get("TargetMode") or "") == "External"
            resolved = None if external else _resolve(source_part, target)
            # 패키지 루트(_rels/.rels)는 XML 파트가 아니므로 참조 여부를 물을 수
            # 없다. 그 밖의 파트는 **존재하고 그 Id 를 실제로 불러야** 앵커다 —
            # 지워진 바이너리 파트의 유령 .rels 를 믿으면 고아가 가려진다.
            referenced = (True if source_part == ""
                          else (rel_id in referenced_ids))
            info.rels.append(Rel(source_part, rel_id, rel_type, target, external,
                                 resolved, referenced))
            if rel_type == IMAGE_REL_TYPE and referenced and resolved in info.media:
                if source_part == "word/document.xml":
                    info.body_anchored.add(resolved)
                else:
                    info.other_anchored.setdefault(resolved, []).append(source_part)


def _read_body(zf, info: DocxInfo, name: str) -> None:
    raw = _read_part(zf, "word/document.xml", name)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocxUnreadable(f"{name}: word/document.xml 파싱 실패 ({exc})")

    nodes = list(_iter_skipping_fallback(root))
    parent = {}
    for node in nodes:
        for child in node:
            parent[id(child)] = node
    paragraphs = [n for n in nodes if n.tag == W + "p"]
    info.raw_paragraph_count = len(paragraphs)

    keep = {id(n) for n in nodes}

    # 문단마다 하위 트리를 다시 훑으면 문단이 깊게 중첩된 문서에서 O(n²) 가
    # 된다(1.8 KB 파일이 30초). 이미 만들어 둔 `nodes` 를 **한 번만** 돌면서
    # 각 노드를 가장 가까운 조상 문단에 배정한다.
    def nearest_paragraph(node):
        ancestor = parent.get(id(node))
        while ancestor is not None and ancestor.tag != W + "p":
            ancestor = parent.get(id(ancestor))
        return ancestor

    texts = {id(p): [] for p in paragraphs}
    drawings = set()
    drawing_tags = (W + "drawing", "{urn:schemas-microsoft-com:vml}imagedata")
    for node in nodes:
        tag = node.tag
        if tag in (W + "t", W + "tab", W + "br", W + "cr"):
            owner = nearest_paragraph(node)
            if owner is not None:
                # 줄바꿈/탭은 공백 한 칸으로 — 없으면 `…Figure 2` + `3 participants`
                # 가 `Figure 23` 이 되어 없는 그림 번호를 지어낸다.
                texts[id(owner)].append((node.text or "") if tag == W + "t" else " ")
        elif tag in drawing_tags:
            owner = nearest_paragraph(node)
            while owner is not None:
                drawings.add(id(owner))
                owner = nearest_paragraph(owner)

    for index, para in enumerate(paragraphs, start=1):
        info.paragraphs.append(
            Paragraph(index, "".join(texts[id(para)]), id(para) in drawings))

    info.insertions = sum(1 for n in nodes if n.tag == W + "ins")
    info.deletions = sum(1 for n in nodes if n.tag == W + "del")
    del keep


def _read_comments(zf, names, info: DocxInfo) -> None:
    if "word/comments.xml" not in names:
        return
    try:
        root = ET.fromstring(_read_part(zf, "word/comments.xml", info.filename))
    except (ET.ParseError, DocxUnreadable):
        info.parts_unparsed.append("word/comments.xml")
        return
    info.comment_count = sum(1 for n in root.iter(W + "comment"))


def _read_core_props(zf, names, info: DocxInfo) -> None:
    if "docProps/core.xml" not in names:
        return
    try:
        root = ET.fromstring(_read_part(zf, "docProps/core.xml", info.filename))
    except (ET.ParseError, DocxUnreadable):
        info.parts_unparsed.append("docProps/core.xml")
        return
    def get(tag):
        node = root.find(tag)
        return (node.text or "").strip() if node is not None else ""
    info.title = get(DC + "title")
    info.creator = get(DC + "creator")
    info.last_modified_by = get(CORE + "lastModifiedBy")
