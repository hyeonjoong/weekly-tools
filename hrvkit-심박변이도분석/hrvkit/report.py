"""사람이 읽는 HRV 리포트 렌더링 (한국어 + 영어 라벨).

단일 파일 리포트(render_text) 외에 여러 파일을 비교/일괄 처리하는
render_comparison(기저 대비 개입)과 render_batch_table/metrics_to_csv를 제공합니다.
"""

from __future__ import annotations

import csv
import io
import math
import os
from typing import List, Sequence

from .analyze import FLAT_COLUMNS, HRVResult, flat_metrics
from .stats import paired_summary

__all__ = ["render_text", "render_comparison", "render_batch_table",
           "metrics_to_csv", "paired_group", "render_paired_group"]


def _num(x, d: int = 2) -> str:
    if x is None:
        return "—"
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if xf != xf:            # NaN
        return "NaN"
    if math.isinf(xf):
        return "∞" if xf > 0 else "-∞"
    return f"{xf:.{d}f}"


def render_text(res: HRVResult) -> str:
    lines: List[str] = []
    L = lines.append
    t = res.time
    f = res.freq
    p = res.poincare

    L("=" * 66)
    L("  hrvkit — 심박변이도(HRV) 분석 리포트 / HRV analysis report")
    L("=" * 66)

    # [0] 입력 & 전처리
    L("")
    L("[0] 입력 / Input")
    if res.source:
        L(f"    파일 source        : {res.source}")
    L(f"    단위 unit          : {res.unit}")
    L(f"    입력 박동 beats     : {res.n_input}")
    L(f"    이상박동 artifacts  : {res.n_artifacts} "
      f"({_num(res.pct_artifacts, 1)}%)  → 보정: {res.clean_method}")

    # [1] 시간영역
    L("")
    L("[1] 시간영역 / Time-domain")
    L(f"    평균 RR mean RR    : {_num(t['mean_nn'], 1)} ms"
      f"   (평균 HR {_num(t['mean_hr'], 1)} bpm)")
    L(f"    중앙값 median RR   : {_num(t.get('median_nn'), 1)} ms"
      f"   (MAD {_num(t.get('mad_nn'), 1)} ms, 로버스트)")
    L(f"    SDNN               : {_num(t['sdnn'], 2)} ms   (전체 변동성)")
    L(f"    RMSSD              : {_num(t['rmssd'], 2)} ms   (단기·부교감)")
    L(f"    SDSD               : {_num(t['sdsd'], 2)} ms")
    L(f"    pNN50 / pNN20      : {_num(t['pnn50'], 1)}% / {_num(t['pnn20'], 1)}%")
    L(f"    CVNN               : {_num(t['cvnn'], 4)}   (= SDNN/meanRR)")
    L(f"    HRV triangular idx : {_num(t.get('hti'), 2)}   "
      f"(TINN {_num(t.get('tinn'), 1)} ms, 기하학·이상값에 강건)")
    L(f"    HR 범위 min–max    : {_num(t['min_hr'], 1)} – {_num(t['max_hr'], 1)} bpm")

    # [2] 주파수영역
    L("")
    L("[2] 주파수영역 / Frequency-domain")
    if f.get("n_resampled"):
        L(f"    방법 method        : {_num(f['resample_fs'], 0)} Hz 선형 리샘플 → "
          f"Welch PSD (Hann, nperseg={int(f['welch_nperseg'])}, 50% overlap, "
          f"radix-2 FFT, {int(f['welch_segments'])} segments)")
        L(f"    기록 길이 duration : {_num(f['duration_sec'], 1)} s "
          f"({int(f['n_resampled'])} samples)")
    L(f"    VLF power          : {_num(f['vlf_power'], 1)} ms²  "
      f"({_num(f['vlf_pct'], 1)}%)")
    L(f"    LF  power          : {_num(f['lf_power'], 1)} ms²  "
      f"({_num(f['lf_pct'], 1)}%,  {_num(f['lf_nu'], 1)} n.u.)")
    L(f"    HF  power          : {_num(f['hf_power'], 1)} ms²  "
      f"({_num(f['hf_pct'], 1)}%,  {_num(f['hf_nu'], 1)} n.u.)")
    L(f"    Total power        : {_num(f['total_power'], 1)} ms²")
    L(f"    LF/HF ratio        : {_num(f['lf_hf_ratio'], 2)}"
      f"   (ln HF {_num(f.get('ln_hf'), 3)})")
    if f.get("peak_lf") is not None or f.get("peak_hf") is not None:
        L(f"    peak LF / HF       : {_num(f.get('peak_lf'), 3)} / "
          f"{_num(f.get('peak_hf'), 3)} Hz")
    if f.get("resp_rate_brpm") is not None:
        band = f.get("resp_source") or "HF"
        note = "느린/공명 호흡 레짐: HF n.u./LF-HF 방향 무시" if \
            f.get("slow_breathing_regime") else "자발 호흡(HF RSA)"
        L(f"    호흡수 est. resp   : {_num(f.get('resp_rate_brpm'), 1)} 회/분 "
          f"({band} 피크 {_num(f.get('resp_rate_hz'), 3)} Hz; {note})")

    # [3] 비선형
    L("")
    L("[3] 비선형 / Nonlinear (Poincaré + SampEn + DFA)")
    L(f"    SD1                : {_num(p['sd1'], 2)} ms   (단기·부교감)")
    L(f"    SD2                : {_num(p['sd2'], 2)} ms   (장기)")
    L(f"    SD1/SD2            : {_num(p['sd1_sd2_ratio'], 3)}")
    L(f"    ellipse area       : {_num(p['ellipse_area'], 1)} ms²")
    L(f"    SampEn (m=2)       : {_num(res.sampen, 3)}   (복잡성/규칙성)")
    d = res.dfa or {}
    L(f"    DFA α1 / α2        : {_num(d.get('dfa_alpha1'), 3)} / "
      f"{_num(d.get('dfa_alpha2'), 3)}   (단기/장기 분형 상관; 참고: 안정·자발호흡 "
      "성인 α1≈1.0, 느린·공명 호흡에서 낮아짐)")

    # [4] 해석
    L("")
    L("[4] 해석 / Interpretation")
    L("    " + res.takeaway)

    # 경고
    if res.warnings:
        L("")
        L("[!] 주의 / Warnings")
        for w in res.warnings:
            L(f"    - {w}")

    L("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CSV / 배치 / 비교 출력
# --------------------------------------------------------------------------- #
def _fmt_cell(value, digits) -> str:
    """CSV 셀 포맷 — None→빈칸, NaN→'NaN', inf→'inf', 그 외 지정 자릿수."""
    if value is None:
        return ""
    if digits is None:
        return str(value)
    try:
        xf = float(value)
    except (TypeError, ValueError):
        return str(value)
    if xf != xf:
        return "NaN"
    if math.isinf(xf):
        return "inf" if xf > 0 else "-inf"
    return f"{xf:.{digits}f}"


def metrics_to_csv(results: Sequence[HRVResult]) -> str:
    """HRVResult들을 한 행씩 담은 CSV 문자열(헤더 포함)로 직렬화.

    열은 analyze.FLAT_COLUMNS 순서. 파일 1개든 여러 개든 동일 스키마.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([k for k, _ in FLAT_COLUMNS])
    for res in results:
        flat = flat_metrics(res)
        writer.writerow([_fmt_cell(flat.get(k), d) for k, d in FLAT_COLUMNS])
    return buf.getvalue()


# 배치 텍스트 표에 보여줄 압축 열 (key, 헤더, 자릿수).
_BATCH_COLS = [
    ("n_input", "n", 0),
    ("pct_artifacts", "art%", 1),
    ("mean_hr", "HR", 1),
    ("rmssd", "RMSSD", 1),
    ("sdnn", "SDNN", 1),
    ("hf_nu", "HF_nu", 1),
    ("lf_hf_ratio", "LF/HF", 2),
    ("sd1", "SD1", 1),
    ("sampen", "SampEn", 2),
    ("dfa_alpha1", "α1", 2),
]


def render_batch_table(results: Sequence[HRVResult]) -> str:
    """여러 파일의 핵심 지표를 정렬된 텍스트 표로 렌더링."""
    lines: List[str] = []
    L = lines.append
    L("=" * 78)
    L("  hrvkit — 일괄 요약 / Batch summary (파일당 한 행)")
    L("=" * 78)

    label_w = max([len("file")] +
                  [len(os.path.basename(r.source or "?")) for r in results])
    header = "  " + "file".ljust(label_w)
    for _, name, _ in _BATCH_COLS:
        header += "  " + name.rjust(8)
    L(header)
    L("  " + "-" * (label_w + len(_BATCH_COLS) * 10))

    for res in results:
        flat = flat_metrics(res)
        row = "  " + os.path.basename(res.source or "?").ljust(label_w)
        for key, _, d in _BATCH_COLS:
            row += "  " + _fmt_cell(flat.get(key), d).rjust(8)
        L(row)
    L("")
    return "\n".join(lines)


# 비교 리포트에 보여줄 지표 (key, 라벨, 자릿수, 부교감↑ 방향, HF기반 여부).
# 방향: +1이면 클수록 부교감 우세, -1이면 작을수록 부교감 우세, 0이면 방향 해석 없음.
# HF기반=True 인 행은 느린/공명 호흡 레짐에서 방향이 역전되므로 집계에서 제외.
_COMPARE_ROWS = [
    ("mean_hr", "mean HR (bpm)", 1, -1, False),
    ("rmssd", "RMSSD (ms)", 1, +1, False),
    ("sdnn", "SDNN (ms)", 1, +1, False),
    ("pnn50", "pNN50 (%)", 1, +1, False),
    ("hti", "HRV tri. index", 2, +1, False),
    ("hf_power", "HF power (ms²)", 1, +1, True),
    ("hf_nu", "HF (n.u.)", 1, +1, True),
    ("lf_hf_ratio", "LF/HF", 3, -1, True),
    ("resp_rate_brpm", "resp (br/min)", 1, 0, False),
    ("sd1", "SD1 (ms)", 2, +1, False),
    ("sampen", "SampEn", 3, 0, False),
    ("dfa_alpha1", "DFA α1", 3, 0, False),
]


def render_comparison(baseline: HRVResult, intervention: HRVResult,
                      base_label: str = "baseline",
                      interv_label: str = "intervention") -> str:
    """두 기록(기저 대 개입)의 지표 델타·변화율·부교감 방향을 표로 렌더링.

    두 기록 중 하나라도 느린/공명 호흡 레짐(호흡 피크가 LF)이면 HF 기반 지표
    (HF power·HF n.u.·LF/HF)의 방향은 역전되어 신뢰할 수 없으므로 집계에서
    제외하고, 시간영역 vagal 지표(RMSSD·SD1·pNN50·HTI)로 결론을 냅니다.
    """
    b = flat_metrics(baseline)
    v = flat_metrics(intervention)
    slow = bool(baseline.freq.get("slow_breathing_regime") or
                intervention.freq.get("slow_breathing_regime"))
    lines: List[str] = []
    L = lines.append
    L("=" * 78)
    L("  hrvkit — 짝지은 비교 / Paired comparison")
    L("=" * 78)
    L(f"  기저 {base_label:<12}: {baseline.source or '?'}")
    L(f"  개입 {interv_label:<12}: {intervention.source or '?'}")
    if slow:
        L("  ※ 느린/공명 호흡 레짐 감지 → HF 기반 지표(HF·HF n.u.·LF/HF)는 방향 집계 "
          "제외(‘레짐?’)")
    L("")
    L(f"  {'metric':<16}{base_label:>12}{interv_label:>14}"
      f"{'Δ':>11}{'%Δ':>9}  방향")
    L("  " + "-" * 74)

    para_hits = 0
    para_total = 0
    for key, label, d, direction, hf_based in _COMPARE_ROWS:
        bx, vx = b.get(key), v.get(key)
        b_s = _num(bx, d)
        v_s = _num(vx, d)
        delta_s = pct_s = arrow = ""
        counts = direction != 0 and not (hf_based and slow)
        if _finite(bx) and _finite(vx):
            delta = float(vx) - float(bx)
            delta_s = _num(delta, d)
            if float(bx) != 0:
                pct_s = _num(100.0 * delta / abs(float(bx)), 1) + "%"
            if hf_based and slow:
                arrow = "레짐?"
            elif direction != 0 and delta != 0:
                toward_para = (delta > 0 and direction > 0) or \
                              (delta < 0 and direction < 0)
                arrow = "↑부교감" if toward_para else "↓교감"
                if counts:
                    para_total += 1
                    if toward_para:
                        para_hits += 1
        L(f"  {label:<16}{b_s:>12}{v_s:>14}{delta_s:>11}{pct_s:>9}  {arrow}")

    L("")
    L("[해석 / Interpretation]")
    if slow:
        L("    느린/공명 호흡 레짐이므로 HF 기반 방향은 제외했습니다. 아래 판정은 "
          "대역에 무관한 시간영역 vagal 지표(RMSSD·SD1·pNN50·HTI) 기준입니다.")
    if para_total:
        L(f"    부교감(미주신경) 우세 방향 지표: {para_hits}/{para_total} "
          f"({100.0 * para_hits / para_total:.0f}%).")
        if para_hits >= para_total - para_total // 4:
            tail = ("RMSSD·SD1·pNN50 ↑" if slow else
                    "RMSSD·HF·SD1 ↑ 및 LF/HF ↓")
            L(f"    → 개입에서 {tail} 가 대체로 관찰됩니다. 느린 호흡 → 부교감 활성 ↑ → "
              "RSA/HRV ↑ 라는 BELL-001 기전과 일치하는 방향입니다.")
        elif para_hits <= para_total // 4:
            L("    → 부교감 우세 방향의 변화가 뚜렷하지 않습니다. 기록 품질/길이와 "
              "이상박동 비율을 확인하세요.")
        else:
            L("    → 혼재된 변화입니다. 개별 지표와 이상박동 비율을 함께 보세요.")
    L("    (주의: n=1 짝 비교는 통계적 유의성이 아니라 방향만 나타냅니다.)")
    L("")
    return "\n".join(lines)


def _finite(x) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return xf == xf and not math.isinf(xf)


# 짝지은 코호트 요약에 보여줄 지표 (key, 라벨, 자릿수, 부교감 방향, HF기반).
_PAIRED_METRICS = [
    ("mean_hr", "mean HR (bpm)", 1, -1, False),
    ("rmssd", "RMSSD (ms)", 2, +1, False),
    ("sdnn", "SDNN (ms)", 2, +1, False),
    ("pnn50", "pNN50 (%)", 1, +1, False),
    ("hti", "HRV tri. index", 2, +1, False),
    ("hf_power", "HF power (ms²)", 1, +1, True),
    ("hf_nu", "HF (n.u.)", 1, +1, True),
    ("lf_hf_ratio", "LF/HF", 3, -1, True),
    ("sd1", "SD1 (ms)", 2, +1, False),
    ("sampen", "SampEn", 3, 0, False),
    ("dfa_alpha1", "DFA α1", 3, 0, False),
]


def paired_group(pairs: Sequence) -> "dict":
    """(기저 HRVResult, 개입 HRVResult) 짝들의 지표별 코호트 요약을 계산.

    반환: {metric_key: paired_summary(...) dict}. 또한 특수 키 '_meta' 에
    피험자 수와 느린/공명 호흡 레짐 짝 수를 담습니다.
    """
    bases = [flat_metrics(b) for b, _ in pairs]
    intervs = [flat_metrics(v) for _, v in pairs]
    slow_n = sum(1 for b, v in pairs
                 if b.freq.get("slow_breathing_regime") or
                 v.freq.get("slow_breathing_regime"))
    out = {"_meta": {"n_subjects": len(pairs), "n_slow_regime": slow_n}}
    for key, _, _, _, _ in _PAIRED_METRICS:
        b_vals = [bm.get(key) for bm in bases]
        v_vals = [vm.get(key) for vm in intervs]
        out[key] = paired_summary(b_vals, v_vals)
    return out


def render_paired_group(pairs: Sequence) -> str:
    """짝지은 코호트 통계(평균 차이±SD, Cohen's dz, Wilcoxon p, 방향)를 렌더링."""
    g = paired_group(pairs)
    meta = g["_meta"]
    n = meta["n_subjects"]
    slow = meta["n_slow_regime"]
    lines: List[str] = []
    L = lines.append
    L("=" * 82)
    L("  hrvkit — 짝지은 코호트 통계 / Paired-cohort statistics")
    L("=" * 82)
    L(f"  피험자 짝 수 n = {n}"
      + (f"   (느린/공명 호흡 레짐 {slow}쌍: HF 기반 지표 해석 주의)" if slow else ""))
    L("  검정: Wilcoxon 부호순위(정규 근사, 연속성·동점 보정), 효과크기 Cohen's dz")
    L("")
    L(f"  {'metric':<16}{'base→interv':>20}{'ΔM±SD':>16}"
      f"{'dz':>7}{'Wilcox p':>10}{'↑n/n':>8}  방향")
    L("  " + "-" * 80)

    for key, label, d, direction, hf_based in _PAIRED_METRICS:
        s = g[key]
        if not s or s.get("n", 0) == 0:
            L(f"  {label:<16}{'(no data)':>20}")
            continue
        bi = f"{s['mean_base']:.{d}f}→{s['mean_interv']:.{d}f}"
        md = f"{s['mean_diff']:+.{d}f}±{s['sd_diff']:.{d}f}"
        dz = s.get("cohens_dz")
        dz_s = _num(dz, 2)
        p_s = _num(s.get("wilcoxon_p"), 4)
        inc = f"{int(s.get('n_increased', 0))}/{s['n']}"
        arrow = ""
        if direction != 0 and not (hf_based and slow):
            md_val = s["mean_diff"]
            if md_val != 0:
                toward = (md_val > 0 and direction > 0) or \
                         (md_val < 0 and direction < 0)
                arrow = "↑부교감" if toward else "↓교감"
        elif hf_based and slow:
            arrow = "레짐?"
        L(f"  {label:<16}{bi:>20}{md:>16}{dz_s:>7}{p_s:>10}{inc:>8}  {arrow}")

    L("")
    L("[해석 / Interpretation]")
    if slow:
        L("    일부 짝이 느린/공명 호흡 레짐 → HF 기반 지표(HF·HF n.u.·LF/HF)의 방향은 "
          "신뢰할 수 없습니다. 시간영역 vagal 지표(RMSSD·SD1·pNN50·HTI)로 판단하세요.")
    # 대표 vagal 지표(RMSSD)의 유의성으로 한 줄 결론
    rm = g.get("rmssd", {})
    if rm.get("n", 0) >= 2 and _finite(rm.get("wilcoxon_p")):
        p = rm["wilcoxon_p"]
        dzv = rm.get("cohens_dz")
        sig = "유의미" if p < 0.05 else "유의하지 않음"
        L(f"    RMSSD: 평균 {rm['mean_diff']:+.1f} ms, Cohen's dz={_num(dzv, 2)}, "
          f"Wilcoxon p={_num(p, 4)} → 개입 효과 {sig}(α=0.05).")
    L("    (주의: 여러 지표를 동시에 검정하면 다중비교 보정이 필요할 수 있습니다.)")
    L("")
    return "\n".join(lines)
