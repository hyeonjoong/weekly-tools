"""출력 — 사람이 읽는 표, 프로토콜에 붙여넣는 문장, 기계가 읽는 JSON.

표본수 계산의 결과물은 숫자 하나가 아니라 **프로토콜/IRB 문서에 들어갈 한 문단**이다.
그래서 여기서는 (1) 계산 근거를 한눈에 보는 표, (2) 한국어·영어 문장, (3) 재현에
필요한 모든 파라미터를 담은 JSON을 함께 만든다.
"""

from __future__ import annotations

import json
import math

from . import __version__
from .korean import has_final_consonant, josa as _josa

__all__ = ["render_text", "render_markdown", "render_json", "protocol_sentences"]

_has_final_consonant = has_final_consonant   # 하위 호환 별칭

_BAR = "─" * 74

#: 배분 딕셔너리 키 → 사람이 읽는 이름 (내부 키가 화면에 새어 나가지 않게)
_ALLOC_LABELS = {
    "n1": "1군", "n2": "2군", "n": "전체", "n_per_group": "군당",
    "n_per_sequence": "순서당", "total": "총",
}

def _article(phrase: str, capital: bool = True) -> str:
    """영문 검정명에 알맞은 관사를 붙인다 (복수형·고유 절차명은 The/생략)."""
    lowered = phrase.lstrip()
    if lowered.startswith(("one", "uni", "eu")):     # 발음이 자음(w/j)으로 시작
        return (("A " if capital else "a ") + phrase)
    if lowered.startswith(("TOST", "ANCOVA")):
        art = "An" if lowered[0] in "AEIOU" else "The"
    elif (lowered.split(" ")[0].endswith("s") and not lowered.startswith("z-")
          and "-" not in lowered.split(" ")[0]):
        # 복수형 앞에는 관사를 쓰지 않는다. 단 "repeated-measures ANCOVA"처럼
        # 하이픈으로 묶인 **수식어**는 복수형이 아니므로 관사가 필요하다.
        art = ""
    else:
        art = "An" if lowered[0].lower() in "aeiou" else "A"
    if not art:
        return phrase
    if not capital:
        art = art.lower()
    return f"{art} {phrase}"


def _md_cell(text) -> str:
    """마크다운 표 칸 이스케이프 — 파이프·백틱이 표를 깨뜨리지 않게."""
    return (str(text).replace("\\", "\\\\").replace("|", "\\|")
            .replace("`", "\\`").replace("\n", " "))


def _fmt_power(p: float) -> str:
    return f"{p * 100:.1f}%"


def _fmt_num(x: float, digits: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, int):
        return f"{x:,}"
    if x != x or math.isinf(x):
        return "-"
    if x == int(x) and abs(x) < 1e15:
        return f"{int(x):,}"
    return f"{x:,.{digits}g}"


def _alloc_line(alloc: dict) -> str:
    """배분 딕셔너리를 사람이 읽는 한 줄로."""
    if "n_per_sequence" in alloc:
        n = alloc["n_per_sequence"]
        return f"순서당 {n:,}명 (AB {n:,} + BA {n:,}) = 총 {alloc['total']:,}명"
    if "n_per_group" in alloc:
        return (f"군당 {alloc['n_per_group']:,}명 × {alloc['k']}군 "
                f"= 총 {alloc['total']:,}명")
    if "n1" in alloc:
        if alloc["n1"] == alloc["n2"]:
            return f"군당 {alloc['n1']:,}명 (1군 {alloc['n1']:,} + 2군 {alloc['n2']:,}) = 총 {alloc['total']:,}명"
        return f"1군 {alloc['n1']:,}명 + 2군 {alloc['n2']:,}명 = 총 {alloc['total']:,}명"
    return f"{alloc.get('n', alloc.get('total')):,}명"


def _alloc_line_en(alloc: dict) -> str:
    """배분 딕셔너리를 영문 한 줄로 (프로토콜 영문 문장용)."""
    if "n_per_sequence" in alloc:
        return (f"{alloc['n_per_sequence']:,} participants per sequence "
                f"({alloc['total']:,} in total)")
    if "n_per_group" in alloc:
        return (f"{alloc['n_per_group']:,} participants per group across "
                f"{alloc['k']} groups ({alloc['total']:,} in total)")
    if "n1" in alloc:
        if alloc["n1"] == alloc["n2"]:
            return f"{alloc['n1']:,} participants per group ({alloc['total']:,} in total)"
        return (f"{alloc['n1']:,} participants in group 1 and {alloc['n2']:,} in group 2 "
                f"({alloc['total']:,} in total)")
    return f"{alloc.get('n', alloc.get('total')):,} participants"


def _effect_line(effect: dict) -> str:
    parts = [f"{effect['name']} = {_fmt_num(effect['value'], 3)}"]
    if effect.get("label"):
        parts.append(f"({effect['label']})")
    extras = []
    for key, label in (("eta_squared", "η²"), ("r_squared", "R²"),
                       ("risk_ratio", "RR"), ("odds_ratio", "OR"), ("cohen_h", "Cohen h")):
        if key in effect:
            extras.append(f"{label} {_fmt_num(effect[key], 3)}")
    if extras:
        parts.append("· " + ", ".join(extras))
    return " ".join(parts)


