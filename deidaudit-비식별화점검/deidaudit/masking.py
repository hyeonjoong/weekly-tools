"""증거 마스킹.

**설계 원칙(안전 요건, 기능 아님)**: 리포트에 들어가는 증거 문자열은
원문에서 잘라 붙이지 않고, (a) 이 모듈이 알고 있는 고정 어휘와
(b) 원본에서 최대 뒤 2자리만 남긴 마스킹 파생값으로 **합성**합니다.
따라서 원본 식별자 문자열이 산출물에 그대로 등장하는 경로가
구조적으로 존재하지 않습니다. (tests/test_report_safety.py 가 강제)
"""

from __future__ import annotations

import re

MASK_CIRCLE = "○"


def mask_phone(digits: str, head: str = "") -> str:
    """전화번호를 `010-****-**89` 형태로 마스킹합니다.

    Args:
        digits: 하이픈이 있어도 되는 원본 번호 문자열.
        head: 그대로 남길 앞자리(통신사·지역번호). 비우면 앞 3자리를 씁니다.

    Returns:
        마지막 2자리만 남긴 마스킹 문자열.
    """
    only = re.sub(r"\D", "", digits)
    if len(only) < 4:
        return "***-****-****"
    prefix = re.sub(r"\D", "", head) or only[:3]
    tail = only[-2:]
    return f"{prefix}-****-**{tail}"


def mask_rrn(text: str) -> str:
    """주민등록번호를 `88****-1******` 형태로 마스킹합니다."""
    only = re.sub(r"\D", "", text)
    if len(only) < 7:
        return "******-*******"
    return f"{only[:2]}****-{only[6]}******"


def mask_email(text: str) -> str:
    """이메일을 `h***@b***.kr` 형태로 마스킹합니다."""
    text = text.strip()
    if "@" not in text:
        return "***@***"
    local, _, domain = text.partition("@")
    local_head = local[:1] if local else "*"
    domain_head = domain[:1] if domain else "*"
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    tld = re.sub(r"[^A-Za-z]", "", tld)[:4]
    suffix = f".{tld}" if tld else ""
    return f"{local_head}***@{domain_head}***{suffix}"


def mask_korean_name(name: str) -> str:
    """한글 성명을 `김○○` 형태로 마스킹합니다(성씨만 남김)."""
    name = name.strip()
    if not name:
        return MASK_CIRCLE * 3
    return name[0] + MASK_CIRCLE * max(len(name) - 1, 1)


def mask_date(text: str) -> str:
    """날짜를 `1988-**-**` 형태로 마스킹합니다(연도만 남김)."""
    m = re.search(r"(\d{4})", text)
    if m:
        return f"{m.group(1)}-**-**"
    return "****-**-**"


def mask_number(text: str) -> str:
    """숫자를 자릿수만 남겨 마스킹합니다(예: `89 초과(2자리)`)."""
    only = re.sub(r"\D", "", text)
    return f"{len(only)}자리 숫자" if only else "숫자 아님"


def mask_free_text_person(masked_name: str, title: str) -> str:
    """자유텍스트 인명 언급 증거를 합성합니다.

    Args:
        masked_name: 이미 마스킹된 이름(또는 원문 자리표시자).
        title: **이 툴의 고정 어휘**에서 온 호칭(간호사/선생님 등).

    Returns:
        `…○○○ 간호사…` 형태의 합성 증거.
    """
    return f"…{masked_name} {title}…"


def mask_generic(text: str, keep: int = 0) -> str:
    """어떤 문자열이든 길이 정보만 남겨 마스킹합니다.

    Args:
        text: 원본.
        keep: 뒤에서 남길 문자 수(기본 0 — 하나도 남기지 않음).
    """
    text = text.strip()
    if not text:
        return "(빈값)"
    if keep and len(text) > keep:
        return "*" * (len(text) - keep) + text[-keep:]
    return "*" * len(text)
