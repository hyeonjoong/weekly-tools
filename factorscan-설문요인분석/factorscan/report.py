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
        # Φ는 회전과 무관하게 promax 기준 '추정치'다. varimax 해는 정의상 Φ=I 이므로
        # 이름에 근거를 박아 두지 않으면 부록 표에서 직교 적재와 함께 보고되는 사고가 난다.
        label = ("_factor_correlation(promax)" if res.get("rotation") == "promax"
                 else "_factor_correlation(promax_estimate;varimax해는Φ=I)")
        w.writerow([label] + [f"F{j+1}" for j in range(kf)])
        for i in range(kf):
            w.writerow([f"F{i+1}"] + [_num(fc[i][j]) for j in range(kf)])
    return buf.getvalue()


def eigen_table_csv(res: Dict) -> str:
    """스크리 도표용 고유값 표 CSV: factor, eigenvalue, parallel_95th, pct_variance, cum_pct.

    리뷰어가 이름을 대고 요구하는 그림이 "Figure 1. Scree plot with parallel analysis"다.
    이 도구는 그림을 그리지 않지만(matplotlib 의존을 만들지 않으려고), 엑셀에서 바로
    꺾은선으로 만들 수 있는 표를 준다 — 관측 고유값과 평행분석 기준선을 나란히 놓아
    두 선이 교차하는 지점이 곧 유지 요인 수가 되게 했다.
    """
    ev = res["eigenvalues"]
    pa = res.get("parallel_eigenvalues")
    pv = res["prop_variance"]
    cv = res["cum_variance"]
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["factor", "eigenvalue"]
    if pa:
        header.append("parallel_95th")
    header += ["pct_variance", "cum_pct_variance", "retained"]
    w.writerow(header)
    kf = res["n_factors"]
    for i in range(len(ev)):
        row = [i + 1, _num(float(ev[i]))]
        if pa:
            row.append(_num(float(pa[i])))
        row += [_num(pv[i] * 100.0), _num(cv[i] * 100.0), 1 if i < kf else 0]
        w.writerow(row)
    return buf.getvalue()


def scores_table_csv(res: Dict, matrix, id_pairs, method: str = "sum",
                     row_numbers=None) -> str:
    """응답자별 하위척도(요인) 점수 CSV. 요인분석에 실제 쓰인 결측제거 표본만 포함.

    id_pairs: [(열이름, 값리스트), ...] — 결측제거 후 살아남은 행에 정렬된 ID 열.
              비어 있으면 row_numbers(원자료 1-based 행번호)를, 그것도 없으면 순번을 쓴다.
    row_numbers: 결측제거 후 남은 행의 '원본 CSV 행 번호'(1-based). ID가 없을 때 원자료로
              역추적할 수 있게 한다(결측 삭제로 순번이 어긋나는 문제 방지).
    matrix: 결측제거·역문항 반영된 (n, p) 행렬. 요인 배정은 |적재|최대(argmax).
    각 요인 점수 = 소속 문항의 합(method="sum")·평균("mean"), 또는 모든 문항의 적재를
    가중치로 쓰는 Thurstone 회귀 요인점수("regression", 표준화 스케일).
    """
    from . import efa
    L = np.array(res["loadings"])
    kf = res["n_factors"]
    groups = np.argmax(np.abs(L), axis=1)
    counts = [int(np.sum(groups == f)) for f in range(kf)]
    # 합산/평균 점수는 문항을 '같은 방향'으로 더한다고 가정한다. 주적재가 음수인 문항
    # (역문항 미처리)이 섞이면 그 문항이 거꾸로 더해져 점수가 조용히 오염되므로,
    # 파일을 쓰지 않고 원인과 해결책을 알린다. 회귀점수는 가중치가 부호를 품어 안전하다.
    neg = res.get("negative_loading_items") or []
    if neg and method in ("sum", "mean"):
        raise ValueError(
            f"주적재가 음수인 문항({', '.join(neg)})이 있어 합산/평균 점수를 만들 수 없습니다 — "
            f"그대로 더하면 해당 문항이 거꾸로 반영됩니다. 역문항이라면 "
            f"`--reverse {','.join(neg)} --scale-min/--scale-max`로 재점수화한 뒤 다시 실행하거나, "
            f"부호를 가중치에 반영하는 `--score-method regression`을 쓰세요.")

    if method == "regression":
        r = res.get("correlation_matrix")
        if r is None:
            raise ValueError("회귀 요인점수에는 상관행렬(correlation_matrix)이 필요합니다.")
        # 사교(promax) 회전에서만 Φ를 반영한다. 직교회전의 factor_correlation은
        # '추정치(진단용)'일 뿐 실제 해가 아니므로 가중치에 쓰면 안 된다.
        phi = None
        if res.get("rotation") == "promax" and res.get("factor_correlation") is not None:
            phi = np.array(res["factor_correlation"])
        scores = efa.regression_factor_scores(matrix, L, np.asarray(r), phi=phi)
    else:
        scores = efa.subscale_scores(matrix, groups, kf, method=method)

    buf = io.StringIO()
    w = csv.writer(buf)
    id_names = [nm for nm, _ in id_pairs]
    suffix = {"sum": "_sum", "mean": "_mean", "regression": "_reg"}[method]
    if method == "regression":
        # 회귀점수는 전 문항 가중합이라 '몇 문항' 라벨이 오해를 부른다(주적재 문항 수만 참고).
        factor_cols = [f"F{j+1}{suffix}(표준화)" for j in range(kf)]
    else:
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