# --------------------------------------------------------------------------
# 프로토콜 문장
# --------------------------------------------------------------------------
def protocol_sentences(plan: dict) -> dict:
    """프로토콜/IRB에 그대로 붙일 수 있는 한국어·영어 문장."""
    if plan.get("kind") == "precision":
        return _precision_sentences(plan)
    design = plan["design"]
    alpha, sides = design["alpha"], design["sides"]
    effect = design["effect"]
    side_kr = "양측" if sides == 2 else "단측"
    side_en = "two-sided" if sides == 2 else "one-sided"
    if design["key"] == "anova":
        side_kr, side_en = "", ""

    alpha_kr = f"유의수준 {side_kr} α = {alpha:.4g}".replace("  ", " ")
    alpha_en = f"{side_en} α of {alpha:.4g}".replace("  ", " ")
    adj = design.get("alpha_adjustment")
    if adj:
        alpha_kr += f" ({adj['label']} 보정 적용)"
        alpha_en += f" (adjusted for {adj['comparisons']} comparisons, {adj['method']})"

    # 효과크기 표현과 **동사**는 설계마다 다르다. 비열등성은 마진을 "배제"하고,
    # 동등성은 마진 "안에서 동등성을 입증"한다 — "마진을 검출한다"는 틀린 표현이다.
    effect_kr = _josa(f"{effect['name']} = {_fmt_num(effect['value'], 3)}", "을", "를")
    effect_en = f"{effect.get('name_en', effect['name'])} = {_fmt_num(effect['value'], 3)}"
    verb_kr, verb_en = "검출하려면", "to detect"
    if design["key"] == "noninf":
        effect_kr = (
            f"SD {_fmt_num(effect['sd'], 3)}·가정 차이 "
            f"{_fmt_num(effect['assumed_diff'], 3)} 조건에서 비열등성 마진 "
            + _josa(_fmt_num(effect["margin_raw"], 3), "을", "를"))
        effect_en = (f"a non-inferiority margin of {_fmt_num(effect['margin_raw'], 3)} "
                     f"(SD {_fmt_num(effect['sd'], 3)}, assumed true difference "
                     f"{_fmt_num(effect['assumed_diff'], 3)})")
        verb_kr, verb_en = "배제하려면", "to exclude"
    elif design["key"] == "equiv":
        effect_kr = (f"SD {_fmt_num(effect['sd'], 3)}·가정 차이 "
                     f"{_fmt_num(effect['assumed_diff'], 3)} 조건에서 마진 "
                     f"±{_fmt_num(effect['margin_raw'], 3)} 안에서")
        effect_en = (f"equivalence within a margin of ±{_fmt_num(effect['margin_raw'], 3)} "
                     f"(SD {_fmt_num(effect['sd'], 3)}, assumed true difference "
                     f"{_fmt_num(effect['assumed_diff'], 3)})")
        verb_kr, verb_en = "동등성을 입증하려면", "to demonstrate"
    elif design["key"] == "noninf_prop":
        effect_kr = (
            f"대조군 반응률 {_fmt_num(effect['p1'], 3)}·중재군 "
            f"{_fmt_num(effect['p2'], 3)} 조건에서 비열등성 마진(위험차) "
            + _josa(_fmt_num(effect["margin_raw"], 3), "을", "를"))
        effect_en = (f"a non-inferiority margin of {_fmt_num(effect['margin_raw'], 3)} "
                     f"on the risk difference (control {_fmt_num(effect['p1'], 3)}, "
                     f"treatment {_fmt_num(effect['p2'], 3)})")
        verb_kr, verb_en = "배제하려면", "to exclude"
    elif design["key"] == "equiv_prop":
        effect_kr = (f"대조군 반응률 {_fmt_num(effect['p1'], 3)}·중재군 "
                     f"{_fmt_num(effect['p2'], 3)} 조건에서 위험차 마진 "
                     f"±{_fmt_num(effect['margin_raw'], 3)} 안에서")
        effect_en = (f"equivalence within a risk-difference margin of "
                     f"±{_fmt_num(effect['margin_raw'], 3)} "
                     f"(control {_fmt_num(effect['p1'], 3)}, treatment "
                     f"{_fmt_num(effect['p2'], 3)})")
        verb_kr, verb_en = "동등성을 입증하려면", "to demonstrate"
    elif design["key"] == "prop2":
        effect_kr = _josa(f"반응률 차이 {_fmt_num(effect['value'], 3)}", "을", "를")
        effect_en = (f"a difference in response rates of "
                     f"{_fmt_num(abs(effect['value']), 3)}")
    elif design["key"] == "prop1":
        effect_kr = _josa(
            f"성능목표치 {_fmt_num(effect['p0'], 3)} 대비 반응률 "
            f"{_fmt_num(effect['p1'], 3)}", "을", "를")
        effect_en = (f"a response rate of {_fmt_num(effect['p1'], 3)} against a "
                     f"performance goal of {_fmt_num(effect['p0'], 3)}")
    elif design["key"] == "crossover":
        effect_kr = _josa(
            f"처치 간 차이 {_fmt_num(effect['diff'], 3)}(개인 내 SD "
            f"{_fmt_num(effect['sd_within'], 3)}, 표준화 "
            f"{_fmt_num(effect['value'], 3)})", "을", "를")
        effect_en = (f"a treatment difference of {_fmt_num(effect['diff'], 3)}, i.e. "
                     f"{_fmt_num(effect['value'], 3)} within-subject standard "
                     f"deviations (sigma_w = {_fmt_num(effect['sd_within'], 3)})")
    elif design["key"] == "survival":
        effect_kr = _josa(f"위험비 {_fmt_num(effect['value'], 3)}", "을", "를")
        effect_en = f"a hazard ratio of {_fmt_num(effect['value'], 3)}"
    elif design["key"] == "count":
        effect_kr = _josa(
            f"발생률비 {_fmt_num(effect['value'], 3)}"
            f"(대조 {_fmt_num(effect['rate1'], 3)}건/{effect['time_unit']} → 중재 "
            f"{_fmt_num(effect['rate2'], 3)}건/{effect['time_unit']}, 과산포 k = "
            f"{effect['dispersion']:g}, 1인당 관찰 {effect['exposure']:g}"
            f"{effect['time_unit']})", "을", "를")
        unit_en = effect["time_unit"]
        model_en = ("Poisson (no overdispersion)" if effect["dispersion"] == 0
                    else f"negative binomial dispersion k = {effect['dispersion']:g}")
        effect_en = (
            f"a rate ratio of {_fmt_num(effect['value'], 3)} "
            f"(control {_fmt_num(effect['rate1'], 3)} vs treatment "
            f"{_fmt_num(effect['rate2'], 3)} events per {unit_en}, {model_en}, "
            f"mean exposure {effect['exposure']:g} {unit_en} per participant)")
    elif design["key"] == "ordinal":
        # 방향은 문장에서 재구성할 수 없다 — OR > 1이 '낮은 범주 쪽 이동'임을 명시한다.
        toward_kr = "낮은" if effect["value"] > 1 else "높은"
        toward_en = "lower" if effect["value"] > 1 else "higher"
        effect_kr = _josa(
            f"비례오즈비 {_fmt_num(effect['value'], 3)}"
            f"(순서형 범주 {effect['categories']}개, 중재군이 {toward_kr} 범주 쪽으로 "
            f"이동, 대조군 분포 "
            + " / ".join(f"{x:.3f}" for x in effect["probs1"]) + ")", "을", "를")
        effect_en = (
            f"a proportional-odds ratio of {_fmt_num(effect['value'], 3)} "
            f"across {effect['categories']} ordered categories "
            f"(shifting the treatment arm toward {toward_en} categories; "
            "control-group distribution "
            + ", ".join(f"{x:.3f}" for x in effect["probs1"]) + ")")
    elif design["key"] == "mcnemar":
        effect_kr = _josa(f"불일치 오즈비 {_fmt_num(effect['value'], 3)}", "을", "를")
        effect_en = (f"a discordant odds ratio of {_fmt_num(effect['value'], 3)} "
                     f"(discordant pairs {effect['discordant']:.1%})")
    else:
        # 군 순서에 따라 부호가 바뀌므로 문장에서는 절댓값으로 쓴다
        effect_en = (f"{effect.get('name_en', effect['name'])} = "
                     f"{_fmt_num(abs(effect['value']), 3)}")
        if design["key"] == "ttest2" and effect.get("analysis") in ("ancova", "change"):
            effect_en += (f" with a baseline correlation of {effect['baseline_r']:g} "
                          f"(design factor {effect['design_factor']:.3f})")
            effect_kr = _josa(
                f"{effect['name']} = {_fmt_num(effect['value'], 3)}"
                f"(기저값 상관 r = {effect['baseline_r']:g}, 설계배율 "
                f"{effect['design_factor']:.3f})", "을", "를")
        elif design["key"] == "repeated":
            post_n = effect["post_measurements"]
            base_n = effect["baseline_measurements"]
            target = ("at the final visit" if effect.get("estimand") == "last"
                      else "averaged over the post-baseline visits")
            def _meas(count: int) -> str:
                return f"{count} measurement" + ("" if count == 1 else "s")

            effect_en = (
                f"a standardised difference of {_fmt_num(abs(effect['value']), 3)} "
                f"(on the SD of a single measurement) {target}, with "
                f"{_meas(post_n)} after baseline and {_meas(base_n)} at baseline, "
                f"at a within-subject correlation of {effect['rho']:g} "
                f"(variance factor {effect['design_factor']:.3f})")
            effect_kr = _josa(
                f"{effect['name']} = {_fmt_num(effect['value'], 3)}"
                f"({'마지막 방문' if effect.get('estimand') == 'last' else '사후 평균'} "
                f"기준, 사후 {effect['post_measurements']}회·사전 "
                f"{effect['baseline_measurements']}회 측정, 측정 간 상관 ρ = "
                f"{effect['rho']:g}, 분산 배율 {effect['design_factor']:.3f})", "을", "를")

    if plan["direction"] == "solve_n":
        analysis = _alloc_line(plan["analysis"]["allocation"])
        kr = (f"{design['test_kr']}, {alpha_kr}, 목표 검정력 "
              f"{_fmt_power(plan['target_power'])} 기준으로 {effect_kr} {verb_kr} "
              f"분석 대상 {_josa(analysis, '이', '가')} 필요하다 "
              f"(실제 검정력 {_fmt_power(plan['achieved_power'])}).")
        en = (f"{_article(design['test_en'])} with {alpha_en} and "
              f"{plan['target_power']:.0%} power requires "
              f"{_alloc_line_en(plan['analysis']['allocation'])} {verb_en} {effect_en} "
              f"(actual power {plan['achieved_power']:.3f}).")
        if plan["enrollment"]["allocation"] != plan["analysis"]["allocation"]:
            kr += (" 탈락을 고려해 "
                   f"{_josa(_alloc_line(plan['enrollment']['allocation']), '을', '를')} 모집한다.")
            adjs = plan["adjustments"]
            reason = (f"{adjs['dropout']:.0%} attrition" if adjs["dropout"]
                      else "the rounding of clusters")
            en += (f" Allowing for {reason}, "
                   f"{_alloc_line_en(plan['enrollment']['allocation'])} will be enrolled.")
    else:
        # 설계마다 '무엇을 할 검정력인가'가 다르다. 예전에는 조사만 떼어 붙여
        # "…마진 ±5 안에서 가정 하에 54.5%이다" 같은 비문이 나왔다.
        # 조사를 떼었다 붙이면 종성 판정이 다시 필요하다 (예전에는 '을'을 하드코딩해
        # "0.5을 검출할"이 나왔다). _josa로 다시 붙인다.
        stem = effect_kr[:-1] if effect_kr.endswith(("을", "를")) else effect_kr
        goal_kr = {
            "검출하려면": _josa(stem, "을", "를") + " 검출할",
            "배제하려면": _josa(stem, "을", "를") + " 배제할",
            "동등성을 입증하려면": f"{effect_kr} 동등성을 입증할",
        }[verb_kr]
        kr = (f"확보 가능한 표본이 {_alloc_line(plan['given']['allocation'])}일 때, "
              f"{design['test_kr']}, {alpha_kr} 조건에서 {goal_kr} 검정력은 "
              f"{_fmt_power(plan['achieved_power'])}이다.")
        en = (f"With {_alloc_line_en(plan['given']['allocation'])}, "
              f"{_article(design['test_en'], capital=False)} at {alpha_en} provides "
              f"{plan['achieved_power']:.3f} power {verb_en} {effect_en}.")
        if plan.get("needed"):
            kr += (f" 목표 검정력 {_josa(_fmt_power(plan['target_power']), '을', '를')} "
                   f"달성하려면 {_josa(_alloc_line(plan['needed']['allocation']), '이', '가')} "
                   "필요하다.")
            en += (f" Reaching {plan['target_power']:.0%} power would require "
                   f"{_alloc_line_en(plan['needed']['allocation'])}.")
    if design["key"] == "survival":
        kr, en = _append_survival_assumptions(plan, effect, kr, en)
    seq = plan.get("sequential")
    if seq:
        marks = ", ".join(f"{row['information']:.0%}" for row in seq["looks_detail"][:-1])
        kr += (f" 정보량의 {marks} 시점에 중간분석을 하며, "
               f"{seq['spending_kr']} α 소비함수로 전체 1종오류율을 "
               + _josa(f"{seq['alpha']:.4g}", "으로", "로") + " 유지한다(경계 Z = "
               + ", ".join(f"{row['bound_z']:.3f}" for row in seq["looks_detail"])
               + f"). 이 표본수는 고정설계의 {seq['inflation']:.3f}배이며, 조기중단 시 "
               + ("추적기간이" if seq.get("information_label", "누적 N") != "누적 N"
                  else "필요한 표본수가")
               + f" 최대치의 {seq['expected_fraction_h1']:.0%} 수준으로 줄어든다.")
        interim_en = ("An interim analysis is planned after" if seq["interim"] == 1
                      else "Interim analyses are planned after")
        en += (f" {interim_en} {marks} of the information, using "
               f"{seq['spending_en']} to preserve an overall type I error rate of "
               f"{seq['alpha']:.4g} (efficacy boundaries Z = "
               + ", ".join(f"{row['bound_z']:.3f}" for row in seq["looks_detail"])
               + f"); the maximum sample size is {seq['inflation']:.3f} times that of the "
               f"corresponding fixed design.")
        if seq.get("futility_bounds") is not None:
            fut_marks = ", ".join(f"{row['futility_z']:.3f}"
                                  for row in seq["looks_detail"][:-1])
            kr += (f" 같은 시점에 {seq['futility_kr']} β 소비함수로 정한 비구속적"
                   f"(non-binding) 무익성 중단 경계(Z = {fut_marks})를 함께 적용한다. "
                   f"비구속적이므로 경계를 넘어도 계속 진행할 수 있으며 그 경우에도 "
                   f"전체 1종오류율은 "
                   + _josa(f"{seq['alpha']:.4g}", "을", "를")
                   + " 넘지 않는다. 효과가 없을 때 중간에 무익성으로 멈출 확률은 "
                   f"{seq['cumulative_futility_h0'][-2]:.0%}이며, 무익성 경계로 인한 "
                   f"검정력 손실({seq['target_power']:.1%} → {seq['power_same_n']:.1%}, "
                   f"{seq['power_loss'] * 100:.1f}%p)은 위 표본수에 이미 반영했다.")
            en += (f" Non-binding futility boundaries (Z = {fut_marks}) derived from "
                   f"{seq['futility_en']} are applied at the same analyses; because they "
                   f"are non-binding, the overall type I error rate remains at most "
                   f"{seq['alpha']:.4g} even if the trial continues past a futility "
                   f"boundary. The probability of stopping early for futility under the "
                   f"null hypothesis is {seq['cumulative_futility_h0'][-2]:.0%}, and the "
                   f"loss of power under the alternative "
                   f"({seq['target_power']:.1%} to {seq['power_same_n']:.1%}, "
                   f"{seq['power_loss'] * 100:.1f} percentage points) is already "
                   f"accounted for in the sample size above.")
    return {"kr": kr, "en": en}


