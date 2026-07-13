"""Human-readable (Korean + English) report and JSON rendering."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .analyze import AnalysisResult

__all__ = ["render_text", "render_json"]


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


def _ci(lo: float, hi: float, level: int, d: int = 3) -> str:
    return f"[{level}% CI {_num(lo, d)}, {_num(hi, d)}]"


def render_text(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    lvl = int(round((1 - res.alpha) * 100))
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
    L(f"    bias (평균차) = {_num(ba.bias)}{u}  {_ci(*ba.bias_ci, lvl)}")
    L(f"    SD of differences = {_num(ba.sd_diff)}{u}")
    L(f"    95% 일치한계 LoA = [{_num(ba.loa_lower)}{u}, {_num(ba.loa_upper)}{u}]")
    L(f"       lower LoA {_num(ba.loa_lower)}{u}  {_ci(*ba.loa_lower_ci, lvl)}")
    L(f"       upper LoA {_num(ba.loa_upper)}{u}  {_ci(*ba.loa_upper_ci, lvl)}")
    if ba.prop_pvalue == ba.prop_pvalue:  # not NaN
        verdict = "비례 편향 있음 ⚠" if ba.prop_bias else "비례 편향 없음"
        L(f"    비례 편향 검정 (diff ~ mean): slope={_num(ba.prop_slope, 4)}, "
          f"p={_p(ba.prop_pvalue)}  → {verdict}")
    else:
        L("    비례 편향 검정: 판정 불가 (측정값 평균의 분산이 0)")

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
    L("    해석 기준(Koo & Li 2016): <0.5 낮음 / 0.5–0.75 보통 / "
      "0.75–0.9 좋음 / >0.9 매우 좋음")

    # [4] CCC
    c = res.ccc
    L("")
    L("[4] Lin's CCC (일치상관계수 / concordance correlation)")
    L(f"    CCC = {_num(c.value)}  {_ci(c.ci_lower, c.ci_upper, lvl)}  "
      f"({c.interpretation})")
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


def _sentence(res: AnalysisResult) -> str:
    ba = res.ba
    lvl = int(round((1 - res.alpha) * 100))
    u = ba.unit
    icc_r = res.icc21
    c = res.ccc
    parts: List[str] = []
    parts.append(
        f"'{res.name_a}'와 '{res.name_b}'의 일치도를 Bland–Altman 분석으로 "
        f"평가한 결과, 평균 편향(bias)은 {_num(ba.bias, 2)}{u}"
        f"({lvl}% CI {_num(ba.bias_ci[0], 2)}~{_num(ba.bias_ci[1], 2)})이었고, "
        f"95% 일치한계(LoA)는 {_num(ba.loa_lower, 2)}{u}에서 "
        f"{_num(ba.loa_upper, 2)}{u}였다."
    )
    if icc_r.value == icc_r.value:
        grade = icc_r.interpretation.split(" / ")[-1]
        parts.append(
            f" ICC(2,1)은 {_num(icc_r.value, 3)}"
            f"({lvl}% CI {_num(icc_r.ci_lower, 3)}~{_num(icc_r.ci_upper, 3)})로 "
            f"'{grade}' 수준이었다."
        )
    if c.value == c.value:
        parts.append(
            f" Lin의 CCC는 {_num(c.value, 3)}"
            f"({lvl}% CI {_num(c.ci_lower, 3)}~{_num(c.ci_upper, 3)})였다."
        )
    if ba.prop_bias:
        parts.append(" 다만 비례 편향이 유의하여(차이가 측정값 크기에 의존) "
                     "단일 LoA 해석에는 주의가 필요하다.")
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
        },
        "icc": {
            "reported": res.reported_icc,
            "icc_2_1": _icc_json(res.icc21),
            "icc_3_1": _icc_json(res.icc31),
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
        "warnings": res.warnings,
    }
    return json.dumps(d, ensure_ascii=False, indent=2)


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
