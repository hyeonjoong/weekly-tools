"""한국어 조사(助詞) 선택 — 숫자·한글·영문 뒤에 맞는 조사를 붙인다.

프로토콜에 그대로 붙여 넣을 문장을 만드는 도구이므로 "0.208**를**", "80.0%**을**"
같은 오류가 나오면 안 된다. 조사는 앞 글자의 **종성 유무**로 정해지는데, 값이
실행할 때마다 달라지므로(0.3 vs 0.5) 문자열에 하드코딩할 수 없다.

읽는 방식:

- 숫자는 한국어 발음의 종성으로 판정한다 (0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔 → 종성 있음)
- 한글 음절은 유니코드 자모 분해로 종성을 직접 본다
- ``%``는 '퍼센트'로 읽어 종성이 없다
- 괄호·따옴표·마침표는 건너뛰고 그 앞 글자를 본다
"""

from __future__ import annotations

__all__ = ["has_final_consonant", "josa"]

#: 숫자를 한국어로 읽었을 때 종성이 있는지 (을/를, 이/가, 으로/로 선택용).
#: 0은 '영'(ㅇ 종성)·'공'(ㅇ 종성) 어느 쪽으로 읽어도 종성이 있다.
_DIGIT_HAS_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False,
                    "5": False, "6": True, "7": True, "8": True, "9": False}

#: 영문 끝소리를 한국어로 읽었을 때 종성이 남는 자음.
#: t/p/k/s/f 등은 '트·프·크·스·프'로 읽혀 종성이 없다(를/가를 쓴다).
_ENGLISH_FINAL = frozenset("bcdglmnr")


def has_final_consonant(text: str) -> bool:
    """마지막 글자에 종성이 있는가 (조사 선택용)."""
    for ch in reversed(str(text)):
        if ch.isspace() or ch in "()[]{}<>\"'`,.":
            continue
        if ch == "%":
            return False                      # '퍼센트' → 종성 없음
        if ch in _DIGIT_HAS_FINAL:
            return _DIGIT_HAS_FINAL[ch]
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:          # 한글 음절
            return (code - 0xAC00) % 28 != 0
        if ch.isalpha():                      # 영문은 한국어로 읽었을 때의 종성으로
            return ch.lower() in _ENGLISH_FINAL
        return False
    return False


def josa(text, with_final: str, without_final: str) -> str:
    """text에 알맞은 조사를 붙여 준다.

    >>> josa("0.208", "을", "를")
    '0.208을'
    >>> josa("0.5", "으로", "로")
    '0.5로'
    """
    text = str(text)
    return f"{text}{with_final if has_final_consonant(text) else without_final}"