def _missing_lines(res: Dict) -> List[str]:
    """결측 구조 요약: 문항별 결측률과 listwise 삭제 편향 점검(결측이 있을 때만).

    결측이 하나도 없으면 표를 띄우지 않는다(깨끗한 자료에 잡음을 더하지 않기 위해).
    """
    m = res.get("missing")
    if not m or m.get("n_incomplete", 0) == 0:
        return []
    out: List[str] = ["", "[ 0. 결측 구조 ]"]
    out.append(f"  완전응답 {m['n_complete']}명 · 결측포함 {m['n_incomplete']}명"
               f" (결측포함 응답자는 listwise 삭제됨)")
    items = res["items"]
    per = m["per_item"]
    prop = m["per_item_prop"]
    shown = [(items[i], per[i], prop[i]) for i in range(len(items)) if per[i] > 0]
    shown.sort(key=lambda t: -t[1])
    if shown:
        out.append("  " + _pad("문항", 18) + "  결측수   결측률")
        for name, cnt, pr in shown:
            out.append(f"  {_pad(_truncate(name, 18), 18)}  {cnt:>5}   {pr*100:>5.1f}%")
    bias = m.get("bias_check") or []
    flagged = [b for b in bias if abs(b["d"]) >= 0.2]
    if flagged:
        out.append("  삭제 편향 점검(완전응답자 vs 삭제된 응답자의 평균차, Cohen's d):")
        for b in sorted(flagged, key=lambda b: -abs(b["d"])):
            out.append(f"    • {b['item']}: d={b['d']:+.2f} "
                       f"(완전 {b['mean_complete']:.2f} vs 삭제 {b['mean_dropped']:.2f}, "
                       f"n={b['n_dropped_obs']})")
        out.append("    |d|≥0.2 는 결측이 무작위(MCAR)가 아닐 수 있다는 신호입니다.")
    elif bias:
        out.append("  삭제 편향 점검: 완전응답자와 삭제된 응답자의 응답 분포에 뚜렷한 차이 없음(|d|<0.2).")
    return out


