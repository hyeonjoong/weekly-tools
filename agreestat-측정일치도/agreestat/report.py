"""Human-readable (Korean + English) report and JSON rendering."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .agreement import interpret_icc
from .analyze import AnalysisResult

__all__ = ["render_text", "render_json", "render_markdown", "render_plot_data",
           "render_svg"]


def _num(x: float, d: int = 3) -> str:
    if x != x:  # NaN
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "inf" if x > 0 else "-inf"
    return f"{x:.{d}f}"


def _p(p: float) -> str:
    if p != p:
        return "NaN"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _lvl(alpha: float) -> str:
    """Confidence-level label; avoids the degenerate '0%'/'100%' at extremes."""
    v = (1.0 - alpha) * 100.0
    if abs(v - round(v)) < 1e-9 and 0 < round(v) < 100:
        return str(int(round(v)))
    for prec in (4, 8, 12, 17):   # e.g. --alpha 1e-12 must not print "100% CI"
        txt = f"{v:.{prec}g}"
        if float(txt) not in (0.0, 100.0):
            return txt
    return f"{v:.17g}"


def _ci(lo: float, hi: float, level: str, d: int = 3, unit: str = "") -> str:
    return f"[{level}% CI {_num(lo, d)}{unit}, {_num(hi, d)}{unit}]"


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    lvl = _lvl(res.alpha)
    u = res.ba.unit  # "" or "%"

    L("=" * 70)
    L("  agreestat — 측정 방법 일치도 리포트 / Method-comparison agreement report")
    L("=" * 70)

    # [1] Data summary
    L("")
    L("[1] 데이터 요약 / Data summary")
    L(f"    paired n = {res.n}" + (f"  (제외 {res.dropped} rows)" if res.dropped else ""))
    L(f"    method A = \"{res.name_a}\":  mean={_num(res.mean_a)}, sd={_num(res.sd_a)}")
    L(f"    method B = \"{res.name_b}\":  mean={_num(res.mean_b)}, sd={_num(res.sd_b)}")
    L(f"    차이(difference) 정의: A - B = {res.name_a} - {res.name_b}")

    # [2] Bland-Altman
    ba = res.ba
    L("")
    if ba.mode == "percent":
        L("[2] Bland–Altman 분석 (백분율 / percentage, diff% = 100·(A−B)/mean)")
    else:
        L("[2] Bland–Altman 분석 (절대 / absolute)")
    L(f"    bias (평균차) = {_num(ba.bias)}{u}  {_ci(*ba.bias_ci, lvl, unit=u)}")
    L(f"    SD of differences = {_num(ba.sd_diff)}{u}")
    L(f"    95% 일치한계 LoA = [{_num(ba.loa_lower)}{u}, {_num(ba.loa_upper)}{u}]")
    L(f"       lower LoA {_num(ba.loa_lower)}{u}  {_ci(*ba.loa_lower_ci, lvl, unit=u)}")
    L(f"       upper LoA {_num(ba.loa_upper)}{u}  {_ci(*ba.loa_upper_ci, lvl, unit=u)}")
    L(f"    LoA 밖 관측치: {ba.n_outside}/{ba.n} "
      f"({_num(ba.pct_outside, 1)}%)  — 정규성 하에서 ~5% 기대")
    if ba.loa_ci_halfwidth == ba.loa_ci_halfwidth:
        L(f"    LoA 추정 정밀도: 각 LoA의 {lvl}% CI 반너비 = "
          f"±{_num(ba.loa_ci_halfwidth, 3)}{u}")
    if res.precision_target_hw is not None:
        if res.precision_required_n is not None:
            L(f"    목표 반너비 ±{_num(res.precision_target_hw, 3)}{u} 달성 필요 표본 "
              f"n ≈ {res.precision_required_n} "
              f"(정규근사 {_num(res.precision_required_n_approx, 1)})"
              + ("  — 현재 n으로 이미 충족"
                 if ba.n >= res.precision_required_n else ""))
        else:
            L(f"    목표 반너비 ±{_num(res.precision_target_hw, 3)}{u} 달성에 필요한 "
              "표본이 매우 큼(n > 10,000,000) — 목표가 너무 촘촘하거나 SD가 큽니다.")
    if ba.prop_pvalue == ba.prop_pvalue:  # not NaN
        verdict = "비례 편향 있음 ⚠" if ba.prop_bias else "비례 편향 없음"
        L(f"    비례 편향 검정 (diff ~ mean): slope={_num(ba.prop_slope, 4)}, "
          f"p={_p(ba.prop_pvalue)}  → {verdict}")
    else:
        L("    비례 편향 검정: 판정 불가 (측정값 평균의 분산이 0)")
    if res.accept_lower is not None:
        verdict = ("교환가능 (interchangeable) ✔" if res.interchangeable
                   else "교환 불가 (LoA가 허용한계 초과) ✗")
        L(f"    임상 허용한계 대비 판정: 허용 [{_num(res.accept_lower, 2)}{u}, "
          f"{_num(res.accept_upper, 2)}{u}]  →  {verdict}")

    # [2b] Regression-based LoA (only when proportional bias detected)
    rl = ba.reg_loa
    if rl is not None and rl.available:
        L("")
        L("[2b] 회귀 기반 LoA / Regression-based LoA (Bland & Altman 1999 §3)")
        L(f"    차이 회귀선   D(mean) = {_num(rl.diff_intercept, 4)} + "
          f"{_num(rl.diff_slope, 4)}·mean")
        L(f"    잔차SD 회귀선 s(mean) = 1.253·({_num(rl.sd_intercept, 4)} + "
          f"{_num(rl.sd_slope, 4)}·mean)")
        L(f"    LoA(mean) = D(mean) ± 1.96·s(mean)")
        L(f"      mean={_num(rl.mean_min, 2)}{u}:  D={_num(rl.fit_at_min, 2)}{u}  "
          f"LoA=[{_num(rl.loa_at_min[0], 2)}{u}, {_num(rl.loa_at_min[1], 2)}{u}]")
        L(f"      mean={_num(rl.mean_max, 2)}{u}:  D={_num(rl.fit_at_max, 2)}{u}  "
          f"LoA=[{_num(rl.loa_at_max[0], 2)}{u}, {_num(rl.loa_at_max[1], 2)}{u}]")
        if rl.sd_negative_warning:
            L("    ⚠ 잔차SD 선형모형이 일부 구간에서 음수로 외삽됩니다 — "
              "백분율(--percent)/로그 변환을 고려하세요.")

    # [2c] Repeated-measures LoA (only when replicates exist)
    rm = res.rm_ba
    if rm is not None and rm.available:
        L("")
        L("[2c] 반복측정 보정 LoA / Repeated-measures LoA "
          "(Bland & Altman 2007) — 권장")
        L(f"    피험자 n={rm.n_subjects}, 총 쌍 N={rm.n_pairs} (개인당 반복 있음)")
        L(f"    bias = {_num(rm.bias)}{u}")
        L(f"    분산성분: between σ_b²={_num(rm.var_between)}, "
          f"within σ_w²={_num(rm.var_within)}  (m0={_num(rm.m0, 2)})"
          + ("  [σ_b² 음수→0 보정]" if rm.var_between_clamped else ""))
        L(f"    SD(단일차이)=√(σ_b²+σ_w²)={_num(rm.sd_diff)}{u}  "
          f"(naive SD={_num(ba.sd_diff)}{u})")
        L(f"    반복측정 95% LoA = [{_num(rm.loa_lower)}{u}, "
          f"{_num(rm.loa_upper)}{u}]   (naive LoA=[{_num(ba.loa_lower)}{u}, "
          f"{_num(ba.loa_upper)}{u}])")
        L(f"       lower LoA {_ci(*rm.loa_lower_ci, lvl, unit=u)}")
        L(f"       upper LoA {_ci(*rm.loa_upper_ci, lvl, unit=u)}")
        L("    ※ LoA의 CI는 피험자 수(n) 기준 근사입니다.")

    # [3] ICC
    L("")
    L("[3] ICC (급내상관계수 / intraclass correlation, 단일 측정)")
    for r in (res.icc21, res.icc31):
        headline = "  ← 보고 권장" if r.model == res.reported_icc else ""
        L(f"    {r.model} {r.description}")
        L(f"        = {_num(r.value)}  {_ci(r.ci_lower, r.ci_upper, lvl)}  "
          f"({r.interpretation}){headline}")
    if res.icc21.f == res.icc21.f and res.icc21.f not in (float("inf"),):
        L(f"    F({_num(res.icc21.df1,0)}, {_num(res.icc21.df2,0)}) = "
          f"{_num(res.icc21.f)}, p={_p(res.icc21.pvalue)}")
    r21 = res.icc21
    if r21.value == r21.value and r21.ci_lower == r21.ci_lower:
        lo_grade = interpret_icc(r21.ci_lower).split(" / ")[0]
        L(f"    ⚑ Koo & Li(2016) 권장: 점추정이 아니라 95% CI 하한"
          f"({_num(r21.ci_lower)}) 기준으로 판단 → '{lo_grade}'")
    L("    해석 기준(Koo & Li 2016): <0.5 낮음 / 0.5–0.75 보통 / "
      "0.75–0.9 좋음 / >0.9 매우 좋음")

    # [4] CCC
    c = res.ccc
    L("")
    L("[4] Lin's CCC (일치상관계수 / concordance correlation)")
    L(f"    CCC = {_num(c.value)}  {_ci(c.ci_lower, c.ci_upper, lvl)}  "
      f"({c.interpretation})")
    L("    ※ CCC 등급은 McBride(2005) 척도로, ICC의 Koo & Li 척도와 다릅니다 "
      "(같은 수치라도 다른 단어가 나올 수 있음).")
    if c.bias_correction == c.bias_correction:
        L(f"    정확도 Cb(bias correction) = {_num(c.bias_correction)} "
          f"(= CCC / Pearson r; 1에 가까울수록 계통오차 작음)")

    # [5] Repeatability
    rep = res.repeat
    L("")
    L("[5] 반복측정 지표 / Repeatability (within-subject)")
    if rep.available:
        L(f"    피험자 {rep.n_subjects}명 중 반복측정 {rep.n_replicated}명 기준")
        L(f"    within-subject CV: {res.name_a}={_num(rep.cv_a, 2)}%, "
          f"{res.name_b}={_num(rep.cv_b, 2)}%")
        L(f"    반복성 계수(repeatability coeff, 2.77·s_w): "
          f"{res.name_a}={_num(rep.rc_a)}, {res.name_b}={_num(rep.rc_b)}")
        L(f"    within-subject SD s_w: {res.name_a}={_num(rep.sw_a)}, "
          f"{res.name_b}={_num(rep.sw_b)}")
    else:
        L(f"    (건너뜀: {rep.note})")

    # [6] Correlation / difference test (context)
    L("")
    L("[6] 상관·차이 검정 (참고용 / context)")
    p = res.pearson
    L(f"    Pearson r = {_num(p.r)}  {_ci(p.ci_lower, p.ci_upper, lvl)}")
    pt = res.paired
    sig = "유의함" if (pt.pvalue == pt.pvalue and pt.pvalue < res.alpha) else "유의하지 않음"
    L(f"    paired t-test: t={_num(pt.t)}, df={pt.df}, p={_p(pt.pvalue)}  "
      f"→ bias≠0 {sig}")
    L("    ⚠ 주의: 높은 상관(r)은 '일치도(agreement)'가 아닙니다. r은 계통편향을 "
      "감지하지 못하므로 Bland–Altman/ICC/CCC로 판단하세요.")

    # [7] Method-comparison regression (CLSI EP09)
    _render_regression_text(res, L, lvl)

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


def _bias_verdict(flag) -> str:
    """Render a proportional/constant-bias flag (True/False/None)."""
    if flag is None:
        return "판정 불가"
    return "있음 ⚠" if flag else "없음"


def _render_regression_text(res: AnalysisResult, L, lvl: str) -> None:
    dem = res.deming
    pb = res.passing_bablok
    if (dem is None or not dem.available) and (pb is None or not pb.available):
        return
    L("")
    L("[7] 방법비교 회귀 / Method-comparison regression (CLSI EP09)")
    L(f"    회귀식: {res.name_a}(검증) = 절편 + 기울기·{res.name_b}(기준)")
    L("    기울기 CI가 1을 포함하면 비례편향 없음, 절편 CI가 0을 포함하면 상수편향 없음")
    L("    권장: 기본은 Passing–Bablok(분포무관·강건). 오차가 정규·등분산이고 오차비 λ를"
      " 알면 Deming. 두 결과가 크게 다르면 이상치/비선형/분포가정을 점검하세요.")
    if pb is not None and pb.available:
        L(f"    Passing–Bablok (분포무관·이상치에 강건):")
        L(f"        기울기 = {_num(pb.slope, 4)}  "
          f"{_ci(*pb.slope_ci, lvl, d=4)}  → 비례편향 {_bias_verdict(pb.proportional_bias)}")
        L(f"        절편   = {_num(pb.intercept, 4)}  "
          f"{_ci(*pb.intercept_ci, lvl, d=4)}  → 상수편향 {_bias_verdict(pb.constant_bias)}")
        if pb.note:
            L(f"        ※ {pb.note}")
    elif pb is not None and pb.note:
        L(f"    Passing–Bablok: 건너뜀 ({pb.note})")
    if dem is not None and dem.available:
        lam_note = "직교회귀" if dem.lam == 1.0 else f"λ={_num(dem.lam, 3)}"
        L(f"    Deming (두 방법 모두 오차 가정, {lam_note}):")
        L(f"        기울기 = {_num(dem.slope, 4)}  "
          f"{_ci(*dem.slope_ci, lvl, d=4)}  → 비례편향 {_bias_verdict(dem.proportional_bias)}")
        L(f"        절편   = {_num(dem.intercept, 4)}  "
          f"{_ci(*dem.intercept_ci, lvl, d=4)}  → 상수편향 {_bias_verdict(dem.constant_bias)}")
        if dem.note:
            L(f"        ※ {dem.note}")
    elif dem is not None and dem.note:
        L(f"    Deming: 건너뜀 ({dem.note})")
    # Predicted systematic bias at a medical decision level (EP09) — the number
    # a clinician actually uses ("at this level, the two methods differ by …").
    dp = None
    if dem is not None and dem.decision_point is not None:
        dp = dem.decision_point
    elif pb is not None and pb.decision_point is not None:
        dp = pb.decision_point
    if dp is not None:
        # The regression is always fit on the RAW (absolute) values, so bias(Xc)
        # is in the methods' absolute units — never the Bland-Altman % unit.
        L(f"    결정수준 XC={_num(dp, 3)}에서의 예측 계통편향 "
          f"bias(XC)=절편+(기울기−1)·XC  (절대 단위):")
        if pb is not None and pb.available and pb.bias_at_dp == pb.bias_at_dp:
            L(f"        Passing–Bablok: {_num(pb.bias_at_dp, 3)}")
        if dem is not None and dem.available and dem.bias_at_dp == dem.bias_at_dp:
            ci_txt = (f"  {_ci(*dem.bias_at_dp_ci, lvl, d=3)}"
                      if dem.bias_at_dp_ci[0] == dem.bias_at_dp_ci[0] else "")
            L(f"        Deming: {_num(dem.bias_at_dp, 3)}{ci_txt}")
        # The acceptance limit compares only in ABSOLUTE mode: in --percent mode
        # --accept is a percentage limit, incomparable to an absolute bias(Xc).
        if (res.accept_lower is not None and res.ba.mode == "absolute"
                and dem is not None and dem.available
                and dem.bias_at_dp_ci[0] == dem.bias_at_dp_ci[0]):
            lo, hi = dem.bias_at_dp_ci
            within = res.accept_lower <= lo and hi <= res.accept_upper
            L(f"        → Deming bias(XC) {lvl}% CI가 허용한계 "
              f"[{_num(res.accept_lower, 2)}, {_num(res.accept_upper, 2)}] "
              + ("안에 있음 ✔ (그 수준에서 교환 가능)"
                 if within else "밖에 있음 ✗ (그 수준에서 편향이 큼)"))
        elif (res.accept_lower is not None and res.ba.mode == "percent"
              and dp is not None):
            L("        (※ --percent 모드에서는 --accept가 백분율 한계이므로 "
              "절대 단위 bias(XC)와 직접 비교하지 않습니다.)")


def _sentence(res: AnalysisResult) -> str:
    ba = res.ba
    lvl = _lvl(res.alpha)
    u = ba.unit
    icc_r = res.icc21
    c = res.ccc
    a_const = res.sd_a == 0.0
    b_const = res.sd_b == 0.0
    rm = res.rm_ba
    parts: List[str] = []
    if rm is not None and rm.available:
        parts.append(
            f"'{res.name_a}'와 '{res.name_b}'의 일치도를 반복측정 보정 "
            f"Bland–Altman 분석(Bland & Altman 2007; 피험자 {rm.n_subjects}명, "
            f"총 {rm.n_pairs}쌍)으로 평가한 결과, 평균 편향(bias)은 "
            f"{_num(rm.bias, 2)}{u}였고, 반복측정 95% 일치한계(LoA)는 "
            f"{_num(rm.loa_lower, 2)}{u}에서 {_num(rm.loa_upper, 2)}{u}였다."
        )
    else:
        parts.append(
            f"'{res.name_a}'와 '{res.name_b}'의 일치도를 Bland–Altman 분석으로 "
            f"평가한 결과, 평균 편향(bias)은 {_num(ba.bias, 2)}{u}"
            f"({lvl}% CI {_num(ba.bias_ci[0], 2)}{u}~{_num(ba.bias_ci[1], 2)}{u})이었고, "
            f"95% 일치한계(LoA)는 {_num(ba.loa_lower, 2)}{u}에서 "
            f"{_num(ba.loa_upper, 2)}{u}였다."
        )
    # ICC/CCC clauses only when meaningful (not a zero-variance/degenerate case).
    if icc_r.value == icc_r.value and not (a_const or b_const):
        if icc_r.ci_lower == icc_r.ci_lower:
            # Grade from the CI lower bound (Koo & Li 2016), not the point est.
            grade = interpret_icc(icc_r.ci_lower).split(" / ")[-1]
            parts.append(
                f" ICC(2,1)은 {_num(icc_r.value, 3)}"
                f"({lvl}% CI {_num(icc_r.ci_lower, 3)}~{_num(icc_r.ci_upper, 3)})로, "
                f"신뢰구간 하한 기준 '{grade}' 수준이었다."
            )
        else:
            parts.append(
                f" ICC(2,1)은 {_num(icc_r.value, 3)}였다(신뢰구간 계산 불가)."
            )
    if c.value == c.value and not (a_const or b_const):
        if c.ci_lower == c.ci_lower:
            parts.append(
                f" Lin의 CCC는 {_num(c.value, 3)}"
                f"({lvl}% CI {_num(c.ci_lower, 3)}~{_num(c.ci_upper, 3)})였다."
            )
        else:
            parts.append(f" Lin의 CCC는 {_num(c.value, 3)}였다(신뢰구간 계산 불가).")
    if a_const or b_const:
        parts.append(" 한 방법의 분산이 0이어서 ICC/CCC/상관은 해석에서 제외하였다.")
    if res.interchangeable is not None:
        loa_label = ("반복측정 95% LoA" if (rm is not None and rm.available)
                     else "95% LoA")
        if res.interchangeable:
            parts.append(
                f" {loa_label}가 사전 설정한 임상 허용한계"
                f"([{_num(res.accept_lower, 2)}{u}, {_num(res.accept_upper, 2)}{u}]) "
                "안에 있어 두 방법은 임상적으로 교환 가능하다고 판단하였다."
            )
        else:
            parts.append(
                f" 다만 {loa_label}가 사전 설정한 임상 허용한계"
                f"([{_num(res.accept_lower, 2)}{u}, {_num(res.accept_upper, 2)}{u}])를 "
                "벗어나 두 방법을 교환하여 쓰기는 어렵다."
            )
    if ba.prop_bias:
        parts.append(" 다만 비례 편향이 유의하여(차이가 측정값 크기에 의존) "
                     "단일 LoA 해석에는 주의가 필요하다.")
    pb = res.passing_bablok
    if pb is not None and pb.available and pb.slope_ci[0] == pb.slope_ci[0]:
        if pb.proportional_bias and pb.constant_bias:
            bias_txt = "비례편향과 상수편향이 모두 관찰되었다"
        elif pb.proportional_bias:
            bias_txt = "유의한 비례편향이 관찰되었다"
        elif pb.constant_bias:
            bias_txt = "유의한 상수편향이 관찰되었다"
        else:
            bias_txt = "유의한 비례·상수 편향은 관찰되지 않았다"
        parts.append(
            f" Passing–Bablok 회귀에서 기울기는 {_num(pb.slope, 3)}"
            f"({lvl}% CI {_num(pb.slope_ci[0], 3)}~{_num(pb.slope_ci[1], 3)}), "
            f"절편은 {_num(pb.intercept, 3)}"
            f"({lvl}% CI {_num(pb.intercept_ci[0], 3)}~{_num(pb.intercept_ci[1], 3)})"
            f"로, {bias_txt}.")
    parts.append(" 상관계수만으로는 일치도를 보장할 수 없으므로 위 지표로 판단하였다.")
    return "".join(parts)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------
def _f(x: float) -> Any:
    """JSON-safe float: NaN/inf -> None."""
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def render_json(res: AnalysisResult) -> str:
    ba = res.ba
    d: Dict[str, Any] = {
        "method_a": res.name_a,
        "method_b": res.name_b,
        "n": res.n,
        "dropped": res.dropped,
        "alpha": res.alpha,
        "difference": f"{res.name_a} - {res.name_b}",
        "descriptives": {
            "mean_a": _f(res.mean_a), "sd_a": _f(res.sd_a),
            "mean_b": _f(res.mean_b), "sd_b": _f(res.sd_b),
        },
        "bland_altman": {
            "mode": ba.mode,
            "unit": ba.unit or "absolute",
            "bias": _f(ba.bias),
            "bias_ci": [_f(ba.bias_ci[0]), _f(ba.bias_ci[1])],
            "sd_diff": _f(ba.sd_diff),
            "loa_lower": _f(ba.loa_lower),
            "loa_lower_ci": [_f(ba.loa_lower_ci[0]), _f(ba.loa_lower_ci[1])],
            "loa_upper": _f(ba.loa_upper),
            "loa_upper_ci": [_f(ba.loa_upper_ci[0]), _f(ba.loa_upper_ci[1])],
            "proportional_slope": _f(ba.prop_slope),
            "proportional_pvalue": _f(ba.prop_pvalue),
            "proportional_bias": ba.prop_bias,
            "n_outside_loa": ba.n_outside,
            "pct_outside_loa": _f(ba.pct_outside),
            "se_loa": _f(ba.se_loa),
            "loa_ci_halfwidth": _f(ba.loa_ci_halfwidth),
            "regression_loa": _reg_loa_json(ba.reg_loa),
        },
        "bland_altman_repeated_measures": _rm_ba_json(res.rm_ba),
        "acceptance": {
            "lower": _f(res.accept_lower) if res.accept_lower is not None else None,
            "upper": _f(res.accept_upper) if res.accept_upper is not None else None,
            "interchangeable": res.interchangeable,
        },
        "precision": {
            "se_loa": _f(ba.se_loa),
            "loa_ci_halfwidth": _f(ba.loa_ci_halfwidth),
            "target_halfwidth": _f(res.precision_target_hw)
            if res.precision_target_hw is not None else None,
            "required_n": res.precision_required_n,
            "required_n_normal_approx": _f(res.precision_required_n_approx)
            if res.precision_required_n_approx is not None else None,
        },
        "icc": {
            "reported": res.reported_icc,
            "icc_2_1": _icc_json(res.icc21),
            "icc_3_1": _icc_json(res.icc31),
            "ci_lower_grade": (
                interpret_icc(res.icc21.ci_lower)
                if res.icc21.ci_lower == res.icc21.ci_lower else None),
        },
        "ccc": {
            "value": _f(res.ccc.value),
            "ci": [_f(res.ccc.ci_lower), _f(res.ccc.ci_upper)],
            "pearson_r": _f(res.ccc.pearson_r),
            "bias_correction_cb": _f(res.ccc.bias_correction),
            "interpretation": res.ccc.interpretation,
        },
        "repeatability": {
            "available": res.repeat.available,
            "note": res.repeat.note,
            "n_subjects": res.repeat.n_subjects,
            "n_replicated": res.repeat.n_replicated,
            "within_subject_cv_pct": {"a": _f(res.repeat.cv_a), "b": _f(res.repeat.cv_b)},
            "repeatability_coefficient": {"a": _f(res.repeat.rc_a), "b": _f(res.repeat.rc_b)},
            "within_subject_sd": {"a": _f(res.repeat.sw_a), "b": _f(res.repeat.sw_b)},
        },
        "pearson": {
            "r": _f(res.pearson.r),
            "ci": [_f(res.pearson.ci_lower), _f(res.pearson.ci_upper)],
        },
        "paired_t": {
            "t": _f(res.paired.t), "df": res.paired.df,
            "pvalue": _f(res.paired.pvalue), "mean_diff": _f(res.paired.mean_diff),
        },
        "regression": {
            "model": f"{res.name_a} = intercept + slope * {res.name_b}",
            "deming": _regression_json(res.deming),
            "passing_bablok": _regression_json(res.passing_bablok),
        },
        "warnings": res.warnings,
    }
    return json.dumps(d, ensure_ascii=False, indent=2)


def _regression_json(reg) -> Any:
    if reg is None:
        return {"available": False, "note": "not computed"}
    if not reg.available:
        return {"available": False, "note": reg.note}
    out: Dict[str, Any] = {
        "available": True,
        "method": reg.method,
        "slope": _f(reg.slope),
        "slope_ci": [_f(reg.slope_ci[0]), _f(reg.slope_ci[1])],
        "intercept": _f(reg.intercept),
        "intercept_ci": [_f(reg.intercept_ci[0]), _f(reg.intercept_ci[1])],
        "proportional_bias": reg.proportional_bias,
        "constant_bias": reg.constant_bias,
        "note": reg.note,
    }
    if reg.method == "Deming":
        out["lambda"] = _f(reg.lam)
    else:
        out["n_slopes"] = reg.n_slopes
        out["k_offset"] = reg.k_offset
    if reg.decision_point is not None:
        dp: Dict[str, Any] = {
            "level": _f(reg.decision_point),
            "bias": _f(reg.bias_at_dp),
        }
        if reg.method == "Deming":
            dp["bias_ci"] = [_f(reg.bias_at_dp_ci[0]), _f(reg.bias_at_dp_ci[1])]
        out["bias_at_decision_point"] = dp
    return out


def _icc_json(r) -> Dict[str, Any]:
    return {
        "model": r.model,
        "description": r.description,
        "value": _f(r.value),
        "ci": [_f(r.ci_lower), _f(r.ci_upper)],
        "f": _f(r.f),
        "df1": _f(r.df1),
        "df2": _f(r.df2),
        "pvalue": _f(r.pvalue),
        "interpretation": r.interpretation,
    }


def _reg_loa_json(rl) -> Any:
    if rl is None or not rl.available:
        return None
    return {
        "available": True,
        "diff_line": {"intercept": _f(rl.diff_intercept), "slope": _f(rl.diff_slope)},
        "sd_line": {"intercept": _f(rl.sd_intercept), "slope": _f(rl.sd_slope),
                    "factor": _f(rl.factor)},
        "loa_at_mean_min": {"mean": _f(rl.mean_min), "fit": _f(rl.fit_at_min),
                            "lower": _f(rl.loa_at_min[0]), "upper": _f(rl.loa_at_min[1])},
        "loa_at_mean_max": {"mean": _f(rl.mean_max), "fit": _f(rl.fit_at_max),
                            "lower": _f(rl.loa_at_max[0]), "upper": _f(rl.loa_at_max[1])},
        "sd_negative_warning": rl.sd_negative_warning,
    }


def _rm_ba_json(rm) -> Any:
    if rm is None or not rm.available:
        return {"available": False,
                "note": rm.note if rm is not None else "not computed"}
    return {
        "available": True,
        "n_subjects": rm.n_subjects,
        "n_pairs": rm.n_pairs,
        "n_replicated_subjects": rm.n_replicated_subjects,
        "bias": _f(rm.bias),
        "sd_diff": _f(rm.sd_diff),
        "variance_components": {"between": _f(rm.var_between),
                                "within": _f(rm.var_within), "m0": _f(rm.m0),
                                "between_clamped": rm.var_between_clamped},
        "loa_lower": _f(rm.loa_lower),
        "loa_lower_ci": [_f(rm.loa_lower_ci[0]), _f(rm.loa_lower_ci[1])],
        "loa_upper": _f(rm.loa_upper),
        "loa_upper_ci": [_f(rm.loa_upper_ci[0]), _f(rm.loa_upper_ci[1])],
        "note": "",
    }


# --------------------------------------------------------------------------
# Markdown results table
# --------------------------------------------------------------------------
def render_markdown(res: AnalysisResult) -> str:
    ba = res.ba
    u = ba.unit
    lvl = _lvl(res.alpha)

    def ci(lo, hi):
        if lo != lo or hi != hi:
            return "—"
        return f"{_num(lo, 3)}{u2} to {_num(hi, 3)}{u2}"

    u2 = u
    rows: List[List[str]] = []
    rows.append(["Bias (A−B)", f"{_num(ba.bias, 3)}{u}", ci(*ba.bias_ci), "—"])
    rows.append(["SD of differences", f"{_num(ba.sd_diff, 3)}{u}", "—", "—"])
    rows.append(["Lower LoA", f"{_num(ba.loa_lower, 3)}{u}", ci(*ba.loa_lower_ci), "—"])
    rows.append(["Upper LoA", f"{_num(ba.loa_upper, 3)}{u}", ci(*ba.loa_upper_ci), "—"])
    rows.append(["% outside LoA",
                 f"{_num(ba.pct_outside, 1)}% ({ba.n_outside}/{ba.n})", "—",
                 "~5% expected"])
    if ba.prop_pvalue == ba.prop_pvalue:
        rows.append(["Proportional-bias slope", _num(ba.prop_slope, 4), "—",
                     f"p={_p(ba.prop_pvalue)} "
                     f"({'present' if ba.prop_bias else 'none'})"])
    rm = res.rm_ba
    if rm is not None and rm.available:
        rows.append(["Repeated-measures lower LoA", f"{_num(rm.loa_lower, 3)}{u}",
                     ci(*rm.loa_lower_ci), "B&A 2007 (recommended)"])
        rows.append(["Repeated-measures upper LoA", f"{_num(rm.loa_upper, 3)}{u}",
                     ci(*rm.loa_upper_ci), "B&A 2007 (recommended)"])
    for r in (res.icc21, res.icc31):
        note = r.interpretation.split(" / ")[0]
        if r is res.icc21 and r.ci_lower == r.ci_lower:
            note += f" (CI-lower: {interpret_icc(r.ci_lower).split(' / ')[0]})"
        rows.append([r.model, _num(r.value, 3), ci(r.ci_lower, r.ci_upper), note])
    c = res.ccc
    rows.append(["Lin's CCC", _num(c.value, 3), ci(c.ci_lower, c.ci_upper),
                 c.interpretation.split(" / ")[0]])
    rows.append(["Pearson r", _num(res.pearson.r, 3),
                 ci(res.pearson.ci_lower, res.pearson.ci_upper), "context only"])
    pb = res.passing_bablok
    if pb is not None and pb.available:
        rows.append(["Passing–Bablok slope", _num(pb.slope, 3),
                     ci(*pb.slope_ci),
                     "prop. bias" if pb.proportional_bias else "slope≈1"])
        rows.append(["Passing–Bablok intercept", _num(pb.intercept, 3),
                     ci(*pb.intercept_ci),
                     "const. bias" if pb.constant_bias else "intercept≈0"])
    dem = res.deming
    if dem is not None and dem.available:
        rows.append(["Deming slope", _num(dem.slope, 3), ci(*dem.slope_ci),
                     "prop. bias" if dem.proportional_bias else "slope≈1"])
        rows.append(["Deming intercept", _num(dem.intercept, 3),
                     ci(*dem.intercept_ci),
                     "const. bias" if dem.constant_bias else "intercept≈0"])
    # decision-point predicted bias (absolute units; Deming CI when available)
    _dp = None
    if dem is not None and dem.decision_point is not None:
        _dp = dem.decision_point
    elif pb is not None and pb.decision_point is not None:
        _dp = pb.decision_point
    if _dp is not None and dem is not None and dem.available \
            and dem.bias_at_dp == dem.bias_at_dp:
        dp_lo, dp_hi = dem.bias_at_dp_ci
        dp_ci = ("—" if dp_lo != dp_lo
                 else f"{_num(dp_lo, 3)} to {_num(dp_hi, 3)}")  # absolute, no unit
        rows.append([f"Predicted bias at Xc={_num(_dp, 3)} (Deming)",
                     _num(dem.bias_at_dp, 3), dp_ci, "absolute units"])
    elif _dp is not None and pb is not None and pb.available \
            and pb.bias_at_dp == pb.bias_at_dp:
        rows.append([f"Predicted bias at Xc={_num(_dp, 3)} (Passing–Bablok)",
                     _num(pb.bias_at_dp, 3), "—", "absolute units"])
    if res.repeat.available:
        rows.append(["Within-subject CV (A / B)",
                     f"{_num(res.repeat.cv_a, 2)}% / {_num(res.repeat.cv_b, 2)}%",
                     "—", "repeatability"])
    if res.interchangeable is not None:
        verdict = "interchangeable" if res.interchangeable else "NOT interchangeable"
        rows.append(["Interchangeability", verdict,
                     f"accept [{_num(res.accept_lower, 2)}{u}, "
                     f"{_num(res.accept_upper, 2)}{u}]", "vs 95% LoA"])

    def _mdname(s: str) -> str:
        # names appear only in the H1/paragraph; neutralise markdown-breaking chars
        return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    na, nb = _mdname(res.name_a), _mdname(res.name_b)
    out: List[str] = []
    out.append(f"# agreestat — {na} vs {nb}")
    out.append("")
    out.append(f"paired n = {res.n}"
               + (f" (dropped {res.dropped})" if res.dropped else "")
               + f" · difference = {na} − {nb} · CI level = {lvl}%")
    out.append("")
    out.append(f"| Metric | Estimate | {lvl}% CI | Grade / Note |")
    out.append("|---|---|---|---|")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")

    # Markdown is the paste-into-the-paper path; shipping the table without the
    # warnings would strip exactly the caveats the reader needs (proportional
    # bias, repeated measures, zero variance, degenerate CIs).
    if res.warnings:
        out.append("")
        out.append("## 주의 / Warnings")
        out.append("")
        for w in res.warnings:
            out.append(f"- {_mdname(w)}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Plot data (CSV) — mean, diff per point + summary lines, tool-agnostic
# --------------------------------------------------------------------------
def render_plot_data(res: AnalysisResult) -> str:
    ba = res.ba
    lines: List[str] = []
    summary = (f"# agreestat plot data | bias={_num(ba.bias, 6)} "
               f"loa_lower={_num(ba.loa_lower, 6)} loa_upper={_num(ba.loa_upper, 6)}")
    if res.accept_lower is not None:
        summary += (f" accept_lower={_num(res.accept_lower, 6)} "
                    f"accept_upper={_num(res.accept_upper, 6)}")
    if ba.unit:
        summary += f" unit={ba.unit}"
    lines.append(summary)
    lines.append("mean,diff,outside_loa")
    for m, d in zip(ba.means, ba.diffs):
        outside = 1 if (d < ba.loa_lower or d > ba.loa_upper) else 0
        lines.append(f"{m:.6g},{d:.6g},{outside}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Bland–Altman plot (SVG) — pure stdlib string templating, no matplotlib
# --------------------------------------------------------------------------
def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def render_svg(res: AnalysisResult) -> str:
    ba = res.ba
    W, H = 720, 480
    ml, mr, mt, mb = 70, 30, 40, 60
    pw, ph = W - ml - mr, H - mt - mb
    means, diffs = ba.means, ba.diffs
    if not means:
        means, diffs = [0.0], [0.0]
    xmin, xmax = min(means), max(means)
    if xmax == xmin:
        xmin, xmax = xmin - 1.0, xmax + 1.0
    ys = list(diffs) + [ba.loa_lower, ba.loa_upper, ba.bias]
    if res.accept_lower is not None:
        ys += [res.accept_lower, res.accept_upper]
    ymin, ymax = min(ys), max(ys)
    if ymax == ymin:
        ymin, ymax = ymin - 1.0, ymax + 1.0
    ypad = 0.05 * (ymax - ymin)
    ymin -= ypad
    ymax += ypad

    def sx(x):
        return ml + (x - xmin) / (xmax - xmin) * pw

    def sy(y):
        return mt + (ymax - y) / (ymax - ymin) * ph

    u = ba.unit
    title = _xml_escape(f"Bland–Altman: {res.name_a} vs {res.name_b}")
    e: List[str] = []
    e.append(f'<?xml version="1.0" encoding="UTF-8"?>')
    e.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">')
    e.append(f'<rect width="{W}" height="{H}" fill="white"/>')
    e.append(f'<text x="{W/2:.0f}" y="24" text-anchor="middle" font-size="15">'
             f'{title}</text>')
    # acceptance band
    if res.accept_lower is not None:
        y1, y2 = sy(res.accept_upper), sy(res.accept_lower)
        e.append(f'<rect x="{ml}" y="{y1:.1f}" width="{pw}" height="{y2-y1:.1f}" '
                 f'fill="#2ca02c" fill-opacity="0.08"/>')
    # axes
    e.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#000"/>')
    e.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#000"/>')
    # ticks (5 each)
    for i in range(5):
        xv = xmin + (xmax - xmin) * i / 4
        xp = sx(xv)
        e.append(f'<line x1="{xp:.1f}" y1="{mt+ph}" x2="{xp:.1f}" y2="{mt+ph+5}" '
                 f'stroke="#000"/>')
        e.append(f'<text x="{xp:.1f}" y="{mt+ph+18}" text-anchor="middle">'
                 f'{xv:.3g}</text>')
        yv = ymin + (ymax - ymin) * i / 4
        yp = sy(yv)
        e.append(f'<line x1="{ml-5}" y1="{yp:.1f}" x2="{ml}" y2="{yp:.1f}" '
                 f'stroke="#000"/>')
        e.append(f'<text x="{ml-8}" y="{yp+4:.1f}" text-anchor="end">'
                 f'{yv:.3g}</text>')
    # scatter
    for m, dv in zip(means, diffs):
        e.append(f'<circle cx="{sx(m):.1f}" cy="{sy(dv):.1f}" r="3" fill="#333" '
                 f'fill-opacity="0.6"/>')
    # bias + LoA lines
    e.append(f'<line x1="{ml}" y1="{sy(ba.bias):.1f}" x2="{ml+pw}" '
             f'y2="{sy(ba.bias):.1f}" stroke="#1f77b4" stroke-width="1.5"/>')
    for val, lab in ((ba.loa_upper, "+1.96 SD"), (ba.loa_lower, "−1.96 SD")):
        e.append(f'<line x1="{ml}" y1="{sy(val):.1f}" x2="{ml+pw}" '
                 f'y2="{sy(val):.1f}" stroke="#d62728" stroke-dasharray="6 4"/>')
        e.append(f'<text x="{ml+pw-4}" y="{sy(val)-4:.1f}" text-anchor="end" '
                 f'fill="#d62728">{lab} ({val:.3g}{u})</text>')
    # axis titles
    e.append(f'<text x="{ml+pw/2:.0f}" y="{H-18}" text-anchor="middle">'
             f'mean of {_xml_escape(res.name_a)} and {_xml_escape(res.name_b)}'
             f'{" (%)" if u else ""}</text>')
    e.append(f'<text x="20" y="{mt+ph/2:.0f}" text-anchor="middle" '
             f'transform="rotate(-90 20 {mt+ph/2:.0f})">difference '
             f'({_xml_escape(res.name_a)} − {_xml_escape(res.name_b)}){u}</text>')
    e.append('</svg>')
    return "\n".join(e) + "\n"
