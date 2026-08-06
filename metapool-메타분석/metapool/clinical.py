"""통합 상대효과를 임상적으로 읽을 수 있는 절대효과로 옮긴다.

상대지표(OR·RR)는 "몇 배"만 말해 주므로, 심사자와 임상의는 늘
"그래서 100명 치료하면 몇 명이 이득을 보나?"를 묻는다. GRADE 요약표의
'절대효과(absolute effect)' 칸이 바로 이것이다.

가정 대조군 위험(baseline/assumed control risk, ACR)을 주면
- OR: EER = ACR·OR / (1 - ACR + ACR·OR)
- RR: EER = ACR·RR
- RD: EER = ACR + RD
로 실험군 위험을 계산하고, 위험차(ARD)와 NNT = 1/|ARD| 를 만든다.

주의: ACR 은 **가정값**이다. 기본값으로는 포함된 연구들의 대조군 사건률
합계를 쓰지만, 실제 진료 상황의 기저 위험이 다르면 NNT 도 달라진다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .effects import Study

__all__ = ["AbsoluteEffect", "NNT_MEASURES", "pooled_control_risk", "absolute_effect",
           "format_absolute"]

#: 절대효과로 옮길 수 있는 지표
NNT_MEASURES = ("or", "rr", "rd")


@dataclass
class AbsoluteEffect:
    """가정 대조군 위험에서 유도한 절대효과."""

    baseline_risk: float  # 가정 대조군 위험 (0~1)
    baseline_source: str  # 'data' | 'user'
    exp_risk: float  # 실험군 위험 추정치
    risk_diff: float  # 실험군 - 대조군 (음수면 사건이 줄어듦)
    risk_diff_low: float
    risk_diff_high: float
    nnt: Optional[float]  # |1/위험차|. 구간이 0을 지나면 점추정만 의미가 있다.
    nnt_low: Optional[float]
    nnt_high: Optional[float]
    #: 위험차 신뢰구간이 0을 포함하면 True (NNT 구간이 무한대를 지난다)
    spans_null: bool
    #: 사건이 늘어나는 방향이면 True → NNT 가 아니라 NNH 로 읽어야 한다
    is_harm: bool
    per_1000: float  # 1000명당 사건 수 변화


def pooled_control_risk(studies: Sequence[Study]) -> Optional[float]:
    """포함된 연구들의 대조군 사건률(사건수 합 / 표본수 합).

    2x2 원자료에서 온 연구에만 ``events2``/``n2`` 가 들어 있다. 하나도 없으면 None.
    """
    ev = 0.0
    n = 0.0
    for s in studies:
        if "events2" in s.extra and "n2" in s.extra:
            ev += s.extra["events2"]
            n += s.extra["n2"]
    if n <= 0:
        return None
    risk = ev / n
    if not (0.0 <= risk <= 1.0):  # pragma: no cover - 입력 검증에서 걸러짐
        return None
    return risk


def _exp_risk(measure: str, effect: float, acr: float) -> float:
    """분석 척도 값(log OR/log RR/RD)과 대조군 위험에서 실험군 위험."""
    if measure == "or":
        odds = acr / (1.0 - acr)
        try:
            new_odds = odds * math.exp(effect)
        except OverflowError:
            return 1.0
        if not math.isfinite(new_odds):
            return 1.0
        return new_odds / (1.0 + new_odds)
    if measure == "rr":
        try:
            value = acr * math.exp(effect)
        except OverflowError:
            return 1.0
        return min(1.0, max(0.0, value))
    if measure == "rd":
        return min(1.0, max(0.0, acr + effect))
    raise ValueError("절대효과를 계산할 수 없는 지표입니다: %r" % (measure,))


def absolute_effect(
    measure: str,
    estimate: float,
    ci_low: float,
    ci_high: float,
    baseline_risk: float,
    baseline_source: str = "user",
) -> Optional[AbsoluteEffect]:
    """통합 추정치(분석 척도)를 절대 위험차·NNT 로 옮긴다.

    ``baseline_risk`` 는 0 초과 1 미만이어야 한다(0 이나 1 이면 절대효과가
    정의되지 않거나 의미가 없다). 계산할 수 없으면 None.
    """
    if measure not in NNT_MEASURES:
        return None
    if not math.isfinite(baseline_risk) or not (0.0 < baseline_risk < 1.0):
        return None
    if not all(math.isfinite(v) for v in (estimate, ci_low, ci_high)):
        return None

    eer = _exp_risk(measure, estimate, baseline_risk)
    rd = eer - baseline_risk
    bounds = sorted(
        _exp_risk(measure, e, baseline_risk) - baseline_risk for e in (ci_low, ci_high)
    )
    rd_low, rd_high = bounds

    spans_null = rd_low <= 0.0 <= rd_high
    nnt = (1.0 / abs(rd)) if rd != 0 else None
    if spans_null:
        nnt_low = nnt_high = None
    else:
        # 위험차가 클수록 NNT 는 작다 — 구간의 순서가 뒤집힌다.
        smaller_rd, larger_rd = sorted((abs(rd_low), abs(rd_high)))
        nnt_low = 1.0 / larger_rd if larger_rd > 0 else None
        nnt_high = 1.0 / smaller_rd if smaller_rd > 0 else None
    return AbsoluteEffect(
        baseline_risk=baseline_risk,
        baseline_source=baseline_source,
        exp_risk=eer,
        risk_diff=rd,
        risk_diff_low=rd_low,
        risk_diff_high=rd_high,
        nnt=nnt,
        nnt_low=nnt_low,
        nnt_high=nnt_high,
        spans_null=spans_null,
        is_harm=rd > 0,
        per_1000=1000.0 * rd,
    )


_HARM = "NNH(사건이 1건 더 생기는 데 필요한 인원)"
_BENEFIT = "NNT(1명이 이득을 보려면)"


def format_absolute(a: AbsoluteEffect, digits: int = 1) -> List[str]:
    """리포트에 넣을 사람용 문장 조각들.

    사건이 늘어나면 NNH, 줄어들면 NNT 로 이름 붙이지만, **사건이 바람직한
    결과(치료 성공·복약순응 등)라면 늘어나는 쪽이 이득**이다. 도구는
    사건의 좋고 나쁨을 알 수 없으므로 그 점을 함께 적어 둔다.
    """
    lines = [
        "가정 대조군 위험 %.1f%% (%s)일 때 실험군 위험 %.1f%%"
        % (100.0 * a.baseline_risk, "포함 연구의 대조군 사건률" if a.baseline_source == "data"
           else "사용자 지정", 100.0 * a.exp_risk),
        "절대 위험차 %+.1f%%p → 1000명당 %+.*f명"
        % (100.0 * a.risk_diff, 0 if abs(a.per_1000) >= 10 else digits, a.per_1000),
    ]
    if a.nnt is None:
        lines.append("위험차가 0이라 NNT를 정의할 수 없습니다.")
    elif a.spans_null:
        lines.append(
            "%s ≈ %.0f (단, 신뢰구간이 '차이 없음'을 포함해 구간은 무한대를 지납니다)"
            % (_HARM if a.is_harm else _BENEFIT, a.nnt)
        )
    else:
        lines.append(
            "%s ≈ %.0f (%.0f ~ %.0f)"
            % (_HARM if a.is_harm else _BENEFIT, a.nnt, a.nnt_low, a.nnt_high)
        )
    lines.append(
        "※ '사건'이 바람직한 결과(치료 성공 등)라면 위 NNH/NNT 이름을 뒤집어 읽으세요 — "
        "도구는 사건의 좋고 나쁨을 알 수 없습니다."
    )
    return lines
