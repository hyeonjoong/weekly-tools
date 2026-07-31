"""Render a Table1 object as Markdown / CSV / TSV / JSON — pure standard library."""

from __future__ import annotations

import html
import io
import json
import math
from typing import List, Optional

from .build import CategoricalRow, ContinuousRow, Options, Table1
from .multiplicity import normalize_method

__all__ = ["render"]

_FORMULA_TRIGGERS = ("=", "+", "-", "@")

# --------------------------------------------------------------------------- #
# Language strings (ko default, en for English-language manuscripts)
# --------------------------------------------------------------------------- #
_LANG = {
    "ko": {
        "title": "표 1. 기저 특성 (Table 1. Baseline characteristics)",
        "char": "특성 (Characteristic)",
        "overall": "전체",
        "p": "p값",
        "p_adj": "p(보정)",
        "p_trend": "p(경향)",
        "effect": "차이 (95% CI)",
        "smd": "SMD",
        "test": "검정",
        "missing": "결측",
        "mean_sd": "평균(SD)",
        "median_iqr": "중앙값[IQR]",
        "n_pct": "n(%)",
        "notes_hdr": "**주석**",
        "warn_hdr": "**경고**",
        "pct_col": "열 기준(그룹 내 비결측 %)",
        "pct_col_incl_missing": "열 기준(그룹 내 전체 % — '(결측)' 수준 포함)",
        "pct_row": "행 기준(수준 내 %)",
        "leg_notation": ("**표기**: 연속형 = 평균(표준편차) 또는 중앙값[IQR] "
                         "(정규성에 따라 자동), 범주형 = n(%). % 는 "),
        "leg_p": ("**p값**: 연속형은 정규성·등분산 점검 후 t/Welch/Mann-Whitney "
                  "(≥3군: ANOVA/Kruskal-Wallis), 범주형은 Pearson χ² 또는 "
                  "Fisher exact."),
        "leg_p_welch": ("**p값**: 연속형은 (사전검정 없이) 항상 Welch t "
                        "(≥3군: 일원배치 ANOVA), 범주형은 Pearson χ² 또는 "
                        "Fisher exact."),
        "leg_p_student": ("**p값**: 연속형은 (사전검정 없이) 항상 Student t "
                          "(≥3군: 일원배치 ANOVA), 범주형은 Pearson χ² 또는 "
                          "Fisher exact."),
        "leg_p_nonparam": ("**p값**: 연속형은 (정규성과 무관하게) Mann-Whitney U "
                           "(≥3군: Kruskal-Wallis), 범주형은 Pearson χ² 또는 "
                           "Fisher exact."),
        "leg_smd": ("**SMD**: 표준화 평균차(절대값). |SMD|>0.1 이면 두 군 간 "
                    "불균형을 시사(범주형은 Yang–Dalton 다변량 SMD)."),
        "leg_effect": ("**차이 (95% CI)**: 첫 군 − 둘째 군(양수면 첫 군이 큼). "
                       "연속형은 모수 검정 시 평균차(보고한 검정과 같은 SE의 t-CI), "
                       "비모수 시 Hodges–Lehmann 중앙값 이동량(쌍별 차의 중앙값이라 "
                       "표시된 두 중앙값의 차와 다를 수 있음). 이진형은 표시된 수준"
                       "(예: 'M:')의 위험차(%p, Newcombe 점수구간 — 범주형 검정과는 "
                       "다른 방법이라 p값의 유의성과 어긋날 수 있음)."),
        "leg_padj": ("**p(보정)**: {method} 다중비교 보정(변수 {m}개 기준). "
                     "무작위배정 시험의 기저 p값 보정은 권장되지 않습니다"
                     "(비교/관찰 연구용)."),
        "leg_trend": ("**p(경향)**: 순서형 군(용량·사분위 등)에 대한 경향성 검정 "
                      "— 연속형은 '검정' 열에 보고된 검정 계열을 따라, 모수 검정"
                      "(t/ANOVA) 행은 일원배치 ANOVA의 선형대비, 순위 검정"
                      "(Mann-Whitney/Kruskal-Wallis) 행은 Jonckheere–Terpstra"
                      "(연속성 보정 없음)를 쓰고, 이진 범주형은 Cochran–Armitage "
                      "입니다(표기를 바꾸는 --display 는 경향성 검정 선택에 영향을 "
                      "주지 않습니다). 군 순서는 표의 열 순서(왼→오른쪽)를 "
                      "'낮음→높음'으로 봅니다{scores}. 3수준 이상 (순서 없는) "
                      "범주형은 단일 경향이 정의되지 않아 공란입니다."),
        "leg_weighted": ("**가중 분석**: 가중치 열 '{col}' 로 가중한 유사모집단"
                         "(pseudo-population) 요약입니다. 연속형은 가중 평균(가중 SD) "
                         "또는 가중 중앙값[가중 IQR], 범주형은 **가중 n(가중 %)** 이며, "
                         "머리글의 n 은 실제 관측 수, ESS 는 Kish 유효표본수"
                         "((Σw)²/Σw²)입니다. 균형은 **가중 SMD**(Austin & Stuart 2015)로 "
                         "판단하세요 — 가중 p값·차이(95% CI)는 설계기반 분산이 필요해 "
                         "생략했습니다."),
    },
    "en": {
        "title": "Table 1. Baseline characteristics",
        "char": "Characteristic",
        "overall": "Overall",
        "p": "p",
        "p_adj": "p (adj)",
        "p_trend": "p (trend)",
        "effect": "Difference (95% CI)",
        "smd": "SMD",
        "test": "Test",
        "missing": "missing",
        "mean_sd": "Mean (SD)",
        "median_iqr": "Median [IQR]",
        "n_pct": "n (%)",
        "notes_hdr": "**Notes**",
        "warn_hdr": "**Warnings**",
        "pct_col": "column-wise (% of non-missing within group)",
        "pct_col_incl_missing": ("column-wise (% of all cells within group, "
                                 "including the '(missing)' level)"),
        "pct_row": "row-wise (% within level)",
        "leg_notation": ("**Notation**: continuous = mean (SD) or median [IQR] "
                         "(auto by normality), categorical = n (%). % is "),
        "leg_p": ("**p**: continuous uses t/Welch/Mann-Whitney after "
                  "normality/variance checks (>=3 groups: ANOVA/Kruskal-Wallis); "
                  "categorical uses Pearson chi-square or Fisher exact."),
        "leg_p_welch": ("**p**: continuous always uses Welch t (no pre-test; "
                        ">=3 groups: one-way ANOVA); categorical uses Pearson "
                        "chi-square or Fisher exact."),
        "leg_p_student": ("**p**: continuous always uses Student t (no pre-test; "
                          ">=3 groups: one-way ANOVA); categorical uses Pearson "
                          "chi-square or Fisher exact."),
        "leg_p_nonparam": ("**p**: continuous always uses Mann-Whitney U "
                           "(regardless of normality; >=3 groups: "
                           "Kruskal-Wallis); categorical uses Pearson "
                           "chi-square or Fisher exact."),
        "leg_smd": ("**SMD**: absolute standardized mean difference. |SMD|>0.1 "
                    "suggests between-group imbalance (categorical: Yang-Dalton "
                    "multivariate SMD)."),
        "leg_effect": ("**Difference (95% CI)**: group 1 - group 2 (positive = "
                       "first group higher). Continuous: difference in means with "
                       "a t-CI from the reported test's SE (parametric), or the "
                       "Hodges-Lehmann median shift = median of pairwise "
                       "differences, which can differ from the difference of the "
                       "displayed medians (nonparametric). Binary: risk difference "
                       "for the labelled level (e.g. 'M:') in percentage points "
                       "(Newcombe score interval — a different method from the "
                       "categorical test, so it may disagree with the p-value)."),
        "leg_padj": ("**p (adj)**: {method} multiple-comparison adjustment "
                     "(family of {m} variables). Adjusting baseline p-values in a "
                     "randomized trial is discouraged (for comparative/"
                     "observational tables)."),
        "leg_trend": ("**p (trend)**: test for a monotone trend across the "
                      "ordered groups (dose levels, quartiles). A continuous "
                      "row follows the family of the test named in the Test "
                      "column: the one-way ANOVA linear contrast for a "
                      "parametric test (t/ANOVA), Jonckheere-Terpstra without "
                      "a continuity correction for a rank test "
                      "(Mann-Whitney/Kruskal-Wallis); binary categorical rows "
                      "use Cochran-Armitage. (--display changes the summary "
                      "shown, not which trend test is used.) Group order is the "
                      "column order shown, read left-to-right as "
                      "low-to-high{scores}. An unordered categorical with 3+ "
                      "levels is left blank (no single direction of trend)."),
        "leg_weighted": ("**Weighted analysis**: summaries describe the "
                         "pseudo-population weighted by column '{col}'. "
                         "Continuous = weighted mean (weighted SD) or weighted "
                         "median [weighted IQR]; categorical = **weighted n "
                         "(weighted %)**. In the header, n is the raw number of "
                         "observations and ESS is Kish's effective sample size "
                         "((sum w)^2 / sum w^2). Judge balance with the "
                         "**weighted SMD** (Austin & Stuart 2015) — weighted "
                         "p-values and difference CIs need design-based "
                         "variances and are omitted."),
    },
}

