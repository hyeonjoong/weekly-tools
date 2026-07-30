"""Human-readable report rendering (Korean + English) for an AnalysisResult."""

from __future__ import annotations

import csv
import io
import json
import math
from typing import Any, Dict, List

from .analyze import AnalysisResult
from .binary import BinaryResult
from .endpoints import CORRECTION_LABELS

__all__ = ["render_text", "render_json", "result_to_dict",
           "render_binary_text", "render_binary_json",
           "binary_to_dict", "binary_sentence",
           "render_multi_text", "render_multi_json", "multi_to_dict",
           "render_csv", "render_multi_csv"]


#: Substrings that mark a warning as "the input itself is suspect", as opposed
#: to a methodological caveat. If one is present the ready-to-paste sentence is
#: withheld: the descriptives make corruption obvious, but the sentence -- the
#: only line most users copy -- renders a plausible-looking median either way.
_INTEGRITY_MARKERS = (
    "결측 코드로 흔히 쓰이는 값",
    "사분위 범위의 3배",
    "대소문자/공백만 다른 그룹 라벨",
    "non-responder imputation",
    "해석할 수 없습니다",
)


def _integrity_warnings(res) -> List[str]:
    return [w for w in getattr(res, "warnings", [])
            if any(marker in w for marker in _INTEGRITY_MARKERS)]


def _render_sentence(res, L, sentence: str) -> None:
    """Emit the paste-ready sentence, or refuse to when the input is suspect."""
    flagged = _integrity_warnings(res)
    L("")
    if flagged:
        L("[논문용 문장 / Ready-to-paste sentence]")
        L("  ⚠ 자료 무결성 경고가 있어 논문용 문장을 생성하지 않았습니다.")
        L("    위 [!] 항목을 해결한 뒤 다시 실행하세요:")
        for w in flagged:
            L(f"      · {w.split('.')[0]}.")
        L("    (그래도 필요하면 --format json 의 'sentence' 필드에 초안이 있습니다.)")
        return
    L("[논문용 문장 / Ready-to-paste sentence]")
    L("  " + sentence)


def _fmt_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


#: Beyond this a fixed-point number stops being readable in any context.
_MAX_NUM_WIDTH = 16


def _num(x: float, d: int = 3, width: int = 0) -> str:
    """Format a number without ever overrunning its column.

    Fixed-point is what a clinical reader wants, but ``f"{x:.3f}"`` on a value
    of 1e6 is 11 characters and silently fuses the descriptives columns into one
    unreadable digit string -- and 1e6 is not exotic (KRW amounts, read counts,
    CFU/mL, raw sensor ticks). Above the column width we fall back to compact
    scientific notation.
    """
    if x != x:  # NaN
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "inf" if x > 0 else "-inf"
    text = f"{x:.{d}f}"
    if width <= 0:
        # No column to fit: only fall back for genuinely unreadable magnitudes,
        # and use the same threshold the table-wide switch uses so a column can
        # never mix fixed-point and scientific cells.
        return text if len(text) <= _MAX_NUM_WIDTH else f"{x:.{min(d, 3)}e}"
    if len(text) > width - 1:
        return f"{x:.{max(1, min(d, 3))}e}"
    return text


def _fmt_df(x: float, d: int = 3) -> str:
    """Integer df printed without decimals; fractional (Welch) df keeps them."""
    if x != x:  # NaN
        return "NaN"
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.{d}f}"


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append

    L("=" * 66)
    L("  statwise — 그룹 비교 통계 리포트 / Group comparison report")
    L("=" * 66)

    _render_descriptives(res, L)

    # Assumptions
    L("")
    L("[2] 가정 점검 / Assumption checks")
    if res.paired:
        dnc = res.diff_normality
        if dnc is None or dnc.pvalue is None:
            note = dnc.note if dnc else ""
            L(f"    정규성(차이값) Shapiro-Wilk: (건너뜀) {note}")
        else:
            verdict = ("정규성 위배 근거 없음" if dnc.normal
                       else "정규성 위배 의심")
            L(f"    정규성(차이값) Shapiro-Wilk [a−b]: W={_num(dnc.w)}, "
              f"p={_fmt_p(dnc.pvalue)}  → {verdict}")
        if res.n_pairs is not None:
            zero = f" (차이 0인 쌍 {res.n_zero_diff}개 제외)" if res.n_zero_diff else ""
            L(f"    대응 표본 n = {res.n_pairs}쌍{zero}")
        # Make the sign convention explicit so CSV row order can never silently
        # flip the reported direction of the effect.
        la, lb = res.groups[0].label, res.groups[1].label
        L(f"    비교 방향 direction: 차이 = ({la} − {lb})  "
          f"[양수면 {la} > {lb}]")
        _render_selected(res, L)
        _render_equivalence(res, L)
        _render_effects(res, L)
        _render_warnings(res, L)
        _render_sentence(res, L, _sentence(res))
        L("")
        return "\n".join(lines)
    for nc in res.normality:
        if nc.pvalue is None:
            L(f"    정규성 Shapiro-Wilk [{nc.label}]: (건너뜀) {nc.note}")
        else:
            verdict = ("정규성 위배 근거 없음" if nc.normal
                       else "정규성 위배 의심")
            low_power = ""
            n = next((g.n for g in res.groups if g.label == nc.label), None)
            if nc.normal and n is not None and n < 15:
                low_power = f" (n={n}, 검정력 낮음 — 완만한 비정규성은 못 잡습니다)"
            L(f"    정규성 Shapiro-Wilk [{nc.label}]: W={_num(nc.w)}, "
              f"p={_fmt_p(nc.pvalue)}  → {verdict}{low_power}")
    if res.levene is not None:
        lp = res.levene.pvalue
        if lp != lp or not math.isfinite(res.levene.statistic):  # NaN / inf
            L(f"    등분산 Levene(median): 판정 불가 "
              f"(분산이 0인 그룹 — 등분산 여부를 정의할 수 없음)")
        else:
            eq = ("등분산 위배 근거 없음" if lp > res.alpha_norm
                  else "등분산 위배 의심")
            L(f"    등분산 Levene(median): W={_num(res.levene.statistic)}, "
              f"p={_fmt_p(lp)}  → {eq}")

    _render_selected(res, L)
    _render_equivalence(res, L)
    _render_effects(res, L)

    # Post-hoc
    if res.pairwise:
        _render_posthoc(res, L)

    _render_warnings(res, L)

    # Publication sentence
    _render_sentence(res, L, _sentence(res))
    L("")
    return "\n".join(lines)


def _label_width(res: AnalysisResult, minimum: int = 16, cap: int = 30) -> int:
    """Column width for group labels: grow for long labels, never break rows."""
    widest = max((len(g.label) for g in res.groups), default=0)
    return max(minimum, min(cap, widest + 1))


