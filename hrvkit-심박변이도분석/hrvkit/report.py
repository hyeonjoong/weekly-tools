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
from .stats import benjamini_hochberg, holm_adjust, paired_summary

__all__ = ["render_text", "render_comparison", "render_batch_table",
           "metrics_to_csv", "paired_group", "render_paired_group",
           "paired_group_to_csv"]


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
        L(f"    해상도 resolution  : {_num(f.get('freq_resolution_hz'), 4)} Hz "
          f"(구간 {_num(f.get('welch_segment_sec'), 1)} s → VLF/LF/HF 빈 "
          f"{int(f.get('vlf_bins') or 0)}/{int(f.get('lf_bins') or 0)}/"
          f"{int(f.get('hf_bins') or 0)}개)")
    # VLF는 구간 길이보다 느린 성분이라 기본 설정에선 과소추정/추정불가.
    # 숫자만 찍으면 오해하므로 신뢰 여부를 같은 줄에 붙입니다.
    vlf_note = ""
    if not f.get("vlf_reliable", False) and f.get("n_resampled"):
        if not _finite(f.get("vlf_power")):
            vlf_note = "  ※ 구간이 짧아 VLF 대역에 빈 없음 → 추정 불가"
        else:
            vlf_note = ("  ※ 구간 < VLF 주기(333 s) → 과소추정, 참고용"
                        " (--nperseg 로 구간 확대)")
    L(f"    VLF power          : {_num(f['vlf_power'], 1)} ms²  "
      f"({_num(f['vlf_pct'], 1)}%){vlf_note}")
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
                arrow = "↑부교감" if toward_para else "↑교감"
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


def paired_group(pairs: Sequence, alpha: float = 0.05) -> "dict":
    """(기저 HRVResult, 개입 HRVResult) 짝들의 지표별 코호트 요약을 계산.

    각 지표에 대해 paired_summary(평균차·dz·Hodges–Lehmann 이동량·분포무관 CI·
    Wilcoxon p)를 내고, **_PAIRED_METRICS 전체를 하나의 검정 가족(family)으로 보아**
    다중비교 보정 p값을 덧붙입니다:
      p_holm : Holm–Bonferroni (FWER 통제 — 보수적, "적어도 하나 위양성" 방지)
      p_bh   : Benjamini–Hochberg (FDR 통제 — 탐색적 지표 스크리닝에 적합)
    검정되지 않은 지표(n=0 이거나 p가 NaN)는 가족 크기 m 에서 제외됩니다.

    주의 — 가족에는 **대수적으로 중복인 지표**가 들어 있습니다: SD1 = SDSD/√2 이고
    SDSD ≈ RMSSD 이므로 RMSSD와 SD1은 사실상 같은 검정이며 실제로 동일한 p값을 냅니다.
    HF power 와 HF n.u., LF/HF 도 서로 강하게 얽혀 있습니다. 즉 m 이 '독립 검정 수'를
    과대평가하므로 Holm/BH 보정은 **필요 이상으로 보수적**입니다(위양성 쪽으로는
    안전, 검정력 쪽으로는 손해). 사전에 주 지표 하나를 지정해 그 지표의 보정 없는 p를
    보고하는 것이 통계적으로 가장 강력합니다.

    반환: {metric_key: summary dict}. 특수 키 '_meta' 에 피험자 수, 느린/공명 호흡
    레짐 짝 수, 가족 크기(n_tests), alpha 를 담습니다.
    """
    bases = [flat_metrics(b) for b, _ in pairs]
    intervs = [flat_metrics(v) for _, v in pairs]
    slow_n = sum(1 for b, v in pairs
                 if b.freq.get("slow_breathing_regime") or
                 v.freq.get("slow_breathing_regime"))
    out = {}
    keys = [k for k, _, _, _, _ in _PAIRED_METRICS]
    for key in keys:
        b_vals = [bm.get(key) for bm in bases]
        v_vals = [vm.get(key) for vm in intervs]
        out[key] = paired_summary(b_vals, v_vals, alpha=alpha)

    # 지표 가족 전체에 대한 다중비교 보정. 검정되지 않은 지표는 NaN → m에서 제외.
    pvals = [out[k].get("wilcoxon_p", float("nan")) for k in keys]
    holm = holm_adjust(pvals)
    bh = benjamini_hochberg(pvals)
    for key, ph, pb in zip(keys, holm, bh):
        out[key]["p_holm"] = ph
        out[key]["p_bh"] = pb

    out["_meta"] = {
        "n_subjects": len(pairs),
        "n_slow_regime": slow_n,
        "n_tests": sum(1 for p in pvals if _finite(p)),
        "alpha": alpha,
    }
    return out


