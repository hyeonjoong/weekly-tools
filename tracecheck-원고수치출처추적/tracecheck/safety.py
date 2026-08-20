"""입력·출력 안전장치.

이 툴은 임상 원고와 분석 산출물을 읽습니다. 그래서:

* 입력은 **읽기 전용**입니다. 어떤 경우에도 원본을 쓰지 않습니다.
* 산출물은 `--out-dir` **안에만** 만듭니다. 경로 탈출(`../`)을 거부합니다.
* 입력 파일을 산출물로 덮어쓰는 사고를 구조적으로 막습니다
  (실제로 같은 저장소의 다른 툴에서 한 번 났던 사고입니다).
* 심볼릭/하드 링크로 들어온 원고는 거부합니다 — 우리가 읽고 있다고 믿는
  파일과 실제로 열리는 파일이 다를 수 있습니다.
* CSV 수식 인젝션(`=cmd|...`)을 막습니다.
"""

import os
import re
from typing import Iterable, Optional


class InputError(Exception):
    """사용자가 고칠 수 있는 입력·인자 오류 (종료 코드 2)."""


def resolve(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def check_input_file(path: str, *, what: str = "입력 파일",
                     allow_links: bool = False) -> str:
    """원고처럼 '사람이 하나만 지정하는' 입력에 쓰는 엄격한 검사."""
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(expanded):
        raise InputError("%s 를 찾을 수 없습니다: %s" % (what, path))
    if os.path.islink(expanded):
        raise InputError(
            "%s 가 심볼릭 링크입니다: %s — 실제 파일 경로를 지정하세요." % (what, path))
    if not os.path.isfile(expanded):
        raise InputError("%s 가 일반 파일이 아닙니다: %s" % (what, path))
    stat = os.stat(expanded)
    if not allow_links and stat.st_nlink > 1:
        raise InputError(
            "%s 가 하드 링크입니다(연결 %d개): %s — 사본을 만들어 지정하세요."
            % (what, stat.st_nlink, path))
    if not os.access(expanded, os.R_OK):
        raise InputError("%s 를 읽을 권한이 없습니다: %s" % (what, path))
    return resolve(expanded)


def check_input_dir(path: str, *, what: str = "폴더") -> str:
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(expanded):
        raise InputError("%s 를 찾을 수 없습니다: %s" % (what, path))
    if not os.path.isdir(expanded):
        # 파일 하나만 준 경우도 받아 줍니다(번들이 파일 1개일 수 있음).
        return check_input_file(expanded, what=what, allow_links=True)
    if os.path.islink(expanded):
        raise InputError("%s 가 심볼릭 링크입니다: %s" % (what, path))
    return resolve(expanded)


def prepare_out_dir(out_dir: str, bundle_dirs: Iterable[str]) -> str:
    """산출물 폴더를 만들고, 입력을 덮어쓸 수 없는 위치인지 확인합니다."""
    if not out_dir or not out_dir.strip():
        raise InputError("--out-dir 가 비어 있습니다. 폴더 이름을 지정하세요.")
    literal = os.path.abspath(os.path.expanduser(out_dir))
    # `resolve()` 는 realpath 라 링크를 이미 따라가 버립니다. 링크 여부는 그 전에 봐야 합니다.
    if os.path.islink(literal):
        raise InputError("산출물 폴더가 심볼릭 링크입니다: %s" % out_dir)
    target = resolve(out_dir)
    for src in bundle_dirs:
        if target == src or target.startswith(src + os.sep):
            raise InputError(
                "산출물 폴더가 출력 번들 폴더 안입니다: %s\n"
                "  → 다음 실행 때 이 리포트가 번들의 일부로 다시 읽혀 대조가 오염됩니다."
                % out_dir)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise InputError("산출물 폴더를 만들 수 없습니다: %s (%s)"
                         % (out_dir, exc.__class__.__name__))
    if not os.path.isdir(target):
        raise InputError("산출물 폴더가 폴더가 아닙니다: %s" % out_dir)
    if not os.access(target, os.W_OK | os.X_OK):
        # 여기서 미리 막지 않으면 리포트를 다 찍은 뒤에 죽어서, 화면에는
        # "종료 코드 0" 이 남고 프로세스는 2 로 끝나는 모순이 생깁니다.
        raise InputError("산출물 폴더에 쓸 권한이 없습니다: %s" % out_dir)
    return target


def safe_out_path(out_dir: str, name: str, protected: Iterable[str]) -> str:
    """`out_dir` 안의 산출물 경로. 경로 탈출과 입력 덮어쓰기를 거부합니다."""
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        raise InputError("산출물 이름에 경로를 넣을 수 없습니다: %s" % name)
    path = os.path.join(out_dir, name)
    resolved = resolve(path)
    if resolved != os.path.join(resolve(out_dir), name):
        raise InputError("산출물 경로가 --out-dir 밖을 가리킵니다: %s" % name)
    if os.path.islink(path):
        raise InputError("산출물 자리에 심볼릭 링크가 있습니다: %s" % name)
    if os.path.exists(path) and not os.path.isfile(path):
        # 산출물 자리에 폴더가 있으면 교체 단계에서 실패해, 앞의 리포트만
        # 갈리고 뒤의 리포트는 옛것이 남는 섞인 상태가 됩니다. 미리 막습니다.
        raise InputError("산출물 자리에 일반 파일이 아닌 것이 있습니다: %s" % name)
    for src in protected:
        if resolved == src:
            raise InputError(
                "산출물이 입력 파일을 덮어쓰게 됩니다: %s — 다른 --out-dir 를 쓰세요." % name)
    return path


_INJECT_PREFIX = ("=", "+", "@", "\t", "\r", "\n", "|")
_LEADING_INVISIBLE = re.compile(
    r"^[\s\x00-\x1f\x7f-\xa0\u061c\u180e\u200b-\u200f\u202a-\u202e"
    r"\u2060-\u2064\u2066-\u206f\ufeff]+")


def csv_safe(value: object) -> str:
    """CSV 수식 인젝션 가드.

    엑셀/넘버스는 `=`, `+`, `-`, `@` 로 시작하는 셀을 수식으로 해석합니다.
    다만 `-3.47` 같은 **숫자로 읽히는 값은 수식이 아니라 숫자**이므로 그대로 둡니다
    (여기에 따옴표를 붙이면 대조표에서 정렬·계산이 전부 깨집니다).
    숫자로 읽히지 않는데 위 문자로 시작하면 `'` 를 붙여 무력화합니다.
    """
    text = "" if value is None else str(value)
    # 앞에 붙은 보이지 않는 문자로 가드를 피해 가지 못하게, 문자 종류로 벗깁니다.
    # (`\u200e=cmd|…` 가 실제로 통과했습니다 — 하드코딩한 목록은 항상 뚫립니다.)
    stripped = _LEADING_INVISIBLE.sub("", text)
    if not stripped:
        return text
    text = stripped
    if text[0] in _INJECT_PREFIX or text[0] == "-":
        if _looks_numeric(text):
            return text
        return "'" + text
    return text


def _looks_numeric(text: str) -> bool:
    body = text.strip()
    if not body:
        return False
    if body[0] in "+-":
        body = body[1:]
    if body.endswith("%"):
        body = body[:-1]
    if not body:
        return False
    try:
        float(body.replace(",", ""))
    except ValueError:
        return False
    return True


def redact(path: Optional[str]) -> str:
    """오류 메시지에 쓸 파일 표기 — 경로 전체 대신 파일명만 노출합니다.

    절대 경로에는 사용자 이름·과제 이름·피험자 코드가 들어 있기 쉽습니다.
    """
    if not path:
        return "(이름 없음)"
    return os.path.basename(path.rstrip(os.sep)) or path