def _elide(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def _render_descriptives(res: AnalysisResult, L) -> None:
    w = _label_width(res)
    any_missing = any(g.n_missing for g in res.groups)
    headers = ["mean", "sd", "median", "Q1", "Q3", "min", "max"]
    cells = []
    for g in res.groups:
        q1, q3 = g.quartiles()
        cells.append([g.mean, g.sd, g.median, q1, q3, g.minimum, g.maximum])
    # Size each column to its own content. A fixed 9/10-wide column silently
    # fused every number into one digit string once any value reached 1e6,
    # which is ordinary for counts, currency and raw sensor units.
    flat = [v for row in cells for v in row]
    widest = max((len(f"{v:.3f}") for v in flat
                  if v == v and math.isfinite(v)), default=0)
    if widest > _MAX_NUM_WIDTH:
        fmt = lambda v: _num(v, 3, 0) if not math.isfinite(v or 0.0) else (
            "NaN" if v != v else f"{v:.4e}")
        colw = 14      # "-1.0000e+300" is 12 chars; leave a separator
    else:
        fmt = lambda v: _num(v)
        colw = max(9, widest + 2)
    miss_h = f"{'miss':>6}" if any_missing else ""
    L("")
    L("[1] 기술통계 / Descriptives")
    head = f"    {'group':<{w}}{'n':>5}{miss_h}"
    head += "".join(f"{h:>{colw}}" for h in headers)
    L(head)
    for g, row in zip(res.groups, cells):
        miss = f"{g.n_missing:>6}" if any_missing else ""
        line = f"    {_elide(g.label, w):<{w}}{g.n:>5}{miss}"
        line += "".join(f"{fmt(v):>{colw}}" for v in row)
        L(line)
    if any_missing:
        L("    (miss = 원본 파일에 있었지만 결측/비수치라 분석에서 빠진 관측치 수)")


def _render_selected(res: AnalysisResult, L) -> None:
    L("")
    L("[3] 선택된 검정 / Selected test")
    L(f"    → {res.test_name}")
    L(f"      (근거: {res.reason})")
    df_str = ""
    if res.df is not None and res.df2 is not None:
        df_str = f", df=({_fmt_df(res.df)}, {_fmt_df(res.df2)})"
    elif res.df is not None:
        df_str = f", df={_fmt_df(res.df)}"
    stat_label = _stat_symbol(res.test_name)
    L(f"      {stat_label}={_num(res.statistic)}{df_str}, p={_fmt_p(res.pvalue)}")
    sig = "통계적으로 유의함" if res.significant else "유의하지 않음"
    adj = res.pvalue_adj
    if adj is not None and adj != res.pvalue:
        cmp = '<' if adj == adj and adj < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} "
          f"(보정 전 p={_fmt_p(res.pvalue)}, 엔드포인트 간 보정 후 "
          f"p(adj)={_fmt_p(adj)} {cmp} {res.alpha})")
    else:
        cmp = '<' if res.pvalue == res.pvalue and res.pvalue < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} (p{cmp}{res.alpha})")

    if res.mean_diff is not None and res.mean_diff_ci is not None:
        lo, hi = res.mean_diff_ci
        direction = ""
        if len(res.groups) == 2:
            direction = f" ({res.groups[0].label} − {res.groups[1].label})"
        L(f"      평균차 mean difference{direction} = {_num(res.mean_diff)} "
          f"[{int((1-res.alpha)*100)}% CI {_num(lo)}, {_num(hi)}]")

    loc = res.location
    if loc is not None:
        direction = ""
        if len(res.groups) == 2:
            direction = f" ({res.groups[0].label} − {res.groups[1].label})"
        if loc.ci_low is not None and loc.ci_high is not None:
            L(f"      위치차 Hodges-Lehmann{direction} = {_num(loc.estimate)} "
              f"[{int(loc.conf*100)}% CI {_num(loc.ci_low)}, {_num(loc.ci_high)}]")
        else:
            L(f"      위치차 Hodges-Lehmann{direction} = {_num(loc.estimate)} "
              f"(신뢰구간 생략: 표본이 작아 이 신뢰수준에서 분포무관 구간을 "
              f"만들 수 없습니다)")


_MODEL_LABEL = {"student": "Student's t (pooled)", "welch": "Welch",
                "paired": "paired t"}


def _render_equivalence(res: AnalysisResult, L) -> None:
    """Section [3b]: the TOST / non-inferiority verdict, if one was requested."""
    eq = res.equivalence
    if eq is None:
        return
    direction = ""
    if len(res.groups) == 2:
        direction = f" ({res.groups[0].label} − {res.groups[1].label})"
    model = _MODEL_LABEL.get(eq.model, eq.model)
    L("")
    if eq.kind == "tost":
        L("[3b] 등가성 검정 / Equivalence (TOST)")
        L(f"     등가 마진 margin: [{_num(eq.margin_low)}, {_num(eq.margin_high)}]"
          f"  (평균차{direction} 기준)")
    else:
        arrow = ("높을수록 좋음 (higher is better)"
                 if eq.direction == "higher_is_better"
                 else "낮을수록 좋음 (lower is better)")
        bound = eq.margin_low if eq.direction == "higher_is_better" else eq.margin_high
        L("[3b] 비열등성 검정 / Non-inferiority")
        L(f"     비열등성 마진 margin: {_num(abs(bound))}  (평균차{direction} 기준, {arrow})")
        side = "하한" if eq.direction == "higher_is_better" else "상한"
        rel = ">" if eq.direction == "higher_is_better" else "<"
        L(f"     → 기각 기준: {int(round(eq.conf * 100))}% 단측 신뢰{side} {rel} "
          f"{_num(bound)}  (점추정값이 아니라 신뢰한계로 판정합니다)")
    L(f"     평균차 diff = {_num(eq.diff)}, SE = {_num(eq.se)}, "
      f"df = {_fmt_df(eq.df)}  [t-모형: {model}]")
    if eq.kind == "tost":
        L(f"     H01 (diff ≤ low):  t = {_num(eq.t_low)}, p = {_fmt_p(eq.p_low)}")
        L(f"     H02 (diff ≥ high): t = {_num(eq.t_high)}, p = {_fmt_p(eq.p_high)}")
        L(f"     p(TOST) = max(p1, p2) = {_fmt_p(eq.pvalue)}")
        L(f"     {int(round(eq.conf * 100))}% CI [{_num(eq.ci_low)}, "
          f"{_num(eq.ci_high)}]")
        L(f"       ↳ 100(1−2α)% 구간입니다. 이 구간이 마진 안에 완전히 들어가는 것이 "
          f"p(TOST)<α 와 정확히 같은 판정이라서 α={eq.alpha}에서는 "
          f"{int(round(eq.conf * 100))}%를 씁니다 "
          f"(연구의 일반적 신뢰구간인 {int(round((1 - eq.alpha) * 100))}%가 아닙니다).")
        if eq.concluded:
            verdict = "등가(equivalence) 성립 — 차이가 마진 안에 있음"
        elif eq.ci_low is not None and eq.ci_low > eq.margin_high:
            verdict = ("등가 아님 — 차이가 마진 **위쪽 밖**에 있음이 입증됨 "
                       "(검정력 부족이 아니라 적극적 비동등)")
        elif eq.ci_high is not None and eq.ci_high < eq.margin_low:
            verdict = ("등가 아님 — 차이가 마진 **아래쪽 밖**에 있음이 입증됨 "
                       "(검정력 부족이 아니라 적극적 비동등)")
        else:
            verdict = ("결론 불가 — 구간이 마진 경계를 걸쳐 있어 등가도 비동등도 "
                       "입증되지 않았습니다 (검정력 부족일 수 있음)")
    else:
        t_stat = eq.t_low if eq.direction == "higher_is_better" else eq.t_high
        L(f"     t = {_num(t_stat)}, p = {_fmt_p(eq.pvalue)} (단측 one-sided)")
        if eq.direction == "higher_is_better":
            L(f"     {int(round(eq.conf * 100))}% 단측 하한 lower bound = "
              f"{_num(eq.ci_low)}")
        else:
            L(f"     {int(round(eq.conf * 100))}% 단측 상한 upper bound = "
              f"{_num(eq.ci_high)}")
        verdict = ("비열등성(non-inferiority) 성립"
                   if eq.concluded else "비열등성 미입증")
    L(f"     → α={eq.alpha}: {verdict}")


