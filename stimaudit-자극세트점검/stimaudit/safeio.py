"""출력 안전장치 — 원본을 덮어쓰지 않고, 표 계산기에 코드를 흘리지 않습니다.

세 가지 실제 사고를 막습니다.

0. **검사 후 바꿔치기(TOCTOU).** `--out-dir` 를 검사한 뒤 분석하는 동안(수 분)
   그 폴더를 다른 곳을 가리키는 심볼릭 링크로 바꿔치기할 수 있습니다. 그래서
   검증한 순간의 디렉터리 fd 를 붙잡아 두고 모든 쓰기를 `dir_fd=` 로 합니다.
1. **심볼릭 링크 덮어쓰기.** `--out-dir` 에 산출물과 같은 이름의 심볼릭 링크를
   심어두면, 평범한 `open(path, "w")` 는 링크를 따라가 **원본 WAV 나 설계 JSON 을
   조용히 날립니다.** 이 저장소의 최근 세 툴이 전부 이 결함을 안고 배포됐습니다.
   그래서 모든 산출물 쓰기는 `O_NOFOLLOW | O_CREAT | O_TRUNC` 로 열고, 그 전에
   `os.path.islink` 도 확인합니다(하드링크는 `st_nlink` 로 거절).
2. **CSV 수식 인젝션.** 파일 이름에 `=cmd|...` 가 들어 있으면 Excel/Numbers 가
   수식으로 해석합니다. `= + - @`, 탭·CR 로 시작하는 셀 앞에 작은따옴표를 붙입니다.
3. **경로 순회.** 산출물 이름은 이 모듈이 정한 상수뿐이며, 사용자 입력이
   파일명에 들어가지 않습니다.
"""
from __future__ import annotations

import csv
import errno
import io
import os
from typing import Iterable, List, Optional, Sequence, Union

#: CSV 셀이 이 문자로 시작하면 표 계산기가 수식으로 해석할 수 있습니다.
_FORMULA_LEAD = ("=", "+", "-", "@")


class OutputError(Exception):
    """출력 준비/쓰기 실패 — CLI 가 종료코드 2 로 바꿉니다."""


class OutDir:
    """검증이 끝난 출력 폴더의 **핸들**(경로 + 디렉터리 파일디스크립터).

    경로 문자열만 들고 다니면 검사와 쓰기 사이에 폴더가 심볼릭 링크로
    바뀌어도 알 수 없습니다(분석에 몇 분이 걸리므로 창이 넓습니다).
    검증한 순간의 디렉터리 **아이노드**를 fd 로 붙잡아 두고, 이후 모든 쓰기를
    `dir_fd=` 로 그 아이노드 안에서 수행합니다.

    `os.fspath` 를 지원하므로 `os.path.join(out_dir, name)` 은 그대로 됩니다.
    """

    def __init__(self, path: str, fd: Optional[int]) -> None:
        self.path = path
        self.fd = fd

    def __fspath__(self) -> str:
        return self.path

    def __str__(self) -> str:
        return self.path

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None


def sanitize_cell(value: object) -> str:
    """CSV 셀 하나를 수식 인젝션이 불가능한 문자열로 만듭니다."""
    if value is None:
        return ""
    text = str(value)
    # 개행·복귀·탭은 공백으로 눌러 셀 경계를 깨지 않게 합니다.
    for ch in ("\r\n", "\n", "\r", "\t"):
        text = text.replace(ch, " ")
    # 나머지 제어문자(ESC 등)도 공백으로 눌러 둡니다. 조건 이름·파일 이름은
    # 사람이 적는 값이라 ESC 시퀀스가 섞여 들어올 수 있고, 그대로 CSV 에 남으면
    # 나중에 터미널로 그 파일을 열어보는 사람의 화면을 조작할 수 있습니다.
    text = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in text)
    # 앞쪽 공백을 벗겨 낸 뒤 판정합니다 — " =cmd|..." 처럼 공백으로 위장한 수식도
    # Excel 은 수식으로 읽습니다.
    if text.lstrip(" ")[:1] in _FORMULA_LEAD:
        return "'" + text
    return text


