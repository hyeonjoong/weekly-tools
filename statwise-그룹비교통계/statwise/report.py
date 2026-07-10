"""Human-readable report rendering (Korean + English) for an AnalysisResult."""

from __future__ import annotations

import math
from typing import List

from .analyze import AnalysisResult

__all__ = ["render_text"]


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

    # Chosen test
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
    L(f"      유의수준 α={res.alpha}: {sig} (p{'<' if res.pvalue < res.alpha else '≥'}{res.alpha})")

    if res.mean_diff is not None and res.mean_diff_ci is not None:
        lo, hi = res.mean_diff_ci
        L(f"      평균차 mean difference = {_num(res.mean_diff)} "
          f"[{int((1-res.alpha)*100)}% CI {_num(lo)}, {_num(hi)}]")

    # Effect sizes
    L("")
    L("[4] 효과크기 / Effect size")
    for es in res.effects:
        ci = ""
        if es.ci_low is not None and es.ci_high is not None:
            ci = f"  [95% CI {_num(es.ci_low)}, {_num(es.ci_high)}]"
        mag = f"  ({es.magnitude})" if es.magnitude else ""
        L(f"    {es.name} = {_num(es.value)}{ci}{mag}")

    # Post-hoc
    if res.pairwise:
        L("")
        L("[5] 사후검정 / Post-hoc (Holm-Bonferroni 보정)")
        L(f"    {'comparison':<28}{'test':<16}{'p(raw)':>9}{'p(adj)':>9}"
          f"{'effect':>10}{'sig':>5}")
        for pw in res.pairwise:
            comp = f"{pw.a} vs {pw.b}"
            star = "*" if pw.significant else ""
            L(f"    {comp:<28}{pw.test:<16}{_fmt_p(pw.pvalue_raw):>9}"
              f"{_fmt_p(pw.pvalue_adj):>9}{_num(pw.effect_value):>10}{star:>5}")

    # Warnings
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")

    # Publication sentence
    L("")
    L("[논문용 문장 / Ready-to-paste sentence]")
    L("  " + _sentence(res))
    L("")
    return "\n".join(lines)


def _stat_symbol(test_name: str) -> str:
    if test_name.startswith("Student") or test_name.startswith("Welch"):
        return "t"
    if "ANOVA" in test_name:
        return "F"
    if "Mann-Whitney" in test_name:
        return "U"
    if "Kruskal" in test_name:
        return "H"
    return "stat"


def _sentence(res: AnalysisResult) -> str:
    psign = "p < 0.001" if res.pvalue < 0.001 else f"p = {res.pvalue:.3f}"
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
            f"{es.name} = {_num(es.value,2)}).")
    # 3+ groups
    es = res.effects[0]
    if "ANOVA" in res.test_name:
        head = (f"A one-way ANOVA showed a "
                f"{'significant' if res.significant else 'non-significant'} effect of group "
                f"(F({_fmt_df(res.df)}, {_fmt_df(res.df2)}) = {_num(res.statistic,2)}, "
                f"{psign}, {es.name} = {_num(es.value,3)}).")
    else:
        head = (f"A Kruskal-Wallis test showed a "
                f"{'significant' if res.significant else 'non-significant'} difference across groups "
                f"(H({_fmt_df(res.df)}) = {_num(res.statistic,2)}, {psign}, "
                f"{es.name} = {_num(es.value,3)}).")
    if res.pairwise:
        sigs = [f"{pw.a}–{pw.b}" for pw in res.pairwise if pw.significant]
        if sigs:
            head += (" Holm-corrected post-hoc comparisons were significant for: "
                     + ", ".join(sigs) + ".")
        else:
            head += " No pairwise comparison survived Holm correction."
    return head