def _render_effects(res: AnalysisResult, L) -> None:
    L("")
    L("[4] 효과크기 / Effect size")
    for es in res.effects:
        ci = ""
        if es.ci_low is not None and es.ci_high is not None:
            pct = int(round(es.conf * 100))
            ci = f"  [{pct}% CI {_num(es.ci_low)}, {_num(es.ci_high)}]"
        mag = f"  ({es.magnitude})" if es.magnitude else ""
        L(f"    {es.name} = {_num(es.value)}{ci}{mag}")


def _demoted(res) -> bool:
    """True when an across-endpoint correction withdrew this result's significance."""
    adj = getattr(res, "pvalue_adj", None)
    return (adj is not None and adj != res.pvalue
            and res.pvalue < res.alpha <= adj)


def _render_posthoc(res: AnalysisResult, L) -> None:
    label = "Benjamini-Hochberg FDR" if res.correction == "bh" else "Holm-Bonferroni"
    L("")
    L(f"[5] 사후검정 / Post-hoc ({label} 보정)")
    if _demoted(res):
        # The post-hoc ran because the *unadjusted* omnibus was significant.
        # Printing a table of stars under "유의하지 않음" invites the reader to
        # quote them anyway.
        L("    ⚠ 이 엔드포인트의 omnibus는 엔드포인트 간 보정 후 유의하지 "
          "않습니다. 아래 사후검정은 보정 전 omnibus를 근거로 수행된 것이므로 "
          "탐색적(exploratory) 결과로만 보고하세요.")
    conf_pct = int(round((1.0 - res.alpha) * 100))
    L(f"    {'comparison':<26}{'n':>9}{'difference':>12}"
      f"{f'{conf_pct}% CI(비보정)':>24}{'p(adj)':>9}{'effect':>9}{'sig':>4}")
    for pw in res.pairwise:
        comp = _elide(f"{pw.a} vs {pw.b}", 25)
        star = "*" if pw.significant else ""
        ns = f"{pw.n_a}/{pw.n_b}"
        if pw.diff_ci is not None:
            ci = f"[{_num(pw.diff_ci[0], 2)}, {_num(pw.diff_ci[1], 2)}]"
        else:
            ci = "(CI 없음)"
        L(f"    {comp:<26}{ns:>9}{_num(pw.diff, 2):>12}{ci:>24}"
          f"{_fmt_p(pw.pvalue_adj):>9}{_num(pw.effect_value, 2):>9}{star:>4}")
    kinds = {pw.diff_label for pw in res.pairwise}
    L(f"    (difference = {', '.join(sorted(kinds))}, 첫 그룹 − 둘째 그룹 / "
      f"first minus second; effect = {res.pairwise[0].effect_name}; "
      f"p(adj) = 보정 후 p, p(raw)는 JSON/CSV에 있습니다)")
    L("    (신뢰구간은 비교 1건 기준이며 다중비교 보정을 반영하지 않습니다 — "
      "동시신뢰구간이 아니므로 별표와 다를 수 있습니다)")


def _render_warnings(res: AnalysisResult, L) -> None:
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")


def _jnum(x) -> Any:
    """JSON-safe number: NaN/inf -> None (JSON has no NaN)."""
    if x is None:
        return None
    if isinstance(x, float) and (x != x or math.isinf(x)):
        return None
    return x


def result_to_dict(res: AnalysisResult) -> Dict[str, Any]:
    """Serialize an AnalysisResult into plain JSON-friendly dict."""
    groups = []
    for g in res.groups:
        q1, q3 = g.quartiles()
        # the per-group mean CI tracks --alpha like every other interval
        lo, hi = g.mean_ci(1.0 - res.alpha)
        groups.append({
            "label": g.label, "n": g.n, "n_missing": g.n_missing,
            "mean": _jnum(g.mean), "sd": _jnum(g.sd),
            "mean_ci": [_jnum(lo), _jnum(hi)],
            "mean_ci_conf": 1.0 - res.alpha,
            "median": _jnum(g.median), "q1": _jnum(q1), "q3": _jnum(q3),
            "min": _jnum(g.minimum), "max": _jnum(g.maximum),
        })
    out: Dict[str, Any] = {
        "schema": "statwise/analysis/1",
        "paired": res.paired,
        "alpha": res.alpha,
        "alpha_norm": res.alpha_norm,
        "groups": groups,
        "test": {
            "name": res.test_name,
            "statistic": _jnum(res.statistic),
            "df": _jnum(res.df),
            "df2": _jnum(res.df2),
            "pvalue": _jnum(res.pvalue),
            "significant": bool(res.significant),
            "reason": res.reason,
        },
        "effects": [
            {"name": e.name, "value": _jnum(e.value),
             "ci_low": _jnum(e.ci_low), "ci_high": _jnum(e.ci_high),
             "conf": e.conf, "magnitude": e.magnitude}
            for e in res.effects
        ],
        "warnings": list(res.warnings),
    }
    if res.mean_diff is not None:
        out["mean_diff"] = _jnum(res.mean_diff)
        if res.mean_diff_ci is not None:
            out["mean_diff_ci"] = [_jnum(res.mean_diff_ci[0]),
                                   _jnum(res.mean_diff_ci[1])]
    if res.location is not None:
        loc = res.location
        out["hodges_lehmann"] = {
            "estimate": _jnum(loc.estimate),
            "ci_low": _jnum(loc.ci_low), "ci_high": _jnum(loc.ci_high),
            "conf": loc.conf, "method": loc.method}
    if res.paired:
        out["n_pairs"] = res.n_pairs
        out["n_zero_diff"] = res.n_zero_diff
        if res.diff_normality is not None:
            dnc = res.diff_normality
            out["diff_normality"] = {
                "w": _jnum(dnc.w), "pvalue": _jnum(dnc.pvalue),
                "normal": dnc.normal, "note": dnc.note}
    else:
        out["normality"] = [
            {"label": nc.label, "w": _jnum(nc.w), "pvalue": _jnum(nc.pvalue),
             "normal": nc.normal, "note": nc.note}
            for nc in res.normality
        ]
        if res.levene is not None:
            out["levene"] = {
                "statistic": _jnum(res.levene.statistic),
                "df_between": _jnum(res.levene.df_between),
                "df_within": _jnum(res.levene.df_within),
                "pvalue": _jnum(res.levene.pvalue)}
    if res.equivalence is not None:
        eq = res.equivalence
        out["equivalence"] = {
            "kind": eq.kind, "model": eq.model, "direction": eq.direction or None,
            "diff": _jnum(eq.diff), "se": _jnum(eq.se), "df": _jnum(eq.df),
            "margin_low": _jnum(eq.margin_low),
            "margin_high": _jnum(eq.margin_high),
            "t_low": _jnum(eq.t_low), "p_low": _jnum(eq.p_low),
            "t_high": _jnum(eq.t_high), "p_high": _jnum(eq.p_high),
            "pvalue": _jnum(eq.pvalue), "alpha": eq.alpha, "conf": eq.conf,
            "ci_low": _jnum(eq.ci_low), "ci_high": _jnum(eq.ci_high),
            "concluded": bool(eq.concluded)}
    if res.pairwise:
        out["correction"] = res.correction
        out["pairwise"] = [
            {"a": pw.a, "b": pw.b, "test": pw.test,
             "n_a": pw.n_a, "n_b": pw.n_b,
             "statistic": _jnum(pw.statistic),
             "pvalue_raw": _jnum(pw.pvalue_raw),
             "pvalue_adj": _jnum(pw.pvalue_adj),
             "effect_name": pw.effect_name, "effect_value": _jnum(pw.effect_value),
             "difference": _jnum(pw.diff), "difference_label": pw.diff_label,
             "difference_ci_basis": "per-comparison (not simultaneous)",
             "difference_ci": (None if pw.diff_ci is None else
                               [_jnum(pw.diff_ci[0]), _jnum(pw.diff_ci[1])]),
             "significant": bool(pw.significant)}
            for pw in res.pairwise
        ]
    out["sentence"] = _sentence(res)
    return out