# Display names for the --padjust methods, used in the legend.
_PADJUST_NAMES = {
    "bonferroni": "Bonferroni",
    "holm": "Holm",
    "bh": "Benjamini-Hochberg (FDR)",
    "by": "Benjamini-Yekutieli (FDR)",
}


def _lang(opt: Options) -> dict:
    return _LANG.get(getattr(opt, "lang", "ko") or "ko", _LANG["ko"])


def _leg_p_for(L: dict, opt: Options) -> str:
    """p-value legend, adapted to an explicit --test-cont so the footnote does
    not claim a normality/variance pre-test that did not happen."""
    tc = getattr(opt, "test_cont", "auto")
    return L.get(f"leg_p_{tc}", L["leg_p"])


def _leg_padj(L: dict, t: Table1, opt: Options) -> str:
    """Legend line for the adjusted-p column, naming the method and family size."""
    method = normalize_method(getattr(opt, "padjust", "none"))
    m = sum(1 for r in t.rows if getattr(r, "pvalue", None) is not None)
    return L["leg_padj"].format(method=_PADJUST_NAMES.get(method, method), m=m)


def _disp_name(opt: Options, name: str) -> str:
    """Apply a --labels display-name override (pretty name / units)."""
    return getattr(opt, "labels", {}).get(name, name)


def _fmt_num(x: float, d: int) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        # A non-finite summary (e.g. a variance that overflowed on ~1e308 data)
        # would otherwise print "inf" or a ~300-digit integer; render the symbol
        # instead, consistent with _fmt_smd.
        return "∞" if x > 0 else "-∞"
    d = max(0, d)  # negative precision is a ValueError in format specs
    return f"{x:.{d}f}"


def _fmt_p(p: Optional[float]) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.001"
    if p > 0.999:
        return ">0.999"
    return f"{p:.3f}"


def _fmt_smd(s: Optional[float]) -> str:
    if s is None:
        return ""
    if math.isinf(s):
        return "∞"
    if math.isnan(s):
        return "—"
    return f"{s:.3f}"


def _fmt_effect(e, decimals: int, pct_decimals: int) -> str:
    """Format an Effect as 'estimate (lo, hi)'.

    A risk difference is shown in percentage points (x100) with a %p suffix so
    it is directly comparable to the n(%) cells; a mean/median difference is
    shown in the variable's own units.
    """
    if e is None:
        return ""
    if any(x is None or (isinstance(x, float) and not math.isfinite(x))
           for x in (e.estimate, e.lo, e.hi)):
        return "—"
    if e.kind == "risk_diff":
        d = pct_decimals
        est, lo, hi = e.estimate * 100.0, e.lo * 100.0, e.hi * 100.0
        suffix = " %p"
    else:
        d = decimals
        est, lo, hi = e.estimate, e.lo, e.hi
        suffix = ""
    return (f"{_fmt_num(est, d)} ({_fmt_num(lo, d)}, {_fmt_num(hi, d)})"
            + suffix)


def _effect_index_prefix(e, esc) -> str:
    """For a binary risk difference in the non-collapsed layout, prefix the
    index level (e.g. ``M: ``) so the reader knows which of the two levels the
    difference refers to. ``esc`` escapes the (data-derived) level per format.
    """
    if e is None or e.kind != "risk_diff" or not getattr(e, "index_level", None):
        return ""
    return esc(e.index_level) + ": "


def _range_suffix(st, d: int) -> str:
    if st.n == 0 or math.isnan(st.vmin) or math.isnan(st.vmax):
        return ""
    return f" ({_fmt_num(st.vmin, d)}–{_fmt_num(st.vmax, d)})"


def _cont_cell(st, display: str, d: int, show_range: bool = False) -> str:
    if st.n == 0:
        return "—"
    mean = f"{_fmt_num(st.mean, d)} ({_fmt_num(st.sd, d)})"
    med = f"{_fmt_num(st.median, d)} [{_fmt_num(st.q1, d)}, {_fmt_num(st.q3, d)}]"
    if display == "mean":
        body = mean
    elif display == "median":
        body = med
    else:
        body = f"{mean} / {med}"
    if show_range:
        body += _range_suffix(st, d)
    return body


def _pct(count: int, denom: int) -> float:
    return 100.0 * count / denom if denom else float("nan")