def _append_survival_assumptions(plan: dict, effect: dict, kr: str, en: str):
    """생존분석 문장에 **사건 수와 생존 모형 가정**을 덧붙인다.

    "군당 198명"만 적힌 문장은 심사에서 통과하지 못한다. 그 숫자를 만든 것은
    중앙생존·등록기간·추적기간이고, 검정력을 결정하는 것은 사건 수이기 때문이다.
    """
    alloc = (plan.get("analysis") or {}).get("allocation") or {}
    n1, n2 = alloc.get("n1"), alloc.get("n2")
    if not (n1 and n2):
        return kr, en
    events = n1 * effect["prob_event_control"] + n2 * effect["prob_event_treatment"]
    kr += f" 이 표본수는 약 {events:.0f}건의 사건을 전제로 한다"
    en += f" This assumes approximately {events:.0f} events"
    median = effect.get("median_control")
    if median is not None:
        unit_kr = plan["design"].get("time_unit", "개월")
        accrual = effect.get("accrual", 0.0)
        followup = effect.get("followup", 0.0)
        kr += (f" (대조군 중앙생존 {median:g}{unit_kr}의 지수 생존모형, 등록 "
               f"{accrual:g}{unit_kr} + 추가 추적 {followup:g}{unit_kr})")
        en += (f" (exponential control survival with a median of {median:g}, "
               f"{accrual:g} of uniform accrual and {followup:g} of additional "
               f"follow-up, in the same time units)")
    else:
        kr += (f" (대조군 사건률 {effect['prob_event_control']:.1%}, 중재군 "
               f"{effect['prob_event_treatment']:.1%} 가정)")
        en += (f" (assuming an event probability of "
               f"{effect['prob_event_control']:.1%} in the control arm and "
               f"{effect['prob_event_treatment']:.1%} in the treatment arm)")
    return kr + ".", en + "."


