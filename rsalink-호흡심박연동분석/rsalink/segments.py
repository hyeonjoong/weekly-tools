"""rsalink.segments — 조건 구간 정의 · 구간별 표 · Δ · 두 갈래 판정.

구간: `--baseline M:SS-M:SS --stimulus M:SS-M:SS` 또는 `--segments CSV(start,end,label)`.
둘 다 없으면 전체 기록 한 구간("전체").

판정(문구 고정, 인과 문구 없음):
 1. "HF 변화가 호흡수 변화와 동반됨 / 비동반"
    - HF고정 변화 = |ln(자극/기준)| > 잡음 마진 M (라운드 2 #2, `noise_margin_ln`).
      M = max(ln 1.25, 1.96·√(2/L_eff)), L_eff = min(L_기준, L_자극)·B_eff, B_eff = 0.5·(HF 대역 안 Welch 빈 수).
      바닥 ln 1.25 = 0.223 은 라운드 1 의 ±20% 규칙(비율 [0.80, 1.25] 안이면 변화 없음)과 같다;
      L 이 작으면(5분 구간 L=8, 빈 16 → L_eff 64 → M 0.346, 비율 0.71–1.41) 마진이 넓어진다.
      정확히 경계값은 '변화 없음'.
    - 호흡수 변화 = |Δ| ≥ max(RATE_CHANGE_MIN_BPM, RATE_CHANGE_FRAC×기준) (기본 1회/분·10%)
    - HF 가 변했고 호흡수도 변했으면 "동반됨", HF 는 변했는데 호흡수는 안 변했으면 "비동반".
      HF 자체가 마진 안이면 "판정 보류(HF 변화 |ln비| ≤ 잡음 마진 ±M%)".
    - 판정 보류(추정량 분산·자료 부족, 라운드 1 A3): HF 고정 < HF_FLOOR_MS2(30 ms²) 는 30 으로 클램프해
      비율을 보고 그래도 마진 안이면 보류(밖이면 판정 + 주석), Welch 세그먼트 수 L < MIN_WELCH_SEGMENTS(6),
      구간의 RSA 판정불가 우세(호흡당 박동 평균 < 3 또는 판정불가 > 판정; 라운드 2 #1), 스펙트럼 없음,
      구간 데이터 부족(잘린 뒤 < 2분).
    - 잡음 마진(바닥 ±20%·1.96·√(2/L_eff)) · 30 ms² · L ≥ 6 은 문헌 근거 없는 이 툴의 관례다.
    - 호흡중심 대역의 방향(2행)도 같은 M 으로 판정한다 — 두 대역의 방향을 같은 잣대로 비교하기 위해.
 2. "호흡중심 대역 기준 결론 동일 / 상이"
    - HF고정의 방향(↑/↓/→)과 호흡중심 대역의 방향이 같으면 "동일", 다르면 "상이".
표에 항상 붙는 주석: RSA 는 호흡수와 깊이 모두에 의존하며 깊이는 미측정
(Grossman & Taylor 2007; Hirsch & Bishop 1981). 평균 호흡수 ≤ 9/분(0.15 Hz) 인 구간은
"고정 HF 는 이 구간의 RSA 를 과소평가" 플래그.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import RsalinkError
from .breath import BreathResult, rate_summary
from .pace import PaceResult, pace_analysis
from .parse import RRSeries, parse_mmss, parse_range, read_rows
from .rsa import BreathRSA, summarize
from .spectral import BandResult, analyze_segment, band_bin_count, HF_BAND, MIN_NPERSEG, FS

POWER_CHANGE_FRAC = 0.20
POWER_RATIO_LO = 1.0 - POWER_CHANGE_FRAC            # 0.80 — 단일 상수 소스
POWER_RATIO_HI = 1.0 / (1.0 - POWER_CHANGE_FRAC)    # 1.25
LN_RATIO_FLOOR = math.log(POWER_RATIO_HI)           # 0.223 — 잡음 마진의 바닥(= 라운드 1 의 ±20%)
MARGIN_Z = 1.96                                     # 로그비 잡음 마진의 z (양측 95% 근사)
WELCH_BIN_EFF = 0.5                                 # Hann 50% 겹침 → 유효 독립 빈 ≈ 절반(근사, noise_margin_ln 참조)
RATE_CHANGE_MIN_BPM = 1.0
RATE_CHANGE_FRAC = 0.10
SHORT_SEGMENT_S = 300.0
SLOW_BREATH_BPM = 9.0           # 0.15 Hz — 고정 HF 하한
MIN_SPECTRAL_S = MIN_NPERSEG / FS   # 16 s
HF_FLOOR_MS2 = 30.0             # 이 아래면 ±20% 판정 보류(절대 바닥) — 이 툴의 관례
MIN_WELCH_SEGMENTS = 6          # L < 6 이면 판정 보류 — 이 툴의 관례
RSA_MIN_BEATS = 3.0             # 구간 호흡당 박동 평균 < 3 → "RSA 판정불가 우세" 보류(라운드 2 #1)
RATE_CV_BIMODAL = 0.15          # 구간 호흡수 CV > 15% → 호흡중심 대역 "평균 중심 무의미 가능" 플래그
MIN_VERDICT_S = 120.0           # 잘린 뒤 2분 미만이면 스펙트럼·판정 생략
CLIP_TOLERANCE_S = 1.0          # 요청 구간과 데이터 범위 차이가 이보다 작으면 "잘림"으로 표기하지 않음


@dataclass
class Segment:
    label: str
    t0: float
    t1: float
    t0_req: Optional[float] = None      # 요청 구간(잘렸을 때만 채움, 라운드 1 A5)
    t1_req: Optional[float] = None

    @property
    def duration_s(self) -> float:
        return self.t1 - self.t0

    @property
    def clipped(self) -> bool:
        return self.t0_req is not None

    @property
    def clipped_frac(self) -> float:
        if not self.clipped:
            return 0.0
        req = self.t1_req - self.t0_req
        return 1.0 - self.duration_s / req if req > 0 else 1.0


def clip_segments(segs: Sequence[Segment], lo: float, hi: float) -> List[Segment]:
    """구간을 두 신호의 공통 범위 [lo, hi] 로 잘라 새 Segment 목록을 만든다(라운드 1 A5).
    완전히 밖이면 길이 0 구간(스펙트럼·판정 생략, 표에는 남긴다)."""
    out: List[Segment] = []
    for s in segs:
        t0, t1 = max(s.t0, lo), min(s.t1, hi)
        if t1 < t0:
            t0 = t1 = min(max(s.t0, lo), hi)
        if (t0 - s.t0) + (s.t1 - t1) < CLIP_TOLERANCE_S:
            out.append(Segment(s.label, s.t0, s.t1))    # 1 s 미만 차이는 잘림으로 치지 않는다(격자 상한이 처리)
        else:
            out.append(Segment(s.label, t0, t1, s.t0, s.t1))
    return out


@dataclass
class SegmentStats:
    seg: Segment
    n_breaths: int
    n_valid: int
    rate: dict
    rsa: dict
    band: Optional[BandResult]
    pace: Optional[PaceResult]
    mean_rr: float
    short_flag: bool
    slow_flag: bool
    spectral_note: str = ""
    rate_cv_flag: bool = False      # 구간 호흡수 CV > 15% → 호흡중심 대역 "평균 중심 무의미 가능"(D3)

    @property
    def artifact_frac(self) -> float:
        return 1.0 - self.n_valid / self.n_breaths if self.n_breaths else float("nan")


@dataclass
class Delta:
    metric: str
    base: float
    stim: float

    @property
    def delta(self) -> float:
        return self.stim - self.base

    @property
    def ratio(self) -> float:
        return self.stim / self.base if self.base and self.base == self.base else float("nan")


@dataclass
class Comparison:
    base: SegmentStats
    stim: SegmentStats
    deltas: List[Delta]
    hf_direction: str          # "↑" | "↓" | "→"
    resp_direction: str
    rate_changed: bool
    hf_changed: bool
    verdict_hf: str            # 동반됨 / 비동반 / 판정 보류(사유)
    verdict_band: str          # 동일 / 상이 / 판정 보류(사유)
    lines: List[str] = field(default_factory=list)
    hold_reasons: List[str] = field(default_factory=list)
    margin_ln: float = LN_RATIO_FLOOR          # 잡음 마진 M(로그비, 라운드 2 #2)
    hf_ln_ratio: float = float("nan")          # ln(자극/기준), 30 ms² 클램프 뒤
    hf_n_bins: int = 0                         # HF 대역 Welch 빈 수(B)
    L_eff: float = float("nan")                # min(L)·B_eff

    @property
    def margin_pct(self) -> float:
        return margin_pct(self.margin_ln)


# ---------------------------------------------------------------- 구간 정의


def _is_mmss(s: str) -> bool:
    try:
        parse_mmss(s)
        return True
    except RsalinkError:
        return False


def parse_segments_csv(path: str) -> List[Segment]:
    """start,end,label 헤더. 헤더 없이 3열이고 첫 두 셀이 시각이면 그 행도 데이터(라운드 1 A6).
    라벨이 비면 "구간N". 라벨 중복은 거부(D13)."""
    header, rows = read_rows(path)
    low = [h.lower() for h in header]
    name = os.path.basename(path)
    if "start" in low and "end" in low:
        i_s, i_e = low.index("start"), low.index("end")
        i_l = low.index("label") if "label" in low else 2
    elif all(not h for h in header):
        i_s, i_e, i_l = 0, 1, 2                 # read_rows 가 전부 값 모양이라 헤더 없음으로 판정
    elif len(header) == 3 and _is_mmss(header[0]) and _is_mmss(header[1]):
        i_s, i_e, i_l = 0, 1, 2
        rows = [header] + rows                  # 첫 행이 데이터
    else:
        raise RsalinkError(f"구간 CSV 헤더 start,end,label 필요 (첫 행: {header}): {name}")
    segs: List[Segment] = []
    seen = set()
    for r in rows:
        if len(r) <= max(i_s, i_e):
            raise RsalinkError(f"구간 CSV 행에 start/end 가 없습니다: {r} ({name})")
        t0, t1 = parse_mmss(r[i_s]), parse_mmss(r[i_e])
        if t1 <= t0:
            raise RsalinkError(f"구간 끝이 시작보다 앞입니다: {r}")
        label = r[i_l] if len(r) > i_l and r[i_l] else f"구간{len(segs) + 1}"
        if label in seen:
            raise RsalinkError(f"구간 라벨 중복: {label!r} ({name}) — 라벨은 조건비교.csv 의 열 값이라 유일해야 합니다")
        seen.add(label)
        segs.append(Segment(label, t0, t1))
    if not segs:
        raise RsalinkError(f"구간 CSV 에 구간이 없습니다: {name}")
    return segs


def build_segments(baseline: Optional[str], stimulus: Optional[str], segments_csv: Optional[str],
                   total_s: float, start_s: float = 0.0) -> List[Segment]:
    """구간이 하나도 없으면 두 신호의 공통 범위 [start_s, total_s] 를 "전체" 한 구간으로."""
    if segments_csv:
        if baseline or stimulus:
            raise RsalinkError("--segments 와 --baseline/--stimulus 는 함께 쓸 수 없습니다")
        segs = parse_segments_csv(segments_csv)
        check_no_overlap(segs)
        return segs
    segs = []
    if baseline:
        segs.append(Segment("기준선", *parse_range(baseline)))
    if stimulus:
        segs.append(Segment("자극", *parse_range(stimulus)))
    if not segs:
        segs.append(Segment("전체", start_s, total_s))
    check_no_overlap(segs)
    return segs


def check_no_overlap(segs: Sequence[Segment]) -> None:
    """구간이 겹치면 exit 2 "구간 겹침"(라운드 2 #9). 끝과 시작이 같은 것(0:00-5:00 / 5:00-15:00)은 겹침이 아니다."""
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if a.t0 < b.t1 and b.t0 < a.t1:
                raise RsalinkError(
                    f"구간 겹침: {a.label} {_fmt_mmss(a.t0)}-{_fmt_mmss(a.t1)} 과 {b.label} {_fmt_mmss(b.t0)}-{_fmt_mmss(b.t1)} "
                    "이 겹칩니다 — 조건 구간은 서로 겹치지 않아야 합니다(끝과 시작이 같은 것은 허용)")


def _fmt_mmss(t: float) -> str:
    m = int(t // 60)
    return f"{m}:{t - 60 * m:02.0f}"


# ---------------------------------------------------------------- 구간 분석


def analyze_segments(segs: Sequence[Segment], br: BreathResult, rsa_rows: Sequence[BreathRSA],
                     rr: RRSeries, resp_t: Optional[Sequence[float]], resp_v: Optional[Sequence[float]],
                     resp_offset: float = 0.0, pace_bpm: Optional[float] = None,
                     half_width: float = 0.04, nperseg: Optional[int] = None) -> List[SegmentStats]:
    out: List[SegmentStats] = []
    for s in segs:
        # 호흡 시각은 호흡 시계 기준, RR 시각 = 호흡 시각 + offset. 구간은 RR(공통) 시계 기준.
        b0, b1 = s.t0 - resp_offset, s.t1 - resp_offset
        breaths = [b for b in br.breaths if b0 <= b.t_onset < b1]
        valid = [b for b in breaths if b.valid]
        rate = rate_summary([b.rate_bpm for b in valid])
        rsa = summarize(rsa_rows, b0, b1)
        band = None
        note = ""
        f_resp = rate["mean"] / 60.0 if rate["n"] else float("nan")
        # RR 커버리지: 구간 안에 박동이 있어야 스펙트럼
        rr_in = [t for t in rr.t_beat if s.t0 <= t < s.t1]
        if s.clipped and s.duration_s < MIN_VERDICT_S:
            note = (f"구간 데이터 부족(요청 대비 {100 * s.clipped_frac:.0f}% 잘려 {s.duration_s / 60:.1f}분 "
                    f"< {MIN_VERDICT_S / 60:.0f}분) — 스펙트럼·판정 생략")
        elif s.duration_s < MIN_SPECTRAL_S or len(rr_in) < 8:
            note = "구간이 너무 짧거나 RR 박동이 부족해 스펙트럼 생략"
        elif not (f_resp == f_resp):
            note = "유효 호흡이 없어 호흡중심 대역을 정할 수 없음 — 스펙트럼 생략"
        else:
            try:
                band = analyze_segment(rr.t_beat, rr.rr_ms, s.t0, s.t1, f_resp, resp_t, resp_v,
                                       resp_offset, half_width, nperseg)
            except ValueError as e:
                note = f"스펙트럼 생략({e})"
        pace = pace_analysis(br.breaths, pace_bpm, t0=b0, t1=b1) if pace_bpm else None
        rr_vals = [x for t, x in zip(rr.t_beat, rr.rr_ms) if s.t0 <= t < s.t1]
        mean_rr = sum(rr_vals) / len(rr_vals) if rr_vals else float("nan")
        out.append(SegmentStats(
            seg=s, n_breaths=len(breaths), n_valid=len(valid), rate=rate, rsa=rsa, band=band, pace=pace,
            mean_rr=mean_rr, short_flag=s.duration_s < SHORT_SEGMENT_S,
            slow_flag=(rate["n"] > 0 and rate["mean"] <= SLOW_BREATH_BPM), spectral_note=note,
            rate_cv_flag=(rate["n"] > 1 and rate["cv"] == rate["cv"] and rate["cv"] > RATE_CV_BIMODAL)))
    return out


# ---------------------------------------------------------------- 비교·판정


def noise_margin_ln(L_base: int, L_stim: int, n_bins: int) -> float:
    """라운드 2 #2 — HF 대역 파워 비율의 잡음 마진 M(로그비 공간).

        M = max(ln 1.25, 1.96·√(2 / L_eff)),  L_eff = min(L_base, L_stim) · B_eff,  B_eff = 0.5 · n_bins

    유도(근사): 정상 가우스 과정의 주기도 빈 하나는 P·χ²₂/2 를 따르므로 세그먼트 L 개를 평균한 Welch
    빈은 상대분산 1/L 이다. 대역 파워는 B 개 빈의 합인데, Hann 창의 주엽이 이웃 빈에 걸치고 50% 겹침
    세그먼트끼리도 상관되어 독립 성분 수는 대략 절반 — B_eff = 0.5·B 로 놓는다(이 툴의 관례·근사).
    빈 파워가 대체로 고르면 var(ln P̂_대역) ≈ 1/(L·B_eff) 이고, 두 구간의 로그비 분산은
    1/(L_base·B_eff) + 1/(L_stim·B_eff) ≤ 2/(min(L)·B_eff) = 2/L_eff. 그 √ 의 1.96 배가 양측 95% 근사 마진.
    실측(합성 백색잡음 RR, 5분/5분, L=8/8, B=16): 로그비 RMS 0.176 vs 이론 √(2/64) = 0.177.
    바닥 ln 1.25 는 라운드 1 의 ±20% 규칙과 같다(L 이 크면 그 규칙으로 돌아간다).
    L=8·B=16 → M = 1.96·√(2/64) = 0.3465(비율 0.71–1.41); L=30·B=16 → 0.179 < 바닥 → 0.223.
    """
    L_eff = min(L_base, L_stim) * WELCH_BIN_EFF * n_bins
    if L_eff <= 0:
        return LN_RATIO_FLOOR
    return max(LN_RATIO_FLOOR, MARGIN_Z * math.sqrt(2.0 / L_eff))


def margin_pct(margin_ln: float) -> float:
    """마진을 %로: 100·(1 − e^{−M}) — 바닥 ln 1.25 가 '±20%'(비율 0.80–1.25) 로 읽히도록 라운드 1 표기와 맞춘다."""
    return 100.0 * (1.0 - math.exp(-margin_ln))


def ratio_bounds(margin_ln: float) -> Tuple[float, float]:
    """(e^{−M}, e^{M}) — 이 밖이면 '변화'."""
    return math.exp(-margin_ln), math.exp(margin_ln)


def _direction(base: float, stim: float, margin_ln: float = LN_RATIO_FLOOR) -> str:
    """ln(stim/base) > M → ↑, < −M → ↓, 그 안(경계 포함) → →. 기본 M = ln 1.25 (비율 [0.80, 1.25] 안이면 변화 없음)."""
    if not (base == base and stim == stim) or base <= 0:
        return "?"
    if stim <= 0:
        return "↓"
    lr = math.log(stim / base)
    if lr > margin_ln + 1e-9:
        return "↑"
    if lr < -margin_ln - 1e-9:
        return "↓"
    return "→"


def hold_reasons(base: SegmentStats, stim: SegmentStats,
                 margin_ln: float = LN_RATIO_FLOOR) -> Tuple[List[str], List[str]]:
    """라운드 1 A3 — (판정을 보류할 사유, 보류는 아니지만 붙일 주석).

    HF 30 ms² 바닥은 **클램프**로 적용한다: 30 미만은 30 으로 놓고 비율을 본다. 클램프 뒤에도
    |ln 비| 가 잡음 마진 밖이면 판정하고 주석만 붙인다(예: 기준 276 → 자극 11 은 30/276 = 0.11 로 ↓ 유지).
    클램프 뒤 마진 안이면 보류(바닥 근처의 작은 차이는 세지 않는다).
    """
    reasons: List[str] = []
    notes: List[str] = []
    for tag, s in (("기준선", base), ("자극", stim)):
        if s.band is None:
            reasons.append(f"{tag} 스펙트럼 없음" + (f": {s.spectral_note}" if s.spectral_note else ""))
            continue
        if s.band.n_segments < MIN_WELCH_SEGMENTS:
            reasons.append(f"{tag} Welch L={s.band.n_segments} < {MIN_WELCH_SEGMENTS}")
        # 라운드 2 #1: "전부" 판정불가(n == 0)만 잡던 것을 "우세"로 — 호흡당 박동 평균 < 3 이거나
        # 판정불가 수 > 판정 수 면 보류 (HR 40·호흡 20/분 은 박동 1.98/호흡, 판정 3 vs 불가 198 이 '동반됨'이었다)
        bpb, n_det, n_und = s.rsa["beats_per_breath"], s.rsa["n"], s.rsa["n_undetermined"]
        if (bpb == bpb and bpb < RSA_MIN_BEATS) or n_und > n_det:
            reasons.append(f"{tag} RSA 판정불가 우세(호흡당 박동 {bpb:.1f}, 판정 {n_det}/판정불가 {n_und})")
    if base.band is not None and stim.band is not None:
        hb, hs = base.band.hf_fixed, stim.band.hf_fixed
        if hb < HF_FLOOR_MS2 or hs < HF_FLOOR_MS2:
            cb, cs = max(hb, HF_FLOOR_MS2), max(hs, HF_FLOOR_MS2)
            txt = f"HF 고정 {hb:.1f}/{hs:.1f} ms² 중 {HF_FLOOR_MS2:.0f} ms² 미만은 {HF_FLOOR_MS2:.0f} 으로 처리"
            if _direction(cb, cs, margin_ln) == "→":
                reasons.append(txt + f" → |ln비| ≤ 잡음 마진 ±{margin_pct(margin_ln):.0f}%")
            else:
                notes.append(txt + f" (비율 {cs / cb:.2f})")
    return reasons, notes


def _hf_bins(b: Optional[BandResult]) -> int:
    if b is None:
        return 0
    return b.hf_n_bins if b.hf_n_bins else band_bin_count(b.df, *HF_BAND)


def pick_baseline(stats: Sequence[SegmentStats]) -> int:
    for i, s in enumerate(stats):
        if "기준" in s.seg.label or "baseline" in s.seg.label.lower() or "base" in s.seg.label.lower():
            return i
    return 0


def compare(base: SegmentStats, stim: SegmentStats) -> Comparison:
    def bp(s: SegmentStats, attr: str) -> float:
        return getattr(s.band, attr) if s.band is not None else float("nan")

    def coh(s: SegmentStats) -> float:
        c = bp(s, "coherence_at_resp")
        return c if c is not None else float("nan")

    deltas = [
        Delta("호흡수(회/분)", base.rate["mean"], stim.rate["mean"]),
        Delta("RSA_pv 중앙값(ms)", base.rsa["median"], stim.rsa["median"]),
        Delta("LF(ms²)", bp(base, "lf"), bp(stim, "lf")),
        Delta("HF 고정(ms²)", bp(base, "hf_fixed"), bp(stim, "hf_fixed")),
        Delta("호흡중심(ms²)", bp(base, "resp_centered"), bp(stim, "resp_centered")),
        Delta("결맞음", coh(base), coh(stim)),
        Delta("평균 RR(ms)", base.mean_rr, stim.mean_rr),
    ]
    hb, hs = bp(base, "hf_fixed"), bp(stim, "hf_fixed")
    # 라운드 2 #2: 잡음 마진 M — 두 구간 모두 스펙트럼이 있을 때 L·HF 빈 수로, 아니면 바닥(ln 1.25)
    n_bins = min(_hf_bins(base.band), _hf_bins(stim.band)) if (base.band and stim.band) else 0
    L_eff = float("nan")
    margin = LN_RATIO_FLOOR
    if base.band is not None and stim.band is not None and n_bins > 0:
        L_eff = min(base.band.n_segments, stim.band.n_segments) * WELCH_BIN_EFF * n_bins
        margin = noise_margin_ln(base.band.n_segments, stim.band.n_segments, n_bins)
    cb = max(hb, HF_FLOOR_MS2) if hb == hb else hb
    cs = max(hs, HF_FLOOR_MS2) if hs == hs else hs
    hf_ln = math.log(cs / cb) if (cb == cb and cs == cs and cb > 0 and cs > 0) else float("nan")
    hf_dir = _direction(cb, cs, margin)
    rc_dir = _direction(bp(base, "resp_centered"), bp(stim, "resp_centered"), margin)
    rb, rs = base.rate["mean"], stim.rate["mean"]
    rate_changed = (rb == rb and rs == rs
                    and abs(rs - rb) >= max(RATE_CHANGE_MIN_BPM, RATE_CHANGE_FRAC * rb))
    hf_changed = hf_dir in ("↑", "↓")
    holds, notes = hold_reasons(base, stim, margin)
    L_txt = (f"L={base.band.n_segments if base.band else '—'}/"
             f"{stim.band.n_segments if stim.band else '—'}")
    pct = margin_pct(margin)
    if holds:
        v_hf = "판정 보류(" + "; ".join(holds) + ")"
    elif not hf_changed:
        v_hf = f"판정 보류(HF 변화 |ln비| ≤ 잡음 마진 ±{pct:.0f}%)"
    elif rate_changed:
        v_hf = "동반됨"
    else:
        v_hf = "비동반"
    if hf_dir == "?" or rc_dir == "?":
        v_band = "판정 보류(스펙트럼 없음)"
    elif holds:
        v_band = "판정 보류(" + "; ".join(holds) + ")"
    else:
        v_band = "동일" if hf_dir == rc_dir else "상이"

    def rate_txt(x: float) -> str:
        return f"{x:.1f}" if x == x else "호흡 없음"

    rate_word = "변화" if rate_changed else ("임계 미만" if (rb == rb and rs == rs) else "판정 불가")
    lines = [
        f"HF 변화가 호흡수 변화와 **{v_hf}** "
        f"(HF 고정 {hf_dir}, {L_txt}, 호흡수 {rate_txt(rb)}→{rate_txt(rs)}/분 {rate_word})",
        f"호흡중심 대역 기준 결론 **{v_band}** (HF 고정 {hf_dir} vs 호흡중심 {rc_dir}, {L_txt})",
    ]
    if L_eff == L_eff:
        lo_r, hi_r = ratio_bounds(margin)
        lines.append(f"잡음 마진 ±{pct:.0f}% (비율 {lo_r:.2f} 배 미만 또는 {hi_r:.2f} 배 초과면 변화; "
                     f"HF 고정 |ln 비| = {abs(hf_ln):.2f}; L_eff = min(L)·(HF 대역 빈 {n_bins}개 × {WELCH_BIN_EFF:g}) = {L_eff:g}) "
                     "— L·대역 빈 수 기반, 이 툴의 관례")
    for n_ in notes:
        lines.append("주석: " + n_)
    if hf_changed and rate_changed and hf_dir != "?":
        # D5: Hirsch & Bishop 1981 롤오프(호흡수↑ → RSA↓) 와 방향 비교 — 관찰 방향의 기술일 뿐
        same = (rs > rb and hf_dir == "↑") or (rs < rb and hf_dir == "↓")
        if same:
            lines.append("방향 주석: 호흡수와 HF 고정이 같은 방향으로 움직임 — Hirsch & Bishop 1981 롤오프 방향"
                         "(호흡수↑·RSA↓)과 불일치. 깊이 변화·대역 이동 등 이 툴이 못 보는 요인 후보")
    return Comparison(base=base, stim=stim, deltas=deltas, hf_direction=hf_dir, resp_direction=rc_dir,
                      rate_changed=rate_changed, hf_changed=hf_changed, verdict_hf=v_hf,
                      verdict_band=v_band, lines=lines, hold_reasons=holds, margin_ln=margin,
                      hf_ln_ratio=hf_ln, hf_n_bins=n_bins, L_eff=L_eff)


def compare_all(stats: Sequence[SegmentStats]) -> List[Comparison]:
    if len(stats) < 2:
        return []
    ib = pick_baseline(stats)
    return [compare(stats[ib], s) for i, s in enumerate(stats) if i != ib]
