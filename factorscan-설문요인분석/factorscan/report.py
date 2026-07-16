"""분석 결과 딕셔너리를 사람이 읽는 텍스트 보고서로 렌더링."""
from __future__ import annotations

import csv
import io
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


_CSV_PRECISION = 4


def _num(v):
    """CSV 셀용: None/NaN은 빈칸, 유한 실수는 지정 소수자리로 반올림."""
    if v is None:
        return ""
    if isinstance(v, float):
        if not np.isfinite(v):
            return ""
        return round(v, _CSV_PRECISION)
    return v


def _safe_text(s):
    """CSV 수식 인젝션 방어: =,+,-,@ 또는 제어문자로 시작하는 셀은 앞에 작은따옴표를 붙인다.

    이 CSV는 '엑셀에서 바로 열기'를 권장하므로, 문항명이 '=SUM(...)' 같으면 엑셀이 수식으로
    실행할 수 있다. OWASP 권고대로 위험 접두문자를 무력화한다(값 자체는 보존).
    """
    if isinstance(s, str) and s and s[0] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + s
    return s


def loadings_table_csv(res: Dict) -> str:
    """문항×요인 적재표 + 요약행을 논문 부록에 붙이기 좋은 tidy CSV 문자열로 반환.

    문항 행 열: item, F1..Fk(적재), communality, msa, item_total_by_factor,
    item_total_overall, primary_factor, problems(플래그를 '; '로 결합).
    이어서 요약 행(빈 줄 뒤): 적재제곱합(SS)·설명분산%·누적%·McDonald ω·Cronbach α,
    그리고 사교회전이면 요인 상관행렬 Φ까지 담아 표 하나로 완결되게 한다.
    한국어 엑셀 호환을 위해 utf-8-sig(BOM)로 저장하도록 CLI에서 인코딩한다.
    """
    n = len(res["items"])
    kf = res["n_factors"]
    load = res["loadings"]
    comm = res["communalities"]
    itf = res.get("item_total_by_factor") or res.get("item_total") or [None] * n
    ito = res.get("item_total_overall") or [None] * n
    kmo = res.get("kmo")
    msa = kmo["per_item"] if kmo else [None] * n
    flags = {f["item"]: f for f in res["item_flags"]}

    buf = io.StringIO()
    w = csv.writer(buf)
    header = (["item"] + [f"F{j+1}" for j in range(kf)]
              + ["communality", "msa", "item_total_by_factor",
                 "item_total_overall", "primary_factor", "problems"])
    w.writerow(header)
    for i, name in enumerate(res["items"]):
        fl = flags.get(name, {})
        row = [_safe_text(name)]
        row += [_num(load[i][j]) for j in range(kf)]
        row += [_num(comm[i]), _num(msa[i]), _num(itf[i]), _num(ito[i]),
                fl.get("primary_factor", ""), _safe_text("; ".join(fl.get("problems", [])))]
        w.writerow(row)

    # --- 요약 행(논문 표 footer) ---
    ss = res.get("ss_loadings")
    ssv = res.get("ss_prop_variance")
    if ss and ssv:
        w.writerow([])
        w.writerow(["_SS_loadings"] + [_num(ss[j]) for j in range(kf)])
        w.writerow(["_pct_variance"] + [_num(ssv[j] * 100.0) for j in range(kf)])
        cum = 0.0
        cum_row = []
        for j in range(kf):
            cum += ssv[j] * 100.0
            cum_row.append(_num(cum))
        w.writerow(["_cumulative_pct"] + cum_row)
    om = res.get("omega")
    if om:
        w.writerow(["_omega"] + [_num(om[j]) if j < len(om) else "" for j in range(kf)])
    al = res.get("alpha")
    if al:
        w.writerow(["_cronbach_alpha"] + [_num(al[j]) if j < len(al) else "" for j in range(kf)])
    fc = res.get("factor_correlation")
    if fc and kf >= 2:
        w.writerow([])
        w.writerow(["_factor_correlation"] + [f"F{j+1}" for j in range(kf)])
        for i in range(kf):
            w.writerow([f"F{i+1}"] + [_num(fc[i][j]) for j in range(kf)])
    return buf.getvalue()