def _precision_sentences(plan: dict) -> dict:
    if plan.get("given_n"):
        return _precision_sentences_given_n(plan)
    target = plan["target"]
    # 신뢰수준은 --alpha에서 유도한다 (예전에는 95%로 하드코딩돼, --alpha를 바꾸면
    # 프로토콜에 붙일 문장이 실제 계산과 다른 신뢰수준을 주장했다)
    level = f"{(1.0 - target['alpha']) * 100:g}%"
    if plan["design_key"] == "diag":
        kr = (f"민감도 {target['sens']:g}·특이도 {target['spec']:g}, 유병률 "
              f"{target['prevalence']:g}인 집단에서 민감도와 특이도를 각각 "
              f"{level} 신뢰구간 반폭 ±{target['half_width']:g} 이내로 추정하려면 "
              f"{plan['n']:,}명을 등록해야 한다 (질환자 약 {plan['n_disease']:.0f}명, "
              f"예상 반폭 민감도 ±{plan['achieved_half_width']:.3g}, 특이도 "
              f"±{plan['achieved_half_width_spec']:.3g}).")
        en = (f"To estimate a sensitivity of {target['sens']:g} and a specificity of "
              f"{target['spec']:g} to within a {level} CI half-width of "
              f"±{target['half_width']:g} at a prevalence of {target['prevalence']:g}, "
              f"{plan['n']:,} participants must be enrolled "
              f"(about {plan['n_disease']:.0f} with the target condition; "
              f"Buderer 1996).")
    elif plan["design_key"] == "kappa":
        lo, hi = plan["expected_ci"]
        kr = ("두 평가자의 이분형 판정 일치도가 κ = "
              + _josa(f"{target['kappa']:g}(관심 범주 유병률 "
                      f"{target['prevalence']:g})", "이라고", "라고") + " 할 때, "
              f"{level} 신뢰구간 폭을 {target['width']:g} 이내로 추정하려면 "
              f"{plan['n']:,}명이 필요하다 (예상 폭 {plan['achieved_width']:.4g}, "
              f"예상 구간 [{lo:.3f}, {hi:.3f}]).")
        en = (f"To estimate a kappa of {target['kappa']:g} between two raters "
              f"(prevalence {target['prevalence']:g}) to within a {level} CI width of "
              f"{target['width']:g}, {plan['n']:,} subjects are required "
              f"(expected width {plan['achieved_width']:.4g}; Fleiss 1969).")
    elif plan["design_key"] == "icc":
        kr = (f"예상 ICC {target['icc']:g}, 측정 {target['raters']}회 조건에서 "
              f"{level} 신뢰구간 폭을 {target['width']:g} 이내로 추정하려면 "
              f"{plan['n']:,}명이 필요하다 (총 측정 {plan['total_measurements']:,}회, "
              f"예상 폭 {plan['achieved_width']:.4g}).")
        en = (f"To estimate an ICC of {target['icc']:g} with {target['raters']} ratings per "
              f"subject to within a {level} CI width of {target['width']:g}, "
              f"{plan['n']:,} subjects are required "
              f"(expected width {plan['achieved_width']:.4g}; Bonett 2002).")
    else:
        kr = ("두 방법 차이의 표준편차를 "
              + _josa(f"{target['sd_diff']:g}", "으로", "로") + " 가정할 때, Bland–Altman "
              f"일치한계(LoA)의 {level} 신뢰구간 반폭을 {target['half_width']:g} 이내로 "
              f"추정하려면 {plan['n']:,}명이 필요하다 "
              f"(예상 반폭 {plan['achieved_half_width']:.4g}, "
              f"예상 LoA ±{1.959963984540054 * target['sd_diff']:.4g}).")
        en = (f"Assuming an SD of the between-method differences of {target['sd_diff']:g}, "
              f"{plan['n']:,} subjects give a {level} CI half-width of "
              f"{plan['achieved_half_width']:.4g} for each limit of agreement "
              f"(Bland & Altman 1999).")
    return {"kr": kr, "en": en}


def _precision_sentences_given_n(plan: dict) -> dict:
    """--n(확보 가능한 인원)을 준 경우의 문장 — "필요하다"가 아니라 "얻는다".

    예전에는 정방향 문장을 그대로 써서 "20명이 필요하다 (예상 폭 0.32)"처럼
    **자기 문장 안에서 모순되는** 주장을 만들었다. 목표를 못 맞추는 인원인데도
    "목표 폭 0.15 이내로 추정하려면 20명이 필요하다"고 적혀 나갔다.
    """
    target = plan["target"]
    level = f"{(1.0 - target['alpha']) * 100:g}%"
    n = plan["n"]
    key = plan["design_key"]
    if key == "icc":
        got, goal, unit = plan["achieved_width"], target["width"], "폭"
        kr_head = (f"확보 가능한 대상자가 {n:,}명일 때, 예상 ICC {target['icc']:g}·측정 "
                   f"{target['raters']}회 조건에서 {level} 신뢰구간 폭은 약 {got:.4g}로 "
                   "추정된다")
        en = (f"With {n:,} subjects, an ICC of {target['icc']:g} measured "
              f"{target['raters']} times is estimated to within a {level} CI width of "
              f"about {got:.4g} (Bonett 2002).")
    elif key == "kappa":
        got, goal, unit = plan["achieved_width"], target["width"], "폭"
        lo, hi = plan["expected_ci"]
        kr_head = (f"확보 가능한 대상자가 {n:,}명일 때, κ = {target['kappa']:g}"
                   f"(유병률 {target['prevalence']:g}) 조건에서 {level} 신뢰구간은 약 "
                   f"[{lo:.3f}, {hi:.3f}](폭 {got:.4g})로 추정된다")
        en = (f"With {n:,} subjects, a kappa of {target['kappa']:g} is estimated to "
              f"within a {level} CI width of about {got:.4g} (Fleiss 1969).")
    elif key == "loa":
        got, goal, unit = plan["achieved_half_width"], target["half_width"], "반폭"
        kr_head = (f"확보 가능한 대상자가 {n:,}명일 때, 차이의 표준편차를 "
                   + _josa(f"{target['sd_diff']:g}", "으로", "로")
                   + f" 가정하면 Bland–Altman 일치한계의 {level} 신뢰구간 반폭은 약 "
                   f"{got:.4g}로 추정된다")
        en = (f"With {n:,} subjects and an SD of the between-method differences of "
              f"{target['sd_diff']:g}, each limit of agreement is estimated to within a "
              f"{level} CI half-width of about {got:.4g} (Bland & Altman 1999).")
    else:      # diag
        got, goal, unit = plan["achieved_half_width"], target["half_width"], "반폭"
        kr_head = (f"확보 가능한 대상자가 {n:,}명일 때(질환자 약 "
                   f"{plan['n_disease']:.0f}명), 민감도의 {level} 신뢰구간 반폭은 약 "
                   f"±{got:.4g}, 특이도는 ±{plan['achieved_half_width_spec']:.4g}로 "
                   "추정된다")
        en = (f"With {n:,} participants (about {plan['n_disease']:.0f} with the target "
              f"condition), sensitivity is estimated to within a {level} CI half-width "
              f"of about ±{got:.4g} and specificity ±"
              f"{plan['achieved_half_width_spec']:.4g} (Buderer 1996).")
    meets = got <= goal + 1e-12
    kr = kr_head + (f" — 목표 {unit} {goal:g}을 충족한다." if meets
                    else f" — 목표 {unit} {goal:g}에는 **미치지 못한다**.")
    en += ("" if meets else
           f" This does not meet the target {'width' if unit == '폭' else 'half-width'} "
           f"of {goal:g}.")
    return {"kr": kr, "en": en}


# --------------------------------------------------------------------------
# 텍스트 출력
# --------------------------------------------------------------------------
def _display_width(text: str) -> int:
    """터미널 표시 폭 (한글·기호는 2칸)."""
    return sum(2 if (ord(ch) > 0x1100 and not ch.isascii()) else 1 for ch in text)


def _pad(text: str, width: int, align: str = "<") -> str:
    """표시 폭 기준 정렬 (한글 열이 밀리지 않게)."""
    gap = max(0, width - _display_width(text))
    if align == ">":
        return " " * gap + text
    return text + " " * gap


