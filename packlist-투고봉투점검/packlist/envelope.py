"""'봉투'(폴더 하나)를 읽는다.

봉투 = 지정한 폴더의 **바로 아래 파일들**. 하위폴더는 봉투로 보지 않고
그 사실을 자백한다. `_이전본/` 같은 폴더가 같이 올라가면 사고이기 때문에
숨기지 않고 항상 이름을 찍는다.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .safeio import UsageError, _short, nfc, sanitize

#: 내부까지 여는 확장자. v1 은 docx 뿐이다.
PARSED_EXTS = (".docx",)

#: 목록·용량만 세고 내부는 열지 않는 확장자.
LISTED_EXTS = (
    ".pdf", ".pptx", ".ppt", ".xlsx", ".xls", ".doc", ".rtf", ".txt", ".md",
    ".csv", ".tsv", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eps", ".svg",
    ".zip", ".json", ".bib", ".tex", ".hwp", ".hwpx",
)

#: 파일명만 보고 IRB 패킷이라고 판단하는 토큰. 본문 내용은 보지 않는다
#: (원고 Methods 의 'informed consent' 로 오탐하지 않으려고 일부러 좁혔다).
IRB_FILENAME_TOKENS = (
    "동의서", "consent_form", "consentform", "설명문", "피험자설명",
    "증례기록", "crf", "모집공고", "모집문건", "심의신청", "irb신청",
)

#: 봉투 안에 있으면 안 되는(또는 사람이 알아야 하는) 숨김/시스템 파일.
JUNK_NAMES = (".ds_store", "thumbs.db", "desktop.ini", "icon\r")

#: Word 가 문서를 열어 둔 동안 만드는 잠금 파일 접두사.
#: 이 툴을 쓰는 순간이 바로 "원고를 Word 로 열어 둔 채 올리기 직전"이라
#: 이것을 원고 후보로 올리면 첫 실행부터 exit 2/3 가 된다.
LOCK_PREFIX = "~$"


@dataclass
class EnvelopeFile:
    name: str
    size: int
    ext: str
    is_hidden: bool = False
    sha256: str = ""

    @property
    def parsed(self) -> bool:
        return self.ext in PARSED_EXTS

    @property
    def kind(self) -> str:
        if self.name.startswith("~$"):
            return "Word 잠금파일"
        if self.is_hidden:
            return "숨김/시스템"
        if self.parsed:
            return "docx"
        if self.ext in LISTED_EXTS:
            return "목록만"
        return "알수없음"


@dataclass
class Envelope:
    root: Path
    files: List[EnvelopeFile] = field(default_factory=list)
    subdirs: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """산출물에 실리는 이름. 절대경로를 흘리지 않으려고 basename 만 쓴다."""
        return sanitize(self.root.name or str(self.root))

    @property
    def docx_files(self) -> List[EnvelopeFile]:
        return [f for f in self.files if f.parsed and not f.is_hidden]

    @property
    def visible_files(self) -> List[EnvelopeFile]:
        return [f for f in self.files if not f.is_hidden]

    @property
    def junk_files(self) -> List[EnvelopeFile]:
        return [f for f in self.files if f.is_hidden]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_envelope(target: str, hash_files: bool = True) -> Envelope:
    """폴더 하나를 봉투로 읽는다. 폴더가 아니면 :class:`UsageError`."""
    if not str(target).strip():
        raise UsageError("점검할 봉투 폴더를 지정하세요 (빈 경로가 들어왔습니다)")
    root = Path(target)
    if not root.exists():
        raise UsageError(f"경로가 없습니다: {_short(target)}")
    if root.is_file():
        hint = (
            "  이 툴은 폴더(봉투) 하나를 통째로 봅니다. 원고 한 개의 텍스트 점검은 draftcheck 가 합니다."
        )
        raise UsageError(f"폴더가 아니라 파일을 넘겼습니다: {sanitize(root.name)}\n{hint}")
    if not root.is_dir():
        raise UsageError(f"폴더가 아닙니다: {_short(target)}")

    env = Envelope(root=root)
    try:
        entries = sorted(os.scandir(root), key=lambda e: nfc(e.name))
    except PermissionError:
        raise UsageError(f"폴더를 읽을 권한이 없습니다: {sanitize(root.name)}")
    except OSError as exc:
        raise UsageError(f"폴더를 읽을 수 없습니다: {sanitize(root.name)} ({exc.strerror})")

    for entry in entries:
        name = nfc(entry.name)
        try:
            if entry.is_dir(follow_symlinks=False):
                env.subdirs.append(name)
                continue
            if entry.is_symlink():
                # 링크는 봉투로 보지 않는다(가리키는 곳이 봉투 밖일 수 있다).
                # 하위폴더로 뭉뚱그리면 "docx 가 없습니다" 라는 틀린 말이 나온다.
                env.links.append(name)
                continue
            if not entry.is_file(follow_symlinks=False):
                env.links.append(name)     # FIFO·소켓 등
                continue
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            env.unreadable.append(name)
            continue
        ext = os.path.splitext(name)[1].lower()
        hidden = (name.startswith(".") or name.startswith(LOCK_PREFIX)
                  or name.lower() in JUNK_NAMES)
        item = EnvelopeFile(name=name, size=size, ext=ext, is_hidden=hidden)
        if hash_files and not hidden:
            try:
                item.sha256 = _sha256_file(root / entry.name)
            except OSError:
                env.unreadable.append(name)
                continue
        env.files.append(item)
    return env


def detect_irb_packet(env: Envelope) -> List[str]:
    """IRB 패킷처럼 보이면 그 근거 파일명을 돌려준다(2개 이상이면 거절)."""
    hits = []
    for item in env.visible_files:
        lowered = nfc(item.name).lower().replace(" ", "").replace("-", "").replace("_", "")
        for token in IRB_FILENAME_TOKENS:
            if token.replace("_", "") in lowered:
                hits.append(f"{item.name} ({token})")
                break
    return hits


class NeedsManuscript(Exception):
    """봉투에 docx 가 여러 개다. 추측하지 않고 ``--manuscript`` 를 요구한다."""

    def __init__(self, candidates: List[str]):
        self.candidates = candidates
        super().__init__("원고를 특정할 수 없습니다")


def choose_manuscript(env: Envelope, explicit: Optional[str]) -> EnvelopeFile:
    """원고 docx 한 개를 고른다. 두 개 이상이면 추측하지 않는다."""
    docs = env.docx_files
    if explicit:
        wanted = nfc(os.path.basename(explicit))
        for item in docs:
            if nfc(item.name) == wanted:
                return item
        raise UsageError(
            f"--manuscript 로 지정한 파일이 봉투에 없습니다: {sanitize(wanted)}\n"
            "  봉투 안 docx: " + (", ".join(sanitize(d.name) for d in docs) or "(없음)")
        )
    if not docs:
        hint = ""
        if env.links:
            hint = ("\n  심볼릭 링크 " + str(len(env.links))
                    + "개는 봉투로 보지 않았습니다: "
                    + ", ".join(sanitize(x) for x in env.links[:5]))
        raise UsageError(
            f"봉투에 .docx 가 없습니다: {env.label}\n"
            "  이 툴은 원고 docx 가 있는 투고 봉투를 봅니다." + hint
        )
    if len(docs) > 1:
        raise NeedsManuscript([d.name for d in docs])
    return docs[0]