def scores_table_csv(res: Dict, matrix, id_pairs, method: str = "sum",
                     row_numbers=None) -> str:
    """응답자별 하위척도(요인) 점수 CSV. 요인분석에 실제 쓰인 결측제거 표본만 포함.

    id_pairs: [(열이름, 값리스트), ...] — 결측제거 후 살아남은 행에 정렬된 ID 열.
              비어 있으면 row_numbers(원자료 1-based 행번호)를, 그것도 없으면 순번을 쓴다.
    row_numbers: 결측제거 후 남은 행의 '원본 CSV 행 번호'(1-based). ID가 없을 때 원자료로
              역추적할 수 있게 한다(결측 삭제로 순번이 어긋나는 문제 방지).
    matrix: 결측제거·역문항 반영된 (n, p) 행렬. 요인 배정은 |적재|최대(argmax).
    각 요인 점수 = 소속 문항의 합(method="sum") 또는 평균("mean").
    """
    from . import efa
    L = np.array(res["loadings"])
    kf = res["n_factors"]
    groups = np.argmax(np.abs(L), axis=1)
    scores = efa.subscale_scores(matrix, groups, kf, method=method)
    counts = [int(np.sum(groups == f)) for f in range(kf)]

    buf = io.StringIO()
    w = csv.writer(buf)
    id_names = [nm for nm, _ in id_pairs]
    suffix = "_sum" if method == "sum" else "_mean"
    factor_cols = [f"F{j+1}{suffix}({counts[j]}문항)" for j in range(kf)]
    # ID가 없으면 원본 행번호(row) 열. row_numbers가 있으면 원자료 기준(결측삭제로 어긋남 방지).
    id_header = [_safe_text(nm) for nm in id_names] or ["row"]
    w.writerow(id_header + factor_cols)
    n = scores.shape[0]
    for i in range(n):
        if id_pairs:
            idvals = [_safe_text(vals[i]) for _, vals in id_pairs]
        elif row_numbers is not None:
            idvals = [int(row_numbers[i])]
        else:
            idvals = [i + 1]
        w.writerow(idvals + [_num(scores[i][j]) for j in range(kf)])
    return buf.getvalue()


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

    if res.get("correlation") == "polychoric":
        A("  상관행렬: 폴리코릭(polychoric, 순서형 잠재상관)")

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
    if res.get("map_k") is not None:
        mv = res.get("map_values")
        extra = ""
        if mv and 0 <= res["map_k"] < len(mv) and mv[res["map_k"]] is not None \
                and np.isfinite(mv[res["map_k"]]):
            extra = f" (최소 평균편상관²={mv[res['map_k']]:.4f})"
        A(f"  → Velicer MAP 기준(최소평균편상관): {res['map_k']}개 요인{extra}")
    src = {"user": "사용자 지정", "parallel": "평행분석 기준", "kaiser": "Kaiser 기준"}.get(
        res["k_source"], res["k_source"])
    A(f"  → 적용한 요인 수: {res['n_factors']}개 ({src})")

    # --- 적재량 ---
    A("")
    rot = {"varimax": "Varimax 회전 후", "promax": "Promax 사교회전 후"}.get(
        res["rotation"], "비회전")
    extr = {"principal_component": "주성분(PCA)",
            "principal_axis": "주축분해(PAF)"}.get(res.get("extraction"),
                                                   res.get("extraction", "주성분(PCA)"))
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
    A(f"  유지 요인 누적 설명분산%: {sum(ssv[:kf])*100:.1f}%")

    om = res.get("omega")
    if om:
        A("  요인별 신뢰도(McDonald ω): " + "  ".join(
            (f"F{j+1}={om[j]:.3f}" if j < len(om) and om[j] is not None else f"F{j+1}=—")
            for j in range(kf)))
    al = res.get("alpha")
    if al:
        A("  요인별 신뢰도(Cronbach α, 응답분산기반·추출방식 무관): " + "  ".join(
            (f"F{j+1}={al[j]:.3f}" if j < len(al) and al[j] is not None else f"F{j+1}=—")
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
    is_paf = res.get("extraction") == "principal_axis"
    extr_name = "주축분해(PAF, 공통요인)" if is_paf else "주성분(PCA, SPSS 기본)"
    A(f"추출은 {extr_name}. '요인총점'·요인별 ω/α는 문항을 |적재|최대 요인에 배정(argmax)해 계산하므로")
    A("교차·저적재 문항은 배정이 불안정합니다(참고용). 전체합 기준 문항-총점은 JSON item_total_overall 참고.")
    if is_paf:
        A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다. PAF는 공통분산만 모형화하므로 ω/RMSR이 PCA보다")
        A("덜 낙관적입니다. ω(적재기반)와 Cronbach α(응답분산기반)를 함께 보고하세요(대략 ≥0.70 권장).")
    else:
        A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다(PCA라 다소 큼). ω는 PCA 기반 근사로 공통요인 ω·α보다")
        A("높게(낙관적) 나오는 경향이 있으니 'PCA 기반 ω(근사)'로 보고하세요(대략 ≥0.70 권장). 공통요인 추출은 --extraction paf.")
    return "\n".join(L)