def _descriptive_lines(res: Dict) -> List[str]:
    """문항 기술통계 표(평균·SD·왜도·첨도·바닥/천장) — 척도 논문 Table 1에 그대로 쓴다."""
    desc = res.get("item_descriptives")
    if not desc:
        return []
    out: List[str] = ["", "[ 1-1. 문항 기술통계 ]"]
    out.append("  " + _pad("문항", 18) + "   평균     SD     왜도    첨도   바닥%  천장%")
    for d in desc:
        flag = ""
        if max(d["floor_prop"], d["ceiling_prop"]) > d.get("extreme_threshold", 0.15):
            flag = " ←몰림"
        elif abs(d["skew"]) > 2.0 or abs(d["kurtosis"]) > 7.0:
            flag = " ←치우침"
        out.append(f"  {_pad(_truncate(d['item'], 18), 18)}"
                   f" {d['mean']:>6.2f} {d['sd']:>6.2f} {d['skew']:>7.2f} {d['kurtosis']:>7.2f}"
                   f" {d['floor_prop']*100:>5.1f} {d['ceiling_prop']*100:>5.1f}{flag}")
    ths = {round(d.get("extreme_threshold", 0.15) * 100) for d in desc}
    th_txt = f"{min(ths):.0f}%" if len(ths) == 1 else f"{min(ths):.0f}~{max(ths):.0f}%"
    out.append(f"  (바닥/천장이 기준({th_txt}, 범주 수로 조정)을 넘으면 변별력 부족, "
               f"|왜도|>2·|첨도|>7이면 정규성 가정 위배 신호)")
    return out


def _bootstrap_lines(res: Dict) -> List[str]:
    """부트스트랩 적재 신뢰구간과 요인 수 합의율(--bootstrap 을 준 경우만)."""
    bs = res.get("bootstrap")
    if not bs or not bs.get("n_ok"):
        return []
    kf = res["n_factors"]
    load = res["loadings"]
    lo, hi = bs["loading_lo"], bs["loading_hi"]
    out: List[str] = ["", f"[ 3-2. 부트스트랩 안정성 (재표본 {bs['n_ok']}/{bs['n_boot']}회) ]"]
    out.append("  문항별 주적재의 95% 신뢰구간(Procrustes 정렬 후 백분위):")
    out.append("  " + _pad("문항", 18) + "  주적재   95% CI              폭")
    for i, name in enumerate(res["items"]):
        j = int(np.argmax(np.abs(np.asarray(load[i]))))
        l, h = lo[i][j], hi[i][j]
        width = h - l
        mark = ""
        if l <= 0.0 <= h:
            mark = "  ←0 포함(불안정)"
        elif width > 0.3:
            mark = "  ←넓음"
        out.append(f"  {_pad(_truncate(name, 18), 18)}  {load[i][j]:>6.3f}"
                   f"  [{l:>6.3f}, {h:>6.3f}]  {width:>5.3f}{mark}")
    if bs.get("pa_agreement") is not None:
        pct = bs["pa_agreement"] * 100.0
        dist = ", ".join(f"{k}요인 {v}회" for k, v in bs["k_counts"].items())
        out.append(f"  요인 수 안정성: 평행분석이 재표본의 {pct:.0f}%에서 {kf}개 요인을 지지"
                   f"  (분포: {dist})")
    out.append("  CI가 0을 포함하는 적재는 표본이 바뀌면 부호까지 달라질 수 있습니다(보고 시 주의).")
    return out


