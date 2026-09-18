"""쓰기는 여기에서만 일어난다 — 그리고 ``--out-dir`` 아래에만.

이 파일 밖의 어떤 모듈도 파일을 쓰지 않는다(``tests/test_safety_ast.py`` 가 AST로 강제).
입력 트리는 절대 건드리지 않는다: 복사·덮어쓰기·이동·삭제 함수가 저장소 어디에도 없다.

**경로 비교는 어휘가 아니라 실체로 한다.** `os.path.abspath` 는 심볼릭 링크를 풀지 않고,
대소문자를 접지 않고, NFC 로 정규화하지 않는다. macOS 에서는 셋 다 문제가 된다 —
`/tmp` 는 `/private/tmp` 로의 심볼릭 링크이고, APFS 는 기본이 대소문자 무시이며,
한글 경로는 NFD 로 저장된다. 그래서 `--out-dir` 이 입력 트리 안인지 볼 때는
`os.path.realpath` + NFC + casefold 로 비교한다. 이 중 하나라도 빠지면
**리포트가 입력 폴더 안으로 떨어진다.**

링크를 통한 덮어쓰기도 막는다. 심볼릭 링크는 `O_NOFOLLOW` 가 막지만 **하드 링크는 막지
못한다** — 그리고 "검사 후 열기" 사이에 링크를 심는 경합이 존재한다. 그래서
`O_TRUNC` 없이 연 뒤 **열린 fd 를 `fstat` 으로 다시 검사하고** 통과했을 때에만
`ftruncate` 한다. 이렇게 하면 경합 창이 사라진다.
"""

import csv
import io
import os
import stat
import unicodedata

from .errors import RefusedError

#: 스프레드시트가 수식으로 해석하는 선두 문자 — 앞에 작은따옴표를 붙여 무력화한다.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: 선행 공백으로 검사를 피해 가지 못하게 함께 벗겨 내는 문자들
_LEADING_BLANKS = " \t ​﻿"


def sanitize_cell(value):
    """CSV 수식 주입 방어 + 제어문자 제거."""
    if value is None:
        text = ""
    elif isinstance(value, float):
        text = ("%.6f" % value).rstrip("0").rstrip(".")
    else:
        text = str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = "".join(ch if (ch >= " " or ch == "\t") else "�" for ch in text)
    if text.lstrip(_LEADING_BLANKS)[:1] in _FORMULA_PREFIXES:
        text = "'" + text
    return text


def canonical(path):
    """비교용 경로 — 심볼릭 링크를 풀고 NFC 로 정규화한다."""
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    return unicodedata.normalize("NFC", resolved)


def is_inside(child, parent, fold=True):
    """``child`` 가 ``parent`` 와 같거나 그 안인가.

    ``fold=True``(기본)는 대소문자를 접는다. 대소문자를 구분하는 볼륨에서 서로 다른
    두 폴더를 같다고 볼 수 있지만, **거절하는 쪽으로 쓸 때는 그 오판이 안전**하다
    (`--out-dir` 봉쇄).

    ``fold=False`` 는 **받아들이는 쪽**에서 쓴다(`--package` 가 `--work` 안인지).
    여기서 대소문자를 접으면 대소문자 구분 파일시스템에서 `work/` 와 `Work/` 가
    같다고 판정돼 **트리 밖 폴더를 봉투로 받아들인다.**
    """
    c = canonical(child)
    p = canonical(parent)
    if fold:
        c, p = c.casefold(), p.casefold()
    return c == p or c.startswith(p + os.sep)


#: `prepare_out_dir` 이 검증한 폴더의 신원 (경로 → (st_dev, st_ino)).
#: 검증 뒤 그 경로가 심볼릭 링크로 바꿔치기돼도, 우리가 여는 것은 **그때 그 폴더**다.
_VERIFIED_OUT_DIRS = {}


def prepare_out_dir(out_dir, forbidden_roots=()):
    """``--out-dir`` 을 검사하고 만든다. 문제가 있으면 한국어 한 줄로 거절(exit 2)."""
    out_dir = os.path.abspath(os.path.expanduser(out_dir))

    def _reject_if_inside(where):
        for root in forbidden_roots:
            if is_inside(where, root):
                raise RefusedError(
                    "--out-dir 이 입력 폴더 안입니다: %s\n"
                    "  (심볼릭 링크·대소문자·한글 자모 표기를 풀어 비교했습니다)\n"
                    "  리포트를 입력 트리 밖에 쓰도록 다른 경로를 지정하세요." % where
                )

    _reject_if_inside(out_dir)

    if os.path.islink(out_dir):
        raise RefusedError("--out-dir 이 심볼릭 링크입니다: %s" % out_dir)
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise RefusedError("--out-dir 이 폴더가 아니라 파일입니다: %s" % out_dir)
    if not os.path.exists(out_dir):
        try:
            os.makedirs(out_dir)
        except OSError as exc:
            raise RefusedError(
                "--out-dir 을 만들 수 없습니다: %s (%s)"
                % (out_dir, exc.strerror or exc)
            )
    # 만든 뒤 실체 경로로 한 번 더 — 만드는 사이에 링크가 끼어들 수 있다.
    _reject_if_inside(out_dir)
    if not os.access(out_dir, os.W_OK | os.X_OK):
        raise RefusedError("--out-dir 에 쓸 권한이 없습니다: %s" % out_dir)
    st = os.stat(out_dir)
    _VERIFIED_OUT_DIRS[out_dir] = (st.st_dev, st.st_ino)
    return out_dir


