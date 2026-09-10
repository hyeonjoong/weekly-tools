"""표기 정규화 — 같은 값을 다르게 적은 것만 같다고 보고, 다른 값은 절대 같다고 하지 않는다.

정규화 규칙은 전부 이 모듈에 모여 있고, 리포트 부록에 그대로 인쇄된다
(조용히 "같다"고 판정하지 않기 위해서다). 규칙을 추가하면 `RULES` 에도 추가할 것.
"""

import re
import unicodedata

# 리포트 부록에 그대로 인쇄되는 규칙 목록. (규칙 id, 사람이 읽는 설명)
RULES = [
    ("nfc", "한글 NFD(자모 분리) → NFC 로 합침 — 자모로 분해된 '한' 과 완성형 '한' 을 같게 본다"),
    ("width", "전각 영문·숫자·괄호·콜론 → 반각 (Ａ→A, １→1, （→( )"),
    ("tilde", "물결·대시 표기 통일 (~ ∼ 〜 ～ – — ‐ ― − → ~)"),
    ("space", "연속 공백·탭·줄바꿈 → 공백 하나, 앞뒤 공백 제거"),
    ("quote", "따옴표 통일 (곡선 따옴표·낫표 → 곧은 따옴표)"),
    ("amount", "금액: 1,000 단위 쉼표 제거 · '1만원' ≡ '10,000원' · 원 단위로 통일"),
    ("count", "횟수: '두 번' ≡ '2 회' ≡ '2회' → 2회 (한~열 까지 한글 수사 지원)"),
    ("date", "날짜: 260518 ≡ 2026-05-18 ≡ 2026.05.18 ≡ 2026년 5월 18일 (YY<70 이면 20YY)"),
    ("version", "버전: 'Version No: V1.0' ≡ 'v1.0' ≡ '버전 1' → 1.0 (뒤의 .0 을 채워 비교)"),
    ("age", "연령: '만 5~12세' ≡ '만 5-12세' ≡ '5∼12세' → 5-12세, '만 19세 이상' → 19+세"),
    ("time", "시간: '1시간' ≡ '60분', '1~3시간' → 60-180분 (전부 분 단위 범위로 통일)"),
]

_TILDES = "∼〜～–—‐―−~"
_QUOTES = {
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "「": '"', "」": '"', "『": '"', "』": '"',
}
_KOR_NUM = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}

# 제어문자·양방향 제어·제로폭 문자는 리포트에 가짜 줄을 심을 수 있으므로 전부 지운다.
_CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩﻿]"
)


def strip_control(text):
    """제어문자·양방향 제어·개행을 없앤다(리포트 위조 방지)."""
    if text is None:
        return ""
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return _CONTROL.sub("", text)


def _halfwidth(text):
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:      # 전각 ASCII
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:               # 전각 공백
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def canon(text):
    """비교·표시 공통 정규화: NFC · 반각 · 물결 통일 · 공백 압축."""
    text = strip_control(text)
    text = unicodedata.normalize("NFC", text)
    text = _halfwidth(text)
    for src, dst in _QUOTES.items():
        text = text.replace(src, dst)
    for ch in _TILDES:
        text = text.replace(ch, "~")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact(text):
    """공백을 완전히 없앤 비교용 키 (표기 흔들림 흡수)."""
    return re.sub(r"\s+", "", canon(text))


# ---------------------------------------------------------------- 값 단위 정규화

def norm_amount(number_text, unit_man=False):
    """'10,000' → 10000, '1' + 만 단위 → 10000. 실패하면 None."""
    digits = re.sub(r"[,\s]", "", number_text or "")
    if not digits.isdigit():
        return None
    value = int(digits)
    if unit_man:
        value *= 10000
    return value


def norm_date(text):
    """여러 날짜 표기를 ISO(YYYY-MM-DD)로. 날짜가 아니면 None."""
    if text is None:
        return None
    text = canon(text)
    match = re.fullmatch(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?", text)
    if not match:
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", text)
        if not match:
            return None
        year, month, day = (int(part) for part in match.groups())
        year += 2000 if year < 70 else 1900
    if not (1900 <= year <= 2199 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return "%04d-%02d-%02d" % (year, month, day)


def norm_version(text):
    """'V1.0' · '1' · '1.0.2' → '1.0' · '1.0' · '1.0.2'. 버전이 아니면 None."""
    if text is None:
        return None
    text = canon(text).lower().strip()
    for _ in range(3):  # 'Version No: V1.0' 처럼 접두어가 겹쳐 붙은 경우
        stripped = re.sub(r"^(version\s*no\.?|version|ver\.?|버전|v)\s*[:.]?\s*", "", text)
        if stripped == text:
            break
        text = stripped
    text = text.strip(" .:")
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3})*", text or ""):
        return None
    parts = text.split(".")
    if len(parts) == 1:
        parts.append("0")
    return ".".join(str(int(part)) for part in parts)


def norm_count_word(word):
    """'두' → 2, '3' → 3. 수사가 아니면 None."""
    if word is None:
        return None
    word = canon(word)
    if word.isdigit():
        return int(word)
    return _KOR_NUM.get(word)


def norm_minutes(value, unit):
    """(숫자, '분'|'시간') → 분. 범위를 벗어나면 None."""
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if unit.startswith("시간"):
        number *= 60
    if number <= 0 or number > 60 * 24 * 30:
        return None
    return int(round(number))


def range_key(low, high=None, unit=""):
    """범위를 비교 가능한 문자열 키로. high 가 없거나 같으면 단일값."""
    if high is None or high == low:
        return "%s%s" % (low, unit)
    return "%s-%s%s" % (low, high, unit)
