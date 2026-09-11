"""한글 자모 분해·재조합.

완성형 한글 음절(U+AC00~U+D7A3)은 `0xAC00 + (초성*21 + 중성)*28 + 종성` 으로
정의된다. 이 모듈은 그 산식만으로 분해·재조합하며 외부 라이브러리를 쓰지 않는다.

분해가 안 되는 문자(자모 단독, 한자, 라틴 문자, 공백)는 조용히 무시하지 않고
`None` 을 돌려주어 호출부가 '대조불가'로 셀 수 있게 한다.
"""

from __future__ import annotations

import unicodedata
from typing import List, Optional, Tuple

SBASE = 0xAC00
LCOUNT, VCOUNT, TCOUNT = 19, 21, 28
NCOUNT = VCOUNT * TCOUNT          # 588
SCOUNT = LCOUNT * NCOUNT          # 11172

CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONGSEONG = (
    "",  # 받침 없음
    "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ",
    "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ",
    "ㅌ", "ㅍ", "ㅎ",
)

#: 종성 없음을 리포트에 적을 때 쓰는 기호 (빈 문자열은 CSV 에서 사라지므로).
NO_JONG = "∅"

JAMO_POSITIONS = ("초성", "중성", "종성")


def normalize(text: str) -> str:
    """엑셀·한글 입력기에서 섞여 들어오는 표기 차이를 하나로 모은다.

    - NFD(자모 분리형, macOS 파일명·일부 붙여넣기)를 NFC 완성형으로 합친다.
    - 전각 영숫자·전각 공백을 반각으로 편다(NFKC 가 아니라 개별 처리 — NFKC 는
      호환 자모까지 바꿔 버려 목표음소 비교를 왜곡한다).
    - 유니코드 제어문자(개행 제외 후 제거)를 없앤다. 리포트 줄 위조를 막는다.
    """
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        if ch == "　":
            out.append(" ")
            continue
        code = ord(ch)
        # 전각 영숫자·기호 → 반각
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
            continue
        out.append(ch)
    return "".join(out).strip()


def is_syllable(ch: str) -> bool:
    """완성형 한글 음절 한 글자인가."""
    return len(ch) == 1 and SBASE <= ord(ch) < SBASE + SCOUNT


def decompose(ch: str) -> Optional[Tuple[str, str, str]]:
    """완성형 음절 → (초성, 중성, 종성). 종성 없으면 빈 문자열."""
    if not is_syllable(ch):
        return None
    idx = ord(ch) - SBASE
    return (
        CHOSEONG[idx // NCOUNT],
        JUNGSEONG[(idx % NCOUNT) // TCOUNT],
        JONGSEONG[idx % TCOUNT],
    )


def compose(cho: str, jung: str, jong: str = "") -> Optional[str]:
    """(초성, 중성, 종성) → 완성형 음절. 잘못된 자모면 None."""
    # 빈 문자열은 str.index 에서 0 을 돌려주므로 길이를 먼저 본다
    # (안 그러면 compose("", "ㅏ", "") 가 조용히 "가" 가 된다).
    if len(cho) != 1 or len(jung) != 1 or len(jong) > 1:
        return None
    try:
        li = CHOSEONG.index(cho)
        vi = JUNGSEONG.index(jung)
        ti = JONGSEONG.index(jong)
    except ValueError:
        return None
    return chr(SBASE + (li * VCOUNT + vi) * TCOUNT + ti)


def syllables(text: str) -> List[str]:
    """정규화된 문자열의 글자 목록(한글 여부를 가리지 않는다)."""
    return list(normalize(text))


def hangul_syllables(text: str) -> List[str]:
    """완성형 한글 음절만 골라낸 목록."""
    return [c for c in normalize(text) if is_syllable(c)]


def jamo_at(text: str, syllable_index: int, position: str) -> Optional[str]:
    """`text` 의 `syllable_index`(1-based) 번째 **한글 음절**의 자모 하나를 꺼낸다.

    글자 위치가 아니라 **한글 음절 위치**로 센다. 전사 칸에는 괄호·물음표·공백이
    섞여 들어오는데(`(아바)`, `아 바`, `아바?`), 글자 위치로 세면 그런 칸에서
    목표음절이 한 칸씩 밀려 없는 불일치를 만들어 낸다.

    음절이 없거나 완성형 한글이 아니면 None(=대조불가).
    종성 없음은 빈 문자열이 아니라 :data:`NO_JONG` 로 돌려준다.
    """
    if position not in JAMO_POSITIONS:
        raise ValueError("position 은 초성·중성·종성 중 하나여야 합니다: %r" % (position,))
    chars = hangul_syllables(text)
    if syllable_index < 1 or len(chars) < syllable_index:
        return None
    parts = decompose(chars[syllable_index - 1])
    if parts is None:
        return None
    value = parts[JAMO_POSITIONS.index(position)]
    return value if value else NO_JONG
