"""변화량 일치 — `기저 18.4 → 12주 11.2 (변화 −7.4)` 에서 −7.2 여야 하는 경우.

세 값(사전·사후·변화)이 **한 문장 안에** 다 있을 때만 본다. 문장을 넘어가면
같은 지표의 값이라는 보장이 없고, 그 순간 오탐 공장이 된다.

부호 관례는 원고마다 다르다("7.2 감소" vs "−7.2"). 그래서 **크기가 맞으면
통과**시키고, 크기까지 다를 때만 지적한다. 부호만 다르면 정보 등급으로 남긴다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .docio import Line
from .model import SKIP_REASONS, Claim, Finding
from .options import Options
from .rounding import Reported, fmt, parse_number
from .textutil import normalize, sentences, snippet

__all__ = ["check_deltas"]

_NUM = r"[-+]?\d{1,7}(?:\.\d{1,4})?"

# 사전 → 사후.
# 화살표 뒤에 시점 라벨이 끼어드는 일이 흔하다("18.4 → 12주 11.2"). 라벨을 건너뛰지
# 않으면 12 를 사후값으로 읽어 버려, 정직한 원고에서 헛된 치명이 나온다. 다만
# 라벨은 **숫자만으로 이루어지면 안 된다**(그건 진짜 값이므로).
_LABEL = r"(?:[^\s]*[^\s\d.,][^\s]*\s+){0,2}"
_ARROW = re.compile(
    rf"(?P<pre>{_NUM})\s*(?:점|명|%)?\s*(?:→|->|⟶|=>)\s*{_LABEL}(?P<post>{_NUM})")
_FROM_TO = re.compile(
    rf"from\s+(?P<pre>{_NUM})\s*(?:±\s*{_NUM}\s*)?to\s+{_LABEL}(?P<post>{_NUM})",
    re.IGNORECASE,
)
_KO_FROM_TO = re.compile(
    rf"(?P<pre>{_NUM})\s*(?:점|명|%)?\s*에서\s+{_LABEL}(?P<post>{_NUM})\s*(?:점|명|%)?\s*(?:으?로)",
)

# 변화량 표기
_CHANGE = re.compile(
    r"(?:변화(?:량|값)?|변화폭|차이|감소(?:폭|량)?|증가(?:폭|량)?|"
    r"change|difference|reduction|reduced by|decrease(?:d by)?|increase(?:d by)?|"
    r"mean difference|MD|Δ|delta)\s*(?:은|는|이|가|of|by)?\s*[=:]?\s*"
    rf"(?P<val>{_NUM})",
    re.IGNORECASE,
)

# 이 수식어가 붙은 "차이"는 **사후−사전이 아니다.** 군간 차이·최소 임상적 중요차·
# 상대위험 감소·목표 대비 차이를 변화량으로 읽으면, 산술이 완벽한 원고에서
# 치명이 쏟아진다(적대적 검토에서 실제로 나온 1순위 오탐 경로).
_NOT_A_CHANGE = (
    # 군간 비교
    "군간", "군 간", "그룹간", "그룹 간", "집단간", "집단 간", "대조군과", "위약군과",
    "sham", "placebo", "between-group", "between group", "versus", " vs", "vs.",
    "compared with", "compared to",
    # 상대적 지표
    "상대위험", "상대 위험", "relative risk", "risk reduction", "rrr",
    # 임상적 기준값(변화가 아니라 문턱이다)
    "최소 임상적", "임상적 중요", "임상적으로 의미", "mcid", "minimal clinically",
    "minimal important", "minimally important", "clinically important difference",
    "smallest detectable", "minimal detectable", "최소검출가능", "최소 검출 가능",
    "반응 기준", "기준값", "기준인", "문턱", "threshold", "margin", "비열등",
    "목표 대비", "기준 대비", "차이의 차이",
)

# 절 경계. 후보 주변을 고정 글자수로 보면 이웃 절의 단어에 오염된다 —
# `(변화 -6.2, 표준편차 4.1)` 에서 26자 창은 두 절을 함께 삼킨다.
_CLAUSE_SPLIT = re.compile(r"[,;()\[\]{}]|(?:\s그리고\s)|(?:\s및\s)|(?:\sand\s)")


def _clause_of(sentence: str, span: Tuple[int, int]) -> str:
    """후보가 속한 절만 잘라 낸다."""
    start = 0
    for m in _CLAUSE_SPLIT.finditer(sentence):
        if m.end() <= span[0]:
            start = m.end()
        elif m.start() >= span[1]:
            return sentence[start:m.start()]
    return sentence[start:]


def _blocked(sentence: str, span: Tuple[int, int]) -> bool:
    """이 변화량 후보가 '사후−사전'이 아니라는 신호가 **같은 절에** 있는가."""
    return any(word in _clause_of(sentence, span).lower() for word in _NOT_A_CHANGE)


def _pair(sentence: str) -> Optional[Tuple[Reported, Reported, Tuple[int, int]]]:
    for pattern in (_ARROW, _FROM_TO, _KO_FROM_TO):
        m = pattern.search(sentence)
        if not m:
            continue
        pre = parse_number(m.group("pre"))
        post = parse_number(m.group("post"))
        if pre is None or post is None:
            continue
        return pre, post, m.span()
    return None


def check_deltas(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        if ln.section == "References":
            continue
        text = normalize(ln.text)
        if not text.strip():
            continue
        for start, _end, sentence in sentences(text):
            pair = _pair(sentence)
            if pair is None:
                continue
            pre, post, pair_span = pair
            change_match, n_candidates = _find_change(sentence, pair_span)
            if change_match is None:
                if n_candidates:
                    # 후보가 있었는데 판단을 접었다면 **그 사실을 남긴다.**
                    # 조용히 넘기면 커버리지 자백이 거짓말이 된다.
                    out.append((Claim(
                        ln.no, ln.section, "delta", "변화량 일치",
                        snippet(text, start + pair_span[0], start + pair_span[1])
                        if opts.quote else "",
                        skip_reason=SKIP_REASONS["ambiguous"], verdict="건너뜀",
                        reported=f"{pre.raw} → {post.raw}",
                        note="같은 문장에 성격이 다른 '차이'가 섞여 있어 판단 보류"), None))
                continue
            change, change_span = change_match
            span = (start + min(pair_span[0], change_span[0]),
                    start + max(pair_span[1], change_span[1]))
            quote = snippet(text, span[0], span[1]) if opts.quote else ""
            claim = Claim(ln.no, ln.section, "delta", "변화량 일치", quote, checked=True,
                          reported=f"{pre.raw} → {post.raw}, 변화 {change.raw}")
            # 구간 산술: 실제 변화량이 있을 수 있는 범위
            # 여기서는 반올림 폭을 좁히지 않는다. 사전·사후·변화 **세 값**이
            # 각각 다른 관례로 인쇄될 수 있어, 절반으로 좁히면 정직한 정수
            # 보고에서 오탐이 난다(적대적 검토 라운드 2에서 5.3% 측정).
            k = opts.k
            pre_lo, pre_hi = pre.interval(k)
            post_lo, post_hi = post.interval(k)
            diff_lo, diff_hi = post_lo - pre_hi, post_hi - pre_lo
            ch_lo, ch_hi = change.interval(k)
            nominal = post.value - pre.value
            claim.recomputed = f"{fmt(nominal, 4)}"
            signed_ok = ch_lo <= diff_hi and diff_lo <= ch_hi
            mag_ok = (-ch_hi) <= diff_hi and diff_lo <= (-ch_lo)
            if signed_ok:
                claim.verdict = "일치"
                out.append((claim, None))
                continue
            if mag_ok:
                claim.verdict = "일치(부호 관례)"
                claim.note = "크기는 맞고 부호 관례만 다름"
                out.append((claim, None))
                continue
            claim.verdict = "불일치"
            out.append((claim, Finding(
                "치명", ln.no, ln.section, "변화량 일치", quote,
                f"변화 {change.raw}", fmt(nominal, 4),
                f"{post.raw} − {pre.raw} = {fmt(nominal, 4)} 인데 변화량이 {change.raw} 로"
                f" 적혀 있습니다 (반올림을 최대한 허용해도 가능한 범위는"
                f" {fmt(diff_lo, 4)} ~ {fmt(diff_hi, 4)}).",
                message_en=(f"{post.raw} − {pre.raw} = {fmt(nominal, 4)}, but the change is "
                            f"reported as {change.raw} (achievable range "
                            f"{fmt(diff_lo, 4)} to {fmt(diff_hi, 4)})."),
            )))
    return out


def _find_change(sentence: str, pair_span: Tuple[int, int]):
    """이 문장에서 '사후 − 사전'을 뜻하는 변화량을 **하나만 확실히** 찾는다.

    셋 다 지킨다.
      · 사전/사후 값 자체를 변화량으로 오인하지 않는다(구간이 겹치면 버린다).
      · "군간 차이"·"최소 임상적 중요 차이" 같은 다른 뜻의 차이는 버린다.
      · 그러고도 후보가 둘 이상 남으면 **어느 쪽인지 모르므로 검사하지 않는다.**
    """
    candidates = []
    blocked = 0
    for m in _CHANGE.finditer(sentence):
        vspan = m.span("val")
        if vspan[0] < pair_span[1] and pair_span[0] < vspan[1]:
            continue
        value = parse_number(m.group("val"))
        if value is None:
            continue
        if _blocked(sentence, m.span()):
            blocked += 1
            continue
        candidates.append((value, m.span()))
    # 걸러 낸 후보가 하나라도 있으면 이 문장은 여러 종류의 '차이'를 말하고 있다.
    # 남은 하나를 사후−사전으로 단정하면, 정확히 그 오탐이 다시 살아난다.
    if len(candidates) != 1 or blocked:
        return None, len(candidates) + blocked
    return candidates[0], 1
