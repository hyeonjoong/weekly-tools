"""Report rendering (Korean + English) for categorical rater agreement."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Dict, List

from .catanalyze import CategoricalResult
from .categorical import interpret_kappa

__all__ = ["render_cat_text", "render_cat_json", "render_cat_markdown"]


# --------------------------------------------------------------------------
# Display-width-aware padding — category labels and headers are often Korean,
# and CJK glyphs occupy two terminal columns, so str.ljust (which counts code
# points) visibly skews every table.
# --------------------------------------------------------------------------
def _w(s: str) -> int:
    """Terminal display width of *s* (East-Asian wide/fullwidth count 2)."""
    total = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _pad(s: str, width: int, right: bool = False) -> str:
    """Pad *s* to *width* display columns (left- or right-aligned)."""
    fill = " " * max(0, width - _w(s))
    return fill + s if right else s + fill


_MAX_LABEL_W = 24          # display columns before a category label is elided
_MAX_MATRIX_K = 25         # categories beyond which the matrix is unreadable


def _short(s: str, width: int = _MAX_LABEL_W) -> str:
    """Elide an over-long category label so one stray cell can't wreck a table."""
    if _w(s) <= width:
        return s
    out = ""
    for ch in s:
        if _w(out) + _w(ch) > width - 1:
            break
        out += ch
    return out + "…"


def _num(x: float, d: int = 3) -> str:
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "inf" if x > 0 else "-inf"
    return f"{x:.{d}f}"


def _p(p: float) -> str:
    if p != p:
        return "NaN"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _lvl(alpha: float) -> str:
    """Confidence-level label.

    Never round to a degenerate '0'/'100': at --alpha 1e-12 the label must not
    claim a "100% CI" — that would travel into the ready-to-paste sentence.
    """
    v = (1.0 - alpha) * 100.0
    if abs(v - round(v)) < 1e-9 and 0 < round(v) < 100:
        return str(int(round(v)))
    for prec in (4, 8, 12, 17):
        txt = f"{v:.{prec}g}"
        if float(txt) not in (0.0, 100.0):
            return txt
    return f"{v:.17g}"


def _ci(lo: float, hi: float, level: str, d: int = 3) -> str:
    return f"[{level}% CI {_num(lo, d)}, {_num(hi, d)}]"


def _grade(x: float) -> str:
    return interpret_kappa(x).split(" / ")[0]


def _matrix_block(res: CategoricalResult) -> List[str]:
    """Render the confusion matrix as a fixed-width table."""
    cm = res.cm
    if cm.k > _MAX_MATRIX_K:
        return [
            f"    (범주가 {cm.k}개라 교차표가 너무 커서 화면에 표시하지 "
            f"않습니다 — 전체 표는 --json 으로 확인하세요.)",
            f"    범주: {', '.join(_short(c, 12) for c in cm.categories[:12])}"
            f"{' …' if cm.k > 12 else ''}",
        ]
    cats = [_short(c) for c in cm.categories]
    head_a = _short(f"{res.name_a}\\{res.name_b}", 30)
    widths = [max(_w(head_a), _w("합계"), max((_w(c) for c in cats), default=1))]
    for j, c in enumerate(cats):
        col = [str(cm.counts[i][j]) for i in range(cm.k)] + [c, str(cm.col_totals[j])]
        widths.append(max(_w(s) for s in col))
    tot_w = max(_w("합계"), _w(str(cm.n)),
                max((_w(str(t)) for t in cm.row_totals), default=1))

    def rule() -> str:
        return ("    " + "-" * widths[0] + "-+-"
                + "-" * (sum(widths[1:]) + len(cats) - 1) + "-+-" + "-" * tot_w)

    def row(label: str, cells: List[int], total: int) -> str:
        return ("    " + _pad(label, widths[0]) + " | "
                + " ".join(_pad(str(v), widths[j + 1], right=True)
                           for j, v in enumerate(cells))
                + " | " + _pad(str(total), tot_w, right=True))

    lines: List[str] = []
    lines.append("    " + _pad(head_a, widths[0]) + " | "
                 + " ".join(_pad(c, widths[j + 1], right=True)
                            for j, c in enumerate(cats))
                 + " | " + _pad("합계", tot_w, right=True))
    lines.append(rule())
    for i, c in enumerate(cats):
        lines.append(row(c, [cm.counts[i][j] for j in range(cm.k)],
                         cm.row_totals[i]))
    lines.append(rule())
    lines.append(row("합계", list(cm.col_totals), cm.n))
    return lines


