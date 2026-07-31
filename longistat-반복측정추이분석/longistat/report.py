"""Rendering: Korean text report, APA sentences, Markdown, JSON and CSV.

Text tables are padded by *display* width (Korean glyphs are double-width) so
they stay aligned in a terminal.  For pasting into a manuscript use
``--format md``, which emits real Markdown tables that Word and every journal
submission system accept.
"""

from __future__ import annotations

import csv
import io
import json
import math
import unicodedata
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Sequence

from .analyze import Analysis
from .describe import ALL_LABEL
from .sensitivity import KIND_EN, KIND_LABEL
from .trend import reported_p, trend_shape

__all__ = ["render_text", "render_json", "render_csv", "render_markdown",
           "apa_sentences", "fmt_p", "fmt_p_cell", "fmt_es", "fmt_df", "fmt",
           "table"]

_CORRECTION_NAME = {"none": "보정 없음", "gg": "Greenhouse–Geisser",
                    "hf": "Huynh–Feldt"}
_CORRECTION_EN = {"none": "uncorrected", "gg": "Greenhouse–Geisser corrected",
                  "hf": "Huynh–Feldt corrected"}
_TRACK_NAME = {"parametric": "모수(ANOVA / t-검정)",
               "nonparametric": "비모수(Friedman / 순위검정)"}


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def fmt(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, str):
        return x
    if not math.isfinite(x):
        return "—" if math.isnan(x) else ("∞" if x > 0 else "−∞")
    return f"{x:.{digits}f}"