def _sequential_text(plan: dict) -> list[str]:
    """중간분석 경계표 — 각 시점의 임계값·명목 유의수준·누적 α·조기중단 확률."""
    seq = plan["sequential"]
    has_fut = seq.get("futility_bounds") is not None
    title = f"■ 중간분석 경계 ({seq['spending_kr']} α 소비함수, 총 {seq['looks']}회 분석"
    title += f", 무익성 {seq['futility_kr']})" if has_fut else ")"
    lines = ["", title]
    info_label = seq.get("information_label", "누적 N")
    show_n = info_label != "누적 N"
    header = ("  " + _pad("시점", 8) + _pad("정보비율", 11, ">")
              + _pad(info_label, 14, ">") + _pad("Z 경계", 9, ">")
              + _pad("명목 p", 11, ">") + _pad("누적 α", 9, ">")
              + _pad("중단확률", 9, ">"))
    if has_fut:
        header += _pad("무익성 Z", 10, ">") + _pad("무익중단", 9, ">")
    lines.append(header)
    # 가로줄은 헤더의 **표시 폭**(한글은 2칸)에 맞춘다
    lines.append("  " + "-" * sum(2 if ord(ch) > 0x2E80 else 1 for ch in header[2:]))
    for row in seq["looks_detail"]:
        name = "최종" if row["is_final"] else f"중간 {row['look']}"
        amount = row.get("information_amount") if show_n else row.get("n_total")
        line = (
            "  " + _pad(name, 8)
            + _pad(f"{row['information']:.3f}", 11, ">")
            + _pad(f"{amount:,}" if amount else "-", 14, ">")
            + _pad(f"{row['bound_z']:.4f}", 9, ">")
            + _pad(f"{row['nominal_p']:.5f}", 11, ">")
            + _pad(f"{row['cumulative_alpha']:.4f}", 9, ">")
            + _pad(f"{row['stop_prob_h1']:.1%}", 9, ">"))
        if has_fut:
            line += (_pad(f"{row['futility_z']:.4f}", 10, ">")
                     + _pad(f"{row['futility_stop_h0']:.1%}", 9, ">"))
        lines.append(line)
    lines.append(
        "  (중단확률 = 대립가설이 참일 때 그 시점에서 **어떤 이유로든** 멈출 확률 "
        "= 효능 + 무익성)" if has_fut else
        "  (중단확률 = 대립가설이 참일 때 그 시점에서 유의성에 도달할 확률)")
    if has_fut:
        lines.append("  (무익중단 = 효과가 **없을** 때 그 시점에서 무익성으로 멈출 확률. "
                     "최종 시점의 무익성 경계는 효능 경계와 같습니다)")
        clamped = [row["look"] for row in seq["looks_detail"]
                   if row.get("futility_at_harm_bound")]
        if clamped:
            marks = ", ".join(f"중간 {k}" for k in clamped)
            lines.append(
                f"  ※ {marks} 시점의 무익성 경계는 **해(harm) 방향 효능 경계와 일치**합니다 "
                "— 그 시점에는 추가 무익성 중단 규칙이 사실상 없고, 경계를 넘으면 "
                "'무익'이 아니라 통계적으로 유의한 해입니다.")
        lines.append(
            f"  무익성으로 멈출 누적확률: 효과 없으면 "
            f"{seq['cumulative_futility_h0'][-2]:.1%}, 효과 있는데 잘못 멈추면 "
            f"{seq['cumulative_futility_h1'][-2]:.1%} (= 소비한 2종오류 β*)")
        lines.append(
            f"  실제 검정력 손실: {seq['target_power']:.1%} → {seq['power_same_n']:.1%} "
            f"({seq['power_loss'] * 100:.1f}%p) — 같은 표본수에 무익성 규칙만 얹었을 때. "
            "이만큼을 되찾도록 표본수를 늘렸습니다")
        cps = ", ".join(
            f"중간 {row['look']}: {row['cp_at_futility_alt']:.0%} "
            f"(추세대로면 {row['cp_at_futility_trend']:.0%})"
            for row in seq["looks_detail"] if row.get("cp_at_futility_alt") is not None)
        lines.append(f"  무익성 경계에서의 조건부 검정력 — {cps}. "
                     "= 그 시점에 경계값을 봤을 때 최종분석에서 유의해질 확률"
                     "(가정한 효과가 맞다면 / 관측 추세가 이어진다면)")
        lines.append(
            f"  비구속적(non-binding): 무익성 경계를 무시하고 계속해도 전체 α는 "
            f"{seq['achieved_alpha']:.4f} 이하로 유지됩니다. "
            f"경계를 지키면 **효능 방향** α가 {seq['alpha_upper_nominal']:.4f} → "
            + _josa(f"{seq['alpha_if_honored']:.4f}", "으로", "로")
            + " 내려갑니다 (양측 α "
            + _josa(f"{seq['alpha']:.4g}", "이", "가")
            + " 아니라 그 절반과 비교한 값입니다).")
    if show_n:
        lines.append(f"  ※ 중간분석 시점은 달력 날짜나 등록 인원이 아니라 "
                     f"**{info_label}**로 프로토콜에 적으세요 — 그 시점에는 이미 "
                     "대부분이 등록을 마친 뒤입니다.")
    if seq.get("expected_n_h1"):
        if show_n:
            # 생존분석: 조기중단이 아껴 주는 것은 인원이 아니라 추적기간이다
            total_info = seq.get("information_total") or 0
            lines.append(
                f"  기대 {info_label}: 효과가 있으면 "
                f"{seq['expected_fraction_h1'] * total_info:.0f}"
                f"{seq.get('information_unit', '')}, 없으면 "
                f"{seq['expected_fraction_h0'] * total_info:.0f}"
                f"{seq.get('information_unit', '')} "
                f"(최대 {total_info:.0f}{seq.get('information_unit', '')})")
            lines.append("  ※ 조기중단이 아껴 주는 것은 **등록 인원이 아니라 추적기간**입니다 "
                         "— 모집 목표는 최대 표본수 그대로 잡으세요.")
        else:
            lines.append(f"  기대 표본수: 효과가 있으면 {seq['expected_n_h1']:.0f}명, "
                         f"없으면 {seq['expected_n_h0']:.0f}명 "
                         f"(최대 {plan['analysis']['allocation'].get('total'):,}명)")
    if plan.get("fixed_design"):
        fixed_total = plan["fixed_design"]["allocation"].get("total")
        lines.append(f"  고정설계(중간분석 없음)라면 {fixed_total:,}명 "
                     f"→ 팽창계수 ×{seq['inflation']:.4f}")
    return lines


def _sensitivity_text(sens: dict) -> list[str]:
    lines = ["", "■ 민감도 분석 (가정이 틀렸을 때 표본수가 어떻게 변하는가)"]
    if sens["kind"] == "n_by_power_and_effect":
        first, cell_w = 14, 16
        labels = sens.get("col_labels") or [f"×{c:g}" for c in sens["cols"]]
        header = "  " + _pad(sens.get("col_label", "효과크기 가정"), first) + "".join(
            _pad(text, cell_w, ">") for text in labels)
        lines.append(header)
        lines.append("  " + "-" * (first + cell_w * len(sens["cols"])))
        for power, row in zip(sens["rows"], sens["cells"]):
            cells = []
            for cell in row:
                if cell is None:
                    cells.append(_pad("계산 불가", cell_w, ">"))
                elif cell.get("single_arm"):
                    cells.append(_pad(f"{cell['unit']:,}", cell_w, ">"))
                else:
                    cells.append(_pad(f"{cell['unit']:,}/총{cell['total']:,}", cell_w, ">"))
            lines.append("  " + _pad(_fmt_power(power), first) + "".join(cells))
        single = all(c is None or c.get("single_arm")
                     for row in sens["cells"] for c in row)
        lines.append("  (표기: " + ("필요 n" if single else "단위당 n / 총 N")
                     + " — 분석 대상 기준. 열 머리의 숫자는 그 배율에서의 실제 가정값)")
    else:
        lines.append("  " + _pad("표본수 배율", 14) + _pad("단위 n", 10, ">")
                     + _pad("총 N", 10, ">") + _pad("검정력", 12, ">"))
        lines.append("  " + "-" * 46)
        for row in sens["rows"]:
            lines.append("  " + _pad("×" + format(row["factor"], "g"), 14)
                         + _pad(f"{row['unit']:,}", 10, ">")
                         + _pad(f"{row['total']:,}", 10, ">")
                         + _pad(_fmt_power(row["power"]), 12, ">"))
        lines.append("  (표기: 분석 가능한 유효 n 기준 — 탈락·설계효과 반영 후)")
    return lines


