"""민감도 분석 문단 초안 (KR/EN).

리뷰어 응답서와 Methods/Results 에 그대로 붙일 수 있는 형태로 쓰되,
**실제로 돌린 것만** 쓴다. 이 파일이 만드는 문장은 사람이 논문에 붙일 물건이라,
여기서의 과장은 그대로 논문의 거짓말이 된다. 그래서 두 가지를 지킨다:

1. **축 목록은 실제 격자에서 만든다.** 결측 처리 축이 1수준뿐인 설계(two-group,
   corl)에서 "결측 처리를 교차했다"고 쓰면 안 된다.
2. **결과 문장은 실제로 발생한 뒤집힘 코드에서 만든다.** 등급(주의/취약)만 보고
   "효과크기 등급이 바뀌었다"고 단정하면, ② 만 발생한 경우 *유의성이 유지됐다*는
   정반대 문장이 나간다(적대적 검토에서 실제로 잡힌 사고).
"""

from typing import Dict, List

from .analyze import Analysis
from .prep import MISSING_LEVELS, PIPELINE_ORDER

__all__ = ["draft_korean", "draft_english", "axis_phrases", "flip_code_counts"]

_DESIGN_EN = {
    "two-group": "the between-group comparison",
    "paired": "the pre–post within-subject comparison",
    "corr": "the correlation",
}
_DESIGN_KR = {
    "two-group": "군간 비교",
    "paired": "전후 대응 비교",
    "corr": "상관",
}

_CODE_KR = {
    "①": "유의성이 사라진",
    "②": "비유의에서 유의로 바뀐",
    "③": "효과 방향이 반대가 된",
    "④": "효과크기 등급이 달라진",
}
_CODE_EN = {
    "①": "statistical significance was lost",
    "②": "a non-significant result became significant",
    "③": "the direction of the effect reversed",
    "④": "the effect-size magnitude category changed",
}


def flip_code_counts(analysis: Analysis) -> Dict[str, int]:
    """실제로 발생한 뒤집힘 코드별 시나리오 수 (①~④)."""
    counts: Dict[str, int] = {}
    for judged in analysis.flipped:
        for code in sorted({f.code for f in judged.flips}):
            counts[code] = counts.get(code, 0) + 1
    return counts


