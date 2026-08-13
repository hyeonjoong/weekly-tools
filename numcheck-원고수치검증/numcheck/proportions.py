"""비율 재계산 — `23/48 (47.9%)` 이 정말 47.9% 인가.

이 툴에서 가장 많이 걸리는 항목이고, 가장 흔한 사고 경로이기도 하다. 데이터가
한 번 갱신돼 N 이 48 → 46 으로 바뀌면 표는 다시 붙여 넣지만 초록의 백분율은
그대로 남는다. 통계는 다시 돌렸으니 맞고, 문장은 읽히고, **틀린 건 숫자뿐이다.**

분모가 문장에 없을 때(`23명(47.9%)`)는 같은 줄/앞 줄에서 전체 N 을 찾아 쓰되,
후보가 **정확히 하나일 때만** 쓰고 지적 등급을 경고로 낮춘다. 추정한 분모로
치명을 찍으면 그 순간 이 툴은 소음이 된다.
"""

from __future__ import annotations

import bisect
import itertools
import re
from typing import List, Optional, Tuple

from .docio import Line
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .rounding import Reported, parse_number
from .textutil import normalize, sentence_at, snippet

__all__ = ["check_proportions"]

# `(?:,\d{3})+` 를 무한 반복으로 두면 `1,000,000,...` 같은 긴 자리 구분 열에서
# 역추적이 폭발한다(3만 자 한 줄에 54초). 실제 표본수에 자리 구분이 5개를
# 넘는 일은 없으므로 반복 횟수를 묶는다.
_INT = r"\d{1,3}(?:,\d{3}){1,4}|\d{1,7}"
# 소수 자릿수에 상한을 두면 `47.91667%` 같은 토큰이 **후보로도 안 잡혀** 커버리지
# 자백이 거짓말이 된다("후보 0개"). 자릿수는 넉넉히 받고 판정에서 거른다.
_PCT = r"\d{1,3}(?:\.\d{1,10})?"

# 1) 23/48 (47.9%) · 23/48 = 47.9% · 23/48, 47.9%
_FRAC_PCT = re.compile(
    rf"(?P<num>{_INT})\s*/\s*(?P<den>{_INT})\s*"
    rf"(?:[,;:]|=|→)?\s*[\(\[]?\s*(?P<pct>{_PCT})\s*%"
)
# 2) 47.9% (23/48)
_PCT_FRAC = re.compile(
    rf"(?P<pct>{_PCT})\s*%\s*[,;:]?\s*[\(\[]\s*(?P<num>{_INT})\s*/\s*(?P<den>{_INT})\s*[\)\]]"
)
# 3) 23 of the 48 patients (47.9%)
_OF_FORM = re.compile(
    rf"(?P<num>{_INT})\s*(?:of|out of)\s+(?:the\s+)?(?P<den>{_INT})\s*"
    rf"[A-Za-z ]{{0,24}}?[\(\[]\s*(?P<pct>{_PCT})\s*%\s*[\)\]]",
    re.IGNORECASE,
)
# 4) 48명 중 23명 (47.9%)
_KO_FORM = re.compile(
    rf"(?P<den>{_INT})\s*(?:명|건|례|개)?\s*중\s*(?P<num>{_INT})\s*(?:명|건|례|개)?"
    rf"[가-힣]{{0,4}}\s*[\(\[]?\s*(?P<pct>{_PCT})\s*%"
)
# 5) 23 (47.9%) — 분모는 문맥에서
_COUNT_PCT = re.compile(
    rf"(?:[nN]\s*=\s*)?(?P<num>{_INT})\s*(?:명|건|례|개)?\s*[\(\[]\s*(?P<pct>{_PCT})\s*%\s*[\)\]]"
)
# 후보 집계용: 모든 백분율 토큰. 단 "95% CI" 의 95 는 비율 claim 이 아니다 —
# 후보로 세면 커버리지 자백이 신뢰구간 개수만큼 부풀어 거짓말이 된다.
# **백분율점(%p)** 도 마찬가지다. `12.0%p` 는 두 비율의 차이이므로 분자/분모가
# 존재할 수 없는데, 이걸 "분모 없음"으로 세면 깨끗한 원고에서 건너뜀이 잔뜩
# 쌓여 사용자가 파싱 실패로 오해한다(사용법.md 가 그렇게 안내한다).
_ANY_PCT = re.compile(
    rf"(?<![\d.])(?P<pct>{_PCT})\s*%"
    rf"(?!\s*(?:CI\b|Cl\b|신뢰구간|confidence))"
    rf"(?!\s*(?:p\b|P\b|포인트|percentage\s+points?))",
    re.IGNORECASE,
)

