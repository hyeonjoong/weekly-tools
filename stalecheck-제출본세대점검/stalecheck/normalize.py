"""내용 해시 — 생성시각 메타데이터를 지우고 비교한다.

이 모듈이 이 툴에서 유일하게 어려운 부분이고, 없으면 툴이 존재할 이유가 없다.
PDF·DOCX 는 **다시 렌더하기만 해도** 바이트가 달라진다. 정규화 없이 비교하면
투고 때마다 "다 다릅니다"가 나오고, 그런 체커는 두 번 열리지 않는다.

정규화는 세 갈래뿐이다:

* ``pdf-meta``       — ``/CreationDate``·``/ModDate``·``/ID`` 를 지운 뒤 해시
* ``zip-docprops``   — zip 컨테이너(docx/xlsx/pptx)에서 ``docProps/`` 를 뺀 뒤
                       엔트리명 + 내용으로 해시
* ``none``           — 원시 바이트

**의도적으로 좁게 잡았다.** 더 지울수록 오탐은 줄지만, 진짜로 낡은 파일을
조용히 통과시키는 위험이 커진다. 지운 항목은 판정 행마다 ``정규화적용`` 으로 실어
나가므로, 어떤 근거로 "같다"고 했는지 사람이 되짚을 수 있다.
"""

import hashlib
import os
import re
import stat
import struct
import zipfile
import zlib

CHUNK = 1 << 20

#: zip 안의 **압축을 푼** 총 바이트 상한. `--max-bytes` 는 디스크 크기만 본다 —
#: 3MB 짜리 .docx 가 3GB 로 부풀 수 있고(zip bomb), 그대로 읽으면 메모리가 터진다.
#: 상한을 넘으면 정규화를 포기하고 원시 바이트로 물러선다(거짓말하지 않는다).
MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024

#: zip 읽기에서 "이 파일은 온전한 zip 이 아니다"로 보고 물러설 예외들.
#: `zlib.error` 가 빠지면 **한두 바이트 손상된 docx 하나가 트레이스백으로 터진다**
#: (무작위 비트 플립 3,000회 중 141회 = 4.7%).
_ZIP_FALLBACK_ERRORS = (zipfile.BadZipFile, RuntimeError, OSError,
                        NotImplementedError, ValueError, EOFError,
                        zlib.error, MemoryError, UnicodeDecodeError)

#: zip 컨테이너로 다루는 확장자 (Office Open XML)
ZIP_EXTS = frozenset({".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"})

#: zip 정규화에서 제외하는 엔트리 접두사 — 작성자·생성시각·편집시간만 들어 있다
ZIP_EXCLUDED_PREFIXES = ("docProps/",)

#: PDF 의 "다시 렌더했다"는 사실만 담고 내용을 담지 않는 항목들.
#:
#: **전부 길이를 제한한다.** 제한 없는 `[^\]]*` 는 압축되지 않은 스트림 안의
#: 우연한 `/ID[` 를 만나면 그 뒤 내용을 통째로 지워 버리고, 그러면 툴은 진짜 차이를
#: 놓친 채 "메타데이터만 달랐다"고 **적극적으로 거짓말한다.**
_PDF_META_PATTERNS = (
    # Info 딕셔너리 (평문)
    re.compile(rb"/CreationDate\s*\((?:\\.|[^)\\]){0,256}\)"),
    re.compile(rb"/ModDate\s*\((?:\\.|[^)\\]){0,256}\)"),
    # `<hex> <hex>` 형태만 지운다. `[^\]]{0,512}` 로 두면 스트림 안의 우연한 `/ID[` 가
    # 본문 512바이트를 삼켜, 서로 다른 문서를 "메타데이터만 다름"으로 만든다(실증됨).
    re.compile(rb"/ID\s*\[\s*(?:<[0-9A-Fa-f\s]{0,128}>\s*){0,4}\]"),
    # XMP 메타데이터 패킷 — Word·Acrobat·LibreOffice 가 함께 심는다.
    # 이걸 빼면 실제 PDF의 상당수가 "다시 저장만 해도 다름"으로 올라온다.
    re.compile(rb"<xmp:CreateDate>[^<]{0,128}</xmp:CreateDate>"),
    re.compile(rb"<xmp:ModifyDate>[^<]{0,128}</xmp:ModifyDate>"),
    re.compile(rb"<xmp:MetadataDate>[^<]{0,128}</xmp:MetadataDate>"),
    re.compile(rb"<xmpMM:InstanceID>[^<]{0,256}</xmpMM:InstanceID>"),
    re.compile(rb"<xmpMM:DocumentID>[^<]{0,256}</xmpMM:DocumentID>"),
    re.compile(rb"<xmpMM:OriginalDocumentID>[^<]{0,256}</xmpMM:OriginalDocumentID>"),
    # RDF 속성 표기 (같은 값을 속성으로 쓰는 생성기가 있다)
    re.compile(rb"xmp:CreateDate=\"[^\"]{0,128}\""),
    re.compile(rb"xmp:ModifyDate=\"[^\"]{0,128}\""),
    re.compile(rb"xmp:MetadataDate=\"[^\"]{0,128}\""),
    re.compile(rb"xmpMM:InstanceID=\"[^\"]{0,256}\""),
    re.compile(rb"xmpMM:DocumentID=\"[^\"]{0,256}\""),
)

