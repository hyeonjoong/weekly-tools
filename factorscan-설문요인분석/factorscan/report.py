"""분석 결과 딕셔너리를 사람이 읽는 텍스트 보고서로 렌더링."""
from __future__ import annotations

import unicodedata
from typing import Dict, List

import numpy as np


def _dwidth(s: str) -> int:
    """터미널 표시 폭(한글 등 전각 문자는 2로 계산)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    """표시 폭 기준 좌측정렬 패딩(모노스페이스 열 정렬용)."""
    return s + " " * max(0, width - _dwidth(s))


def _truncate(s: str, width: int) -> str:
    """표시 폭이 width를 넘으면 '..'을 붙여 자른다."""
    if _dwidth(s) <= width:
        return s
    out = ""
    for ch in s:
        if _dwidth(out) + _dwidth(ch) > width - 2:
            break
        out += ch
    return out + ".."


def _kmo_verdict(v: float) -> str:
    if v >= 0.9:
        return "매우 우수(marvelous)"
    if v >= 0.8:
        return "우수(meritorious)"
    if v >= 0.7:
        return "양호(middling)"
    if v >= 0.6:
        return "보통(mediocre)"
    if v >= 0.5:
        return "미흡(miserable)"
    return "부적합(unacceptable)"


def _fmt(v, nd=3, width=7):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return f"{'—':>{width}}"
    return f"{v:>{width}.{nd}f}"


def render(res: Dict) -> str:
    L: List[str] = []
    A = L.append
    bar = "=" * 66

    A(bar)
    A("  factorscan — 설문 척도 요인분석·타당도 진단")
    A(bar)
    A(f"문항 수: {res['n_items']}    응답자: {res['n_used']}명 사용"
      f" (전체 {res['n_total']}, 결측제거 {res['n_dropped']})")

    for w in res.get("warnings", []):
        A(f"  ⚠ {w}")

    # --- 요인분석 적합성 ---
    A("")
    A("[ 1. 요인분석 적합성 ]")
    b = res.get("bartlett")
    if b:
        sig = "유의(적합)" if b["p_value"] < 0.05 else "비유의(주의)"
        A(f"  Bartlett 구형성 검정: χ²({b['df']}) = {b['chi_square']:.2f}, "
          f"p = {b['p_value']:.4g}  → {sig}")
    else:
        A("  Bartlett 구형성 검정: 계산 불가(특이행렬)")
    k = res.get("kmo")
    if k:
        A(f"  KMO 전체: {k['overall']:.3f}  ({_kmo_verdict(k['overall'])})")
    else:
        A("  KMO: 계산 불가(특이행렬)")

    # --- 차원 수 진단 ---
    A("")
    A("[ 2. 요인(차원) 수 진단 ]")
    ev = res["eigenvalues"]
    pv = res["prop_variance"]
    cv = res["cum_variance"]
    pa = res.get("parallel_eigenvalues")
    A("  요인   고유값   설명분산%   누적%" + ("     평행분석" if pa else ""))
    for i in range(len(ev)):
        line = f"  {i+1:>3}  {_fmt(ev[i])}   {pv[i]*100:6.1f}   {cv[i]*100:6.1f}"
        if pa:
            mark = " ★" if ev[i] > pa[i] else "  "
            line += f"    {pa[i]:6.3f}{mark}"
        A(line)
    A(f"  → Kaiser 기준(고유값>1): {res['kaiser_k']}개 요인")
    if res.get("parallel_k") is not None:
        A(f"  → 평행분석 기준(★): {res['parallel_k']}개 요인")
    src = {"user": "사용자 지정", "parallel": "평행분석 기준", "kaiser": "Kaiser 기준"}.get(
        res["k_source"], res["k_source"])
    A(f"  → 적용한 요인 수: {res['n_factors']}개 ({src})")

    # --- 적재량 ---
    A("")
    rot = {"varimax": "Varimax 회전 후", "promax": "Promax 사교회전 후"}.get(
        res["rotation"], "비회전")
    extr = {"principal_component": "주성분(PCA)"}.get(res.get("extraction"), res.get("extraction", "주성분(PCA)"))
    A(f"[ 3. 요인 적재량 ({rot}, 추출={extr}) · 공통성 · MSA · 문항-총점 ]")
    kf = res["n_factors"]
    header = "  " + _pad("문항", 18) + "".join(f"  F{j+1:<5}" for j in range(kf))
    header += "  공통성    MSA   요인총점"
    A(header)
    load = res["loadings"]
    comm = res["communalities"]
    # 문항-총점은 소속 요인(하위척도) 기준. 구버전 키 호환도 유지.
    it = res.get("item_total_by_factor") or res.get("item_total")
    ml = res.get("min_loading", 0.40)
    flags = {f["item"]: f for f in res["item_flags"]}
    for i, name in enumerate(res["items"]):
        disp = _pad(_truncate(name, 18), 18)
        cells = ""
        for j in range(kf):
            v = load[i][j]
            star = "*" if abs(v) >= ml else " "
            cells += f" {v:>6.3f}{star}"
        fl = flags[name]
        primary = fl["primary_factor"]
        row = (f"  {disp}{cells}  {comm[i]:>6.3f}  "
               f"{_fmt(fl.get('msa'), width=6)}  {_fmt(it[i], width=8)}")
        A(row + f"   →F{primary}")
    ssv = res["ss_prop_variance"]
    A("  " + "-" * 62)
    ss_cells = "".join(f" {res['ss_loadings'][j]:>6.3f} " for j in range(kf))
    A("  " + _pad("적재제곱합(SS)", 18) + ss_cells)
    A(f"  요인별 설명분산%: " + "  ".join(f"F{j+1}={ssv[j]*100:.1f}%" for j in range(kf)))

    om = res.get("omega")
    if om:
        A("  요인별 신뢰도(McDonald ω): " + "  ".join(
            (f"F{j+1}={om[j]:.3f}" if j < len(om) and om[j] is not None else f"F{j+1}=—")
            for j in range(kf)))
    resid = res.get("residual")
    if resid and resid.get("n_resid"):
        th = resid.get("threshold", 0.05)
        A(f"  모형 적합: RMSR={resid['rmsr']:.3f}  "
          f"(|잔차|>{th:.2f} 인 비중복잔차 {resid['n_large']}/{resid['n_resid']}"
          f" = {resid['prop_large']*100:.0f}%)")

    # 요인 간 상관(사교 promax 추정/실제): 직교 가정이 타당한지 판단 근거
    fc = res.get("factor_correlation")
    if fc and kf >= 2:
        label = "promax 실제" if res["rotation"] == "promax" else "promax 추정"
        if res["rotation"] == "promax":
            A("  (위 표는 패턴적재; 공통성·ω·설명분산은 구조행렬 S=PΦ 기준)")
        A(f"  요인 간 상관행렬 ({label}):")
        head = "        " + "".join(f"  F{j+1:<5}" for j in range(kf))
        A(head)
        for i in range(kf):
            cells = "".join(f" {fc[i][j]:>6.3f} " for j in range(kf))
            A(f"    F{i+1}{cells}")

    # --- 문제 문항 ---
    A("")
    A("[ 4. 점검이 필요한 문항 ]")
    problem_items = [f for f in res["item_flags"] if f["problems"]]
    if not problem_items:
        A("  ✓ 임계값 기준으로 눈에 띄는 문제 문항이 없습니다.")
    else:
        for f in problem_items:
            A(f"  • {f['item']}: {', '.join(f['problems'])}"
              f"  (주적재 F{f['primary_factor']}={f['primary_loading']:.3f})")

    for note in res.get("notes", []):
        A(f"  ⚠ {note}")

    A("")
    A(f"해석 도움말: KMO>0.6 · Bartlett p<0.05 이면 요인분석 적합. "
      f"적재량 |{ml:.2f}| 이상(*)을 주요 적재로 봅니다.")
    A("공통성<0.3, 문항-총점<0.3, MSA<0.5, 교차적재 문항은 제거/수정을 검토하세요.")
    A("추출은 주성분(PCA, SPSS 기본). '요인총점'·요인별 ω는 문항을 |적재|최대 요인에 배정(argmax)해 계산하므로")
    A("교차·저적재 문항은 배정이 불안정합니다(참고용). 전체합 기준 문항-총점은 JSON item_total_overall 참고.")
    A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다(PCA라 다소 큼). ω는 PCA 기반 근사로 공통요인 ω·α보다")
    A("높게(낙관적) 나오는 경향이 있으니 'PCA 기반 ω(근사)'로 보고하세요(대략 ≥0.70 권장).")
    return "\n".join(L)
