"""신뢰구간 정합 — 점추정치가 CI 안에 있는가, CI 와 p 가 서로 모순되지 않는가.

두 가지를 본다.

1. **점추정치 ∈ [하한, 상한]**. 하한 > 상한(뒤바뀜) 도 여기서 걸린다.
   리비전에서 표를 다시 붙일 때 한 칸이 밀리면 정확히 이 모양이 된다.
2. **CI ↔ p 모순**. CI 가 귀무값(차이는 0, 비는 1)을 포함하는데 p < .05 이거나,
   포함하지 않는데 p ≥ .05 이면 둘 중 하나가 틀렸다. 다만 검정과 구간 추정의
   방법이 다를 때(정확검정 vs Wald 등) 정당하게 갈릴 수 있으므로 **경고**다.

귀무값을 모르면 2번을 건너뛴다. "평균 ISI 14.4 (95% CI 13.1–15.7)" 처럼 차이가
아닌 절대값의 CI 에 0 을 들이대면 헛소리가 되기 때문이다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .docio import Line
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .pvalues import find_pvalues
from .rounding import Reported, effective_k, parse_number
from .textutil import normalize, sentences, snippet

__all__ = ["check_intervals"]

_NUM = r"[-+]?\d{1,9}(?:\.\d{1,6})?"
_SEP = r"(?:\s*(?:to|,|;|~|-|–|—|부터)\s*|\s+)"

# 구분자는 `--`(LaTeX 엔대시)를 **먼저** 소비해야 한다. 그러지 않으면
# `0.49--0.94` 에서 첫 `-` 만 구분자로 먹고 둘째를 부호로 읽어, 상한이 -0.94 가
# 되고 "상·하한이 뒤바뀌었다"는 치명이 정직한 원고에서 나온다.
_CI_RE = re.compile(
    rf"(?P<level>9[05](?:\.\d)?)\s*%\s*(?:CI|Cl|confidence\s+interval|신뢰구간)\s*"
    rf"[:,=]?\s*[\[\(]?\s*(?P<lo>{_NUM})\s*(?:--|—|–|to|,|~|;|부터|-)\s*"
    rf"(?P<hi>{_NUM})\s*[\]\)]?",
    re.IGNORECASE,
)
# 점추정치: CI 표기 바로 앞에 오는 수 (괄호·대괄호와 **단위어**를 사이에 두고).
# `-4.2 points (95% CI …)` 와 `24 minutes (95% CI …)` 는 수면의학 논문에서
# 가장 흔한 CI 표기인데, 단위어를 허용하지 않으면 이 검사가 통째로 건너뛰어진다.
_UNIT_WORD = (r"(?:점|분|시간|초|개|명|%p|%포인트|퍼센트포인트"
              r"|points?|units?|minutes?|min|hours?|hrs?|seconds?|sec|s"
              r"|days?|ms|kg|cm|mmHg|dB|percentage\s+points?)")
_POINT_BEFORE = re.compile(
    rf"(?P<point>{_NUM})\s*(?:%|{_UNIT_WORD})?\s*[\(\[,;:]?\s*$", re.IGNORECASE)

# 비(ratio) 지표 — 귀무값이 1.
# **substring 매칭을 쓰면 안 된다.** 예전에 " or " 를 넣었더니 "the ISI or the
# PSQI" 같은 평범한 접속사가 걸려, 평균차 CI 를 귀무값 1 로 검사하고 헛된 경고를
# 냈다. 약어는 대문자 + 등호/괄호/숫자 인접일 때만 인정한다.
_RATIO_RE = re.compile(
    r"odds ratio|risk ratio|rate ratio|hazard ratio|prevalence ratio"
    r"|incidence rate ratio|risk difference ratio"
    r"|교차비|위험비|비교위험도|위험도비|상대위험|오즈비|위험도 비"
    r"|(?<![A-Za-z])(?:OR|RR|HR|IRR|aOR|aHR)\s*(?:=|:|\s*[\(\[]?\s*\d)",
)
# 차이 지표 — 귀무값이 0
_DIFF_RE = re.compile(
    r"difference|change|effect|coefficient|beta|β|slope"
    r"|(?<![A-Za-z])(?:MD|SMD|WMD)(?![A-Za-z])|cohen|hedges"
    r"|차이|변화|효과|계수|감소|증가|기울기",
    re.IGNORECASE,
)


# 비열등성·동등성 설계에서 p 는 **여백(margin)** 에 대한 검정이다. 귀무값 0/1 을
# 들이대면 "CI 가 0 을 포함하는데 p < .001" 이라는 헛된 경고가 나온다.
_MARGIN_RE = re.compile(
    r"non-?inferior|noninferior|equivalence|equivalent|two one-?sided|TOST"
    r"|margin|비열등|동등성|열등하지 않",
    re.IGNORECASE,
)


# 절대량 지표 — 차이도 비도 아니므로 귀무값이 없다. `평균 ISI 8.20 (95% CI
# 7.10–9.30)` 에 0 을 들이대면 "CI 가 0 을 포함하지 않는데 p 는 유의하지 않다"는
# 헛소리가 나온다. 이 모듈 맨 위 docstring 이 하지 않겠다고 적어 둔 바로 그 행동이다.
_ABSOLUTE_RE = re.compile(
    r"평균|중앙값|점수|총점|\bmean\b|\bmedian\b|\bscore\b|\bprevalence\b|유병률|발생률",
    re.IGNORECASE,
)


def _null_value(sentence: str, head: Optional[str] = None) -> Optional[float]:
    """이 CI 가 무엇의 CI 인가 — 차이(0) 인가 비(1) 인가. 모르면 None.

    단서는 **CI 앞쪽(head)** 에서만 찾는다. 문장 전체를 훑으면
    ``평균 8.20 (95% CI 7.10–9.30) 으로 감소하였고`` 의 뒤따르는 "감소" 가 걸려
    절대 평균의 CI 를 차이로 오인한다. 그리고 CI 바로 앞 어절이 '평균/중앙값'
    같은 절대량이면, 앞쪽에 차이 단서가 있더라도 판정을 접는다.
    """
    scope = sentence if head is None else head
    if _MARGIN_RE.search(sentence):
        return None
    tail = scope[-40:]
    ratio_near, diff_near = _RATIO_RE.search(tail), _DIFF_RE.search(tail)
    if not (ratio_near or diff_near) and _ABSOLUTE_RE.search(tail):
        return None
    if _RATIO_RE.search(scope):
        return 1.0
    if _DIFF_RE.search(scope):
        return 0.0
    return None


def check_intervals(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        if ln.section == "References":
            continue
        text = normalize(ln.text)
        if not text.strip():
            continue
        for start, _end, sentence in sentences(text):
            cis = list(_CI_RE.finditer(sentence))
            for m in cis:
                lo = parse_number(m.group("lo"))
                hi = parse_number(m.group("hi"))
                if lo is None or hi is None:
                    continue
                span = (start + m.start(), start + m.end())
                quote = snippet(text, span[0], span[1]) if opts.quote else ""
                out.extend(_check_one(ln, sentence, m, lo, hi, quote, opts,
                                      single_ci=len(cis) == 1))
    return out


def _check_one(ln: Line, sentence: str, m: re.Match, lo: Reported, hi: Reported,
               quote: str, opts: Options, single_ci: bool):
    results = []
    label = f"{m.group('level')}% CI"
    point = _point_estimate(sentence, m.start())

    claim = Claim(ln.no, ln.section, "ci", "신뢰구간 정합", quote,
                  reported=f"{label} {lo.raw} – {hi.raw}")

    # 뒤바뀜 판정에도 반올림을 허용한다. 참값 [2.1, 2.9] 를 0자리로 인쇄하면
    # 관례에 따라 `3 to 2` 가 될 수 있는데, 그건 오탈자가 아니다.
    # 다만 허용 폭은 아래 포함 검사와 **같은** effective_k 여야 한다. 여기만
    # 원시 k 를 쓰면 정수로 적힌 뒤바뀜(`4 (95% CI 5 to 3)`)이 ±1 ulp 에 묻혀
    # 통과한다 — 이 검사가 잡겠다고 명시한 바로 그 경우다.
    swap_k = effective_k(opts.k, lo, hi)
    if lo.interval(swap_k)[0] > hi.interval(swap_k)[1]:
        claim.checked = True
        claim.verdict = "불일치"
        results.append((claim, Finding(
            "치명", ln.no, ln.section, "신뢰구간 정합", quote,
            f"{label} {lo.raw} – {hi.raw}", "-",
            f"신뢰구간의 하한({lo.raw})이 상한({hi.raw})보다 큽니다. 두 값이 뒤바뀌었습니다.",
            message_en=(f"The CI lower bound ({lo.raw}) exceeds the upper bound ({hi.raw}); "
                        f"the two are swapped."),
        )))
    elif point is None:
        claim.skip_reason = SKIP_REASONS["ambiguous"]
        claim.verdict = "건너뜀"
        claim.note = "CI 앞의 점추정치를 특정할 수 없음"
        results.append((claim, None))
    else:
        claim.checked = True
        claim.reported = f"{point.raw} ({label} {lo.raw} – {hi.raw})"
        k = effective_k(opts.k, point, lo, hi)
        # 반올림 안에서 순서가 뒤집혔을 수 있으므로(`3 to 2` ← 참값 [2.1, 2.9])
        # 포함 여부는 두 경계의 min/max 로 본다.
        bounds = sorted((lo.interval(k), hi.interval(k)))
        p_lo, p_hi = point.interval(k)
        inside = p_lo <= max(bounds[0][1], bounds[1][1]) and \
            min(bounds[0][0], bounds[1][0]) <= p_hi
        if inside:
            claim.verdict = "일치"
            results.append((claim, None))
        else:
            claim.verdict = "불일치"
            claim.recomputed = f"[{lo.raw}, {hi.raw}]"
            results.append((claim, Finding(
                "치명", ln.no, ln.section, "신뢰구간 정합", quote,
                f"{point.raw} ({label} {lo.raw} – {hi.raw})", f"[{lo.raw}, {hi.raw}]",
                f"점추정치 {point.raw} 가 신뢰구간 [{lo.raw}, {hi.raw}] 밖에 있습니다."
                " 표를 옮겨 붙이면서 열이 밀렸을 때 나오는 전형적인 모양입니다.",
                message_en=(f"The point estimate {point.raw} lies outside its own confidence "
                            f"interval [{lo.raw}, {hi.raw}]."),
            )))

    # -- CI ↔ p 모순 --------------------------------------------------------
    if not single_ci:
        return results
    ps = find_pvalues(sentence)
    if len(ps) != 1:
        return results
    null = _null_value(sentence, sentence[:m.start()])
    if null is None:
        results.append((Claim(ln.no, ln.section, "ci", "CI–p 정합", quote,
                              skip_reason=SKIP_REASONS["ambiguous"], verdict="건너뜀",
                              note="차이/비 중 무엇의 CI 인지 알 수 없어 귀무값 미상"), None))
        return results
    pv = ps[0]
    # 경계값도 반올림된 값이다. 같은 '넓힌 구간'으로 포함/불포함을 **둘 다** 판정하면
    # 한쪽이 반드시 틀린다(예전 버전은 CI 0.1–0.9 를 "0 을 포함한다"고 말했다).
    #   확실히 포함  = 넓힌 하한의 위끝 ≤ 귀무값 ≤ 넓힌 상한의 아래끝
    #   확실히 불포함 = 귀무값이 넓힌 구간 밖
    #   그 사이(경계에서 1 ulp 이내)는 판정하지 않는다.
    definitely_in = lo.interval(opts.k)[1] <= null <= hi.interval(opts.k)[0]
    definitely_out = not (lo.interval(opts.k)[0] <= null <= hi.interval(opts.k)[1])
    if not (definitely_in or definitely_out):
        results.append((Claim(ln.no, ln.section, "ci", "CI–p 정합", quote,
                              skip_reason=SKIP_REASONS["ambiguous"], verdict="건너뜀",
                              note=f"귀무값 {null:g} 이 CI 경계에서 반올림 오차 안에 있어 판정 보류"),
                        None))
        return results
    includes_null = definitely_in
    p_significant = _p_is_significant(pv, opts.alpha)
    if p_significant is None:
        return results
    pclaim = Claim(ln.no, ln.section, "ci", "CI–p 정합", quote, checked=True,
                   reported=f"{label} {lo.raw} – {hi.raw}, {pv.raw}",
                   recomputed=f"귀무값 {null:g} {'포함' if includes_null else '미포함'}")
    if includes_null == p_significant:
        pclaim.verdict = "불일치"
        if includes_null:
            msg = (f"신뢰구간 [{lo.raw}, {hi.raw}] 이 귀무값 {null:g} 을 포함하는데"
                   f" {pv.raw} 로 유의하다고 되어 있습니다.")
        else:
            msg = (f"신뢰구간 [{lo.raw}, {hi.raw}] 이 귀무값 {null:g} 을 포함하지 않는데"
                   f" {pv.raw} 로 유의하지 않다고 되어 있습니다.")
        results.append((pclaim, Finding(
            "경고", ln.no, ln.section, "CI–p 정합", quote,
            f"{label} {lo.raw} – {hi.raw}, {pv.raw}",
            f"귀무값 {null:g} {'포함' if includes_null else '미포함'}",
            msg + " 검정과 구간추정의 방법이 다르면(정확검정 vs Wald 등) 정당하게 갈릴 수"
                  " 있으니 확인하세요.",
            downgraded="검정·구간추정 방법 차이로 정당할 수 있어 경고",
            message_en=(
                f"The CI [{lo.raw}, {hi.raw}] "
                f"{'includes' if includes_null else 'excludes'} the null value {null:g}, "
                f"which contradicts {pv.raw}. Different test/interval methods can legitimately "
                f"disagree — please check."),
        )))
    else:
        pclaim.verdict = "일치"
        results.append((pclaim, None))
    return results


def _point_estimate(sentence: str, ci_start: int) -> Optional[Reported]:
    head = sentence[:ci_start]
    # 괄호·대괄호를 열었으면 그 앞까지가 점추정치 자리다
    head = re.sub(r"[\(\[]\s*$", "", head)
    m = _POINT_BEFORE.search(head)
    if not m:
        return None
    return parse_number(m.group("point"))


def _p_is_significant(pv, alpha: float) -> Optional[bool]:
    if pv.op in ("<", "<="):
        return True if pv.value.value <= alpha else None
    if pv.op in (">", ">="):
        return False if pv.value.value >= alpha else None
    if abs(pv.value.value - alpha) < 1e-12:
        return None  # 정확히 경계면 판단하지 않는다
    return pv.value.value < alpha