def render_text(plan: dict, width: int = 74) -> str:
    """터미널용 사람이 읽는 리포트."""
    if plan.get("kind") == "precision":
        return _render_precision_text(plan)
    design = plan["design"]
    out: list[str] = []
    out.append(_BAR[:width])
    out.append(f" powerplan — {design['name_kr']}  [{design['key']}]")
    out.append(_BAR[:width])
    out.append(f" 검정         : {design['test_kr']}")
    side = "양측" if design["sides"] == 2 else "단측"
    alpha_txt = f"α = {design['alpha']:.4g} ({side})"
    if design["key"] == "anova":
        alpha_txt = f"α = {design['alpha']:.4g}"
    adj = design.get("alpha_adjustment")
    if adj:
        alpha_txt += f"  ← {adj['label']}"
    out.append(f" 유의수준     : {alpha_txt}")
    out.append(f" 효과크기     : {_effect_line(design['effect'])}")
    out.append("")

    if plan["direction"] == "solve_n":
        effective = plan.get("effective")
        if effective and effective["allocation"] != plan["analysis"]["allocation"]:
            # 군집설계: 검정력이 요구하는 '유효 표본수'와 실제 분석 인원이 다르다
            out.append(f"  유효 표본수(개인배정 기준) : {_alloc_line(effective['allocation'])}")
        out.append(f"▶ 필요한 분석 표본수 : {_alloc_line(plan['analysis']['allocation'])}")
        out.append(f"  목표 검정력        : {_fmt_power(plan['target_power'])}"
                   f"  →  실제 달성 {_fmt_power(plan['achieved_power'])}")
    else:
        out.append(f"▶ 주어진 표본수      : {_alloc_line(plan['given']['allocation'])}")
        eff = plan["given"]["effective_unit"]
        if abs(eff - plan["given"]["unit"]) > 1e-9:
            out.append(f"  분석 가능 유효 n   : 단위당 {eff:.1f}"
                       f" (탈락·설계효과 반영)")
        out.append(f"▶ 검정력             : {_fmt_power(plan['achieved_power'])}")
        if "meets_target" in plan:
            mark = "충족" if plan["meets_target"] else "미달"
            out.append(f"  목표 {_fmt_power(plan['target_power'])} 대비 : {mark}")
        if plan.get("needed"):
            line = f"  목표 달성에 필요   : {_alloc_line(plan['needed']['allocation'])}"
            if plan["needed"]["enrollment"] != plan["needed"]["allocation"]:
                line += f" (모집 {_alloc_line(plan['needed']['enrollment'])})"
            out.append(line)

    enroll = plan["enrollment"]["allocation"]
    if plan["direction"] == "solve_n" and enroll != plan["analysis"]["allocation"]:
        adjs = plan["adjustments"]
        # 설계효과는 이미 '분석 표본수' 줄에서 반영됐다. 여기 붙이면 두 번 곱한 것처럼
        # 읽히므로, 이 줄에는 분석 → 모집 사이에 실제로 일어난 일만 적는다.
        detail = []
        if adjs["dropout"]:
            detail.append(f"탈락 {adjs['dropout']:.0%}")
        if plan["enrollment"].get("before_cluster_rounding"):
            detail.append("군집 단위 올림")
        out.append(f"▶ 모집 표본수        : {_alloc_line(enroll)}"
                   + (f"  ({', '.join(detail)})" if detail else ""))
    for label, value in plan.get("design_lines", ()):
        out.append("  " + _pad(str(label), 19) + ": " + str(value))
    if plan["enrollment"].get("clusters"):
        clusters = plan["enrollment"]["clusters"]
        pretty = ", ".join(f"{_ALLOC_LABELS.get(k, k)} {v:,}개"
                           for k, v in clusters.items())
        out.append(f"  군집 수            : {pretty}"
                   f" (군집당 {plan['enrollment']['cluster_size']}명"
                   + (" — 위 모집 인원은 군집 단위로 올린 값)"
                      if plan["enrollment"].get("before_cluster_rounding") else ")"))

    if plan.get("sequential"):
        out.extend(_sequential_text(plan))

    out.append("")
    if plan.get("suppress_protocol_sentence"):
        out.append("■ 프로토콜용 문장 — 만들지 않았습니다")
        out.append("  " + _wrap(plan["suppress_protocol_sentence"], width - 4, "  "))
    else:
        sentences = protocol_sentences(plan)
        out.append("■ 프로토콜용 문장 (그대로 붙여 쓰세요)")
        out.append("  [KR] " + _wrap(sentences["kr"], width - 8, "        "))
        out.append("  [EN] " + _wrap(sentences["en"], width - 8, "        "))

    if plan.get("sensitivity"):
        out.extend(_sensitivity_text(plan["sensitivity"]))

    if plan["notes"]:
        out.append("")
        out.append("■ 확인하세요 (가정과 한계)")
        for note in plan["notes"]:
            out.append("  · " + _wrap(note, width - 6, "    "))
    if plan["references"]:
        out.append("")
        out.append("■ 근거 문헌")
        for ref in plan["references"]:
            out.append("  - " + _wrap(ref, width - 6, "    "))
    out.extend(_provenance_lines(plan, width))
    out.append(_BAR[:width])
    return "\n".join(out)


def _provenance_lines(plan: dict, width: int = 74) -> list[str]:
    """이 결과를 만든 명령·버전·시각 (SAP 부록에 그대로 들어간다)."""
    prov = plan.get("provenance")
    if not prov:
        return []
    lines = ["",
             "■ 재현 정보 (이 줄까지 함께 붙여 두면 그대로 재현됩니다)",
             "  " + _wrap(f"{prov['tool']} {prov['version']} · {prov['generated']}",
                          width - 4, "  "),
             "  $ " + _wrap(prov["command"], width - 6, "     ")]
    if prov.get("paths_shortened"):
        lines.append("  (경로는 파일 이름만 남겼습니다 — 폴더 이름에 연구·대상자 정보가 "
                     "들어가는 일이 흔하기 때문입니다. 다시 돌릴 때는 파일이 있는 "
                     "폴더에서 실행하거나 경로를 붙이세요)")
    return lines