def render_cat_text(res: CategoricalResult) -> str:
    lines: List[str] = []
    L = lines.append
    lvl = _lvl(res.alpha)
    cm = res.cm

    L("=" * 70)
    L("  agreestat — 범주형 일치도 리포트 / Categorical agreement report")
    L("=" * 70)

    # [1] Data summary
    L("")
    L("[1] 데이터 요약 / Data summary")
    L(f"    paired n = {res.n}"
      + (f"  (제외 {res.dropped} rows)" if res.dropped else ""))
    L(f"    평가자 A = \"{res.name_a}\",  평가자 B = \"{res.name_b}\"")
    scale = "순서형 (ordinal)" if res.ordinal else "명목형 (nominal)"
    shown = [_short(c, 16) for c in cm.categories[:12]]
    more = f" … 외 {cm.k - 12}개" if cm.k > 12 else ""
    L(f"    척도 = {scale},  범주 {cm.k}개: {shown}{more}")

    # [2] Confusion matrix
    L("")
    L("[2] 교차표 / Confusion matrix (행=A, 열=B; 대각선=일치)")
    lines.extend(_matrix_block(res))
    L(f"    관찰 일치도 po = {_num(cm.po)}  ({sum(cm.counts[i][i] for i in range(cm.k))}/{cm.n})")

    # [3] Agreement coefficients
    L("")
    L("[3] 일치도 계수 / Agreement coefficients")
    prim = res.primary
    for k in (res.kappa, res.kappa_weighted, res.ac1, res.ac2):
        if k is None:
            continue
        tag = "  ← 보고 권장" if k is prim else ""
        # Landis & Koch grades were derived for kappa. Printing them beside AC1
        # / AC2 (whose chance correction differs) invites picking the flattering
        # number, so the grade is marked as a convention there.
        grade = (f"{k.interpretation}†" if k in (res.ac1, res.ac2)
                 else k.interpretation)
        L(f"    {k.statistic:<28s} = {_num(k.value)}  "
          f"{_ci(k.ci_lower, k.ci_upper, lvl)}  ({grade}){tag}")
    if res.ac1 is not None:
        L("    † AC1/AC2 등급은 kappa용 Landis & Koch 척도를 관례적으로 적용한 "
          "것으로, 근거가 약합니다.")
    L("    ※ kappa·AC1·PABAK은 우연 보정 방식이 달라 값이 다릅니다. 유리한 값만 "
      "고르지 말고,")
    L("      주 지표를 분석 전에 정해 명시하고 나머지는 함께 보고하세요.")
    if res.kappa.pvalue == res.kappa.pvalue:
        L(f"      └ H0: kappa=0 검정 → z={_num(res.kappa.z, 2)}, "
          f"p={_p(res.kappa.pvalue)}  (※ '우연보다 나은가'일 뿐, "
          "'충분히 일치하는가'가 아닙니다)")
    L(f"    Scott's pi                   = {_num(res.scott_pi)}")
    L(f"    Krippendorff's alpha ({res.krippendorff_metric:<7s}) = "
      f"{_num(res.krippendorff)}")
    L(f"    po (weighted) = {_num(prim.po)}, pe (chance) = {_num(prim.pe)}"
      f"  → {prim.statistic} = (po−pe)/(1−pe)")
    L("    해석 기준(Landis & Koch 1977): <0.21 미미 / 0.21–0.40 약함 / "
      "0.41–0.60 보통 /")
    L("                                   0.61–0.80 상당함 / >0.80 거의 완벽")
    if prim.ci_lower == prim.ci_lower:
        L(f"    ⚑ 보수적 판단: 점추정({_num(prim.value)}, '{_grade(prim.value)}')이 "
          f"아니라 CI 하한({_num(prim.ci_lower)}, '{_grade(prim.ci_lower)}') 기준 "
          "권장")
    if prim.note:
        L(f"    ※ {prim.note}")

    # [3b] Cluster-robust CIs (only when subjects repeat)
    cl = res.cluster
    if cl is not None and cl.available:
        L("")
        L("[3b] 군집 보정 신뢰구간 / Cluster-robust CI (피험자 재표집 부트스트랩)"
          " — 권장")
        L(f"    피험자 {cl.n_subjects}명, 총 {cl.n_pairs}행 "
          f"(반복 있는 피험자 {cl.n_replicated_subjects}명), "
          f"재표본 {cl.replicates}회, seed={cl.seed}")
        L(f"    {prim.statistic} = {_num(cl.value)}  "
          f"군집 CI [{_num(cl.ci_lower)}, {_num(cl.ci_upper)}]  "
          f"(SE {_num(cl.se, 4)})")
        L(f"       naive CI [{_num(cl.naive_ci[0])}, {_num(cl.naive_ci[1])}]"
          f"  (SE {_num(cl.naive_se, 4)}) — 각 행을 독립으로 가정, 너무 좁음")
        if cl.design_effect == cl.design_effect:
            L(f"    설계효과 design effect = {_num(cl.design_effect, 2)}  →  "
              f"유효 표본수 ≈ {_num(cl.n_effective, 0)} (실제 {cl.n_pairs}행)")
        if res.cluster_ac1 is not None and res.cluster_ac1.available:
            c2 = res.cluster_ac1
            L(f"    Gwet's AC = {_num(c2.value)}  "
              f"군집 CI [{_num(c2.ci_lower)}, {_num(c2.ci_upper)}]")
        if cl.n_subject_estimates:
            L(f"    피험자별 {prim.statistic} 분포 (n={cl.n_subject_estimates}명): "
              f"중앙값 {_num(cl.subject_median)}, "
              f"IQR {_num(cl.subject_q1)}–{_num(cl.subject_q3)}, "
              f"범위 {_num(cl.subject_min)}–{_num(cl.subject_max)}")
        if cl.n_failed:
            L(f"    ※ 재표본 {cl.n_failed}회는 계수를 계산할 수 없어 제외했습니다.")
        if cl.note:
            L(f"    ⚠ {cl.note}")
        L("    ※ 같은 피험자의 여러 행은 서로 독립이 아니므로 위의 군집 CI를 "
          "보고하세요.")

    # [4] Paradox diagnostics
    par = res.paradox
    L("")
    L("[4] kappa 역설 진단 / Kappa-paradox diagnostics (Byrt 1993)")
    L(f"    관찰 일치도 po = {_num(par.po)}")
    if par.prevalence_index == par.prevalence_index:
        L(f"    유병률 지수 PI = {_num(par.prevalence_index)}  "
          "(클수록 한 범주 쏠림 → kappa를 끌어내림)")
        L(f"    편향 지수   BI = {_num(par.bias_index)}  "
          "(클수록 두 평가자의 범주 사용 빈도 차이가 큼)")
    L(f"    PABAK (유병률·편향 보정 kappa) = {_num(par.pabak)}  "
      f"({_grade(par.pabak)})")
    if par.max_kappa == par.max_kappa:
        L(f"    이 주변분포에서 가능한 최대 kappa = {_num(par.max_kappa)}")
    L(f"    Gwet's AC1 = {_num(res.ac1.value)} — 쏠린 자료에서 kappa보다 안정적")
    if par.paradox:
        L("    ⚠ 역설 감지: po는 높은데 kappa는 낮습니다 → AC1/PABAK을 함께 "
          "보고하세요.")

    # [5] Per-category agreement
    L("")
    L("[5] 범주별 일치도 / Per-category agreement")
    if cm.k == 2:
        L("    (2x2: 특이적 일치도 = FDA의 PPA/NPA와 동일)")
    headers = ["범주", "A사용", "B사용", "둘다", "특이적일치도",
               f"[{lvl}% CI]", "one-vs-rest κ"]
    table: List[List[str]] = []
    for pc in res.per_category:
        ci_txt = (f"[{_num(pc.sa_ci[0], 2)}, {_num(pc.sa_ci[1], 2)}]"
                  if pc.sa_ci[0] == pc.sa_ci[0] else "—")
        table.append([_short(pc.category), str(pc.n_a), str(pc.n_b),
                      str(pc.n_both), _num(pc.specific_agreement), ci_txt,
                      _num(pc.kappa_ovr)])
    colw = [max(_w(headers[j]), max((_w(r[j]) for r in table), default=0))
            for j in range(len(headers))]
    L("    " + _pad(headers[0], colw[0]) + " "
      + " ".join(_pad(h, colw[j + 1], right=True)
                 for j, h in enumerate(headers[1:])))
    for r in table:
        L("    " + _pad(r[0], colw[0]) + " "
          + " ".join(_pad(v, colw[j + 1], right=True)
                     for j, v in enumerate(r[1:])))
    L("    ※ 특이적 일치도 = 2·n_ii/(A사용+B사용): 한 평가자가 그 범주를 썼을 때 "
      "다른 평가자도 썼을 확률.")
    L("      CI는 두 평가자의 사용을 독립 시행으로 본 Wilson 근사(참고용)입니다.")

    # [6] Marginal homogeneity
    m = res.marginal
    L("")
    L("[6] 주변 동질성 검정 / Marginal homogeneity "
      "(한쪽이 특정 범주를 더 자주 쓰는가?)")
    if m.available:
        if m.statistic == m.statistic:
            L(f"    {m.name}: 통계량={_num(m.statistic)}, df={m.df}, "
              f"p={_p(m.pvalue)}")
        else:
            L(f"    {m.name}: p={_p(m.pvalue)}"
              + (f"  (불일치 셀 b={m.b}, c={m.c})" if cm.k == 2 else ""))
        if m.pvalue == m.pvalue:
            verdict = ("주변분포 다름 ⚠ (계통 편향 있음)" if m.pvalue < res.alpha
                       else "주변분포 차이 근거 없음")
            L(f"    → {verdict}")
        if m.note:
            L(f"    ※ {m.note}")
    else:
        L(f"    (건너뜀: {m.note})")

    # Warnings
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")

    L("")
    L("[논문용 문장 / Ready-to-paste sentence]")
    L("  " + _cat_sentence(res))
    L("")
    return "\n".join(lines)


