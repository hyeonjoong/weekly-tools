"""산출물 쓰기. 이 저장소에서 반복해서 났던 사고를 코드로 막는다.

1. ``--out-dir`` 에 산출물 이름으로 심볼릭 링크를 심어 두면 그 링크가 가리키는
   **입력 파일이 조용히 덮어써진다.** → 링크는 거절한다(O_NOFOLLOW + 사전 검사).
2. 하드링크도 같은 사고를 낸다(``st_nlink > 1``). → 거절한다.
3. ``--out-dir`` 이 이미 파일이거나 권한이 없으면 트레이스백 대신 한국어 오류.
4. CSV 셀이 ``=``·``+``·``-``·``@`` 로 시작하면 엑셀이 **수식으로 실행**한다.
5. 절대경로(집 디렉터리 이름)가 산출물에 새어 나가면 파일을 공유할 수 없다.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from typing import Iterable, List, Sequence

#: 평범한 숫자(음수 포함). 이건 수식이 아니라 값이므로 따옴표를 붙이면 안 된다.
_PLAIN_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")

#: 엑셀·구글시트가 수식으로 해석하는 시작 문자.
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r", "\x0b", "\x0c")


class OutputError(Exception):
    """산출물을 쓸 수 없을 때. CLI 가 한국어 메시지 + exit 2 로 바꾼다."""


#: 절대경로 조각. 오류 메시지에 집 디렉터리 이름이 섞여 나가면 안 된다.
_ABS_PATH_RE = re.compile(r"(/[^\s:\"']+)+")


def strip_paths(text: str) -> str:
    """문자열 안의 절대경로를 파일 이름만 남기고 지운다."""
    if not text:
        return text
    return _ABS_PATH_RE.sub(lambda m: os.path.basename(m.group(0).rstrip("/"))
                            or "(경로)", str(text))


def _mask_phone(match) -> str:
    """국가번호·지역번호만 남기고 나머지는 가린다."""
    country = match.group(1) or ""
    return "%s%s-****-****" % (country, match.group(2))


def mask_identifiers(text: str) -> str:
    """산출물로 나가는 문장에서 식별 패턴을 가린다.

    이 툴은 셀 값을 근거로 그대로 인쇄한다(그게 쓸모의 핵심이다). 그런데 전사
    칸에 전화번호나 주민등록번호가 적혀 들어오는 일이 실제로 있다. 값을 버리면
    근거가 사라지므로, **모양만 남기고 가린다.**
    """
    if not text:
        return text
    # 전각 숫자를 반각으로 펴서 우회를 막는다(normalize 를 안 거친 문자열도 온다).
    text = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in text)
    # 주민번호를 먼저 가린다 — 전화번호 판이 그 일부를 먼저 먹으면
    # 주민번호가 전화번호인 것처럼 잘못 마스킹된다.
    out = _RRN_RE.sub(lambda m: m.group(1) + "-*******", text)
    out = _PHONE_RE.sub(_mask_phone, out)
    out = _BIRTH_RE.sub(lambda m: m.group(0)[:4] + "-**-**", out)
    return out


#: 주민등록번호 — 구분자가 있든 없든, 뒷자리 첫 숫자가 0·9 여도 가린다.
#: (1~8만 보던 판이 실제로 900101-9234567 을 통과시켰다.)
_RRN_RE = re.compile(r"(?<!\d)(\d{6})\s*[-\u2010-\u2015]?\s*(\d{7})(?!\d)")
#: 휴대전화 + 지역번호 유선 + 국가번호.
_PHONE_RE = re.compile(
    r"(?<!\d)(\+?82[-\s.]?)?"
    r"(0?1[0-9]|0[2-6][0-9]{0,2})"
    r"[-\s.\u2010-\u2015]?\d{3,4}[-\s.\u2010-\u2015]?\d{4}(?!\d)")
_BIRTH_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])(?!\d)")



def base_name(path: str) -> str:
    """산출물에 적어도 되는 이름 — 경로를 떼고 파일 이름만 남긴다."""
    return os.path.basename(os.path.normpath(str(path)))


def sanitize_cell(value) -> str:
    """CSV 한 칸을 안전하게 만든다.

    - 수식 시작 문자는 앞에 ``'`` 를 붙여 무력화한다.
    - 제어문자(개행·탭 포함)는 지운다 — 리포트·CSV 줄을 위조할 수 없게.
    """
    if value is None:
        return ""
    text = str(value)
    text = "".join(
        ch for ch in text
        if not (unicodedata.category(ch) in ("Cc", "Cf") or ch in "\r\n\t")
    )
    text = mask_identifiers(text)
    if text.startswith(FORMULA_LEADERS):
        # 음수(-7.2)는 수식이 아니라 값이다. 여기에 따옴표를 붙이면 임계차의
        # '변화%p' 열이 통째로 문자열이 되어 다음 툴이 읽지 못한다.
        if not _PLAIN_NUMBER_RE.match(text):
            text = "'" + text
    return text


def sanitize_line(value) -> str:
    """리포트 한 줄에 넣을 문자열 — 줄바꿈·제어문자를 없앤다.

    파일 이름이나 셀 값에 ``\\n[치명] ...`` 을 심어 가짜 소견 줄을 만드는 것을 막는다.
    """
    if value is None:
        return ""
    return "".join(
        ch for ch in str(value)
        if not (unicodedata.category(ch) in ("Cc", "Cf") or ch in "\r\n\t")
    )


def ensure_subdir(parent: str, name: str) -> str:
    """산출물 폴더 **안의** 하위 폴더를 만든다.

    ``os.makedirs(..., exist_ok=True)`` 는 그 자리에 있는 심볼릭 링크를 그대로
    받아들인다(``os.path.isdir`` 이 링크를 따라가므로). 그러면 하위 폴더 안의
    파일이 전부 --out-dir 바깥으로 빠져나간다 — 개별 파일 링크 검사만으로는
    막히지 않는 경로다.
    """
    target = os.path.join(parent, name)
    if os.path.islink(target):
        raise OutputError(
            "산출물 하위 폴더 자리에 심볼릭 링크가 있습니다: %s\n"
            "  링크를 따라가면 산출물이 --out-dir 바깥에 쓰입니다. 링크를 지우고 "
            "다시 실행하세요." % name)
    if os.path.exists(target) and not os.path.isdir(target):
        raise OutputError(
            "산출물 하위 폴더 자리에 폴더가 아닌 것이 있습니다: %s" % name)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise OutputError("산출물 하위 폴더를 만들 수 없습니다: %s (%s)"
                          % (name, exc.strerror or exc))
    return target


def ensure_out_dir(path: str) -> str:
    """산출물 폴더를 만들고 검사한다. 문제가 있으면 :class:`OutputError`."""
    if not str(path).strip():
        raise OutputError("--out-dir 이 비어 있습니다.")
    target = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.islink(target):
        raise OutputError(
            "--out-dir 이 심볼릭 링크입니다: %s\n"
            "  링크가 가리키는 곳에 파일을 쓰면 원본을 덮어쓸 수 있어 거절합니다."
            % base_name(target))
    if os.path.exists(target) and not os.path.isdir(target):
        raise OutputError(
            "--out-dir 자리에 이미 '파일'이 있습니다: %s\n"
            "  폴더 이름을 다른 것으로 바꾸거나 그 파일을 옮겨 주세요."
            % base_name(target))
    try:
        os.makedirs(target, exist_ok=True)
    except PermissionError:
        raise OutputError(
            "--out-dir 을 만들 권한이 없습니다: %s\n"
            "  쓰기 가능한 폴더를 지정해 주세요." % base_name(target))
    except OSError as exc:
        raise OutputError("--out-dir 을 만들 수 없습니다: %s (%s)"
                          % (base_name(target), exc.strerror or exc))
    if not os.access(target, os.W_OK):
        raise OutputError(
            "--out-dir 에 쓸 수 없습니다(권한): %s" % base_name(target))
    return target


def _open_exclusive(path: str):
    """링크를 따라가지 않고 쓰기 위해 여는 저수준 open."""
    if os.path.islink(path):
        raise OutputError(
            "산출물 자리에 심볼릭 링크가 있습니다: %s\n"
            "  링크를 따라가면 엉뚱한 파일(입력 원본일 수 있음)을 덮어씁니다. "
            "  링크를 지우고 다시 실행하세요." % base_name(path))
    if os.path.exists(path):
        try:
            st = os.stat(path)
        except OSError as exc:
            raise OutputError("산출물 상태를 확인할 수 없습니다: %s (%s)"
                              % (base_name(path), exc))
        if st.st_nlink > 1:
            raise OutputError(
                "산출물 자리의 파일이 다른 이름과 하드링크로 묶여 있습니다: %s\n"
                "  덮어쓰면 그 다른 파일도 같이 바뀌므로 거절합니다." % base_name(path))
        if not os.path.isfile(path):
            raise OutputError("산출물 자리에 파일이 아닌 것이 있습니다: %s"
                              % base_name(path))
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags, 0o644)
    except OSError as exc:
        if getattr(exc, "errno", None) in (40, 62, 92):     # ELOOP 계열
            raise OutputError("산출물 자리가 링크입니다: %s" % base_name(path))
        raise OutputError("산출물을 쓸 수 없습니다: %s (%s)"
                          % (base_name(path), exc.strerror or exc))


def write_text(path: str, text: str) -> None:
    fd = _open_exclusive(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        raise OutputError("산출물을 쓰지 못했습니다: %s (%s)"
                          % (base_name(path), exc.strerror or exc))


def write_csv(path: str, header: Sequence[str],
              rows: Iterable[Sequence[object]]) -> None:
    """UTF-8(BOM) CSV. 엑셀이 한글을 깨뜨리지 않도록 BOM 을 붙인다."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([sanitize_cell(c) for c in header])
    for row in rows:
        writer.writerow([sanitize_cell(c) for c in row])
    fd = _open_exclusive(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(buf.getvalue())
    except OSError as exc:
        raise OutputError("CSV 를 쓰지 못했습니다: %s (%s)"
                          % (base_name(path), exc.strerror or exc))


def read_text_any(path: str) -> str:
    """cp949·utf-8-sig·utf-8 를 차례로 시도해 텍스트를 읽는다."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
