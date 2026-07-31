"""구간(epoch)별 HRV — 긴 기록을 창(window)으로 쪼개 시간에 따른 변화를 봅니다.

왜 필요한가
-----------
Task Force(1996) 표준의 시간·주파수영역 지표는 **정상성(stationarity)** 을 가정한
**단기(5분) 기록** 에 대해 정의돼 있습니다. 30분·야간 기록을 한 덩어리로 계산하면

  - 각성→수면 전환, 자세 변화, 개입 순응(habituation) 같은 **느린 추세**가
    SDNN 을 부풀리고(구간 간 평균 이동이 분산으로 들어감),
  - "개입 중 언제 효과가 붙었는가" 라는 임상적으로 가장 중요한 질문에
    답할 수 없습니다.

이 모듈은 기록을 겹치거나 겹치지 않는 창으로 나눠 창마다 전 지표를 계산하고,
창 사이의 **단조 추세를 Mann–Kendall 검정**으로 정량화합니다. 겹치지 않는 5분 창은
Task Force 의 장기(long-term) 지표 정의 그 자체이기도 합니다:

  SDANN      = 연속 5분 구간 **평균 NN 들의 표준편차** (ms) — 초저주파/서파 성분
  SDNN index = 연속 5분 구간 **SDNN 들의 평균** (ms) — 구간 내부 변동성

정제(이상박동 보정)는 **기록 전체에서 한 번만** 수행합니다. 창마다 다시 탐지하면
(a) 국소 중앙값 기준선이 창 경계에서 끊기고 (b) 이미 보간된 값 위에서 탐지가 돌아
이상박동 비율이 0 으로 잘못 보고됩니다.

꼬리(마지막 불완전 구간)는 **버립니다** — 길이가 다른 구간을 섞으면 SDANN·추세가
구간 길이에 오염됩니다. 버린 초 수는 notes 로 보고합니다(숨기지 않음).
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .analyze import HRVResult, analyze_rr, flat_metrics
from .artifacts import clean_rr
from .stats import benjamini_hochberg, holm_adjust, mann_kendall

__all__ = ["Window", "WindowSeries", "analyze_windows", "window_trends",
           "long_term_indices", "DEFAULT_WINDOW_SEC", "MIN_WINDOW_BEATS",
           "TREND_METRICS", "MAX_WINDOWS", "SAMPEN_MAX_WINDOWS"]

# Task Force(1996) 단기 기록 표준 길이.
DEFAULT_WINDOW_SEC = 300.0
# 창 하나를 분석하기 위한 최소 박동 수. 20박동이면 시간영역은 계산되지만
# 주파수영역은 기록 길이(20 s) 조건에서 따로 걸러집니다.
MIN_WINDOW_BEATS = 20
# 창 수 상한 — Theil–Sen 기울기가 창 수의 제곱 비용이라 상한 없이는
# `--step 0.1` 같은 오타가 수십 GB 할당/수십 분 정지가 됩니다.
MAX_WINDOWS = 2000
# 이 개수를 넘으면 SampEn(창당 O(n²))을 자동 생략합니다.
SAMPEN_MAX_WINDOWS = 300

# 창 사이 추세를 검정할 지표 (key, 라벨, 자릿수).
TREND_METRICS = [
    ("mean_hr", "mean HR (bpm)", 1),
    ("rmssd", "RMSSD (ms)", 2),
    ("sdnn", "SDNN (ms)", 2),
    ("pnn50", "pNN50 (%)", 1),
    ("hf_nu", "HF (n.u.)", 1),
    ("lf_hf_ratio", "LF/HF", 3),
    ("sd1", "SD1 (ms)", 2),
    ("sampen", "SampEn", 3),
    ("dfa_alpha1", "DFA α1", 3),
]


@dataclass
class Window:
    """한 구간(epoch)의 분석 결과."""
    index: int                      # 0-기반 구간 번호
    start_sec: float                # 기록 시작으로부터의 구간 시작(초)
    end_sec: float                  # 구간 끝(초) = start + window_sec
    n_beats: int                    # 구간에 든 박동 수
    n_artifacts: int                # 그중 이상박동으로 표시된 수
    pct_artifacts: float
    result: Optional[HRVResult] = None
    error: Optional[str] = None     # 분석 불가 사유(박동 부족 등)

    @property
    def ok(self) -> bool:
        return self.result is not None


@dataclass
class WindowSeries:
    """기록 하나를 구간별로 분석한 전체 결과."""
    source: str
    window_sec: float
    step_sec: float
    n_input: int
    n_artifacts: int
    pct_artifacts: float
    clean_method: str
    duration_sec: float
    windows: List[Window] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok_windows(self) -> List[Window]:
        return [w for w in self.windows if w.ok]

    @property
    def overlapping(self) -> bool:
        return self.step_sec < self.window_sec

    def values(self, key: str) -> List[float]:
        """분석에 성공한 구간들의 지표값 리스트(순서 유지, NaN 포함)."""
        return [flat_metrics(w.result).get(key) for w in self.ok_windows]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "window_sec": self.window_sec,
            "step_sec": self.step_sec,
            "overlapping": self.overlapping,
            "n_input_beats": self.n_input,
            "n_artifacts": self.n_artifacts,
            "pct_artifacts": self.pct_artifacts,
            "clean_method": self.clean_method,
            "duration_sec": self.duration_sec,
            "n_windows": len(self.windows),
            "n_windows_ok": len(self.ok_windows),
            "windows": [
                {
                    "index": w.index,
                    "start_sec": w.start_sec,
                    "end_sec": w.end_sec,
                    "n_beats": w.n_beats,
                    "n_artifacts": w.n_artifacts,
                    "pct_artifacts": w.pct_artifacts,
                    "error": w.error,
                    "metrics": (flat_metrics(w.result) if w.ok else None),
                    # 구간은 짧아 VLF가 거의 항상 신뢰 불가입니다. 그런데
                    # total_power 는 정의상 VLF를 포함하므로, 이 플래그 없이
                    # 구간별 total_power 를 쓰면 편향을 모르고 쓰게 됩니다.
                    "vlf_reliable": (bool(w.result.freq.get("vlf_reliable"))
                                     if w.ok else None),
                    "warnings": (w.result.warnings if w.ok else []),
                }
                for w in self.windows
            ],
            "long_term": long_term_indices(self),
            "trends": window_trends(self),
            "notes": self.notes,
        }


def _start_times_sec(values: Sequence[float]) -> List[float]:
    """각 박동 간격의 **시작 시각**(초). t_0 = 0, t_i = Σ_{k<i} v_k / 1000."""
    out: List[float] = []
    acc = 0.0
    for v in values:
        out.append(acc)
        acc += v / 1000.0
    return out


def analyze_windows(rr,
                    *,
                    window_sec: float = DEFAULT_WINDOW_SEC,
                    step_sec: Optional[float] = None,
                    min_beats: int = MIN_WINDOW_BEATS,
                    source: str = "",
                    clean_method: str = "interpolate",
                    fs: float = 4.0,
                    min_rr: float = 300.0,
                    max_rr: float = 2000.0,
                    rel_thresh: float = 0.2,
                    nperseg: Optional[int] = None,
                    do_sampen: bool = True) -> WindowSeries:
    """RR(ms) 시계열을 window_sec 초 창으로 나눠 창마다 전 지표를 계산.

    step_sec 을 주면 창이 겹칩니다(예: window 300, step 60 → 5분 창을 1분씩 밀며
    슬라이딩). 겹치는 창은 **서로 독립이 아니므로** 추세 p값이 낙관적이 되고
    SDANN 정의도 깨집니다 — 그 경우 SDANN 은 계산하지 않고 notes 로 알립니다.

    마지막 불완전 구간은 버립니다(버린 초는 notes 에 보고).
    """
    rr = [float(x) for x in rr]
    n_input = len(rr)
    if n_input < 2:
        raise ValueError("구간 분석에는 최소 2개의 박동이 필요합니다.")
    if not (window_sec > 0):
        raise ValueError("--window 는 양수여야 합니다.")
    step = float(step_sec) if step_sec is not None else float(window_sec)
    if not (step > 0):
        raise ValueError("--step 은 양수여야 합니다.")
    if step > window_sec:
        raise ValueError(
            "--step 이 --window 보다 크면 구간 사이에 분석되지 않는 빈틈이 "
            "생깁니다. step ≤ window 로 지정하세요.")
    min_beats = max(2, int(min_beats))

    notes: List[str] = []

    cleaned, flags = clean_rr(rr, method=clean_method, min_rr=min_rr,
                              max_rr=max_rr, rel_thresh=rel_thresh)
    n_art_total = sum(1 for f in flags if f)
    pct_art_total = 100.0 * n_art_total / len(flags) if flags else 0.0

    # ---- 시간축은 **원시 RR** 로 만듭니다 (정제된 값이 아니라). --------------
    # 정제된 값으로 누적시각을 만들면 기록 시간이 사라집니다: 센서 끊김으로 생긴
    # 30 s 간격은 max_rr 위반이라 이상박동이고, interpolate 는 그것을 ~800 ms 로
    # 바꿉니다. 그러면 "기록 길이"가 32 % 줄고, 창 라벨 `5:00` 이 실제로는 벽시계
    # 10:00 을 가리키게 됩니다 — 개입 로그와 구간을 맞출 수 없게 되는 조용한 오답.
    # remove 도 마찬가지로 제거된 박동의 시간이 통째로 사라집니다.
    # 따라서 **경과시간은 원시 값, 지표는 정제 값**으로 분리합니다.
    raw_starts_all = _start_times_sec(
        [v if (math.isfinite(v) and v > 0.0) else 0.0 for v in rr])
    duration = sum(v for v in rr if math.isfinite(v) and v > 0.0) / 1000.0

    # (원시 시작시각, 정제값, 이상플래그) — 지표 계산에 쓸 박동만 남깁니다.
    if clean_method == "remove":
        triples = [(raw_starts_all[i], rr[i], flags[i])
                   for i in range(n_input) if not flags[i]]
    else:
        triples = [(raw_starts_all[i], cleaned[i], flags[i])
                   for i in range(n_input)]

    # 정제 후에도 남을 수 있는 비생리적 값 제거 (analyze_rr 과 같은 최종 방어).
    n_before = len(triples)
    triples = [(t, v, f) for t, v, f in triples
               if math.isfinite(v) and v > 0.0]
    if len(triples) < n_before:
        notes.append(f"{n_before - len(triples)}개 박동이 비생리적 값(0/음수/NaN)으로 "
                     "구간 분할 전에 제외되었습니다.")
    if len(triples) < 2:
        raise ValueError("정제 후 유효한 박동이 2개 미만입니다.")

    starts = [t for t, _, _ in triples]
    values = [v for _, v, _ in triples]
    wflags = [f for _, _, f in triples]
    # 이상박동의 원시 시작시각 — 창별 이상박동 비율을 원시 시간축에서 셉니다.
    # (remove 경로에서 제거된 박동도 그 시간대에 '있었던' 사실은 남아야 합니다.
    #  과거엔 남은 박동에 전부 False 를 달아 창별 art% 가 항상 0 이었습니다.)
    art_starts = [raw_starts_all[i] for i in range(n_input) if flags[i]]
    all_starts = raw_starts_all

    if duration < window_sec:
        raise ValueError(
            f"기록 길이가 {duration:.1f} s 로 창 길이 {window_sec:.0f} s 보다 "
            f"짧습니다 — --window 를 줄이거나 구간 분석 없이 실행하세요.")

    # 창 수에 상한을 둡니다. window_trends 의 Theil–Sen 기울기는 창 수의 **제곱**
    # 시간·메모리라, `--step 0.1` 같은 오타 하나로 8시간 야간 기록이 수만 개 창 →
    # 수십 GB 할당으로 되돌아올 수 없는 멈춤이 됩니다. 오류로 즉시 알립니다.
    n_win_est = int((duration - window_sec) / step) + 1
    if n_win_est > MAX_WINDOWS:
        raise ValueError(
            f"--step {step:g} s 로는 창이 {n_win_est}개 생깁니다(상한 "
            f"{MAX_WINDOWS}개). 추세 검정 비용이 창 수의 제곱으로 커지므로 "
            f"거부합니다 — --step 을 "
            f"{math.ceil((duration - window_sec) / MAX_WINDOWS)} s 이상으로 "
            f"올리세요.")

    series = WindowSeries(
        source=source, window_sec=float(window_sec), step_sec=step,
        n_input=n_input, n_artifacts=n_art_total, pct_artifacts=pct_art_total,
        clean_method=clean_method, duration_sec=duration, notes=notes,
    )

    # 창이 많으면 SampEn(창당 O(n²))이 전체 실행 시간을 지배합니다 — 자동 생략.
    if do_sampen and n_win_est > SAMPEN_MAX_WINDOWS:
        do_sampen = False
        notes.append(f"창이 {n_win_est}개로 많아 SampEn 을 자동 생략했습니다 "
                     f"(창당 O(n²) 비용). 필요하면 --window/--step 을 키우세요.")

    # 완전한 창만 생성 (k·step + window ≤ duration).
    k = 0
    n_short = 0
    while k * step + window_sec <= duration + 1e-9:
        w_start = k * step
        w_end = w_start + window_sec
        # starts 는 (원시 시각이 순증가하므로) 정렬돼 있어 이분 탐색으로 O(log n).
        # 선형 스캔이면 창 수 × 박동 수가 되어 야간 기록(수만 박동 × 수백 창)에서
        # 체감될 만큼 느려집니다.
        lo = bisect.bisect_left(starts, w_start)
        hi = bisect.bisect_left(starts, w_end)
        sub = values[lo:hi]
        subf = wflags[lo:hi]
        # 창의 이상박동 비율은 **원시 시간축의 전체 박동** 대비로 셉니다
        # (remove 로 사라진 박동도 분모·분자에 들어가야 정직합니다).
        n_a = (bisect.bisect_left(art_starts, w_end) -
               bisect.bisect_left(art_starts, w_start))
        n_all = (bisect.bisect_left(all_starts, w_end) -
                 bisect.bisect_left(all_starts, w_start))
        pct_a = 100.0 * n_a / n_all if n_all else 0.0
        win = Window(index=k, start_sec=w_start, end_sec=w_end,
                     n_beats=len(sub), n_artifacts=n_a, pct_artifacts=pct_a)
        if len(sub) < min_beats:
            win.error = (f"박동 {len(sub)}개 < 최소 {min_beats}개 — 건너뜀")
            n_short += 1
        else:
            try:
                win.result = analyze_rr(
                    sub, source=source, unit="ms", clean_method=clean_method,
                    fs=fs, min_rr=min_rr, max_rr=max_rr,
                    rel_thresh=rel_thresh, nperseg=nperseg,
                    do_sampen=do_sampen, precleaned_flags=subf)
            except ValueError as exc:
                win.error = str(exc)
                n_short += 1
        series.windows.append(win)
        k += 1

    tail = duration - ((k - 1) * step + window_sec) if k > 0 else duration
    if tail > 1e-6:
        notes.append(f"마지막 {tail:.1f} s 는 완전한 창을 이루지 못해 제외했습니다 "
                     "(구간 길이를 같게 유지하기 위함).")
    if n_short:
        notes.append(f"{n_short}개 창이 박동 부족으로 분석되지 않았습니다 "
                     "(--min-window-beats 로 기준 조정 가능).")
    if series.overlapping:
        notes.append(
            "창이 겹칩니다(step < window) — 구간들이 서로 독립이 아니므로 추세 "
            "p값은 낙관적(과소)입니다. SDANN 은 정의상 겹치지 않는 구간에서만 "
            "계산하므로 생략합니다.")
    if not series.ok_windows:
        raise ValueError(
            f"분석 가능한 창이 없습니다 (창 {len(series.windows)}개 모두 박동 부족). "
            f"--window 를 늘리거나 --min-window-beats 를 낮추세요.")
    return series


def long_term_indices(series: WindowSeries) -> Dict[str, float]:
    """Task Force 장기 지표의 **정의를 그대로 적용한** SDANN / SDNN index.

    SDANN      = 구간별 평균 NN 들의 표준편차(ms). **겹치지 않는** 구간에서만
                 정의됩니다(겹치면 같은 박동을 여러 번 세어 과소추정).
    SDNN index = 구간별 SDNN 들의 평균(ms).

    ※ 중요 — Task Force(1996)는 이 둘을 **24시간 홀터 기록**의 장기 지표로
    정의했고 참고값(SDANN ≈ 127±35 ms, SDNN index ≈ 54±15 ms)도 24시간 기준입니다.
    여기서는 **입력이 무엇이든 같은 공식을 적용**하므로, 20분 기록에서 나온
    SDANN 0.5 ms 는 공식상 맞지만 어떤 발표 값과도 비교할 수 없습니다.
    short_record=True 로 그 사실을 표시합니다(기록 < 6시간).

    구간이 2개 미만이면 SDANN 은 NaN(표준편차 정의 불가).
    표준은 5분 구간이므로 window_sec 이 300 이 아니면 nonstandard_window=True.
    """
    oks = series.ok_windows
    means = [w.result.time["mean_nn"] for w in oks
             if math.isfinite(w.result.time.get("mean_nn", float("nan")))]
    sdnns = [w.result.time["sdnn"] for w in oks
             if math.isfinite(w.result.time.get("sdnn", float("nan")))]
    sdann = float("nan")
    if not series.overlapping and len(means) >= 2:
        sdann = statistics.stdev(means)
    return {
        "sdann": sdann,
        "sdnn_index": statistics.fmean(sdnns) if sdnns else float("nan"),
        "n_windows": len(oks),
        "window_sec": series.window_sec,
        "nonstandard_window": abs(series.window_sec - 300.0) > 1e-9,
        "overlapping": series.overlapping,
        # Task Force 참고값은 24시간 기록 기준. 6시간 미만이면 비교 불가.
        "short_record": series.duration_sec < 6 * 3600.0,
        "duration_sec": series.duration_sec,
    }


def _finite(x) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return xf == xf and not math.isinf(xf)


def window_trends(series: WindowSeries) -> Dict[str, dict]:
    """구간 사이의 단조 추세를 지표마다 Mann–Kendall 로 검정.

    각 지표에 대해 mean/sd/cv/min/max 와 Mann–Kendall(S·tau·p·Theil–Sen 기울기)
    를 냅니다. 기울기 단위는 **지표단위/구간**(예: RMSSD ms/구간)입니다.

    TREND_METRICS 전체를 하나의 검정 가족으로 보아 Holm(FWER)·BH(FDR) 보정 p를
    덧붙입니다. paired_group 과 같은 주의가 적용됩니다 — RMSSD 와 SD1 은
    대수적으로 거의 같은 지표라 가족에 중복이 있어 보정은 보수적입니다.
    """
    keys = [k for k, _, _ in TREND_METRICS]
    oks = series.ok_windows
    flats = [flat_metrics(w.result) for w in oks]
    # Theil–Sen 기울기의 분모는 **실제 창 번호 차이**여야 합니다. 지표가 NaN인
    # 창(짧은 창의 SampEn 등)이나 박동 부족으로 빠진 창이 있으면, 압축된 리스트의
    # 이웃은 원래 이웃이 아니라서 기울기가 빠진 만큼 부풀려집니다 — 실측:
    # `--window 20` 에서 HF n.u. 기울기가 0.697/창으로 나왔지만 참값은 0.284/창
    # (60창 중 25창만 유한 → 2.45배 과대). 순서만 쓰는 S/tau/p 는 영향 없습니다.
    win_idx = [w.index for w in oks]
    out: Dict[str, dict] = {}
    for key in keys:
        raw = [fm.get(key) for fm in flats]
        vals = [v for v in raw if _finite(v)]
        n = len(vals)
        rec: Dict[str, float] = {"n": n, "n_windows": len(oks)}
        if n:
            rec["mean"] = statistics.fmean(vals)
            rec["sd"] = statistics.stdev(vals) if n >= 2 else 0.0
            rec["min"] = min(vals)
            rec["max"] = max(vals)
            rec["cv"] = (rec["sd"] / abs(rec["mean"])
                         if rec["mean"] != 0 else float("nan"))
        mk = mann_kendall(raw, positions=win_idx)
        rec.update({
            "s": mk["s"], "tau": mk["tau"], "z": mk["z"],
            "trend_p": mk["p_value"], "trend_method": mk["method"],
            "slope_per_window": mk["slope"],
        })
        out[key] = rec
    pvals = [out[k].get("trend_p", float("nan")) for k in keys]
    for key, ph, pb in zip(keys, holm_adjust(pvals), benjamini_hochberg(pvals)):
        out[key]["p_holm"] = ph
        out[key]["p_bh"] = pb
    out["_meta"] = {
        "n_windows": len(series.ok_windows),
        "n_tests": sum(1 for p in pvals if _finite(p)),
        "overlapping": series.overlapping,
    }
    return out
