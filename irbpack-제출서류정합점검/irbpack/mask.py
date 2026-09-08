"""리포트가 유출 경로가 되지 않게 — 전화번호·주민등록번호·이메일 마스킹.

이 툴은 동의서와 모집공고를 읽습니다. 거기에는 **연구자 개인의 휴대전화**가
들어 있는 일이 흔합니다(실제로 그것을 잡는 것이 이 툴의 점검 항목 4번입니다).
그런데 잡아낸 결과를 그대로 인쇄하면, 공유해도 되는 점검 리포트가 **개인 연락처
사본**이 되어 버립니다. 그래서 화면·`.md`·`.csv` 로 나가는 모든 문자열은 이
모듈을 반드시 거칩니다.

마스킹 규칙(리포트에도 인쇄됩니다):

- 전화번호 `010-0000-1234` → `010-****-1234` (**가운데 자리만** 가림)
  — 어느 번호인지 사람이 알아보되, 그대로 복사해 쓸 수는 없게.
- 주민등록번호 형태 `900101-1234567` → `******-*******` (**전부** 가림)
- 이메일 `snubhirb@snubh.org` → `s*******@snubh.org` (앞 한 글자만 남김)

비교·정규화는 **가리지 않은 원본 값**으로 합니다. 마스킹은 출력 직전에만
적용되므로 `010-1111-1111` 과 `010-2222-1111` 이 같은 값으로 뭉개지지 않습니다.
"""
from __future__ import annotations

import re

_INVIS = set("\u00ad\u2060\u200b\u200c\u200d")
_DASH_BETWEEN_DIGITS = re.compile(r"(?<=[0-9])\s*[‐‑‒–—―−﹣]\s*(?=[0-9])")

#: 주민등록번호 형태. 전화번호보다 **먼저** 가려야 합니다
#: (`900101-1234567` 이 전화번호 정규식에 걸리지 않도록 자리수를 고정).
_RRN = re.compile(r"(?<![0-9])([0-9]{6})\s*[-‑–—]?\s*([0-9][0-9]{6})(?![0-9])")

#: 한국 전화번호. 지역번호 2~3자리 + 국번 3~4자리 + 4자리.
_PHONE = re.compile(
    r"(?<![0-9])(?:\+\s*82\s*[-‑–—.\s]?\s*(?:\(0\)\s*|0)?|\(?0)([0-9]{1,2})\)?\s*[-‑–—.\s]?\s*([0-9]{3,4})\s*[-‑–—.\s]?\s*([0-9]{4})(?![0-9])"
    r"|(?<![0-9])(1[5-8][0-9]{2})\s*[-‑–—.]\s*([0-9]{4})(?![0-9])")

#: 이메일. 로컬파트 첫 글자만 남깁니다.
#: 앞쪽 lookbehind 로 토큰 중간에서 시작하지 못하게 — 긴 ASCII 문자열에서 O(n²) 백트래킹이 나던 사고 방지.
_EMAIL = re.compile(r"(?<![\w.%+\-])([\w.%+\-])([\w.%+\-]{0,63})@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

#: 리포트 부록에 그대로 인쇄되는 규칙 설명.
RULES = (
    "전화번호: 가운데 자리를 `*` 로 가림 (010-0000-1234 → 010-****-1234)",
    "주민등록번호 형태: 전부 가림 (900101-1234567 → ******-*******)",
    "이메일: 로컬파트 첫 글자만 남김 (irb@snubh.org → i**@snubh.org)",
)


def _mask_phone(m: "re.Match") -> str:
    if m.group(4):  # 1588-xxxx 대표번호
        return "{}-{}".format(m.group(4), "*" * 4)
    head, mid, tail = m.group(1), m.group(2), m.group(3)
    return "0{}-{}-{}".format(head, "*" * len(mid), tail)


def _mask_email(m: "re.Match") -> str:
    return "{}{}@{}".format(m.group(1), "*" * max(len(m.group(2)), 1), m.group(3))


def mask(text: str) -> str:
    """리포트로 나가는 문자열 하나를 마스킹합니다. 전각 숫자(０１０)는 반각으로 바꾼 뒤 가립니다."""
    if not text:
        return text
    text = "".join(chr(ord(ch) - 0xFEE0) if "！" <= ch <= "～" else ch for ch in text)  # 전각 숫자·기호 → 반각
    text = "".join(ch for ch in text if ch not in _INVIS)  # 소프트하이픈·워드조이너 제거
    text = _DASH_BETWEEN_DIGITS.sub("-", text)  # 숫자 사이의 ‐‒–—− 만 - 로 (문장 속 대시는 건드리지 않음)
    out = _RRN.sub(lambda m: "*" * 6 + "-" + "*" * 7, text)
    out = _PHONE.sub(_mask_phone, out)
    out = _EMAIL.sub(_mask_email, out)
    return out


def looks_personal_mobile(number: str) -> bool:
    """`010-…` 계열 개인 휴대전화로 보이는지. 대표번호(02·031…)는 False."""
    digits = re.sub(r"[^0-9]", "", number or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    return digits.startswith("01") and len(digits) in (10, 11)