def _open_verified_dir(out_dir):
    """검증된 바로 그 폴더를 연다.

    경로 문자열로 산출물을 열면, 검증과 쓰기 사이에 `--out-dir` 자체를 입력 트리로
    향하는 심볼릭 링크로 바꿔치기해 **입력 파일을 덮어쓸 수 있다**(실증됨).
    폴더 fd 를 잡고 `dir_fd=` 로 쓰면 그 창이 사라진다.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        dir_fd = os.open(out_dir, flags)
    except OSError as exc:
        raise RefusedError(
            "--out-dir 을 열 수 없습니다: %s (%s)" % (out_dir, exc.strerror or exc))
    expected = _VERIFIED_OUT_DIRS.get(out_dir)
    if expected is not None:
        st = os.fstat(dir_fd)
        if (st.st_dev, st.st_ino) != expected:
            os.close(dir_fd)
            raise RefusedError(
                "--out-dir 이 검사 이후 다른 폴더로 바뀌었습니다 — 중단합니다: %s"
                % out_dir)
    return dir_fd


def _target_path(out_dir, filename):
    if os.sep in filename or (os.altsep and os.altsep in filename) or filename in (".", ".."):
        raise RefusedError("산출물 이름에 경로 구분자를 쓸 수 없습니다: %s" % filename)
    return os.path.join(out_dir, filename)


def check_artifact_targets(out_dir, filenames):
    """쓰기 전에 산출물 자리를 **모두** 먼저 검사한다.

    다섯 번째에서 거절하면 앞의 네 개가 이미 디스크에 남아 "낡은 리포트 한 벌"이
    된다. 그래서 하나라도 문제가 있으면 **아무것도 쓰기 전에** 멈춘다.
    """
    os.close(_open_verified_dir(out_dir))
    for filename in filenames:
        target = _target_path(out_dir, filename)
        if os.path.islink(target):
            raise RefusedError(
                "산출물 자리에 심볼릭 링크가 있습니다 — 입력을 덮어쓸 수 있어 중단합니다: %s"
                % target
            )
        if os.path.exists(target):
            st = os.lstat(target)
            if st.st_nlink > 1:
                raise RefusedError(
                    "산출물 자리에 하드 링크가 있습니다 — 다른 파일을 함께 덮어씁니다: %s"
                    % target
                )
            if not stat.S_ISREG(st.st_mode):
                raise RefusedError(
                    "산출물 자리에 일반 파일이 아닌 것이 있습니다: %s" % target)


def _open_artifact(out_dir, filename):
    """산출물 하나를 연다. 링크·탈출 경로는 거절한다.

    ``O_TRUNC`` 을 쓰지 않고 연 다음 **열린 fd** 를 검사한다. 검사와 열기 사이에
    하드 링크가 심어져도, 자르기 전에 `fstat` 이 그것을 본다.
    """
    target = _target_path(out_dir, filename)
    dir_fd = _open_verified_dir(out_dir)
    try:
        if os.path.islink(target):
            raise RefusedError(
                "산출물 자리에 심볼릭 링크가 있습니다 — 입력을 덮어쓸 수 있어 중단합니다: %s"
                % target
            )
        # O_NONBLOCK 이 없으면 **FIFO 에서 커널이 멈춘다** — fstat 까지 가지도 못한다.
        try:
            fd = os.open(filename,
                         os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0),
                         0o600, dir_fd=dir_fd)
        except OSError as exc:
            raise RefusedError(
                "산출물을 쓸 수 없습니다: %s (%s)" % (target, exc.strerror or exc)
            )
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RefusedError("산출물 자리가 일반 파일이 아닙니다: %s" % target)
        if st.st_nlink > 1:
            raise RefusedError(
                "산출물 자리에 하드 링크가 있습니다 — 다른 파일을 함께 덮어씁니다: %s"
                % target
            )
        if hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
        os.ftruncate(fd, 0)
    except Exception:
        os.close(fd)
        raise
    return fd, target


def write_text(out_dir, filename, text):
    """UTF-8(BOM 없음) 텍스트 산출물."""
    fd, target = _open_artifact(out_dir, filename)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return target


def write_csv(out_dir, filename, header, rows):
    """엑셀에서 바로 열리도록 UTF-8 BOM. 모든 셀은 수식 주입을 막아 기록한다."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([sanitize_cell(h) for h in header])
    for row in rows:
        writer.writerow([sanitize_cell(c) for c in row])
    fd, target = _open_artifact(out_dir, filename)
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(buf.getvalue())
    return target