def _cat_cell(count: int, denom: int, level_total: int, pct_mode: str,
              pct_decimals: int = 1) -> str:
    p = _pct(count, level_total) if pct_mode == "row" else _pct(count, denom)
    return f"{count} ({_fmt_num(p, pct_decimals)})"


def _ess_suffix(t: Table1, gi: Optional[int], opt: Options) -> str:
    """', ESS=…' for a weighted group header ('' when unweighted).

    Kish's effective sample size is the honest denominator of a weighted group:
    it equals n when the weights are equal and shrinks as they spread out, so a
    reader can see immediately how much information an IPTW arm really carries.
    ``gi=None`` gives the whole-cohort ESS (pooled across groups).
    """
    if not t.meta.get("weighted"):
        return ""
    ess = t.meta.get("ess") or []
    if gi is None:
        # Pooled over every retained row's weight — NOT the sum of the per-group
        # ESS values, which is a different (larger) quantity.
        pooled = t.meta.get("ess_overall")
        return "" if pooled is None else f", ESS={_fmt_num(pooled, 1)}"
    if gi >= len(ess):
        return ""
    return f", ESS={_fmt_num(ess[gi], 1)}"


def _cat_cell_for(row: CategoricalRow, lvl, gi: Optional[int],
                  opt: Options) -> str:
    """Render one categorical cell — the Overall column when ``gi`` is None,
    otherwise group ``gi``.

    Single choke point for the count/percent basis so the weighted and
    unweighted tables cannot drift apart across the four renderers. Under
    weighting the cell shows the *summed weight* and the *weighted* percent
    (the pseudo-population is what a weighted Table 1 describes); the raw
    head-count still drives the header n and the missing counts.
    """
    if row.wdenom_per_group is not None and lvl.wcounts is not None:
        count = lvl.woverall if gi is None else lvl.wcounts[gi]
        denom = (row.woverall_denom if gi is None
                 else row.wdenom_per_group[gi])
        base = lvl.woverall if row.pct == "row" else denom
        p = 100.0 * count / base if base else float("nan")
        return (f"{_fmt_num(count, opt.decimals)} "
                f"({_fmt_num(p, opt.pct_decimals)})")
    count = lvl.overall if gi is None else lvl.counts[gi]
    denom = row.overall_denom if gi is None else row.denom_per_group[gi]
    return _cat_cell(count, denom, lvl.overall, row.pct, opt.pct_decimals)


def _binary_collapse(row: CategoricalRow, opt: Options):
    """For --binary-single: the single CatLevel to display for a 2-level
    categorical, or None if the row should render normally.

    Collapses only when exactly two levels are present (so a synthetic
    '(결측)' missing level, which makes three, disables the collapse). The
    displayed level is the non-reference one: reference defaults to the first
    level, overridable per column via --ref COL=level.
    """
    if not getattr(opt, "binary_single", False):
        return None
    if len(row.levels) != 2:
        return None
    labels = [lvl.label for lvl in row.levels]
    ref = getattr(opt, "ref", {}).get(row.name)
    show = 1  # default: reference = first level, show the second
    if ref is not None and ref == labels[1]:
        show = 0
    return row.levels[show]


def _cont_suffix(row: ContinuousRow, L: dict) -> str:
    if row.display == "mean":
        return f" — {L['mean_sd']}"
    if row.display == "median":
        return f" — {L['median_iqr']}"
    return f" — {L['mean_sd']}/{L['median_iqr']}"


def _col_flags(t: Table1, opt: Options):
    """Which optional analytic columns are present, in table order.

    Returns (two_group, show_p, show_effect, show_padj, single_group). All
    renderers share this so the columns line up identically. A single-group
    (no grouping column) descriptive table suppresses every comparison column
    and the test column.
    """
    two_group = len(t.groups) == 2
    single_group = len(t.groups) < 2
    # A weighted table has no p-values, no adjusted p-values and no effect CI
    # (all would need design-based variances) — build_table1 records the
    # decision in meta so every renderer agrees with the builder rather than
    # re-deriving it.
    weighted = bool(t.meta.get("weighted"))
    show_p = bool(t.meta.get("show_pvalue", opt.show_pvalue)) and not single_group
    show_effect = (two_group and getattr(opt, "effect", False)
                   and not weighted)
    # Only show the adjusted-p column when at least one variable is actually
    # testable; otherwise the column is all "—" and the legend reads "0 vars".
    any_testable = any(getattr(r, "pvalue", None) is not None for r in t.rows)
    show_padj = (show_p and any_testable and
                 normalize_method(getattr(opt, "padjust", "none")) != "none")
    return two_group, show_p, show_effect, show_padj, single_group


def _show_trend(t: Table1) -> bool:
    """Whether the p-for-trend column is present (decided by the builder)."""
    return bool(t.meta.get("trend"))


def _ident(x: str) -> str:
    return x


def _leg_trend(L: dict, t: Table1, esc=_ident) -> str:
    """Trend legend, naming the custom scores when --trend-scores was used.

    Jonckheere-Terpstra is rank-based and ignores the scores, so the clause
    says which rows actually used them (and disappears entirely when none did):
    a legend that gets pasted into a manuscript must not assert a mg dose axis
    that no reported p-value was computed on.
    """
    scores = t.meta.get("trend_scores")
    if not scores:
        return L["leg_trend"].format(scores="")
    used = [r for r in t.rows
            if getattr(r, "trend_test", None) in ("linear contrast",
                                                  "Cochran-Armitage")]
    ranked = [r for r in t.rows
              if getattr(r, "trend_test", None) == "Jonckheere-Terpstra"]
    if not used:
        # Nothing consumed the scores — say so instead of listing them as if
        # they had been applied.
        return L["leg_trend"].format(
            scores=(" (지정한 --trend-scores 는 순위 기반 Jonckheere–Terpstra "
                    "행에만 해당해 적용되지 않았습니다)" if L is _LANG["ko"] else
                    " (the --trend-scores given were not used: every trend row "
                    "here is the rank-based Jonckheere-Terpstra)"))
    pairs = ", ".join(f"{esc(g)}={_fmt_score(x)}"
                      for g, x in zip(t.groups, scores))
    if L is _LANG["ko"]:
        tail = f" (점수: {pairs}"
        tail += ("; 순위 기반 Jonckheere–Terpstra 행에는 적용되지 않음)"
                 if ranked else ")")
    else:
        tail = f" (scores: {pairs}"
        tail += ("; not applied to the rank-based Jonckheere-Terpstra rows)"
                 if ranked else ")")
    return L["leg_trend"].format(scores=tail)


