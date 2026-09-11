"""Methods 문단 초안(한국어/영어).

**결과를 해석하는 문장은 만들지 않는다.** 채점 규칙·분모·임계차 산출 방식처럼
이 툴이 실제로 한 계산만 문장으로 옮긴다. 숫자를 넣을 자리는 그대로 둔다.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from .safeio import sanitize_line
from .spec import SubtestSpec

_RULE_KR = {
    "전체": "제시 자극과 응답 전사가 완전히 같을 때 정답",
    "초중종": "해당 음절의 초성·중성·종성을 각각 1점으로 채점(만점 3점)",
}
_RULE_EN = {
    "전체": "scored correct only when the transcribed response matched the "
            "stimulus exactly",
    "초중종": "scored 0-3 by counting matches of the onset, nucleus and coda",
}


def _rule_kr(spec: SubtestSpec) -> str:
    rule = spec.rule
    if rule.kind in _RULE_KR:
        return _RULE_KR[rule.kind]
    return "%d번째 음절의 %s만 일치하면 정답" % (rule.syllable, rule.position)


def _rule_en(spec: SubtestSpec) -> str:
    rule = spec.rule
    if rule.kind in _RULE_EN:
        return _RULE_EN[rule.kind]
    pos = {"초성": "onset consonant", "중성": "vowel nucleus",
           "종성": "coda consonant"}[rule.position]
    return ("scored correct when the %s of syllable %d matched the target"
            % (pos, rule.syllable))


def build(specs: Sequence[SubtestSpec], denominators: Dict[str, int],
          alpha: float, min_count: int, features_used: bool,
          n_subjects: int, tool_version: str) -> str:
    kr: List[str] = ["# Methods 초안 (한국어)", ""]
    kr.append("## 채점 재계산")
    kr.append(
        "사람이 문항 단위로 채점한 기록에서 하위검사별 정답수와 분모를 다시 "
        "계산하였다. 분모는 하위검사마다 독립적으로 산출하였으며, 한 블록을 두 "
        "번(단어 단위·음소 단위) 채점한 경우 두 값을 서로 다른 지표로 분리하여 "
        "보고하였다.")
    for spec in specs:
        denom = denominators.get(spec.label)
        kr.append("- %s: %s. 문항 %s개, 만점 %d점, 분모 %s."
                  % (sanitize_line(spec.label), _rule_kr(spec),
                     spec.n_items if spec.n_items else "가변",
                     spec.max_points,
                     denom if denom else "피험자별 산출"))
    kr.append("")
    kr.append("## 목표음소 규칙 대조")
    kr.append(
        "한글 음절을 유니코드 완성형 산식(0xAC00 + (초성 × 21 + 중성) × 28 + 종성)"
        "으로 초성·중성·종성으로 분해한 뒤, 위 규칙으로 매긴 점수를 사람이 매긴 "
        "점수와 문항 단위로 대조하였다. 두 점수가 갈리는 문항은 원 기록 확인 "
        "대상으로만 표시하였고, 어느 쪽이 옳은지는 판정하지 않았다.")
    kr.append("")
    kr.append("## 개인 내 변화의 해석 범위")
    kr.append(
        "말지각 점수를 이항변수로 보는 모형(Thornton & Raffin, 1978)을 참고하여, "
        "**사전 점수 하나**의 정확 이항 %g%% 신뢰구간(Clopper–Pearson)을 계산하고 "
        "사후 점수가 그 구간 밖인지를 표시하였다." % ((1 - alpha) * 100))
    kr.append("")
    kr.append(
        "> **반드시 함께 적을 것.** 이 값은 위 논문의 임계차 표가 아니다. 그 표는 "
        "**두 점수의 차**가 갖는 분포에서 나오는데, 여기서는 사후 점수 자체의 "
        "표본오차를 넣지 않았다. 따라서 이 구간은 두 점수 임계차보다 **좁고**, "
        "같은 자료에서 '오차 범위를 벗어남'으로 표시되는 사례가 **더 많다**. "
        "논문에 쓸 때는 (가) 이 계산 방식을 그대로 적거나, (나) 원 논문의 임계차 "
        "표를 직접 찾아 쓰되 이 값을 그 표의 값이라고 적지 않는다.")
    kr.append("")
    kr.append(
        "문항이 서로 독립인 0/1 채점(단어 단위)에만 적용하였고, 한 단어 안의 음소 "
        "점수는 서로 독립이 아니므로 적용하지 않았다. 임계차는 하락·상승 방향을 "
        "따로 보고하였으며, 바닥·천장에 붙어 그 방향으로 구간을 벗어날 수 없는 "
        "경우에는 값을 적지 않았다.")
    kr.append("")
    kr.append("## 음소 혼동")
    kr.append(
        "목표 자모와 응답 자모의 쌍을 초성·중성·종성별로 집계하였다. 관측 %d회 "
        "미만인 칸은 표에서 제외하고 제외된 칸 수를 함께 보고하였다. %s"
        % (min_count,
           "조음위치·조음방법 묶음은 외부 자질표를 입력받아 집계하였다."
           if features_used else
           "조음위치·조음방법에 따른 묶음 집계는 수행하지 않았다."))
    kr.append("")
    kr.append("## 도구")
    kr.append("재계산·대조는 jamoscore %s(외부 의존성 없음, 오프라인)로 "
              "수행하였으며, 원본 파일은 읽기 전용으로만 접근하였다. 대상 "
              "피험자 시트 %d개." % (tool_version, n_subjects))
    kr.append("")

    en: List[str] = ["# Methods draft (English)", ""]
    en.append("## Rescoring")
    en.append(
        "Item-level scores recorded by the examiners were re-tabulated to "
        "recover the number of correct items and the denominator for each "
        "subtest. Denominators were derived independently per subtest; where a "
        "block had been scored twice (word level and phoneme level) the two "
        "were reported as separate measures rather than pooled.")
    for spec in specs:
        en.append("- %s: %s (%s items, maximum %d points per item)."
                  % (sanitize_line(spec.label), _rule_en(spec),
                     spec.n_items if spec.n_items else "variable",
                     spec.max_points))
    en.append("")
    en.append("## Target-phoneme rule check")
    en.append(
        "Korean syllables were decomposed into onset, nucleus and coda using "
        "the Unicode composition formula (0xAC00 + (onset x 21 + nucleus) x 28 "
        "+ coda). Scores derived from the rules above were compared item by "
        "item with the examiner scores. Items where the two disagreed were "
        "flagged for source-record review only; the tool does not adjudicate "
        "which score is correct.")
    en.append("")
    en.append("## Within-subject change")
    en.append(
        "With reference to the binomial model of speech-recognition scores "
        "(Thornton & Raffin, 1978, J Speech Hear Res 21:507-518), an exact "
        "binomial (Clopper-Pearson) %g%% confidence interval was computed for "
        "each **pre-treatment score alone**, and the post-treatment score was "
        "classified as inside or outside that interval." % ((1 - alpha) * 100))
    en.append("")
    en.append(
        "> **State this alongside the result.** These are NOT the published "
        "critical differences. Those are derived from the distribution of the "
        "*difference between two scores*; the interval used here omits the "
        "sampling error of the post-treatment score and is therefore NARROWER, "
        "flagging MORE changes as exceeding test-retest error than the "
        "published table would. Either describe the computation as done here, "
        "or consult the original table directly - but do not report these "
        "values as if they came from that table.")
    en.append("")
    en.append(
        "This was applied only to dichotomously scored (0/1) word-level items; "
        "phoneme scores within a single word are not independent and were "
        "excluded. Critical differences are reported separately for decreases "
        "and increases, and are left blank in a direction where the score is at "
        "floor or ceiling and cannot leave the interval that way.")
    en.append("")
    en.append("## Phoneme confusions")
    en.append(
        "Target-response jamo pairs were tabulated separately for onsets, "
        "nuclei and codas. Cells observed fewer than %d times were withheld "
        "from the tables and the number of withheld cells was reported. %s"
        % (min_count,
           "Place and manner groupings were computed from a supplied feature "
           "table." if features_used else
           "No place- or manner-of-articulation grouping was performed."))
    en.append("")
    en.append("## Software")
    en.append("Rescoring and cross-checking were performed with jamoscore %s "
              "(no external dependencies, offline); source workbooks were "
              "opened read-only. %d participant sheets."
              % (tool_version, n_subjects))
    en.append("")
    return "\n".join(kr + en)
