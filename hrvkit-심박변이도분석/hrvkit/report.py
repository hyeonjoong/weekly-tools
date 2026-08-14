"""사람이 읽는 HRV 리포트 렌더링 (한국어 + 영어 라벨).

단일 파일 리포트(render_text) 외에 여러 파일을 비교/일괄 처리하는
render_comparison(기저 대비 개입)과 render_batch_table/metrics_to_csv를 제공합니다.
"""

from __future__ import annotations

import csv
import io
import math
import os
import unicodedata
from typing import List, Sequence

from .analyze import FLAT_COLUMNS, HRVResult, flat_metrics
from .power import MAX_N, plan_paired, plan_parallel
from .stats import (benjamini_hochberg, holm_adjust, paired_summary,
                    unpaired_summary)
from .window import (TREND_METRICS, WindowSeries, long_term_indices,
                     window_trends)

__all__ = ["render_text", "render_comparison", "render_batch_table",
           "metrics_to_csv", "paired_group", "render_paired_group",
           "paired_group_to_csv", "render_windows", "windows_to_csv",
           "group_compare", "render_group_compare", "group_compare_to_csv",
           "power_plan_paired", "power_plan_groups", "power_plan_to_csv",
           "render_power_plan", "render_plan"]


def _disp_width(s: str) -> int:
    """터미널 표시 폭 — 한글/전각 문자는 2칸을 차지합니다.

    파이썬의 f-string 정렬(`{x:>12}`)은 **문자 수**로 채우므로 헤더에 한글이
    섞이면 열이 어긋납니다. 표 헤더에 한국어 라벨을 쓰려면 폭 기준 패딩이
    필요합니다.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in s)


def _rj(s: str, width: int) -> str:
    """표시 폭 기준 오른쪽 정렬."""
    return " " * max(0, width - _disp_width(s)) + s


def _lj(s: str, width: int) -> str:
    """표시 폭 기준 왼쪽 정렬."""
    return s + " " * max(0, width - _disp_width(s))


def _pct(x: float) -> str:
    """비율을 % 로 — 0/100 으로 **반올림되어 사실이 바뀌는 것**을 막습니다.

    0.999 을 "100%" 로 찍으면 IRB 제출 문서에 "탈락률 100%" 가 들어갑니다.
    반올림 결과가 0 또는 100 인데 원값이 그렇지 않으면 자릿수를 늘립니다.
    """
    v = 100.0 * x
    for digits in (0, 1, 2, 3, 4, 6):
        s_ = f"{v:.{digits}f}"
        f = float(s_)
        if (f not in (0.0, 100.0)) or (v in (0.0, 100.0)):
            return s_
    return f"{v:.10g}"


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
    is_lomb = f.get("psd_method") == "lomb"
    has_psd = bool(f.get("n_resampled")) or is_lomb
    if is_lomb:
        L(f"    방법 method        : Lomb–Scargle 주기도 (보간 없음, 박동 시각에 "
          f"직접 최소제곱 적합, 격자 과표본 ×{_num(f.get('ls_oversample'), 1)})")
        L(f"    기록 길이 duration : {_num(f['duration_sec'], 1)} s "
          f"({int(f.get('ls_n_beats') or 0)} beats, 평균 표본율 "
          f"{_num(f.get('ls_fs_eff'), 3)} Hz)")
        L(f"    해상도 resolution  : {_num(f.get('freq_resolution_hz'), 4)} Hz "
          f"(=1/기록길이; 격자 {_num(f.get('ls_df_hz'), 5)} Hz × "
          f"{int(f.get('ls_nfreq') or 0)}점 → VLF/LF/HF 빈 "
          f"{int(f.get('vlf_bins') or 0)}/{int(f.get('lf_bins') or 0)}/"
          f"{int(f.get('hf_bins') or 0)}개)")
        if f.get("ls_above_nyquist"):
            L(f"      ※ 평균 표본율의 절반({_num(f.get('ls_nyquist_hz'), 3)} Hz)이 "
              "HF 상단(0.40 Hz)보다 낮습니다 — 서맥 기록이라 HF 상단은 앨리어싱 "
              "위험이 있습니다.")
    elif f.get("n_resampled"):
        L(f"    방법 method        : {_num(f['resample_fs'], 0)} Hz 선형 리샘플 → "
          f"Welch PSD (Hann, nperseg={int(f['welch_nperseg'])}, 50% overlap, "
          f"radix-2 FFT, {int(f['welch_segments'])} segments)")
        L(f"    기록 길이 duration : {_num(f['duration_sec'], 1)} s "
          f"({int(f['n_resampled'])} samples)")
        L(f"    해상도 resolution  : {_num(f.get('freq_resolution_hz'), 4)} Hz "
          f"(구간 {_num(f.get('welch_segment_sec'), 1)} s → VLF/LF/HF 빈 "
          f"{int(f.get('vlf_bins') or 0)}/{int(f.get('lf_bins') or 0)}/"
          f"{int(f.get('hf_bins') or 0)}개)")
    # VLF는 구간(Welch)/기록(Lomb) 길이보다 느린 성분이라 짧은 기록에선
    # 과소추정/추정불가. 숫자만 찍으면 오해하므로 신뢰 여부를 같은 줄에 붙입니다.
    vlf_note = ""
    if not f.get("vlf_reliable", False) and has_psd:
        limit = "기록" if is_lomb else "구간"
        fix = "더 긴 기록 필요" if is_lomb else "--nperseg 로 구간 확대"
        if not _finite(f.get("vlf_power")):
            vlf_note = f"  ※ {limit}이 짧아 VLF 대역에 빈 없음 → 추정 불가"
        else:
            vlf_note = (f"  ※ {limit} < VLF 주기(333 s) → 과소추정, 참고용 ({fix})")
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


_PSD_LABELS = {"welch": "Welch (4 Hz 선형보간 → FFT)",
               "lomb": "Lomb–Scargle (보간 없음)"}


def psd_method_of(results) -> str:
    """여러 HRVResult 가 쓴 PSD 방법을 하나로 요약합니다.

    코호트 표(--paired/--groups)와 --compare 는 여러 기록을 한 표에 모으므로,
    어느 추정기로 낸 숫자인지 **표 위에 반드시 적혀야** 합니다. Welch 와 Lomb 의
    절대 파워는 같은 기록에서도 20 % 이상 다를 수 있어, 방법이 안 적힌 표는
    나중에 다른 연구와 섞이면 복구할 수 없는 혼동이 됩니다.

    전부 같으면 그 방법을, 섞였으면 'mixed', 주파수영역이 전부 생략됐으면 ''.
    """
    seen = set()
    for r in results:
        m = getattr(r, "freq", {}).get("psd_method")
        if m:
            seen.add(m)
    if not seen:
        return ""
    if len(seen) > 1:
        return "mixed"
    return seen.pop()


def _psd_method_line(results) -> str:
    """리포트 머리말에 넣을 'PSD 방법: …' 한 줄 (없으면 빈 문자열)."""
    m = psd_method_of(results)
    if not m:
        return ""
    if m == "mixed":
        return ("  ※ PSD 방법: **혼합(mixed)** — 이 표의 기록들이 서로 다른 "
                "추정기로 계산됐습니다. 절대 파워를 비교하지 마세요.")
    return f"  PSD 방법     : {_PSD_LABELS.get(m, m)}"


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
    _pm = _psd_method_line([baseline, intervention])
    if _pm:
        L(_pm)
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
        "psd_method": psd_method_of([r for pair in pairs for r in pair]),
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
    pm = psd_method_of([r for pair in pairs for r in pair])
    writer.writerow(["metric", "psd_method"] + [k for k, _ in _PAIRED_CSV_COLS])
    for key, _label, _d, _direction, _hf in _PAIRED_METRICS:
        s = g.get(key) or {}
        writer.writerow([key, pm] +
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
    _pm = _psd_method_line([r for pair in pairs for r in pair])
    if _pm:
        L(_pm)
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
    # 짝 수가 적으면 모든 피험자가 같은 방향이어도 정확검정 최소 p = 2^(1-n) 이라
    # 보정 후 기각이 불가능합니다. 이를 밝히지 않으면 "유의하지 않음"이 효과 없음으로
    # 오독됩니다.
    n_eff = int(rm.get("n_pairs") or 0) if rm else 0
    if 1 <= n_eff <= 12:
        min_p = 2.0 ** (1 - n_eff)
        if min_p * max(1, m_tests) >= alpha:
            L(f"    ※ 유효 짝 n={n_eff} 에서 정확검정이 낼 수 있는 최소 p는 "
              f"{min_p:.4f}, {m_tests}개 지표 보정 후에는 "
              f"{min(1.0, min_p * max(1, m_tests)):.4f} 입니다 — 즉 **효과가 아무리 "
              f"커도** α={alpha:g} 에서 Holm 기각이 불가능한 표본 수입니다.")
    if m_tests > 1:
        L(f"    ({m_tests}개 지표를 동시에 검정했습니다. 사전 지정한 주 지표가 있으면 "
          "그 지표의 p를,")
        L("     탐색적 스크리닝이면 p_BH(FDR)를, 확증적이면 p_holm(FWER)을 보고하세요.")
        L("     RMSSD와 SD1은 대수적으로 거의 같은 지표라 가족에 중복이 있습니다 →")
        L("     보정은 필요 이상으로 보수적입니다. 주 지표 사전 지정이 가장 강력합니다.)")
    L("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 구간(epoch)별 리포트
# --------------------------------------------------------------------------- #
# 구간 표에 보여줄 압축 열 (key, 헤더, 자릿수).
_WINDOW_COLS = [
    ("n_input", "n", 0),
    ("pct_artifacts", "art%", 1),
    ("mean_hr", "HR", 1),
    ("rmssd", "RMSSD", 1),
    ("sdnn", "SDNN", 1),
    ("pnn50", "pNN50", 1),
    ("hf_nu", "HF_nu", 1),
    ("lf_hf_ratio", "LF/HF", 2),
    ("sd1", "SD1", 1),
    ("dfa_alpha1", "α1", 2),
]


def _mmss(sec: float) -> str:
    """초를 mm:ss 로 (1시간 넘으면 h:mm:ss)."""
    if not _finite(sec) or sec < 0:
        return "?"
    s = int(round(sec))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


def _window_flat(w) -> dict:
    """구간의 평탄 지표 — 이상박동 수/비율만 **창 수준의 참값**으로 덮어씁니다.

    `--clean remove` 에서는 이상박동이 시계열에서 아예 사라지므로 그 구간을
    분석한 HRVResult 는 이상박동 0개를 보고합니다. 실제로는 그 시간대에
    이상박동이 있었고, 사용자가 "이 구간을 믿을지" 판단하는 근거가 바로 그
    숫자입니다. Window 는 원시 시간축에서 센 참값을 갖고 있으므로 그것을 씁니다.
    """
    flat = flat_metrics(w.result)
    flat["pct_artifacts"] = w.pct_artifacts
    return flat


def windows_to_csv(series: WindowSeries) -> str:
    """구간별 지표를 구간당 한 행인 CSV로 직렬화(헤더 포함).

    앞에 window/start_sec/end_sec/n_beats/pct_artifacts_window 열을 붙이고
    나머지는 단일 파일 CSV와 **같은 스키마**(FLAT_COLUMNS)를 씁니다 —
    기존 파이프라인이 그대로 재사용됩니다. 분석에 실패한 구간도 행을 남기고
    (지표는 빈칸) error 열에 사유를 적습니다: 조용히 사라지면 "그 시간대에
    데이터가 있었다"는 사실 자체를 잃습니다.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["window", "start_sec", "end_sec", "n_beats",
                     "n_artifacts_window", "pct_artifacts_window"] +
                    [k for k, _ in FLAT_COLUMNS] +
                    ["vlf_reliable", "warnings", "error"])
    for w in series.windows:
        head = [w.index, _fmt_cell(w.start_sec, 1), _fmt_cell(w.end_sec, 1),
                w.n_beats, w.n_artifacts, _fmt_cell(w.pct_artifacts, 1)]
        if w.ok:
            flat = _window_flat(w)
            body = [_fmt_cell(flat.get(k), d) for k, d in FLAT_COLUMNS]
            tail = [bool(w.result.freq.get("vlf_reliable")),
                    " | ".join(w.result.warnings)]
        else:
            body = [""] * len(FLAT_COLUMNS)
            tail = ["", ""]
        writer.writerow(head + body + tail + [w.error or ""])
    return buf.getvalue()