# 코호트 통계 CSV 열 (key, 자릿수). 논문 표/스프레드시트에 바로 붙일 수 있는 형태.
_PAIRED_CSV_COLS = [
    ("n", 0), ("mean_base", 4), ("mean_interv", 4), ("mean_diff", 4),
    ("sd_diff", 4), ("sem_diff", 4), ("cohens_dz", 4), ("median_diff", 4),
    ("hl_shift", 4), ("ci_low", 4), ("ci_high", 4), ("ci_alpha", 3),
    ("ci_method", None), ("w_plus", 1), ("n_pairs", 0), ("wilcoxon_z", 4),
    ("wilcoxon_p", 6), ("wilcoxon_method", None), ("p_holm", 6), ("p_bh", 6),
    ("n_increased", 0),
]


def paired_group_to_csv(pairs: Sequence, alpha: float = 0.05) -> str:
    """짝지은 코호트 통계를 지표당 한 행인 CSV로 직렬화(헤더 포함).

    render_paired_group 의 표를 스프레드시트/논문 표로 옮기기 위한 형식.
    """
    g = paired_group(pairs, alpha=alpha)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric"] + [k for k, _ in _PAIRED_CSV_COLS])
    for key, _label, _d, _direction, _hf in _PAIRED_METRICS:
        s = g.get(key) or {}
        writer.writerow([key] +
                        [_fmt_cell(s.get(k), d) for k, d in _PAIRED_CSV_COLS])
    return buf.getvalue()


