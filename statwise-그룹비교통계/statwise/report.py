"""Human-readable report rendering (Korean + English) for an AnalysisResult."""

from __future__ import annotations

import csv
import io
import json
import math
import unicodedata
from typing import Any, Dict, List

from .analyze import AnalysisResult
from .ancova import AncovaResult
from .binary import BinaryResult
from .endpoints import CORRECTION_LABELS
from .mcnemar import PairedBinaryResult

__all__ = ["render_text", "render_json", "result_to_dict",
           "render_binary_text", "render_binary_json",
           "binary_to_dict", "binary_sentence",
           "render_mcnemar_text", "render_mcnemar_json", "mcnemar_to_dict",
           "mcnemar_sentence",
           "render_multi_text", "render_multi_json", "multi_to_dict",
           "render_csv", "render_multi_csv",
           "render_ancova_text", "render_ancova_json", "ancova_to_dict"]


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
    # no discordant pairs: p = 1.000 is "cannot judge", not "no difference",
    # and the sentence would state the reading the warning forbids
    "판단 불가",
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
                "paired": "paired t",
                "ancova": "ANCOVA (공변량 보정 최소제곱)"}


def _two_labels(res) -> List[str]:
    """The compared labels, whichever result type this is."""
    groups = getattr(res, "groups", None)
    if groups:
        return [g.label for g in groups]
    return list(getattr(res, "group_labels", []))


