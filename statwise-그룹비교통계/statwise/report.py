"""Human-readable report rendering (Korean + English) for an AnalysisResult."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List

from .analyze import AnalysisResult

__all__ = ["render_text", "render_json", "result_to_dict"]


def _fmt_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _num(x: float, d: int = 3) -> str:
    if x != x:  # NaN
        return "NaN"
    return f"{x:.{d}f}"


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

    # Descriptives
    L("")
    L("[1] 기술통계 / Descriptives")
    L(f"    {'group':<16}{'n':>5}{'mean':>10}{'sd':>10}{'median':>10}"
      f"{'Q1':>9}{'Q3':>9}")
    for g in res.groups:
        q1, q3 = g.quartiles()
        L(f"    {g.label:<16}{g.n:>5}{_num(g.mean):>10}{_num(g.sd):>10}"
          f"{_num(g.median):>10}{_num(q1):>9}{_num(q3):>9}")

    # Assumptions
    L("")
    L("[2] 가정 점검 / Assumption checks")
    if res.paired:
        dnc = res.diff_normality
        if dnc is None or dnc.pvalue is None:
            note = dnc.note if dnc else ""
            L(f"    정규성(차이값) Shapiro-Wilk: (건너뜀) {note}")
        else:
            verdict = "정규분포로 볼 수 있음" if dnc.normal else "정규성 위배 의심"
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
        _render_effects(res, L)
        _render_warnings(res, L)
        L("")
        L("[논문용 문장 / Ready-to-paste sentence]")
        L("  " + _sentence(res))
        L("")
        return "\n".join(lines)
    for nc in res.normality:
        if nc.pvalue is None:
            L(f"    정규성 Shapiro-Wilk [{nc.label}]: (건너뜀) {nc.note}")
        else:
            verdict = "정규분포로 볼 수 있음" if nc.normal else "정규성 위배 의심"
            L(f"    정규성 Shapiro-Wilk [{nc.label}]: W={_num(nc.w)}, "
              f"p={_fmt_p(nc.pvalue)}  → {verdict}")
    if res.levene is not None:
        lp = res.levene.pvalue
        if lp != lp or not math.isfinite(res.levene.statistic):  # NaN / inf
            L(f"    등분산 Levene(median): 판정 불가 "
              f"(분산이 0인 그룹 — 등분산 여부를 정의할 수 없음)")
        else:
            eq = "등분산 가정 충족" if lp > res.alpha_norm else "등분산 위배 의심"
            L(f"    등분산 Levene(median): W={_num(res.levene.statistic)}, "
              f"p={_fmt_p(lp)}  → {eq}")

    _render_selected(res, L)
    _render_effects(res, L)

    # Post-hoc
    if res.pairwise:
        _render_posthoc(res, L)

    _render_warnings(res, L)

    # Publication sentence
    L("")
    L("[논문용 문장 / Ready-to-paste sentence]")
    L("  " + _sentence(res))
    L("")
    return "\n".join(lines)


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
              f"(CI 생략: {loc.method})")


def _render_effects(res: AnalysisResult, L) -> None:
    L("")
    L("[4] 효과크기 / Effect size")
    for es in res.effects:
        ci = ""
        if es.ci_low is not None and es.ci_high is not None:
            ci = f"  [95% CI {_num(es.ci_low)}, {_num(es.ci_high)}]"
        mag = f"  ({es.magnitude})" if es.magnitude else ""
        L(f"    {es.name} = {_num(es.value)}{ci}{mag}")


def _render_posthoc(res: AnalysisResult, L) -> None:
    label = "Benjamini-Hochberg FDR" if res.correction == "bh" else "Holm-Bonferroni"
    L("")
    L(f"[5] 사후검정 / Post-hoc ({label} 보정)")
    L(f"    {'comparison':<28}{'test':<16}{'p(raw)':>9}{'p(adj)':>9}"
      f"{'effect':>10}{'sig':>5}")
    for pw in res.pairwise:
        comp = f"{pw.a} vs {pw.b}"
        star = "*" if pw.significant else ""
        L(f"    {comp:<28}{pw.test:<16}{_fmt_p(pw.pvalue_raw):>9}"
          f"{_fmt_p(pw.pvalue_adj):>9}{_num(pw.effect_value):>10}{star:>5}")


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
        groups.append({
            "label": g.label, "n": g.n,
            "mean": _jnum(g.mean), "sd": _jnum(g.sd),
            "median": _jnum(g.median), "q1": _jnum(q1), "q3": _jnum(q3),
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
             "magnitude": e.magnitude}
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
    if res.pairwise:
        out["correction"] = res.correction
        out["pairwise"] = [
            {"a": pw.a, "b": pw.b, "test": pw.test,
             "statistic": _jnum(pw.statistic),
             "pvalue_raw": _jnum(pw.pvalue_raw),
             "pvalue_adj": _jnum(pw.pvalue_adj),
             "effect_name": pw.effect_name, "effect_value": _jnum(pw.effect_value),
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
    return (f", median difference = {_num(loc.estimate,2)}, "
            f"{int(loc.conf*100)}% CI [{_num(loc.ci_low,2)}, {_num(loc.ci_high,2)}]")


def _sentence(res: AnalysisResult) -> str:
    psign = "p < 0.001" if res.pvalue < 0.001 else f"p = {res.pvalue:.3f}"
    if res.paired:
        a, b = res.groups
        es = res.effects[0]
        sig = ("statistically significant" if res.significant
               else "not statistically significant")
        if res.test_name.startswith("Paired"):
            return (
                f"{a.label} (M = {_num(a.mean,2)}, SD = {_num(a.sd,2)}) and "
                f"{b.label} (M = {_num(b.mean,2)}, SD = {_num(b.sd,2)}) were "
                f"compared using a paired-samples t-test; the difference was "
                f"{sig} (t({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,2)}).")
        return (
            f"{a.label} (Mdn = {_num(a.median,2)}) and {b.label} "
            f"(Mdn = {_num(b.median,2)}) were compared using a Wilcoxon "
            f"signed-rank test; the difference was {sig} "
            f"(W = {_num(res.statistic,1)}, {psign}, "
            f"{es.name} = {_num(es.value,2)}){_hl_phrase(res)}.")
    if len(res.groups) == 2:
        a, b = res.groups
        es = res.effects[0]
        if res.test_name.startswith(("Student", "Welch")):
            test_phrase = ("an independent-samples" if res.test_name.startswith("Student")
                           else "a Welch's")
            sig_phrase = ("statistically significant" if res.significant
                          else "not statistically significant")
            return (
                f"{a.label} (M = {_num(a.mean,2)}, SD = {_num(a.sd,2)}) and "
                f"{b.label} (M = {_num(b.mean,2)}, SD = {_num(b.sd,2)}) were compared "
                f"using {test_phrase} t-test; the difference was {sig_phrase} "
                f"(t({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,2)}).")
        return (
            f"{a.label} (Mdn = {_num(a.median,2)}) and {b.label} "
            f"(Mdn = {_num(b.median,2)}) were compared using a Mann-Whitney U test; "
            f"the difference was "
            f"{'statistically significant' if res.significant else 'not statistically significant'} "
            f"(U = {_num(res.statistic,1)}, {psign}, "
            f"{es.name} = {_num(es.value,2)}){_hl_phrase(res)}.")
    # 3+ groups
    es = res.effects[0]
    if "ANOVA" in res.test_name:
        anova_name = "Welch's ANOVA" if res.test_name.startswith("Welch") else "one-way ANOVA"
        head = (f"A {anova_name} showed a "
                f"{'significant' if res.significant else 'non-significant'} effect of group "
                f"(F({_fmt_df(res.df)}, {_fmt_df(res.df2)}) = {_num(res.statistic,2)}, "
                f"{psign}, {es.name} = {_num(es.value,3)}).")
    else:
        head = (f"A Kruskal-Wallis test showed a "
                f"{'significant' if res.significant else 'non-significant'} difference across groups "
                f"(H({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,3)}).")
    if res.pairwise:
        corr = ("Benjamini-Hochberg (FDR)" if res.correction == "bh"
                else "Holm-corrected")
        sigs = [f"{pw.a}–{pw.b}" for pw in res.pairwise if pw.significant]
        if sigs:
            head += (f" {corr} post-hoc comparisons were significant for: "
                     + ", ".join(sigs) + ".")
        else:
            head += f" No pairwise comparison survived {corr} correction."
    return head
