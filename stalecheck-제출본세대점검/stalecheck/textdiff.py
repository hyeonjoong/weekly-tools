"""텍스트 파일은 몇 줄이 다른지까지 센다.

바이너리는 크기 차이만. 텍스트는 ``+N/-N`` 행수와 추가된 첫 3행을 보여 준다 —
실제 사고 두 건(재현성 예치본에서 계산 블록이 통째로 빠진 것, 저널이 요구하는
``pdf.fonttype=42`` 가 빠진 것)이 그래서 눈에 띄었다.
"""

import difflib
import os
import re

from .normalize import UnreadableFile, _open_regular

#: 행 단위로 비교할 확장자
TEXT_EXTS = frozenset({".py", ".md", ".csv", ".txt", ".tex", ".json", ".r",
                       ".do", ".sql", ".yml", ".yaml", ".bib", ".html", ".css"})

#: 이보다 큰 텍스트 파일은 행 비교를 건너뛴다(메모리·시간 보호)
MAX_DIFF_BYTES = 8 * 1024 * 1024

#: 미리보기 행 수와 폭
PREVIEW_LINES = 3
PREVIEW_WIDTH = 110

_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")

#: 제어문자 — 파일 안의 ANSI 이스케이프나 개행이 리포트 행을 위조하지 못하게 지운다
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize(text, width=PREVIEW_WIDTH):
    """리포트에 실을 한 줄로 만든다. 제어문자 제거 + 길이 제한."""
    cleaned = _CONTROL_RE.sub("�", text.replace("\t", "    "))
    cleaned = cleaned.replace("\r", "").replace("\n", " ")
    if width and len(cleaned) > width:
        cleaned = cleaned[:width - 1] + "…"
    return cleaned


#: 리포트 한 줄에 실을 수 있는 경로 최대 길이
PATH_WIDTH = 160


def safe_path(text):
    """**파일 이름도 신뢰하지 않는다.**

    macOS 파일명에는 개행·ESC·백틱이 들어갈 수 있다. 그대로 인쇄하면 파일 하나가
    ``[치명] 봉투가 구세대 — 0쌍`` 같은 **가짜 판정 줄을 위조**하고, ANSI 시퀀스로
    앞 줄을 지우고, 백틱으로 마크다운 코드 펜스를 닫아 버린다. 경로는 전부 이 함수를
    거쳐야 리포트에 실린다.
    """
    return sanitize(str(text), width=PATH_WIDTH).replace("`", "ˋ")


def _read_lines(path):
    try:
        with _open_regular(path) as fh:
            raw = fh.read(MAX_DIFF_BYTES + 1)
    except (OSError, UnreadableFile):
        return None
    if len(raw) > MAX_DIFF_BYTES:
        return None
    if b"\x00" in raw:
        return None  # 확장자가 텍스트여도 내용이 바이너리면 비교하지 않는다
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return None


class LineDiff(object):
    __slots__ = ("added", "removed", "preview", "comparable", "reason")

    def __init__(self, added=0, removed=0, preview=(), comparable=True, reason=""):
        self.added = added
        self.removed = removed
        self.preview = list(preview)
        self.comparable = comparable
        self.reason = reason


def line_diff(pkg_path, work_path, preview_lines=PREVIEW_LINES):
    """봉투본 → 작업본 방향의 행 차이. 비교 불가면 ``comparable=False``."""
    ext = os.path.splitext(work_path)[1].lower()
    if ext not in TEXT_EXTS:
        return LineDiff(comparable=False, reason="대조불가(바이너리)")
    old = _read_lines(pkg_path)
    new = _read_lines(work_path)
    if old is None or new is None:
        return LineDiff(comparable=False, reason="대조불가(디코딩/크기)")
    # `unified_diff` 의 텍스트를 파싱하면 `--` 로 시작하는 삭제 줄이 `---` 가 되어
    # 헤더와 구분되지 않는다(`++` 도 마찬가지). 그래서 텍스트 대신 opcode 로 센다.
    added = removed = 0
    preview = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
            for line in new[j1:j2]:
                if len(preview) < preview_lines:
                    preview.append("+ " + sanitize(line))
    return LineDiff(added=added, removed=removed, preview=preview)
