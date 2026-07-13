"""사람이 읽는 HRV 리포트 렌더링 (한국어 + 영어 라벨)."""

from __future__ import annotations

import math
from typing import List

from .analyze import HRVResult

__all__ = ["render_text"]


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
    L(f"    SDNN               : {_num(t['sdnn'], 2)} ms   (전체 변동성)")
    L(f"    RMSSD              : {_num(t['rmssd'], 2)} ms   (단기·부교감)")
    L(f"    SDSD               : {_num(t['sdsd'], 2)} ms")
    L(f"    pNN50 / pNN20      : {_num(t['pnn50'], 1)}% / {_num(t['pnn20'], 1)}%")
    L(f"    CVNN               : {_num(t['cvnn'], 4)}   (= SDNN/meanRR)")
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
    L(f"    LF/HF ratio        : {_num(f['lf_hf_ratio'], 2)}")
    if f.get("peak_lf") is not None or f.get("peak_hf") is not None:
        L(f"    peak LF / HF       : {_num(f.get('peak_lf'), 3)} / "
          f"{_num(f.get('peak_hf'), 3)} Hz")

    # [3] 비선형
    L("")
    L("[3] 비선형 / Nonlinear (Poincaré + SampEn)")
    L(f"    SD1                : {_num(p['sd1'], 2)} ms   (단기·부교감)")
    L(f"    SD2                : {_num(p['sd2'], 2)} ms   (장기)")
    L(f"    SD1/SD2            : {_num(p['sd1_sd2_ratio'], 3)}")
    L(f"    ellipse area       : {_num(p['ellipse_area'], 1)} ms²")
    L(f"    SampEn (m=2)       : {_num(res.sampen, 3)}   (복잡성/규칙성)")

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