def _g(v, nd=3):
    """None/NaN을 '—'로 내는 자유폭 실수 포맷(문장 안에 끼워 쓰는 용도)."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:.{nd}f}"


def _fit_lines(res: Dict) -> List[str]:
    """ML 추출의 공식 적합도지수(χ²·RMSEA·CFI/TLI·AIC/BIC)와 k별 적합도 스캔 표.

    EFA 논문 표에서 리뷰어가 요구하는 지표를 그대로 옮겨 적을 수 있는 형태로 낸다.
    ML이 아니거나 자유도가 없어 검정이 불가하면 그 사실을 명시한다(조용히 비우지 않음).
    """
    fit = res.get("fit")
    if not fit:
        return []
    out: List[str] = ["", "[ 3-1. 모형 적합도 (최대우도 ML) ]"]
    if not fit.get("identified"):
        out.append(f"  자유도 df={fit['df']} (≤0) — 문항 수 대비 요인이 많아 적합도 검정이 불가합니다.")
        return out

    n_used = res.get("n_used") or 0
    chi, df, pv = fit["chi_square"], fit["df"], fit["p_value"]
    # χ² 비유의를 '적합'이라 단정하지 않는다. 표본이 작으면 χ²는 검정력이 없어서
    # 기각하지 못하는 것뿐이며, '기각 안 됨'은 '적합의 증거'가 아니다(귀무가설 수용 오류).
    if pv is not None:
        if pv >= 0.05:
            verdict = ("모형 기각 안 됨" +
                       (" — 단 표본이 작아 검정력이 낮습니다(적합의 증거로 보기 어려움)"
                        if n_used < 200 else "(적합)"))
        else:
            verdict = "모형 기각(부적합 신호)"
        out.append(f"  χ²({df}) = {_g(chi, 2)}, p = {pv:.4g}  → {verdict}")
    else:
        out.append(f"  χ²({df}) = {_g(chi, 2)}")
    ratio = chi / df if df > 0 and chi is not None else None
    if ratio is not None:
        out.append(f"  χ²/df = {_g(ratio, 2)}  (관례: <3 이면 양호)")

    rm = fit.get("rmsea")
    lo, hi = fit.get("rmsea_lo"), fit.get("rmsea_hi")
    ci = f"  90% CI [{_g(lo)}, {_g(hi)}]" if lo is not None and hi is not None else ""
    if rm is not None:
        # 판정은 '점추정'이 아니라 '신뢰구간'으로 읽는다. CI가 .05~.10을 가로지르면
        # 자료가 우수와 미흡을 구분하지 못한다는 뜻이라, 점추정만 보고 '우수'라 하면 안 된다.
        if hi is not None and hi > 0.10 and (lo is None or lo <= 0.08):
            judge = "결론 보류 — 90% CI가 넓어 적합/부적합을 가릴 수 없습니다(표본 부족)"
        elif hi is not None and hi <= 0.05:
            judge = "우수(CI 상한도 ≤.05)"
        elif hi is not None and hi <= 0.08:
            judge = "수용(CI 상한 ≤.08)"
        elif rm <= 0.05:
            judge = "점추정 우수(≤.05)이나 CI 상한 확인 필요"
        elif rm <= 0.08:
            judge = "점추정 수용(≤.08)이나 CI 상한 확인 필요"
        else:
            judge = "미흡(>.08)"
        out.append(f"  RMSEA = {_g(rm)}{ci}  → {judge}")
    if fit.get("p_close") is not None:
        out.append(f"  PCLOSE(근접적합 H0: RMSEA≤.05) p = {fit['p_close']:.4g}")
    if fit.get("cfi") is not None or fit.get("tli") is not None:
        out.append(f"  CFI = {_g(fit.get('cfi'))}   TLI = {_g(fit.get('tli'))}"
                   f"  (관례: ≥.95 우수, ≥.90 수용)")
        tli = fit.get("tli")
        if tli is not None and tli > 1.0:
            # TLI>1은 '아주 좋음'이 아니라 모형이 잡음까지 맞춘 신호(과다모수화)다.
            out.append("    ※ TLI>1은 우수함이 아니라 표본 대비 모형이 과하다는 신호입니다"
                       "(보고 시 1.00으로 절단하는 관례가 있습니다).")
    out.append(f"  AIC = {_g(fit.get('aic'), 2)}   BIC = {_g(fit.get('bic'), 2)}"
               f"  (χ² 기준 상대값 — 요인 수 k 비교에만 사용, 작을수록 좋음)")

    scan = res.get("fit_scan")
    if scan:
        out.append("")
        out.append("  ── 요인 수별 적합도 스캔 (k=1..최대) ──")
        out.append("    k     χ²      df       p     RMSEA     CFI     TLI       AIC       BIC")
        # BIC 최소 k를 표시해 요인 수 선택 근거를 한눈에 준다(수렴 실패/식별 불가 k는 제외).
        ok = [r for r in scan if r.get("bic") is not None and not r.get("error")
              and r.get("converged", True)]
        best = min(ok, key=lambda r: r["bic"])["k"] if ok else None
        for r in scan:
            if r.get("error"):
                out.append(f"  {r['k']:>3}   계산 불가: {_truncate(str(r['error']), 50)}")
                continue
            if not r.get("identified"):
                out.append(f"  {r['k']:>3}   df={r.get('df')} (≤0) — 식별 불가")
                continue
            mark = " ←BIC 최소" if best is not None and r["k"] == best else ""
            note = ""
            if not r.get("converged", True):
                note = " ⚠수렴실패"
            elif r.get("heywood"):
                note = " ⚠Heywood"
            pv_r = r.get("p_value")
            out.append(
                f"  {r['k']:>3} {_g(r.get('chi_square'), 1):>8} {r.get('df'):>5}"
                f" {(f'{pv_r:.3g}' if pv_r is not None else '—'):>8}"
                f" {_g(r.get('rmsea')):>8} {_g(r.get('cfi')):>7} {_g(r.get('tli')):>7}"
                f" {_g(r.get('aic'), 1):>9} {_g(r.get('bic'), 1):>9}{mark}{note}")
        out.append("    해석: χ² p≥.05 가 되는 '가장 작은 k', 또는 BIC/RMSEA가 최소인 k를 요인 수 근거로 씁니다.")
    return out


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

    # --- 결측 구조(결측이 있을 때만) ---
    for line in _missing_lines(res):
        A(line)

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

    # --- 문항 기술통계 ---
    for line in _descriptive_lines(res):
        A(line)

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
            "principal_axis": "주축분해(PAF)",
            "maximum_likelihood": "최대우도(ML)"}.get(res.get("extraction"),
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
        # PCA 적재로 계산한 ω는 McDonald's ω가 아니다(공통성을 과대추정해 낙관적).
        # 표에 'McDonald ω'라 적으면 그 문자열이 그대로 논문에 복사되므로, 추출 방식에
        # 따라 이름 자체를 바꾼다 — 각주 고지는 잘못된 라벨을 취소하지 못한다.
        is_common = res.get("extraction") in ("principal_axis", "maximum_likelihood")
        om_label = "요인별 신뢰도(McDonald ω)" if is_common else "요인별 합성신뢰도(ω, PCA 근사·낙관적)"
        A(f"  {om_label}: " + "  ".join(
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

    # --- 부트스트랩 안정성 ---
    for line in _bootstrap_lines(res):
        A(line)

    # --- 모형 적합도(ML 전용) ---
    for line in _fit_lines(res):
        A(line)

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
    is_ml = res.get("extraction") == "maximum_likelihood"
    extr_name = ("최대우도(ML, 공통요인·적합도지수)" if is_ml else
                 "주축분해(PAF, 공통요인)" if is_paf else "주성분(PCA, SPSS 기본)")
    A(f"추출은 {extr_name}. '요인총점'·요인별 ω/α는 문항을 |적재|최대 요인에 배정(argmax)해 계산하므로")
    A("교차·저적재 문항은 배정이 불안정합니다(참고용). 전체합 기준 문항-총점은 JSON item_total_overall 참고.")
    if is_ml:
        A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다. ML은 확률모형이라 [3-1]의 χ²/RMSEA/CFI/TLI를")
        A("논문 표에 그대로 보고할 수 있습니다(χ²는 표본이 크면 쉽게 유의해지므로 RMSEA·CFI를 함께 보세요).")
        A("ω(적재기반)와 Cronbach α(응답분산기반)를 함께 보고하세요(대략 ≥0.70 권장).")
    elif is_paf:
        A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다. PAF는 공통분산만 모형화하므로 ω/RMSR이 PCA보다")
        A("덜 낙관적입니다. ω(적재기반)와 Cronbach α(응답분산기반)를 함께 보고하세요(대략 ≥0.70 권장).")
    else:
        A("RMSR은 작을수록(대략 <0.08) 적합이 좋습니다(PCA라 다소 큼). ω는 PCA 기반 근사로 공통요인 ω·α보다")
        A("높게(낙관적) 나오는 경향이 있으니 'PCA 기반 ω(근사)'로 보고하세요(대략 ≥0.70 권장). 공통요인 추출은 --extraction paf.")
    return "\n".join(L)
