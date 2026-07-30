"""출력 — 사람이 읽는 표, 프로토콜에 붙여넣는 문장, 기계가 읽는 JSON.

표본수 계산의 결과물은 숫자 하나가 아니라 **프로토콜/IRB 문서에 들어갈 한 문단**이다.
그래서 여기서는 (1) 계산 근거를 한눈에 보는 표, (2) 한국어·영어 문장, (3) 재현에
필요한 모든 파라미터를 담은 JSON을 함께 만든다.
"""

from __future__ import annotations

import json
import math

__all__ = ["render_text", "render_markdown", "render_json", "protocol_sentences"]

_BAR = "─" * 74

#: 숫자를 한국어로 읽었을 때 종성이 있는지 (을/를, 이/가 선택용)
_DIGIT_HAS_FINAL = {"0": False, "1": True, "2": False, "3": True, "4": False,
                    "5": False, "6": True, "7": True, "8": True, "9": False}


def _has_final_consonant(text: str) -> bool:
    """마지막 글자에 종성이 있는가 (조사 선택용)."""
    for ch in reversed(str(text)):
        if ch.isspace() or ch in "()[]{}<>\"'`,.":
            continue
        if ch == "%":
            return False                      # '퍼센트' → 종성 없음
        if ch in _DIGIT_HAS_FINAL:
            return _DIGIT_HAS_FINAL[ch]
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:          # 한글 음절
            return (code - 0xAC00) % 28 != 0
        if ch.isalpha():                      # 영문은 자주 쓰는 관례를 따른다
            return ch.lower() in "bcdfghjklmnpqrstvwxz"
        return False
    return False


def _josa(text: str, with_final: str, without_final: str) -> str:
    """text에 알맞은 조사를 붙여 준다 (예: _josa('0.208', '을', '를'))."""
    return f"{text}{with_final if _has_final_consonant(text) else without_final}"