def render_json(res: AnalysisResult, indent: int = 2) -> str:
    """Render an AnalysisResult as a JSON string."""
    return json.dumps(result_to_dict(res), indent=indent, ensure_ascii=False)


def _stat_symbol(test_name: str) -> str:
    if "ANOVA" in test_name:
        return "F"
    if "Wilcoxon" in test_name:
        return "W"
    if test_name.startswith(("Student", "Welch", "Paired")):
        return "t"
    if "Mann-Whitney" in test_name:
        return "U"
    if "Kruskal" in test_name:
        return "H"
    return "stat"


def _hl_phrase(res: AnalysisResult) -> str:
    """', median difference = X, 95% CI [lo, hi]' for a rank test, else ''."""
    loc = res.location
    if loc is None or loc.ci_low is None or loc.ci_high is None:
        return ""
    return (f", Hodges-Lehmann location shift (the median of all paired "
            f"between-group differences, which is not the difference of the "
            f"two medians quoted above) = {_num(loc.estimate,2)}, "
            f"{int(loc.conf*100)}% CI [{_num(loc.ci_low,2)}, {_num(loc.ci_high,2)}]")


def _equiv_phrase(res: AnalysisResult) -> str:
    """APA-style sentence for the TOST / non-inferiority verdict, or ''."""
    eq = res.equivalence
    if eq is None:
        return ""
    psign = "p < 0.001" if eq.pvalue < 0.001 else f"p = {eq.pvalue:.3f}"
    pct = int(round(eq.conf * 100))
    # A rank-based main test reports a median shift; the margin test is run on
    # the mean difference. One sentence must not carry two estimands unmarked.
    estimand = ""
    if eq.model in ("welch", "student", "paired") and \
            ("Mann-Whitney" in res.test_name or "Wilcoxon" in res.test_name):
        estimand = (" (assessed on the mean difference under a t-model, not on "
                    "the median shift tested above)")
    if eq.kind == "tost":
        if eq.concluded:
            verdict = "equivalence was established"
        elif (eq.ci_low is not None and eq.ci_low > eq.margin_high) or \
                (eq.ci_high is not None and eq.ci_high < eq.margin_low):
            verdict = ("the difference was shown to lie outside the margin "
                       "(non-equivalence demonstrated)")
        else:
            verdict = ("equivalence was not established (the interval spans a "
                       "margin boundary, so neither equivalence nor "
                       "non-equivalence was shown)")
        outside = (not eq.concluded and
                   ((eq.ci_low is not None and eq.ci_low > eq.margin_high) or
                    (eq.ci_high is not None and eq.ci_high < eq.margin_low)))
        if outside:
            # p(TOST) is large here by construction; quoting it as the evidence
            # for non-equivalence reads as a contradiction.
            return (
                f" Using two one-sided tests against an equivalence margin of "
                f"[{_num(eq.margin_low, 2)}, {_num(eq.margin_high, 2)}]"
                f"{estimand}, {verdict}: the {pct}% CI "
                f"[{_num(eq.ci_low, 2)}, {_num(eq.ci_high, 2)}] lies wholly "
                f"outside the margin. (The TOST {psign} tests equivalence and "
                f"is not the basis of this conclusion.)")
        return (
            f" Using two one-sided tests against an equivalence margin of "
            f"[{_num(eq.margin_low, 2)}, {_num(eq.margin_high, 2)}]{estimand}, "
            f"{verdict} ({psign}; {pct}% CI [{_num(eq.ci_low, 2)}, "
            f"{_num(eq.ci_high, 2)}]).")
    if eq.direction == "higher_is_better":
        bound_txt = f"lower bound {_num(eq.ci_low, 2)}"
        margin_txt = f"{_num(abs(eq.margin_low), 2)} (higher is better)"
    else:
        bound_txt = f"upper bound {_num(eq.ci_high, 2)}"
        margin_txt = f"{_num(abs(eq.margin_high), 2)} (lower is better)"
    verdict = ("non-inferiority was established" if eq.concluded
               else "non-inferiority was not established")
    return (f" Against a non-inferiority margin of {margin_txt}{estimand}, "
            f"{verdict} (mean difference {_num(eq.diff, 2)}, one-sided {psign}; "
            f"{pct}% one-sided {bound_txt}).")


def _adjusted_phrase(res, method: str = "", family: int = 0) -> str:
    """Name the across-endpoint adjustment whenever it changed the verdict."""
    adj = getattr(res, "pvalue_adj", None)
    if adj is None or adj == res.pvalue:
        return ""
    padj = "p < .001" if adj < 0.001 else f"p = {adj:.3f}"
    label = method or "multiple-endpoint"
    fam = f" across {family} endpoints" if family else ""
    changed = (res.pvalue < res.alpha) != (adj < res.alpha)
    tail = (" (the unadjusted result would have been significant)."
            if changed and not res.significant else ".")
    return (f" After {label} adjustment{fam} the adjusted {padj}{tail}")


def _sentence(res: AnalysisResult) -> str:
    return _sentence_core(res) + _adjusted_phrase(res) + _equiv_phrase(res)