def _cat_sentence(res: CategoricalResult) -> str:
    lvl = _lvl(res.alpha)
    prim = res.primary
    cm = res.cm
    cl = res.cluster
    clustered = cl is not None and cl.available
    # The sentence must quote the CI the verdict was judged on, not the naive
    # one — otherwise the paper inherits an interval that is too narrow.
    ci_lo, ci_hi = res.decision_ci
    parts: List[str] = []
    kind = ("가중 kappa(quadratic)" if res.weights == "quadratic"
            else "가중 kappa(linear)" if res.weights == "linear"
            else "Cohen의 kappa")
    scope = (f"피험자 {cl.n_subjects}명의 {cm.n}쌍" if clustered else f"{cm.n}쌍")
    parts.append(
        f"'{res.name_a}'와 '{res.name_b}'의 범주 일치도를 {cm.k}개 범주 "
        f"{scope}에 대해 평가한 결과, 관찰 일치도는 {_num(cm.po * 100, 1)}%였고 "
        f"{kind}는 {_num(prim.value, 3)}"
        + (f"({lvl}% CI {_num(ci_lo, 3)}~{_num(ci_hi, 3)})"
           if ci_lo == ci_lo else "")
        + "였다.")
    if clustered:
        parts.append(
            f" 동일 피험자에서 반복 측정된 자료이므로 신뢰구간은 피험자 단위 "
            f"군집 부트스트랩({cl.replicates}회 재표집)으로 산출하였다.")
    if ci_lo == ci_lo:
        parts.append(
            f" Landis와 Koch(1977) 기준으로 신뢰구간 하한"
            f"({_num(ci_lo, 3)}) 기준 '{_grade(ci_lo)}' "
            "수준이었다.")
    if res.paradox.paradox:
        parts.append(
            f" 다만 한 범주의 쏠림으로 인해 kappa가 관찰 일치도에 비해 낮게 "
            f"나타나(kappa 역설), 유병률에 강건한 Gwet의 AC1"
            f"({_num(res.ac1.value, 3)})과 PABAK({_num(res.paradox.pabak, 3)})을 "
            "함께 보고한다.")
    m = res.marginal
    if m.available and m.pvalue == m.pvalue and m.pvalue < res.alpha:
        parts.append(
            f" {m.name} 검정 결과 두 평가자의 주변분포가 유의하게 달라"
            f"(p={_p(m.pvalue)}) 범주 사용에 계통적 차이가 있었다.")
    if res.meets_threshold is not None:
        if res.meets_threshold:
            parts.append(
                f" 신뢰구간 하한이 사전 설정한 기준({_num(res.min_kappa, 2)})을 "
                "넘어 일치도가 허용 수준을 충족하였다.")
        else:
            parts.append(
                f" 신뢰구간 하한이 사전 설정한 기준({_num(res.min_kappa, 2)})에 "
                "미치지 못하여 일치도가 충분하다고 보기 어렵다.")
    return "".join(parts)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------
