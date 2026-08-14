"""비교용 정규화 — revcheck 의 모든 판정은 이 파일의 결과 위에서 이루어진다.

이 툴은 **의미 기반 매칭을 하지 않는다.** "비슷한 문장"을 찾아 주지 않는다.
그래서 정규화가 곧 정확도다. 워드가 곧은 따옴표를 굽은 따옴표로 바꾸고,
하이픈을 en-dash 로 바꾸고, 같은 문장을 ``<w:r>`` 여러 개로 쪼개 저장하는 바람에
멀쩡한 응답서에서 치명이 열 건 뜨면 이 툴은 두 번 다시 열리지 않는다.

정규화 단계 (양쪽 문자열에 **똑같이** 적용된다)
    1. 유니코드 NFKC (전각 → 반각, ％ → %, ﬁ → fi)
    2. 굽은 따옴표·대시·생략부호·특수 공백을 대표 문자로 통일
    3. 서식 마크업 제거 (마크다운 ``**``/``*``/`` ` ``, LaTeX ``\\textit{}``)
    4. 제어문자·zero-width 제거
    5. 연속 공백 1칸, 앞뒤 공백 제거
    6. 대소문자 무시 (casefold)

6번은 존재 여부를 보는 검사이기 때문이다. 워드/저자가 문장 첫 글자의 대소문자를
바꾸는 일은 흔하고, 그것 때문에 "개정본에 없습니다"라고 외치면 오탐이다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

__all__ = [
    "canonical",
    "norm_compare",
    "norm_display",
    "numbers_in",
    "numbers_in_prose",
    "strip_control",
]

# ── 문자 통일 표 ────────────────────────────────────────────────────────────
_CHAR_MAP = {
    # 따옴표
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2032": "'", "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
    "\u300c": '"', "\u300d": '"', "\u300e": '"', "\u300f": '"',
    "\uff02": '"', "\uff07": "'", "\u0060": "'", "\u00b4": "'",
    # 대시·하이픈 (마이너스·en·em·figure dash 모두 하이픈으로)
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\uff0d": "-",
    "\u2043": "-", "\u00ad": "",  # soft hyphen 은 지운다
    # 생략부호
    "\u2026": "...", "\u22ef": "...",
    # 공백류
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ",
    "\u2002": " ", "\u2003": " ", "\u3000": " ", "\t": " ",
}

_TRANSLATION = {ord(k): v for k, v in _CHAR_MAP.items()}

# 화면·CSV 를 망가뜨리는 제어문자와 보이지 않는 서식 문자.
# 원고 안의 ESC 시퀀스가 터미널을 지우거나 제목을 바꾸는 사고를 막는다.
# U+2066–U+2069(LRI/RLI/FSI/PDI)는 Trojan Source 가 실제로 쓰는 문자다 —
# 이걸 남겨 두면 원고가 **리포트에 찍히는 숫자의 순서를 눈속임할 수** 있다.
_CONTROL = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u202a-\u202e"
    "\u2060-\u2064\u2066-\u2069\ufeff]"
)

# ── 서식 마크업 ─────────────────────────────────────────────────────────────
_TEX_WRAPPERS = re.compile(
    r"\\(?:textit|textbf|emph|text|textrm|texttt|underline|uline|mbox)\s*\{([^{}]*)\}"
)
_TEX_ESCAPES = {
    r"\%": "%", r"\&": "&", r"\_": "_", r"\$": "$", r"\#": "#",
    r"\{": "{", r"\}": "}", r"\,": " ", r"\;": " ", r"\ ": " ", r"\\": " ",
}
_MD_UNDERSCORE = re.compile(r"(?<![0-9A-Za-z_])_{1,2}([^_\n]+?)_{1,2}(?![0-9A-Za-z_])")
_MD_LEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)")

# 숫자 토큰 — 정수·소수·천단위 콤마·백분율·p 값(선행 점 포함)을 모두 잡는다.
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.\d+|\d+")


def strip_control(text: str) -> str:
    """출력에 들어갈 원고 유래 문자열에서 제어문자를 지운다."""
    return _CONTROL.sub("", text or "")


def _strip_markup(text: str) -> str:
    out = text
    # LaTeX 은 중첩이 있을 수 있으므로 몇 번 반복해 벗긴다.
    for _ in range(3):
        new = _TEX_WRAPPERS.sub(r"\1", out)
        if new == out:
            break
        out = new
    for src, dst in _TEX_ESCAPES.items():
        out = out.replace(src, dst)
    out = out.replace("`", "")
    out = out.replace("*", "")
    out = _MD_UNDERSCORE.sub(r"\1", out)
    return out


def canonical(text: str) -> str:
    """문자만 통일하고 **대소문자와 서식은 그대로** 둔다 (화면 출력용).

    사람이 읽을 인용문은 원문 그대로 보여 줘야 하므로, 눈에 보이지 않는
    특수 공백·제어문자만 정리한다.
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_TRANSLATION)
    out = strip_control(out)
    return re.sub(r"\s+", " ", out).strip()