def render_windows(series: WindowSeries) -> str:
    """구간별 분석을 [A] 구간 표 · [B] 요약/추세 · [C] 장기 지표로 렌더링."""
    lines: List[str] = []
    L = lines.append
    L("=" * 88)
    L("  hrvkit — 구간별 추이 / Windowed (epoch-wise) HRV")
    L("=" * 88)
    if series.source:
        L(f"  파일 source   : {series.source}")
    L(f"  기록 길이     : {_num(series.duration_sec, 1)} s "
      f"({_mmss(series.duration_sec)}), 입력 박동 {series.n_input}개, "
      f"이상박동 {_num(series.pct_artifacts, 1)}% → 보정 {series.clean_method}")
    _pm = _psd_method_line([w.result for w in series.ok_windows])
    if _pm:
        L(_pm)
    L(f"  창 window     : {_num(series.window_sec, 0)} s, "
      f"step {_num(series.step_sec, 0)} s "
      f"({'겹침 overlapping' if series.overlapping else '겹치지 않음'}) → "
      f"분석된 구간 {len(series.ok_windows)}/{len(series.windows)}개")
    L("")

    # ---------------- [A] 구간 표 ----------------
    L("[A] 구간별 지표 / Per-window metrics")
    header = "  " + "win".ljust(5) + "start".rjust(9)
    for _, name, _ in _WINDOW_COLS:
        header += "  " + name.rjust(7)
    L(header)
    L("  " + "-" * (14 + len(_WINDOW_COLS) * 9))
    for w in series.windows:
        row = "  " + str(w.index).ljust(5) + _mmss(w.start_sec).rjust(9)
        if w.ok:
            flat = _window_flat(w)
            for key, _, d in _WINDOW_COLS:
                row += "  " + _fmt_cell(flat.get(key), d).rjust(7)
        else:
            row += "  " + (w.error or "분석 불가")
        L(row)

    # ---------------- [A'] 구간별 경고 ----------------
    # 구간마다 나온 경고(느린/공명 호흡 레짐, 주파수영역 생략, 높은 이상박동률…)를
    # 텍스트 리포트에서도 보여 줍니다. 과거엔 --json 사용자만 볼 수 있었습니다.
    wmsgs: dict = {}
    for w in series.windows:
        for msg in (w.result.warnings if w.ok else []):
            wmsgs.setdefault(msg, []).append(w.index)
    if wmsgs:
        L("")
        L("  [!] 구간별 경고 / Per-window warnings")
        for msg, idxs in wmsgs.items():
            shown = ",".join(str(i) for i in idxs[:8])
            more = f" …(+{len(idxs) - 8})" if len(idxs) > 8 else ""
            L(f"      - 창 {shown}{more}: {msg}")

    # ---------------- [B] 요약 + 추세 ----------------
    tr = window_trends(series)
    meta = tr["_meta"]
    L("")
    L("[B] 구간 요약과 단조 추세 / Summary + Mann–Kendall trend"
      f"   (구간 {meta['n_windows']}개, 보정 m={meta['n_tests']})")
    L(f"  {'metric':<16}{'n':>4}{'mean±SD':>20}{'CV':>8}{'min–max':>20}"
      f"{'tau':>8}{'slope/창':>11}{'p':>9}{'p_holm':>9}")
    L("  " + "-" * 103)
    partial = False
    for key, label, d in TREND_METRICS:
        s = tr[key]
        if not s.get("n"):
            L(f"  {label:<16}{0:>4}{'(no data)':>20}")
            continue
        # 지표별 유효 창 수 — NaN 구간(짧은 창의 SampEn, HF=0 의 LF/HF=inf 등)이
        # 빠지면 그 행만 다른 n 으로 계산됩니다. 숨기면 같은 n 으로 오독됩니다.
        n_s = int(s["n"])
        if n_s < meta["n_windows"]:
            partial = True
        n_disp = f"{n_s}*" if n_s < meta["n_windows"] else str(n_s)
        ms = f"{_num(s.get('mean'), d)}±{_num(s.get('sd'), d)}"
        cv = _num(s.get("cv"), 3)
        mm = f"{_num(s.get('min'), d)}–{_num(s.get('max'), d)}"
        tau = _num(s.get("tau"), 3)
        slope = _num(s.get("slope_per_window"), d)
        mark = {"exact": "e", "approx": "a"}.get(s.get("trend_method"), "")
        p_s = _num(s.get("trend_p"), 4) + mark
        ph_s = _num(s.get("p_holm"), 4)
        L(f"  {label:<16}{n_disp:>4}{ms:>20}{cv:>8}{mm:>20}{tau:>8}{slope:>11}"
          f"{p_s:>9}{ph_s:>9}")
    L("")
    L("  tau = Kendall tau-b (구간 순서 대비 단조 추세, +면 시간에 따라 증가).")
    L("  slope = Theil–Sen 중앙값 기울기(지표단위/구간) — 이상 구간에 강건.")
    L("  p 뒤 e=정확(exact) 분포, a=정규 근사.")
    if partial:
        L(f"  n 뒤 * = 그 지표가 유한한 창이 전체 {meta['n_windows']}개보다 적음"
          " (해당 구간은 제외하고 검정·기울기 계산).")

    # ---------------- [C] 장기 지표 ----------------
    lt = long_term_indices(series)
    L("")
    L("[C] 장기 지표 / Long-term indices (Task Force 1996 공식 적용)")
    if _finite(lt["sdann"]):
        L(f"    SDANN              : {_num(lt['sdann'], 2)} ms   "
          f"(구간 평균 NN 들의 SD)")
    else:
        reason = ("창이 겹쳐 정의되지 않음" if lt["overlapping"]
                  else "구간이 2개 미만이라 계산 불가")
        L(f"    SDANN              : —   ({reason})")
    L(f"    SDNN index         : {_num(lt['sdnn_index'], 2)} ms   "
      f"(구간 SDNN 들의 평균"
      + ("; 창이 겹쳐 같은 박동을 여러 번 셈 — 참고용" if lt["overlapping"]
         else "") + ")")
    if lt["short_record"]:
        L(f"    ※ Task Force 는 이 둘을 **24시간 홀터** 지표로 정의했고 참고값"
          f"(SDANN ≈ 127±35, SDNN index ≈ 54±15 ms)도 24시간 기준입니다. 이 기록은 "
          f"{_num(lt['duration_sec'] / 60.0, 1)}분뿐이라 공식은 맞아도 발표된 "
          f"SDANN·SDNN index 값과 비교할 수 없습니다.")
    if lt["nonstandard_window"]:
        L(f"    ※ 표준 정의는 5분(300 s) 구간입니다. 현재 창은 "
          f"{_num(series.window_sec, 0)} s 이므로 위 두 값을 다른 도구/논문의 "
          f"SDANN·SDNN index 와 직접 비교하지 마세요.")
    # 구간이 짧으면 Welch 구간도 짧아 VLF가 심하게 과소추정되고, total_power 는
    # 정의상 VLF를 포함하므로 그 편향을 그대로 물려받습니다. "NaN 이 아니니까
    # 괜찮다"는 오독을 막습니다.
    n_unrel = sum(1 for w in series.ok_windows
                  if not w.result.freq.get("vlf_reliable"))
    if n_unrel:
        # 세 가지를 구분해야 합니다: ① 주파수영역 자체가 생략됨(값이 아예 없음),
        # ② VLF 가 NaN(해상 불가로 정직하게 비움), ③ VLF 가 유한하지만 과소추정.
        # 예전에는 셋을 한 문장으로 뭉뚱그려, 주파수영역이 전부 생략된 창에도
        # "Welch 구간이 짧아서" + "NaN 이 아니라 유한 값" 이라고 썼습니다(둘 다 거짓).
        n_total = len(series.ok_windows)
        n_skipped = sum(1 for w in series.ok_windows
                        if not w.result.freq.get("psd_method"))
        n_nan = sum(1 for w in series.ok_windows
                    if w.result.freq.get("psd_method")
                    and not _finite(w.result.freq.get("vlf_power")))
        n_finite = n_unrel - n_skipped - n_nan
        method = psd_method_of([w.result for w in series.ok_windows])
        L("")
        if n_skipped:
            L(f"    ※ 구간 {n_skipped}/{n_total}개는 창이 너무 짧아 **주파수영역을 "
              f"아예 계산하지 못했습니다**(VLF/LF/HF/total 모두 비어 있음). "
              f"창을 늘리거나 주파수 지표 없이 해석하세요.")
        if n_nan or n_finite:
            limit = "각 구간(epoch)의 길이가" if method == "lomb" else \
                "Welch 구간이"
            L(f"    ※ 구간 {n_nan + n_finite}/{n_total}개에서 VLF 가 신뢰 불가"
              f"(vlf_reliable=False)입니다 — {limit} VLF 주기(333 s)보다 짧기 "
              f"때문입니다."
              + (f" 그중 {n_nan}개는 해상 불가라 **NaN**(total_power 도 NaN)이고,"
                 if n_nan else "")
              + (f" {n_finite}개는 **심하게 과소추정된 유한 값**으로 나오며 "
                 f"total_power 는 정의상 VLF 를 포함해 같은 편향을 갖습니다."
                 if n_finite else "")
              + " 구간별로는 total_power/VLF 대신 시간영역·Poincaré 지표를 쓰세요.")

    # ---------------- 해석 ----------------
    L("")
    L("[해석 / Interpretation]")
    rm = tr.get("rmssd", {})
    if rm.get("n", 0) >= 3 and _finite(rm.get("trend_p")):
        ph = rm.get("p_holm")
        direction = "증가" if (rm.get("s") or 0) > 0 else "감소"
        if _finite(ph) and ph < 0.05:
            L(f"    RMSSD 가 구간에 따라 단조 {direction} 하는 추세가 보입니다 "
              f"(tau={_num(rm.get('tau'), 3)}, Holm p={_num(ph, 4)}, "
              f"기울기 {_num(rm.get('slope_per_window'), 2)} ms/창).")
            L("    → 기록이 정상적(stationary)이지 않습니다. 전체를 한 덩어리로 낸 "
              "SDNN 은 이 추세를 변동성으로 흡수해 부풀려집니다.")
        else:
            L(f"    RMSSD 에서 유의한 단조 추세는 확인되지 않았습니다 "
              f"(tau={_num(rm.get('tau'), 3)}, Holm p={_num(ph, 4)}).")
    else:
        L("    추세 검정에는 구간이 3개 이상 필요합니다 (창을 줄이거나 더 긴 기록 필요).")
    # 구간 수가 적으면 **완벽한 단조 추세(tau=±1)여도** 정확검정이 낼 수 있는 최소
    # p가 α를 넘어 기각이 원천적으로 불가능합니다. 이걸 말하지 않으면 "추세 없음"이
    # 증거의 부재가 아니라 부재의 증거처럼 읽힙니다.
    n_w = meta["n_windows"]
    if 3 <= n_w <= 8:
        min_p = 2.0 / math.factorial(n_w)
        L(f"    ※ 구간이 {n_w}개뿐이라 정확검정이 낼 수 있는 최소 p는 "
          f"{min_p:.4f} 입니다"
          + (f" — 보정 후에는 어떤 지표도 α=0.05 에서 유의할 수 없습니다."
             if min_p * max(1, meta['n_tests']) >= 0.05 else
             " (완벽한 단조 추세일 때의 값).")
          + " 추세를 검정하려면 더 긴 기록이나 더 짧은 창이 필요합니다.")
    L("    (주의: 구간별 지표는 짧은 기록이라 주파수영역이 특히 불안정합니다 — "
      "창 길이와 대역 해상도를 함께 보세요.)")
    if series.notes:
        L("")
        L("[!] 주의 / Notes")
        for nlines in series.notes:
            L(f"    - {nlines}")
    L("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 독립 2군(평행군) 비교
# --------------------------------------------------------------------------- #
# 군 비교에 보여줄 지표 (key, 라벨, 자릿수, 부교감 방향, HF기반).
_GROUP_METRICS = list(_PAIRED_METRICS)


def group_compare(a_results: Sequence[HRVResult],
                  b_results: Sequence[HRVResult],
                  alpha: float = 0.05) -> dict:
    """독립 2군(a=대조, b=개입)의 지표별 Mann–Whitney 요약.

    각 지표에 대해 unpaired_summary(평균·Hedges g·Hodges–Lehmann 이동량·
    분포무관 CI·Mann–Whitney p)를 내고, _GROUP_METRICS 전체를 하나의 검정
    가족으로 보아 Holm(FWER)·BH(FDR) 보정 p를 덧붙입니다.

    paired_group 과 같은 주의: 가족 안에 대수적으로 중복인 지표(RMSSD≈SD1·√2)가
    있어 m 이 독립 검정 수를 과대평가하므로 보정은 필요 이상으로 보수적입니다.

    반환: {metric_key: summary}. '_meta' 에 군별 n, 느린/공명 호흡 레짐 기록 수,
    가족 크기, alpha.
    """
    a_flat = [flat_metrics(r) for r in a_results]
    b_flat = [flat_metrics(r) for r in b_results]
    slow_n = sum(1 for r in list(a_results) + list(b_results)
                 if r.freq.get("slow_breathing_regime"))
    keys = [k for k, _, _, _, _ in _GROUP_METRICS]
    out: dict = {}
    for key in keys:
        out[key] = unpaired_summary([fm.get(key) for fm in a_flat],
                                    [fm.get(key) for fm in b_flat],
                                    alpha=alpha)
    pvals = [out[k].get("mw_p", float("nan")) for k in keys]
    for key, ph, pb in zip(keys, holm_adjust(pvals), benjamini_hochberg(pvals)):
        out[key]["p_holm"] = ph
        out[key]["p_bh"] = pb
    out["_meta"] = {
        "n_a": len(a_results),
        "n_b": len(b_results),
        "psd_method": psd_method_of(list(a_results) + list(b_results)),
        "n_slow_regime": slow_n,
        "n_tests": sum(1 for p in pvals if _finite(p)),
        "alpha": alpha,
    }
    return out


# 군 비교 CSV 열 (key, 자릿수).
_GROUP_CSV_COLS = [
    ("n_a", 0), ("n_b", 0), ("mean_a", 4), ("mean_b", 4), ("sd_a", 4),
    ("sd_b", 4), ("median_a", 4), ("median_b", 4), ("mean_diff", 4),
    ("sd_pooled", 4), ("cohens_d", 4), ("hedges_g", 4), ("hl_shift", 4),
    ("ci_low", 4), ("ci_high", 4), ("ci_alpha", 3), ("ci_method", None),
    ("u_stat", 1), ("mw_z", 4), ("mw_p", 6), ("mw_method", None),
    ("rank_biserial", 4), ("cles", 4), ("p_holm", 6), ("p_bh", 6),
]


def group_compare_to_csv(a_results: Sequence[HRVResult],
                         b_results: Sequence[HRVResult],
                         alpha: float = 0.05) -> str:
    """독립 2군 비교 통계를 지표당 한 행인 CSV로 직렬화(헤더 포함)."""
    g = group_compare(a_results, b_results, alpha=alpha)
    buf = io.StringIO()
    writer = csv.writer(buf)
    pm = psd_method_of(list(a_results) + list(b_results))
    writer.writerow(["metric", "psd_method"] + [k for k, _ in _GROUP_CSV_COLS])
    for key, _label, _d, _dir, _hf in _GROUP_METRICS:
        s = g.get(key) or {}
        writer.writerow([key, pm] +
                        [_fmt_cell(s.get(k), d) for k, d in _GROUP_CSV_COLS])
    return buf.getvalue()


def render_group_compare(a_results: Sequence[HRVResult],
                         b_results: Sequence[HRVResult],
                         a_label: str = "control",
                         b_label: str = "treatment",
                         alpha: float = 0.05) -> str:
    """독립 2군 비교를 기술 표 + 추론 표로 렌더링 (평행군 시험용)."""
    g = group_compare(a_results, b_results, alpha=alpha)
    meta = g["_meta"]
    slow = meta["n_slow_regime"]
    m_tests = meta["n_tests"]
    pct = int(round((1.0 - alpha) * 100))
    lines: List[str] = []
    L = lines.append
    L("=" * 90)
    L("  hrvkit — 독립 2군 비교 / Two-group (parallel-arm) comparison")
    L("=" * 90)
    L(f"  기준(대조) {a_label}: n = {meta['n_a']}    "
      f"비교(개입) {b_label}: n = {meta['n_b']}"
      + (f"   (느린/공명 호흡 레짐 {slow}건: HF 기반 지표 해석 주의)"
         if slow else ""))
    # 어느 군이 기준인지 명시합니다 — 매니페스트 행 순서로 정해지므로, 모르면
    # 행 순서만 바꿔도 HL 이동량과 방향 화살표가 통째로 뒤집힙니다.
    L(f"  ※ 기준군은 매니페스트에 **먼저 나온 군**('{a_label}')입니다. "
      f"모든 차이·이동량은 {b_label} − {a_label} 방향입니다.")
    L("  검정: Mann–Whitney U(=Wilcoxon 순위합, 양측) — 각 피험자가 한 군에만 "
      "속하는 설계용.")
    _pm = _psd_method_line(list(a_results) + list(b_results))
    if _pm:
        L(_pm)
    L("")

    # ---------------- [A] 기술 ----------------
    L("[A] 기술 / Descriptive   (mean±SD)")
    L(f"  {'metric':<16}{a_label[:14]:>22}{b_label[:14]:>22}{'Δmean':>12}"
      f"{'n_a/n_b':>10}")
    L("  " + "-" * 82)
    partial = False
    for key, label, d, _dir, _hf in _GROUP_METRICS:
        s = g[key]
        if not s.get("n_a") or not s.get("n_b"):
            L(f"  {label:<16}{'(no data)':>22}")
            continue
        av = f"{s['mean_a']:.{d}f}±{s['sd_a']:.{d}f}"
        bv = f"{s['mean_b']:.{d}f}±{s['sd_b']:.{d}f}"
        dm = f"{s['mean_diff']:+.{d}f}"
        # 지표별 유효 n — NaN 지표(짧은 기록의 SampEn 등)는 그 군에서 빠지므로
        # 헤더의 n 과 달라질 수 있습니다. 숨기면 5대5 결과로 오독됩니다.
        if s["n_a"] < meta["n_a"] or s["n_b"] < meta["n_b"]:
            partial = True
        nn = f"{s['n_a']}/{s['n_b']}"
        if s["n_a"] < meta["n_a"] or s["n_b"] < meta["n_b"]:
            nn += "*"
        L(f"  {label:<16}{av:>22}{bv:>22}{dm:>12}{nn:>10}")
    if partial:
        L(f"  * = 그 지표가 유한한 기록이 전체({meta['n_a']}/{meta['n_b']})보다 "
          "적음 — 아래 검정도 그 n 으로 계산됩니다.")

    # ---------------- [B] 추론 ----------------
    L("")
    L("[B] 추론 / Inference"
      f"   — Mann–Whitney · Hodges–Lehmann {pct}% CI · 다중비교 보정(m={m_tests})")
    L(f"  {'metric':<16}{'HL shift':>10}{f'{pct}% CI':>22}{'g':>8}{'rb':>7}"
      f"{'p':>9}{'p_holm':>9}{'p_BH':>9}  방향")
    L("  " + "-" * 97)
    for key, label, d, direction, hf_based in _GROUP_METRICS:
        s = g[key]
        if not s.get("n_a") or not s.get("n_b"):
            L(f"  {label:<16}{'(no data)':>10}")
            continue
        hl_s = _num(s.get("hl_shift"), d)
        lo, hi = s.get("ci_low"), s.get("ci_high")
        if _finite(lo) and _finite(hi):
            ci_s = f"[{_num(lo, d)}, {_num(hi, d)}]"
        elif s.get("ci_method") == "insufficient-n":
            ci_s = "(-∞, ∞)†"
        else:
            ci_s = "—"
        g_s = _num(s.get("hedges_g"), 2)
        rb_s = _num(s.get("rank_biserial"), 2)
        mark = {"exact": "e", "approx": "a"}.get(s.get("mw_method"), "")
        p_s = _num(s.get("mw_p"), 4) + mark
        ph_s = _num(s.get("p_holm"), 4)
        pb_s = _num(s.get("p_bh"), 4)
        arrow = ""
        if hf_based and slow:
            arrow = "레짐?"
        elif direction != 0:
            shift = s.get("hl_shift")
            if not _finite(shift):
                shift = s.get("mean_diff")
            if _finite(shift) and shift != 0:
                toward = (shift > 0 and direction > 0) or \
                         (shift < 0 and direction < 0)
                arrow = "↑부교감" if toward else "↑교감"
        L(f"  {label:<16}{hl_s:>10}{ci_s:>22}{g_s:>8}{rb_s:>7}"
          f"{p_s:>9}{ph_s:>9}{pb_s:>9}  {arrow}")

    L("")
    L(f"  HL shift = Hodges–Lehmann 이동량 median({b_label} − {a_label}), 강건. "
      "g = Hedges g, rb = 순위이연 상관.")
    L("  p 뒤 e=정확(exact) 분포, a=정규 근사(동점 보정).")
    L("  † = 이 표본 수에서는 해당 수준의 유한 신뢰구간이 존재하지 않음.")
    # 동점이 있으면 p는 근사, CI의 절단 지수 k는 정확 분포에서 나옵니다 —
    # 둘의 기준이 달라 경계에서 어긋날 수 있다는 것을 숨기지 않습니다.
    if any(g[k].get("mw_method") == "approx" for k, _, _, _, _ in _GROUP_METRICS
           if g[k].get("n_a")):
        L("  ※ 'a' 표시 지표는 동점 때문에 p 가 정규 근사입니다. 신뢰구간의 절단"
          " 지수는 정확 분포에서 나오므로 (동점이 있을 때) p 와 CI 가 경계에서")
        L("     완전히 일치하지 않을 수 있습니다 — 대개 CI 쪽이 더 보수적입니다.")

    # ---------------- 해석 ----------------
    L("")
    L("[해석 / Interpretation]")
    if slow:
        L("    일부 기록이 느린/공명 호흡 레짐 → HF 기반 지표(HF·HF n.u.·LF/HF)의 "
          "방향은 신뢰할 수 없습니다. 시간영역 vagal 지표로 판단하세요.")
    rm = g.get("rmssd", {})
    if rm.get("n_a", 0) >= 2 and rm.get("n_b", 0) >= 2 and \
            _finite(rm.get("mw_p")):
        ph = rm.get("p_holm")
        lo, hi = rm.get("ci_low"), rm.get("ci_high")
        ci_s = (f", {pct}% CI [{_num(lo, 1)}, {_num(hi, 1)}] ms"
                if _finite(lo) and _finite(hi) else "")
        sig = "유의미" if _finite(ph) and ph < alpha else "유의하지 않음"
        L(f"    RMSSD: HL 이동량 {_num(rm.get('hl_shift'), 1)} ms{ci_s}, "
          f"Hedges g={_num(rm.get('hedges_g'), 2)},")
        L(f"           p={_num(rm.get('mw_p'), 4)} → Holm 보정 "
          f"p={_num(ph, 4)} → 군간 차이 {sig} (α={alpha:g}, "
          f"{m_tests}개 지표 보정).")
    # 표본이 작으면 완전분리(모든 개입값 > 모든 대조값)여도 정확검정의 최소 p가
    # 커서 보정 후 기각이 원천적으로 불가능합니다 — 이걸 밝히지 않으면 "유의하지
    # 않음"이 효과 없음으로 오독됩니다.
    na, nb = meta["n_a"], meta["n_b"]
    if na >= 1 and nb >= 1:
        min_p = 2.0 / math.comb(na + nb, na)
        if min_p * max(1, m_tests) >= alpha:
            L(f"    ※ n={na}/{nb} 에서 정확검정이 낼 수 있는 최소 p는 "
              f"{min_p:.4f} 이고, {m_tests}개 지표 보정 후에는 "
              f"{min(1.0, min_p * max(1, m_tests)):.4f} 입니다 — 즉 **효과가 아무리 "
              f"커도** α={alpha:g} 에서 Holm 기각이 불가능한 표본 수입니다. "
              f"주 지표를 사전 지정해 보정 없는 p를 보고하거나, 표본을 늘리세요.")
    L("    (주의: 무작위배정이 아니면 군간 차이는 인과가 아닙니다. 짝지은 "
      "pre–post 설계라면 --paired 를 쓰세요 — 그쪽이 검정력이 훨씬 높습니다.)")
    L("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 표본수 설계 리포트 (--power / --plan)
#
# 왜 여기 있나: --paired/--groups 는 "이 파일럿에서 효과가 있었나"에 답합니다.
# 임상/제약 연구자가 바로 다음에 묻는 것은 "그럼 본시험은 몇 명인가"이고,
# 그 답은 같은 요약통계(평균차·SD·효과크기)에서 바로 나옵니다. 같은 실행에서
# 같은 표로 내야 두 숫자가 다른 전처리에서 나오는 사고를 막습니다.
# --------------------------------------------------------------------------- #
def power_plan_paired(pairs: Sequence, *, target_power: float = 0.80,
                      alpha: float = 0.05, dropout: float = 0.0) -> dict:
    """짝지은 파일럿에서 지표별 본시험 표본수 계획을 계산.

    반환: {metric_key: power.plan_paired(...) 결과}. '_meta' 에 설계 조건.
    """
    bases = [flat_metrics(b) for b, _ in pairs]
    intervs = [flat_metrics(v) for _, v in pairs]
    out: dict = {}
    for key, _label, _d, _dir, _hf in _PAIRED_METRICS:
        s = paired_summary([bm.get(key) for bm in bases],
                           [vm.get(key) for vm in intervs], alpha=alpha)
        out[key] = plan_paired(s, target_power=target_power, alpha=alpha,
                               dropout=dropout)
    out["_meta"] = {"design": "paired", "n_pilot": len(pairs),
                    "target_power": target_power, "alpha": alpha,
                    "dropout": dropout}
    return out


def power_plan_groups(a_results: Sequence[HRVResult],
                      b_results: Sequence[HRVResult], *,
                      target_power: float = 0.80, alpha: float = 0.05,
                      dropout: float = 0.0) -> dict:
    """평행군 파일럿에서 지표별 **군당** 본시험 표본수 계획을 계산."""
    a_flat = [flat_metrics(r) for r in a_results]
    b_flat = [flat_metrics(r) for r in b_results]
    out: dict = {}
    for key, _label, _d, _dir, _hf in _GROUP_METRICS:
        s = unpaired_summary([fm.get(key) for fm in a_flat],
                             [fm.get(key) for fm in b_flat], alpha=alpha)
        out[key] = plan_parallel(s, target_power=target_power, alpha=alpha,
                                 dropout=dropout)
    out["_meta"] = {"design": "parallel", "n_pilot_a": len(a_results),
                    "n_pilot_b": len(b_results), "target_power": target_power,
                    "alpha": alpha, "dropout": dropout}
    return out


# 표본수 계획 CSV 열.
# 열 이름은 JSON 키(n_t / n_nonparam / n_recommended / n_enrol)와 같은 어간을
# 쓰고 접미사로 관측/보수적 기준만 구분합니다.
_PLAN_CSV_COLS = ["design", "n_pilot", "mean_diff", "sd_used",
                  "n_exact_floor",
                  "d_observed", "n_t_observed", "n_nonparam_observed",
                  "n_recommended_observed", "n_enrol_observed",
                  "ci_low", "ci_high",
                  "d_conservative", "n_t_conservative",
                  "n_nonparam_conservative", "n_recommended_conservative",
                  "n_enrol_conservative", "target_power", "alpha", "dropout",
                  "note"]


def _plan_row(key: str, p: dict) -> List:
    obs = p.get("observed") or {}
    con = p.get("conservative") or {}
    n_pilot = p.get("n_pilot")
    if n_pilot is None:
        n_pilot = f"{p.get('n_pilot_a')}/{p.get('n_pilot_b')}"
    return [key,
            p.get("design"), n_pilot,
            _fmt_cell(p.get("mean_diff"), 4), _fmt_cell(p.get("sd_used"), 4),
            _fmt_cell(obs.get("n_exact_floor"), 0),
            _fmt_cell(obs.get("d"), 4), _fmt_cell(obs.get("n_t"), 0),
            _fmt_cell(obs.get("n_nonparam"), 0),
            _fmt_cell(obs.get("n_recommended"), 0),
            _fmt_cell(obs.get("n_enrol"), 0),
            _fmt_cell(p.get("ci_low"), 4), _fmt_cell(p.get("ci_high"), 4),
            _fmt_cell(con.get("d"), 4), _fmt_cell(con.get("n_t"), 0),
            _fmt_cell(con.get("n_nonparam"), 0),
            _fmt_cell(con.get("n_recommended"), 0),
            _fmt_cell(con.get("n_enrol"), 0),
            _fmt_cell(p.get("target_power"), 3), _fmt_cell(p.get("alpha"), 3),
            _fmt_cell(p.get("dropout"), 3), p.get("note") or ""]


def power_plan_to_csv(plan: dict) -> str:
    """표본수 계획을 지표당 한 행인 CSV로 직렬화(헤더 포함)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric"] + _PLAN_CSV_COLS)
    for key, _label, _d, _dir, _hf in _PAIRED_METRICS:
        p = plan.get(key)
        if p is None:
            continue
        writer.writerow(_plan_row(key, p))
    return buf.getvalue()


def _n_cell(v) -> str:
    """표본수 셀 — None 은 '—'(효과가 0을 포함하거나 너무 작아 산출 불가)."""
    return "—" if v is None else str(int(v))


def render_power_plan(plan: dict) -> str:
    """표본수 계획을 사람이 읽는 블록으로 렌더링 (--paired/--groups 에 덧붙임)."""
    meta = plan["_meta"]
    design = meta["design"]
    tp = meta["target_power"]
    alpha = meta["alpha"]
    drop = meta["dropout"]
    per = "군당 " if design == "parallel" else ""
    lines: List[str] = []
    L = lines.append
    L("[C] 표본수 설계 / Sample-size planning — **다음(본)시험용**")
    if design == "parallel":
        L(f"  파일럿 n = {meta['n_pilot_a']}/{meta['n_pilot_b']} (대조/개입), "
          f"목표 검정력 {_pct(tp)}%, α={alpha:g} 양측"
          + (f", 탈락률 {_pct(drop)}%" if drop else ""))
    else:
        L(f"  파일럿 n = {meta['n_pilot']}, 목표 검정력 {_pct(tp)}%, "
          f"α={alpha:g} 양측" + (f", 탈락률 {_pct(drop)}%" if drop else ""))
    L("  (헤더의 n 은 파일럿 전체 인원입니다 — 지표에 따라 NaN 인 기록이 빠져 "
      "실제 검정에 쓰인 n 은 더 작을 수 있습니다.)")
    head_obs = f"{per}N(관측)" if not drop else f"{per}N(관측)→모집"
    head_con = f"{per}N(보수적)" if not drop else f"{per}N(보수적)→모집"
    L("  " + _lj("metric", 16) + _rj("d(관측)", 12) + _rj(head_obs, 20)
      + _rj("d(CI경계)", 12) + _rj(head_con, 22))
    L("  " + "-" * 82)
    any_row = False
    floored_any = False
    floor_n = None
    for key, label, _d, _dir, _hf in _PAIRED_METRICS:
        p = plan.get(key)
        if p is None:
            continue
        obs = p.get("observed") or {}
        con = p.get("conservative") or {}
        if not obs:
            L("  " + _lj(label, 16) + _rj("(계획 불가)", 12))
            continue
        if floor_n is None:
            floor_n = obs.get("n_exact_floor")
        any_row = True
        n_o = _n_cell(obs.get("n_recommended")) + ("\u2021" if obs.get("floored") else "")
        n_c = _n_cell(con.get("n_recommended")) + ("\u2021" if con.get("floored") else "")
        if obs.get("floored") or con.get("floored"):
            floored_any = True
        if drop:
            n_o += f"→{_n_cell(obs.get('n_enrol'))}"
            n_c += f"→{_n_cell(con.get('n_enrol'))}"
        L("  " + _lj(label, 16) + _rj(_num(obs.get("d"), 3), 12)
          + _rj(n_o, 20) + _rj(_num(con.get("d"), 3), 12) + _rj(n_c, 22))
    L("")
    if not any_row:
        notes = {p.get("note") for k, p in plan.items()
                 if k != "_meta" and p.get("note")}
        L("  어떤 지표도 표본수를 낼 수 없습니다. 원인:")
        for note in sorted(notes):
            L(f"    · {note}")
        L("")
        return "\n".join(lines)
    L(f"  N 은 {per}**완료 인원**(양측 t 검정 기준 N 에 정규분포 하 순위검정 "
      f"효율 보정 ARE=3/π → +4.7% 적용)")
    if drop:
        L(f"  이고, '→' 뒤는 탈락률 {_pct(drop)}% 를 반영해 **모집해야 할** "
          "인원(⌈N/(1−탈락률)⌉)입니다. 탈락자가 아무 정보도 주지 않는다는 "
          "가정(완료자 분석)이며, ITT 분석에는 그대로 쓰면 안 됩니다.")
    L("")
    L("  [두 N 을 어떻게 읽나] — **범위**로 읽으세요, 하나를 고르는 게 아닙니다.")
    L("   · d(관측): 파일럿에서 본 효과 그대로. 파일럿 효과크기는 표본오차 때문에")
    L("     **낙관적으로 치우치므로** 이 N 은 사실상 **하한**입니다.")
    L("   · d(CI 경계): 평균차 양측 CI 의 0 쪽 경계 = **97.5% 단측 신뢰한계**")
    if True:
        L("     (Browne 1995 / Kieser & Wassmer 1996 의 신뢰한계 접근). 이 문헌은")
        L("     97.5% 한계가 **과도하게 크게 나온다**고 보고하며 60–80% 단측 한계를")
        L("     권합니다 — 즉 이 N 은 **상한**이고, 파일럿 n 이 작으면 수천 명까지")
        L("     치솟습니다. 그럴 때 그 숫자는 설계값이 아니라 **'이 파일럿만으로는")
        L("     표본수를 정할 수 없다'** 는 신호입니다. 그 경우 선행 문헌값이나")
        L("     임상적 최소 의미차(MCID)를 `--plan --delta` 에 넣어 설계하세요.")
    L("   · '—' 는 N 을 낼 수 없다는 표시이고 원인은 셋 중 하나입니다:")
    L("     (1) 그 지표의 CI 가 0을 포함 — 효과 방향조차 확정 못 함(보수적 열),")
    L("     (2) 관측 효과크기가 정확히 0,")
    L(f"     (3) 효과가 너무 작아 상한 {MAX_N:,}명으로도 목표 검정력에 못 미침.")
    L("     어느 쪽이든 숫자를 지어내지 않습니다.")
    L("   · 여기 쓰인 CI 는 **모수적 t 신뢰구간**(평균차 기준)입니다 — 위 [B] 표의")
    L("     CI 는 분포무관 Hodges–Lehmann 구간이라 같은 자료에서도 값이 다릅니다.")
    L("   · d(관측)에는 Hedges 소표본 편의 보정 J(자유도)를 적용했습니다 — 표본")
    L("     효과크기는 모집단 효과를 과대추정하므로, 보정 없이는 N 이 작게 나옵니다.")
    L("   · 두 N 모두 **평균차의 불확실성만** 반영합니다. 파일럿의 SD 자체도")
    L("     불확실하고(n=10이면 σ의 95% CI ≈ [0.69ŝ, 1.83ŝ]), N ∝ 1/d² ∝ ŝ² 이라")
    L("     그것만으로 3배 가까이 흔들립니다.")
    L("")
    if floored_any and floor_n:
        L(f"  ‡ = 정확검정 하한 {per}{floor_n}명으로 올린 값. {per}이보다 적으면 "
          f"부호순위/Mann–Whitney")
        L(f"    정확검정이 낼 수 있는 **최소 p 가 이미 α={alpha:g} 를 넘어** 효과가 "
          "아무리 커도 기각이")
        L("    불가능합니다(= 실제 검정력 0). t 기준 N 이 더 작게 나와도 그 설계는 "
          "쓸 수 없습니다.")
        L("")
    L("  ※ 사후 검정력(observed power)은 계산하지 않습니다 — p값의 단조함수라")
    L("     새 정보가 없고, 유의하지 않은 결과의 사후 변명으로 오용됩니다.")
    L("     (다만 N(관측)도 관측 효과크기의 단조함수이므로 같은 한계를 공유합니다.)")
    L(f"  ※ 이 표는 **보정 없는 α={alpha:g}** 로 계산했습니다. 위 [B] 블록은 "
      f"{len(_PAIRED_METRICS)}개 지표에")
    L("     Holm/BH 를 겁니다 — 보정된 기준으로 검정할 계획이라면 필요 N 이 약 "
      "1.7배로 늘어납니다.")
    L(f"     주 평가변수 하나를 **사전 지정**하거나, "
      f"`--alpha {alpha / len(_PAIRED_METRICS):.4f}`(=α/{len(_PAIRED_METRICS)}) 로 "
      "다시 계산하세요.")
    L("     여러 지표의 N 중 가장 작은 것을 고르면 검정력이 부풀려집니다.")
    L("  ※ 양측 우월성(superiority) 설계만 지원합니다 — 단측·비열등성(NI)·"
      "동등성 설계는")
    L("     여기서 나온 N 을 그대로 쓰면 안 됩니다.")
    L("  ※ HF power 처럼 오른쪽으로 크게 치우친 지표는 원 척도의 평균/SD 로 낸 N 이")
    L("     잘 맞지 않습니다 — ln 변환한 값(ln_hf)으로 설계하는 편이 안전합니다.")
    L("")
    return "\n".join(lines)


def render_plan(grid: dict) -> str:
    """파일럿 없이 가정값만으로 만든 계획표(--plan)를 렌더링."""
    design = grid["design"]
    per = "군당 " if design == "parallel" else ""
    alpha = grid["alpha"]
    sd = grid["sd"]
    delta = grid.get("delta")
    n = grid.get("n")
    drop = grid.get("dropout", 0.0)
    lines: List[str] = []
    L = lines.append
    L("=" * 78)
    L("  hrvkit — 표본수·검정력 설계 / Sample-size planning")
    L("=" * 78)
    label = {"paired": "짝지은(pre–post, 동일 피험자)",
             "parallel": "평행군(독립 2군)"}[design]
    L(f"  설계: {label}   α={alpha:g} 양측   가정 SD={sd:g}"
      + (f"   탈락률 {_pct(drop)}%" if drop else ""))
    if delta is not None:
        L(f"  탐지하려는 차이 Δ = {delta:g}  →  효과크기 d = {abs(delta) / sd:.3f}")
    if n is not None:
        L(f"  확보 가능한 {per}표본수 n = {n}")
    L("")
    floored_any = False
    if delta is not None:
        L(f"  [필요 {per}표본수]")
        L("    " + _lj("목표 검정력", 14) + _rj("N(t 검정)", 14)
          + _rj("N(순위검정)", 16) + (_rj("모집 인원", 14) if drop else ""))
        L("    " + "-" * (44 + (14 if drop else 0)))
        for r in grid["rows"]:
            mark = "\u2021" if r.get("floored") else ""
            req = "  \u25c0 --target-power" if r.get("requested") else ""
            row = ("    " + _lj(_pct(r["target_power"]) + "%", 14)
                   + _rj(_n_cell(r.get("n_t")), 14)
                   + _rj(_n_cell(r.get("n_recommended")) + mark, 16))
            if drop:
                row += _rj(_n_cell(r.get("n_enrol")), 14)
            L(row + req)
            if r.get("floored"):
                floored_any = True
        L("")
    if n is not None:
        L(f"  [탐지 가능한 최소 차이 MDD — {per}n={n} 에서]")
        L("    " + _lj("목표 검정력", 14) + _rj("MDD(t 검정)", 16)
          + _rj("MDD(순위검정)", 18) + _rj("= d", 10))
        L("    " + "-" * 60)
        for r in grid["rows"]:
            mdd = r.get("mdd", float("nan"))
            d_eq = (mdd / sd) if _finite(mdd) else float("nan")
            req = "  \u25c0 --target-power" if r.get("requested") else ""
            L("    " + _lj(_pct(r["target_power"]) + "%", 14)
              + _rj(_num(mdd, 3), 16)
              + _rj(_num(r.get("mdd_nonparam"), 3), 18)
              + _rj(_num(d_eq, 3), 10) + req)
        L("")
    if "power_at_n" in grid:
        L(f"  [검정력] {per}n={n}, Δ={delta:g}, SD={sd:g} → "
          f"검정력 = {_pct(grid['power_at_n'])}% (t 검정 기준)")
        L("")
    if delta is not None:
        L("  N(t 검정) 은 비중심 t 분포를 수치적분(Simpson)해 오차 1e-9 이내로")
        L("  계산한 값입니다(정규근사식이 아닙니다). N(순위검정) 은 hrvkit 이")
        L("  실제로 쓰는 Wilcoxon/Mann–Whitney 의 **정규분포 하** 점근상대효율")
        L("  (ARE = 3/π ≈ 0.955)을 반영해 +4.7% 한 값입니다.")
    else:
        L("  MDD 는 비중심 t 분포를 수치적분해 구한 값이고, MDD(순위검정) 은")
        L("  같은 n 에서 순위검정이 탐지할 수 있는 차이(ARE 반영, ×1/√0.955 ≈ +2.3%)")
        L("  입니다. 두 값 모두 **정규분포 하** 효율 기준입니다.")
    L("  ※ ARE 는 점근값이고 정규분포 기준입니다 — 꼬리가 두꺼우면 순위검정이 더")
    L("     효율적이지만, 최악의 연속분포에서는 ARE 가 0.864 까지 내려가 +15.7% 가")
    L("     필요할 수 있습니다. 또 순위검정의 귀무가설은 평균차가 아니라 확률적")
    L("     순서(HL 이동량)이므로, 평균/SD 로 만든 d 에 ARE 를 곱하는 것은 근사적인")
    L("     다리입니다 — 오른쪽으로 치우친 HRV 지표(HF power 등)에서는 특히 그렇습니다.")
    if floored_any:
        floor_n = grid["rows"][0].get("n_exact_floor")
        L(f"  ‡ = 정확검정 하한 {per}{floor_n}명으로 올린 값 — 이보다 적으면 순위검정이")
        L(f"     낼 수 있는 최소 p 가 이미 α={alpha:g} 를 넘어 기각이 불가능합니다.")
    L("  ※ **보정 없는 α** 기준입니다. 여러 지표에 Holm/BH 를 걸 계획이면 필요 N 이")
    L("     약 1.7배로 늘어납니다 — `--alpha` 를 낮춰 다시 계산하세요.")
    L(f"  ※ '—' 는 효과가 너무 작아 상한 {MAX_N:,}명으로도 목표 검정력에 도달하지")
    L("     못한다는 뜻입니다(무한대가 아니라 '이 도구의 탐색 범위 밖').")
    L("  ※ 양측 우월성 설계 전용입니다(단측·비열등성·동등성 설계 미지원).")
    L("  ※ --sd 는 점추정치일 뿐이고 그 자체도 불확실합니다. N ∝ 1/d² ∝ SD² 이라")
    L("     SD 를 10% 잘못 잡으면 N 이 약 21% 틀어집니다 — 보수적으로 잡으세요.")
    if design == "paired":
        L("  ※ 짝지은 설계의 SD 는 **개인 내 차이(post−pre)의 SD** 입니다 —")
        L("     집단 SD 를 넣으면 필요 표본수가 크게 과대추정됩니다.")
    else:
        L("  ※ 평행군 설계의 SD 는 **군 내 합동 SD** 이고 N 은 **군당** 인원입니다")
        L("     (총 인원은 2N).")
    L("")
    return "\n".join(lines)