NORM_PDF = "pdf-meta"
NORM_ZIP = "zip-docprops"
#: docProps 는 없었지만 zip 컨테이너 수준(엔트리 순서·저장시각·압축방식)을 벗겨 낸 경우.
#: `none` 이라고 적으면 "원시 바이트로 비교했다"는 거짓말이 된다.
NORM_ZIP_CONTAINER = "zip-container"
NORM_NONE = "none"


class _TooBig(Exception):
    """zip 압축 해제 크기가 상한을 넘었다 (내부 신호)."""


class UnreadableFile(Exception):
    """파일을 읽지 못했다. 삼키지 않고 커버리지 자백으로 올라간다."""

    def __init__(self, path, reason):
        super().__init__("%s: %s" % (path, reason))
        self.path = path
        self.reason = reason


def _open_regular(path):
    """일반 파일일 때만 연다.

    스캔 시점에 일반 파일이었어도, 해시하는 순간 FIFO 로 바뀌어 있을 수 있다.
    평범한 `open()` 은 그 자리에서 **영원히 멈춘다.** 논블로킹으로 열고 fd 를
    `fstat` 으로 확인한 다음에만 읽는다.
    """
    try:
        # 플래그를 호출 자리에 그대로 둔다 — AST 검사가 "읽기 전용"임을 확인할 수 있게.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise UnreadableFile(path, exc.strerror or str(exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnreadableFile(path, "일반 파일이 아닙니다")
        # 논블로킹 플래그를 되돌린다 — 일반 파일 읽기에는 필요 없다.
        if hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
    except Exception:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def raw_hash(path):
    """파일의 원시 바이트 SHA-256."""
    h = hashlib.sha256()
    try:
        with _open_regular(path) as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                h.update(chunk)
    except OSError as exc:
        raise UnreadableFile(path, exc.strerror or str(exc))
    return h.hexdigest()


def _pdf_hash(path):
    try:
        with _open_regular(path) as fh:
            data = fh.read()
    except OSError as exc:
        raise UnreadableFile(path, exc.strerror or str(exc))
    stripped = data
    for pattern in _PDF_META_PATTERNS:
        stripped = pattern.sub(b"", stripped)
    if stripped == data:
        # 지울 메타데이터가 없었다 — 정규화했다고 말하지 않는다.
        return hashlib.sha256(data).hexdigest(), NORM_NONE
    return hashlib.sha256(stripped).hexdigest(), NORM_PDF


def _zip_hash(path):
    """zip 컨테이너를 엔트리 단위로 해시한다.

    **길이 접두사를 반드시 붙인다.** `name || content` 를 그냥 이어 붙이면
    `("a", b"Xb\\x00Y")` 와 `("a", b"X"), ("b", b"Y")` 가 같은 다이제스트를 낸다 —
    즉 **서로 다른 문서를 '일치'로 판정**할 수 있다.

    또 `namelist()` + `read(name)` 은 **이름이 중복된 엔트리에서 마지막 것만** 읽는다.
    앞엣것의 바이트가 해시에 전혀 들어가지 않으므로, `infolist()` 로 전부 훑는다.
    """
    excluded = 0
    digests = []
    total = 0
    try:
        with _open_regular(path) as raw, zipfile.ZipFile(raw) as zf:
            for info in zf.infolist():
                if info.filename.startswith(ZIP_EXCLUDED_PREFIXES):
                    excluded += 1
                    continue
                name = info.filename.encode("utf-8", "surrogateescape")
                entry = hashlib.sha256()
                entry.update(struct.pack("<Q", len(name)))
                entry.update(name)
                # 엔트리를 통째로 메모리에 올리지 않고 흘려 보내며 해시한다.
                with zf.open(info) as fh:
                    while True:
                        chunk = fh.read(CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_ZIP_UNCOMPRESSED:
                            raise _TooBig()
                        entry.update(chunk)
                digests.append((name, entry.digest()))
    except UnreadableFile:
        raise
    except _TooBig:
        return raw_hash(path), NORM_NONE
    except _ZIP_FALLBACK_ERRORS:
        # 암호가 걸렸거나 zip 이 아니거나 손상됐다 — 원시 바이트로 물러선다.
        return raw_hash(path), NORM_NONE
    # 엔트리 간 순서는 무시하되(zip 저장 순서는 내용이 아니다), **같은 이름이 여러 번
    # 나오는 경우의 순서는 보존**한다. 다이제스트만 정렬하면 `x=1,x=2` 와 `x=2,x=1` 이
    # 같아지는데, 중복 엔트리를 읽는 쪽에는 서로 다른 문서다.
    grouped = {}
    for name, digest in digests:
        grouped.setdefault(name, []).append(digest)
    h = hashlib.sha256()
    h.update(struct.pack("<Q", len(digests)))
    for name in sorted(grouped):
        h.update(struct.pack("<QQ", len(name), len(grouped[name])))
        h.update(name)
        for digest in grouped[name]:
            h.update(digest)
    if excluded == 0:
        return h.hexdigest(), NORM_ZIP_CONTAINER
    return h.hexdigest(), NORM_ZIP


def content_hash(path):
    """(해시, 정규화종류) 를 돌려준다.

    정규화가 실제로 무언가를 지웠을 때에만 ``pdf-meta``/``zip-docprops`` 로
    표시한다. 아무것도 안 지웠으면 ``none`` — 리포트가 과장하지 않게 하려는 것.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _pdf_hash(path)
    if ext in ZIP_EXTS:
        return _zip_hash(path)
    return raw_hash(path), NORM_NONE