def render_paired_group(pairs: Sequence, alpha: float = 0.05) -> str:
    """짝지은 코호트 통계를 기술 표 + 추론 표 두 블록으로 렌더링.

    [A] 기술: 평균 base→interv, ΔM±SD, 증가한 피험자 수.
    [B] 추론: Hodges–Lehmann 이동량과 분포무관 CI, Cohen's dz, Wilcoxon p
        (정확/근사 표기), Holm·BH 보정 p, 부교감 방향.
    한 표에 다 넣으면 폭이 120자를 넘어 터미널에서 접히므로 나눕니다.
    """
    g = paired_group(pairs, alpha=alpha)
    meta = g["_meta"]
    n = meta["n_subjects"]
    slow = meta["n_slow_regime"]
    m_tests = meta["n_tests"]
    pct = int(round((1.0 - alpha) * 100))
    lines: List[str] = []
    L = lines.append
    L("=" * 84)
    L("  hrvkit — 짝지은 코호트 통계 / Paired-cohort statistics")
    L("=" * 84)
    L(f"  피험자 짝 수 n = {n}"
      + (f"   (느린/공명 호흡 레짐 {slow}쌍: HF 기반 지표 해석 주의)" if slow else ""))
    L("")

    # ---------------- [A] 기술통계 ----------------
    L("[A] 기술 / Descriptive")
    L(f"  {'metric':<16}{'base→interv':>22}{'ΔM±SD':>18}{'↑n/n':>9}")
    L("  " + "-" * 63)
    for key, label, d, _direction, _hf in _PAIRED_METRICS:
        s = g[key]
        if not s or s.get("n", 0) == 0:
            L(f"  {label:<16}{'(no data)':>22}")
            continue
        bi = f"{s['mean_base']:.{d}f}→{s['mean_interv']:.{d}f}"
        md = f"{s['mean_diff']:+.{d}f}±{s['sd_diff']:.{d}f}"
        inc = f"{int(s.get('n_increased', 0))}/{s['n']}"
        L(f"  {label:<16}{bi:>22}{md:>18}{inc:>9}")

    # ---------------- [B] 추론통계 ----------------
    L("")
    L("[B] 추론 / Inference"
      f"   — Wilcoxon 부호순위 · Hodges–Lehmann {pct}% CI · 다중비교 보정(m={m_tests})")
    L(f"  {'metric':<16}{'HL shift':>10}{f'{pct}% CI':>22}{'dz':>9}"
      f"{'p':>9}{'p_holm':>9}{'p_BH':>9}  방향")
    L("  " + "-" * 92)

    for key, label, d, direction, hf_based in _PAIRED_METRICS:
        s = g[key]
        if not s or s.get("n", 0) == 0:
            L(f"  {label:<16}{'(no data)':>10}")
            continue
        hl_s = _num(s.get("hl_shift"), d)
        lo, hi = s.get("ci_low"), s.get("ci_high")
        if _finite(lo) and _finite(hi):
            ci_s = f"[{_num(lo, d)}, {_num(hi, d)}]"
        elif s.get("ci_method") == "insufficient-n":
            # n이 작아 어떤 유한 구간도 1-alpha 를 담보 못 함 → (-∞,∞). 숨기지 않는다.
            ci_s = "(-∞, ∞)†"
        else:
            ci_s = "—"
        dz_s = _num(s.get("cohens_dz"), 2)
        # 정확 검정이면 p 뒤에 'e' 표시(exact), 근사면 'a'.
        mark = {"exact": "e", "approx": "a"}.get(s.get("wilcoxon_method"), "")
        p_s = _num(s.get("wilcoxon_p"), 4) + mark
        ph_s = _num(s.get("p_holm"), 4)
        pb_s = _num(s.get("p_bh"), 4)
        arrow = ""
        if hf_based and slow:
            arrow = "레짐?"
        elif direction != 0:
            # 방향은 강건한 HL 이동량 기준(평균은 이상값에 끌림). HL이 없으면 평균.
            shift = s.get("hl_shift")
            if not _finite(shift):
                shift = s.get("mean_diff")
            if _finite(shift) and shift != 0:
                toward = (shift > 0 and direction > 0) or \
                         (shift < 0 and direction < 0)
                arrow = "↑부교감" if toward else "↑교감"
        L(f"  {label:<16}{hl_s:>10}{ci_s:>22}{dz_s:>9}"
          f"{p_s:>9}{ph_s:>9}{pb_s:>9}  {arrow}")

    L("")
    L("  p 뒤 e=정확(exact) 분포, a=정규 근사. HL=Hodges–Lehmann 이동량(강건).")
    L("  † = 이 n에서는 해당 수준의 유한 신뢰구간이 존재하지 않음(표본 부족).")
    L(f"  CI는 부호순위 검정과 쌍대인 분포무관 {pct}% 구간 — CI가 0을 포함하지 않는 것과")
    L("  p<α 는 (동점이 없을 때) 서로 일치합니다.")

    # ---------------- 해석 ----------------
    L("")
    L("[해석 / Interpretation]")
    if slow:
        L("    일부 짝이 느린/공명 호흡 레짐 → HF 기반 지표(HF·HF n.u.·LF/HF)의 방향은 "
          "신뢰할 수 없습니다. 시간영역 vagal 지표(RMSSD·SD1·pNN50·HTI)로 판단하세요.")
    # 대표 vagal 지표(RMSSD)의 유의성으로 한 줄 결론 — 보정 p 기준으로 판정.
    rm = g.get("rmssd", {})
    if rm.get("n", 0) >= 2 and _finite(rm.get("wilcoxon_p")):
        p = rm["wilcoxon_p"]
        ph = rm.get("p_holm")
        lo, hi = rm.get("ci_low"), rm.get("ci_high")
        ci_s = (f", {pct}% CI [{_num(lo, 1)}, {_num(hi, 1)}] ms"
                if _finite(lo) and _finite(hi) else "")
        sig = "유의미" if _finite(ph) and ph < alpha else "유의하지 않음"
        L(f"    RMSSD: HL 이동량 {_num(rm.get('hl_shift'), 1)} ms{ci_s}, "
          f"dz={_num(rm.get('cohens_dz'), 2)},")
        L(f"           p={_num(p, 4)} → Holm 보정 p={_num(ph, 4)} "
          f"→ 개입 효과 {sig} (α={alpha:g}, {m_tests}개 지표 보정).")
    if m_tests > 1:
        L(f"    ({m_tests}개 지표를 동시에 검정했습니다. 사전 지정한 주 지표가 있으면 "
          "그 지표의 p를,")
        L("     탐색적 스크리닝이면 p_BH(FDR)를, 확증적이면 p_holm(FWER)을 보고하세요.")
        L("     RMSSD와 SD1은 대수적으로 거의 같은 지표라 가족에 중복이 있습니다 →")
        L("     보정은 필요 이상으로 보수적입니다. 주 지표 사전 지정이 가장 강력합니다.)")
    L("")
    return "\n".join(lines)