# 문맥에서 전체 N 을 찾는 표현들
_DEN_PATTERNS = (
    re.compile(rf"\b[nN]\s*=\s*(?P<v>{_INT})"),
    re.compile(rf"총\s*(?P<v>{_INT})\s*(?:명|건|례|개)"),
    re.compile(rf"전체\s*(?P<v>{_INT})\s*(?:명|건|례|개)"),
    # 한국어에는 단어 경계가 없다. `\b` 를 붙이면 "46명이었다" 의 46 을 놓치고,
    # 그 결과 분모 후보가 하나뿐인 것처럼 보여 **엉뚱한 분모로 지적**하게 된다.
    re.compile(rf"(?P<v>{_INT})\s*(?:명|례|건|개)(?:의)?"),
    re.compile(
        rf"(?P<v>{_INT})\s*(?:patients|participants|subjects|cases|"
        rf"individuals|respondents|adults|children|women|men)\b",
        re.IGNORECASE,
    ),
)

_MEASURE_HINTS = ("증가", "감소", "reduction", "increase", "decrease", "change", "변화")


def _looks_like_a_date(num: int, den: int) -> bool:
    """`2025/03` 은 분수가 아니라 날짜다. 분자가 연도이고 분모가 월이면 손대지 않는다."""
    return 1900 <= num <= 2100 and 1 <= den <= 12


def _to_int(text: str) -> Optional[int]:
    try:
        return int(text.replace(",", ""))
    except ValueError:  # pragma: no cover - 정규식이 이미 막는다
        return None


def _context_denominators(text: str, offset: int = 0,
                          exclude: Optional[List[Tuple[int, int]]] = None) -> List[int]:
    """문맥에서 전체 N 후보를 모은다.

    ``exclude`` 는 "이 위치의 숫자는 분모가 될 수 없다"는 구간 목록이다. 다른 군의
    **분자**를 분모로 삼는 사고를 막는다 — `능동 37명 (41.6%), 위약 24명 (27.0%)`
    에서 37 을 24 의 분모로 쓰면 정직한 문장에 경고가 뜬다.
    """
    values: List[int] = []
    # `blocked` 를 매 후보마다 선형 훑으면 한 문장 안의 `24명 (27.0%)` 토큰 수
    # M 에 대해 M³ 이 된다 — 17KB 짜리 한 줄에서 53초가 걸렸다. 정렬해 두고
    # 이분 탐색하면 M²·logM 이 되고 같은 입력이 1초 아래로 떨어진다.
    blocked = sorted(exclude or [])
    starts = [a for a, _b in blocked]
    max_end = list(itertools.accumulate((b for _a, b in blocked), max)) if blocked else []
    for pattern in _DEN_PATTERNS:
        for m in pattern.finditer(text):
            lo_pos, hi_pos = offset + m.start("v"), offset + m.end("v")
            if blocked:
                # 시작점이 hi_pos 보다 작은 구간들 중 하나라도 끝점이 lo_pos 보다
                # 크면 겹친다. 끝점의 누적 최대값으로 한 번에 판정한다.
                idx = bisect.bisect_left(starts, hi_pos)
                if idx and max_end[idx - 1] > lo_pos:
                    continue
            v = _to_int(m.group("v"))
            if v is not None and 0 < v <= 10_000_000:
                values.append(v)
    return values