def _fmt_score(x: float) -> str:
    """Score as it appears in the legend.

    Exact for the realistic dose values (0, 10, 40); falls back to %g above
    2**53, where ``str(int(x))`` would print a 100-digit expansion of a float
    that never held that many significant digits.
    """
    x = float(x)
    if x.is_integer() and abs(x) < 2 ** 53:
        return str(int(x))
    return f"{x:g}"


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def _md_escape(s: str) -> str:
    """Escape a DATA string for a Markdown table cell.

    Neutralizes: table-breaking pipes and newlines, and HTML injection
    (`<img onerror=...>` from a free-text patient value) since Markdown viewers
    render raw HTML. Applied only to data (variable names, level/group labels),
    never to tool-generated markup like the <sup> footnote markers.
    """
    s = s.replace("\r", " ").replace("\n", " ")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("|", "\\|")


def _render_markdown(t: Table1, opt: Options) -> str:
    L = _lang(opt)
    two_group, show_p, show_effect, show_padj, single_group = _col_flags(t, opt)
    # In a single-group table the group column and the Overall column coincide,
    # so show only Overall (forced on) and drop the test column.
    show_overall = True if single_group else opt.overall
    show_groups = not single_group
    # No test is run under weighting, so the 검정/Test column is dropped too.
    show_test = not single_group and not bool(t.meta.get("weighted"))
    show_trend = _show_trend(t)
    out: List[str] = []
    out.append("## " + L["title"])
    out.append("")

    def value_tail(row, with_level: bool = True) -> List[str]:
        """Trailing analytic cells for a row that carries statistics.

        ``with_level`` prepends the binary risk-difference index level (e.g.
        ``M: ``); suppressed on a collapsed row whose label already names it.
        """
        tail: List[str] = []
        if show_effect:
            pre = _effect_index_prefix(row.effect, _md_escape) if with_level else ""
            tail.append(pre + _fmt_effect(row.effect, opt.decimals,
                                          opt.pct_decimals))
        if show_p:
            tail.append(_fmt_p(row.pvalue))
        if show_trend:
            tail.append(_fmt_p(getattr(row, "trend_p", None)))
        if show_padj:
            tail.append(_fmt_p(row.p_adjusted))
        if two_group:
            tail.append(_fmt_smd(row.smd))
        if show_test:
            tail.append(row.test_name)
        return tail

    def blank_tail() -> List[str]:
        """Trailing cells for a level sub-row (no per-level statistics)."""
        n = (int(show_effect) + int(show_p) + int(show_trend) + int(show_padj)
             + int(two_group) + int(show_test))
        return [""] * n

    headers = [L["char"]]
    if show_overall:
        headers.append(f"{L['overall']} (N={t.overall_size}"
                       f"{_ess_suffix(t, None, opt)})")
    if show_groups:
        for gi, (g, n) in enumerate(zip(t.groups, t.group_sizes)):
            headers.append(f"{_md_escape(g)} (n={n}{_ess_suffix(t, gi, opt)})")
    if show_effect:
        headers.append(L["effect"])
    if show_p:
        headers.append(L["p"])
    if show_trend:
        headers.append(L["p_trend"])
    if show_padj:
        headers.append(L["p_adj"])
    if two_group:
        headers.append(L["smd"])
    if show_test:
        headers.append(L["test"])

    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")

    note_markers: List[str] = []

    def note_suffix(notes: List[str]) -> str:
        if not notes:
            return ""
        idx = len(note_markers) + 1
        note_markers.append("; ".join(notes))
        return f" <sup>{idx}</sup>"

    def miss_suffix(n_missing: int, per_group=None) -> str:
        """Total missing, with a per-group breakdown so differential
        missingness between arms (a key trial-review concern) is visible."""
        if not n_missing:
            return ""
        base = f" · {L['missing']} {n_missing}"
        if per_group is not None and len(t.groups) >= 2:
            parts = [f"{_md_escape(g)} {m}"
                     for g, m in zip(t.groups, per_group) if m]
            if parts:
                base += " (" + ", ".join(parts) + ")"
        return base

    for row in t.rows:
        if isinstance(row, ContinuousRow):
            per_group_missing = [st.n_missing for st in row.per_group]
            label = (_md_escape(_disp_name(opt, row.name)) + _cont_suffix(row, L)
                     + miss_suffix(row.n_missing_total, per_group_missing)
                     + note_suffix(row.notes))
            cells = [label]
            if show_overall:
                cells.append(_cont_cell(row.overall, row.display, opt.decimals,
                                        opt.show_range))
            if show_groups:
                for st in row.per_group:
                    cells.append(_cont_cell(st, row.display, opt.decimals,
                                            opt.show_range))
            cells.extend(value_tail(row))
            out.append("| " + " | ".join(cells) + " |")
        else:  # CategoricalRow
            pd = opt.pct_decimals
            single = _binary_collapse(row, opt)
            if single is not None:
                # Collapsed 2-level row: one line, value/percent of the shown
                # level, with effect/p/SMD/test on the same row.
                label = (_md_escape(_disp_name(opt, row.name)) + " = "
                         + _md_escape(single.label) + f" — {L['n_pct']}"
                         + miss_suffix(row.n_missing_total, row.missing_per_group)
                         + note_suffix(row.notes))
                cells = [label]
                if show_overall:
                    cells.append(_cat_cell_for(row, single, None, opt))
                if show_groups:
                    for gi, c in enumerate(single.counts):
                        cells.append(_cat_cell_for(row, single, gi, opt))
                cells.extend(value_tail(row, with_level=False))
                out.append("| " + " | ".join(cells) + " |")
                continue
            header_label = (_md_escape(_disp_name(opt, row.name)) + f" — {L['n_pct']}"
                            + miss_suffix(row.n_missing_total,
                                          row.missing_per_group)
                            + note_suffix(row.notes))
            cells = [header_label]
            if show_overall:
                cells.append("")
            if show_groups:
                cells.extend([""] * len(t.groups))
            cells.extend(value_tail(row))
            out.append("| " + " | ".join(cells) + " |")
            for lvl in row.levels:
                lcells = [" " + _md_escape(lvl.label)]
                if show_overall:
                    lcells.append(_cat_cell_for(row, lvl, None, opt))
                if show_groups:
                    for gi, c in enumerate(lvl.counts):
                        lcells.append(_cat_cell_for(row, lvl, gi, opt))
                lcells.extend(blank_tail())
                out.append("| " + " | ".join(lcells) + " |")

    out.append("")
    # Legend + notes
    if opt.pct == "col":
        # Under --missing-as-level the synthetic '(결측)' level is counted, so the
        # column denominator is the full group, not the non-missing subset — but
        # only when a categorical variable actually HAS missing (otherwise no
        # synthetic level was added and the denominator is unchanged).
        incl_missing = (getattr(opt, "missing_as_level", False) and
                        any(isinstance(r, CategoricalRow) and r.n_missing_total > 0
                            for r in t.rows))
        pct_desc = L["pct_col_incl_missing"] if incl_missing else L["pct_col"]
    else:
        pct_desc = L["pct_row"]
    legend = [L["leg_notation"] + pct_desc + "."]
    if t.meta.get("weighted"):
        # State the weighting up front: every number above it is a weighted
        # (pseudo-population) quantity, which a reader must not mistake for a
        # raw count.
        legend.append(L["leg_weighted"].format(
            col=_md_escape(str(t.meta.get("weight_col")))))
    if show_effect:
        legend.append(L["leg_effect"])
    if show_p:
        legend.append(_leg_p_for(L, opt))
    if show_trend:
        legend.append(_leg_trend(L, t, _md_escape))
    if show_padj:
        legend.append(_leg_padj(L, t, opt))
    if two_group:
        legend.append(L["leg_smd"])
    out.extend(legend)
    if note_markers:
        out.append("")
        out.append(L["notes_hdr"])
        for i, n in enumerate(note_markers, 1):
            out.append(f"{i}. {_md_escape(n)}")
    if t.warnings:
        out.append("")
        out.append(L["warn_hdr"])
        for w in t.warnings:
            out.append(f"- {_md_escape(w)}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CSV / TSV
# --------------------------------------------------------------------------- #
def _csv_safe(field: str) -> str:
    """Neutralize spreadsheet formula injection for a DATA-DERIVED field.

    Applied only to attacker-influenceable cells (variable names, level/group
    labels), never to tool-generated numeric summary cells — so a legitimate
    negative mean like ``-3.6 (2.3)`` is left intact instead of being prefixed
    with a stray apostrophe. A leading ``= + - @`` (the Excel/LibreOffice
    formula/DDE triggers) on a data cell is quoted so the spreadsheet imports it
    as text.
    """
    if field and field[0] in _FORMULA_TRIGGERS:
        return "'" + field
    return field


def _render_delimited(t: Table1, opt: Options, delim: str) -> str:
    import csv as _csv

    two_group, show_p, show_effect, show_padj, single_group = _col_flags(t, opt)
    show_overall = True if single_group else opt.overall
    show_groups = not single_group
    # No test is run under weighting, so the 검정/Test column is dropped too.
    show_test = not single_group and not bool(t.meta.get("weighted"))
    show_trend = _show_trend(t)
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=delim, lineterminator="\n")

    header = ["characteristic", "level"]
    if show_overall:
        header.append(f"overall_N={t.overall_size}")
    if show_groups:
        header += [f"{g}_n={n}{_ess_suffix(t, gi, opt)}"
                   for gi, (g, n) in enumerate(zip(t.groups, t.group_sizes))]
    if show_p:
        header.append("p_value")
    if show_test:
        header.append("test")
    header.append("n_missing")
    if two_group:
        header.append("smd")
    # New analytic columns are appended at the END so existing column indices
    # stay stable for downstream parsers.
    if show_effect:
        header.append("effect_95ci")
    if show_padj:
        header.append("p_adjusted")
    if show_trend:
        header += ["p_trend", "trend_test"]
    writer.writerow([_csv_safe(h) for h in header])

    def value_tail(row, with_level: bool = True) -> List[str]:
        tail: List[str] = []
        if show_effect:
            pre = _effect_index_prefix(row.effect, _csv_safe) if with_level else ""
            tail.append(pre + _fmt_effect(row.effect, opt.decimals,
                                          opt.pct_decimals))
        if show_padj:
            tail.append(_fmt_p(row.p_adjusted))
        if show_trend:
            tail.append(_fmt_p(getattr(row, "trend_p", None)))
            tail.append(getattr(row, "trend_test", None) or "")
        return tail

    def blank_tail() -> List[str]:
        return [""] * (int(show_effect) + int(show_padj) + 2 * int(show_trend))

    # Only DATA-derived cells (variable name, level label) are formula-guarded;
    # tool-generated numeric cells (means, counts, p-values, SMD) are written
    # raw so a legitimate negative value is not corrupted.
    for row in t.rows:
        sname = _csv_safe(_disp_name(opt, row.name))
        if isinstance(row, ContinuousRow):
            L = _lang(opt)
            line = [sname, _cont_suffix(row, L).split(" — ")[-1]]
            if show_overall:
                line.append(_cont_cell(row.overall, row.display, opt.decimals,
                                       opt.show_range))
            if show_groups:
                for st in row.per_group:
                    line.append(_cont_cell(st, row.display, opt.decimals,
                                           opt.show_range))
            if show_p:
                line.append(_fmt_p(row.pvalue))
            if show_test:
                line.append(row.test_name)
            line.append(str(row.n_missing_total))
            if two_group:
                line.append(_fmt_smd(row.smd))
            line += value_tail(row)
            writer.writerow([str(x) for x in line])
        else:
            pd = opt.pct_decimals
            single = _binary_collapse(row, opt)
            if single is not None:
                line = [sname, _csv_safe(single.label)]
                if show_overall:
                    line.append(_cat_cell_for(row, single, None, opt))
                if show_groups:
                    for gi, c in enumerate(single.counts):
                        line.append(_cat_cell_for(row, single, gi, opt))
                if show_p:
                    line.append(_fmt_p(row.pvalue))
                if show_test:
                    line.append(row.test_name)
                line.append(str(row.n_missing_total))
                if two_group:
                    line.append(_fmt_smd(row.smd))
                line += value_tail(row, with_level=False)
                writer.writerow([str(x) for x in line])
                continue
            line = [sname, "n(%)"]
            if show_overall:
                line.append("")
            if show_groups:
                line += [""] * len(t.groups)
            # The variable-level p and SMD belong on this header row, exactly as
            # md/html/latex/json report them. Blanking them here silently
            # stripped every multi-level categorical of its statistics from the
            # CSV export the README recommends for manuscripts.
            if show_p:
                line.append(_fmt_p(row.pvalue))
            if show_test:
                line.append(row.test_name)
            line.append(str(row.n_missing_total))
            if two_group:
                line.append(_fmt_smd(row.smd))
            line += value_tail(row)
            writer.writerow([str(x) for x in line])
            for lvl in row.levels:
                lline = [sname, _csv_safe(lvl.label)]
                if show_overall:
                    lline.append(_cat_cell_for(row, lvl, None, opt))
                if show_groups:
                    for gi, c in enumerate(lvl.counts):
                        lline.append(_cat_cell_for(row, lvl, gi, opt))
                if show_p:
                    lline.append("")
                if show_test:
                    lline.append("")
                lline.append("")  # n_missing (blank on a level sub-row)
                if two_group:
                    lline.append("")
                lline += blank_tail()
                writer.writerow([str(x) for x in lline])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def _finite(x):
    """Map non-finite floats to None so the JSON is valid RFC-8259."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _render_json(t: Table1, opt: Options) -> str:
    def stat(st):
        return {
            "n": st.n, "n_missing": st.n_missing,
            "mean": _finite(st.mean), "sd": _finite(st.sd),
            "median": _finite(st.median), "q1": _finite(st.q1),
            "q3": _finite(st.q3), "min": _finite(st.vmin), "max": _finite(st.vmax),
            # Weighted mode only (null otherwise). Under weighting, mean/sd/
            # median/q1/q3 above are already the WEIGHTED estimates, while "n"
            # stays the raw observation count.
            "weight_sum": _finite(st.wsum), "ess": _finite(st.ess),
        }

    def effect(e):
        if e is None:
            return None
        return {
            "estimate": _finite(e.estimate), "ci_low": _finite(e.lo),
            "ci_high": _finite(e.hi), "kind": e.kind, "conf": e.conf,
            "index_level": getattr(e, "index_level", None),
            "reference_level": getattr(e, "reference_level", None),
        }

    rows = []
    for row in t.rows:
        disp = _disp_name(opt, row.name)
        if isinstance(row, ContinuousRow):
            rows.append({
                "name": row.name, "label": disp, "kind": "continuous",
                "display": row.display, "overall": stat(row.overall),
                "groups": [stat(s) for s in row.per_group],
                "p_value": _finite(row.pvalue),
                "p_adjusted": _finite(row.p_adjusted), "test": row.test_name,
                "smd": _finite(row.smd), "effect": effect(row.effect),
                "p_trend": _finite(row.trend_p), "trend_test": row.trend_test,
                "n_missing": row.n_missing_total, "notes": row.notes,
            })
        else:
            rows.append({
                "name": row.name, "label": disp, "kind": "categorical",
                "pct": row.pct, "denom_per_group": row.denom_per_group,
                "overall_denom": row.overall_denom,
                "missing_per_group": row.missing_per_group,
                "levels": [{"label": l.label, "counts": l.counts,
                            "overall": l.overall,
                            # Weighted mode only (null otherwise): summed
                            # weights per level; "counts" stays the raw
                            # head-count in both modes.
                            "weighted_counts": l.wcounts,
                            "weighted_overall": l.woverall}
                           for l in row.levels],
                "weighted_denom_per_group": row.wdenom_per_group,
                "weighted_overall_denom": row.woverall_denom,
                "p_value": _finite(row.pvalue),
                "p_adjusted": _finite(row.p_adjusted), "test": row.test_name,
                "smd": _finite(row.smd), "effect": effect(row.effect),
                "p_trend": _finite(row.trend_p), "trend_test": row.trend_test,
                "n_missing": row.n_missing_total, "notes": row.notes,
            })
    obj = {
        "groups": t.groups, "group_sizes": t.group_sizes,
        "overall_size": t.overall_size, "rows": rows,
        "warnings": t.warnings, "meta": t.meta,
    }
    # allow_nan=False guarantees strictly valid JSON; _finite already nulled
    # every non-finite value so this never raises.
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)


# --------------------------------------------------------------------------- #
# HTML  (self-contained <table>, ready to paste into Word / a journal system)
# --------------------------------------------------------------------------- #
def _h(s: str) -> str:
    """HTML-escape a data string (quotes included)."""
    return html.escape(str(s), quote=True)


def _md_inline_html(s: str) -> str:
    """Escape a legend/note string, then turn **bold** markdown into <strong>."""
    esc = html.escape(s, quote=False)
    parts = esc.split("**")
    # Odd-indexed segments are the emphasized ones (paired **...**).
    out = []
    for i, seg in enumerate(parts):
        out.append(f"<strong>{seg}</strong>" if i % 2 == 1 else seg)
    return "".join(out)


def _render_html(t: Table1, opt: Options) -> str:
    L = _lang(opt)
    two_group, show_p, show_effect, show_padj, single_group = _col_flags(t, opt)
    show_overall = True if single_group else opt.overall
    show_groups = not single_group
    # No test is run under weighting, so the 검정/Test column is dropped too.
    show_test = not single_group and not bool(t.meta.get("weighted"))
    show_trend = _show_trend(t)

    def value_tail(row, with_level: bool = True) -> List[str]:
        tail: List[str] = []
        if show_effect:
            pre = _effect_index_prefix(row.effect, _h) if with_level else ""
            tail.append(pre + _h(_fmt_effect(row.effect, opt.decimals,
                                             opt.pct_decimals)))
        if show_p:
            tail.append(_h(_fmt_p(row.pvalue)))
        if show_trend:
            tail.append(_h(_fmt_p(getattr(row, "trend_p", None))))
        if show_padj:
            tail.append(_h(_fmt_p(row.p_adjusted)))
        if two_group:
            tail.append(_h(_fmt_smd(row.smd)))
        if show_test:
            tail.append(_h(row.test_name))
        return tail

    def blank_tail() -> List[str]:
        n = (int(show_effect) + int(show_p) + int(show_trend) + int(show_padj)
             + int(two_group) + int(show_test))
        return [""] * n

    note_markers: List[str] = []

    def note_suffix(notes: List[str]) -> str:
        if not notes:
            return ""
        idx = len(note_markers) + 1
        note_markers.append("; ".join(notes))
        return f"<sup>{idx}</sup>"

    def miss_suffix(n_missing: int, per_group=None) -> str:
        if not n_missing:
            return ""
        base = f" · {L['missing']} {n_missing}"
        if per_group is not None and len(t.groups) >= 2:
            parts = [f"{g} {m}" for g, m in zip(t.groups, per_group) if m]
            if parts:
                base += " (" + ", ".join(parts) + ")"
        return base

    headers = [L["char"]]
    if show_overall:
        headers.append(f"{L['overall']} (N={t.overall_size}"
                       f"{_ess_suffix(t, None, opt)})")
    if show_groups:
        for gi, (g, n) in enumerate(zip(t.groups, t.group_sizes)):
            headers.append(f"{g} (n={n}{_ess_suffix(t, gi, opt)})")
    if show_effect:
        headers.append(L["effect"])
    if show_p:
        headers.append(L["p"])
    if show_trend:
        headers.append(L["p_trend"])
    if show_padj:
        headers.append(L["p_adj"])
    if two_group:
        headers.append(L["smd"])
    if show_test:
        headers.append(L["test"])

    out: List[str] = ['<table class="table1">']
    out.append(f"  <caption>{_h(L['title'])}</caption>")
    out.append("  <thead>")
    out.append("    <tr>" + "".join(f"<th scope=\"col\">{_h(h)}</th>"
                                    for h in headers) + "</tr>")
    out.append("  </thead>")
    out.append("  <tbody>")

    def emit(row_th: str, data_cells: List[str], is_level: bool = False) -> None:
        cls = ' class="level"' if is_level else ""
        tds = "".join(f"<td>{c}</td>" for c in data_cells)
        out.append(f"    <tr{cls}><th scope=\"row\">{row_th}</th>{tds}</tr>")

    for row in t.rows:
        if isinstance(row, ContinuousRow):
            per_group_missing = [st.n_missing for st in row.per_group]
            label = (_h(_disp_name(opt, row.name) + _cont_suffix(row, L)
                        + miss_suffix(row.n_missing_total, per_group_missing))
                     + note_suffix(row.notes))
            cells = []
            if show_overall:
                cells.append(_h(_cont_cell(row.overall, row.display,
                                           opt.decimals, opt.show_range)))
            if show_groups:
                for st in row.per_group:
                    cells.append(_h(_cont_cell(st, row.display, opt.decimals,
                                               opt.show_range)))
            cells.extend(value_tail(row))
            emit(label, cells)
        else:
            pd = opt.pct_decimals
            single = _binary_collapse(row, opt)
            if single is not None:
                label = (_h(_disp_name(opt, row.name) + " = " + single.label
                            + f" — {L['n_pct']}"
                            + miss_suffix(row.n_missing_total,
                                          row.missing_per_group))
                         + note_suffix(row.notes))
                cells = []
                if show_overall:
                    cells.append(_h(_cat_cell_for(row, single, None, opt)))
                if show_groups:
                    for gi, c in enumerate(single.counts):
                        cells.append(_h(_cat_cell_for(row, single, gi, opt)))
                cells.extend(value_tail(row, with_level=False))
                emit(label, cells)
                continue
            header_label = (_h(_disp_name(opt, row.name) + f" — {L['n_pct']}"
                               + miss_suffix(row.n_missing_total,
                                             row.missing_per_group))
                            + note_suffix(row.notes))
            cells = []
            if show_overall:
                cells.append("")
            if show_groups:
                cells.extend([""] * len(t.groups))
            cells.extend(value_tail(row))
            emit(header_label, cells)
            for lvl in row.levels:
                lcells = []
                if show_overall:
                    lcells.append(_h(_cat_cell_for(row, lvl, None, opt)))
                if show_groups:
                    for gi, c in enumerate(lvl.counts):
                        lcells.append(_h(_cat_cell_for(row, lvl, gi, opt)))
                lcells.extend(blank_tail())
                # &#160; (numeric NBSP) keeps the output valid XHTML/XML too.
                emit("&#160;" + _h(lvl.label), lcells, is_level=True)

    out.append("  </tbody>")
    out.append("</table>")

    # Legend / notes / warnings mirror the markdown output.
    if opt.pct == "col":
        incl_missing = (getattr(opt, "missing_as_level", False) and
                        any(isinstance(r, CategoricalRow) and r.n_missing_total > 0
                            for r in t.rows))
        pct_desc = L["pct_col_incl_missing"] if incl_missing else L["pct_col"]
    else:
        pct_desc = L["pct_row"]
    legend = [L["leg_notation"] + pct_desc + "."]
    if t.meta.get("weighted"):
        # State the weighting up front: every number above it is a weighted
        # (pseudo-population) quantity, which a reader must not mistake for a
        # raw count.
        legend.append(L["leg_weighted"].format(col=t.meta.get("weight_col")))
    if show_effect:
        legend.append(L["leg_effect"])
    if show_p:
        legend.append(_leg_p_for(L, opt))
    if show_trend:
        legend.append(_leg_trend(L, t))
    if show_padj:
        legend.append(_leg_padj(L, t, opt))
    if two_group:
        legend.append(L["leg_smd"])
    for line in legend:
        out.append(f'<p class="legend">{_md_inline_html(line)}</p>')
    if note_markers:
        out.append('<ol class="notes">')
        for n in note_markers:
            out.append(f"  <li>{_h(n)}</li>")
        out.append("</ol>")
    if t.warnings:
        out.append('<ul class="warnings">')
        for w in t.warnings:
            out.append(f"  <li>{_h(w)}</li>")
        out.append("</ul>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# LaTeX  (booktabs table, ready to \input into a manuscript)
# --------------------------------------------------------------------------- #
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    # Not TeX specials, but under the default OT1 encoding "<" and ">" typeset
    # as inverted punctuation -- so a censored lab value ">100" would silently
    # print as "?100" in the manuscript.
    "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}",
}


# Typographic glyphs this tool emits (chi-square, en/em dashes, plus-minus...)
# have no pdfLaTeX representation under the default OT1 font encoding, so they
# are rewritten to math/ligature equivalents AFTER escaping (the replacements
# contain backslashes and $ that must not be escaped again). Longest sequence
# first so "chi squared" wins over the two single glyphs.
_TEX_GLYPHS = [
    ("χ²", r"$\chi^2$"),   # chi-square
    ("χ", r"$\chi$"), ("²", r"$^2$"),
    ("—", "---"), ("–", "--"),
    ("±", r"$\pm$"), ("·", r"$\cdot$"),
    ("≥", r"$\ge$"), ("≤", r"$\le$"), ("≠", r"$\ne$"),
    ("→", r"$\rightarrow$"), ("∞", r"$\infty$"),
    ("×", r"$\times$"), ("−", "-"),
]


def _tex(s: str) -> str:
    r"""Escape a DATA string for LaTeX.

    Every one of TeX's ten special characters is neutralized, so a variable
    literally named ``dose_mg`` or a level ``>50%`` cannot break the build (or,
    worse, silently typeset as a subscript). Newlines become spaces because a
    table cell is a single paragraph. Non-ASCII typographic glyphs are then
    mapped to math equivalents; any remaining non-ASCII text (Korean labels
    under ``--lang ko``) needs a Unicode engine, which the README states.
    """
    out = []
    for ch in str(s).replace("\r", " ").replace("\n", " "):
        out.append(_TEX_ESCAPES.get(ch, ch))
    text = "".join(out)
    for glyph, repl in _TEX_GLYPHS:
        if glyph in text:
            text = text.replace(glyph, repl)
    return text


def _tex_inline(s: str) -> str:
    """Escape a legend/note line and turn **bold** markdown into \\textbf."""
    parts = _tex(s).split("**")
    return "".join(f"\\textbf{{{seg}}}" if i % 2 else seg
                   for i, seg in enumerate(parts))


def _render_latex(t: Table1, opt: Options) -> str:
    L = _lang(opt)
    two_group, show_p, show_effect, show_padj, single_group = _col_flags(t, opt)
    show_overall = True if single_group else opt.overall
    show_groups = not single_group
    show_test = not single_group and not bool(t.meta.get("weighted"))
    show_trend = _show_trend(t)

    def value_tail(row, with_level: bool = True) -> List[str]:
        tail: List[str] = []
        if show_effect:
            pre = _effect_index_prefix(row.effect, _tex) if with_level else ""
            tail.append(pre + _tex(_fmt_effect(row.effect, opt.decimals,
                                               opt.pct_decimals)))
        if show_p:
            tail.append(_tex(_fmt_p(row.pvalue)))
        if show_trend:
            tail.append(_tex(_fmt_p(getattr(row, "trend_p", None))))
        if show_padj:
            tail.append(_tex(_fmt_p(row.p_adjusted)))
        if two_group:
            tail.append(_tex(_fmt_smd(row.smd)))
        if show_test:
            tail.append(_tex(row.test_name))
        return tail

    def blank_tail() -> List[str]:
        n = (int(show_effect) + int(show_p) + int(show_trend) + int(show_padj)
             + int(two_group) + int(show_test))
        return [""] * n

    note_markers: List[str] = []

    def note_suffix(notes: List[str]) -> str:
        if not notes:
            return ""
        note_markers.append("; ".join(notes))
        return f"\\textsuperscript{{{len(note_markers)}}}"

    def miss_suffix(n_missing: int, per_group=None) -> str:
        if not n_missing:
            return ""
        base = f" $\\cdot$ {_tex(L['missing'])} {n_missing}"
        if per_group is not None and len(t.groups) >= 2:
            parts = [f"{_tex(g)} {m}"
                     for g, m in zip(t.groups, per_group) if m]
            if parts:
                base += " (" + ", ".join(parts) + ")"
        return base

    headers = [_tex(L["char"])]
    if show_overall:
        headers.append(_tex(f"{L['overall']} (N={t.overall_size}"
                            f"{_ess_suffix(t, None, opt)})"))
    if show_groups:
        for gi, (g, n) in enumerate(zip(t.groups, t.group_sizes)):
            headers.append(_tex(f"{g} (n={n}{_ess_suffix(t, gi, opt)})"))
    if show_effect:
        headers.append(_tex(L["effect"]))
    if show_p:
        headers.append(_tex(L["p"]))
    if show_trend:
        headers.append(_tex(L["p_trend"]))
    if show_padj:
        headers.append(_tex(L["p_adj"]))
    if two_group:
        headers.append(_tex(L["smd"]))
    if show_test:
        headers.append(_tex(L["test"]))

    # First column left-aligned (long variable labels), the rest right-aligned
    # so decimal points line up the way a typeset table should.
    colspec = "l" + "r" * (len(headers) - 1)

    body: List[str] = []

    def emit(cells: List[str]) -> None:
        body.append("  " + " & ".join(cells) + r" \\")

    for row in t.rows:
        if isinstance(row, ContinuousRow):
            per_group_missing = [st.n_missing for st in row.per_group]
            label = (_tex(_disp_name(opt, row.name) + _cont_suffix(row, L))
                     + miss_suffix(row.n_missing_total, per_group_missing)
                     + note_suffix(row.notes))
            cells = [label]
            if show_overall:
                cells.append(_tex(_cont_cell(row.overall, row.display,
                                             opt.decimals, opt.show_range)))
            if show_groups:
                for st in row.per_group:
                    cells.append(_tex(_cont_cell(st, row.display, opt.decimals,
                                                 opt.show_range)))
            cells.extend(value_tail(row))
            emit(cells)
        else:
            single = _binary_collapse(row, opt)
            if single is not None:
                label = (_tex(_disp_name(opt, row.name) + " = " + single.label
                              + f" — {L['n_pct']}")
                         + miss_suffix(row.n_missing_total,
                                       row.missing_per_group)
                         + note_suffix(row.notes))
                cells = [label]
                if show_overall:
                    cells.append(_tex(_cat_cell_for(row, single, None, opt)))
                if show_groups:
                    for gi, _c in enumerate(single.counts):
                        cells.append(_tex(_cat_cell_for(row, single, gi, opt)))
                cells.extend(value_tail(row, with_level=False))
                emit(cells)
                continue
            header_label = (_tex(_disp_name(opt, row.name) + f" — {L['n_pct']}")
                            + miss_suffix(row.n_missing_total,
                                          row.missing_per_group)
                            + note_suffix(row.notes))
            cells = [header_label]
            if show_overall:
                cells.append("")
            if show_groups:
                cells.extend([""] * len(t.groups))
            cells.extend(value_tail(row))
            emit(cells)
            for lvl in row.levels:
                # \quad indents the level under its variable, the way the
                # markdown output uses a leading space.
                lcells = [r"\quad " + _tex(lvl.label)]
                if show_overall:
                    lcells.append(_tex(_cat_cell_for(row, lvl, None, opt)))
                if show_groups:
                    for gi, _c in enumerate(lvl.counts):
                        lcells.append(_tex(_cat_cell_for(row, lvl, gi, opt)))
                lcells.extend(blank_tail())
                emit(lcells)

    if opt.pct == "col":
        incl_missing = (getattr(opt, "missing_as_level", False) and
                        any(isinstance(r, CategoricalRow) and r.n_missing_total > 0
                            for r in t.rows))
        pct_desc = L["pct_col_incl_missing"] if incl_missing else L["pct_col"]
    else:
        pct_desc = L["pct_row"]
    legend = [L["leg_notation"] + pct_desc + "."]
    if t.meta.get("weighted"):
        legend.append(L["leg_weighted"].format(col=t.meta.get("weight_col")))
    if show_effect:
        legend.append(L["leg_effect"])
    if show_p:
        legend.append(_leg_p_for(L, opt))
    if show_trend:
        legend.append(_leg_trend(L, t))
    if show_padj:
        legend.append(_leg_padj(L, t, opt))
    if two_group:
        legend.append(L["leg_smd"])

    out: List[str] = []
    out.append("% requires \\usepackage{booktabs}")
    out.append(r"\begin{table}[htbp]")
    out.append(r"  \centering")
    out.append(f"  \\caption{{{_tex(L['title'])}}}")
    out.append(f"  \\begin{{tabular}}{{{colspec}}}")
    out.append(r"  \toprule")
    out.append("  " + " & ".join(headers) + r" \\")
    out.append(r"  \midrule")
    out.extend(body)
    out.append(r"  \bottomrule")
    out.append(r"  \end{tabular}")
    # Notes go in a fixed-width parbox so long legends wrap to the table width
    # instead of running off the page.
    foot: List[str] = [_tex_inline(line) for line in legend]
    for i, n in enumerate(note_markers, 1):
        foot.append(f"\\textsuperscript{{{i}}} {_tex(n)}")
    for w in t.warnings:
        foot.append(f"{_tex(L['warn_hdr'].strip('*'))}: {_tex(w)}")
    if foot:
        out.append(r"  \begin{minipage}{\linewidth}\footnotesize")
        for line in foot:
            out.append("  " + line + r" \par")
        out.append(r"  \end{minipage}")
    out.append(r"\end{table}")
    return "\n".join(out)


def render(t: Table1, opt: Options, fmt: str = "md") -> str:
    if fmt == "md":
        return _render_markdown(t, opt)
    if fmt == "csv":
        return _render_delimited(t, opt, ",")
    if fmt == "tsv":
        return _render_delimited(t, opt, "\t")
    if fmt == "json":
        return _render_json(t, opt)
    if fmt == "html":
        return _render_html(t, opt)
    if fmt == "latex":
        return _render_latex(t, opt)
    raise ValueError(f"알 수 없는 출력 형식: {fmt}")
