"""유의성 경계 문구 — `p = .07` 인데 "유의하게 감소했다" 라고 쓴 경우.

리비전에서 분석을 다시 돌렸는데 문장은 예전 그대로일 때 정확히 이 모양이
남는다. 숫자만 갱신하고 서술은 고치지 않는 것이다.

**오탐을 막는 문지기가 세 개** 있다.
  · 한 문장에 p 가 둘 이상이면 어느 p 를 가리키는지 알 수 없으므로 건너뛴다.
  · "clinically significant"·"임상적으로 유의미한" 은 통계 이야기가 아니다.
  · 부정 표현("not significant", "유의하지 않")을 먼저 찾아 긍정으로 오독하지 않는다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .docio import Line
from .model import Claim, Finding
from .options import Options
from .pvalues import find_pvalues
from .textutil import (
    NONSIGNIFICANT_WORDS,
    SIGNIFICANT_WORDS,
    normalize,
    sentences,
    snippet,
)

__all__ = ["check_language"]

# 통계적 유의성이 아닌 '중요하다'는 뜻의 용법
_NON_STATISTICAL = (
    "clinically significant", "clinical significance", "clinically meaningful",
    "임상적으로 유의", "임상적 유의", "임상적으로 의미", "실질적으로 유의",
)

# 완곡한 유의성 표현은 **오류가 아니라 관례**다. "marginally significant (p = .058)"
# 를 치명으로 찍으면 정직한 원고에서 지적이 쏟아진다.
_HEDGES = (
    "marginally", "borderline", "nearly", "almost", "approaching", "trend toward",
    "trend towards", "a trend", "경계", "경향", "근소하게", "가까스로",
)
# 완곡 표현은 **경계 근처에서만** 관례다. `marginally significant (p = 0.51)` 은
# 완곡이 아니라 오류이므로 그냥 넘기면 안 된다.
HEDGE_MAX_MULTIPLE = 3.0

# 한 문장 안에서 **두 결과를 대비**하면 하나뿐인 p 가 어느 쪽 것인지 알 수 없다.
# 다만 "위약군에 비해 유의하게 감소" 같은 비교 표현은 결과가 하나뿐이므로 대비가
# 아니다. `에 비해`·`그러나`·`한편` 을 여기에 넣었더니 한국어 결과문의 절반이
# 검사되지 않았다(적대적 검토 라운드 2에서 7/20 미탐 측정). 진짜 대비 접속사만 둔다.
_CONTRASTIVE = (
    " while ", " whereas ", " although ", " though ", ", but ", "; however",
    ", however", "반면에", "반면,", "인 반면",
)


def _claimed_direction(sentence: str) -> Optional[bool]:
    """저자가 유의하다고(True) / 유의하지 않다고(False) 말하는가. 모르면 None."""
    low = sentence.lower()
    for phrase in NONSIGNIFICANT_WORDS:
        if phrase.lower() in low:
            return False
    for phrase in SIGNIFICANT_WORDS:
        idx = low.find(phrase.lower())
        while idx != -1:
            head = low[max(0, idx - 24): idx]
            if not any(word in head for word in _NON_STATISTICAL_HEADS):
                return True
            idx = low.find(phrase.lower(), idx + 1)
    return None


_NON_STATISTICAL_HEADS = ("clinical", "임상", "실질")


def check_language(lines: List[Line], opts: Options) -> List[Tuple[Claim, Optional[Finding]]]:
    out: List[Tuple[Claim, Optional[Finding]]] = []
    for ln in lines:
        if ln.section in ("References", "Other"):
            continue
        text = normalize(ln.text)
        if not text.strip():
            continue
        for start, _end, sentence in sentences(text):
            low = sentence.lower()
            if any(phrase in low for phrase in _NON_STATISTICAL) and \
                    _claimed_direction(sentence) is not False:
                continue
            hedged = any(word in low for word in _HEDGES)
            if any(word in low for word in _CONTRASTIVE):
                continue  # 두 결과를 대비하는 문장 — p 가 어느 쪽 것인지 모른다
            ps = find_pvalues(sentence)
            if len(ps) != 1:
                continue
            claimed = _claimed_direction(sentence)
            if claimed is None:
                continue
            pv = ps[0]
            span = (start + pv.span[0], start + pv.span[1])
            quote = snippet(text, span[0], span[1]) if opts.quote else ""
            actual = _significant_by_number(pv, opts.alpha)
            claim = Claim(ln.no, ln.section, "wording", "유의성 문구", quote,
                          reported=f"{pv.raw} + '{'유의' if claimed else '유의하지 않음'}'")
            if actual is None:
                claim.verdict = "건너뜀"
                claim.skip_reason = "표기 불명확"
                claim.note = "p 가 기준값과 같아 판정 보류"
                out.append((claim, None))
                continue
            if hedged and opts.alpha < pv.value.value <= opts.alpha * HEDGE_MAX_MULTIPLE:
                continue  # "marginally significant (p = .058)" — 경계 근처의 관례적 표현
            claim.checked = True
            claim.recomputed = f"p 기준 α = {opts.alpha:g} → {'유의' if actual else '유의하지 않음'}"
            if actual == claimed:
                claim.verdict = "일치"
                out.append((claim, None))
                continue
            claim.verdict = "불일치"
            if claimed:
                msg = (f"{pv.raw} 는 α = {opts.alpha:g} 기준으로 유의하지 않은데"
                       " 같은 문장에서 유의하다고 서술했습니다."
                       " 분석을 다시 돌린 뒤 문장을 고치지 않았을 때 나오는 모양입니다.")
            else:
                msg = (f"{pv.raw} 는 α = {opts.alpha:g} 기준으로 유의한데"
                       " 같은 문장에서 유의하지 않다고 서술했습니다.")
            msg_en = (
                f"{pv.raw} is not significant at α = {opts.alpha:g}, yet the same sentence "
                f"describes the result as significant."
                if claimed else
                f"{pv.raw} is significant at α = {opts.alpha:g}, yet the same sentence "
                f"describes the result as non-significant."
            )
            out.append((claim, Finding(
                "치명", ln.no, ln.section, "유의성 문구", quote,
                claim.reported, claim.recomputed, msg, message_en=msg_en,
            )))
    return out


def _significant_by_number(pv, alpha: float) -> Optional[bool]:
    """적힌 p 값 **그대로** 유의한가. 경계와 같으면 판단하지 않는다."""
    value = pv.value.value
    if pv.op in ("<", "<="):
        if value <= alpha:
            return True
        return None  # p < .10 같은 표기는 유의 여부를 말하지 않는다
    if pv.op in (">", ">="):
        if value >= alpha:
            return False
        return None
    if abs(value - alpha) < 1e-12:
        return None
    return value < alpha
