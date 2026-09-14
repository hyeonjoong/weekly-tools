"""산출물 쓰기와 문자열 위생 처리.

이 모듈이 막는 것은 저장소에서 반복해서 나온 결함들이다.

* ``--out-dir`` 안에 산출물과 같은 이름의 심볼릭/하드링크를 심어 두면
  입력 파일이 조용히 덮어써진다 → 링크면 쓰지 않고 거절한다.
* 파일명에 들어간 개행/제어문자로 리포트에 가짜 ``[치명]`` 줄을 위조한다
  → 출력에 실리는 모든 외부 문자열은 :func:`sanitize` 를 통과한다.
* CSV 셀이 ``=``/``+``/``-``/``@`` 로 시작하면 엑셀이 수식으로 실행한다
  → :func:`csv_cell` 이 작은따옴표를 앞에 붙인다.
"""

import os
import stat
import unicodedata
from pathlib import Path
from typing import Optional

_C0 = {c: "�" for c in range(0x20)}
_C0.update({0x7F: "�"})
_C0_TABLE = str.maketrans({chr(k): v for k, v in _C0.items()})

#: 엑셀이 수식으로 실행해 버리는 셀 시작 문자.
_CSV_DANGER = ("=", "+", "-", "@", "\t", "\r", "\n")


class UsageError(Exception):
    """사용자 입력 문제. 한국어 한 줄 + 종료코드 2 로 끝난다."""


def sanitize(text: str, limit: int = 300) -> str:
    """외부에서 온 문자열(파일명 등)을 한 줄짜리 안전한 표시용으로 만든다."""
    if text is None:
        return ""
    flat = str(text).translate(_C0_TABLE)
    cleaned = []
    for ch in flat:
        category = unicodedata.category(ch)
        if category in ("Cf", "Cs"):
            continue                      # 보이지 않는 서식 문자는 지운다
        if category in ("Cc", "Zl", "Zp"):
            cleaned.append("\ufffd")       # NEL(U+0085)·줄바꿈류는 눈에 보이게
            continue
        cleaned.append(ch)
    flat = "".join(cleaned)
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat


def csv_cell(value) -> str:
    """CSV 수식 인젝션 차단. 위험 문자로 시작하면 ``'`` 를 앞에 붙인다."""
    raw = "" if value is None else str(value)
    text = sanitize(raw, limit=1000)
    # 원본과 정규화 결과의 **양쪽** 첫 글자를 본다.
    #   · 원본만 보면 보이지 않는 서식 문자(U+00AD, U+200E …)가 앞에 붙은
    #     `=cmd` 가 정규화 뒤 `=` 로 시작하면서 그대로 통과한다.
    #   · 정규화 결과만 보면 탭/CR 로 시작하는 셀을 놓친다.
    if raw[:1] in _CSV_DANGER or text[:1] in _CSV_DANGER:
        text = "'" + text
    return text


def _reject_link(target: Path) -> None:
    if target.is_symlink():
        raise UsageError(
            f"산출물 자리에 심볼릭 링크가 있습니다: {sanitize(target.name)}\n"
            "  링크를 따라 쓰면 엉뚱한 파일을 덮어씁니다. 링크를 지우고 다시 실행하세요."
        )
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise UsageError(
            f"산출물 자리가 일반 파일이 아닙니다: {sanitize(target.name)}\n"
            "  (폴더·FIFO·장치 파일에는 쓰지 않습니다.) 다른 --out-dir 를 지정하세요."
        )
    if st.st_nlink > 1:
        raise UsageError(
            f"산출물 자리의 파일이 하드링크입니다: {sanitize(target.name)}\n"
            "  덮어쓰면 연결된 원본까지 바뀝니다. 다른 --out-dir 를 지정하세요."
        )


def _short(path) -> str:
    """오류 메시지에 쓰는 짧은 경로. 홈 디렉터리(=사용자 이름)를 흘리지 않는다."""
    candidate = Path(str(path))
    try:
        absolute = candidate.absolute()
        home = Path.home()
        # 문자열 접두사로 비교하면 `/Users/hyeonjoong` 이 `~joong` 이 된다.
        if absolute == home or home in absolute.parents:
            return sanitize("~/" + str(absolute.relative_to(home)))
    except (OSError, ValueError):
        pass
    return sanitize(str(path))


