"""Text / JSON / Markdown rendering for the 3+-rater analyses."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .agreement import interpret_icc
from .categorical import interpret_kappa
from .multirater import MultiCategorical, MultiContinuous

__all__ = ["render_multi_text", "render_multi_json", "render_multi_markdown",
           "render_multicat_text", "render_multicat_json",
           "render_multicat_markdown"]


_LABEL_W = 40


def _lab(text: str) -> str:
    """Category/rater label, truncated the same way the text report does.

    Keeps a stray free-text column out of the JSON/Markdown files a researcher
    attaches to a manuscript.
    """
    return text if len(text) <= _LABEL_W else text[:_LABEL_W] + "…"


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
    v = (1.0 - alpha) * 100.0
    if abs(v - round(v)) < 1e-9 and 0 < round(v) < 100:
        return str(int(round(v)))
    for prec in (4, 8, 12, 17):
        txt = f"{v:.{prec}g}"
        if float(txt) not in (0.0, 100.0):
            return txt
    return f"{v:.17g}"


def _ci(lo: float, hi: float, lvl: str, d: int = 3) -> str:
    return f"[{lvl}% CI {_num(lo, d)}, {_num(hi, d)}]"


def _rng(lo: float, hi: float, d: int = 3) -> str:
    """Interval for prose. Uses '~' so negative bounds stay readable."""
    return f"{_num(lo, d)}~{_num(hi, d)}"


def _ci_phrase(lo: float, hi: float, lvl: str, d: int = 3) -> str:
    """`(95% CI a~b)` — or an explicit 'not computable' when the CI is NaN.

    Never paste 'NaN–NaN' into a manuscript sentence.
    """
    if lo != lo or hi != hi:
        return "(신뢰구간 계산 불가)"
    return f"({lvl}% CI {_rng(lo, hi, d)})"


def _ci_phrase_en(lo: float, hi: float, lvl: str, d: int = 3) -> str:
    if lo != lo or hi != hi:
        return "(CI not estimable)"
    return f"({lvl}% CI {_rng(lo, hi, d)})"


# ==========================================================================
# Continuous
# ==========================================================================
def render_multi_text(res: MultiContinuous) -> str:
    lines: List[str] = []
    L = lines.append
    lvl = _lvl(res.alpha)
    sec = [0]

    def nxt() -> int:
        sec[0] += 1
        return sec[0]

    L("=" * 70)
    L("  agreestat — 다중 평가자 일치도 / Multi-rater agreement (continuous)")
    L("=" * 70)
    L("")
    L(f"[{nxt()}] 데이터 요약 / Data summary")
    L(f"    피험자 n = {res.n},  평가자/방법 k = {res.k}"
      + (f"  (불완전 {res.dropped}행 제외)" if res.dropped else ""))
    L("")
    L(f"    {'rater':<20} {'mean':>10} {'sd':>10} {'bias*':>10}")
    L(f"    {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10}")
    for d in res.descriptives:
        L(f"    {d.name[:20]:<20} {_num(d.mean):>10} {_num(d.sd):>10} "
          f"{_num(d.bias):>10}")
    L("    * bias = 해당 평가자 값 − 피험자별 평균 (계통 편차)")

    fam = res.icc
    L("")
    L(f"[{nxt()}] 급내상관계수 ICC / Intraclass correlation")
    L(f"    {'model':<12} {'ICC':>8}  {'CI':<26} 해석")
    L(f"    {'-' * 12} {'-' * 8}  {'-' * 26} {'-' * 12}")
    for r in list(fam.single) + list(fam.average):
        L(f"    {r.model:<12} {_num(r.value):>8}  "
          f"{_ci(r.ci_lower, r.ci_upper, lvl):<26} {r.interpretation}")
    L("")
    L("    해석 기준(Koo & Li 2016): <0.5 낮음 / 0.5–0.75 보통 / "
      "0.75–0.9 좋음 / >0.9 매우 좋음")
    L("    — 등급은 점추정이 아니라 신뢰구간 하한으로 판단하세요.")
    L("")
    L("    ICC(1,·) 각 대상을 서로 다른 평가자가 봄(one-way random)")
    L("    ICC(2,·) 같은 평가자들이 모든 대상을 봄, 절대일치(two-way random)")
    L("    ICC(3,·) 이 평가자들만 관심, 일관성(two-way mixed)")
    L(f"    (·,1)=평가자 1명의 신뢰도 · (·,{res.k})={res.k}명 평균의 신뢰도")
    icc21 = fam.single[1]
    lo_grade = interpret_icc(icc21.ci_lower)
    if lo_grade != icc21.interpretation and icc21.ci_lower == icc21.ci_lower:
        L("")
        L(f"    ⚑ Koo & Li(2016) 권장: ICC(2,1)의 등급은 CI 하한 "
          f"{_num(icc21.ci_lower)} 기준으로 '{lo_grade}' 입니다 "
          f"(점추정 기준 '{icc21.interpretation}'). 보수적으로 보고하세요.")
    L("")
    L("    분산분석 평균제곱: MSR(대상)=" + _num(fam.ms.msr)
      + f", MSC(평가자)={_num(fam.ms.msc)}, MSE={_num(fam.ms.mse)}"
      + f", MSW={_num(fam.msw)}")
    L(f"    평가자 간 계통차이 검정: F({_num(fam.rater_df1, 0)}, "
      f"{_num(fam.rater_df2, 0)}) = {_num(fam.rater_f)}, p = {_p(fam.rater_p)}"
      + ("  → 유의한 평가자 편향 있음" if fam.rater_p == fam.rater_p
         and fam.rater_p < 0.05 else "  → 유의한 평가자 편향 없음"))

    L("")
    L(f"[{nxt()}] 측정오차 / Measurement error  (단위: 입력 자료와 동일)")
    L(f"    SEM  (절대일치 기준, √MSW)  = {_num(fam.sem)}"
      "   ← ICC(2,1)과 짝을 이루는 값")
    L(f"    SEM  (일관성 기준, √MSE)    = {_num(fam.sem_consistency)}"
      "   ← 평가자 간 계통차이를 제외")
    L(f"    MDC95 = 1.96·√2·SEM(절대일치) = {_num(fam.mdc95)}")
    L("        이보다 작은 변화는 측정오차와 구분할 수 없습니다. 추적관찰을 "
      "매번 같은 평가자가")
    L("        한다면 일관성 SEM 기반의 더 작은 값을 써도 되지만, 평가자가 "
      "바뀔 수 있으면 위 값을 쓰세요.")
    L(f"    s_w  (개체내 표준편차 √MSW) = {_num(fam.sw)}")
    L(f"    재현성 계수 2.77·s_w        = {_num(fam.rc)}")
    L("        ⚠ Bland–Altman의 '반복성(repeatability)' 계수는 같은 평가자의 "
      "반복측정을 뜻합니다.")
    L("        여기서는 서로 다른 평가자의 변동이므로 재현성"
      "(reproducibility, 평가자 간)입니다.")

    L("")
    L(f"[{nxt()}] 평가자 쌍별 일치도 / Pairwise agreement")
    L(f"    {'pair':<28} {'bias':>9} {'sd(diff)':>9} {f'LoA({lvl}%)':<24} "
      f"{'CCC':>7}")
    L(f"    {'-' * 28} {'-' * 9} {'-' * 9} {'-' * 24} {'-' * 7}")
    for pw in res.pairwise:
        pair = f"{pw.name_a[:12]} vs {pw.name_b[:12]}"
        loa = f"[{_num(pw.loa_lower, 2)}, {_num(pw.loa_upper, 2)}]"
        L(f"    {pair:<28} {_num(pw.mean_diff):>9} {_num(pw.sd_diff):>9} "
          f"{loa:<24} {_num(pw.ccc):>7}")

    if res.warnings:
        L("")
        L(f"[{nxt()}] 주의 / Warnings")
        for w in res.warnings:
            L(f"    ! {w}")

    icc2k = fam.average[1]
    grade = interpret_icc(icc21.ci_lower) if icc21.ci_lower == icc21.ci_lower \
        else icc21.interpretation
    L("")
    L(f"[{nxt()}] 논문용 문장 / Suggested sentence")
    L(f"    {res.k}명의 평가자가 {res.n}명을 평가한 자료에서 단일 평가자의 "
      f"급내상관계수는 ICC(2,1) = {_num(icc21.value)} "
      f"{_ci_phrase(icc21.ci_lower, icc21.ci_upper, lvl)}, "
      f"{res.k}명 평균의 ICC(2,{res.k}) = {_num(icc2k.value)} "
      f"{_ci_phrase(icc2k.ci_lower, icc2k.ci_upper, lvl)} 이었다"
      + ("" if icc21.ci_lower != icc21.ci_lower
         else f" (CI 하한 기준 신뢰도 등급: {grade})") + ". "
      f"측정의 표준오차(SEM, 절대일치)는 {_num(fam.sem)}, 최소 검출 가능 "
      f"변화(MDC95)는 {_num(fam.mdc95)} 였다.")
    L("")
    L(f"    Agreement among {res.k} raters on {res.n} subjects was "
      f"ICC(2,1) = {_num(icc21.value)} "
      f"{_ci_phrase_en(icc21.ci_lower, icc21.ci_upper, lvl)}"
      f" for a single rater and ICC(2,{res.k}) = "
      f"{_num(icc2k.value)} for the mean of {res.k} raters; SEM = "
      f"{_num(fam.sem)}, MDC95 = {_num(fam.mdc95)}.")
    L("")
    return "\n".join(lines)


def render_multi_json(res: MultiContinuous) -> str:
    fam = res.icc

    def icc_d(r) -> Dict[str, Any]:
        return {"model": r.model, "description": r.description,
                "value": r.value, "ci_lower": r.ci_lower,
                "ci_upper": r.ci_upper, "interpretation": r.interpretation}

    out: Dict[str, Any] = {
        "analysis": "multi_rater_continuous",
        "n_subjects": res.n, "n_raters": res.k, "raters": res.names,
        "alpha": res.alpha, "dropped_rows": res.dropped,
        "descriptives": [{"name": d.name, "mean": d.mean, "sd": d.sd,
                          "bias": d.bias} for d in res.descriptives],
        "icc": {"single": [icc_d(r) for r in fam.single],
                "average": [icc_d(r) for r in fam.average]},
        "anova": {"msr": fam.ms.msr, "msc": fam.ms.msc, "mse": fam.ms.mse,
                  "msw": fam.msw,
                  "rater_effect": {"f": fam.rater_f, "df1": fam.rater_df1,
                                   "df2": fam.rater_df2, "p": fam.rater_p}},
        "measurement_error": {"sem": fam.sem, "mdc95": fam.mdc95,
                              "within_subject_sd": fam.sw,
                              "repeatability_coefficient": fam.rc},
        "pairwise": [{"a": _lab(p.name_a), "b": _lab(p.name_b), "bias": p.mean_diff,
                      "sd_diff": p.sd_diff, "loa_lower": p.loa_lower,
                      "loa_upper": p.loa_upper, "ccc": p.ccc}
                     for p in res.pairwise],
        "warnings": res.warnings,
    }
    return json.dumps(out, ensure_ascii=False, indent=2, allow_nan=True)


def render_multi_markdown(res: MultiContinuous) -> str:
    lvl = _lvl(res.alpha)
    L: List[str] = []
    L.append(f"# 다중 평가자 일치도 (연속형) — n={res.n}, k={res.k}")
    L.append("")
    L.append("## ICC")
    L.append(f"| 모형 | ICC | {lvl}% CI | 해석 |")
    L.append("|---|---|---|---|")
    for r in list(res.icc.single) + list(res.icc.average):
        L.append(f"| {r.model} | {_num(r.value)} | "
                 f"{_num(r.ci_lower)}–{_num(r.ci_upper)} | {r.interpretation} |")
    L.append("")
    L.append("## 측정오차")
    L.append("| 지표 | 값 |")
    L.append("|---|---|")
    L.append(f"| SEM | {_num(res.icc.sem)} |")
    L.append(f"| MDC95 | {_num(res.icc.mdc95)} |")
    L.append(f"| s_w | {_num(res.icc.sw)} |")
    L.append(f"| RC (2.77·s_w) | {_num(res.icc.rc)} |")
    L.append("")
    L.append("## 평가자 쌍별")
    L.append("| 쌍 | bias | sd(diff) | LoA | CCC |")
    L.append("|---|---|---|---|---|")
    for p in res.pairwise:
        L.append(f"| {_lab(p.name_a)} vs {_lab(p.name_b)} | {_num(p.mean_diff)} | "
                 f"{_num(p.sd_diff)} | {_num(p.loa_lower, 2)}–"
                 f"{_num(p.loa_upper, 2)} | {_num(p.ccc)} |")
    if res.warnings:
        L.append("")
        L.append("## 주의")
        for w in res.warnings:
            L.append(f"- {w}")
    L.append("")
    return "\n".join(L)


# ==========================================================================
# Categorical
# ==========================================================================
def render_multicat_text(res: MultiCategorical) -> str:
    lines: List[str] = []
    L = lines.append
    lvl = _lvl(res.alpha_level)
    sec = [0]

    def nxt() -> int:
        sec[0] += 1
        return sec[0]

    L("=" * 70)
    L("  agreestat — 다중 평가자 일치도 / Multi-rater agreement (categorical)")
    L("=" * 70)
    L("")
    L(f"[{nxt()}] 데이터 요약 / Data summary")
    L(f"    대상 n = {res.n} (완전자료),  평가자 m = {res.m}"
      + (f"  (제외 {res.dropped}행)" if res.dropped else ""))
    L(f"    평가자: {', '.join(res.names)}")
    total = sum(res.category_counts) or 1
    L(f"    범주 {len(res.categories)}개 및 전체 평가 분포:")
    for c, cnt in zip(res.categories, res.category_counts):
        L(f"        {c[:24]:<24} {cnt:>7}  ({cnt / total * 100:5.1f}%)")

    L("")
    L(f"[{nxt()}] 다중 평가자 일치도 계수 / Multi-rater coefficients")
    L(f"    전체 관측 일치율 P̄a         = {_num(res.percent_agreement)}")
    L(f"    우연 일치 확률 P̄e (Fleiss)  = {_num(res.pe)}")
    unw = " (비가중)" if res.weights != "unweighted" else ""
    lo_grade = (interpret_kappa(res.fleiss_ci[0])
                if res.fleiss_ci[0] == res.fleiss_ci[0] else "판정 불가")
    L(f"    {("Fleiss' kappa" + unw):<28} = {_num(res.fleiss)}  "
      f"{_ci(res.fleiss_ci[0], res.fleiss_ci[1], lvl)}  → {res.interpretation}")
    if lo_grade != res.interpretation and res.fleiss_ci[0] == res.fleiss_ci[0]:
        L(f"        ⚑ CI 하한 {_num(res.fleiss_ci[0])} 기준 등급은 "
          f"'{lo_grade}' 입니다 — 등급은 점추정이 아니라 CI 하한으로 "
          "판단하세요(Landis & Koch 등급은 관례일 뿐입니다).")
    L(f"        H0(kappa=0) 검정: z = {_num(res.fleiss_z, 2)}, "
      f"p = {_p(res.fleiss_p)} (SE0 = {_num(res.fleiss_se_h0, 4)})")
    L(f"    {"Gwet's AC1":<28} = {_num(res.ac1)}  "
      f"{_ci(res.ac1_ci[0], res.ac1_ci[1], lvl)}")
    L(f"    {f"Krippendorff's alpha ({res.kalpha_metric})":<28} = {_num(res.kalpha)}  "
      f"{_ci(res.kalpha_ci[0], res.kalpha_ci[1], lvl)}  "
      f"(사용 대상 {res.n_alpha}건)")
    wname = ("가중(" + res.weights + ") " if res.weights != "unweighted" else "")
    L(f"    {f'쌍별 {wname}kappa 평균 (Light)':<26} = {_num(res.light_kappa)}")
    L("")
    L("    해석 기준(Landis & Koch 1977): <0.21 미미 / 0.21–0.40 약함 / "
      "0.41–0.60 보통 /")
    L("                                  0.61–0.80 상당함 / >0.80 거의 완벽 "
      "— 임상적 근거가 아니라 관례입니다.")
    L(f"    CI는 대상 단위 부트스트랩 백분위법 (B={res.bootstrap}, "
      f"seed={res.seed}) — 재실행해도 같은 값이 나옵니다.")
    if res.weights != "unweighted":
        L(f"    가중({res.weights}) kappa는 다중 평가자 Fleiss에는 적용되지 "
          "않습니다 — 순서 정보를 반영한")
        L("    지표로는 Krippendorff's alpha(ordinal)와 아래 쌍별 가중 kappa를 "
          "보세요.")

    L("")
    L(f"[{nxt()}] 범주별 Fleiss kappa / Category-specific")
    L(f"    {'category':<24} {'비율':>7} {'kappa':>8} {'z':>7} {'p':>8}")
    L(f"    {'-' * 24} {'-' * 7} {'-' * 8} {'-' * 7} {'-' * 8}")
    for e in res.per_category:
        L(f"    {e.category[:24]:<24} {_num(e.proportion, 3):>7} "
          f"{_num(e.kappa):>8} {_num(e.z, 2):>7} {_p(e.pvalue):>8}")

    L("")
    L(f"[{nxt()}] 평가자 쌍별 Cohen's kappa / Pairwise")
    wl = "가중(" + res.weights + ")" if res.weights != "unweighted" else "비가중"
    L(f"    ({wl} kappa)")
    for a, b, v, npair in res.pairwise:
        L(f"    {a[:14]:<14} vs {b[:14]:<14} kappa = {_num(v):>7}  (n={npair})")

    if res.min_kappa is not None:
        L("")
        L(f"[{nxt()}] 사전 설정 기준 / Pre-specified threshold")
        L(f"    기준: Fleiss' kappa(비가중) CI 하한 ≥ {_num(res.min_kappa)}")
        if res.meets_threshold is None:
            L("    판정: 판정 불가 — 부트스트랩 신뢰구간이 정의되지 않았습니다"
              "(자료가 퇴화했거나 대상 수가 너무 적습니다).")
        elif res.meets_threshold:
            L(f"    판정: 충족 — CI 하한 {_num(res.fleiss_ci[0])} ≥ "
              f"{_num(res.min_kappa)}")
        else:
            L(f"    판정: 미충족 — CI 하한 {_num(res.fleiss_ci[0])} < "
              f"{_num(res.min_kappa)}")
        if res.weights != "unweighted":
            L("    ※ 판정에 쓰인 Fleiss' kappa는 비가중입니다 — 2명 분석의 "
              "가중 kappa 기준과 직접 비교하지 마세요.")

    if res.warnings:
        L("")
        L(f"[{nxt()}] 주의 / Warnings")
        for w in res.warnings:
            L(f"    ! {w}")

    L("")
    L(f"[{nxt()}] 논문용 문장 / Suggested sentence")
    order = (f" 범주 순서 {'<'.join(res.categories)};" if res.ordinal else "")
    L(f"    {res.m}명의 평가자가 {res.n}건을 평가했을 때 Fleiss' kappa"
      f"(비가중)는 {_num(res.fleiss)} "
      f"{_ci_phrase(res.fleiss_ci[0], res.fleiss_ci[1], lvl)}"
      + ("" if res.fleiss_ci[0] != res.fleiss_ci[0]
         else f"(CI 하한 기준 등급 {lo_grade})")
      + f"이었고, Gwet's AC1 = {_num(res.ac1)} "
      f"{_ci_phrase(res.ac1_ci[0], res.ac1_ci[1], lvl)}, "
      f"Krippendorff's alpha({res.kalpha_metric}) = {_num(res.kalpha)} "
      f"{_ci_phrase(res.kalpha_ci[0], res.kalpha_ci[1], lvl)} 였다. "
      f"전체 관측 일치율은 {res.percent_agreement * 100:.1f}% 였다."
      f"{order} 신뢰구간은 대상 단위 부트스트랩(B={res.bootstrap})으로 "
      "구하였다.")
    L("")
    L(f"    Agreement among {res.m} raters over {res.n} cases was "
      f"unweighted Fleiss' kappa = {_num(res.fleiss)} "
      f"{_ci_phrase_en(res.fleiss_ci[0], res.fleiss_ci[1], lvl)}, "
      f"Gwet's AC1 = {_num(res.ac1)}, Krippendorff's "
      f"alpha ({res.kalpha_metric}) = {_num(res.kalpha)}; observed agreement "
      f"{res.percent_agreement * 100:.1f}%. Confidence intervals are "
      f"subject-level bootstrap percentiles (B={res.bootstrap}).")
    L("")
    return "\n".join(lines)


def render_multicat_json(res: MultiCategorical) -> str:
    out: Dict[str, Any] = {
        "analysis": "multi_rater_categorical",
        "n_subjects": res.n, "n_raters": res.m, "raters": [_lab(r) for r in res.names],
        "categories": [_lab(c) for c in res.categories],
        "category_counts": res.category_counts,
        "alpha": res.alpha_level, "dropped_rows": res.dropped,
        "percent_agreement": res.percent_agreement,
        "fleiss_kappa": {"value": res.fleiss, "ci_lower": res.fleiss_ci[0],
                         "ci_upper": res.fleiss_ci[1], "pe": res.pe,
                         "se_h0": res.fleiss_se_h0, "z": res.fleiss_z,
                         "p": res.fleiss_p,
                         "interpretation": res.interpretation},
        "gwet_ac1": {"value": res.ac1, "ci_lower": res.ac1_ci[0],
                     "ci_upper": res.ac1_ci[1]},
        "krippendorff_alpha": {"value": res.kalpha, "metric": res.kalpha_metric,
                               "ci_lower": res.kalpha_ci[0],
                               "ci_upper": res.kalpha_ci[1],
                               "n_units": res.n_alpha},
        "light_kappa": res.light_kappa,
        "per_category": [{"category": _lab(e.category), "proportion": e.proportion,
                          "kappa": e.kappa, "se": e.se, "z": e.z,
                          "p": e.pvalue} for e in res.per_category],
        "pairwise_kappa": [{"a": _lab(a), "b": _lab(b), "kappa": v, "n": npair}
                           for a, b, v, npair in res.pairwise],
        "ci_method": {"type": "cluster bootstrap (subject-level percentile)",
                      "resamples": res.bootstrap, "seed": res.seed},
        "weights": res.weights,
        "warnings": res.warnings,
    }
    if res.min_kappa is not None:
        out["threshold"] = {"min_kappa": res.min_kappa,
                            "meets": res.meets_threshold}
    return json.dumps(out, ensure_ascii=False, indent=2, allow_nan=True)


def render_multicat_markdown(res: MultiCategorical) -> str:
    lvl = _lvl(res.alpha_level)
    L: List[str] = []
    L.append(f"# 다중 평가자 일치도 (범주형) — n={res.n}, m={res.m}")
    L.append("")
    L.append("## 일치도 계수")
    L.append(f"| 지표 | 값 | {lvl}% CI |")
    L.append("|---|---|---|")
    L.append(f"| 관측 일치율 | {_num(res.percent_agreement)} | — |")
    L.append(f"| Fleiss' kappa | {_num(res.fleiss)} | "
             f"{_num(res.fleiss_ci[0])}–{_num(res.fleiss_ci[1])} |")
    L.append(f"| Gwet's AC1 | {_num(res.ac1)} | "
             f"{_num(res.ac1_ci[0])}–{_num(res.ac1_ci[1])} |")
    L.append(f"| Krippendorff's alpha ({res.kalpha_metric}) | "
             f"{_num(res.kalpha)} | {_num(res.kalpha_ci[0])}–"
             f"{_num(res.kalpha_ci[1])} |")
    L.append(f"| Light's kappa | {_num(res.light_kappa)} | — |")
    L.append("")
    L.append("## 범주별 Fleiss kappa")
    L.append("| 범주 | 비율 | kappa | p |")
    L.append("|---|---|---|---|")
    for e in res.per_category:
        L.append(f"| {_lab(e.category)} | {_num(e.proportion)} | {_num(e.kappa)} | "
                 f"{_p(e.pvalue)} |")
    L.append("")
    L.append("## 평가자 쌍별 Cohen's kappa")
    L.append("| 쌍 | kappa | n |")
    L.append("|---|---|---|")
    for a, b, v, npair in res.pairwise:
        L.append(f"| {_lab(a)} vs {_lab(b)} | {_num(v)} | {npair} |")
    if res.warnings:
        L.append("")
        L.append("## 주의")
        for w in res.warnings:
            L.append(f"- {w}")
    L.append("")
    return "\n".join(L)
