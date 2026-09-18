"""파일 트리를 읽는다 — 읽기만 한다.

macOS 파일명은 NFD 로 저장되므로, 이름 비교 전에 반드시 NFC 로 정규화한다.
이걸 빼먹으면 한글 파일명 쌍이 통째로 짝지어지지 않고, **그 사실이 조용히 넘어간다.**
"""

import os
import re
import unicodedata
from collections import Counter

#: 기본 스캔 확장자 — 원고·그림·분석 스크립트·표
DEFAULT_EXTS = (".png", ".pdf", ".docx", ".md", ".py", ".csv")

#: 60MB 초과는 건너뛴다(트리에 50MB 넘는 pptx·wav 가 있다). 건너뛴 파일은 자백한다.
DEFAULT_MAX_BYTES = 60 * 1024 * 1024

#: 폴더 이름이 이 중 하나를 포함하면 "봉투"로 본다(NFC·소문자 비교).
PACKAGE_TOKENS = ("submission", "제출용", "제출패키", "제출본")

#: 작업 폴더가 아닌 것들 — 아카이브/구버전. 여기를 작업본으로 세면
#: 아카이브가 봉투와 짝지어져 전부 "구세대"로 찍힌다.
DEFAULT_EXCLUDE_PATTERN = re.compile(
    r"(_이전|이전버전|_superseded|superseded|_archive|archive|_backup|백업"
    r"|__pycache__|\.git|\.venv|node_modules)",
    re.IGNORECASE,
)


def nfc(text):
    """macOS 의 NFD 파일명을 NFC 로. 비교 전에 **항상** 거친다."""
    return unicodedata.normalize("NFC", text)


def is_package_name(name):
    """폴더 이름 하나가 봉투로 보이는가."""
    lowered = nfc(name).lower()
    return any(token in lowered for token in PACKAGE_TOKENS)


class FileRecord(object):
    """스캔된 파일 하나. 해시는 아직 계산하지 않는다(지연)."""

    __slots__ = ("path", "rel", "name_key", "mtime", "size", "ext")

    def __init__(self, path, rel, mtime, size):
        self.path = path
        self.rel = rel
        self.name_key = nfc(os.path.basename(rel)).casefold()
        self.mtime = mtime
        self.size = size
        self.ext = os.path.splitext(rel)[1].lower()

    def __repr__(self):  # pragma: no cover - 디버깅 편의
        return "FileRecord(%r)" % (self.rel,)


class ScanResult(object):
    """한 트리를 읽은 결과 + 못 읽은 것들의 자백."""

    def __init__(self):
        self.files = []
        self.skipped_large = []      # (rel, size)
        self.unreadable = []         # (rel, reason)
        self.excluded_dirs = []      # rel (아카이브 등으로 제외한 폴더)
        self.package_dirs = []       # rel (봉투로 보여 작업본에서 뺀 폴더)
        self.symlinks = []           # rel (따라가지 않은 심볼릭 링크)
        #: 확장자가 대상 밖이라 아예 보지 않은 파일 수 (확장자 → 개수).
        #: 이걸 세지 않으면 봉투가 전부 .tif 일 때 "읽음 0/0 · 치명 0건" 이 나오고,
        #: 사람은 그것을 "다 확인했다"로 읽는다. 커버리지 자백에 반드시 올라간다.
        self.skipped_ext = Counter()
        #: 숨김 파일(`.` 로 시작)도 세어 둔다. 세지 않으면 `.숨은원고.md` 가
        #: 흔적 없이 사라지고 "읽음 2/2" 가 인쇄된다.
        self.hidden = []

    @property
    def read_count(self):
        return len(self.files)

    @property
    def total_seen(self):
        return len(self.files) + len(self.skipped_large) + len(self.unreadable)