def fmt_p(p: Optional[float]) -> str:
    """APA-style p-value for prose: ``= .023`` / ``< .001`` (no leading zero)."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    if p < 0.001:
        return "< .001"
    if p > 0.999:
        return "> .999"
    return "= " + f"{p:.3f}"[1:]


def fmt_p_cell(p: Optional[float]) -> str:
    """Same value without the relational operator, for a column headed ``p``."""
    s = fmt_p(p)
    return s[2:] if s.startswith("= ") else s.replace(" ", "")


def fmt_es(x: Optional[float], digits: int = 3) -> str:
    """Effect size in APA style — leading zero dropped for |x| < 1."""
    if x is None or not isinstance(x, float) or not math.isfinite(x):
        return "—"
    s = f"{x:.{digits}f}"
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s


def fmt_df(x: Optional[float]) -> str:
    """Degrees of freedom: integral values print without decimals."""
    if x is None or not math.isfinite(x):
        return "—"
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:.2f}"


def fmt_ci(lo: float, hi: float, digits: int = 2) -> str:
    if lo is None or hi is None or any(math.isnan(v) for v in (lo, hi)):
        return "—"
    return f"[{lo:.{digits}f}, {hi:.{digits}f}]"


def fmt_es_ci(value: float, ci: Sequence[float]) -> str:
    """Effect size with its interval, e.g. ``-1.74 [-2.41, -1.07]``."""
    base = fmt(value, 2)
    if ci is None or len(ci) != 2 or any(
            v is None or math.isnan(v) for v in ci):
        return base
    return f"{base} [{ci[0]:.2f}, {ci[1]:.2f}]"


def fmt_pct(x: float) -> str:
    return "—" if x is None or math.isnan(x) else f"{x:.1%}"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1
               for ch in text)


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - _width(text))
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          aligns: Optional[Sequence[str]] = None) -> List[str]:
    """Render an aligned plain-text table."""
    if not rows:
        return []
    cols = len(headers)
    aligns = list(aligns or ["left"] + ["right"] * (cols - 1))
    widths = [_width(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], _width(str(row[i])))
    out = ["  ".join(_pad(h, widths[i], aligns[i]) for i, h in enumerate(headers))]
    out.append("  ".join("─" * widths[i] for i in range(cols)))
    for row in rows:
        out.append("  ".join(_pad(str(row[i]), widths[i], aligns[i])
                             for i in range(cols)))
    return out


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    """Render a GitHub/Word-friendly Markdown table."""
    if not rows:
        return []

    def esc(v: Any) -> str:
        return str(v).replace("|", "\\|")

    out = ["| " + " | ".join(esc(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in row) + " |" for row in rows]
    return out


def _stars(p: float, alpha: float) -> str:
    if p is None or math.isnan(p):
        return ""
    if p < 0.001:
        return " ***"
    if p < 0.01:
        return " **"
    if p < alpha:
        return " *"
    return ""


def _en(a: Analysis, label: str) -> str:
    """English rendering of a timepoint/group label, if the user supplied one."""
    return a.options.labels_en.get(label, label)


# --------------------------------------------------------------------------
# shared table builders (text and Markdown use the same rows)
# --------------------------------------------------------------------------

def _rows_descriptives(a: Analysis) -> List[List[str]]:
    return [[c.group, c.time, str(c.n), fmt(c.mean), fmt(c.sd),
             fmt_ci(c.ci_low, c.ci_high),
             f"{fmt(c.median)} [{fmt(c.q1)}, {fmt(c.q3)}]"]
            for c in a.descriptives
            if a.grouped or c.group == ALL_LABEL]


def _rows_availability(a: Analysis) -> List[List[str]]:
    m = a.missing
    scopes = [ALL_LABEL]
    if a.grouped:
        scopes += [g for g in m.per_time_by_group if g != ALL_LABEL]
    rows = []
    for scope in scopes:
        obs = m.per_time_by_group.get(scope, {})
        complete, total = m.per_group_complete.get(scope, (0, 0))
        rows.append([scope, str(total)]
                    + [str(obs.get(t, 0)) for t in a.panel.times]
                    + [f"{complete} ({complete / total:.0%})" if total else "—"])
    return rows


def _rows_anova(a: Analysis) -> List[List[str]]:
    if a.anova is None:
        return []
    rows = []
    for eff in a.anova.effects:
        pv = eff.p_reported(a.correction_used)
        d1, d2 = eff.df_reported(a.correction_used)
        rows.append([eff.name, fmt(eff.ss), f"{fmt_df(d1)}, {fmt_df(d2)}",
                     fmt(eff.f), fmt_p_cell(pv) + _stars(pv, a.options.alpha),
                     fmt_es(eff.partial_eta2), fmt_es(eff.generalized_eta2)])
    return rows


def _rows_change_within(a: Analysis) -> List[List[str]]:
    change = a.change_param if a.recommended == "parametric" else a.change_rank
    return [[r.group, r.time, str(r.n), fmt(r.mean_change), fmt(r.sd_change),
             fmt_ci(r.ci_low, r.ci_high),
             fmt_p_cell(r.p_adj) + _stars(r.p_adj, a.options.alpha),
             fmt_es_ci(r.effect, r.effect_ci)]
            for r in change.within if a.grouped or r.group == ALL_LABEL]


def _rows_change_between(a: Analysis) -> List[List[str]]:
    change = a.change_param if a.recommended == "parametric" else a.change_rank
    return [[c.time + (" (주요)" if c.primary else ""),
             f"{c.group_a} − {c.group_b}", f"{c.n_a}/{c.n_b}",
             fmt(c.change_a), fmt(c.change_b), fmt(c.diff),
             fmt_ci(c.ci_low, c.ci_high),
             fmt_p_cell(c.p_adj) + _stars(c.p_adj, a.options.alpha),
             fmt_es_ci(c.effect, c.effect_ci)]
            for c in change.between]


def _rows_ancova(a: Analysis) -> List[List[str]]:
    if a.ancova is None:
        return []
    return [[c.time + (" (주요)" if c.primary else ""),
             f"{c.group_a} − {c.group_b}", f"{c.n_a}/{c.n_b}",
             fmt(c.adjusted_diff), fmt_ci(c.ci_low, c.ci_high),
             fmt_p_cell(c.p_adj) + _stars(c.p_adj, a.options.alpha),
             fmt(c.unadjusted_diff), fmt(c.slope)]
            for c in a.ancova.contrasts]


def _rows_pairwise(a: Analysis) -> List[List[str]]:
    pw = a.pairwise_param if a.recommended == "parametric" else a.pairwise_rank
    return [[r.group, f"{r.time_a} → {r.time_b}", str(r.n), fmt(r.mean_diff),
             fmt_ci(r.ci_low, r.ci_high), fmt_p_cell(r.p_raw),
             fmt_p_cell(r.p_adj) + _stars(r.p_adj, a.options.alpha),
             fmt_es_ci(r.effect, r.effect_ci)]
            for r in pw if a.grouped or r.group == ALL_LABEL]


def _rows_between(a: Analysis) -> List[List[str]]:
    return [[r.time + (" (기저·참고)" if r.reference_only else ""),
             f"{r.group_a} − {r.group_b}", f"{r.n_a}/{r.n_b}", fmt(r.diff),
             fmt_ci(r.ci_low, r.ci_high),
             ("검정 안 함" if r.reference_only
              else fmt_p_cell(r.p_adj) + _stars(r.p_adj, a.options.alpha)),
             fmt_es_ci(r.effect, r.effect_ci)]
            for r in a.between]


def _rows_trend(a: Analysis) -> List[List[str]]:
    if a.trend is None:
        return []
    return [[t.order_name, t.scope, fmt(t.ss), f"{fmt_df(t.df1)}, {fmt_df(t.df2)}",
             fmt(t.f), fmt_p_cell(t.p_raw), fmt_p_cell(t.p_adj)
             + _stars(t.p_adj, a.options.alpha), fmt_es(t.partial_eta2)]
            for t in a.trend.effects]


def _unit(a: Analysis) -> str:
    """Slope unit suffix, e.g. '/주' — blank when the user did not name one."""
    if a.trend is None or not a.trend.time_unit:
        return ""
    return "/" + a.trend.time_unit


def _rows_slopes(a: Analysis) -> List[List[str]]:
    if a.trend is None:
        return []
    return [[s.group, str(s.n), fmt(s.mean_slope, 3), fmt(s.sd, 3),
             fmt_ci(s.ci_low, s.ci_high, 3),
             fmt_p_cell(s.p) + _stars(s.p, a.options.alpha)]
            for s in a.trend.slopes]


def _rows_slope_contrast(a: Analysis) -> List[List[str]]:
    if a.trend is None:
        return []
    return [[f"{c.group_a} − {c.group_b}", f"{c.n_a}/{c.n_b}",
             fmt(c.slope_a, 3), fmt(c.slope_b, 3), fmt(c.diff, 3),
             fmt_ci(c.ci_low, c.ci_high, 3),
             fmt_p_cell(c.p) + _stars(c.p, a.options.alpha),
             fmt_es_ci(c.effect, c.effect_ci)]
            for c in a.trend.slope_contrasts]


def _rows_sensitivity(a: Analysis) -> List[List[str]]:
    if a.sensitivity is None:
        return []
    # Group the three variants of each visit together, but keep the visits in
    # protocol order — sorting on the label alone would put 12주 before 4주.
    order = {t: j for j, t in enumerate(a.panel.times)}
    rows = sorted(a.sensitivity.rows,
                  key=lambda r: (order.get(r.time, len(order)), r.contrast))
    return [[r.time, r.contrast, KIND_LABEL[r.kind], str(r.n),
             str(r.imputed) if r.kind != "observed" else "—",
             fmt(r.estimate), fmt_ci(r.ci_low, r.ci_high),
             fmt_p_cell(r.p) + _stars(r.p, a.options.alpha)]
            for r in rows]


def _rows_responder(a: Analysis) -> List[List[str]]:
    if a.responder is None:
        return []
    return [[x.group, x.time, f"{x.responders}/{x.n}", fmt_pct(x.rate),
             f"[{x.ci_low:.1%}, {x.ci_high:.1%}]"]
            for x in a.responder.rates if a.grouped or x.group == ALL_LABEL]


# --------------------------------------------------------------------------
# APA sentences
# --------------------------------------------------------------------------

def apa_sentences(a: Analysis) -> List[str]:
    """Ready-to-paste result sentences, Korean and English for every claim."""
    out: List[str] = []
    corr = a.correction_used
    alpha = a.options.alpha

    if a.recommended == "parametric" and a.anova is not None:
        for eff in a.anova.effects:
            p = eff.p_reported(corr)
            df1, df2 = eff.df_reported(corr)
            tag = (f" ({_CORRECTION_NAME[corr]} 보정)"
                   if eff.within and corr != "none" else "")
            tag_en = (f", {_CORRECTION_EN[corr]}"
                      if eff.within and corr != "none" else "")
            if math.isnan(p):
                out.append(f"[KO] {eff.name} 효과는 검정할 수 없었습니다 "
                           "(잔차 분산이 0).")
                out.append(f"[EN] The effect of {_en_effect(eff.name)} could not "
                           "be tested (zero residual variance).")
                continue
            verdict = "유의하였다" if p < alpha else "유의하지 않았다"
            verdict_en = "a significant" if p < alpha else "no significant"
            out.append(
                f"[KO] {eff.name} 효과는 {verdict}, F({fmt_df(df1)}, "
                f"{fmt_df(df2)}) = {fmt(eff.f)}, p {fmt_p(p)}, "
                f"ηp² = {fmt_es(eff.partial_eta2)}{tag}.")
            out.append(
                f"[EN] There was {verdict_en} effect of {_en_effect(eff.name)}, "
                f"F({fmt_df(df1)}, {fmt_df(df2)}) = {fmt(eff.f)}, p {fmt_p(p)}, "
                f"ηp² = {fmt_es(eff.partial_eta2)}{tag_en}.")
    else:
        for fr in a.friedman:
            if fr.group != ALL_LABEL and not a.grouped:
                continue
            scope = "" if fr.group == ALL_LABEL else f"{fr.group}군에서 "
            scope_en = ("" if fr.group == ALL_LABEL
                        else f"In the {_en(a, fr.group)} arm, ")
            out.append(
                f"[KO] {scope}시점에 따른 변화는 Friedman 검정에서 "
                f"χ²({fr.df}) = {fmt(fr.chi2)}, p {fmt_p(fr.p)}, "
                f"Kendall W = {fmt_es(fr.kendall_w)} 이었다 (n = {fr.n}).")
            out.append(
                f"[EN] {scope_en}a Friedman test gave χ²({fr.df}) = "
                f"{fmt(fr.chi2)}, p {fmt_p(fr.p)}, Kendall's W = "
                f"{fmt_es(fr.kendall_w)} (n = {fr.n}).")

    if a.trend is not None and a.recommended == "parametric":
        for eff in a.trend.effects:
            if not math.isfinite(eff.f) or eff.residual:
                continue
            scope_ko = "시점 추세" if eff.scope == "시점" else "그룹 × 시점 추세"
            scope_en = ("the " if eff.scope == "시점"
                        else "the group × time interaction for the ")
            # Quote the same p the table prints (adjusted within its family) —
            # quoting p_raw here contradicted the star column two lines above.
            pv = reported_p(eff)
            out.append(
                f"[KO] {scope_ko}의 {eff.order_name} 성분은 "
                f"F({fmt_df(eff.df1)}, {fmt_df(eff.df2)}) = {fmt(eff.f)}, "
                f"보정 p {fmt_p(pv)}, ηp² = {fmt_es(eff.partial_eta2)} "
                "이었다 (직교 다항 대비 — 시점 점수 하나에 대한 검정이라 "
                "구형성 보정 없음).")
            out.append(
                f"[EN] For {scope_en}{_ORDER_EN_SENT.get(eff.order, '')} trend, "
                f"F({fmt_df(eff.df1)}, {fmt_df(eff.df2)}) = {fmt(eff.f)}, "
                f"adjusted p {fmt_p(pv)}, ηp² = {fmt_es(eff.partial_eta2)} "
                "(orthogonal polynomial contrast on a single within-subject "
                "score, so no sphericity correction applies).")
        for con in a.trend.slope_contrasts:
            unit = a.trend.time_unit or "시점 1단위"
            unit_en = a.trend.time_unit or "unit of time"
            out.append(
                f"[KO] 개인별 회귀 기울기의 군간 차이는 {fmt(con.diff, 3)} "
                f"({a.panel.value_name}/{unit}, 95% CI "
                f"{fmt_ci(con.ci_low, con.ci_high, 3)}), p {fmt_p(con.p)}.")
            out.append(
                f"[EN] The difference between arms in individual regression "
                f"slopes was {fmt(con.diff, 3)} units per {unit_en} "
                f"(95% CI {fmt_ci(con.ci_low, con.ci_high, 3)}), "
                f"p {fmt_p(con.p)}.")

    change = a.change_param if a.recommended == "parametric" else a.change_rank
    for row in change.within:
        if row.group != ALL_LABEL or not a.grouped:
            scope = "" if row.group == ALL_LABEL else f"{row.group} — "
            scope_en = ("" if row.group == ALL_LABEL
                        else f"In the {_en(a, row.group)} arm, ")
            out.append(
                f"[KO] {scope}{change.baseline} 대비 {row.time} 변화량은 "
                f"{fmt(row.mean_change)} (95% CI "
                f"{fmt_ci(row.ci_low, row.ci_high)}), p {fmt_p(row.p_adj)}.")
            out.append(
                f"[EN] {scope_en}the change from {_en(a, change.baseline)} to "
                f"{_en(a, row.time)} was {fmt(row.mean_change)} (95% CI "
                f"{fmt_ci(row.ci_low, row.ci_high)}), p {fmt_p(row.p_adj)}.")
    for con in change.between:
        mark = " (사전 지정 주요 시점, 보정 없음)" if con.primary else ""
        mark_en = (" (pre-specified primary endpoint, unadjusted)"
                   if con.primary else "")
        out.append(
            f"[KO] {con.time} 시점에서 {con.group_a}군과 {con.group_b}군의 "
            f"변화량 차이는 {fmt(con.diff)} "
            f"(95% CI {fmt_ci(con.ci_low, con.ci_high)}), "
            f"p {fmt_p(con.p_adj)}{mark}.")
        out.append(
            f"[EN] At {_en(a, con.time)}, the difference in change from "
            f"{_en(a, change.baseline)} between {_en(a, con.group_a)} and "
            f"{_en(a, con.group_b)} was {fmt(con.diff)} "
            f"(95% CI {fmt_ci(con.ci_low, con.ci_high)}), "
            f"p {fmt_p(con.p_adj)}{mark_en}.")

    if a.ancova is not None:
        for con in a.ancova.contrasts:
            out.append(
                f"[KO] 기저값을 공변량으로 보정했을 때 {con.time} 시점의 "
                f"{con.group_a} − {con.group_b} 조정평균차는 "
                f"{fmt(con.adjusted_diff)} (95% CI "
                f"{fmt_ci(con.ci_low, con.ci_high)}), p {fmt_p(con.p_adj)}.")
            out.append(
                f"[EN] Adjusting for baseline (ANCOVA), the adjusted mean "
                f"difference at {_en(a, con.time)} between "
                f"{_en(a, con.group_a)} and {_en(a, con.group_b)} was "
                f"{fmt(con.adjusted_diff)} (95% CI "
                f"{fmt_ci(con.ci_low, con.ci_high)}), p {fmt_p(con.p_adj)}.")

    if a.responder is not None:
        basis = "무응답 대체(NRI)" if a.responder.nri else "관측 완료자"
        basis_en = ("non-responder imputation" if a.responder.nri
                    else "observed cases")
        for con in a.responder.contrasts:
            out.append(
                f"[KO] {con.time} 시점 반응자 비율은 {con.group_a} "
                f"{con.rate_a:.1%} vs {con.group_b} {con.rate_b:.1%} "
                f"(위험차 {con.risk_difference:+.1%}, 95% CI "
                f"[{con.rd_ci[0]:.1%}, {con.rd_ci[1]:.1%}], {con.method} "
                f"p {fmt_p(con.p_adj)}; 분모는 {basis}).")
            out.append(
                f"[EN] At {_en(a, con.time)}, {con.rate_a:.1%} of "
                f"{_en(a, con.group_a)} versus {con.rate_b:.1%} of "
                f"{_en(a, con.group_b)} met the responder criterion "
                f"(risk difference {con.risk_difference:+.1%}, 95% CI "
                f"[{con.rd_ci[0]:.1%}, {con.rd_ci[1]:.1%}], p "
                f"{fmt_p(con.p_adj)}; {basis_en}).")

    if a.sensitivity is not None:
        issues = a.sensitivity.flips(alpha)
        agree = not issues
        kinds = " · ".join(KIND_LABEL[k] for k in a.sensitivity.kinds)
        kinds_en = " and ".join(KIND_EN[k] for k in a.sensitivity.kinds)
        if not agree and all("비교" in i for i in issues):
            # Every difference was "no observed-case counterpart".  Claiming
            # either agreement or disagreement would be an invention.
            out.append(
                f"[KO] 일부 시점은 관측값 결과가 없어 {kinds} 대체와 비교할 수 "
                "없었다 — 그 시점의 민감도 판정은 보류한다.")
            out.append(
                "[EN] For some visits there was no observed-case result to "
                f"compare against {kinds_en} imputation, so no sensitivity "
                "verdict is claimed for them.")
        elif agree:
            out.append(
                f"[KO] 결측을 {kinds} 로 대체한 민감도 분석에서도 주요 결과의 "
                "방향과 유의성은 동일했다.")
            out.append(
                f"[EN] Sensitivity analyses using {kinds_en} imputation gave "
                "the same direction and significance as the observed-case "
                "analysis.")
        else:
            out.append(
                f"[KO] 결측을 {kinds} 로 대체하면 주요 결과의 결론이 달라졌다 — "
                "탈락 처리에 민감한 결과이므로 신중히 해석해야 한다.")
            out.append(
                f"[EN] Under {kinds_en} imputation the conclusion changed, so "
                "the result is sensitive to how dropout is handled and should "
                "be interpreted cautiously.")
    return out


_ORDER_EN_SENT = {1: "linear", 2: "quadratic", 3: "cubic"}


def _en_effect(name: str) -> str:
    return {"시점(시간)": "time", "그룹(집단)": "group",
            "그룹 × 시점": "group × time interaction"}.get(name, name)


# --------------------------------------------------------------------------
# text report
# --------------------------------------------------------------------------

_COMPLETER_BANNER = (
    "※ 아래 ANOVA와 변화량은 모든 시점이 관측된 대상만 쓰는 "
    "완전사례(completer) 분석입니다 — ITT 주분석이 아닙니다.")


def render_text(a: Analysis, full: bool = False, brief: bool = False) -> str:
    p = a.panel
    L: List[str] = []
    add = L.append
    alpha = a.options.alpha
    track = _TRACK_NAME[a.recommended]

    add("=" * 72)
    add("longistat — 반복측정 추이 분석 리포트")
    add("=" * 72)
    add(f"대상 수 N = {p.n_subjects}   시점 {p.n_times}개: "
        f"{' → '.join(p.times)}   기준시점: {p.times[a.baseline_index]}")
    if p.groups is not None:
        sizes = {g: p.groups.count(g) for g in p.group_labels()}
        add("그룹: " + ", ".join(f"{g} (n={n})" for g, n in sizes.items()))
    add(f"측정변수: {p.value_name}   유의수준 α = {alpha}")
    if a.options.primary_time:
        add(f"사전 지정 주요 시점: {a.options.primary_time} (다중비교 보정 제외)")
    if p.n_times == 2 and not a.grouped:
        add("· 시점이 2개, 단일 군입니다 — 대응 t/Wilcoxon만 필요하다면 "
            "자매 도구 statwise --paired 가 더 간단합니다.")
    add("")

    # -- [1] missingness --------------------------------------------------
    add("[1] 결측 · 탈락 (CONSORT 흐름에 넣을 숫자)")
    add(f"  완전자료(모든 시점 관측) {a.missing.n_complete}/{a.missing.n_subjects}명 "
        f"({a.missing.complete_fraction:.0%})"
        f" · 결측 패턴 {'단조(탈락형)' if a.missing.monotone else '비단조(중간 누락 포함)'}")
    L.extend("  " + ln for ln in table(
        ["범위", "배정 n"] + list(p.times) + ["완전자료"],
        _rows_availability(a),
        ["left", "right"] + ["right"] * (p.n_times + 1)))
    if a.missing.n_complete < a.missing.n_subjects:
        add("  결측 패턴별 인원 (1=관측, 0=결측, 시점 순):")
        add("    " + ", ".join(f"{k} × {v}" for k, v in a.missing.patterns[:8]))
    add("")

    # -- [2] descriptives -------------------------------------------------
    add("[2] 기술통계 (관측된 값 기준 — 가용사례)")
    L.extend("  " + ln for ln in table(
        ["그룹", "시점", "n", "평균", "SD", "95% CI", "중앙값 [IQR]"],
        _rows_descriptives(a),
        ["left", "left", "right", "right", "right", "right", "right"]))
    add("")

    # -- [3] assumptions --------------------------------------------------
    add("[3] 가정 점검")
    rows = [[r.what, r.label, str(r.n), fmt(r.w, 3), fmt_p_cell(r.p_raw),
             fmt_p_cell(r.p_adj) + _stars(r.p_adj, a.options.alpha_norm)]
            for r in a.normality]
    if rows:
        add("  · 정규성 (Shapiro–Wilk, 군내 중심화 잔차; 보정 p는 Holm)")
        L.extend("    " + ln for ln in table(
            ["대상", "항목", "n", "W", "p", "보정 p"], rows,
            ["left", "left", "right", "right", "right", "right"]))
    if a.anova is not None:
        s = a.anova.sphericity
        if s.mauchly_ok:
            add(f"  · 구형성 (Mauchly): W = {fmt(s.w, 3)}, "
                f"χ²({fmt_df(s.df)}) = {fmt(s.chi2)}, p {fmt_p(s.p)}"
                f"  → {'위배' if s.violated(a.options.alpha_norm) else '위배 근거 없음'}")
        else:
            add(f"  · 구형성: {s.reason}")
        if s.epsilon_ok:
            add(f"    ε: Greenhouse–Geisser {fmt_es(s.eps_gg)}, "
                f"Huynh–Feldt {fmt_es(s.eps_hf)}, 하한 {fmt_es(s.eps_lb)}"
                f"  → 적용: {_CORRECTION_NAME[a.correction_used]}")
            if a.grouped:
                add("    (혼합설계의 Huynh–Feldt ε는 SPSS/SAS 형태입니다 — "
                    "Lecoutre 1991 수정형보다 약간 관대합니다.)")
    add(f"  · 권장 분석: {track} — {a.recommendation_reason}")
    add("")

    # -- [4] omnibus ------------------------------------------------------
    add("[4] 주 분석 (omnibus)")
    add("  " + _COMPLETER_BANNER)
    if a.anova is not None:
        if a.recommended == "parametric" or full:
            add(f"  반복측정/혼합 ANOVA (완전자료 N = {a.anova.n_subjects}"
                f", 시점 내 보정: {_CORRECTION_NAME[a.correction_used]})")
            L.extend("    " + ln for ln in table(
                ["효과", "SS", "df", "F", "p", "ηp²", "η²G"], _rows_anova(a)))
            if a.grouped:
                add("    · 시점 주효과는 Type III(그룹 비가중 평균) 기준입니다.")
                add("    · 그룹 주효과는 기저 시점까지 평균한 값이라 무작위배정 "
                    "시험에서는 해석 가치가 낮습니다 — 상호작용을 보세요.")
        else:
            eff = a.anova.effect("시점(시간)")
            if eff is not None:
                pv = eff.p_reported(a.correction_used)
                add(f"  [교차확인] 반복측정 ANOVA 시점 효과: F = {fmt(eff.f)}, "
                    f"p {fmt_p(pv)}, ηp² = {fmt_es(eff.partial_eta2)}")
        for note in a.anova.notes:
            add(f"    ※ {note}")
    elif a.anova_error:
        add(f"  ANOVA 미수행: {a.anova_error}")

    if a.friedman:
        if a.recommended == "nonparametric" or full:
            rows = [[fr.group, str(fr.n), fmt(fr.chi2), str(fr.df),
                     fmt_p_cell(fr.p) + _stars(fr.p, alpha),
                     fmt_es(fr.kendall_w),
                     ", ".join(fmt(m, 2) for m in fr.mean_ranks)]
                    for fr in a.friedman]
            add("  Friedman 검정 (시점 효과, 순위 기반)")
            L.extend("    " + ln for ln in table(
                ["그룹", "n", "χ²", "df", "p", "Kendall W", "평균순위(시점순)"],
                rows, ["left", "right", "right", "right", "right", "right",
                       "left"]))
        else:
            fr = a.friedman[0]
            add(f"  [교차확인] Friedman χ²({fr.df}) = {fmt(fr.chi2)}, "
                f"p {fmt_p(fr.p)}, W = {fmt_es(fr.kendall_w)}")
    add("")

    # -- [4b] trend over time ---------------------------------------------
    if a.trend is not None:
        tr = a.trend
        spacing = ", ".join(f"{t}={fmt(v, 2).rstrip('0').rstrip('.')}"
                            for t, v in zip(p.times, tr.time_values))
        add(f"[4b] 시점 추세 (직교 다항 대비 · 간격 {tr.time_source}: {spacing})")
        rows = _rows_trend(a)
        if rows:
            add(f"  시점 내 대비 (완전자료 N = {tr.n_complete}; 각 행은 시점 점수 "
                "하나에 대한 검정이라 구형성 보정이 필요 없습니다)")
            L.extend("    " + ln for ln in table(
                ["대비", "효과", "SS", "df", "F", "raw p", "보정 p", "ηp²"], rows,
                ["left", "left", "right", "right", "right", "right", "right",
                 "right"]))
            shape = trend_shape(tr.effects, alpha)
            if shape:
                add(f"    → {shape}")
        if tr.slopes:
            add("")
            add(f"  개인별 회귀 기울기 (대상마다 관측된 시점만으로 적합 · 단위 "
                f"{p.value_name}{_unit(a) or ' / 시점 1단위'}, 가용사례)")
            L.extend("    " + ln for ln in table(
                ["그룹", "n", "평균 기울기", "SD", "95% CI", "p"],
                _rows_slopes(a),
                ["left", "right", "right", "right", "right", "right"]))
        if tr.slope_contrasts:
            add("")
            add("  군간 기울기 차이 (방문을 2회 이상 마친 탈락자도 포함)")
            L.extend("    " + ln for ln in table(
                ["대비", "n", "기울기A", "기울기B", "차이", "95% CI", "p",
                 "Hedges g [95% CI]"], _rows_slope_contrast(a),
                ["left", "right", "right", "right", "right", "right", "right",
                 "right"]))
        for note in tr.notes:
            add(f"  ※ {note}")
        add("")

    # -- [5] change from baseline ----------------------------------------
    change = a.change_param if a.recommended == "parametric" else a.change_rank
    add(f"[5] 기준시점({change.baseline}) 대비 변화량 — {track}")
    L.extend("  " + ln for ln in table(
        ["그룹", "시점", "n", "평균변화", "SD", "95% CI", "보정 p",
         "효과크기 [95% CI]"], _rows_change_within(a),
        ["left", "left", "right", "right", "right", "right", "right", "right"]))
    if change.between:
        add("")
        add("  군간 변화량 차이 (임상시험의 통상적 주요 추정치)")
        L.extend("    " + ln for ln in table(
            ["시점", "대비", "n", "변화A", "변화B", "차이", "95% CI", "보정 p",
             "효과크기 [95% CI]"], _rows_change_between(a),
            ["left", "left", "right", "right", "right", "right", "right",
             "right", "right"]))
    if a.ancova is not None:
        add("")
        add("  기저값 보정 (ANCOVA) — 기저 불균형·평균회귀에 강건하고 대개 검정력이 더 높습니다")
        L.extend("    " + ln for ln in table(
            ["시점", "대비", "n", "조정평균차", "95% CI", "보정 p", "비보정 차이",
             "기저 기울기"], _rows_ancova(a),
            ["left", "left", "right", "right", "right", "right", "right",
             "right"]))
        for note in a.ancova.notes:
            add(f"    ※ {note}")
    if a.sensitivity is not None:
        s = a.sensitivity
        add("")
        names = "·".join(KIND_LABEL[k] for k in s.kinds)
        what = "군간 변화량 차이" if s.grouped else "기저 대비 변화량"
        add(f"  결측 대체 민감도 ({names}) — 결론이 탈락 처리에 흔들리는지만 봅니다")
        add(f"    대상 추정치: 위의 '{what}'(기저값 보정 없음). "
            "ANCOVA 조정평균차는 다시 계산하지 않습니다.")
        L.extend("    " + ln for ln in table(
            ["시점", "대비" if s.grouped else "그룹", "분석", "n", "대체 셀",
             "추정치", "95% CI", "p"], _rows_sensitivity(a),
            ["left", "left", "left", "right", "right", "right", "right",
             "right"]))
        flips = s.flips(alpha)
        if flips:
            for line in flips:
                add(f"    ⚠ {line}")
        else:
            add(f"    → 관측값과 {names}의 결론(유의성·방향)이 일치합니다.")
        if a.recommended != "parametric":
            add("    ※ 이 표의 세 열은 모두 모수 t-검정 기준입니다 — 위 [5]는 "
                "권장 트랙(순위검정) 결과이므로 '관측값' 열과 숫자가 다릅니다.")
        for note in s.notes:
            add(f"    ※ {note}")
    add("")

    if not brief:
        pw_rows = _rows_pairwise(a)
        if pw_rows:
            add(f"[6] 시점 간 사후비교 (대상 내, {track}, 다중비교 보정)")
            L.extend("  " + ln for ln in table(
                ["그룹", "비교", "n", "평균차", "95% CI", "raw p", "보정 p",
                 "효과크기 [95% CI]"], pw_rows,
                ["left", "left", "right", "right", "right", "right", "right",
                 "right"]))
            if p.n_times > 12 and not a.options.all_pairs:
                add("  ※ 시점이 12개를 넘어 기준시점 대비 + 인접 시점만 비교했습니다 "
                    "(--all-pairs 로 전체 조합).")
            add("")

        if a.between:
            add(f"[7] 시점별 군간 비교 (단순주효과, {track})")
            L.extend("  " + ln for ln in table(
                ["시점", "대비", "n", "평균차", "95% CI", "보정 p",
                 "효과크기 [95% CI]"], _rows_between(a),
                ["left", "left", "right", "right", "right", "right", "right"]))
            add("  ※ 기저 시점은 무작위배정 균형을 보여주는 참고값입니다 "
                "(CONSORT 15: 기저 검정은 부적절) — 다중비교 계산에서 제외했습니다.")
            add("")

    # -- [8] responder ----------------------------------------------------
    if a.responder is not None:
        r = a.responder
        unit = "% 개선" if r.kind == "%" else f"점({p.value_name})"
        basis = ("무응답 대체(NRI): 탈락자를 비반응으로 계산"
                 if r.nri else "관측 완료자 기준")
        add(f"[8] 반응자 분석 (MCID {fmt(r.threshold)}{unit} 이상, "
            f"{'낮을수록' if r.lower_is_better else '높을수록'} 호전 · {basis})")
        L.extend("  " + ln for ln in table(
            ["그룹", "시점", "반응자/n", "반응률", "95% CI"], _rows_responder(a),
            ["left", "left", "right", "right", "right"]))
        for con in r.contrasts:
            add("")
            add(f"  {con.time}: {con.group_a} {con.rate_a:.1%} vs "
                f"{con.group_b} {con.rate_b:.1%}")
            add(f"    위험차 RD = {con.risk_difference:+.1%} "
                f"[{con.rd_ci[0]:.1%}, {con.rd_ci[1]:.1%}] · "
                f"RR = {fmt(con.risk_ratio)} "
                f"[{fmt(con.rr_ci[0])}, {fmt(con.rr_ci[1])}] · "
                f"OR = {fmt(con.odds_ratio)} "
                f"[{fmt(con.or_ci[0])}, {fmt(con.or_ci[1])}]")
            add(f"    {con.nnt_note}: {fmt(con.nnt, 1)} · {con.method} "
                f"보정 p {fmt_p(con.p_adj)}")
        if not r.nri:
            add("  ※ 분모가 해당 시점 관측자입니다. ITT 기준 반응률이 필요하면 "
                "--responder-denominator randomized (NRI) 를 쓰세요.")
        for n in r.notes:
            add(f"  ※ {n}")
        add("")

    # -- [9] RCI ----------------------------------------------------------
    if a.rci is not None:
        r = a.rci
        add(f"[9] 신뢰변화지수 RCI (Jacobson–Truax; 신뢰도 r = {fmt(r.reliability, 2)}, "
            f"기준 SD = {fmt(r.sd_baseline)}"
            f"{' 사용자 지정' if r.sd_supplied else ' 관측값'}, "
            f"S_diff = {fmt(r.s_diff)}, 컷오프 |RCI| ≥ {fmt(r.cutoff)})")
        headers = ["그룹", "시점", "n", "신뢰적 호전", "변화 없음", "신뢰적 악화"]
        if r.recovery_cutoff is not None:
            headers.append(r.recovery_label)
        rows = []
        for x in r.rows:
            if not a.grouped and x.group != ALL_LABEL:
                continue
            row = [x.group, x.time, str(x.n),
                   f"{x.improved} ({x.improved / x.n:.0%})",
                   f"{x.unchanged} ({x.unchanged / x.n:.0%})",
                   f"{x.deteriorated} ({x.deteriorated / x.n:.0%})"]
            if r.recovery_cutoff is not None:
                row.append(f"{x.recovered} ({(x.recovered or 0) / x.n:.0%})")
            rows.append(row)
        L.extend("  " + ln for ln in table(headers, rows))
        for n in r.notes:
            add(f"  ※ {n}")
        add("")

    # -- [10] sentences ---------------------------------------------------
    add("[10] 논문용 문장 (그대로 붙여쓰고 숫자만 확인하세요)")
    for s in apa_sentences(a):
        add("  " + s)
    add("")

    if a.warnings:
        add("[!] 주의")
        for w in a.warnings:
            add("  · " + w)
        add("")
    add("* p < α, ** p < .01, *** p < .001 (보정 p 기준)")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def render_markdown(a: Analysis, full: bool = False, brief: bool = False) -> str:
    """Manuscript-ready Markdown: the same tables, pasteable into Word."""
    p = a.panel
    L: List[str] = ["# 반복측정 추이 분석 (longistat)", ""]
    add = L.append
    add(f"- 대상 수 N = {p.n_subjects}, 시점 {p.n_times}개: "
        f"{' → '.join(p.times)} (기준: {p.times[a.baseline_index]})")
    if p.groups is not None:
        sizes = {g: p.groups.count(g) for g in p.group_labels()}
        add("- 그룹: " + ", ".join(f"{g} (n={n})" for g, n in sizes.items()))
    add(f"- 완전자료 {a.missing.n_complete}/{a.missing.n_subjects} "
        f"({a.missing.complete_fraction:.0%}) · 권장 분석 "
        f"{_TRACK_NAME[a.recommended]}")
    add("")
    add(f"> {_COMPLETER_BANNER}")
    add("")

    def block(title: str, headers: Sequence[str],
              rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
        add(f"## {title}")
        add("")
        L.extend(md_table(headers, rows))
        add("")

    block("자료 가용성", ["범위", "배정 n"] + list(p.times) + ["완전자료"],
          _rows_availability(a))
    block("기술통계", ["그룹", "시점", "n", "평균", "SD", "95% CI",
                     "중앙값 [IQR]"], _rows_descriptives(a))
    block(f"반복측정/혼합 ANOVA ({_CORRECTION_NAME[a.correction_used]})",
          ["효과", "SS", "df", "F", "p", "ηp²", "η²G"], _rows_anova(a))
    block("시점 추세 (직교 다항 대비)",
          ["대비", "효과", "SS", "df", "F", "raw p", "보정 p", "ηp²"],
          _rows_trend(a))
    block(f"개인별 회귀 기울기 ({p.value_name}{_unit(a) or ' / 시점 1단위'})",
          ["그룹", "n", "평균 기울기", "SD", "95% CI", "p"], _rows_slopes(a))
    block("군간 기울기 차이",
          ["대비", "n", "기울기A", "기울기B", "차이", "95% CI", "p",
           "Hedges g [95% CI]"], _rows_slope_contrast(a))
    change = a.change_param if a.recommended == "parametric" else a.change_rank
    block(f"기준시점({change.baseline}) 대비 변화량",
          ["그룹", "시점", "n", "평균변화", "SD", "95% CI", "보정 p",
           "효과크기 [95% CI]"], _rows_change_within(a))
    block("군간 변화량 차이",
          ["시점", "대비", "n", "변화A", "변화B", "차이", "95% CI", "보정 p",
           "효과크기 [95% CI]"], _rows_change_between(a))
    block("기저값 보정 (ANCOVA)",
          ["시점", "대비", "n", "조정평균차", "95% CI", "보정 p", "비보정 차이",
           "기저 기울기"], _rows_ancova(a))
    block("결측 대체 민감도"
          + (" (" + "·".join(KIND_LABEL[k] for k in a.sensitivity.kinds) + ")"
             if a.sensitivity is not None else ""),
          ["시점", "대비" if a.sensitivity and a.sensitivity.grouped else "그룹",
           "분석", "n", "대체 셀", "추정치", "95% CI", "p"],
          _rows_sensitivity(a))
    if not brief:
        block("시점 간 사후비교",
              ["그룹", "비교", "n", "평균차", "95% CI", "raw p", "보정 p",
               "효과크기 [95% CI]"], _rows_pairwise(a))
        block("시점별 군간 비교",
              ["시점", "대비", "n", "평균차", "95% CI", "보정 p",
               "효과크기 [95% CI]"], _rows_between(a))
    block("반응자 분석", ["그룹", "시점", "반응자/n", "반응률", "95% CI"],
          _rows_responder(a))

    add("## 논문용 문장")
    add("")
    for s in apa_sentences(a):
        add(f"- {s}")
    add("")
    if a.warnings:
        add("## 주의")
        add("")
        for w in a.warnings:
            add(f"- {w}")
        add("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return _clean(asdict(obj))
    return obj


# json.dumps escapes control characters but not these five, so a group label of
# "</script>" survives verbatim into any dashboard that inlines the payload.
_JSON_INLINE_SAFE = (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                     (" ", "\\u2028"), (" ", "\\u2029"))


def render_json(a: Analysis) -> str:
    payload: Dict[str, Any] = {
        "n_subjects": a.panel.n_subjects,
        "times": a.panel.times,
        "baseline": a.panel.times[a.baseline_index],
        "primary_time": a.options.primary_time,
        "groups": a.panel.group_labels(),
        "value_name": a.panel.value_name,
        "alpha": a.options.alpha,
        "recommended": a.recommended,
        "recommendation_reason": a.recommendation_reason,
        "sphericity_correction": a.correction_used,
        "missing": _clean(a.missing),
        "descriptives": _clean(a.descriptives),
        "normality": _clean(a.normality),
        "anova": _clean(a.anova),
        "anova_error": a.anova_error,
        "friedman": _clean(a.friedman),
        "pairwise_parametric": _clean(a.pairwise_param),
        "pairwise_rank": _clean(a.pairwise_rank),
        "between_at_time": _clean(a.between),
        "change_parametric": _clean(a.change_param),
        "change_rank": _clean(a.change_rank),
        "ancova": _clean(a.ancova),
        "trend": _clean(a.trend),
        "sensitivity": _clean(a.sensitivity),
        "responder": _clean(a.responder),
        "rci": _clean(a.rci),
        "apa": apa_sentences(a),
        "warnings": a.warnings,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for ch, esc in _JSON_INLINE_SAFE:
        text = text.replace(ch, esc)
    return text


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def _safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in exported text cells.

    Numeric text keeps its leading sign — Excel reads it as a number, not a
    formula, and prefixing it would corrupt the value on re-import.
    """
    if not isinstance(value, str) or not value:
        return value
    if value[0] not in ("=", "+", "-", "@", "\t", "\r"):
        return value
    try:
        float(value)
        return value
    except ValueError:
        return "'" + value