def _f(x: float) -> Any:
    if x is None:
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _kappa_json(k) -> Any:
    if k is None:
        return None
    out: Dict[str, Any] = {
        "statistic": k.statistic,
        "value": _f(k.value),
        "se": _f(k.se),
        "ci": [_f(k.ci_lower), _f(k.ci_upper)],
        "po": _f(k.po),
        "pe": _f(k.pe),
        "interpretation": k.interpretation,
        "note": k.note,
    }
    if k.z == k.z:
        out["z"] = _f(k.z)
        out["pvalue"] = _f(k.pvalue)
    if k.max_kappa == k.max_kappa:
        out["max_kappa_given_margins"] = _f(k.max_kappa)
    return out


def _cluster_json(cl, cl_ac) -> Any:
    if cl is None:
        return {"available": False, "note": "no subject column supplied"}
    if not cl.available:
        return {"available": False, "note": cl.note,
                "n_subjects": cl.n_subjects, "n_pairs": cl.n_pairs}
    out: Dict[str, Any] = {
        "available": True,
        "method": "percentile bootstrap, resampling subjects (clusters)",
        "n_subjects": cl.n_subjects,
        "n_pairs": cl.n_pairs,
        "n_replicated_subjects": cl.n_replicated_subjects,
        "replicates": cl.replicates,
        "seed": cl.seed,
        "n_failed_replicates": cl.n_failed,
        "statistic": cl.statistic,
        "value": _f(cl.value),
        "se": _f(cl.se),
        "ci": [_f(cl.ci_lower), _f(cl.ci_upper)],
        "naive_se": _f(cl.naive_se),
        "naive_ci": [_f(cl.naive_ci[0]), _f(cl.naive_ci[1])],
        "design_effect": _f(cl.design_effect),
        "n_effective": _f(cl.n_effective),
        "note": cl.note,
    }
    if cl.n_subject_estimates:
        out["per_subject"] = {
            "n": cl.n_subject_estimates,
            "median": _f(cl.subject_median),
            "q1": _f(cl.subject_q1),
            "q3": _f(cl.subject_q3),
            "min": _f(cl.subject_min),
            "max": _f(cl.subject_max),
        }
    if cl_ac is not None and cl_ac.available:
        out["gwet_ac"] = {
            "value": _f(cl_ac.value),
            "ci": [_f(cl_ac.ci_lower), _f(cl_ac.ci_upper)],
            "se": _f(cl_ac.se),
        }
    return out