def axis_phrases(analysis: Analysis) -> Dict[str, List[str]]:
    """실제로 흔든 축만 한/영으로. 1수준뿐인 축은 언급하지 않는다."""
    kr = ["이상치 처리 3종(미적용·±3SD·IQR 1.5배)"]
    en = ["outlier handling (none, +/-3 SD, 1.5 x IQR)"]
    if analysis.spec.design == "paired":
        kr.append("결측 처리 %d종(완결자만·LOCF·평균대체)" % len(MISSING_LEVELS))
        en.append("missing-data handling (completers only, LOCF, mean imputation)")
    kr.append("모수/비모수 검정")
    en.append("parametric versus non-parametric tests")
    if analysis.log_axis_used:
        kr.append("로그변환 유무")
        en.append("log transformation")
    return {"kr": kr, "en": en}


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무에 따라 조사를 고른다 (`검정를` 같은 오식을 막는다)."""
    for ch in reversed(word):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            return with_final if (code - 0xAC00) % 28 else without_final
        if ch.isalnum():
            return with_final
    return without_final


def _outcome_kr(analysis: Analysis) -> str:
    counts = flip_code_counts(analysis)
    if not counts:
        # **"방향이 유지되었다"고 쓰지 않는다.** 양쪽 다 비유의라 ③④ 판정을
        # 건너뛴 경우, 효과크기 부호가 실제로 바뀌었을 수 있다(적대적 검토 실측).
        text = ("유의성 판정이 계산된 %d개 분석 명세 전부에서 기준선과 일치하였다"
                % analysis.computed)
        shifts = len(analysis.silent_effect_shifts)
        if shifts:
            text += ("; 다만 기준선과 해당 명세가 모두 비유의인 상태에서 효과크기의 "
                     "부호가 달라진 명세가 %d개 있었다" % shifts)
        return text
    parts = ["%s 명세가 %d개" % (_CODE_KR[code], counts[code])
             for code in ("①", "②", "③", "④") if code in counts]
    return "계산된 %d개 명세 중 %s 있었다" % (analysis.computed, ", ".join(parts))


def _outcome_en(analysis: Analysis) -> str:
    counts = flip_code_counts(analysis)
    if not counts:
        text = ("the statistical significance of the primary comparison was "
                "unchanged across all %d analytic specifications that could be "
                "computed" % analysis.computed)
        shifts = len(analysis.silent_effect_shifts)
        if shifts:
            text += ("; in %d specification(s) the sign of the effect size "
                     "differed, although both the baseline and those "
                     "specifications were non-significant" % shifts)
        return text
    parts = ["%s in %d specification(s)" % (_CODE_EN[code], counts[code])
             for code in ("①", "②", "③", "④") if code in counts]
    return "%s" % "; ".join(parts)


def _subject_sentence_kr(analysis: Analysis) -> List[str]:
    if analysis.loo_baseline is None:
        return []
    solo = len(analysis.solo_flippers)
    warned = len(analysis.loo_warned)
    text = ("또한 피험자를 한 명씩 제외하는 leave-one-out 분석(%d회)을 수행한 "
            "결과, 단독으로 결론(유의성 또는 효과 방향)을 바꾸는 피험자는 %d명이었다"
            % (len(analysis.loo_baseline.entries), solo))
    if warned:
        text += "; 효과크기 등급만 달라진 피험자는 %d명이었다" % warned
    return ["", text + "."]


def _subject_sentence_en(analysis: Analysis) -> List[str]:
    if analysis.loo_baseline is None:
        return []
    solo = len(analysis.solo_flippers)
    warned = len(analysis.loo_warned)
    text = ("In addition, a leave-one-out analysis excluding each participant in "
            "turn (%d re-analyses) identified %d participant(s) whose exclusion "
            "alone altered the significance or direction of the primary result"
            % (len(analysis.loo_baseline.entries), solo))
    if warned:
        text += ("; for a further %d participant(s), only the effect-size "
                 "magnitude category changed" % warned)
    return ["", text + "."]


def draft_korean(analysis: Analysis) -> List[str]:
    lines = ["**민감도 분석 (한국어 초안)**", ""]
    if analysis.undecidable_reason:
        lines.append(
            "이 자료에서는 민감도 분석을 수행할 수 없었다(%s). 아래에 문장을 "
            "만들지 않는다 — 돌리지 못한 분석을 돌렸다고 쓰게 되기 때문이다."
            % analysis.undecidable_reason
        )
        return lines
    lines.append(
        "주 분석(%s)의 결론이 분석 선택에 따라 달라지는지를 확인하기 위해, %s%s "
        "교차한 %d개 분석 명세를 전수 재계산하는 민감도 분석을 수행하였다"
        "(계산 %d개 / 계산 불가 %d개). 처리 순서는 %s 로 고정하였다. 그 결과 %s."
        % (_DESIGN_KR[analysis.spec.design],
           " · ".join(axis_phrases(analysis)["kr"]),
           _josa(axis_phrases(analysis)["kr"][-1], "을", "를"),
           analysis.total, analysis.computed, analysis.skipped,
           PIPELINE_ORDER, _outcome_kr(analysis))
    )
    lines.extend(_subject_sentence_kr(analysis))
    lines.append("")
    lines.append(
        "여기서 산출된 다수의 p 값은 서로 독립인 가설검정이 아니라 동일 가설의 "
        "재계산이므로 다중비교 보정을 적용하지 않았다."
    )
    lines.append("")
    lines.append(
        "※ 이 초안의 명세 조합은 **이 도구가 정의한 것**이지 연구계획서에 사전 "
        "명시한 것이 아니다. 논문에 쓸 때는 어느 것이 사전 명시였는지 직접 밝힐 것."
    )
    return lines


def draft_english(analysis: Analysis) -> List[str]:
    lines = ["**Sensitivity analysis (English draft)**", ""]
    if analysis.undecidable_reason:
        lines.append(
            "No sensitivity analysis could be computed for this dataset (%s). "
            "No draft sentence is produced here, because it would claim an "
            "analysis that was never run." % analysis.undecidable_reason
        )
        return lines
    lines.append(
        "To assess whether the conclusion of %s depended on analytic choices, we "
        "re-estimated the primary analysis across %d analytic specifications "
        "crossing %s (%d computed; %d not computable). Processing order was fixed "
        "as missing-data handling, then transformation, then outlier exclusion, "
        "then testing. %s."
        % (_DESIGN_EN[analysis.spec.design], analysis.total,
           ", ".join(axis_phrases(analysis)["en"]),
           analysis.computed, analysis.skipped,
           _capitalise(_outcome_en(analysis)))
    )
    lines.extend(_subject_sentence_en(analysis))
    lines.append("")
    lines.append(
        "Because these p values are repeated re-computations of the same "
        "hypothesis rather than independent tests, no multiplicity correction "
        "was applied."
    )
    lines.append("")
    lines.append(
        "NOTE: this set of specifications was defined by the tool, not "
        "pre-registered. State explicitly in your manuscript which choices were "
        "pre-specified."
    )
    return lines
