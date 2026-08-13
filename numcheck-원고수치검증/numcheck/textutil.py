"""문장 자르기·발췌·키워드 근접 탐지 — 검사 규칙이 공유하는 텍스트 도구.

여기서 하는 일은 전부 "지적을 좁히기 위한" 것이다. 문장 경계를 잘못 잡으면
옆 문장의 "significant" 가 이 문장의 p 값에 붙어 헛된 치명이 나온다.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import List, Optional, Tuple

__all__ = [
    "normalize",
    "sentences",
    "sentence_at",
    "snippet",
    "has_keyword",
    "ONE_TAILED_HINTS",
    "CORRECTION_HINTS",
    "SIGNIFICANT_WORDS",
    "NONSIGNIFICANT_WORDS",
    "NEGATIONS",
]

# 원고에서 실제로 나오는 유니코드 변형들을 ASCII 로 접어 준다.
_FOLD = {
    "−": "-", "–": "-", "—": "-", "－": "-", "‐": "-", "‑": "-", "﹣": "-",
    "＝": "=", "％": "%", "（": "(", "）": ")", "，": ",", "．": ".",
    "：": ":", "；": ";", "／": "/", "＜": "<", "＞": ">", "±": "±",
    " ": " ", " ": " ", " ": " ", "　": " ",
    "χ": "χ", "Χ": "χ", "х": "χ",  # 키릴 x 로 쓴 카이제곱까지
    "’": "'", "‘": "'", "“": '"', "”": '"',
}


def normalize(text: str) -> str:
    """전각·특수 문자 접기. **길이를 바꾸지 않는다** — 줄 안 위치를 보존해야 한다."""
    out = []
    for ch in text:
        repl = _FOLD.get(ch)
        if repl is not None and len(repl) == 1:
            out.append(repl)
        else:
            out.append(ch)
    return "".join(out)


def fullwidth_safe(text: str) -> str:
    """NFKC 정규화(길이가 바뀔 수 있으므로 비교용으로만)."""
    return unicodedata.normalize("NFKC", text)


# 마침표로 끝나지만 문장이 끝난 것이 아닌 축약어들
_ABBREV = {
    "e.g", "i.e", "vs", "cf", "et al", "al", "approx", "fig", "figs", "tab",
    "no", "ref", "refs", "dr", "prof", "mr", "ms", "st", "ca", "etc", "sd", "se",
    "min", "max", "sec", "hr", "wk", "mo", "yr", "ml", "mg", "kg", "cm", "mm",
}

_ABBREV_TAIL = re.compile(r"([A-Za-z.]{1,8})$")


@lru_cache(maxsize=512)
def sentences(text: str) -> List[Tuple[int, int, str]]:
    """(시작, 끝, 문장) 목록. 표 행의 ``|`` 구분자도 문장 경계로 본다.

    소수점(``2.31``)과 축약어(``e.g.``)에서는 자르지 않는다.

    같은 줄에 대해 여러 검사가 반복 호출하므로 결과를 캐시한다. 축약어 판정도
    **직전 8글자만** 본다 — 예전에는 ``text[:i]`` 전체를 정규식에 넘겨 한 줄이
    길어질수록 O(N²)~O(N³) 로 폭발했고, 2만 자짜리 표 행 하나에 50초가 걸렸다.
    """
    return tuple(_sentences(text))


def _sentences(text: str) -> List[Tuple[int, int, str]]:
    if not text:
        return []
    bounds: List[int] = [0]
    for m in re.finditer(r"[.!?。！？]", text):
        i = m.start()
        # 소수점: 앞뒤가 모두 숫자
        if i > 0 and text[i - 1].isdigit() and i + 1 < len(text) and text[i + 1].isdigit():
            continue
        # 뒤에 공백(또는 문자열 끝)이 와야 문장 끝이다
        j = i + 1
        while j < len(text) and text[j] in ")]”\"'":
            j += 1
        if j < len(text) and not text[j].isspace():
            continue
        # 축약어 방어
        prefix = _ABBREV_TAIL.search(text[max(0, i - 8):i])
        if prefix and prefix.group(1).lower().strip(".") in _ABBREV:
            continue
        if j >= len(text):
            bounds.append(len(text))
            break
        bounds.append(j)
    # 표 셀 구분자
    for m in re.finditer(r"\s\|\s", text):
        bounds.append(m.end())
    bounds.append(len(text))
    bounds = sorted(set(b for b in bounds if 0 <= b <= len(text)))
    out: List[Tuple[int, int, str]] = []
    for a, b in zip(bounds, bounds[1:]):
        chunk = text[a:b]
        if chunk.strip():
            out.append((a, b, chunk))
    if not out:
        out = [(0, len(text), text)]
    return out


def sentence_at(text: str, pos: int) -> Tuple[int, int, str]:
    """``pos`` 가 속한 문장. 못 찾으면 줄 전체."""
    for a, b, chunk in sentences(text):
        if a <= pos < b:
            return a, b, chunk
    return 0, len(text), text


def snippet(text: str, start: int, end: int, radius: int = 45, limit: int = 160) -> str:
    """지적에 붙일 원문 발췌. 길이를 제한해 리포트가 원고 사본이 되지 않게 한다."""
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    piece = text[a:b].strip()
    piece = re.sub(r"\s+", " ", piece)
    if len(piece) > limit:
        piece = piece[: limit - 1].rstrip() + "…"
    if a > 0:
        piece = "…" + piece
    if b < len(text):
        piece = piece + "…"
    return piece


# ── 키워드 ───────────────────────────────────────────────────────────────────

ONE_TAILED_HINTS = (
    "one-tailed", "one tailed", "one-sided", "one sided", "단측", "일측",
)

CORRECTION_HINTS = (
    "bonferroni", "holm", "greenhouse", "geisser", "huynh", "feldt",
    "sidak", "šidák", "benjamini", "hochberg", "fdr", "tukey", "scheffe",
    "scheffé", "dunnett", "corrected", "adjusted", "보정", "교정", "다중비교",
    "welch", "웰치", "satterthwaite", "yates", "continuity correction", "연속성 보정",
    "permutation", "bootstrap", "부트스트랩", "exact test", "정확검정", "몬테카를로",
)

# 어간으로 잡는다("유의하였다"·"유의했다"·"유의함" 을 전부 나열할 수는 없다).
# 부정 표현은 NONSIGNIFICANT_WORDS 에서 **먼저** 걸러지므로 "유의하지 않"이
# 여기에 잘못 걸리는 일은 없다.
SIGNIFICANT_WORDS = (
    "significant", "significantly", "유의하", "유의한", "유의미하", "유의미한",
    "유의성이 있", "유의차", "통계적으로 유의", "유의했", "유의함", "유의성을 보",
)

NONSIGNIFICANT_WORDS = (
    "not significant", "non-significant", "nonsignificant", "no significant",
    "did not differ", "was not different", "were not different", "no difference",
    "유의하지 않", "유의미하지 않", "유의한 차이가 없", "유의한 차이는 없",
    "차이가 없었", "차이는 없었", "유의차가 없", "비유의",
)

NEGATIONS = ("not ", "no ", "non-", "n't ", "없", "아니", "않")


def has_keyword(text: str, keywords) -> Optional[str]:
    """키워드가 있으면 그 키워드를, 없으면 ``None``."""
    low = text.lower()
    for word in keywords:
        if word.lower() in low:
            return word
    return None