def _overlaps(span: Tuple[int, int], taken: List[Tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in taken)


def _evaluate(
    num: int,
    den: int,
    pct: Reported,
    opts: Options,
) -> Tuple[bool, float, str]:
    """(일치 여부, 재계산 백분율, 설명)."""
    computed = num / den * 100.0
    lo, hi = pct.interval(opts.k)
    ok = lo <= computed <= hi
    return ok, computed, ""


def check_proportions(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    """비율 claim 을 뽑아 재계산한다."""
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        text = normalize(ln.text)
        if not text.strip():
            continue
        if ln.section == "References":
            for m in _ANY_PCT.finditer(text):
                out.append((
                    Claim(ln.no, ln.section, "proportion", "비율", _q(opts, text, m),
                          skip_reason=SKIP_REASONS["reference"], verdict="건너뜀"),
                    None,
                ))
            continue

        taken: List[Tuple[int, int]] = []
        # 백분율이 딸린 개수는 그 자체가 분자다 — 다른 비율의 분모가 될 수 없다.
        numerator_spans = [m.span("num") for m in _COUNT_PCT.finditer(text)]

        # -- 분모가 본문에 있는 형태 (치명 등급) ------------------------------
        for pattern, explicit in (
            (_FRAC_PCT, True), (_PCT_FRAC, True), (_OF_FORM, True), (_KO_FORM, True),
        ):
            for m in pattern.finditer(text):
                if _overlaps(m.span(), taken):
                    continue
                num = _to_int(m.group("num"))
                den = _to_int(m.group("den"))
                pct = parse_number(m.group("pct"))
                if num is None or den is None or pct is None:
                    continue
                if _looks_like_a_date(num, den):
                    continue
                taken.append(m.span())
                quote = _q(opts, text, m)
                claim = Claim(ln.no, ln.section, "proportion", "비율 재계산", quote,
                              reported=f"{pct.raw}%")
                if den == 0:
                    claim.skip_reason = SKIP_REASONS["ambiguous"]
                    claim.verdict = "건너뜀"
                    out.append((claim, None))
                    continue
                claim.checked = True
                if num > den:
                    claim.verdict = "불일치"
                    claim.recomputed = "-"
                    out.append((claim, Finding(
                        "치명", ln.no, ln.section, "비율 재계산", quote,
                        f"{num}/{den}", "-",
                        f"분자({num})가 분모({den})보다 큽니다. 두 수가 뒤바뀌었을 수 있습니다.",
                        message_en=(f"Numerator ({num}) is larger than the denominator "
                                    f"({den}); the two may be swapped."),
                    )))
                    continue
                ok, computed, _ = _evaluate(num, den, pct, opts)
                claim.recomputed = f"{computed:.4g}%"
                claim.verdict = "일치" if ok else "불일치"
                finding = None
                if not ok:
                    finding = Finding(
                        "치명", ln.no, ln.section, "비율 재계산", quote,
                        f"{pct.raw}%", f"{computed:.4g}%",
                        f"{num}/{den} = {computed:.4g}% 인데 {pct.raw}% 로 적혀 있습니다"
                        f" (반올림·버림·올림 어느 관례로도 나오지 않는 값).",
                        message_en=(f"{num}/{den} = {computed:.4g}% but the manuscript says "
                                    f"{pct.raw}% — unreachable under round/floor/ceil."),
                    )
                out.append((claim, finding))

        # -- 분모를 문맥에서 찾아야 하는 형태 (경고 등급) --------------------
        for m in _COUNT_PCT.finditer(text):
            if _overlaps(m.span(), taken):
                continue
            num = _to_int(m.group("num"))
            pct = parse_number(m.group("pct"))
            if num is None or pct is None:
                continue
            taken.append(m.span())
            quote = _q(opts, text, m)
            claim = Claim(ln.no, ln.section, "proportion", "비율 재계산(추정 분모)",
                          quote, reported=f"{pct.raw}%")
            # 변화율·증감률은 분모가 전체 N 이 아니다 — 손대지 않는다.
            around = text[max(0, m.start() - 40): m.end() + 20].lower()
            if any(hint in around for hint in _MEASURE_HINTS):
                claim.skip_reason = SKIP_REASONS["ambiguous"]
                claim.verdict = "건너뜀"
                claim.note = "증감률로 보여 분모를 특정하지 않음"
                out.append((claim, None))
                continue
            # 분모는 **같은 문장 안**에서만 찾는다. 앞 문장까지 뒤지면
            # "총 120명이 선별되었다. 이 중 15명 (65.2%) 이 반응하였다." 에서
            # 선별 N 을 반응률의 분모로 삼아 정직한 원고에 경고를 낸다.
            sent_start, _sent_end, _sent = sentence_at(text, m.start())
            scope = text[sent_start: m.start()]
            cands = _context_denominators(scope, sent_start, numerator_spans)
            distinct = sorted(set(v for v in cands if v >= num))
            if len(distinct) != 1:
                claim.skip_reason = SKIP_REASONS["no_denominator"]
                claim.verdict = "건너뜀"
                claim.note = (
                    "문맥에서 분모 후보를 찾지 못함" if not distinct
                    else f"분모 후보가 {len(distinct)}개라 특정 불가"
                )
                out.append((claim, None))
                continue
            den = distinct[0]
            claim.checked = True
            ok, computed, _ = _evaluate(num, den, pct, opts)
            claim.recomputed = f"{computed:.4g}%"
            claim.verdict = "일치" if ok else "불일치"
            claim.note = f"분모 {den} 은 문맥에서 추정"
            finding = None
            if not ok:
                finding = Finding(
                    "경고", ln.no, ln.section, "비율 재계산(추정 분모)", quote,
                    f"{pct.raw}%", f"{computed:.4g}%",
                    f"문맥에서 찾은 분모 {den} 으로 계산하면 {num}/{den} = {computed:.4g}% 입니다."
                    f" 적힌 값은 {pct.raw}%. 분모가 {den} 이 맞는지 확인하세요.",
                    downgraded="분모를 문맥에서 추정했으므로 경고",
                    message_en=(f"Using the denominator {den} found in context, "
                                f"{num}/{den} = {computed:.4g}%, not {pct.raw}%. "
                                f"Check that {den} is the right denominator."),
                )
            out.append((claim, finding))

        # -- 남은 백분율 토큰: 후보로만 기록(커버리지 자백) ------------------
        for m in _ANY_PCT.finditer(text):
            if _overlaps(m.span(), taken):
                continue
            out.append((
                Claim(ln.no, ln.section, "proportion", "비율", _q(opts, text, m),
                      reported=f"{m.group('pct')}%",
                      skip_reason=SKIP_REASONS["no_denominator"], verdict="건너뜀",
                      note="분자/분모가 함께 적혀 있지 않음"),
                None,
            ))

    return out


def _q(opts: Options, text: str, m: re.Match) -> str:
    if not opts.quote:
        return ""
    return snippet(text, m.start(), m.end())