def _render_equivalence(res, L, heading: str = "3b",
                        estimate: str = "평균차") -> None:
    """Section [3b]: the TOST / non-inferiority verdict, if one was requested."""
    eq = res.equivalence
    if eq is None:
        return
    direction = ""
    labels = _two_labels(res)
    if len(labels) == 2:
        direction = f" ({labels[0]} − {labels[1]})"
    model = _MODEL_LABEL.get(eq.model, eq.model)
    L("")
    if eq.kind == "tost":
        L(f"[{heading}] 등가성 검정 / Equivalence (TOST)")
        L(f"     등가 마진 margin: [{_num(eq.margin_low)}, {_num(eq.margin_high)}]"
          f"  ({estimate}{direction} 기준)")
    else:
        arrow = ("높을수록 좋음 (higher is better)"
                 if eq.direction == "higher_is_better"
                 else "낮을수록 좋음 (lower is better)")
        bound = eq.margin_low if eq.direction == "higher_is_better" else eq.margin_high
        L(f"[{heading}] 비열등성 검정 / Non-inferiority")
        L(f"     비열등성 마진 margin: {_num(abs(bound))}  ({estimate}{direction} 기준, {arrow})")
        side = "하한" if eq.direction == "higher_is_better" else "상한"
        rel = ">" if eq.direction == "higher_is_better" else "<"
        L(f"     → 기각 기준: {int(round(eq.conf * 100))}% 단측 신뢰{side} {rel} "
          f"{_num(bound)}  (점추정값이 아니라 신뢰한계로 판정합니다)")
    L(f"     {estimate} diff = {_num(eq.diff)}, SE = {_num(eq.se)}, "
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
# paired binary (McNemar)
# ==========================================================================

def _dwidth(text: str) -> int:
    """Terminal columns ``text`` occupies (CJK characters take two).

    The matched-pair table is the one place a *user-supplied* label sits inside
    a grid next to Korean header text, and ``len()`` counts '사건' as 2 where a
    terminal draws 4 — enough to shear every column of the table apart.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _dpad(text: str, width: int, right: bool = False) -> str:
    pad = " " * max(0, width - _dwidth(text))
    return (pad + text) if right else (text + pad)


def _mcnemar_estimate_line(est, conf_pct: int) -> str:
    if est.name.startswith("Risk difference"):
        shown = _pct(est.value)
        ci = ("" if est.ci_low is None else
              f"  [{conf_pct}% CI {_pct(est.ci_low)}, {_pct(est.ci_high)}]")
    elif "NNT" in est.name or est.name.startswith("Number needed"):
        shown = _ratio(est.value, 1)
        ci = ("" if est.ci_low is None else
              f"  [{conf_pct}% CI {_ratio(est.ci_low, 1)}, "
              f"{_ratio(est.ci_high, 1)}]")
    else:
        shown = _ratio(est.value)
        ci = ("" if est.ci_low is None else
              f"  [{conf_pct}% CI {_ratio(est.ci_low)}, {_ratio(est.ci_high)}]")
    mag = f"  ({est.magnitude})" if est.magnitude else ""
    return f"    {est.name} = {shown}{ci}{mag}"


def render_mcnemar_text(res: PairedBinaryResult) -> str:
    """Human-readable report for a paired binary (McNemar) comparison."""
    lines: List[str] = []
    L = lines.append
    t = res.table
    conf_pct = int(round((1.0 - res.alpha) * 100))
    a = _elide(t.label_a, 22)
    b = _elide(t.label_b, 22)

    L("=" * 66)
    L("  statwise — 대응 이진 결과 리포트 / Paired binary (McNemar) report")
    L("=" * 66)
    L("")
    L("[1] 짝지어진 2x2 표 / Matched-pair table")
    L(f"    (행 = {a}, 열 = {b}; 같은 대상 {t.n}쌍)")
    rows = [("", f"{b}: 사건", f"{b}: 비사건", "합계"),
            (f"{a}: 사건", t.both, t.a_only, t.events_a),
            (f"{a}: 비사건", t.b_only, t.neither, t.n - t.events_a),
            ("합계", t.events_b, t.n - t.events_b, t.n)]
    w0 = max(_dwidth(str(r[0])) for r in rows) + 2
    cw = [max(_dwidth(str(r[j])) for r in rows) + 3 for j in (1, 2, 3)]
    for r in rows:
        L("    " + _dpad(str(r[0]), w0)
          + "".join(_dpad(str(r[j + 1]), cw[j], right=True) for j in range(3)))
    L("")
    L(f"    {a} 사건률 = {t.events_a}/{t.n} ({_pct(t.prop_a)}), "
      f"{b} 사건률 = {t.events_b}/{t.n} ({_pct(t.prop_b)})")
    L(f"    불일치(discordant) 쌍 = {t.n_discordant}개 "
      f"({a}만 {t.a_only}, {b}만 {t.b_only}) — 검정은 이 쌍들만 사용합니다.")
    if any(res.missing.values()):
        miss = ", ".join(f"{k}={v}" for k, v in res.missing.items() if v)
        L(f"    (제외된 관측치: {miss} — 짝이 없거나 값이 결측/해석 불가)")

    L("")
    L("[2] 선택된 검정 / Selected test")
    L(f"    → {res.test_name}")
    L(f"      (근거: {res.reason})")
    if res.statistic is not None and res.statistic == res.statistic:
        L(f"      χ²={_num(res.statistic)}, df={_fmt_df(res.df)}, "
          f"p={_fmt_p(res.pvalue)}")
    else:
        L(f"      p={_fmt_p(res.pvalue)}")
    sig = "통계적으로 유의함" if res.significant else "유의하지 않음"
    adj = res.pvalue_adj
    if adj is not None and adj != res.pvalue:
        cmp = '<' if adj == adj and adj < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} "
          f"(보정 전 p={_fmt_p(res.pvalue)}, 보정 후 "
          f"p(adj)={_fmt_p(adj)} {cmp} {res.alpha})")
    else:
        cmp = '<' if res.pvalue == res.pvalue and res.pvalue < res.alpha else '≥'
        L(f"      유의수준 α={res.alpha}: {sig} (p{cmp}{res.alpha})")

    if res.estimates:
        L("")
        L("[3] 효과 크기 / Effect measures")
        for est in res.estimates:
            L(_mcnemar_estimate_line(est, conf_pct))
            if est.method:
                L(f"      방법: {est.method}")
            if est.note:
                L(f"      주: {est.note}")
        L(f"    (기준 reference = {b}: 위험차 = p({a}) − p({b}))")
        L("    (조건부 오즈비는 **불일치 쌍만**의 비율입니다 — 두 사건률의 "
          "주변부 오즈비와 다르며 그렇게 인용하면 안 됩니다.)")

    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for warn in res.warnings:
            L(f"    - {warn}")

    _render_sentence(res, L, mcnemar_sentence(res) + _adjusted_phrase(res))
    L("")
    return "\n".join(lines)


def mcnemar_sentence(res: PairedBinaryResult) -> str:
    """APA-ish sentence for a paired binary comparison."""
    t = res.table
    adjusted = (res.pvalue_adj is not None and res.pvalue_adj != res.pvalue)
    label = "unadjusted p" if adjusted else "p"
    psign = (f"{label} < 0.001" if res.pvalue == res.pvalue
             and res.pvalue < 0.001 else f"{label} = {res.pvalue:.3f}")
    conf_pct = int(round((1.0 - res.alpha) * 100))
    sig = ("statistically significant" if res.significant
           else "not statistically significant")
    stat = ("" if res.statistic is None or res.statistic != res.statistic
            else f"χ²({_fmt_df(res.df)}) = {_num(res.statistic, 2)}, ")
    test = ("an exact McNemar test" if res.method == "exact"
            else "a continuity-corrected McNemar test"
            if "continuity" in res.test_name else "McNemar's test")
    out = (f"Among {t.n} matched pairs the event rate was "
           f"{t.events_a}/{t.n} ({_pct(t.prop_a)}) under {t.label_a} versus "
           f"{t.events_b}/{t.n} ({_pct(t.prop_b)}) under {t.label_b} "
           f"({t.a_only} and {t.b_only} discordant pairs); the change was "
           f"{sig} by {test} ({stat}{psign}).")
    rd = next((e for e in res.estimates
               if e.name.startswith("Risk difference")), None)
    if rd is not None and rd.ci_low is not None:
        out += (f" Paired risk difference {_pct(rd.value)} "
                f"({conf_pct}% CI {_pct(rd.ci_low)} to {_pct(rd.ci_high)}).")
        # Without this the sentence states a non-significant test and an
        # interval excluding zero side by side, and a reader picks whichever
        # half suits them. The conflict is real and has to be in the sentence,
        # not only in the warnings block above it.
        excludes_zero = not (rd.ci_low <= 0.0 <= rd.ci_high)
        if excludes_zero != res.significant:
            out += (" Note that the test and the interval disagree here: the "
                    "test is exact (conservative) while the score interval is "
                    "asymptotic, so this is a borderline result.")
    return out


def mcnemar_to_dict(res: PairedBinaryResult) -> Dict[str, Any]:
    """Serialize a PairedBinaryResult into a plain JSON-friendly dict."""
    t = res.table
    out: Dict[str, Any] = {
        "schema": "statwise/paired-binary/1",
        "alpha": res.alpha,
        "design": "paired",
        "conditions": {"a": t.label_a, "b": t.label_b},
        "table": {
            "n_pairs": t.n, "both": t.both, "a_only": t.a_only,
            "b_only": t.b_only, "neither": t.neither,
            "n_discordant": t.n_discordant,
            "events_a": t.events_a, "events_b": t.events_b,
            "proportion_a": _jnum(t.prop_a), "proportion_b": _jnum(t.prop_b),
        },
        "missing": {k: int(v) for k, v in res.missing.items()},
        "test": {
            "name": res.test_name,
            "statistic": _jnum(res.statistic),
            "df": _jnum(res.df),
            "pvalue": _jnum(res.pvalue),
            "pvalue_adj": _jnum(res.pvalue_adj),
            "method": res.method,
            "significant": bool(res.significant),
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
    out["sentence"] = mcnemar_sentence(res)
    return out


def render_mcnemar_json(res: PairedBinaryResult, indent: int = 2) -> str:
    return json.dumps(mcnemar_to_dict(res), indent=indent, ensure_ascii=False)


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


def _mcnemar_rows(res: PairedBinaryResult, endpoint: str = "") -> List[List[str]]:
    t = res.table
    comparison = f"{t.label_a} vs {t.label_b} (paired)"
    n_all = (f"{t.label_a}={t.events_a}/{t.n}|{t.label_b}={t.events_b}/{t.n}"
             f"|discordant={t.a_only}/{t.b_only}")
    rows: List[List[str]] = []
    first = res.estimates[0] if res.estimates else None
    rows.append([endpoint, "paired-binary", comparison, res.test_name,
                 str(t.n), str(t.n), n_all,
                 first.name if first else "",
                 _csv_num(first.value if first else None),
                 _csv_num(first.ci_low if first else None),
                 _csv_num(first.ci_high if first else None),
                 repr(first.conf) if first and first.ci_low is not None else "",
                 _csv_num(res.statistic), _csv_num(res.df),
                 _csv_num(res.pvalue), _csv_num(res.pvalue_adj),
                 "yes" if res.significant else "no",
                 "significant change" if res.significant
                 else "no significant change"])
    for extra in res.estimates[1:]:
        rows.append([endpoint, "paired-binary-effect", comparison,
                     res.test_name, str(t.n), str(t.n), n_all, extra.name,
                     _csv_num(extra.value), _csv_num(extra.ci_low),
                     _csv_num(extra.ci_high),
                     repr(extra.conf) if extra.ci_low is not None else "",
                     "", "", "", "", "", ""])
    return rows


def _ancova_rows(res, endpoint: str = "") -> List[List[str]]:
    """Adjusted-mean rows, the omnibus row, and one row per adjusted contrast."""
    rows: List[List[str]] = []
    labels = res.group_labels
    comparison = " vs ".join(labels) if len(labels) <= 3 else \
        f"{len(labels)} groups"
    n_all = "|".join(f"{a.label}={a.n}" for a in res.adjusted_means)
    conf = repr(1.0 - res.alpha)
    model = "ANCOVA (" + ", ".join(res.covariate_names + res.factor_names) + ")"
    two = len(labels) == 2
    rows.append([endpoint, "ancova", comparison, model,
                 str(res.adjusted_means[0].n) if two else "",
                 str(res.adjusted_means[1].n) if two else "",
                 n_all, "group effect (partial eta-squared)",
                 _csv_num(res.partial_eta_sq), "", "", "",
                 _csv_num(res.f_statistic),
                 f"{_csv_num(res.df1)}|{_csv_num(res.df2)}",
                 _csv_num(res.pvalue), "",
                 "yes" if res.significant else "no",
                 "significant difference" if res.significant
                 else "no significant difference"])
    for a in res.adjusted_means:
        rows.append([endpoint, "adjusted-mean", a.label, model, str(a.n), "",
                     n_all, "adjusted (LS) mean", _csv_num(a.adjusted),
                     _csv_num(a.ci[0]), _csv_num(a.ci[1]), conf, "",
                     _csv_num(res.df2), "", "", "", ""])
    for c in res.contrasts:
        rows.append([endpoint, "adjusted-contrast", f"{c.a} vs {c.b}", model,
                     str(c.n_a), str(c.n_b), n_all,
                     f"adjusted mean difference ({c.a} − {c.b})",
                     _csv_num(c.diff), _csv_num(c.ci[0]), _csv_num(c.ci[1]),
                     conf, _csv_num(c.diff / c.se if c.se else float("nan")),
                     _csv_num(c.df), _csv_num(c.pvalue_raw),
                     _csv_num(c.pvalue_adj),
                     "yes" if c.significant else "no",
                     "significant difference" if c.significant
                     else "no significant difference"])
    for e in res.covariate_effects:
        rows.append([endpoint, "covariate", e.name, model, "", "", n_all,
                     "slope" if e.kind == "numeric" else "factor level effect",
                     _csv_num(e.coef), _csv_num(e.ci[0]), _csv_num(e.ci[1]),
                     conf, _csv_num(e.t), _csv_num(res.df2),
                     _csv_num(e.pvalue), "", "", ""])
    if res.equivalence is not None:
        eq = res.equivalence
        rows.append([endpoint, eq.kind, comparison, model + " + margin",
                     str(res.adjusted_means[0].n),
                     str(res.adjusted_means[1].n) if len(labels) > 1 else "",
                     n_all, "adjusted mean difference", _csv_num(eq.diff),
                     _csv_num(eq.ci_low), _csv_num(eq.ci_high), repr(eq.conf),
                     "", _csv_num(eq.df), _csv_num(eq.pvalue), "",
                     "yes" if eq.concluded else "no",
                     ("equivalence concluded" if eq.kind == "tost"
                      else "non-inferiority concluded") if eq.concluded
                     else ("equivalence not concluded" if eq.kind == "tost"
                           else "non-inferiority not concluded")])
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
    if isinstance(res, PairedBinaryResult):
        return _rows_to_csv(_mcnemar_rows(res))
    if isinstance(res, AncovaResult):
        return _rows_to_csv(_ancova_rows(res))
    return _rows_to_csv(_analysis_rows(res))


# --------------------------------------------------------------------------
# ANCOVA rendering
# --------------------------------------------------------------------------

def _p_phrase(p: float, label: str = "p") -> str:
    """"p < 0.001" / "p = 0.031" — the form a manuscript sentence needs."""
    if p != p:
        return f"{label} = NaN"
    return f"{label} < 0.001" if p < 0.001 else f"{label} = {p:.3f}"


def _ancova_sentence(res) -> str:
    """A paste-ready Methods+Results sentence for the adjusted comparison."""
    terms = []
    if res.covariate_names:
        terms.append(", ".join(res.covariate_names) + "을(를) 공변량으로")
    if res.factor_names:
        terms.append(", ".join(res.factor_names) + "을(를) 보정인자로")
    conf = int(round((1.0 - res.alpha) * 100))
    parts = [
        f"{res.outcome}은(는) {', '.join(terms)} 포함한 공분산분석"
        f"(ANCOVA)으로 비교하였다."]
    if len(res.adjusted_means) == 2 and res.contrasts:
        c = res.contrasts[0]
        a = next(m for m in res.adjusted_means if m.label == c.a)
        b = next(m for m in res.adjusted_means if m.label == c.b)
        parts.append(
            f"보정평균은 {a.label} {_num(a.adjusted)} "
            f"({conf}% CI {_num(a.ci[0])}–{_num(a.ci[1])}), "
            f"{b.label} {_num(b.adjusted)} "
            f"({conf}% CI {_num(b.ci[0])}–{_num(b.ci[1])})였고, "
            f"보정된 평균차({c.a} − {c.b})는 {_num(c.diff)} "
            f"({conf}% CI {_num(c.ci[0])}–{_num(c.ci[1])}), "
            f"{_p_phrase(c.pvalue_raw)}였다.")
    else:
        parts.append(
            f"그룹 효과는 F({_fmt_df(res.df1)}, {_fmt_df(res.df2)})="
            f"{_num(res.f_statistic)}, {_p_phrase(res.pvalue)}, "
            f"부분 η²={_num(res.partial_eta_sq)}였다 "
            f"(기준군 {res.reference}, 쌍별 비교는 "
            f"{CORRECTION_LABELS.get(res.correction, res.correction)} 보정).")
    verdict = ("유의한 차이가 있었다" if res.significant
               else "유의한 차이는 관찰되지 않았다")
    parts.append(f"유의수준 {res.alpha}에서 {verdict}.")
    if res.slopes is not None and res.slopes.homogeneous is False:
        parts.append(
            "다만 기울기 동질성 가정이 기각되어(그룹×공변량 상호작용 "
            f"{_p_phrase(res.slopes.pvalue)}) 하나의 보정된 차이로 요약하는 데는 "
            "한계가 있다.")
    return " ".join(parts)


def render_ancova_text(res) -> str:
    lines: List[str] = []
    L = lines.append
    L("=" * 66)
    L("  statwise — 공변량 보정 그룹 비교 (ANCOVA) / Adjusted comparison")
    L("=" * 66)

    w = max(16, min(30, max((len(a.label) for a in res.adjusted_means),
                            default=0) + 1))
    conf = int(round((1.0 - res.alpha) * 100))

    # Size the numeric columns to their own content. Fixed 9/11-wide columns
    # fused every number into one digit string as soon as a value reached ~1e6,
    # which is ordinary for viral load (copies/mL), KRW amounts and raw counts --
    # the report is the deliverable, so an unreadable table is a real defect.
    def _cw(values, minimum: int) -> int:
        widest = max((len(_num(v)) for v in values
                      if v == v and math.isfinite(v)), default=0)
        return max(minimum, min(_MAX_NUM_WIDTH + 4, widest + 2))

    def _ciw(pairs) -> int:
        widest = max((len(f"[{_num(lo)}, {_num(hi)}]") for lo, hi in pairs),
                     default=0)
        return max(23, min(2 * _MAX_NUM_WIDTH + 8, widest + 2))

    L("")
    L("[1] 모형 / Model")
    L(f"    결과변수 outcome : {res.outcome}")
    L(f"    공변량 covariates: "
      f"{', '.join(res.covariate_names) if res.covariate_names else '(없음)'}")
    L(f"    보정인자 factors : "
      f"{', '.join(res.factor_names) if res.factor_names else '(없음)'}")
    if len(res.adjusted_means) > 2:
        L(f"    기준군 reference : {res.reference}  "
          f"[기준군 대비 차이 = (다른 군 − {res.reference})]")
        L(f"      ↳ 아래 [4]에는 기준군을 포함하지 않는 쌍도 **모두** 나오며, "
          f"다중비교 보정은 그 전체 가족에 적용됩니다.")
    else:
        L(f"    기준군 reference : {res.reference}  "
          f"[차이 = (다른 군 − {res.reference})]")
    L(f"    분석 n = {res.n_used}"
      + (f" (결측으로 제외 {res.n_dropped}행)" if res.n_dropped else ""))
    if any(res.missing.values()):
        detail = ", ".join(f"{g or '(군 없음)'}={c}"
                           for g, c in res.missing.items() if c)
        L(f"    군별 제외 행수 excluded rows: {detail}")
    L(f"    잔차 표준편차 σ = {_num(res.sigma)}, "
      f"R²={_num(res.r_squared)}, 수정 R²={_num(res.adj_r_squared)}")

    L("")
    L("[2] 보정평균 / Adjusted (LS) means")
    mw = _cw([a.raw_mean for a in res.adjusted_means]
             + [a.adjusted for a in res.adjusted_means], 11)
    sw = _cw([a.se for a in res.adjusted_means], 9)
    cw = _ciw([a.ci for a in res.adjusted_means])
    vw = _cw([v for a in res.adjusted_means for v in a.covariate_means], 11)
    cov_head = "".join(f"{_elide(c, vw - 1):>{vw}}" for c in res.covariate_names)
    L(f"    {'group':<{w}}{'n':>5}{'raw mean':>{mw}}{'adjusted':>{mw}}"
      f"{'SE':>{sw}}{f'{conf}% CI':>{cw}}{cov_head}")
    for a in res.adjusted_means:
        ci = f"[{_num(a.ci[0])}, {_num(a.ci[1])}]"
        covs = "".join(f"{_num(v, 2):>{vw}}" for v in a.covariate_means)
        L(f"    {_elide(a.label, w):<{w}}{a.n:>5}{_num(a.raw_mean):>{mw}}"
          f"{_num(a.adjusted):>{mw}}{_num(a.se):>{sw}}{ci:>{cw}}{covs}")
    if res.covariate_names:
        L(f"    (마지막 열들 = 그룹별 공변량 평균 — 기저 균형을 눈으로 확인하세요)")

    L("")
    L("[3] 그룹 효과 / Omnibus test of the group term")
    L(f"    F({_fmt_df(res.df1)}, {_fmt_df(res.df2)})={_num(res.f_statistic)}, "
      f"p={_fmt_p(res.pvalue)}, 부분 η²={_num(res.partial_eta_sq)}")
    sig = "통계적으로 유의함" if res.significant else "유의하지 않음"
    L(f"    유의수준 α={res.alpha}: {sig}")

    L("")
    L("[4] 보정된 그룹 차이 / Adjusted differences")
    label = CORRECTION_LABELS.get(res.correction, res.correction)
    multi = len(res.contrasts) > 1
    dw = _cw([c.diff for c in res.contrasts], 11)
    sw2 = _cw([c.se for c in res.contrasts], 9)
    cw2 = _ciw([c.ci for c in res.contrasts])
    head = (f"    {'comparison':<{2 * w + 4}}{'차이':>{dw}}{'SE':>{sw2}}"
            f"{f'{conf}% CI':>{cw2}}")
    head += f"{'p':>9}" + (f"{'p(adj)':>9}" if multi else "")
    L(head)
    for c in res.contrasts:
        ci = f"[{_num(c.ci[0])}, {_num(c.ci[1])}]"
        star = " *" if c.significant else ""
        line = (f"    {_elide(c.a + ' − ' + c.b, 2 * w + 3):<{2 * w + 4}}"
                f"{_num(c.diff):>{dw}}{_num(c.se):>{sw2}}{ci:>{cw2}}"
                f"{_fmt_p(c.pvalue_raw):>9}")
        if multi:
            line += f"{_fmt_p(c.pvalue_adj):>9}"
        L(line + star)
    if multi:
        L(f"    (* = {label} 보정 후 α={res.alpha}에서 유의. 신뢰구간은 "
          f"**비교 1건 기준(비보정)** 이므로 별표와 결론이 다를 수 있습니다.)")

    if res.covariate_effects:
        L("")
        L("[5] 공변량 효과 / Covariate & factor effects")
        tw = max(20, min(40, max(len(e.name) for e in res.covariate_effects) + 1))
        bw = _cw([e.coef for e in res.covariate_effects], 11)
        sw3 = _cw([e.se for e in res.covariate_effects], 9)
        cw3 = _ciw([e.ci for e in res.covariate_effects])
        L(f"    {'term':<{tw}}{'coef':>{bw}}{'SE':>{sw3}}"
          f"{f'{conf}% CI':>{cw3}}{'t':>9}{'p':>9}")
        for e in res.covariate_effects:
            ci = f"[{_num(e.ci[0])}, {_num(e.ci[1])}]"
            L(f"    {_elide(e.name, tw):<{tw}}"
              f"{_num(e.coef):>{bw}}{_num(e.se):>{sw3}}{ci:>{cw3}}"
              f"{_num(e.t):>9}{_fmt_p(e.pvalue):>9}")

    L("")
    L("[6] 가정 점검 / Assumption checks")
    s = res.slopes
    if s is not None and s.pvalue is not None:
        verdict = ("기울기 동질성 위배 근거 없음" if s.homogeneous
                   else "기울기 동질성 위배 의심")
        L(f"    기울기 동질성(그룹×공변량): F({_fmt_df(s.df1)}, "
          f"{_fmt_df(s.df2)})={_num(s.statistic)}, p={_fmt_p(s.pvalue)}"
          f"  → {verdict}")
    elif s is not None:
        L(f"    기울기 동질성: (건너뜀) {s.note}")
    if res.resid_normal_p is not None:
        verdict = ("정규성 위배 근거 없음" if res.resid_normal_p > res.alpha_norm
                   else "정규성 위배 의심")
        L(f"    잔차 정규성 Shapiro-Wilk: p={_fmt_p(res.resid_normal_p)}"
          f"  → {verdict}")
    else:
        L("    잔차 정규성: (건너뜀 — n<3 또는 n>5000)")
    if res.resid_levene_p is not None and res.resid_levene_p == res.resid_levene_p:
        verdict = ("등분산 위배 근거 없음" if res.resid_levene_p > res.alpha_norm
                   else "등분산 위배 의심")
        L(f"    잔차 등분산 Levene(median): p={_fmt_p(res.resid_levene_p)}"
          f"  → {verdict}")

    _render_equivalence(res, L, heading="7", estimate="보정된 평균차")

    _render_warnings(res, L)
    _render_sentence(res, L, _ancova_sentence(res))
    L("")
    return "\n".join(lines)


def ancova_to_dict(res) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema": "statwise/ancova/1",
        "analysis": "ancova",
        "outcome": res.outcome,
        "covariates": list(res.covariate_names),
        "factors": list(res.factor_names),
        "reference": res.reference,
        "alpha": res.alpha,
        "alpha_norm": res.alpha_norm,
        "n_used": res.n_used,
        "n_dropped": res.n_dropped,
        "model": {
            "sigma": _jnum(res.sigma),
            "r_squared": _jnum(res.r_squared),
            "adj_r_squared": _jnum(res.adj_r_squared),
            "df_resid": _jnum(res.df2),
        },
        "group_effect": {
            "f": _jnum(res.f_statistic),
            "df1": _jnum(res.df1),
            "df2": _jnum(res.df2),
            "pvalue": _jnum(res.pvalue),
            "partial_eta_squared": _jnum(res.partial_eta_sq),
            "significant": bool(res.significant),
        },
        "adjusted_means": [
            {"label": a.label, "n": a.n, "raw_mean": _jnum(a.raw_mean),
             "adjusted": _jnum(a.adjusted), "se": _jnum(a.se),
             "ci_low": _jnum(a.ci[0]), "ci_high": _jnum(a.ci[1]),
             "covariate_means": [_jnum(v) for v in a.covariate_means]}
            for a in res.adjusted_means],
        "contrasts": [
            {"a": c.a, "b": c.b, "difference": _jnum(c.diff), "se": _jnum(c.se),
             "df": _jnum(c.df), "ci_low": _jnum(c.ci[0]),
             "ci_high": _jnum(c.ci[1]), "pvalue": _jnum(c.pvalue_raw),
             "pvalue_adj": _jnum(c.pvalue_adj),
             "significant": bool(c.significant), "n_a": c.n_a, "n_b": c.n_b}
            for c in res.contrasts],
        "covariate_effects": [
            {"name": e.name, "kind": e.kind, "coef": _jnum(e.coef),
             "se": _jnum(e.se), "t": _jnum(e.t), "pvalue": _jnum(e.pvalue),
             "ci_low": _jnum(e.ci[0]), "ci_high": _jnum(e.ci[1])}
            for e in res.covariate_effects],
        "assumptions": {
            "slope_homogeneity": (
                None if res.slopes is None else {
                    "f": _jnum(res.slopes.statistic),
                    "df1": _jnum(res.slopes.df1),
                    "df2": _jnum(res.slopes.df2),
                    "pvalue": _jnum(res.slopes.pvalue),
                    "homogeneous": res.slopes.homogeneous,
                    "note": res.slopes.note}),
            "residual_normality_p": _jnum(res.resid_normal_p),
            "residual_levene_p": _jnum(res.resid_levene_p),
        },
        "correction": res.correction,
        "missing": dict(res.missing),
        "warnings": list(res.warnings),
        "sentence": _ancova_sentence(res),
    }
    if res.equivalence is not None:
        eq = res.equivalence
        out["equivalence"] = {
            "kind": eq.kind, "difference": _jnum(eq.diff),
            "ci_low": _jnum(eq.ci_low), "ci_high": _jnum(eq.ci_high),
            "conf": _jnum(eq.conf), "pvalue": _jnum(eq.pvalue),
            "df": _jnum(eq.df), "concluded": bool(eq.concluded),
            "margin_low": _jnum(eq.margin_low),
            "margin_high": _jnum(eq.margin_high),
            "direction": eq.direction, "model": eq.model}
    return out


def render_ancova_json(res, indent: int = 2) -> str:
    return json.dumps(ancova_to_dict(res), ensure_ascii=False, indent=indent,
                      allow_nan=False)


def render_multi_csv(multi) -> str:
    rows: List[List[str]] = []
    for run in multi.analysed:
        rows.extend(_binary_rows(run.result, run.name) if multi.binary
                    else _analysis_rows(run.result, run.name))
    return _rows_to_csv(rows)
