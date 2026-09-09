"""rsalink.segments — 조건 구간 정의 · 구간별 표 · Δ · 두 갈래 판정.

구간: `--baseline M:SS-M:SS --stimulus M:SS-M:SS` 또는 `--segments CSV(start,end,label)`.
둘 다 없으면 전체 기록 한 구간("전체").

판정(문구 고정, 인과 문구 없음):
 1. "HF 변화가 호흡수 변화와 동반됨 / 비동반"
    - HF고정 변화 = 자극/기준 비율이 [1−POWER_CHANGE_FRAC, 1/(1−POWER_CHANGE_FRAC)] 밖 (기본 ±20%)
    - 호흡수 변화 = |Δ| ≥ max(RATE_CHANGE_MIN_BPM, RATE_CHANGE_FRAC×기준) (기본 1회/분·10%)
    - HF 가 변했고 호흡수도 변했으면 "동반됨", HF 는 변했는데 호흡수는 안 변했으면 "비동반".
      HF 자체가 ±20% 안이면 "HF 변화 작음 — 판정 보류".
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
from .spectral import BandResult, analyze_segment, MIN_NPERSEG, FS

POWER_CHANGE_FRAC = 0.20
RATE_CHANGE_MIN_BPM = 1.0
RATE_CHANGE_FRAC = 0.10
SHORT_SEGMENT_S = 300.0
SLOW_BREATH_BPM = 9.0           # 0.15 Hz — 고정 HF 하한
MIN_SPECTRAL_S = MIN_NPERSEG / FS   # 16 s


@dataclass
class Segment:
    label: str
    t0: float
    t1: float

    @property
    def duration_s(self) -> float:
        return self.t1 - self.t0


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
    verdict_hf: str            # 동반됨 / 비동반 / 판정 보류
    verdict_band: str          # 동일 / 상이
    lines: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 구간 정의


def parse_segments_csv(path: str) -> List[Segment]:
    header, rows = read_rows(path)
    low = [h.lower() for h in header]
    try:
        i_s = low.index("start") if "start" in low else 0
        i_e = low.index("end") if "end" in low else 1
        i_l = low.index("label") if "label" in low else 2
    except ValueError:
        raise RsalinkError("구간 CSV 헤더는 start,end,label 이어야 합니다")
    segs = []
    for r in rows:
        if len(r) <= max(i_s, i_e):
            continue
        t0, t1 = parse_mmss(r[i_s]), parse_mmss(r[i_e])
        if t1 <= t0:
            raise RsalinkError(f"구간 끝이 시작보다 앞입니다: {r}")
        label = r[i_l] if len(r) > i_l and r[i_l] else f"구간{len(segs) + 1}"
        segs.append(Segment(label, t0, t1))
    if not segs:
        raise RsalinkError(f"구간 CSV 에 구간이 없습니다: {os.path.basename(path)}")
    return segs


def build_segments(baseline: Optional[str], stimulus: Optional[str], segments_csv: Optional[str],
                   total_s: float) -> List[Segment]:
    if segments_csv:
        if baseline or stimulus:
            raise RsalinkError("--segments 와 --baseline/--stimulus 는 함께 쓸 수 없습니다")
        return parse_segments_csv(segments_csv)
    segs = []
    if baseline:
        segs.append(Segment("기준선", *parse_range(baseline)))
    if stimulus:
        segs.append(Segment("자극", *parse_range(stimulus)))
    if not segs:
        segs.append(Segment("전체", 0.0, total_s))
    return segs


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
        if s.duration_s < MIN_SPECTRAL_S or len(rr_in) < 8:
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
            slow_flag=(rate["n"] > 0 and rate["mean"] <= SLOW_BREATH_BPM), spectral_note=note))
    return out


# ---------------------------------------------------------------- 비교·판정


def _direction(base: float, stim: float, frac: float = POWER_CHANGE_FRAC) -> str:
    if not (base == base and stim == stim) or base <= 0:
        return "?"
    r = stim / base
    if r >= 1.0 / (1.0 - frac):
        return "↑"
    if r <= 1.0 - frac:
        return "↓"
    return "→"


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
    hf_dir = _direction(bp(base, "hf_fixed"), bp(stim, "hf_fixed"))
    rc_dir = _direction(bp(base, "resp_centered"), bp(stim, "resp_centered"))
    rb, rs = base.rate["mean"], stim.rate["mean"]
    rate_changed = (rb == rb and rs == rs
                    and abs(rs - rb) >= max(RATE_CHANGE_MIN_BPM, RATE_CHANGE_FRAC * rb))
    hf_changed = hf_dir in ("↑", "↓")
    if hf_dir == "?":
        v_hf = "판정 보류(스펙트럼 없음)"
    elif not hf_changed:
        v_hf = "판정 보류(HF 변화 ±20% 안)"
    elif rate_changed:
        v_hf = "동반됨"
    else:
        v_hf = "비동반"
    if hf_dir == "?" or rc_dir == "?":
        v_band = "판정 보류(스펙트럼 없음)"
    else:
        v_band = "동일" if hf_dir == rc_dir else "상이"
    lines = [
        f"HF 변화가 호흡수 변화와 **{v_hf}** "
        f"(HF 고정 {hf_dir}, 호흡수 {rb:.1f}→{rs:.1f}/분{' 변화' if rate_changed else ' 불변'})",
        f"호흡중심 대역 기준 결론 **{v_band}** (HF 고정 {hf_dir} vs 호흡중심 {rc_dir})",
    ]
    return Comparison(base=base, stim=stim, deltas=deltas, hf_direction=hf_dir, resp_direction=rc_dir,
                      rate_changed=rate_changed, hf_changed=hf_changed, verdict_hf=v_hf,
                      verdict_band=v_band, lines=lines)


def compare_all(stats: Sequence[SegmentStats]) -> List[Comparison]:
    if len(stats) < 2:
        return []
    ib = pick_baseline(stats)
    return [compare(stats[ib], s) for i, s in enumerate(stats) if i != ib]
