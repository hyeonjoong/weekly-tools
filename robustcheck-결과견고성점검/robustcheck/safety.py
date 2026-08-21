"""안전 장치 — 원본 보호 · 경로 순회 차단 · CSV 수식 인젝션 방어.

이 툴은 임상 데이터를 다룬다. 세 가지를 코드로 막는다:

1. **원본은 절대 건드리지 않는다.** 산출물 경로가 입력 파일과 같은 실체
   (심볼릭 링크·하드링크·대소문자 무시 파일시스템 포함)를 가리키면 쓰기 전에
   멈춘다.
2. **`--out-dir` 밖으로는 한 글자도 쓰지 않는다.** 파일명을 조합한 결과가
   해석 후 out-dir 안에 없으면 거부한다.
3. **CSV 수식 인젝션.** `= + - @` 로 시작하는 셀은 Excel 이 수식으로 읽는다.
   단, `-0.71` 같은 **정상적인 음수까지 문자열로 만들면 표가 못 쓰게 되므로**
   숫자로 파싱되는 셀은 그대로 둔다.
"""

import os
import re
import unicodedata
from typing import IO, Iterable, List

__all__ = [
    "OutputPathError",
    "csv_safe",
    "prepare_out_dir",
    "safe_join",
    "assert_not_input",
    "open_for_write",
]

# 앞에 작은따옴표를 붙여 무력화할 선두 문자. 탭·캐리지리턴도 포함한다
# (Excel 은 선행 공백/탭 뒤의 '=' 도 수식으로 읽는다).
_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "|", "%", "\\",
                       "＝", "＋", "－", "＠", "−")

def _strip_invisible(text: str) -> str:
    """선두의 공백·제어문자·서식문자(Cf)를 전부 벗겨 낸다.

    목록을 손으로 나열하면 반드시 빠뜨린다 — 실제로 U+200E/200F(LRM/RLM),
    U+2066–2069(bidi isolate), U+180E, ESC 가 그대로 새어 나갔다. 그래서
    유니코드 카테고리로 판정한다.
    """
    index = 0
    while index < len(text):
        ch = text[index]
        if ch.isspace() or unicodedata.category(ch) in ("Cf", "Cc"):
            index += 1
            continue
        break
    return text[index:]


# 순수 숫자 리터럴(정수·소수·지수·부호). 이건 인젝션이 아니다.
_NUMERIC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class OutputPathError(Exception):
    """산출물을 안전하게 쓸 수 없을 때."""


def csv_safe(cell: object, numeric_ok: bool = True) -> str:
    """CSV 한 칸을 수식 인젝션으로부터 안전하게 만든다.

    `numeric_ok=False` 는 **식별자 칸**(subject_id 등)에 쓴다. 숫자로 보이는
    ID(`007`, `+1e5`, `-0071`)를 그대로 두면 Excel 이 `7`, `100000`, `-71` 로
    바꿔 버려 리포트와 원본 표의 피험자가 어긋난다 — 수식 실행보다 조용하고
    임상 자료에서는 더 나쁜 사고다.
    """
    text = "" if cell is None else str(cell)
    if not text:
        return text
    # 앞쪽의 공백·제어문자·서식문자를 벗겨 낸 뒤 판정한다. 벗기기 전 문자열로
    # 판정하면 " =1+1" 이나 제로폭 문자를 앞세운 payload 가 그대로 빠져나간다.
    head = _strip_invisible(text)
    if numeric_ok and _NUMERIC.match(head) and not text[:1] in ("\t", "\r", "\n"):
        return text
    if head[:1] in _INJECTION_PREFIXES or text[:1] in _INJECTION_PREFIXES:
        return "'" + text
    if not numeric_ok and _NUMERIC.match(head):
        return "'" + text
    return text


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def prepare_out_dir(out_dir: str) -> str:
    """출력 폴더를 만들고 절대경로를 돌려준다."""
    if not out_dir or "\x00" in out_dir:
        raise OutputPathError("출력 폴더 경로가 비었거나 널 바이트를 포함합니다.")
    resolved = os.path.abspath(os.path.expanduser(out_dir))
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise OutputPathError("출력 경로가 폴더가 아닙니다: %s" % resolved)
    os.makedirs(resolved, exist_ok=True)
    return resolved


def safe_join(out_dir: str, name: str) -> str:
    """out-dir 안의 파일 경로. 밖으로 나가면 거부한다.

    **`realpath` 로 검사한다.** 어휘적 검사(`abspath`)만 하면 out-dir 안에
    미리 심어 둔 심볼릭 링크(`out/시나리오표.csv → /어딘가/중요파일`)를 타고
    폴더 밖으로 환자 ID 를 써 버린다. 실제로 재현됐던 취약점이다.
    """
    if not name or "\x00" in name:
        raise OutputPathError("잘못된 파일명입니다: %r" % name)
    if os.path.isabs(name) or os.path.splitdrive(name)[0]:
        raise OutputPathError("파일명에 절대경로를 쓸 수 없습니다: %r" % name)
    candidate = os.path.abspath(os.path.join(out_dir, name))
    root = os.path.abspath(out_dir)
    root_real = _real(root)
    candidate_real = _real(candidate)
    inside = (candidate_real == root_real
              or candidate_real.startswith(root_real + os.sep))
    lexically_inside = (
        os.path.normcase(candidate) == os.path.normcase(root)
        or os.path.normcase(candidate).startswith(os.path.normcase(root) + os.sep))
    if not (inside and lexically_inside):
        raise OutputPathError(
            "출력 폴더를 벗어나는 경로입니다: %r (해석 결과 %s)"
            % (name, candidate_real))
    return candidate


def open_for_write(path: str) -> IO[str]:
    """산출물 쓰기 전용 열기. **심볼릭 링크를 따라가지 않는다.**

    `open(path, "w")` 는 링크를 그대로 따라가므로 `safe_join` 의 검사를
    통과한 뒤에도(경쟁 조건) 엉뚱한 파일을 덮어쓸 수 있다.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise OutputPathError(
            "산출물을 열 수 없습니다: %s (%s). 심볼릭 링크이거나 권한이 없습니다."
            % (path, exc.strerror or exc))
    return os.fdopen(fd, "w", encoding="utf-8-sig", newline="")


def _same_file(a: str, b: str) -> bool:
    """두 경로가 같은 실체를 가리키는가 (하드링크·대소문자 포함)."""
    if _real(a) == _real(b):
        return True
    try:
        sa = os.stat(a)
        sb = os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def assert_not_input(targets: Iterable[str], inputs: Iterable[str]) -> None:
    """산출물이 입력 파일을 덮어쓰려 하면 쓰기 전에 멈춘다."""
    existing: List[str] = [p for p in inputs if p and os.path.exists(p)]
    for target in targets:
        for source in existing:
            if _same_file(target, source):
                raise OutputPathError(
                    "산출물이 입력 파일을 덮어쓰려 합니다: %s → %s. "
                    "다른 --out-dir 를 지정하세요." % (target, source)
                )
