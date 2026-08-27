"""직접식별자 탐지기.

**설계 방침: 넓히지 말고 좁힌다.** 매번 우는 체커는 두 번 다시 열리지
않습니다. 그래서 각 규칙은 오탐이 나기 쉬운 쪽을 잘라 냅니다.

* 주민등록번호: 체크섬을 실제로 검증합니다. 통과 → 치명, 하이픈이 명시된
  형태에서만 미통과 → 경고. 하이픈 없는 13자리는 체크섬을 통과해야만
  보고합니다(RR 간격·측정값이 우연히 걸리는 것을 막기 위해).
* 휴대전화: 앞뒤가 숫자/하이픈이면 매칭하지 않습니다
  (`BELL-001-010-1234` 같은 코드가 전화번호로 둔갑하지 않도록).
* 한글 성명: 성씨 사전 + 음절 수 + **문맥**(이름류 열의 셀 전체이거나,
  자유텍스트에서 호칭이 뒤따를 때)에서만 봅니다.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import List, Optional

from .findings import CRITICAL, WARNING
from .masking import (
    mask_email,
    mask_free_text_person,
    mask_korean_name,
    mask_phone,
    mask_rrn,
)

# 한국 성씨(단음절 + 복성). 이름 판정의 1차 관문입니다.
SURNAMES = {
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신",
    "권", "황", "안", "송", "류", "전", "홍", "고", "문", "양", "손", "배", "백", "허",
    "유", "남", "심", "노", "하", "곽", "성", "차", "주", "우", "구", "나", "민", "진",
    "지", "엄", "채", "원", "천", "방", "공", "현", "함", "변", "여", "추", "도", "소",
    "석", "선", "설", "마", "길", "연", "위", "표", "명", "기", "반", "왕", "금", "옥",
    "육", "인", "맹", "제", "모", "탁", "국", "라", "복", "태", "빈", "봉", "궁",
}
COMPOUND_SURNAMES = {"남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방", "어금"}

# 자유텍스트에서 인명을 가리키는 호칭(이 모듈의 고정 어휘 — 증거 합성에 씁니다).
PERSON_TITLES_LONG = (
    "간호사", "선생님", "선생", "교수님", "교수", "원장님", "원장", "박사님", "박사",
    "환자분", "보호자", "담당자", "상담사", "치료사", "실장님", "팀장님", "코디",
    "연구원", "주치의", "전공의", "약사님", "기사님", "조교",
)
PERSON_TITLES_SHORT = ("씨", "님")

_MASK_PLACEHOLDER = r"[○◯Ｏ⚪oO0*xX×□■?][○◯Ｏ⚪oO0*xX×□■?]{1,3}"

_RRN_HYPHEN_RE = re.compile(r"(?<![0-9])(\d{6})\s?[-–—]\s?([0-9]\d{6})(?![0-9])")
_RRN_PLAIN_RE = re.compile(r"(?<![0-9])(\d{6})([0-9]\d{6})(?![0-9])")
_PHONE_RE = re.compile(r"(?<![0-9\-])(01[016789])[-. ]?(\d{3,4})[-. ]?(\d{4})(?![0-9\-])")

# 유선·인터넷전화: 지역번호는 고정 목록이고 **구분자를 반드시 요구**합니다.
# (구분자 없는 `0234567890` 을 허용하면 측정값·코드가 전화번호로 둔갑합니다.)
_AREA_CODES = (
    "02", "031", "032", "033", "041", "042", "043", "044", "051", "052", "053",
    "054", "055", "061", "062", "063", "064", "070", "0502", "0503", "0504", "0505", "0506", "0507",
)
# 구분자는 `-`·`.`·공백·`)` 를 받습니다(`02)345-6789`, `02 345 6789` 는 흔한 표기).
# 구분자 자체는 여전히 **필수** — 없으면 측정값이 전화번호로 둔갑합니다.
_LANDLINE_RE = re.compile(
    r"(?<![0-9\-])(" + "|".join(sorted(_AREA_CODES, key=len, reverse=True))
    + r")[-. )](\d{3,4})[-. ](\d{4})(?![0-9\-])"
)
# 국제 표기: +82-10-1234-5678, +82 2 345 6789
_INTL_RE = re.compile(r"\+82[-. ]?(\d{1,2})[-. ]?(\d{3,4})[-. ]?(\d{4})(?![0-9])")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_HANGUL_NAME_RE = re.compile(r"^[가-힣]{2,4}$")

# 호칭 앞에는 **반드시 공백**이 있어야 합니다. `연구간호사`·`담당자`처럼 붙여 쓴 직함은
# 사람 이름이 아니라 역할 명사이기 때문입니다 (실측: 붙여쓰기를 허용하면 현실적인 임상
# 메모 24문장 중 14문장이 오탐이었습니다). 대신 `김간호사`처럼 붙여 쓴 진짜 이름은
# 놓치며, 그 한계는 README 에 적어 두었습니다.
_LONG_TITLE_RE = re.compile(
    r"(?<![가-힣])((?:[가-힣]{2,4})|(?:" + _MASK_PLACEHOLDER + r"))\s(" + "|".join(PERSON_TITLES_LONG) + r")"
)
# `씨`/`님` 은 **붙여 쓴 형태가 오히려 표준**입니다(`김철수님께 안내함`).
# 붙여쓰기를 허용하되, 앞 토큰이 정확히 3음절(또는 복성 4음절)일 것을 요구하고
# 역할 수식어·관용어 스톱워드로 오탐을 막습니다.
_SHORT_TITLE_RE = re.compile(
    r"(?<![가-힣])((?:[가-힣]{3,4})|(?:" + _MASK_PLACEHOLDER + r"))\s?("
    + "|".join(PERSON_TITLES_SHORT)
    + r")(?=[^가-힣]|$|가|는|이|에게|한테|와|과|도|의|를|을|랑|께)"
)
_NAME_LABEL_RE = re.compile(r"(?:이름|성명|성함)\s*[:：]\s*([가-힣]{2,4})")

# 호칭처럼 보이지만 사람이 아닌 표현(오탐 차단).
_PERSON_STOPWORDS = {
    "오늘날씨", "날씨", "아저씨", "아가씨", "손님", "주님", "하나님", "하느님",
    "고객님", "회원님", "여러분", "이번주", "지난주",
}

# 호칭 **앞에 오는 역할·부서·도메인 수식어**. 성씨로 시작하지만 사람 이름이 아닙니다
# (연구/지도/담당/방문/임상/심리/조사/안전성 … 은 전부 첫 음절이 한국 성씨입니다).
# 이 목록이 없으면 "연구 간호사 확인함" 이 "연○ 간호사" 로 잡혀 매 행마다 웁니다.
_ROLE_MODIFIERS = {
    "연구", "지도", "담당", "방문", "임상", "심리", "조사", "주간", "야간", "안전",
    "안전성", "전화", "고객", "기기", "수면", "상담", "진료", "외래", "병동", "응급",
    "재활", "언어", "물리", "작업", "영양", "사회", "정신", "마취", "소아", "내과",
    "외과", "검사", "시험", "기관", "현장", "본원", "협력", "통계", "자문", "교육",
    "훈련", "지원", "관리", "행정", "간호", "의료", "보건", "가정", "지역", "전담",
    "주치", "보조", "제1", "제2", "해당", "각각", "본인", "상기", "하기", "기존",
}


@dataclass(frozen=True)
class Hit:
    """셀 하나에서 나온 탐지 결과."""

    kind: str
    severity: str
    evidence: str
    note: str = ""


def rrn_checksum_ok(digits: str) -> bool:
    """주민등록번호 13자리의 검증번호를 실제로 계산해 대조합니다."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    total = sum(int(d) * w for d, w in zip(digits[:12], weights))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