def prepare_out_dir(path: str) -> "OutDir":
    """`--out-dir` 를 만들고 검증합니다. 실패는 전부 한국어 `OutputError`."""
    if not path:
        raise OutputError("--out-dir 경로가 비었습니다.")
    target = os.path.abspath(os.path.expanduser(path))
    if os.path.islink(target):
        raise OutputError(
            "--out-dir 가 심볼릭 링크입니다: {}\n"
            "링크를 따라가면 엉뚱한 곳에 쓰게 되므로 거절합니다. 실제 폴더를 지정하십시오.".format(path))
    if os.path.exists(target) and not os.path.isdir(target):
        raise OutputError(
            "--out-dir 가 폴더가 아니라 파일입니다: {}\n"
            "다른 이름을 쓰거나 그 파일을 옮기십시오.".format(path))
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise OutputError(
            "--out-dir 를 만들 수 없습니다: {}\n사유: {}".format(path, exc.strerror or exc)) from exc
    if not os.access(target, os.W_OK):
        raise OutputError("--out-dir 에 쓸 권한이 없습니다: {}".format(path))
    fd = None
    flags = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, os.O_RDONLY | flags)
    except OSError:
        fd = None          # dir_fd 를 못 얻어도 파일별 O_NOFOLLOW 방어는 남습니다
    return OutDir(target, fd)


#: 이번 실행에서 **읽은** 파일들의 실제 경로. 산출물이 이 중 하나를 덮어쓰려
#: 하면 거절합니다. 심볼릭/하드 링크는 막고 있었지만, `--out-dir` 를 입력이
#: 들어 있는 폴더로 잡고 `--manifest DIR/문제목록.csv` 를 주면 **매니페스트가
#: 산출물로 덮여 사라졌습니다**(그것도 종료코드 0 으로).
#: (적대적 검토 라운드 1, 안전성 감사 A1)
_PROTECTED: set = set()


def protect_inputs(paths) -> None:
    """이번 실행의 입력 파일들을 '덮어쓰기 금지'로 등록합니다."""
    for p in paths:
        if not p:
            continue
        try:
            _PROTECTED.add(os.path.realpath(p))
        except OSError:
            continue


def clear_protected() -> None:
    """테스트·연속 실행용 초기화."""
    _PROTECTED.clear()


def refuse_if_input(path: str) -> None:
    if os.path.realpath(path) in _PROTECTED:
        raise OutputError(
            "산출물이 이번 실행의 **입력 파일**을 덮어쓰려 합니다: {}\n"
            "입력이 들어 있는 폴더를 --out-dir 로 주지 마십시오 "
            "(원본은 읽기 전용입니다).".format(os.path.basename(path)))


def _open_no_follow(out_dir: Union[str, "OutDir"], name: str):
    """심볼릭/하드 링크를 따라가지 않고 새로 쓰기 위한 파일 디스크립터."""
    dir_fd = out_dir.fd if isinstance(out_dir, OutDir) else None
    path = os.path.join(os.fspath(out_dir), name)
    refuse_if_input(path)
    target = name if dir_fd is not None else path
    if os.path.islink(target if dir_fd is None else path):
        raise OutputError(
            "산출물 자리에 심볼릭 링크가 있습니다: {}\n"
            "링크를 따라가면 원본 파일을 덮어쓸 수 있으므로 거절합니다.".format(os.path.basename(path)))
    # O_TRUNC 를 여기서 주면 안 됩니다 — 하드링크 검사(fstat)를 하기 **전에**
    # 내용을 날려 버려서, 거절하더라도 원본은 이미 비어 있게 됩니다.
    flags = os.O_WRONLY | os.O_CREAT
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags | nofollow, 0o644, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise OutputError(
                "산출물 자리에 심볼릭 링크가 있습니다: {}".format(os.path.basename(path))) from exc
        raise OutputError(
            "산출물을 쓸 수 없습니다: {}\n사유: {}".format(
                os.path.basename(path), exc.strerror or exc)) from exc
    try:
        st = os.fstat(fd)
        if st.st_nlink > 1:
            os.close(fd)
            raise OutputError(
                "산출물 자리에 하드링크가 있습니다: {}\n"
                "다른 파일과 내용이 공유되므로 거절합니다.".format(os.path.basename(path)))
        os.ftruncate(fd, 0)      # 검사를 통과한 뒤에야 비웁니다
    except OSError:
        os.close(fd)
        raise
    return fd


def write_text(out_dir: Union[str, "OutDir"], name: str, text: str) -> str:
    """텍스트 산출물 하나를 안전하게 씁니다. 반환은 절대경로."""
    path = os.path.join(os.fspath(out_dir), name)
    fd = _open_no_follow(out_dir, name)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def write_csv(out_dir: Union[str, "OutDir"], name: str, header: Sequence[str],
              rows: Iterable[Sequence[object]]) -> str:
    """CSV 산출물을 씁니다. 모든 셀은 수식 인젝션 방어를 거칩니다.

    BOM 을 붙입니다 — 한국어 Windows Excel 이 UTF-8 CSV 를 깨뜨리지 않게.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([sanitize_cell(h) for h in header])
    for row in rows:
        writer.writerow([sanitize_cell(v) for v in row])
    path = os.path.join(os.fspath(out_dir), name)
    fd = _open_no_follow(out_dir, name)
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(buf.getvalue())
    return path