def _render_precision_text(plan: dict, width: int = 74) -> str:
    out = [_BAR[:width], f" powerplan — {plan['name_kr']}  [{plan['design_key']}]",
           _BAR[:width]]
    target = plan["target"]
    if plan["design_key"] == "diag":
        out.append(f" 예상 민감도  : {target['sens']:g}")
        out.append(f" 예상 특이도  : {target['spec']:g}")
        out.append(f" 유병률       : {target['prevalence']:g}")
        out.append(f" 목표 반폭    : ±{target['half_width']:g} "
                   f"(신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        head = "▶ 필요한 대상자 수   : " if not plan.get("given_n") else "▶ 주어진 대상자 수   : "
        out.append(f"{head}{plan['n']:,}명")
        out.append(f"  질환자 / 비질환자  : 약 {plan['n_disease']:.0f}명 / "
                   f"{plan['n_healthy']:.0f}명  (제약이 되는 쪽: {plan['binding']})")
        out.append(f"  예상 반폭          : 민감도 ±{plan['achieved_half_width']:.4g} · "
                   f"특이도 ±{plan['achieved_half_width_spec']:.4g}")
        out.append(f"  사례-대조 설계라면 : 질환자 {math.ceil(plan['required_disease']):,}명 + "
                   f"비질환자 {math.ceil(plan['required_healthy']):,}명이면 충분합니다")
    elif plan["design_key"] == "kappa":
        out.append(f" 예상 κ       : {target['kappa']:g}")
        out.append(f" 유병률 π     : {target['prevalence']:g} (관심 범주의 비율)")
        out.append(f" 목표 CI 폭   : {target['width']:g} "
                   f"(±{target['width'] / 2:g}, 신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        head = ("▶ 주어진 대상자 수   : " if plan.get("given_n")
                else "▶ 필요한 대상자 수   : ")
        out.append(f"{head}{plan['n']:,}명")
        out.append(f"  예상 CI 폭         : {plan['achieved_width']:.4f}")
        lo, hi = plan["expected_ci"]
        out.append(f"  예상 신뢰구간      : [{lo:.4f}, {hi:.4f}]")
        out.append(f"  기대 '있음' 판정   : 약 {plan['expected_positive']:.0f}명")
    elif plan["design_key"] == "icc":
        out.append(f" 예상 ICC     : {target['icc']:g}")
        out.append(f" 측정 횟수 k  : {target['raters']}")
        out.append(f" 목표 CI 폭   : {target['width']:g} (신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        head = ("▶ 주어진 대상자 수   : " if plan.get("given_n")
                else "▶ 필요한 대상자 수   : ")
        out.append(f"{head}{plan['n']:,}명 (총 측정 {plan['total_measurements']:,}회)")
        out.append(f"  예상 CI 폭         : {plan['achieved_width']:.4f}")
    else:
        out.append(f" 차이의 SD    : {target['sd_diff']:g}")
        ratio = plan["ratio_to_sd"]
        ratio_txt = (f"차이 SD의 {ratio:.2f}배, " if math.isfinite(ratio) else "")
        out.append(f" 목표 반폭    : {target['half_width']:g} "
                   f"({ratio_txt}신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        head = ("▶ 주어진 대상자 수   : " if plan.get("given_n")
                else "▶ 필요한 대상자 수   : ")
        out.append(f"{head}{plan['n']:,}명")
        out.append(f"  예상 LoA CI 반폭   : {plan['achieved_half_width']:.4g}")
        lo, hi = plan["expected_loa"]
        out.append(f"  예상 LoA           : {lo:.4g} ~ {hi:.4g} (bias = 0 가정)")
    sentences = protocol_sentences(plan)
    out.append("")
    out.append("■ 프로토콜용 문장")
    out.append("  [KR] " + _wrap(sentences["kr"], width - 8, "        "))
    out.append("  [EN] " + _wrap(sentences["en"], width - 8, "        "))
    out.append("")
    out.append("■ 확인하세요 (가정과 한계)")
    for note in plan["notes"]:
        out.append("  · " + _wrap(note, width - 6, "    "))
    out.append("")
    out.append("■ 근거 문헌")
    for ref in plan["references"]:
        out.append("  - " + _wrap(ref, width - 6, "    "))
    out.extend(_provenance_lines(plan, width))
    out.append(_BAR[:width])
    return "\n".join(out)


def _wrap(text: str, width: int, indent: str) -> str:
    """한글 폭을 고려한 간단한 줄바꿈 (동아시아 문자는 2칸으로 계산)."""
    words = str(text).split()
    lines: list[str] = []
    current = ""
    current_w = 0
    for word in words:
        w = _display_width(word)
        if current and current_w + 1 + w > width:
            lines.append(current)
            current, current_w = word, w
        else:
            current = f"{current} {word}" if current else word
            current_w += (1 + w) if current_w else w
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines) if lines else ""


