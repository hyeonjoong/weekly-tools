"""출력 안전 — 원본 읽기 전용 · 출력은 --out-dir 에만 · CSV 인젝션 · 링크 방어.

이 툴은 IRB 서류를 읽습니다. 서류를 **덮어쓰는 사고**가 나면 복구가 안 되므로,
입력 경로 집합을 기억해 두고 어떤 쓰기도 그 경로(또는 그 경로의 링크)로 가지
못하게 막습니다.
"""
from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Set

from .model import Coverage

_PROTECTED: Set[str] = set()
_PROTECTED_DIRS: Set[str] = set()
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029]")
MAX_CELL = 2000


class OutputError(Exception):
    pass


def _canon(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.realpath(path)).casefold()


def protect_inputs(paths: Iterable[str]) -> None:
    for p in paths:
        _PROTECTED.add(_canon(p))
        _PROTECTED_DIRS.add(_canon(os.path.dirname(os.path.abspath(p))))


def clear_protected() -> None:
    _PROTECTED.clear()
    _PROTECTED_DIRS.clear()


def prepare_out_dir(path: str) -> str:
    """출력 폴더를 만들고 절대경로를 돌려줍니다. 입력 폴더·입력 파일·링크면 거부."""
    if not path or not path.strip():
        raise OutputError("--out-dir 이 비어 있습니다")
    ap = os.path.abspath(path)
    if os.path.islink(path.rstrip("/\\")) or os.path.islink(ap):
        raise OutputError("--out-dir 이 심볼릭 링크입니다: {}".format(path))
    canon = _canon(ap)
    if canon in _PROTECTED:
        raise OutputError("--out-dir 이 입력 파일과 같습니다: {}".format(path))
    if canon in _PROTECTED_DIRS:
        raise OutputError("--out-dir 이 입력 문서가 든 폴더입니다 — 리포트를 서류 폴더 안에 쓰면 다음 실행이 리포트를 문서로 읽습니다. 다른 폴더를 지정하세요: {}".format(path))
    if os.path.exists(ap) and not os.path.isdir(ap):
        raise OutputError("--out-dir 이 폴더가 아닙니다: {}".format(path))
    os.makedirs(ap, exist_ok=True)
    return ap


def sanitize_cell(value: object) -> str:
    """CSV 셀 — 제어문자 제거, 수식 인젝션 방어(= + - @ 탭 CR 앞에 '), 길이 제한."""
    s = "" if value is None else str(value)
    s = unicodedata.normalize("NFC", s).encode("utf-8", "replace").decode("utf-8")
    s = _CTRL.sub("�", s).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if len(s) > MAX_CELL:
        s = s[:MAX_CELL] + " …(잘림)"
    if s and s[0] in "=+-@":
        s = "'" + s
    return s


def sanitize_line(value: object) -> str:
    """리포트 한 줄 — 개행·제어문자를 가시 문자로 (가짜 [치명] 줄 방지)."""
    s = "" if value is None else str(value)
    s = unicodedata.normalize("NFC", s).encode("utf-8", "replace").decode("utf-8")
    s = s.replace("\r\n", "␤").replace("\n", "␤").replace("\r", "␤")
    return _CTRL.sub("�", s)


def _target(out_dir: str, name: str) -> str:
    if os.sep in name or (os.altsep and os.altsep in name) or name in ("", ".", ".."):
        raise OutputError("잘못된 출력 파일명: {!r}".format(name))
    target = os.path.join(out_dir, name)
    if os.path.islink(target):
        raise OutputError("출력 대상이 심볼릭 링크입니다 — 덮어쓰지 않습니다: {}".format(target))
    if _canon(target) in _PROTECTED:
        raise OutputError("출력 대상이 입력 파일입니다 — 덮어쓰지 않습니다: {}".format(target))
    if os.path.isdir(target):
        raise OutputError("출력 대상 이름의 폴더가 이미 있습니다: {}".format(target))
    if os.path.exists(target):
        st = os.stat(target)
        if st.st_nlink > 1:
            raise OutputError("출력 대상이 하드링크입니다 — 덮어쓰지 않습니다: {}".format(target))
    return target


def _open_write(target: str):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="")


def write_text(out_dir: str, name: str, text: str) -> str:
    target = _target(out_dir, name)
    data = text.encode("utf-8", "replace").decode("utf-8")  # 파일을 열기 전에 인코딩 문제를 걸러 0바이트 파일이 남지 않게
    with _open_write(target) as fh:
        fh.write(data)
    return target


def write_csv(out_dir: str, name: str, header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    target = _target(out_dir, name)
    with _open_write(target) as fh:
        fh.write("\ufeff")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([sanitize_cell(h) for h in header])
        for row in rows:
            w.writerow([sanitize_cell(c) for c in row])
    return target


def relpath_for_display(path: str) -> str:
    """절대경로(홈 디렉터리 사용자명)가 리포트에 새지 않게 파일명만."""
    return os.path.basename(path.rstrip(os.sep)) or path