def _sentence_core(res: AnalysisResult) -> str:
    # res.significant already reflects any across-endpoint adjustment; the
    # p quoted here is the unadjusted one, and _adjusted_phrase names the other.
    adjusted = (getattr(res, "pvalue_adj", None) is not None
                and res.pvalue_adj != res.pvalue)
    label = "unadjusted p" if adjusted else "p"
    psign = ("{} < 0.001".format(label)
             if res.pvalue == res.pvalue and res.pvalue < 0.001
             else f"{label} = {res.pvalue:.3f}")
    if res.paired:
        a, b = res.groups
        es = res.effects[0]
        sig = ("statistically significant" if res.significant
               else "not statistically significant")
        if res.test_name.startswith("Paired"):
            md = ""
            if res.mean_diff is not None and res.mean_diff_ci is not None:
                md = (f" The mean change ({a.label} − {b.label}) was "
                      f"{_num(res.mean_diff,2)} "
                      f"({int(round((1-res.alpha)*100))}% CI "
                      f"{_num(res.mean_diff_ci[0],2)} to "
                      f"{_num(res.mean_diff_ci[1],2)}).")
            return (
                f"{a.label} (M = {_num(a.mean,2)}, SD = {_num(a.sd,2)}) and "
                f"{b.label} (M = {_num(b.mean,2)}, SD = {_num(b.sd,2)}) were "
                f"compared in {res.n_pairs} matched pairs using a "
                f"paired-samples t-test; the difference was "
                f"{sig} (t({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,2)}).{md}")
        return (
            f"{a.label} (Mdn = {_num(a.median,2)}) and {b.label} "
            f"(Mdn = {_num(b.median,2)}) were compared in {res.n_pairs} matched "
            f"pairs"
            + (f" ({res.n_pairs - (res.n_zero_diff or 0)} with a non-zero "
               f"difference contributing to the test)"
               if res.n_zero_diff else "")
            + f" using a Wilcoxon signed-rank test; the difference was {sig} "
            f"(W = {_num(res.statistic,1)}, {psign}, "
            f"{es.name} = {_num(es.value,2)}){_hl_phrase(res)}.")
    if len(res.groups) == 2:
        a, b = res.groups
        es = res.effects[0]
        if res.test_name.startswith(("Student", "Welch")):
            test_phrase = ("an independent-samples t-test"
                           if res.test_name.startswith("Student")
                           else "Welch's t-test")
            sig_phrase = ("statistically significant" if res.significant
                          else "not statistically significant")
            md = ""
            if res.mean_diff is not None and res.mean_diff_ci is not None:
                md = (f" The mean difference ({a.label} − {b.label}) was "
                      f"{_num(res.mean_diff,2)} "
                      f"({int(round((1-res.alpha)*100))}% CI "
                      f"{_num(res.mean_diff_ci[0],2)} to "
                      f"{_num(res.mean_diff_ci[1],2)}).")
            return (
                f"{a.label} (n = {a.n}, M = {_num(a.mean,2)}, "
                f"SD = {_num(a.sd,2)}) and {b.label} (n = {b.n}, "
                f"M = {_num(b.mean,2)}, SD = {_num(b.sd,2)}) were compared "
                f"using {test_phrase}; the difference was {sig_phrase} "
                f"(t({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,2)}).{md}")
        return (
            f"{a.label} (n = {a.n}, Mdn = {_num(a.median,2)}) and {b.label} "
            f"(n = {b.n}, Mdn = {_num(b.median,2)}) were compared using a "
            f"Mann-Whitney U test; "
            f"the difference was "
            f"{'statistically significant' if res.significant else 'not statistically significant'} "
            f"(U = {_num(res.statistic,1)}, {psign}, "
            f"{es.name} = {_num(es.value,2)}){_hl_phrase(res)}.")
    # 3+ groups
    es = res.effects[0]
    rank_based = "Kruskal" in res.test_name
    if rank_based:
        summary = "; ".join(
            f"{g.label} n = {g.n}, Mdn = {_num(g.median,2)}" for g in res.groups)
    else:
        summary = "; ".join(
            f"{g.label} n = {g.n}, M = {_num(g.mean,2)}, SD = {_num(g.sd,2)}"
            for g in res.groups)
    if "ANOVA" in res.test_name:
        anova_name = ("Welch ANOVA" if res.test_name.startswith("Welch")
                      else "one-way ANOVA")
        verb = ("showed a significant effect of group" if res.significant
                else "did not show a significant effect of group")
        eta_note = ("" if not res.test_name.startswith("Welch") else
                    " (computed from the equal-variance sums of squares and "
                    "therefore approximate under heteroscedasticity)")
        head = (f"A {anova_name} {verb} "
                f"(F({_fmt_df(res.df)}, {_fmt_df(res.df2)}) = {_num(res.statistic,2)}, "
                f"{psign}, {es.name} = {_num(es.value,3)}{eta_note}).")
    else:
        verb = ("showed a significant difference across groups"
                if res.significant
                else "did not show a significant difference across groups")
        head = (f"A Kruskal-Wallis test {verb} "
                f"(H({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,3)}).")
    head = f"Group summaries were {summary}. " + head
    if res.pairwise:
        corr = ("Benjamini-Hochberg (FDR)" if res.correction == "bh"
                else "Holm-Bonferroni")
        conf_pct = int(round((1.0 - res.alpha) * 100))
        sigs = [pw for pw in res.pairwise if pw.significant]
        if sigs:
            detail = "; ".join(
                f"{pw.a} vs {pw.b}: {_num(pw.diff,2)}"
                + ("" if pw.diff_ci is None else
                   f" ({conf_pct}% per-comparison CI {_num(pw.diff_ci[0],2)} "
                   f"to {_num(pw.diff_ci[1],2)})")
                + f", adjusted {'p < 0.001' if pw.pvalue_adj < 0.001 else f'p = {pw.pvalue_adj:.3f}'}"
                for pw in sigs)
            head += (f" Post-hoc comparisons with {corr} correction were "
                     f"significant for — {detail}.")
        else:
            head += (f" No pairwise comparison survived {corr} correction.")
    return head


# ==========================================================================
# binary (yes/no) endpoints
# ==========================================================================

def _pct(x: float, d: int = 1) -> str:
    if x != x:
        return "NaN"
    return f"{100.0 * x:.{d}f}%"


def _ratio(x: float, d: int = 3) -> str:
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "∞"
    return f"{x:.{d}f}"


def _binary_label_width(res: BinaryResult, minimum: int = 16,
                        cap: int = 30) -> int:
    widest = max((len(g.label) for g in res.groups), default=0)
    return max(minimum, min(cap, widest + 1))


