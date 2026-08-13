"""N 합계 — `총 48명 (치료군 24, 대조군 23)` 에서 24 + 23 = 47 ≠ 48.

미배정 1명일 수도 있고 오탈자일 수도 있다. 어느 쪽이든 **저자가 알아야 하는
사실**이고, 사람 눈으로는 논문 한 편에서 이걸 전부 더해 보지 않는다.

오탐을 막기 위한 문지기가 여럿 있다. 괄호 안에 소수점·백분율·±·"세"·"년" 같은
단위가 섞여 있으면 하위군 분해가 아니라고 보고 손대지 않는다. 합계가 전체의
절반보다 작거나 1.5배보다 크면 애초에 분해가 아니라고 본다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .docio import Line
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .textutil import normalize, snippet

__all__ = ["check_counts"]

# `(?:,\d{3})+` 를 무한 반복으로 두면 `1,000,000,...` 같은 긴 자리 구분 열에서
# 역추적이 폭발한다(3만 자 한 줄에 54초). 실제 표본수에 자리 구분이 5개를
# 넘는 일은 없으므로 반복 횟수를 묶는다.
_INT = r"\d{1,3}(?:,\d{3}){1,4}|\d{1,7}"

_UNIT = (r"(?:명|례|건|patients|participants|subjects|cases|individuals"
         r"|women|men|children|adults)")
# 전체 N 과 괄호 사이에 동사구가 끼는 것이 영문에서는 오히려 표준이다 —
# `A total of 112 were randomized (56 active, 55 sham)` 은 무작위배정 논문에서
# 가장 흔한 CONSORT 문장인데, 붙어 있는 형태만 받으면 통째로 놓친다.
# 사이에 **숫자가 없어야** 하고(다른 수치를 건너뛰어 엮지 않도록) 길이도 제한한다.
_TOTAL_PAREN = re.compile(
    rf"(?:[nN]\s*=\s*)?(?P<total>{_INT})\s*{_UNIT}?"
    rf"(?P<gap>[A-Za-z가-힣\s,]{{0,44}}?)"
    rf"[\(\[](?P<inner>[^()\[\]]{{2,140}})[\)\]]",
    re.IGNORECASE,
)
# `총 112명이 배정되었다: 56명은 능동, 55명은 위약` — 괄호 대신 콜론.
# 앞에 '총/전체/a total of' 같은 **명시적 전체 표지**가 있을 때만 받는다.
# 그게 없으면 평범한 콜론 목록이 전부 하위군 분해로 오인된다.
_TOTAL_COLON = re.compile(
    rf"(?:총|전체|합계|a\s+total\s+of|overall|in\s+total)\s*"
    rf"(?:[nN]\s*=\s*)?(?P<total>{_INT})\s*{_UNIT}?"
    rf"[A-Za-z가-힣\s,]{{0,44}}?:\s*(?P<inner>[^.:;()\[\]]{{2,140}})",
    re.IGNORECASE,
)

# 괄호 안이 '하위군 분해'가 아님을 알려 주는 신호들
_INNER_BLOCKERS = (
    "%", "±", "sd", "se", "iqr", "range", "ci", "95", "mean", "median", "평균", "중앙",
    "years", "year", "세", "yr", "months", "개월", "주", "week", "day", "일차",
    "kg", "cm", "mm", "ms", "bpm", "점", "score", "p =", "p<", "p =", "vs.",
    "그림", "figure", "table", "표 ", "ref", "et al",
)
_DECIMAL = re.compile(r"\d+\.\d")

_TOTAL_WORDS = ("전체", "total", "all", "합계", "overall", "합", "계")


def _to_int(text: str) -> Optional[int]:
    try:
        return int(text.replace(",", ""))
    except ValueError:  # pragma: no cover
        return None


def _inner_integers(inner: str) -> Optional[List[int]]:
    """괄호 안이 하위군 분해로 보이면 그 정수들, 아니면 ``None``."""
    low = inner.lower()
    if _DECIMAL.search(inner):
        return None
    for bad in _INNER_BLOCKERS:
        if bad in low:
            return None
    numbers = [_to_int(m.group(0)) for m in re.finditer(_INT, inner)]
    numbers = [v for v in numbers if v is not None]
    if not (2 <= len(numbers) <= 6):
        return None
    if any(v <= 0 for v in numbers):
        return None
    # 라벨이 하나도 없는 "(1, 2, 3)" 은 목록일 뿐 하위군이 아니다
    if not re.search(r"[A-Za-z가-힣]", inner):
        return None
    return numbers


def check_counts(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        if ln.section == "References":
            continue
        text = normalize(ln.text)
        if not text.strip():
            continue
        out.extend(_paren_breakdowns(ln, text, opts))
        if ln.kind == "table" and "|" in text:
            out.extend(_table_row(ln, text, opts))
    return out


def _paren_breakdowns(ln: Line, text: str, opts: Options):
    results = []
    seen: List[Tuple[int, int]] = []
    for m in list(_TOTAL_PAREN.finditer(text)) + list(_TOTAL_COLON.finditer(text)):
        if any(m.start() < b and a < m.end() for a, b in seen):
            continue
        total = _to_int(m.group("total"))
        if total is None or total <= 0:
            continue
        parts = _inner_integers(m.group("inner"))
        if parts is None:
            continue
        seen.append(m.span())
        subtotal = sum(parts)
        quote = snippet(text, m.start(), m.end()) if opts.quote else ""
        claim = Claim(ln.no, ln.section, "nsum", "N 합계", quote,
                      reported=f"전체 {total}", recomputed=f"합 {subtotal}")
        if not (0.5 * total <= subtotal <= 1.5 * total):
            claim.skip_reason = SKIP_REASONS["ambiguous"]
            claim.verdict = "건너뜀"
            claim.note = "괄호 안 숫자의 합이 전체와 너무 달라 하위군 분해로 보지 않음"
            results.append((claim, None))
            continue
        claim.checked = True
        if subtotal == total:
            claim.verdict = "일치"
            results.append((claim, None))
            continue
        claim.verdict = "불일치"
        gap = subtotal - total
        results.append((claim, Finding(
            "경고", ln.no, ln.section, "N 합계", quote,
            f"전체 {total}", f"{' + '.join(str(v) for v in parts)} = {subtotal}",
            f"하위군 합 {subtotal} 이 전체 {total} 과 {abs(gap)} 만큼 다릅니다"
            f" ({'초과' if gap > 0 else '부족'}). 미배정·탈락 인원이거나 오탈자입니다.",
            message_en=(f"Subgroup counts sum to {subtotal}, which differs from the stated "
                        f"total {total} by {abs(gap)}."),
        )))
    return results


_CELL_N = re.compile(rf"[nN]\s*=\s*(?P<v>{_INT})")


def _table_row(ln: Line, text: str, opts: Options):
    """표 머리행의 `전체 (N = 48) | 치료 (n = 24) | 대조 (n = 23)` 형태."""
    cells = [c.strip() for c in text.split("|")]
    total: Optional[int] = None
    subs: List[int] = []
    for cell in cells:
        m = _CELL_N.search(cell)
        if not m:
            continue
        value = _to_int(m.group("v"))
        if value is None or value <= 0:
            continue
        low = cell.lower()
        if total is None and any(word in low for word in _TOTAL_WORDS):
            total = value
        else:
            subs.append(value)
    if total is None or len(subs) < 2:
        return []
    subtotal = sum(subs)
    quote = snippet(text, 0, min(len(text), 80)) if opts.quote else ""
    claim = Claim(ln.no, ln.section, "nsum", "표 열 N 합계", quote,
                  reported=f"전체 {total}", recomputed=f"합 {subtotal}")
    if not (0.5 * total <= subtotal <= 1.5 * total):
        claim.skip_reason = SKIP_REASONS["ambiguous"]
        claim.verdict = "건너뜀"
        claim.note = "열 N 의 합이 전체와 너무 달라 판단하지 않음"
        return [(claim, None)]
    claim.checked = True
    if subtotal == total:
        claim.verdict = "일치"
        return [(claim, None)]
    claim.verdict = "불일치"
    return [(claim, Finding(
        "경고", ln.no, ln.section, "표 열 N 합계", quote,
        f"전체 {total}", f"{' + '.join(str(v) for v in subs)} = {subtotal}",
        f"표 머리행의 군별 N 합 {subtotal} 이 전체 {total} 과 다릅니다.",
        message_en=(f"Group Ns in the table header sum to {subtotal}, "
                    f"not the stated total {total}."),
    ))]
