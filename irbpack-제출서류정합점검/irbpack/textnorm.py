"""정규화 규칙 — 전부 리포트 부록에 인쇄됩니다. 조용히 같다고 하지 않습니다.

규칙 이름(`RULES` 의 키)이 그대로 커버리지 블록의 "정규화 적용" 줄과 `정합점검.md`
부록에 나갑니다. 새 규칙을 추가하면 반드시 여기에 설명을 함께 적으십시오.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

#: 규칙 이름 → 사람이 읽는 설명.
RULES = {
    "유니코드 NFC": "NFD 로 저장된 한글(macOS 파일명 등)을 NFC 로 합칩니다.",
    "전각→반각": "전각 숫자·영문·괄호·콜론(１２３ＡＢ（）：)을 반각으로 바꿉니다.",
    "물결 통일": "범위 기호 `~` `∼` `〜` `-` `–` `—` `부터…까지` 를 모두 `~` 로 봅니다 (만 5~12세 ≡ 만 5-12세).",
    "공백·문장부호 제거": "제목·이름 비교에서 공백과 문장부호를 지우고 대소문자를 무시합니다.",
    "금액 통일": "`10,000원`·`1만원`·`1만 원`·`만원` 을 모두 원 단위 정수(10000)로 봅니다.",
    "횟수 통일": "`2회`·`두 번`·`2번`·`2차례`·`2 회기` 를 같은 횟수로 봅니다.",
    "시간→분": "`1시간 30분`·`90분`·`1.5시간` 을 분 단위 정수(90)로 봅니다.",
    "날짜 통일": "`260518`·`2026.05.18`·`2026-05-18`·`2026년 5월 18일` 을 ISO 날짜로 봅니다.",
    "버전 통일": "`Version No: 1.0`·`v1.0`·`V1.0`·`ver 1.0`·`1.0판` 을 `1.0` 으로 봅니다.",
    "기간 통일": "`3년`·`3 년간`·`삼년`·`36개월` 을 개월 단위 정수로 봅니다.",
}

_FULL2HALF = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}
_FULL2HALF["　"] = " "
_TILDES = "~∼〜～–—―‐−-"
_KNUM = {"영": 0, "한": 1, "일": 1, "두": 2, "이": 2, "세": 3, "삼": 3, "네": 4, "사": 4,
         "다섯": 5, "오": 5, "여섯": 6, "육": 6, "일곱": 7, "칠": 7, "여덟": 8, "팔": 8,
         "아홉": 9, "구": 9, "열": 10, "십": 10}


def nfc(text: str) -> str:
    """유니코드 NFC 정규화 (규칙: 유니코드 NFC)."""
    return unicodedata.normalize("NFC", text or "")


def halfwidth(text: str) -> str:
    """전각 → 반각 (규칙: 전각→반각)."""
    return "".join(_FULL2HALF.get(ch, ch) for ch in text or "")


def basic(text: str) -> str:
    """모든 문서 텍스트에 공통으로 먼저 적용되는 정규화."""
    t = halfwidth(nfc(text))
    t = t.replace(" ", " ").replace("​", "")
    return t


def tilde(text: str) -> str:
    """범위 기호 통일 (규칙: 물결 통일)."""
    out = text
    for ch in _TILDES:
        out = out.replace(ch, "~")
    out = re.sub(r"\s*~\s*", "~", out)
    return out


def squash(text: str) -> str:
    """공백·문장부호 제거 + 소문자 (규칙: 공백·문장부호 제거)."""
    t = basic(text).lower()
    t = re.sub(r"[\s　·•\-–—_,.:;'\"“”‘’()\[\]{}<>《》「」『』〈〉/\\|!?]+", "", t)
    return t


_KUNIT = {"천": 1000, "만": 10000, "억": 100000000}


def money(text: str) -> Optional[int]:
    """금액 문자열 → 원 단위 정수 (규칙: 금액 통일). `10,000원`·`1만원`·`3만5천원`·`1억 2천만원`. 못 읽으면 None."""
    t = basic(text).replace(" ", "")
    m = re.search(r"((?:[0-9]{1,3}(?:,[0-9]{3}){0,4}|[0-9]{1,12})(?:\.[0-9]{1,2})?(?:천|만|억){0,2}(?:[0-9]{1,4}(?:천|만|억){1,2})*)원", t)
    if not m:
        if re.search(r"(?<![0-9])만원", t):
            return 10000
        return None
    body = m.group(1)
    total = 0.0
    for num, units in re.findall(r"([0-9][0-9,]*(?:\.[0-9]+)?)((?:천|만|억)*)", body):
        val = float(num.replace(",", ""))
        for u in units:
            val *= _KUNIT[u]
        total += val
    total = int(round(total))
    return total if total > 0 else None


def count(text: str) -> Optional[int]:
    """`2회`·`두 번`·`2차례` → 2 (규칙: 횟수 통일)."""
    t = basic(text).replace(" ", "")
    m = re.search(r"([0-9]{1,3})(?:회기|회|번|차례|차)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)(?:번|차례|회)", t)
    if m:
        return _KNUM[m.group(1)]
    return None


def minutes(text: str) -> Optional[int]:
    """`1시간 30분`·`90분`·`1.5시간` → 분 (규칙: 시간→분)."""
    t = basic(text).replace(" ", "")
    h = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)시간", t)
    mn = re.search(r"([0-9]{1,4})분", t)
    if not h and not mn:
        half = re.search(r"(한|두|세|네)시간(반)?", t)
        if half:
            val = _KNUM[half.group(1)] * 60
            if half.group(2):
                val += 30
            return val
        return None
    total = 0
    if h:
        total += int(round(float(h.group(1)) * 60))
        if re.search(r"시간반", t):
            total += 30
    if mn:
        total += int(mn.group(1))
    return total


def months(text: str) -> Optional[int]:
    """`3년`·`36개월`·`삼년`·`1년 6개월` → 개월 (규칙: 기간 통일)."""
    t = basic(text).replace(" ", "")
    total = 0
    m = re.search(r"([0-9]{1,3})년", t)
    if m:
        total += int(m.group(1)) * 12
    else:
        m = re.search(r"(일|이|삼|사|오|육|칠|팔|구|십)년", t)
        if m:
            total += _KNUM[m.group(1)] * 12
    m = re.search(r"([0-9]{1,4})개월", t)
    if m:
        total += int(m.group(1))
    return total or None


_DATE_PATTERNS = [
    re.compile(r"(?<![0-9])(20[0-9]{2})[.\-/년]\s*([0-9]{1,2})[.\-/월]\s*([0-9]{1,2})일?(?![0-9])"),
    re.compile(r"(?<![0-9])(20[0-9]{2})([01][0-9])([0-3][0-9])(?![0-9])"),
    re.compile(r"(?<![0-9])(2[0-9])([01][0-9])([0-3][0-9])(?![0-9])"),
]


def date(text: str) -> Optional[str]:
    """`260518`·`2026.05.18`·`2026년 5월 18일` → `2026-05-18` (규칙: 날짜 통일)."""
    t = basic(text)
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(t)
        if not m:
            continue
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if i == 2:
            y = "20" + y
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return "{}-{:02d}-{:02d}".format(y, mo, d)
    return None


def find_dates(text: str) -> List[str]:
    """문자열에 있는 날짜 전부 (순서 보존, 중복 제거)."""
    return [d for d, _ in find_dates_with_text(text)]


def find_dates_with_text(text: str) -> List[Tuple[str, str]]:
    """[(ISO 날짜, 원문에서 매치된 토큰)]."""
    t = basic(text)
    found: List[Tuple[str, str]] = []
    for i, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(t):
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            if i == 2:
                y = "20" + y
            if 1 <= mo <= 12 and 1 <= d <= 31:
                s = "{}-{:02d}-{:02d}".format(y, mo, d)
                if s not in [f for f, _ in found]:
                    found.append((s, m.group(0)))
    return found


_VER = re.compile(
    r"(?:version\s*(?:no\.?|number)?|ver\.?|v|버전|판번호|개정번호)\s*[:：.]?\s*([0-9]{1,4}(?:\.[0-9]{1,4}){0,3})(?![0-9]|\.[0-9])",
    re.IGNORECASE)
_VER_KO = re.compile(r"(?<![0-9.])([0-9]{1,4}(?:\.[0-9]{1,4}){1,3})\s*판(?![0-9])")


def version(text: str) -> Optional[str]:
    """`Version No: 1.0`·`v1.2`·`1.0판` → `1.0` (규칙: 버전 통일)."""
    got = version_match(text)
    return got[0] if got else None


def version_match(text: str) -> Optional[Tuple[str, str]]:
    """(버전, 원문에서 매치된 토큰)."""
    t = basic(text)
    for m in _VER.finditer(t):
        ver = canon_version(m.group(1))
        if ver is None:
            continue  # `Version 2026.05.18` 같은 날짜형은 버전이 아니다
        token = re.sub(r"^(?:version\s*(?:no\.?|number)?|ver\.?|버전|판번호|개정번호)\s*[:：.]?\s*", "", m.group(0).strip(), flags=re.IGNORECASE)
        return ver, token or m.group(1)
    m = _VER_KO.search(t)
    if m:
        ver = canon_version(m.group(1))
        if ver:
            return ver, m.group(0).strip()
    return None


def canon_version(ver: str) -> Optional[str]:
    """`2.0`≡`2`≡`2.0.0` → `2`, `1.2`→`1.2`. 첫 자리가 1900 이상이면 날짜형이라 None."""
    parts = ver.split(".")
    try:
        if int(parts[0]) >= 1900:
            return None
    except ValueError:
        return None
    while len(parts) > 1 and re.fullmatch(r"0+", parts[-1]):
        parts.pop()
    return ".".join(str(int(x)) if x.isdigit() else x for x in parts)


def age_ranges(text: str) -> List[Tuple[int, int]]:
    """`만 5~12세`·`만 19세 이상 45세 이하`·`5-12세` → [(5, 12)]."""
    return [pair for pair, _ in age_ranges_with_text(text)]


def age_ranges_with_text(text: str) -> List[Tuple[Tuple[int, int], str]]:
    """[((하한, 상한), 원문 토큰)]. 원문 토큰은 정규화 전 표기 그대로 (정규화 쌍 계수용).
    `45세 미만` 은 상한 44 로 봅니다 — `45세 이하` 와 같지 않습니다."""
    raw = basic(text)
    t = tilde(raw)
    out: List[Tuple[Tuple[int, int], str]] = []
    seen: List[Tuple[int, int]] = []

    def push(lo: str, hi: str, exclusive: bool, m: "re.Match") -> None:
        pair = (int(lo), int(hi) - (1 if exclusive else 0))
        if pair not in seen and pair[0] <= pair[1]:
            seen.append(pair)
            out.append((pair, _original_span(raw, t, m)))

    for m in re.finditer(r"만?\s*([0-9]{1,3})\s*(?:세)?\s*~\s*만?\s*([0-9]{1,3})\s*세(\s*미만)?", t):
        push(m.group(1), m.group(2), bool(m.group(3)), m)
    for m in re.finditer(r"만?\s*([0-9]{1,3})\s*세\s*(?:이상|부터|에서)\s*(?:,?|부터)?\s*만?\s*([0-9]{1,3})\s*세\s*(이하|미만|까지|사이)", t):
        push(m.group(1), m.group(2), m.group(3) == "미만", m)
    return out


def _original_span(raw: str, normalized: str, m: "re.Match") -> str:
    """tilde() 는 길이를 바꿀 수 있으므로(공백 제거) 원문 토큰은 근사로 되찾습니다."""
    if len(raw) == len(normalized):
        return raw[m.start():m.end()]
    return m.group(0)


def fmt_age(pair: Tuple[int, int]) -> str:
    return "만 {}~{}세".format(pair[0], pair[1])