def render_binary_text(res: BinaryResult) -> str:
    """Human-readable report for a binary-endpoint comparison."""
    lines: List[str] = []
    L = lines.append
    conf_pct = int(round((1.0 - res.alpha) * 100))
    w = _binary_label_width(res)
    any_missing = any(g.n_missing for g in res.groups)

    L("=" * 66)
    L("  statwise — 이진 결과 비교 리포트 / Binary endpoint report")
    L("=" * 66)
    L("")
    L("[1] 반응률 / Event rates")
    miss_h = f"{'miss':>6}" if any_missing else ""
    L(f"    {'group':<{w}}{'events':>8}{'n':>6}{miss_h}{'rate':>9}"
      f"{f'{conf_pct}% CI (Wilson)':>22}")
    for g in res.groups:
        lo, hi = g.ci(1.0 - res.alpha)
        miss = f"{g.n_missing:>6}" if any_missing else ""
        ci = f"[{_pct(lo)}, {_pct(hi)}]"
        L(f"    {_elide(g.label, w):<{w}}{g.events:>8}{g.n:>6}{miss}"
          f"{_pct(g.proportion):>9}{ci:>22}")
    if any_missing:
        L("    (miss = 원본 파일에 있었지만 결측/해석 불가라 빠진 관측치 수)")

    L("")
    L("[2] 선택된 검정 / Selected test")
    L(f"    → {res.test_name}")
    L(f"      (근거: {res.reason})")
    if res.statistic is not None and res.df is not None:
        L(f"      χ²={_num(res.statistic)}, df={_fmt_df(res.df)}, "
          f"p={_fmt_p(res.pvalue)}")
    else:
        L(f"      p={_fmt_p(res.pvalue)}")
    sig = "통계적으로 유의함" if res.significant else "유의하지 않음"
    adj = res.pvalue_adj
    if adj is not None and adj != res.pvalue:
        cmp = '<' if adj == adj and adj < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} "
          f"(보정 전 p={_fmt_p(res.pvalue)}, 엔드포인트 간 보정 후 "
          f"p(adj)={_fmt_p(adj)} {cmp} {res.alpha})")
    else:
        cmp = '<' if res.pvalue == res.pvalue and res.pvalue < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} (p{cmp}{res.alpha})")
    if res.pvalue_yates is not None and "Yates" not in res.test_name:
        L(f"      (참고: Yates 연속성 보정 카이제곱 p={_fmt_p(res.pvalue_yates)})")
    L(f"      기대빈도 최솟값 min expected = {_num(res.expected_min, 2)}")

    if res.estimates:
        L("")
        L("[3] 효과 크기 / Effect measures")
        for est in res.estimates:
            if est.name.startswith("Risk difference"):
                shown = _pct(est.value)
                ci = ("" if est.ci_low is None else
                      f"  [{conf_pct}% CI {_pct(est.ci_low)}, {_pct(est.ci_high)}]")
            elif est.name.startswith("Number needed") or "NNT" in est.name:
                shown = _ratio(est.value, 1)
                ci = ("" if est.ci_low is None else
                      f"  [{conf_pct}% CI {_ratio(est.ci_low, 1)}, "
                      f"{_ratio(est.ci_high, 1)}]")
            else:
                shown = _ratio(est.value)
                ci = ("" if est.ci_low is None else
                      f"  [{conf_pct}% CI {_ratio(est.ci_low)}, "
                      f"{_ratio(est.ci_high)}]")
            mag = f"  ({est.magnitude})" if est.magnitude else ""
            L(f"    {est.name} = {shown}{ci}{mag}")
            if est.method:
                L(f"      방법: {est.method}")
            if est.note:
                L(f"      주: {est.note}")
        if len(res.groups) == 2:
            a, b = res.groups
            L(f"    (기준 reference = {b.label}: RD = p({a.label}) − "
              f"p({b.label}), RR/OR = {a.label} ÷ {b.label})")

    if res.pairwise:
        label = ("Benjamini-Hochberg FDR" if res.correction == "bh"
                 else "Holm-Bonferroni")
        L("")
        L(f"[4] 사후검정 / Post-hoc ({label} 보정)")
        if _demoted(res):
            L("    ⚠ 이 엔드포인트의 omnibus는 엔드포인트 간 보정 후 유의하지 "
              "않습니다 — 아래는 탐색적 결과입니다.")
        L(f"    {'comparison':<26}{'n':>11}{'RD':>9}"
          f"{f'{conf_pct}% CI':>22}{'p(adj)':>9}{'sig':>4}")
        for pw in res.pairwise:
            comp = _elide(f"{pw.a} vs {pw.b}", 25)
            star = "*" if pw.significant else ""
            ns = f"{pw.n_a}/{pw.n_b}"
            ci = ("(CI 없음)" if pw.rd_ci_low is None else
                  f"[{_pct(pw.rd_ci_low)}, {_pct(pw.rd_ci_high)}]")
            L(f"    {comp:<26}{ns:>11}{_pct(pw.risk_diff):>9}{ci:>22}"
              f"{_fmt_p(pw.pvalue_adj):>9}{star:>4}")
        L("    (RD = 위험차, 첫 그룹 − 둘째 그룹 / first minus second)")

    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for warn in res.warnings:
            L(f"    - {warn}")

    _render_sentence(res, L, binary_sentence(res) + _adjusted_phrase(res))
    L("")
    return "\n".join(lines)


def binary_sentence(res: BinaryResult) -> str:
    """APA-ish sentence for a binary endpoint comparison."""
    adjusted = (getattr(res, "pvalue_adj", None) is not None
                and res.pvalue_adj != res.pvalue)
    label = "unadjusted p" if adjusted else "p"
    psign = ("{} < 0.001".format(label)
             if res.pvalue == res.pvalue and res.pvalue < 0.001
             else f"{label} = {res.pvalue:.3f}")
    conf_pct = int(round((1.0 - res.alpha) * 100))
    sig = ("statistically significant" if res.significant
           else "not statistically significant")
    if len(res.groups) == 2:
        a, b = res.groups
        rates = (f"{a.events}/{a.n} ({_pct(a.proportion)}) in {a.label} versus "
                 f"{b.events}/{b.n} ({_pct(b.proportion)}) in {b.label}")
        by = {e.name.split(" (")[0]: e for e in res.estimates}
        rd = by.get("Risk difference")
        rr = by.get("Risk ratio")
        if "Fisher" in res.test_name:
            test, stat = "Fisher's exact test", ""
        else:
            test = "a chi-square test"
            stat = ("" if res.statistic is None else
                    f"χ²({_fmt_df(res.df)}) = {_num(res.statistic, 2)}, ")
        out = (f"The event rate was {rates}; the difference was {sig} by "
               f"{test} ({stat}{psign}).")
        if rd is not None and rd.ci_low is not None:
            out += (f" Risk difference {_pct(rd.value)} "
                    f"({conf_pct}% CI {_pct(rd.ci_low)} to {_pct(rd.ci_high)})")
            # Only quote the risk ratio when it is a finite, estimable number —
            # a sentence reading "risk ratio NaN" must never reach a manuscript.
            if (rr is not None and rr.ci_low is not None
                    and math.isfinite(rr.value)):
                out += (f"; risk ratio {_ratio(rr.value, 2)} "
                        f"({conf_pct}% CI {_ratio(rr.ci_low, 2)} to "
                        f"{_ratio(rr.ci_high, 2)})")
            out += "."
        return out
    rates = ", ".join(f"{g.label} {g.events}/{g.n} ({_pct(g.proportion)})"
                      for g in res.groups)
    stat = ("" if res.statistic is None else
            f"χ²({_fmt_df(res.df)}) = {_num(res.statistic, 2)}, ")
    out = (f"Event rates were {rates}; the difference across the "
           f"{len(res.groups)} groups was {sig} ({stat}{psign}).")
    if res.pairwise:
        corr = ("Benjamini-Hochberg (FDR)" if res.correction == "bh"
                else "Holm-Bonferroni")
        sigs = [pw for pw in res.pairwise if pw.significant]
        if sigs:
            detail = "; ".join(
                f"{pw.a} vs {pw.b}: risk difference {_pct(pw.risk_diff)}"
                + ("" if pw.rd_ci_low is None else
                   f" ({conf_pct}% CI {_pct(pw.rd_ci_low)} to "
                   f"{_pct(pw.rd_ci_high)})")
                + (", adjusted p < .001" if pw.pvalue_adj < 0.001
                   else f", adjusted p = {pw.pvalue_adj:.3f}")
                for pw in sigs)
            out += (f" Pairwise comparisons with {corr} correction were "
                    f"significant for — {detail}.")
        else:
            out += f" No pairwise comparison survived {corr} correction."
    return out