def render_csv(a: Analysis) -> str:
    """Tidy long export.

    Both analysis tracks are exported (``track`` column).  Exporting only the
    parametric one meant ``--method nonparametric --format csv`` silently
    produced different p-values from the text report of the same command.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["section", "track", "group", "time", "label", "n", "estimate",
                "sd_or_se", "ci_low", "ci_high", "statistic", "p", "p_adj",
                "effect", "effect_ci_low", "effect_ci_high"])

    def row(*vals: Any) -> None:
        w.writerow([_safe(v) if isinstance(v, str) else
                    ("" if v is None or (isinstance(v, float) and
                                         not math.isfinite(v)) else v)
                    for v in vals])

    for c in a.descriptives:
        row("descriptive", "", c.group, c.time, "mean", c.n, c.mean, c.sd,
            c.ci_low, c.ci_high, "", "", "", "", "", "")
    if a.anova is not None:
        for eff in a.anova.effects:
            row("anova", "parametric", "", "", eff.name, a.anova.n_subjects,
                eff.ss, eff.ms, "", "", eff.f, eff.p,
                eff.p_reported(a.correction_used), eff.partial_eta2, "", "")
    for fr in a.friedman:
        row("friedman", "nonparametric", fr.group, "", "chi2", fr.n, fr.chi2,
            "", "", "", fr.chi2, fr.p, fr.p, fr.kendall_w, "", "")
    for track, change in (("parametric", a.change_param),
                          ("nonparametric", a.change_rank)):
        for r in change.within:
            row("change", track, r.group, r.time, r.method, r.n, r.mean_change,
                r.sd_change, r.ci_low, r.ci_high, "", r.p_raw, r.p_adj,
                r.effect, r.effect_ci[0], r.effect_ci[1])
        for c in change.between:
            row("change_between", track, f"{c.group_a}−{c.group_b}", c.time,
                c.method, c.n_a + c.n_b, c.diff, "", c.ci_low, c.ci_high, "",
                c.p_raw, c.p_adj, c.effect, c.effect_ci[0], c.effect_ci[1])
    for track, pw in (("parametric", a.pairwise_param),
                      ("nonparametric", a.pairwise_rank)):
        for r in pw:
            row("pairwise_time", track, r.group, f"{r.time_a}→{r.time_b}",
                r.method, r.n, r.mean_diff, "", r.ci_low, r.ci_high,
                r.statistic, r.p_raw, r.p_adj, r.effect, r.effect_ci[0],
                r.effect_ci[1])
    for r in a.between:
        row("between_at_time", a.recommended, f"{r.group_a}−{r.group_b}",
            r.time, "기저 참고" if r.reference_only else r.method,
            r.n_a + r.n_b, r.diff, "", r.ci_low, r.ci_high, r.statistic,
            r.p_raw, r.p_adj, r.effect, r.effect_ci[0], r.effect_ci[1])
    if a.ancova is not None:
        for c in a.ancova.contrasts:
            row("ancova", "parametric", f"{c.group_a}−{c.group_b}", c.time,
                "adjusted mean difference", c.n_a + c.n_b, c.adjusted_diff, "",
                c.ci_low, c.ci_high, c.t, c.p_raw, c.p_adj, c.unadjusted_diff,
                "", "")
    if a.trend is not None:
        for t in a.trend.effects:
            row("trend_contrast", "parametric", "", t.scope,
                f"{t.order_name} 대비", a.trend.n_complete, t.ss, t.se, t.ci_low,
                t.ci_high, t.f, t.p_raw, t.p_adj, t.partial_eta2, "", "")
        for s in a.trend.slopes:
            row("subject_slope", "parametric", s.group, "", "mean OLS slope",
                s.n, s.mean_slope, s.sd, s.ci_low, s.ci_high, s.t, s.p, s.p,
                "", "", "")
        for c in a.trend.slope_contrasts:
            row("slope_between", "parametric", f"{c.group_a}−{c.group_b}", "",
                c.method, c.n_a + c.n_b, c.diff, "", c.ci_low, c.ci_high, "",
                c.p, c.p, c.effect, c.effect_ci[0], c.effect_ci[1])
    if a.sensitivity is not None:
        for r in a.sensitivity.rows:
            row("sensitivity", "parametric", r.contrast, r.time,
                KIND_LABEL[r.kind], r.n, r.estimate, "", r.ci_low, r.ci_high,
                r.imputed, r.p, "", "", "", "")
    if a.responder is not None:
        for x in a.responder.rates:
            row("responder_rate", "", x.group, x.time, "rate", x.n, x.rate, "",
                x.ci_low, x.ci_high, x.responders, "", "", "", "", "")
        for c in a.responder.contrasts:
            row("responder_contrast", "", f"{c.group_a}−{c.group_b}", c.time,
                c.method, "", c.risk_difference, "", c.rd_ci[0], c.rd_ci[1],
                c.odds_ratio, c.p_raw, c.p_adj, c.nnt, "", "")
    if a.rci is not None:
        for x in a.rci.rows:
            row("rci", "", x.group, x.time, "reliably improved", x.n,
                x.improved / x.n if x.n else "", "", "", "", x.improved, "",
                "", x.deteriorated, "", "")
    return buf.getvalue()