def prepare_out_dir(path: str) -> Path:
    """``--out-dir`` 를 검증하고 만든다. 문제가 있으면 :class:`UsageError`."""
    out = Path(path)
    if out.is_symlink():
        raise UsageError(
            f"--out-dir 가 심볼릭 링크입니다: {_short(out)}\n"
            "  링크가 아닌 실제 폴더를 지정하세요."
        )
    # 마지막 조각만 보면 `링크폴더/하위` 처럼 **조상**이 링크일 때
    # 지정한 곳이 아닌 데에 조용히 쓰게 된다. 다만 macOS 의 `/tmp → private/tmp`
    # 처럼 시스템이 만든 링크와 `..` 을 실제 링크로 오해하면 정상 경로를
    # 거절하게 되므로, **사용자가 지정한 조각**만 하나씩 확인한다.
    for ancestor in list(out.parents)[:-1]:
        if ancestor.is_symlink():
            raise UsageError(
                f"--out-dir 의 상위 경로에 심볼릭 링크가 있습니다: {_short(ancestor)}\n"
                "  링크를 따라가면 지정한 곳이 아닌 폴더에 쓰게 됩니다. 실제 경로를 지정하세요."
            )
    if out.exists() and not out.is_dir():
        raise UsageError(
            f"--out-dir 자리에 폴더가 아닌 파일이 있습니다: {_short(out)}\n"
            "  다른 폴더 이름을 지정하세요."
        )
    try:
        out.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise UsageError(
            f"--out-dir 를 만들 권한이 없습니다: {_short(out)}\n"
            "  쓰기 가능한 위치를 지정하세요."
        )
    except OSError as exc:
        raise UsageError(
            f"--out-dir 를 만들 수 없습니다: {_short(out)} ({exc.strerror})"
        )
    if not os.access(out, os.W_OK):
        raise UsageError(
            f"--out-dir 에 쓸 수 없습니다(권한): {_short(out)}"
        )
    return out


def precheck_artifacts(out_dir: Path, names) -> None:
    """산출물 자리를 **판정 전에** 확인한다.

    쓰기 직전에 걸리면 이미 콘솔에 '치명 N건'을 찍어 놓고 종료코드만 2로
    바뀌어, 판정을 내리고도 '거절'이라고 말하는 모순이 생긴다.
    """
    for name in names:
        _reject_link(out_dir / name)


def write_text(out_dir: Path, name: str, text: str) -> Path:
    """산출물 한 개를 쓴다. 링크면 거절하고, 따라가지 않는다."""
    target = out_dir / name
    _reject_link(target)
    # O_TRUNC 를 쓰지 않는다. lstat 과 open 사이에 하드링크를 갈아끼우는
    # 경쟁이 실제로 성립하기 때문에(40회 중 12회 성공), **연 뒤에** fstat 으로
    # 다시 확인하고 그때 비로소 비운다.
    # O_NONBLOCK 이 없으면 산출물 자리에 놓인 FIFO 를 여는 순간 **영원히 멈춘다**.
    # 일반 파일에는 아무 영향이 없다.
    flags = (os.O_WRONLY | os.O_CREAT
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(target, flags, 0o644)
    except OSError as exc:
        raise UsageError(
            f"산출물을 쓸 수 없습니다: {sanitize(name)} ({exc.strerror})"
        )
    try:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            raise UsageError(
                f"산출물 자리의 파일이 하드링크입니다: {sanitize(name)}\n"
                "  덮어쓰면 연결된 원본까지 바뀝니다. 다른 --out-dir 를 지정하세요."
            )
        if not stat.S_ISREG(st.st_mode):
            raise UsageError(f"산출물 자리가 일반 파일이 아닙니다: {sanitize(name)}")
        os.ftruncate(fd, 0)
    except UsageError:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return target


def read_text_any(path: Path, what: str) -> str:
    """utf-8-sig → utf-8 → cp949 순으로 텍스트 파일을 읽는다."""
    # FIFO·장치 파일을 그냥 read_bytes 하면 영원히 멈추거나 메모리를 다 먹는다.
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise UsageError(f"{what} 파일이 없습니다: {sanitize(path.name)}")
    except OSError as exc:
        raise UsageError(f"{what} 파일을 읽을 수 없습니다: {sanitize(path.name)} ({exc.strerror})")
    if stat.S_ISDIR(st.st_mode):
        raise UsageError(f"{what} 자리에 파일이 아니라 폴더가 있습니다: {sanitize(path.name)}")
    if not stat.S_ISREG(st.st_mode):
        raise UsageError(f"{what} 는 일반 파일이어야 합니다: {sanitize(path.name)}")
    if st.st_size > 4 * 1024 * 1024:
        raise UsageError(f"{what} 파일이 지나치게 큽니다: {sanitize(path.name)}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UsageError(f"{what} 파일이 없습니다: {sanitize(path.name)}")
    except PermissionError:
        raise UsageError(f"{what} 파일을 읽을 권한이 없습니다: {sanitize(path.name)}")
    except OSError as exc:
        raise UsageError(f"{what} 파일을 읽을 수 없습니다: {sanitize(path.name)} ({exc.strerror})")
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UsageError(f"{what} 파일의 문자 인코딩을 알 수 없습니다: {sanitize(path.name)}")


def nfc(text: Optional[str]) -> str:
    """macOS 가 만드는 NFD 파일명을 NFC 로 정규화한다(한글 비교용)."""
    return unicodedata.normalize("NFC", text or "")