def binary_to_dict(res: BinaryResult) -> Dict[str, Any]:
    """Serialize a BinaryResult into a plain JSON-friendly dict."""
    groups = []
    for g in res.groups:
        lo, hi = g.ci(1.0 - res.alpha)
        groups.append({
            "label": g.label, "events": g.events, "n": g.n,
            "n_missing": g.n_missing, "proportion": _jnum(g.proportion),
            "ci_low": _jnum(lo), "ci_high": _jnum(hi)})
    out: Dict[str, Any] = {
        "schema": "statwise/binary/1",
        "alpha": res.alpha,
        "groups": groups,
        "test": {
            "name": res.test_name,
            "statistic": _jnum(res.statistic),
            "df": _jnum(res.df),
            "pvalue": _jnum(res.pvalue),
            "pvalue_yates": _jnum(res.pvalue_yates),
            "significant": bool(res.significant),
            "min_expected": _jnum(res.expected_min),
            "reason": res.reason,
        },
        "estimates": [
            {"name": e.name, "value": _jnum(e.value), "ci_low": _jnum(e.ci_low),
             "ci_high": _jnum(e.ci_high), "conf": e.conf, "method": e.method,
             "magnitude": e.magnitude, "note": e.note}
            for e in res.estimates
        ],
        "warnings": list(res.warnings),
    }
    if res.pairwise:
        out["correction"] = res.correction
        out["pairwise"] = [
            {"a": pw.a, "b": pw.b, "test": pw.test,
             "n_a": pw.n_a, "n_b": pw.n_b,
             "pvalue_raw": _jnum(pw.pvalue_raw),
             "pvalue_adj": _jnum(pw.pvalue_adj),
             "risk_diff": _jnum(pw.risk_diff),
             "risk_diff_ci": (None if pw.rd_ci_low is None else
                              [_jnum(pw.rd_ci_low), _jnum(pw.rd_ci_high)]),
             "significant": bool(pw.significant)}
            for pw in res.pairwise
        ]
    out["sentence"] = binary_sentence(res)
    return out


def render_binary_json(res: BinaryResult, indent: int = 2) -> str:
    return json.dumps(binary_to_dict(res), indent=indent, ensure_ascii=False)


# ==========================================================================
# several endpoints at once
# ==========================================================================

def _endpoint_effect(res) -> str:
    """A one-cell effect summary appropriate to whatever test was run."""
    if isinstance(res, BinaryResult):
        for est in res.estimates:
            if est.name.startswith("Risk difference"):
                return f"RD {_pct(est.value)}"
            if est.name.startswith("Cramér"):
                return f"V {_num(est.value, 2)}"
        return ""
    if res.effects:
        es = res.effects[0]
        short = (es.name.replace("Hedges' ", "").replace("Cohen's ", "")
                 .replace("rank-biserial r", "rb")
                 .replace("eta-squared (H)", "eta2H")
                 .replace("eta-squared", "eta2")
                 .replace("Cliff's delta", "delta"))
        return _elide(f"{short} {_num(es.value, 2)}", 13)
    return ""


def _short_test(name: str) -> str:
    for long, short in (("Student's t-test", "Student t"),
                        ("Welch's t-test", "Welch t"),
                        ("Mann-Whitney U test", "Mann-Whitney"),
                        ("Kruskal-Wallis H test", "Kruskal-Wallis"),
                        ("One-way ANOVA", "ANOVA"),
                        ("Welch's ANOVA", "Welch ANOVA"),
                        ("Paired t-test", "Paired t"),
                        ("Wilcoxon signed-rank test", "Wilcoxon"),
                        ("Chi-square test of independence", "Chi-square"),
                        ("Chi-square test (Yates-corrected)", "Chi-sq (Yates)"),
                        ("Fisher's exact test", "Fisher exact")):
        if name == long:
            return short
    return name


def render_multi_text(multi, detail: bool = True) -> str:
    """Summary table across endpoints, optionally followed by full reports."""
    lines: List[str] = []
    L = lines.append
    corr = CORRECTION_LABELS.get(multi.correction, multi.correction)
    ok = multi.analysed

    L("=" * 78)
    L("  statwise — 다중 엔드포인트 리포트 / Multi-endpoint report")
    L("=" * 78)
    L("")
    L(f"[요약] 엔드포인트 {len(ok)}개 "
      f"(분석 실패 {len(multi.failed)}개) — 엔드포인트 간 보정: {corr}")
    L("")
    name_w = max([16] + [min(24, len(r.name) + 1) for r in multi.runs])
    L(f"    {'endpoint':<{name_w}}{'test':<16}{'effect':>14}{'p(raw)':>9}"
      f"{'p(adj)':>9}{'sig':>5}")
    for run in ok:
        res = run.result
        adj = res.pvalue_adj if res.pvalue_adj is not None else res.pvalue
        sig = "*" if (adj == adj and adj < multi.alpha) else ""
        L(f"    {_elide(run.name, name_w):<{name_w}}"
          f"{_short_test(res.test_name):<16}{_endpoint_effect(res):>14}"
          f"{_fmt_p(res.pvalue):>9}{_fmt_p(adj):>9}{sig:>5}")
    for run in multi.failed:
        L(f"    {_elide(run.name, name_w):<{name_w}}{'— 분석 불가':<16}"
          f"{'':>14}{'':>9}{'':>9}{'':>5}")
    L("")
    L(f"    * = 보정 후 p < {multi.alpha} (엔드포인트 간 {corr})")
    if multi.correction != "none" and len(ok) > 1:
        L("    p(adj)는 엔드포인트 패밀리에 대한 보정이며, 각 엔드포인트 안의 "
          "사후검정 보정과는 별개입니다.")

    if multi.failed:
        L("")
        L("[!] 분석하지 못한 엔드포인트 / Endpoints that could not be analysed")
        for run in multi.failed:
            L(f"    - {run.name}: {run.error}")
    demoted = [r.name for r in ok if _demoted(r.result)]
    if demoted:
        L("")
        L("[!] 보정 후 유의성이 철회된 엔드포인트 / Withdrawn by the correction")
        for name in demoted:
            res = next(r.result for r in ok if r.name == name)
            L(f"    - {name}: 보정 전 p={_fmt_p(res.pvalue)} → 보정 후 "
              f"p(adj)={_fmt_p(res.pvalue_adj)} (α={multi.alpha})")
    if multi.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in multi.warnings:
            L(f"    - {w}")

    if detail:
        for run in ok:
            L("")
            L("#" * 78)
            L(f"### 엔드포인트 / Endpoint: {run.name}")
            L("#" * 78)
            body = (render_binary_text(run.result) if multi.binary
                    else render_text(run.result))
            lines.extend(body.splitlines())
    L("")
    return "\n".join(lines)


def multi_to_dict(multi) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema": "statwise/multi/1",
        "alpha": multi.alpha,
        "endpoint_correction": multi.correction,
        "binary": bool(multi.binary),
        "warnings": list(multi.warnings),
        "endpoints": [],
        "failed": [{"endpoint": r.name, "error": r.error}
                   for r in multi.failed],
    }
    for run in multi.analysed:
        body = (binary_to_dict(run.result) if multi.binary
                else result_to_dict(run.result))
        body["endpoint"] = run.name
        body["pvalue_adj"] = _jnum(run.result.pvalue_adj)
        out["endpoints"].append(body)
    return out


def render_multi_json(multi, indent: int = 2) -> str:
    return json.dumps(multi_to_dict(multi), indent=indent, ensure_ascii=False)