def _article(phrase: str, capital: bool = True) -> str:
    """영문 검정명에 알맞은 관사를 붙인다 (복수형·고유 절차명은 The/생략)."""
    lowered = phrase.lstrip()
    if lowered.startswith(("one", "uni", "eu")):     # 발음이 자음(w/j)으로 시작
        return (("A " if capital else "a ") + phrase)
    if lowered.startswith(("TOST", "ANCOVA")):
        art = "An" if lowered[0] in "AEIOU" else "The"
    elif lowered.split(" ")[0].endswith("s") and not lowered.startswith("z-"):
        art = ""          # 복수형 앞에는 관사를 쓰지 않는다
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
    elif design["key"] == "prop2":
        effect_kr = _josa(f"반응률 차이 {_fmt_num(effect['value'], 3)}", "을", "를")
        effect_en = (f"a difference in response rates of "
                     f"{_fmt_num(abs(effect['value']), 3)}")
    else:
        # 군 순서에 따라 부호가 바뀌므로 문장에서는 절댓값으로 쓴다
        effect_en = (f"{effect.get('name_en', effect['name'])} = "
                     f"{_fmt_num(abs(effect['value']), 3)}")
        if effect.get("analysis") in ("ancova", "change"):
            effect_en += (f" with a baseline correlation of {effect['baseline_r']:g} "
                          f"(design factor {effect['design_factor']:.3f})")
            effect_kr = _josa(
                f"{effect['name']} = {_fmt_num(effect['value'], 3)}"
                f"(기저값 상관 r = {effect['baseline_r']:g}, 설계배율 "
                f"{effect['design_factor']:.3f})", "을", "를")

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
        kr = (f"확보 가능한 표본이 {_alloc_line(plan['given']['allocation'])}일 때, "
              f"{design['test_kr']}, {alpha_kr} 조건에서의 검정력은 "
              f"{effect_kr.rstrip('을를')} 가정 하에 "
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
    return {"kr": kr, "en": en}


def _precision_sentences(plan: dict) -> dict:
    target = plan["target"]
    # 신뢰수준은 --alpha에서 유도한다 (예전에는 95%로 하드코딩돼, --alpha를 바꾸면
    # 프로토콜에 붙일 문장이 실제 계산과 다른 신뢰수준을 주장했다)
    level = f"{(1.0 - target['alpha']) * 100:g}%"
    if plan["design_key"] == "icc":
        kr = (f"예상 ICC {target['icc']:g}, 측정 {target['raters']}회 조건에서 "
              f"{level} 신뢰구간 폭을 {target['width']:g} 이내로 추정하려면 "
              f"{plan['n']:,}명이 필요하다 (총 측정 {plan['total_measurements']:,}회, "
              f"예상 폭 {plan['achieved_width']:.4g}).")
        en = (f"To estimate an ICC of {target['icc']:g} with {target['raters']} ratings per "
              f"subject to within a {level} CI width of {target['width']:g}, "
              f"{plan['n']:,} subjects are required "
              f"(expected width {plan['achieved_width']:.4g}; Bonett 2002).")
    else:
        kr = (f"두 방법 차이의 표준편차를 {target['sd_diff']:g}로 가정할 때, Bland–Altman "
              f"일치한계(LoA)의 {level} 신뢰구간 반폭을 {target['half_width']:g} 이내로 "
              f"추정하려면 {plan['n']:,}명이 필요하다 "
              f"(예상 반폭 {plan['achieved_half_width']:.4g}, "
              f"예상 LoA ±{1.959963984540054 * target['sd_diff']:.4g}).")
        en = (f"Assuming an SD of the between-method differences of {target['sd_diff']:g}, "
              f"{plan['n']:,} subjects give a {level} CI half-width of "
              f"{plan['achieved_half_width']:.4g} for each limit of agreement "
              f"(Bland & Altman 1999).")
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


def _sensitivity_text(sens: dict) -> list[str]:
    lines = ["", "■ 민감도 분석 (가정이 틀렸을 때 표본수가 어떻게 변하는가)"]
    if sens["kind"] == "n_by_power_and_effect":
        first, cell_w = 14, 16
        header = "  " + _pad("목표 검정력", first) + "".join(
            _pad("효과×" + format(c, "g"), cell_w, ">") for c in sens["cols"])
        lines.append(header)
        lines.append("  " + "-" * (first + cell_w * len(sens["cols"])))
        for power, row in zip(sens["rows"], sens["cells"]):
            cells = []
            for cell in row:
                if cell is None:
                    cells.append(_pad("계산 불가", cell_w, ">"))
                else:
                    cells.append(_pad(f"{cell['unit']:,}/총{cell['total']:,}", cell_w, ">"))
            lines.append("  " + _pad(_fmt_power(power), first) + "".join(cells))
        lines.append("  (표기: 단위당 n / 총 N — 분석 대상 기준)")
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
        detail = []
        if adjs["design_effect"] != 1.0:
            detail.append(f"설계효과 ×{adjs['design_effect']:.3f}")
        if adjs["dropout"]:
            detail.append(f"탈락 {adjs['dropout']:.0%}")
        out.append(f"▶ 모집 표본수        : {_alloc_line(enroll)}"
                   + (f"  ({', '.join(detail)})" if detail else ""))
    if plan["enrollment"].get("clusters"):
        clusters = plan["enrollment"]["clusters"]
        pretty = ", ".join(f"{k}: {v:,}개" for k, v in clusters.items())
        out.append(f"  군집 수            : {pretty}"
                   f" (군집당 {plan['enrollment']['cluster_size']}명"
                   + (" — 위 모집 인원은 군집 단위로 올린 값)"
                      if plan["enrollment"].get("before_cluster_rounding") else ")"))

    sentences = protocol_sentences(plan)
    out.append("")
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
    out.append(_BAR[:width])
    return "\n".join(out)


def _render_precision_text(plan: dict, width: int = 74) -> str:
    out = [_BAR[:width], f" powerplan — {plan['name_kr']}  [{plan['design_key']}]",
           _BAR[:width]]
    target = plan["target"]
    if plan["design_key"] == "icc":
        out.append(f" 예상 ICC     : {target['icc']:g}")
        out.append(f" 측정 횟수 k  : {target['raters']}")
        out.append(f" 목표 CI 폭   : {target['width']:g} (신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        out.append(f"▶ 필요한 대상자 수   : {plan['n']:,}명 "
                   f"(총 측정 {plan['total_measurements']:,}회)")
        out.append(f"  예상 CI 폭         : {plan['achieved_width']:.4f}")
    else:
        out.append(f" 차이의 SD    : {target['sd_diff']:g}")
        out.append(f" 목표 반폭    : {target['half_width']:g} "
                   f"(차이 SD의 {plan['ratio_to_sd']:.2f}배, 신뢰수준 {1 - target['alpha']:.0%})")
        out.append("")
        out.append(f"▶ 필요한 대상자 수   : {plan['n']:,}명")
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
        if plan["design_key"] == "icc":
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
    sentences = protocol_sentences(plan)
    lines += ["", "**프로토콜 문장 (KR)**", "", f"> {sentences['kr']}", "",
              "**Protocol sentence (EN)**", "", f"> {sentences['en']}"]
    if plan.get("sensitivity") and plan["sensitivity"]["kind"] == "n_by_power_and_effect":
        sens = plan["sensitivity"]
        lines += ["", "**민감도 분석 (분석 표본수: 단위당 n / 총 N)**", ""]
        lines.append("| 목표 검정력 | " + " | ".join(f"효과×{c:g}" for c in sens["cols"]) + " |")
        lines.append("|---" * (len(sens["cols"]) + 1) + "|")
        for power, row in zip(sens["rows"], sens["cells"]):
            cells = ["계산 불가" if c is None else f"{c['unit']:,} / {c['total']:,}" for c in row]
            lines.append(f"| {_fmt_power(power)} | " + " | ".join(cells) + " |")
    if plan.get("notes"):
        lines += ["", "**가정과 한계**", ""]
        lines += [f"- {n}" for n in plan["notes"]]
    if plan.get("references"):
        lines += ["", "**근거 문헌**", ""]
        lines += [f"- {r}" for r in plan["references"]]
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
    payload["sentences"] = protocol_sentences(plan)
    return json.dumps(_finite_only(payload), ensure_ascii=False, indent=2, allow_nan=False)