def _walk(root, exts, max_bytes, exclude_pattern, result, package_mode):
    """root 아래를 훑는다. package_mode=False 면 봉투 폴더를 작업본에서 제외한다."""
    ext_set = frozenset(e.lower() for e in exts)

    def on_walk_error(exc):
        # os.walk 는 기본으로 오류를 삼킨다 — 권한 없는 폴더가 통째로 사라지고
        # "읽음 2/2 · 못 읽음 0" 이 나온다. 그건 자백이 아니라 거짓말이다.
        target = getattr(exc, "filename", None) or str(exc)
        try:
            rel = nfc(os.path.relpath(target, root))
        except ValueError:
            rel = nfc(str(target))
        result.unreadable.append((rel, exc.strerror or str(exc)))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False,
                                                onerror=on_walk_error):
        kept = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                result.symlinks.append(nfc(rel))
                continue
            if d.startswith("."):
                result.excluded_dirs.append(nfc(rel))
                continue
            if exclude_pattern is not None and exclude_pattern.search(nfc(d)):
                result.excluded_dirs.append(nfc(rel))
                continue
            if not package_mode and is_package_name(d):
                # 작업 폴더 안의 또 다른 봉투(예: review/_repro/submission).
                # 이걸 작업본으로 세면 봉투끼리 짝지어져 유령 불일치가 난다.
                result.package_dirs.append(nfc(rel))
                continue
            kept.append(d)
        dirnames[:] = kept

        for fn in filenames:
            if fn.startswith("."):
                if os.path.splitext(fn)[1].lower() in ext_set:
                    result.hidden.append(nfc(os.path.relpath(
                        os.path.join(dirpath, fn), root)))
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ext_set:
                result.skipped_ext[ext or "(확장자없음)"] += 1
                continue
            full = os.path.join(dirpath, fn)
            rel = nfc(os.path.relpath(full, root))
            if os.path.islink(full):
                result.symlinks.append(rel)
                continue
            try:
                st = os.stat(full)
            except OSError as exc:
                result.unreadable.append((rel, exc.strerror or str(exc)))
                continue
            if not os.path.isfile(full):
                continue
            if st.st_size > max_bytes:
                result.skipped_large.append((rel, st.st_size))
                continue
            if not os.access(full, os.R_OK):
                result.unreadable.append((rel, "읽기 권한 없음"))
                continue
            result.files.append(FileRecord(full, rel, st.st_mtime, st.st_size))


def scan_tree(root, exts=DEFAULT_EXTS, max_bytes=DEFAULT_MAX_BYTES,
              exclude_pattern=DEFAULT_EXCLUDE_PATTERN, package_mode=False,
              skip_subtrees=()):
    """트리 하나를 읽는다.

    ``package_mode=True`` 면 봉투 폴더를 제외하지 않는다(봉투 자신을 읽을 때).
    ``skip_subtrees`` 는 절대경로 목록 — 지정한 하위 트리는 통째로 건너뛴다
    (봉투를 작업 폴더에서 빼낼 때 쓴다).
    """
    result = ScanResult()
    _walk(root, exts, max_bytes, exclude_pattern, result, package_mode)
    if skip_subtrees:
        # 봉투 하위 트리는 파일뿐 아니라 **자백 목록에서도** 빠져야 한다.
        # 빼지 않으면 봉투를 따로 스캔할 때 같은 파일이 두 번 세어져
        # "읽음 2/4 · 건너뜀 2" 처럼 분모가 부풀고 같은 경로가 두 번 인쇄된다.
        skip_rel = tuple(
            nfc(os.path.relpath(os.path.abspath(p), os.path.abspath(root))) + os.sep
            for p in skip_subtrees)

        def outside(rel):
            return not rel.startswith(skip_rel)

        result.files = [r for r in result.files if outside(r.rel)]
        result.skipped_large = [t for t in result.skipped_large if outside(t[0])]
        result.unreadable = [t for t in result.unreadable if outside(t[0])]
        result.symlinks = [r for r in result.symlinks if outside(r)]
        result.hidden = [r for r in result.hidden if outside(r)]
    return result


def find_package_candidates(root, exclude_pattern=DEFAULT_EXCLUDE_PATTERN, limit=200):
    """작업 폴더 아래에서 봉투로 보이는 폴더를 찾아 (상대경로, 근거) 목록으로 돌려준다.

    **추론해서 바로 판정하지 않는다.** 후보를 인쇄하고 ``--package`` 를 요구한다.
    이 트리에는 명명 규약이 셋 공존한다(``submission/``, ``01_제출용_SUBMISSION/``,
    ``제출패키지``) — 추론이 틀리면 전부 헛짚는다.
    """
    found = []
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        kept = []
        for d in dirnames:
            if d.startswith(".") or os.path.islink(os.path.join(dirpath, d)):
                continue
            if exclude_pattern is not None and exclude_pattern.search(nfc(d)):
                continue
            kept.append(d)
        dirnames[:] = kept
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            continue
        parts = [nfc(p) for p in rel.split(os.sep)]
        if not is_package_name(parts[-1]):
            continue
        if any(is_package_name(p) for p in parts[:-1]):
            continue  # 이미 봉투 안이다 — 최상위 봉투만 후보로 센다
        matched = [t for t in PACKAGE_TOKENS if t in parts[-1].lower()]
        found.append((nfc(rel), "폴더 이름에 %s 포함"
                      % ", ".join("'%s'" % t for t in matched)))
        if len(found) >= limit:
            break
    found.sort()
    return found