# ==========================================================================
# tidy CSV results table (one row per reported comparison)
# ==========================================================================

_CSV_COLUMNS = ["endpoint", "kind", "comparison", "test", "n1", "n2",
                "n_all", "estimate_name", "estimate", "ci_low", "ci_high",
                "ci_conf", "statistic", "df", "pvalue", "pvalue_adj",
                "significant", "verdict"]


def _csv_num(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        if x != x or math.isinf(x):
            return ""
        return repr(x)
    return str(x)


def _analysis_rows(res: AnalysisResult, endpoint: str = "") -> List[List[str]]:
    """The omnibus row plus one row per post-hoc comparison."""
    rows: List[List[str]] = []
    labels = [g.label for g in res.groups]
    comparison = " vs ".join(labels) if len(labels) <= 3 else \
        f"{len(labels)} groups"
    est_name = est = lo = hi = None
    if res.mean_diff is not None:
        est_name, est = "mean difference", res.mean_diff
        if res.mean_diff_ci is not None:
            lo, hi = res.mean_diff_ci
    elif res.location is not None:
        est_name, est = "Hodges-Lehmann", res.location.estimate
        lo, hi = res.location.ci_low, res.location.ci_high
    elif res.effects:
        e = res.effects[0]
        est_name, est, lo, hi = e.name, e.value, e.ci_low, e.ci_high
    ns = [g.n for g in res.groups]
    n_all = "|".join(f"{g.label}={g.n}" for g in res.groups)
    conf = "" if lo is None else repr(1.0 - res.alpha)
    if est_name and len(labels) == 2:
        est_name = f"{est_name} ({labels[0]} − {labels[1]})"
    rows.append([endpoint, "paired" if res.paired else "continuous",
                 comparison, res.test_name,
                 str(ns[0]), str(ns[1]) if len(ns) > 1 else "", n_all,
                 est_name or "", _csv_num(est), _csv_num(lo), _csv_num(hi),
                 conf, _csv_num(res.statistic), _csv_num(res.df),
                 _csv_num(res.pvalue), _csv_num(res.pvalue_adj),
                 "yes" if res.significant else "no",
                 "significant difference" if res.significant
                 else "no significant difference"])
    by_label = {g.label: g.n for g in res.groups}
    for pw in res.pairwise:
        lo_p = "" if pw.diff_ci is None else _csv_num(pw.diff_ci[0])
        hi_p = "" if pw.diff_ci is None else _csv_num(pw.diff_ci[1])
        rows.append([endpoint, "post-hoc", f"{pw.a} vs {pw.b}", pw.test,
                     str(pw.n_a), str(pw.n_b),
                     n_all,
                     f"{pw.diff_label} ({pw.a} − {pw.b}, per-comparison CI)",
                     _csv_num(pw.diff), lo_p, hi_p,
                     "" if pw.diff_ci is None else repr(1.0 - res.alpha),
                     _csv_num(pw.statistic), "", _csv_num(pw.pvalue_raw),
                     _csv_num(pw.pvalue_adj),
                     "yes" if pw.significant else "no",
                     "significant difference" if pw.significant
                     else "no significant difference"])
        rows.append([endpoint, "post-hoc-effect", f"{pw.a} vs {pw.b}", pw.test,
                     str(pw.n_a), str(pw.n_b), n_all, pw.effect_name,
                     _csv_num(pw.effect_value), "", "", "", "", "", "", "",
                     "", ""])
    if res.equivalence is not None:
        eq = res.equivalence
        # `significant` on this row means "margin claim concluded", not
        # "difference detected"; ci_conf carries its own (1-2a) coverage so it
        # is never confused with the omnibus interval above it.
        rows.append([endpoint, eq.kind,
                     " vs ".join(labels[:2]), res.test_name + " + margin",
                     str(ns[0]), str(ns[1]) if len(ns) > 1 else "", n_all,
                     "mean difference", _csv_num(eq.diff), _csv_num(eq.ci_low),
                     _csv_num(eq.ci_high), repr(eq.conf), "", _csv_num(eq.df),
                     _csv_num(eq.pvalue), "",
                     "yes" if eq.concluded else "no",
                     ("equivalence concluded" if eq.kind == "tost"
                      else "non-inferiority concluded") if eq.concluded
                     else ("equivalence not concluded" if eq.kind == "tost"
                           else "non-inferiority not concluded")])
    return rows


def _binary_rows(res: BinaryResult, endpoint: str = "") -> List[List[str]]:
    rows: List[List[str]] = []
    labels = [g.label for g in res.groups]
    comparison = " vs ".join(labels) if len(labels) <= 3 else \
        f"{len(labels)} groups"
    ns = [g.n for g in res.groups]
    n_all = "|".join(f"{g.label}={g.events}/{g.n}" for g in res.groups)
    est = res.estimates[0] if res.estimates else None
    rows.append([endpoint, "binary", comparison, res.test_name,
                 str(ns[0]), str(ns[1]) if len(ns) > 1 else "", n_all,
                 est.name if est else "", _csv_num(est.value if est else None),
                 _csv_num(est.ci_low if est else None),
                 _csv_num(est.ci_high if est else None),
                 repr(est.conf) if est and est.ci_low is not None else "",
                 _csv_num(res.statistic), _csv_num(res.df),
                 _csv_num(res.pvalue), _csv_num(res.pvalue_adj),
                 "yes" if res.significant else "no",
                 "significant difference" if res.significant
                 else "no significant difference"])
    for extra in res.estimates[1:]:
        rows.append([endpoint, "binary-effect", comparison, res.test_name,
                     str(ns[0]), str(ns[1]) if len(ns) > 1 else "", n_all,
                     extra.name, _csv_num(extra.value), _csv_num(extra.ci_low),
                     _csv_num(extra.ci_high),
                     repr(extra.conf) if extra.ci_low is not None else "",
                     "", "", "", "", "", ""])
    by_label = {g.label: g.n for g in res.groups}
    for pw in res.pairwise:
        rows.append([endpoint, "post-hoc", f"{pw.a} vs {pw.b}", pw.test,
                     str(pw.n_a or by_label.get(pw.a, "")),
                     str(pw.n_b or by_label.get(pw.b, "")),
                     n_all, "risk difference (first minus second)",
                     _csv_num(pw.risk_diff), _csv_num(pw.rd_ci_low),
                     _csv_num(pw.rd_ci_high),
                     "" if pw.rd_ci_low is None else repr(1.0 - res.alpha),
                     "", "", _csv_num(pw.pvalue_raw),
                     _csv_num(pw.pvalue_adj),
                     "yes" if pw.significant else "no",
                     "significant difference" if pw.significant
                     else "no significant difference"])
    return rows


def _rows_to_csv(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    writer.writerows(rows)
    return buf.getvalue()


def render_csv(res) -> str:
    """A tidy one-row-per-comparison CSV of the results (for Excel / R)."""
    if isinstance(res, BinaryResult):
        return _rows_to_csv(_binary_rows(res))
    return _rows_to_csv(_analysis_rows(res))


def render_multi_csv(multi) -> str:
    rows: List[List[str]] = []
    for run in multi.analysed:
        rows.extend(_binary_rows(run.result, run.name) if multi.binary
                    else _analysis_rows(run.result, run.name))
    return _rows_to_csv(rows)
