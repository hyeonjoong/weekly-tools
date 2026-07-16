"""오케스트레이션 — RR 시계열 하나를 받아 정제·전 지표 계산·해석을 조립.

파이프라인:
  clean_rr(이상박동 탐지/보정) → time_domain → poincare(+SampEn) → frequency_domain
결과는 HRVResult 데이터클래스로 담고, 사람이 읽는 리포트/JSON로 렌더링합니다.

BELL-001 작용기전 연결:
  느린 호흡 → 부교감신경 활성 ↑ → 호흡성 동성부정맥(RSA) ↑ → HF/RMSSD/SD1 ↑
  → 서파수면 촉진. 따라서 RMSSD·SD1·HF(정규화 단위 hf_nu)의 상승과 LF/HF 하락을
  '부교감 우세' 방향으로 해석합니다.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .artifacts import clean_rr
from .frequency import frequency_domain
from .nonlinear import dfa, poincare, sample_entropy
from .timedomain import time_domain

__all__ = ["HRVResult", "analyze_rr", "flat_metrics", "FLAT_COLUMNS"]

# 배치/CSV/비교 출력에 쓰는 평탄화된 지표 열 순서 (key, 소수 자릿수).
FLAT_COLUMNS = [
    ("source", None),
    ("n_input", 0),
    ("pct_artifacts", 1),
    ("mean_nn", 1),
    ("median_nn", 1),
    ("mean_hr", 1),
    ("sdnn", 2),
    ("rmssd", 2),
    ("pnn50", 1),
    ("pnn20", 1),
    ("cvnn", 4),
    ("hti", 2),
    ("tinn", 1),
    ("lf_power", 1),
    ("hf_power", 1),
    ("total_power", 1),
    ("lf_hf_ratio", 3),
    ("lf_nu", 1),
    ("hf_nu", 1),
    ("ln_hf", 3),
    ("resp_rate_brpm", 1),
    ("sd1", 2),
    ("sd2", 2),
    ("sd1_sd2_ratio", 3),
    ("sampen", 3),
    ("dfa_alpha1", 3),
    ("dfa_alpha2", 3),
]


def flat_metrics(res: "HRVResult") -> Dict[str, float]:
    """HRVResult를 배치/CSV/비교용 평탄 dict로 변환 (FLAT_COLUMNS 키)."""
    src: Dict[str, float] = {}
    # CSV/배치용 source 는 파일명(basename)만 — 전체 경로가 스프레드시트로 공유될 때
    # 디렉터리 트리·사용자명 등이 새는 것을 줄입니다(전체 경로는 JSON to_dict 참조).
    src["source"] = os.path.basename(res.source) if res.source else res.source
    src["n_input"] = res.n_input
    src["pct_artifacts"] = res.pct_artifacts
    src.update(res.time)
    src.update(res.freq)
    src.update(res.poincare)
    src.update(res.dfa or {})
    src["sampen"] = res.sampen
    out: Dict[str, float] = {}
    for key, _ in FLAT_COLUMNS:
        out[key] = src.get(key)
    return out


@dataclass
class HRVResult:
    # 입력/전처리 메타
    n_input: int
    n_artifacts: int
    pct_artifacts: float
    clean_method: str
    unit: str
    source: str
    fs: float
    # 지표 묶음
    time: Dict[str, float]
    freq: Dict[str, float]
    poincare: Dict[str, float]
    sampen: float
    dfa: Dict[str, float] = field(default_factory=dict)
    # 해석
    takeaway: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "unit": self.unit,
            "resample_fs": self.fs,
            "n_input_beats": self.n_input,
            "n_artifacts": self.n_artifacts,
            "pct_artifacts": self.pct_artifacts,
            "clean_method": self.clean_method,
            "time_domain": self.time,
            "frequency_domain": self.freq,
            "nonlinear": {**self.poincare, "sampen": self.sampen, **self.dfa},
            "interpretation": self.takeaway,
            "warnings": self.warnings,
        }


def _interpret(time: Dict[str, float], freq: Dict[str, float],
               pc: Dict[str, float]) -> str:
    """RMSSD·HF·LF/HF를 BELL-001 기전에 연결한 한 줄 요약.

    느린/공명 호흡 레짐(호흡 피크가 LF)에서는 HF n.u./LF/HF의 방향 해석이
    역전되므로, 대역에 무관한 시간영역 vagal 지표(RMSSD·SD1·pNN50)에 근거해
    해석합니다.
    """
    rmssd = time["rmssd"]
    hf_nu = freq["hf_nu"]
    lf_hf = freq["lf_hf_ratio"]
    sd1 = pc.get("sd1", float("nan"))
    pnn50 = time.get("pnn50", float("nan"))

    if freq.get("slow_breathing_regime"):
        # 호흡이 LF에 실림 → HF 기반 지표 대신 시간영역 vagal 지표로 판단.
        vagal = (rmssd >= 30.0) or (sd1 >= 20.0) or (pnn50 >= 15.0)
        if vagal:
            tone = ("느린/공명 호흡 레짐(호흡 피크가 LF) — 이 경우 HF n.u.·LF/HF의 "
                    "방향 해석은 역전되니 무시하세요. 시간영역 vagal 지표(RMSSD·SD1·"
                    "pNN50)가 높아 부교감(미주신경) 우세로 보이며, 느린 호흡 → RSA↑ → "
                    "HRV↑ 라는 BELL-001 기전과 일치합니다.")
        else:
            tone = ("느린/공명 호흡 레짐(호흡 피크가 LF) — HF n.u./LF/HF 방향은 "
                    "신뢰할 수 없습니다. 시간영역 vagal 지표(RMSSD·SD1·pNN50)로 판단하세요.")
        return (f"RMSSD={rmssd:.1f} ms, SD1={sd1:.1f} ms, pNN50={pnn50:.1f}%. {tone}")

    if hf_nu >= 50.0 or lf_hf < 1.0:
        tone = ("부교감(미주신경) 우세 — 높은 RMSSD/HF/SD1은 호흡성 동성부정맥(RSA)이 "
                "잘 실린 상태로, 느린 호흡 → 부교감 활성 ↑ → HRV ↑ → 서파수면 촉진이라는 "
                "BELL-001 기전과 일치하는 방향입니다.")
    elif lf_hf > 2.5 or hf_nu < 30.0:
        tone = ("교감 쪽으로 치우침 — 낮은 HF/높은 LF/HF는 각성·스트레스 부하를 시사합니다. "
                "느린 호흡 개입으로 RSA(HF)와 RMSSD를 끌어올릴 여지가 큽니다.")
    else:
        tone = ("교감-부교감 균형 구간 — 느린 호흡 개입은 HF/RMSSD를 더 높여 부교감 쪽으로 "
                "이동시키는 것을 목표로 합니다.")

    return (f"RMSSD={rmssd:.1f} ms, HF(n.u.)={hf_nu:.1f}, LF/HF={lf_hf:.2f}. {tone}")


def analyze_rr(rr,
               *,
               source: str = "",
               unit: str = "ms",
               clean_method: str = "interpolate",
               fs: float = 4.0,
               min_rr: float = 300.0,
               max_rr: float = 2000.0,
               rel_thresh: float = 0.2,
               nperseg: Optional[int] = None,
               do_sampen: bool = True) -> HRVResult:
    """RR(ms) 시계열 하나를 전 지표로 분석해 HRVResult를 반환.

    rr: RR/NN 간격(ms) 리스트. (단위 변환은 dataio.load_series 가 미리 수행)
    """
    rr = [float(x) for x in rr]
    n_input = len(rr)
    if n_input < 2:
        raise ValueError("분석에는 최소 2개의 박동이 필요합니다.")

    warnings: List[str] = []

    cleaned, flags = clean_rr(rr, method=clean_method, min_rr=min_rr,
                              max_rr=max_rr, rel_thresh=rel_thresh)
    n_art = sum(1 for f in flags if f)
    pct_art = 100.0 * n_art / len(flags) if flags else 0.0
    if pct_art > 5.0:
        warnings.append(
            f"이상박동 비율이 {pct_art:.1f}% 로 높습니다 (>5%). 기록 품질을 확인하세요 — "
            "특히 RMSSD/pNN50/HF 해석에 주의.")

    if clean_method == "remove" and len(cleaned) < 2:
        raise ValueError("이상박동 제거 후 남은 박동이 부족합니다.")

    # 정제 후에도 남을 수 있는 비생리적 값(0·음수·NaN/inf)을 제거해 지표 계산의
    # ZeroDivision/NaN 오염을 막습니다. interpolate/none 경로에서 '전부 이상'인
    # 구간은 원본을 그대로 두므로 여기서 최종 방어합니다.
    n_before = len(cleaned)
    cleaned = [v for v in cleaned if math.isfinite(v) and v > 0.0]
    n_nonphys = n_before - len(cleaned)
    if n_nonphys:
        warnings.append(
            f"{n_nonphys}개 박동이 비생리적 값(0/음수/NaN)으로 지표 계산에서 "
            "제외되었습니다. 입력 단위/열 또는 --timestamps 필요 여부를 확인하세요.")
    if len(cleaned) < 2:
        raise ValueError(
            "유효한(양수·유한) 박동이 2개 미만입니다. 입력 단위/열을 확인하거나 "
            "박동 발생시각이라면 --timestamps 를 사용하세요.")

    time = time_domain(cleaned)

    try:
        pc = poincare(cleaned)
    except ValueError:
        pc = {"sd1": float("nan"), "sd2": float("nan"),
              "sd1_sd2_ratio": float("nan"), "ellipse_area": float("nan")}

    freq: Dict[str, float]
    try:
        freq = frequency_domain(cleaned, fs=fs, nperseg=nperseg)
    except ValueError as exc:
        warnings.append(f"주파수영역 분석 생략: {exc}")
        freq = {k: float("nan") for k in (
            "vlf_power", "lf_power", "hf_power", "total_power", "lf_hf_ratio",
            "lf_nu", "hf_nu", "ln_hf", "vlf_pct", "lf_pct", "hf_pct", "peak_lf",
            "peak_hf", "resp_rate_hz", "resp_rate_brpm")}
        freq.update({"resample_fs": fs, "duration_sec": float("nan"),
                     "n_resampled": 0, "welch_nperseg": 0, "welch_nfft": 0,
                     "welch_segments": 0, "resp_source": None,
                     "slow_breathing_regime": False, "welch_segment_sec": 0.0,
                     "freq_resolution_hz": float("nan"), "vlf_bins": 0,
                     "lf_bins": 0, "hf_bins": 0, "vlf_reliable": False})

    sampen = float("nan")
    if do_sampen:
        if len(cleaned) > 3000:
            warnings.append("SampEn 생략: 박동 수가 많아(>3000) 계산 비용이 큽니다 "
                            "(--no-sampen 없이도 자동 생략).")
        else:
            sampen = sample_entropy(cleaned)

    dfa_metrics = dfa(cleaned)

    if freq.get("slow_breathing_regime"):
        warnings.append(
            "느린/공명 호흡 레짐으로 감지됨(호흡 피크가 LF 대역). 이 경우 HF n.u.·LF/HF의 "
            "'부교감 방향' 해석이 역전되므로, 시간영역 vagal 지표(RMSSD·SD1·pNN50)로 "
            "판단하세요. 호흡수는 LF 피크에서 추정했습니다.")

    takeaway = _interpret(time, freq, pc)

    return HRVResult(
        n_input=n_input,
        n_artifacts=n_art,
        pct_artifacts=pct_art,
        clean_method=clean_method,
        unit=unit,
        source=source,
        fs=fs,
        time=time,
        freq=freq,
        poincare=pc,
        sampen=sampen,
        dfa=dfa_metrics,
        takeaway=takeaway,
        warnings=warnings,
    )
