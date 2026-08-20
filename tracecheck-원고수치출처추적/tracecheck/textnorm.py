"""문자 정규화 — 원고와 출력 번들 양쪽에서 똑같은 규칙을 씁니다.

원고에 `１２.４`(전각), `−3.5`(유니코드 마이너스), `1,234`(천단위 콤마)로 적힌 값과
CSV 에 `12.4`, `-3.5`, `1234` 로 적힌 값이 같은 숫자로 읽혀야 대조가 성립합니다.

정규화는 **문자 단위 사상**으로만 하고, 원문 인덱스 대응표를 함께 돌려줍니다.
리포트에 인용할 때는 정규화본이 아니라 원문 그대로를 잘라 쓰기 위해서입니다.
"""

from typing import List, Tuple

# 폭이 다른 공백들 → 보통 공백
_SPACE = {0x3000, 0x00A0, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
          0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F}
# 보이지 않는 문자 → 제거 (원고에 자주 섞여 들어와 숫자를 쪼갭니다)
_DROP = {0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD, 0x2060}
# 하이픈·대시·유니코드 마이너스 → ASCII '-'
_DASH = {0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x2796,
         0xFE58, 0xFE63, 0x30FC}


def normalize(text: str) -> Tuple[str, List[int]]:
    """(정규화된 문자열, 원문 인덱스 대응표) 를 돌려줍니다.

    대응표 `idx[i]` 는 정규화본 i 번째 문자가 원문의 몇 번째 문자에서 왔는지입니다.
    문자를 늘리지 않고(1:1 또는 삭제만) 사상하므로 항상 단조 증가합니다.
    """
    out: List[str] = []
    idx: List[int] = []
    for i, ch in enumerate(text):
        code = ord(ch)
        if code in _DROP:
            continue
        if code in _SPACE:
            ch = " "
        elif code in _DASH:
            ch = "-"
        elif 0xFF01 <= code <= 0xFF5E:      # 전각 ASCII → 반각 (．，％０-９ 포함)
            ch = chr(code - 0xFEE0)
        elif code == 0x2044:                # 분수 슬래시
            ch = "/"
        elif code == 0xFF65:                # 반각 가운뎃점
            ch = "·"
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def normalize_simple(text: str) -> str:
    """인덱스 대응표가 필요 없을 때(번들 셀 등) 쓰는 짧은 형태."""
    return normalize(text)[0]


def original_slice(text: str, idx: List[int], start: int, end: int) -> str:
    """정규화본의 [start, end) 구간에 대응하는 **원문** 조각을 돌려줍니다."""
    if start >= end or not idx:
        return ""
    start = max(0, min(start, len(idx) - 1))
    end = max(start + 1, min(end, len(idx)))
    first = idx[start]
    last = idx[end - 1]
    return text[first:last + 1]
