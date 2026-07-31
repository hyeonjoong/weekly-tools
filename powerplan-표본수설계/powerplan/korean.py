"""한국어 조사(助詞) 선택 — 숫자·한글·영문 뒤에 맞는 조사를 붙인다.

프로토콜에 그대로 붙여 넣을 문장을 만드는 도구이므로 "0.208**를**", "80.0%**을**"
같은 오류가 나오면 안 된다. 조사는 앞 글자의 **종성 유무**로 정해지는데, 값이
실행할 때마다 달라지므로(0.3 vs 0.5) 문자열에 하드코딩할 수 없다.

읽는 방식:

- 숫자는 한국어 발음의 종성으로 판정한다 (0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔 → 종성 있음)
- **으로/로만은 규칙이 다르다**: 종성이 ㄹ이면 '로'를 쓴다(일**로**, 칠**로**, 팔**로**,
  0.28 → '이십팔'**로**). 종성 유무만 보면 "0.01961으로" 같은 오류가 난다.
- 한글 음절은 유니코드 자모 분해로 종성을 직접 본다
- ``%``는 '퍼센트'로 읽어 종성이 없다
- 괄호·따옴표·마침표는 건너뛰고 그 앞 글자를 본다
"""

from __future__ import annotations

__all__ = ["has_final_consonant", "has_rieul_final", "josa"]

#: 숫자를 한국어로 읽었을 때 종성이 있는지 (을/를, 이/가, 으로/로 선택용).
#: 0은 '영'(ㅇ 종성)·'공'(ㅇ 종성) 어느 쪽으로 읽어도 종성이 있다.
_DIGIT_HAS_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False,
                    "5": False, "6": True, "7": True, "8": True, "9": False}

#: 영문 끝소리를 한국어로 읽었을 때 종성이 남는 자음.
#: t/p/k/s/f 등은 '트·프·크·스·프'로 읽혀 종성이 없다(를/가를 쓴다).
_ENGLISH_FINAL = frozenset("bcdglmnr")

#: 한국어로 읽었을 때 **ㄹ 종성**으로 끝나는 숫자 (일·칠·팔). 으로/로 선택에 쓴다.
_DIGIT_RIEUL = frozenset("178")
#: 한국어로 읽었을 때 ㄹ 종성으로 끝나는 영문자 (l → '엘').
_ENGLISH_RIEUL = frozenset("l")


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


def has_rieul_final(text: str) -> bool:
    """마지막 글자의 종성이 **ㄹ**인가 (으로/로 선택용)."""
    for ch in reversed(str(text)):
        if ch.isspace() or ch in "()[]{}<>\"'`,.":
            continue
        if ch == "%":
            return False
        if ch in _DIGIT_HAS_FINAL:
            return ch in _DIGIT_RIEUL
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 == 8      # 종성 ㄹ
        if ch.isalpha():
            return ch.lower() in _ENGLISH_RIEUL
        return False
    return False


def josa(text, with_final: str, without_final: str) -> str:
    """text에 알맞은 조사를 붙여 준다.

    >>> josa("0.208", "을", "를")
    '0.208을'
    >>> josa("0.5", "으로", "로")
    '0.5로'
    >>> josa("0.01961", "으로", "로")     # 일 → ㄹ 종성이라 '로'
    '0.01961로'
    """
    text = str(text)
    final = has_final_consonant(text)
    # 으로/로는 ㄹ 종성을 종성 없음과 같이 다룬다 (서울로, 일로, 팔로)
    if final and with_final == "으로" and has_rieul_final(text):
        final = False
    return f"{text}{with_final if final else without_final}"