# --------------------------------------------------------------------------
# Markdown / JSON
# --------------------------------------------------------------------------
def render_markdown(plan: dict) -> str:
    """논문·프로토콜 문서에 붙이는 Markdown 표."""
    if plan.get("kind") == "precision":
        target = plan["target"]
        rows = [("설계", plan["name_kr"]), ]
        if plan["design_key"] == "diag":
            rows += [("예상 민감도/특이도", f"{target['sens']:g} / {target['spec']:g}"),
                     ("유병률", f"{target['prevalence']:g}"),
                     ("목표 반폭", f"±{target['half_width']:g}"),
                     ("**필요 대상자**", f"**{plan['n']:,}명**"),
                     ("질환자 / 비질환자",
                      f"{plan['n_disease']:.0f}명 / {plan['n_healthy']:.0f}명"),
                     ("예상 반폭",
                      f"민감도 ±{plan['achieved_half_width']:.4g} · "
                      f"특이도 ±{plan['achieved_half_width_spec']:.4g}")]
        elif plan["design_key"] == "kappa":
            lo, hi = plan["expected_ci"]
            rows += [("예상 κ", f"{target['kappa']:g}"),
                     ("유병률 π", f"{target['prevalence']:g}"),
                     ("목표 CI 폭", f"{target['width']:g}"),
                     ("**필요 대상자**", f"**{plan['n']:,}명**"),
                     ("예상 CI", f"[{lo:.3f}, {hi:.3f}] (폭 {plan['achieved_width']:.4g})")]
        elif plan["design_key"] == "icc":
            rows += [("예상 ICC", f"{target['icc']:g}"),
                     ("측정 횟수 k", str(target["raters"])),
                     ("목표 CI 폭", f"{target['width']:g}"),
                     ("**필요 대상자**", f"**{plan['n']:,}명**"),
                     ("예상 CI 폭", f"{plan['achieved_width']:.4f}")]
        else:
            rows += [("차이의 SD", f"{target['sd_diff']:g}"),
                     ("목표 반폭", f"{target['half_width']:g}"),
                     ("**필요 대상자**", f"**{plan['n']:,}명**"),
                     ("예상 반폭", f"{plan['achieved_half_width']:.4g}")]
    else:
        design = plan["design"]
        side = "양측" if design["sides"] == 2 else "단측"
        rows = [
            ("설계", f"{design['name_kr']} ({design['key']})"),
            ("검정", design["test_kr"]),
            ("유의수준 α", f"{design['alpha']:g} ({side})"),
            ("효과크기", _effect_line(design["effect"])),
        ]
        if plan["direction"] == "solve_n":
            rows += [
                ("목표 검정력", _fmt_power(plan["target_power"])),
                ("**분석 표본수**", f"**{_alloc_line(plan['analysis']['allocation'])}**"),
                ("실제 검정력", _fmt_power(plan["achieved_power"])),
            ]
            if plan["enrollment"]["allocation"] != plan["analysis"]["allocation"]:
                rows.append(("모집 표본수", _alloc_line(plan["enrollment"]["allocation"])))
        else:
            rows += [
                ("주어진 표본수", _alloc_line(plan["given"]["allocation"])),
                ("**검정력**", f"**{_fmt_power(plan['achieved_power'])}**"),
            ]
            if plan.get("needed"):
                rows.append(("목표 달성 필요 표본수",
                             _alloc_line(plan["needed"]["allocation"])))
        rows += [(str(label), str(value))
                 for label, value in plan.get("design_lines", ())]
    pilot = plan.get("pilot")
    if pilot:
        obs = pilot["observed"]
        ci = obs["ci"]
        if obs["kind"] == "two_group":
            g1, g2 = pilot["data"]["group1"], pilot["data"]["group2"]
            rows.insert(0, ("사전연구 관측",
                            f"{g1['label']} n={g1['n']} ({g1['mean']:.3g}±{g1['sd']:.3g}) vs "
                            f"{g2['label']} n={g2['n']} ({g2['mean']:.3g}±{g2['sd']:.3g})"))
            rows.insert(1, ("관측 효과크기",
                            f"d = {obs['d']:.3f} (Hedges g {obs['hedges_g']:.3f}), "
                            f"{ci['conf']:.0%} CI [{ci['low']:.3f}, {ci['high']:.3f}]"))
        else:
            d = pilot["data"]["diff"]
            rows.insert(0, ("사전연구 관측",
                            f"{d['n']}쌍, 변화량 {d['mean']:.3g}±{d['sd']:.3g}"))
            rows.insert(1, ("관측 효과크기",
                            f"dz = {obs['dz']:.3f} (Hedges g {obs['hedges_g']:.3f}), "
                            f"{ci['conf']:.0%} CI [{ci['low']:.3f}, {ci['high']:.3f}]"))
        rows.insert(2, ("계획 기준",
                        "신뢰구간 하한 (보수적)" if pilot.get("planned_on") == "conservative"
                        else "관측 효과크기 (--plan-on observed)"))
        if obs["conservative_d"] <= 0.0:
            rows.insert(3, ("⚠ 주의",
                            "효과크기 신뢰구간이 0을 포함합니다 — 사전연구만으로는 효과크기를 "
                            "확정할 수 없습니다. 임상적으로 의미있는 최소 차이(MCID)로 "
                            "다시 계산하세요"))
    lines = ["| 항목 | 값 |", "|---|---|"]
    lines += [f"| {_md_cell(k)} | {_md_cell(v)} |" for k, v in rows]
    if plan.get("suppress_protocol_sentence"):
        lines += ["", "**프로토콜 문장 — 만들지 않았습니다**", "",
                  f"> ⚠ {plan['suppress_protocol_sentence']}"]
    else:
        sentences = protocol_sentences(plan)
        lines += ["", "**프로토콜 문장 (KR)**", "", f"> {sentences['kr']}", "",
                  "**Protocol sentence (EN)**", "", f"> {sentences['en']}"]
    if plan.get("sequential"):
        seq = plan["sequential"]
        info_label = seq.get("information_label", "누적 N")
        has_fut = seq.get("futility_bounds") is not None
        head = f"**중간분석 경계 ({seq['spending_kr']}, 총 {seq['looks']}회"
        head += f", 무익성 {seq['futility_kr']})**" if has_fut else ")**"
        lines += ["", head, "",
                  f"| 시점 | 정보비율 | {info_label} | Z 경계 | 명목 p | 누적 α | "
                  + ("중단확률(H1, 효능+무익) |" if has_fut else "중단확률(H1) |")
                  + (" 무익성 Z | 무익성 명목 p(단측) | 무익중단(H0) |" if has_fut else ""),
                  "|---|---|---|---|---|---|---|" + ("---|---|---|" if has_fut else "")]
        for row in seq["looks_detail"]:
            name = "최종" if row["is_final"] else f"중간 {row['look']}"
            amount = row.get("information_amount") or row.get("n_total") or 0
            cells = (
                f"| {name} | {row['information']:.3f} | {amount:,} | "
                f"{row['bound_z']:.4f} | {row['nominal_p']:.5f} | "
                f"{row['cumulative_alpha']:.4f} | {row['stop_prob_h1']:.1%} |")
            if has_fut:
                cells += (f" {row['futility_z']:.4f} | "
                          f"{row['futility_nominal_p']:.5f} | "
                          f"{row['futility_stop_h0']:.1%} |")
            lines.append(cells)
        if has_fut:
            cps = ", ".join(
                f"중간 {row['look']}: {row['cp_at_futility_alt']:.0%} "
                f"(추세대로면 {row['cp_at_futility_trend']:.0%})"
                for row in seq["looks_detail"]
                if row.get("cp_at_futility_alt") is not None)
            lines += ["", f"무익성 경계에서의 조건부 검정력 — {cps} "
                          "(가정한 효과가 맞다면 / 관측 추세가 이어진다면).",
                      "", f"실제 검정력 손실: {seq['target_power']:.1%} → "
                          f"{seq['power_same_n']:.1%} "
                          f"({seq['power_loss'] * 100:.1f}%p).",
                      "", f"무익성 경계는 **비구속적**입니다 — 무시하고 계속해도 전체 α는 "
                          f"{seq['achieved_alpha']:.4f} 이하이며, 지키면 효능 방향 α가 "
                          f"{seq['alpha_upper_nominal']:.4f} → "
                          + _josa(f"{seq['alpha_if_honored']:.4f}", "으로", "로")
                          + " 내려갑니다. 효과가 없을 때 "
                          f"중간에 멈출 확률 {seq['cumulative_futility_h0'][-2]:.1%}, "
                          f"효과가 있는데 잘못 멈출 확률 "
                          f"{seq['cumulative_futility_h1'][-2]:.1%}(= 소비한 β*)."]
        if seq.get("information_label", "누적 N") != "누적 N":
            total_info = seq.get("information_total") or 0
            unit = seq.get("information_unit", "")
            lines += ["", f"기대 {seq['information_label']}: 효과가 있으면 "
                          f"{seq['expected_fraction_h1'] * total_info:.0f}{unit}, "
                          f"없으면 {seq['expected_fraction_h0'] * total_info:.0f}{unit} "
                          f"(팽창계수 ×{seq['inflation']:.4f}). 조기중단이 아껴 주는 것은 "
                          "등록 인원이 아니라 추적기간입니다."]
        elif seq.get("expected_n_h1"):
            lines += ["", f"기대 표본수: 효과가 있으면 {seq['expected_n_h1']:.0f}명, "
                          f"없으면 {seq['expected_n_h0']:.0f}명 "
                          f"(팽창계수 ×{seq['inflation']:.4f})"]
    if plan.get("sensitivity") and plan["sensitivity"]["kind"] == "n_by_power_and_effect":
        sens = plan["sensitivity"]
        lines += ["", "**민감도 분석 (분석 표본수: 단위당 n / 총 N)**", ""]
        labels = sens.get("col_labels") or [f"×{c:g}" for c in sens["cols"]]
        lines.append("| 목표 검정력 | " + " | ".join(_md_cell(t) for t in labels) + " |")
        lines.append("|---" * (len(sens["cols"]) + 1) + "|")
        for power, row in zip(sens["rows"], sens["cells"]):
            cells = ["계산 불가" if c is None
                     else (f"{c['unit']:,}" if c.get("single_arm")
                           else f"{c['unit']:,} / {c['total']:,}") for c in row]
            lines.append(f"| {_fmt_power(power)} | " + " | ".join(cells) + " |")
    if plan.get("notes"):
        lines += ["", "**가정과 한계**", ""]
        lines += [f"- {n}" for n in plan["notes"]]
    if plan.get("references"):
        lines += ["", "**근거 문헌**", ""]
        lines += [f"- {r}" for r in plan["references"]]
    prov = plan.get("provenance")
    if prov:
        # 명령에 백틱이 들어와도 표·코드 스팬이 깨지지 않게 펜스 블록을 쓴다
        fence = "```"
        while fence in prov["command"]:
            fence += "`"
        lines += ["", "**재현 정보**", "",
                  f"- {prov['tool']} {prov['version']} · {prov['generated']}",
                  "", fence, prov["command"].replace("\n", " "), fence]
    return "\n".join(lines)


def _finite_only(value):
    """NaN/inf를 None으로 바꿔 항상 유효한 JSON이 되게 한다 (조용히 숨기지 않도록
    텍스트 출력에서는 '-'로 보인다)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite_only(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_only(v) for v in value]
    return value


def render_json(plan: dict) -> str:
    """재현 가능한 전체 파라미터 (다른 스크립트에서 파싱용)."""
    payload = dict(plan)
    if not plan.get("suppress_protocol_sentence"):
        payload["sentences"] = protocol_sentences(plan)
    return json.dumps(_finite_only(payload), ensure_ascii=False, indent=2, allow_nan=False)