def rrn_date_ok(digits: str) -> bool:
    """앞 6자리 + 성별자리로 생년월일이 실재하는 날짜인지 확인합니다."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    gender = int(digits[6])
    # 성별자리 1·2(내국인 1900년대), 3·4(2000년대), 5·6(외국인 1900년대), 7·8(2000년대).
    # 9·0(1800년대 출생)은 현존 인구가 사실상 없어 오탐만 늘리므로 취급하지 않습니다.
    century = {1: 1900, 2: 1900, 3: 2000, 4: 2000, 5: 1900, 6: 1900, 7: 2000, 8: 2000}
    if gender not in century:
        return False
    year = century[gender] + yy
    if not 1 <= mm <= 12:
        return False
    try:
        last_day = calendar.monthrange(year, mm)[1]
    except calendar.IllegalMonthError:
        return False
    return 1 <= dd <= last_day


def scan_rrn(text: str) -> List[Hit]:
    """셀에서 주민등록번호를 찾습니다."""
    hits: List[Hit] = []
    seen = set()
    for m in _RRN_HYPHEN_RE.finditer(text):
        digits = m.group(1) + m.group(2)
        if digits in seen:
            continue
        seen.add(digits)
        if not rrn_date_ok(digits):
            continue
        if rrn_checksum_ok(digits):
            hits.append(Hit("주민등록번호(체크섬 통과)", CRITICAL, mask_rrn(digits),
                            "검증번호까지 맞는 실제 주민등록번호 형식입니다."))
        else:
            hits.append(Hit("주민등록번호 형식(체크섬 불일치)", WARNING, mask_rrn(digits),
                            "형식은 주민등록번호인데 검증번호가 맞지 않습니다 — 오타이거나 가짜 번호일 수 있습니다."))
    for m in _RRN_PLAIN_RE.finditer(text):
        digits = m.group(1) + m.group(2)
        if digits in seen:
            continue
        seen.add(digits)
        # 하이픈이 없으면 체크섬을 통과할 때만 보고합니다(오탐 억제).
        if rrn_date_ok(digits) and rrn_checksum_ok(digits):
            hits.append(Hit("주민등록번호(체크섬 통과)", CRITICAL, mask_rrn(digits),
                            "하이픈 없이 13자리로 적혀 있지만 검증번호가 맞습니다."))
    return hits


def scan_phone(text: str) -> List[Hit]:
    """셀에서 휴대전화 번호를 찾습니다."""
    hits: List[Hit] = []
    seen = set()
    for m in _PHONE_RE.finditer(text):
        digits = m.group(1) + m.group(2) + m.group(3)
        if len(digits) not in (10, 11) or digits in seen:
            continue
        seen.add(digits)
        hits.append(Hit("휴대전화", CRITICAL, mask_phone(digits), ""))
    return hits


def scan_landline(text: str) -> List[Hit]:
    """유선전화·인터넷전화(070)·국제표기(+82) 번호를 찾습니다."""
    hits: List[Hit] = []
    seen = set()
    for m in _LANDLINE_RE.finditer(text):
        digits = m.group(1) + m.group(2) + m.group(3)
        if digits in seen:
            continue
        seen.add(digits)
        hits.append(Hit("유선/인터넷 전화", CRITICAL, mask_phone(digits, head=m.group(1)),
                        "지역번호가 붙은 연락처입니다. 구분자(-·.)가 있을 때만 잡습니다."))
    for m in _INTL_RE.finditer(text):
        digits = "0" + m.group(1) + m.group(2) + m.group(3)
        if digits in seen:
            continue
        seen.add(digits)
        hits.append(Hit("국제표기 전화(+82)", CRITICAL, mask_phone(digits, head="0" + m.group(1)), ""))
    return hits


def scan_email(text: str) -> List[Hit]:
    """셀에서 이메일 주소를 찾습니다."""
    hits: List[Hit] = []
    seen = set()
    for m in _EMAIL_RE.finditer(text):
        value = m.group(0)
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        hits.append(Hit("이메일", CRITICAL, mask_email(value), ""))
    return hits


def scan_structured(text: str) -> List[Hit]:
    """모든 셀에 돌리는 고정밀 스캔(주민번호·전화·이메일)."""
    if not text:
        return []
    hits: List[Hit] = []
    if any(ch.isdigit() for ch in text):
        hits.extend(scan_rrn(text))
        hits.extend(scan_phone(text))
        hits.extend(scan_landline(text))
    if "@" in text:
        hits.extend(scan_email(text))
    return hits


def is_korean_name(text: str) -> bool:
    """셀 값 전체가 한글 성명으로 보이는가."""
    value = (text or "").strip()
    if not _HANGUL_NAME_RE.match(value):
        return False
    if value[:2] in COMPOUND_SURNAMES:
        return len(value) >= 3
    return value[0] in SURNAMES and len(value) >= 2


def scan_name_cell(text: str) -> List[Hit]:
    """이름류 열의 셀 하나를 검사합니다(셀 전체가 이름일 때만)."""
    value = (text or "").strip()
    if not value:
        return []
    if is_korean_name(value):
        return [Hit("성명", CRITICAL, mask_korean_name(value), "")]
    return []


def scan_free_text_person(text: str) -> List[Hit]:
    """자유텍스트에서 사람을 가리키는 언급을 찾습니다.

    증거 문자열은 원문에서 잘라 오지 않고, 마스킹된 이름 + 이 모듈의
    고정 호칭 어휘로 **합성**합니다.
    """
    if not text:
        return []
    hits: List[Hit] = []
    seen = set()
    for regex in (_LONG_TITLE_RE, _SHORT_TITLE_RE):
        for m in regex.finditer(text):
            name, title = m.group(1), m.group(2)
            whole = (name + title).strip()
            if whole in _PERSON_STOPWORDS or name in _PERSON_STOPWORDS:
                continue
            masked = _mask_person_token(name)
            if masked is None:
                continue
            key = (masked, title)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                Hit("자유텍스트 내 인명", WARNING, mask_free_text_person(masked, title),
                    "자유기술 칸에 제3자 또는 본인을 가리키는 표현이 있습니다. 지울지 남길지는 사람이 정해야 합니다.")
            )
    for m in _NAME_LABEL_RE.finditer(text):
        name = m.group(1)
        masked = mask_korean_name(name)
        key = (masked, "이름표기")
        if key in seen:
            continue
        seen.add(key)
        hits.append(Hit("자유텍스트 내 인명", WARNING, f"…이름: {masked}…",
                        "자유기술 칸에 이름이 라벨과 함께 적혀 있습니다."))
    return hits


def _mask_person_token(token: str) -> Optional[str]:
    """인명 후보 토큰을 마스킹합니다. 사람 이름 같지 않으면 None."""
    token = token.strip()
    if not token:
        return None
    if re.fullmatch(r"[가-힣]{2,4}", token):
        if token in _ROLE_MODIFIERS:
            return None
        if token[:2] in COMPOUND_SURNAMES:
            return mask_korean_name(token) if len(token) >= 3 else None
        if len(token) == 4:
            return None  # 복성이 아닌 4음절은 이름보다 낱말일 확률이 훨씬 높습니다.
        if token[0] not in SURNAMES:
            return None
        return mask_korean_name(token)
    # 이미 가려진 자리표시자(○○○ 등) — 그대로 마스킹 형태로 씁니다.
    if re.fullmatch(_MASK_PLACEHOLDER, token):
        return "○" * len(token)
    return None