def norm_display(text: str, limit: int = 160) -> str:
    """화면에 한 줄로 넣을 수 있게 자른다(가운데 생략)."""
    clean = canonical(text)
    if len(clean) <= limit:
        return clean
    head = clean[: limit - 30].rstrip()
    tail = clean[-25:].lstrip()
    return f"{head} … {tail}"


def norm_compare(text: str) -> str:
    """**판정에 쓰는** 정규화 문자열. 양쪽에 똑같이 적용한다."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_TRANSLATION)
    out = strip_control(out)
    out = _MD_LEADING.sub("", out)
    out = _strip_markup(out)
    out = re.sub(r"\s+", " ", out).strip()
    return out.casefold()


def numbers_in(text: str) -> List[str]:
    """문자열에서 숫자 토큰을 순서대로 뽑는다(천단위 콤마 제거, 소수점 유지).

    ``"mean ISI decreased by 5.2 (SD 3.1)"`` → ``["5.2", "3.1"]``
    ``"1,240 (45.2%)"`` → ``["1240", "45.2"]``
    """
    if not text:
        return []
    found = []
    for raw in _NUMBER.findall(unicodedata.normalize("NFKC", text)):
        token = raw.replace(",", "")
        # ".05" 와 "0.05" 는 같은 값이다 — p 값 표기 차이로 오탐이 나면 안 된다.
        if token.startswith("."):
            token = "0" + token
        if "." in token:
            token = token.rstrip("0").rstrip(".") or "0"
        else:
            token = str(int(token)) if token.isdigit() else token
        found.append(token)
    return found


# ── 인용 번호·그림표 라벨 (= 데이터가 아닌 숫자) ─────────────────────────────
# 참고문헌을 한 편 넣으면 [5] 이후의 인용 번호가 **전부** 밀린다. 그걸 "숫자가
# 조용히 바뀌었다"고 지적하면, 가장 흔한 정상 리비전에서 치명이 열 건씩 뜬다.
_CITATION_BRACKET = re.compile(r"\[[\d,;\s\u2013\u2014-]+\]")
# 저자-연도 인용 ``(Smith & Jones, 2018; Cho et al., 2022)`` — 괄호 안에 저자 이름과
# 연도가 함께 있으면 인용이다. ``(95% CI 1.6 to 4.6)`` 에는 연도가 없으므로 남는다.
_PAREN_CITATION = re.compile(
    r"\([^()]*[A-Za-z]{2,}[^()]*\b(?:19|20)\d{2}[a-z]?\b[^()]*\)"
)
_SUPERSCRIPT = re.compile(r"[\u00b2\u00b3\u00b9\u2070-\u2079]+")
_LABEL_NUMBER = re.compile(
    r"\b(?:supplementary\s+)?(?:table|tbl|figure|fig|section|appendix|eq|equation)"
    r"\.?\s*\d{1,3}\b|(?:표|그림|부록|식)\s*\d{1,3}",
    re.IGNORECASE,
)


def _bracket_repl(match: "re.Match") -> str:
    """대괄호 안 숫자가 **인용 번호**일 때만 지운다.

    ``adults [5]`` 는 인용이지만 ``22 [14-31]`` 은 중앙값[사분위범위]다. 판별은
    괄호 **앞 글자**로 한다 — 숫자나 ``%``·``)`` 뒤에 오는 대괄호는 값의 범위다.
    """
    text = match.string
    head = text[: match.start()].rstrip()
    if head and (head[-1].isdigit() or head[-1] in "%)"):
        return match.group(0)  # 중앙값[IQR]·n[%] — 데이터 숫자다
    return " "


def numbers_in_prose(text: str) -> List[str]:
    """**데이터 숫자만** 뽑는다 — 인용 번호 ``[5]`` 와 ``Table 2`` 는 뺀다.

    리비전에서 인용 번호가 밀리는 것과 표 번호가 늘어나는 것은 정상이다.
    값비싼 것은 평균·표준편차·N·p 값이 조용히 바뀌는 쪽이다.
    """
    if not text:
        return []
    cleaned = _CITATION_BRACKET.sub(_bracket_repl, text)
    cleaned = _PAREN_CITATION.sub(" ", cleaned)
    cleaned = _SUPERSCRIPT.sub(" ", cleaned)
    cleaned = _LABEL_NUMBER.sub(" ", cleaned)
    return numbers_in(cleaned)