def render_cat_json(res: CategoricalResult) -> str:
    cm = res.cm
    d: Dict[str, Any] = {
        "analysis": "categorical",
        "rater_a": res.name_a,
        "rater_b": res.name_b,
        "n": res.n,
        "dropped": res.dropped,
        "alpha": res.alpha,
        "scale": "ordinal" if res.ordinal else "nominal",
        "categories": cm.categories,
        "confusion_matrix": {
            "counts": cm.counts,
            "row_totals_a": cm.row_totals,
            "col_totals_b": cm.col_totals,
            "observed_agreement": _f(cm.po),
        },
        "coefficients": {
            "cohens_kappa": _kappa_json(res.kappa),
            "weighted_kappa": _kappa_json(res.kappa_weighted),
            "weights": res.weights,
            "gwet_ac1": _kappa_json(res.ac1),
            "gwet_ac2": _kappa_json(res.ac2),
            "scott_pi": _f(res.scott_pi),
            "krippendorff_alpha": _f(res.krippendorff),
            "krippendorff_metric": res.krippendorff_metric,
            "headline": res.primary.statistic,
        },
        "paradox_diagnostics": {
            "observed_agreement": _f(res.paradox.po),
            "prevalence_index": _f(res.paradox.prevalence_index),
            "bias_index": _f(res.paradox.bias_index),
            "pabak": _f(res.paradox.pabak),
            "max_kappa_given_margins": _f(res.paradox.max_kappa),
            "paradox_detected": res.paradox.paradox,
        },
        "per_category": [
            {
                "category": pc.category,
                "n_used_by_a": pc.n_a,
                "n_used_by_b": pc.n_b,
                "n_both": pc.n_both,
                "specific_agreement": _f(pc.specific_agreement),
                "specific_agreement_ci": [_f(pc.sa_ci[0]), _f(pc.sa_ci[1])],
                "kappa_one_vs_rest": _f(pc.kappa_ovr),
                "kappa_one_vs_rest_ci": [_f(pc.kappa_ovr_ci[0]),
                                         _f(pc.kappa_ovr_ci[1])],
            }
            for pc in res.per_category
        ],
        "cluster_bootstrap": _cluster_json(res.cluster, res.cluster_ac1),
        "marginal_homogeneity": {
            "available": res.marginal.available,
            "test": res.marginal.name,
            "statistic": _f(res.marginal.statistic),
            "df": res.marginal.df,
            "pvalue": _f(res.marginal.pvalue),
            "note": res.marginal.note,
        },
        "acceptance": {
            "min_kappa": _f(res.min_kappa) if res.min_kappa is not None else None,
            "judged_on": ("cluster bootstrap CI lower bound"
                          if (res.cluster is not None and res.cluster.available)
                          else "CI lower bound"),
            "ci_used": [_f(res.decision_ci[0]), _f(res.decision_ci[1])],
            "meets_threshold": res.meets_threshold,
        },
        "warnings": res.warnings,
    }
    return json.dumps(d, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def _mdcell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_cat_markdown(res: CategoricalResult) -> str:
    cm = res.cm
    lvl = _lvl(res.alpha)

    def ci(lo, hi):
        if lo != lo or hi != hi:
            return "—"
        return f"{_num(lo, 3)} to {_num(hi, 3)}"

    out: List[str] = []
    na, nb = _mdcell(res.name_a), _mdcell(res.name_b)
    out.append(f"# agreestat — {na} vs {nb} (categorical agreement)")
    out.append("")
    out.append(f"paired n = {cm.n}"
               + (f" (dropped {res.dropped})" if res.dropped else "")
               + f" · {'ordinal' if res.ordinal else 'nominal'}, {cm.k} categories"
               + f" · CI level = {lvl}%")
    out.append("")

    # Confusion matrix
    out.append(f"## Confusion matrix (rows = {na}, columns = {nb})")
    out.append("")
    out.append("| | " + " | ".join(_mdcell(c) for c in cm.categories) + " | Total |")
    out.append("|---" * (cm.k + 2) + "|")
    for i, c in enumerate(cm.categories):
        out.append(f"| **{_mdcell(c)}** | "
                   + " | ".join(str(cm.counts[i][j]) for j in range(cm.k))
                   + f" | {cm.row_totals[i]} |")
    out.append("| **Total** | "
               + " | ".join(str(t) for t in cm.col_totals) + f" | {cm.n} |")
    out.append("")

    # Coefficients
    out.append("## Agreement coefficients")
    out.append("")
    out.append(f"| Statistic | Estimate | {lvl}% CI | Grade / Note |")
    out.append("|---|---|---|---|")
    out.append(f"| Observed agreement (po) | {_num(cm.po, 3)} | — | "
               f"{sum(cm.counts[i][i] for i in range(cm.k))}/{cm.n} agreed |")
    for k in (res.kappa, res.kappa_weighted, res.ac1, res.ac2):
        if k is None:
            continue
        note = _grade(k.value)
        if k is res.primary:
            note += " (headline)"
        if k.ci_lower == k.ci_lower:
            note += f"; CI-lower: {_grade(k.ci_lower)}"
        out.append(f"| {k.statistic} | {_num(k.value, 3)} | "
                   f"{ci(k.ci_lower, k.ci_upper)} | {note} |")
    out.append(f"| Scott's pi | {_num(res.scott_pi, 3)} | — | context |")
    out.append(f"| Krippendorff's alpha ({res.krippendorff_metric}) | "
               f"{_num(res.krippendorff, 3)} | — | context |")
    out.append(f"| PABAK | {_num(res.paradox.pabak, 3)} | — | "
               "prevalence/bias-adjusted |")
    if res.paradox.prevalence_index == res.paradox.prevalence_index:
        out.append(f"| Prevalence index | {_num(res.paradox.prevalence_index, 3)} "
                   "| — | 2x2 only |")
        out.append(f"| Bias index | {_num(res.paradox.bias_index, 3)} | — | "
                   "2x2 only |")
    if res.paradox.max_kappa == res.paradox.max_kappa:
        out.append(f"| Max kappa given margins | {_num(res.paradox.max_kappa, 3)} "
                   "| — | marginal imbalance |")
    m = res.marginal
    if m.available and m.pvalue == m.pvalue:
        out.append(f"| {m.name} | {_num(m.statistic, 3)} | — | "
                   f"p={_p(m.pvalue)} "
                   f"({'margins differ' if m.pvalue < res.alpha else 'no evidence'}) |")
    cl = res.cluster
    if cl is not None and cl.available:
        out.append(f"| **{res.primary.statistic} (cluster bootstrap)** | "
                   f"{_num(cl.value, 3)} | {ci(cl.ci_lower, cl.ci_upper)} | "
                   f"**recommended**: {cl.n_subjects} subjects, "
                   f"{cl.replicates} resamples, seed {cl.seed} |")
        out.append(f"| Design effect | {_num(cl.design_effect, 2)} | — | "
                   f"effective n ≈ {_num(cl.n_effective, 0)} of {cl.n_pairs} rows |")
        if cl.n_subject_estimates:
            out.append(f"| Per-subject {res.primary.statistic} | "
                       f"median {_num(cl.subject_median, 3)} | "
                       f"IQR {_num(cl.subject_q1, 3)}–{_num(cl.subject_q3, 3)} | "
                       f"range {_num(cl.subject_min, 3)}–"
                       f"{_num(cl.subject_max, 3)} (n={cl.n_subject_estimates}) |")
    if res.meets_threshold is not None:
        judged = ("cluster bootstrap CI lower bound"
                  if (cl is not None and cl.available) else "CI lower bound")
        out.append(f"| Acceptance (κ ≥ {_num(res.min_kappa, 2)}) | "
                   + ("**met**" if res.meets_threshold else "**not met**")
                   + f" | — | judged on {judged} |")
    out.append("")

    # Per-category
    out.append("## Per-category agreement")
    out.append("")
    out.append(f"| Category | Used by {na} | Used by {nb} | Both | "
               f"Specific agreement | {lvl}% CI | One-vs-rest κ |")
    out.append("|---|---|---|---|---|---|---|")
    for pc in res.per_category:
        out.append(f"| {_mdcell(pc.category)} | {pc.n_a} | {pc.n_b} | "
                   f"{pc.n_both} | {_num(pc.specific_agreement, 3)} | "
                   f"{ci(*pc.sa_ci)} | {_num(pc.kappa_ovr, 3)} |")

    # Warnings must travel with the table: markdown is the paste-into-the-paper
    # path, and these are exactly the caveats a reader needs (kappa paradox,
    # assumed category order, clustering, failed acceptance).
    if res.warnings:
        out.append("")
        out.append("## 주의 / Warnings")
        out.append("")
        for w in res.warnings:
            out.append(f"- {_mdcell(w)}")
    return "\n".join(out) + "\n"
