"""rsalink.report — 터미널/마크다운 리포트, 산출물 쓰기(가드 포함), KR/EN 문장.

순서(고정): ① 커버리지 자백 → ② 호흡 검출 → ③ RSA → ④ 스펙트럼·결맞음 → ⑤ 페이싱
→ ⑥ 조건 비교표 + 판정 두 줄 → ⑦ Methods/Results 문장(KR/EN) → ⑧ 근거 → ⑨ 면책.
경로는 basename 만 적는다. CSV 셀은 수식 가드(=,+,-,@,탭,CR 로 시작하는 문자열에 ' 접두).
"""
from __future__ import annotations

import csv
import io
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import RsalinkError, __version__
from .breath import BreathResult, RATE_CV_FLAG
from .parse import RRSeries
from .rsa import BreathRSA
from .segments import (Comparison, SegmentStats, SHORT_SEGMENT_S, SLOW_BREATH_BPM, POWER_CHANGE_FRAC,
                       POWER_RATIO_LO, POWER_RATIO_HI, RATE_CHANGE_MIN_BPM, RATE_CHANGE_FRAC,
                       HF_FLOOR_MS2, MIN_WELCH_SEGMENTS, MARGIN_Z, WELCH_BIN_EFF, ratio_bounds)

REPORT_MD = "연동리포트.md"
BREATH_CSV = "호흡별.csv"
COMPARE_CSV = "조건비교.csv"

GATE_VALID_BREATH_FRAC = 0.60
GATE_USABLE_S = 120.0
GATE_RR_EXCLUDED_FRAC = 0.20

REFS = [
    ("Task Force 1996", "Heart rate variability: standards of measurement, physiological interpretation and clinical use. "
     "Eur Heart J 17:354 / Circulation 93:1043. PMID 8737210 / 8598068",
     "LF 0.04–0.15 / HF 0.15–0.40 Hz, 단기 기록 5분 권장"),
    ("Grossman, van Beek & Wientjes 1990", "Psychophysiology 27:702. doi:10.1111/j.1469-8986.1990.tb03198.x",
     "peak-valley·스펙트럼·복합 디트렌드 3법 상호상관 >.92(개인 내 평균 .96); peak-valley 의 호흡 상관이 복합 디트렌드법보다 높음(.91 vs .84)"),
    ("Grossman & Taylor 2007", "Biol Psychol 74:263. doi:10.1016/j.biopsycho.2005.11.014",
     "RSA 는 호흡수·깊이 등 호흡 파라미터에 의존하며 이를 고려하지 않으면 RSA–미주신경 관계가 왜곡될 수 있음; 신체활동·β-아드레날린 톤도 영향"),
    ("Hirsch & Bishop 1981", "Am J Physiol 241:H620. doi:10.1152/ajpheart.1981.241.4.H620",
     "RSA 진폭은 6회/분 미만에서 크고 안정, 코너 주파수 7.2±1.5회/분 위에서 21±3.4 dB/decade 감소, 일회호흡량과 선형"),
    ("Lehrer & Gevirtz 2014", "Front Psychol 5:756. doi:10.3389/fpsyg.2014.00756",
     "HRV 바이오피드백 기전 리뷰 — 압반사 공명 기전이 가장 지지됨(공명 ≈0.1 Hz 는 통상 인용값, 개인차 있음)"),
    ("Laborde, Mosley & Thayer 2017", "Front Psychol 8:213. doi:10.3389/fpsyg.2017.00213",
     "HRV 실험 설계·분석·보고 권고(호흡 관련 보고 포함)"),
]

DISCLAIMER = (
    "이 리포트는 두 신호가 **함께 변했는지**를 숫자로 보여줄 뿐, 한쪽이 다른 쪽을 일으켰다는 "
    "주장을 하지 않는다(비인과). 의학적 진단·치료 판단에 쓰는 도구가 아니다(비진단). "
    "RSA 는 호흡수와 호흡 깊이 모두에 의존하며 이 툴은 깊이를 측정하지 않는다(파형 진폭 비보정) "
    "— Grossman & Taylor 2007; Hirsch & Bishop 1981."
)


@dataclass
class Coverage:
    resp_source: str
    resp_file: str
    rr_file: str
    fs_est: float
    irregular_frac: float
    n_gaps: int
    gap_total_s: float
    dup_resp: int
    resp_duration_s: float
    rr_duration_s: float
    usable_s: float
    rr_unit: str
    rr_col: str
    rr_total: int
    rr_excluded: int
    rr_excluded_frac: float
    n_breaths: int
    n_valid: int
    artifact_frac: float
    flags: dict
    beats_per_breath: float
    n_undetermined: int
    resp_offset: float
    rsa_lag: float
    offset_suggest: Optional[tuple]
    peak_assumed: bool
    time_kinds: tuple
    short_segments: List[str] = field(default_factory=list)
    rate_unstable: bool = False                         # 호흡수 CV > 40%
    rate_gate_reasons: List[str] = field(default_factory=list)   # CV > 60% / 중앙값−평균 > 25%
    abs_time_diff: Optional[float] = None               # 호흡 첫 샘플 − RR 첫 타임스탬프(둘 다 절대 시각일 때)
    rr_first_s: float = 0.0
    tz_note: str = ""
    samples_per_breath: float = float("nan")
    n_bad_resp_values: int = 0
    n_spikes_clipped: int = 0
    ignored_options: List[str] = field(default_factory=list)
    rr_unparsed: int = 0
    mean_breath_period_s: float = float("nan")

    def gate_reasons(self) -> List[str]:
        r = []
        if self.n_breaths and (self.n_valid / self.n_breaths) < GATE_VALID_BREATH_FRAC:
            r.append(f"유효 호흡 {100 * self.n_valid / self.n_breaths:.0f}% < {GATE_VALID_BREATH_FRAC * 100:.0f}% "
                     "(--k-mad, --smooth-s, --min-breath-s/--max-breath-s, --invert 확인)")
        if self.n_breaths == 0:
            r.append("검출된 호흡 없음 (--invert, --k-mad, --max-breath-s 확인)")
        if self.usable_s < GATE_USABLE_S:
            r.append(f"사용 가능 구간 {self.usable_s / 60:.1f}분 < {GATE_USABLE_S / 60:.0f}분 "
                     "(--resp-offset, --inspect 로 두 기록의 겹침 확인)")
        if self.rr_excluded_frac > GATE_RR_EXCLUDED_FRAC:
            r.append(f"RR 제외 {100 * self.rr_excluded_frac:.0f}% > {GATE_RR_EXCLUDED_FRAC * 100:.0f}% "
                     "(--rr-col 로 열 확인, 단위 자동 인식 결과는 --inspect)")
        for x in self.rate_gate_reasons:
            r.append(x + " (--k-mad, --smooth-s, --max-breath-s 확인)")
        return r


def _f(x, nd=1, unit=""):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:.{nd}f}{unit}"


def _mmss(t: float) -> str:
    if t is None or t != t:
        return "—"
    m = int(t // 60)
    return f"{m}:{t - 60 * m:04.1f}"


def _md(s: str) -> str:
    """마크다운 표 셀용: `|` 와 개행을 이스케이프(라운드 1 B2)."""
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


# ---------------------------------------------------------------- 리포트 본문


def build_report(cov: Coverage, br: BreathResult, rsa_rows: Sequence[BreathRSA], stats: Sequence[SegmentStats],
                 comps: Sequence[Comparison], pace_bpm: Optional[float], half_width: float, gate: List[str]) -> str:
    L: List[str] = []
    a = L.append
    a(f"# rsalink 연동 리포트 (v{__version__})")
    a("")
    a(f"입력: 호흡 `{cov.resp_file}` ({'파형' if cov.resp_source == 'waveform' else '흡기 시작 이벤트'}), "
      f"RR `{cov.rr_file}`  — 산출물 경로는 파일 이름만 적는다.")
    a("")
    # ① 커버리지
    a("## ① 커버리지 자백 (결과보다 먼저)")
    a("")
    if gate:
        a("**신뢰 불가 — exit 3**: " + "; ".join(gate))
        a("")
    a("| 항목 | 값 |")
    a("|---|---|")
    if cov.resp_source == "waveform":
        a(f"| 호흡 샘플링레이트(Δt 중앙값) | {_f(cov.fs_est, 2)} Hz"
          + (f" — 호흡당 샘플 {_f(cov.samples_per_breath, 1)}개"
             + (" **< 4: 파형이 너무 성김**" if cov.samples_per_breath == cov.samples_per_breath
                and cov.samples_per_breath < 4 else "")) + " |")
        a(f"| 불균등 샘플링(Δt 가 중앙값 ±20% 밖) | {_f(100 * cov.irregular_frac, 1)}% |")
        a(f"| 호흡 갭(Δt > 3×중앙값) | {cov.n_gaps}건, 합계 {_f(cov.gap_total_s, 1)} s |")
        if cov.n_bad_resp_values:
            a(f"| 호흡 값 셀 제외(빈칸·NA·nan/inf) | {cov.n_bad_resp_values}건 |")
        if cov.n_spikes_clipped:
            a(f"| 스파이크 클리핑(디트렌드 전 \\|z\\| > 10·MAD) | {cov.n_spikes_clipped} 샘플 |")
    else:
        a("| 호흡 입력 | 흡기 시작 이벤트만 — 파형이 없어 결맞음·교차상관 제안은 생략, 흡기 끝은 주기의 40% 로 가정 |")
        if cov.ignored_options:
            a(f"| 무시된 옵션 | {', '.join(cov.ignored_options)} — 이벤트 입력에는 적용되지 않음 |")
    a(f"| 호흡 중복 타임스탬프 | {cov.dup_resp}건 |")
    a(f"| 타임스탬프 형식 | 호흡 {cov.time_kinds[0]}, RR {cov.time_kinds[1]}"
      + (f" — {cov.tz_note}" if cov.tz_note else "") + " |")
    if cov.abs_time_diff is not None:
        d = cov.abs_time_diff
        a(f"| 절대 시각 차(호흡 첫 샘플 − RR 첫 타임스탬프, 미적용) | {d:+.2f} s — --resp-offset 후보: "
          f"RR 타임스탬프가 간격 시작이면 {d:+.2f} s / 박동 시각(간격 끝)이면 {d + cov.rr_first_s:+.2f} s |")
    a(f"| 호흡 기록 길이 / RR 기록 길이 | {_f(cov.resp_duration_s / 60, 1)}분 / {_f(cov.rr_duration_s / 60, 1)}분 |")
    a(f"| 두 신호가 겹치는 사용 가능 구간 | {_f(cov.usable_s / 60, 1)}분 |")
    a(f"| RR 단위 자동 인식 / 열 | {cov.rr_unit} / `{cov.rr_col}` |")
    a(f"| RR 제외(300–2000 ms 밖 + 파싱 실패 셀) | {cov.rr_excluded}/{cov.rr_total} ({_f(100 * cov.rr_excluded_frac, 1)}%)"
      f" — 파싱 실패(빈칸·NA·nan/inf) {cov.rr_unparsed}건 포함, 보정 없이 제외만(시간축은 중앙값 RR 로 진행) |")
    flag_txt = ", ".join(f"{k} {v}" for k, v in cov.flags.items() if v) or "없음"
    a(f"| 검출 호흡 / 유효 호흡 | {cov.n_breaths} / {cov.n_valid} (아티팩트 {_f(100 * cov.artifact_frac, 1)}%: {flag_txt}) |")
    if cov.rate_unstable:
        a(f"| 호흡 검출 불안정 | 유효 호흡수 CV > {RATE_CV_FLAG * 100:.0f}% — 호흡수 평균이 대표값이 아닐 수 있음 |")
    a(f"| 호흡당 박동 수(평균) | {_f(cov.beats_per_breath, 1)} — 3 미만 호흡은 RSA 판정 불가 ({cov.n_undetermined}건) |")
    a(f"| --resp-offset (적용값) | {_f(cov.resp_offset, 2)} s |")
    a(f"| --rsa-lag (RSA 창 위상 지연 허용치) | {_f(cov.rsa_lag, 2)} s |")
    if cov.offset_suggest is not None:
        lag, r = cov.offset_suggest
        hint = " r > 0: 호흡 부호가 반대일 수 있음(`--invert` 확인)." if r > 0 else ""
        period = cov.mean_breath_period_s
        amb = (f" 탐색 범위 ±5 s 안의 최대 |r| 이며 호흡 주기(≈{period:.1f} s)의 배수만큼 모호할 수 있다."
               if period == period else " 탐색 범위 ±5 s.")
        a(f"| 교차상관 제안 오프셋(표시만, 미적용) | {lag:+.2f} s (r = {r:+.2f}) — 맞다고 판단되면 "
          f"`--resp-offset {lag:.2f}` 로 직접 지정.{hint}{amb} |")
    if cov.short_segments:
        a(f"| 5분 미만 구간(스펙트럼 '짧음') | {', '.join(_md(s) for s in cov.short_segments)} |")
    a("")
    # ② 호흡 검출
    a("## ② 호흡 검출")
    a("")
    rates = br.rates()
    if rates:
        from .breath import rate_summary
        s = rate_summary(rates)
        a(f"유효 호흡 {s['n']}회: 호흡수 평균 {_f(s['mean'], 2)} ± {_f(s['sd'], 2)} 회/분 "
          f"(CV {_f(100 * s['cv'], 1)}%, 중앙값 {_f(s['median'], 2)})."
          + (f" **호흡 검출 불안정**(CV > {RATE_CV_FLAG * 100:.0f}%) — 호흡수 평균·호흡중심 대역이 "
             "대표값이 아닐 수 있음." if cov.rate_unstable else ""))
    else:
        a("유효 호흡 없음.")
    if br.source == "waveform":
        a(f"검출 임계 = max({br.k_mad:g} × 잡음 MAD·1.4826 = {_f(br.k_mad * br.mad, 3)}, "
          f"0.15 × (60 s 창별 p95−p5) = {_f(br.prom_floor, 3)} [창별 {_f(br.prom_floor_lo, 3)}–{_f(br.prom_floor_hi, 3)}]) "
          f"= {_f(br.prom_thr, 3)} (파형 단위, 대표값 — 바닥은 골마다 그 자리의 창 값). "
          f"평활 {br.smooth_s:g} s, 기준선 이동 중앙값 창 {br.baseline_win_s:g} s. "
          "흡기 시작 = 골에서 봉우리 쪽으로 되오름(max(3·σ_smooth, 흡기 진폭의 10%)) 전까지의 누적 최소 자리.")
    a("")
    # ③ RSA
    a("## ③ RSA peak-valley (Grossman 1990)")
    a("")
    a(f"호흡마다 호기 창 최대 RR − 흡기 창 최소 RR (ms). RR 값은 간격 중점 시각에 배정하고, "
      f"흡기/호기 창은 `--rsa-lag` {cov.rsa_lag:.2f} s 만큼 뒤로 민다(생리적 호흡→RR 위상 지연 허용치). "
      "값이 음수면 위상 지연 의심: `--rsa-lag` 확인, 호흡별.csv 의 RR최소_위상·RR최대_위상 열 참조"
      "(부호 반전 장비면 `--invert`).")
    a("")
    # ④~⑥ 구간표
    a("## ④ 구간별 표")
    a("")
    a("| 구간 | 길이 | 호흡수(회/분) | RSA_pv 중앙값 [IQR] (ms) | n/판정불가 | 호흡당 박동 | LF (ms²) | HF 고정 0.15–0.40 (ms²) | 호흡중심 ±%.2f Hz (ms²) | 결맞음 | 평균 RR (ms) | 플래그 |" % half_width)
    a("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for st in stats:
        b = st.band
        flags = []
        if st.short_flag:
            flags.append("짧음(<5분)")
        if st.slow_flag:
            flags.append("호흡 ≤9/분: 고정 HF 는 이 구간의 RSA 를 과소평가")
        if st.rate_cv_flag:
            flags.append(f"호흡수 CV {_f(100 * st.rate['cv'], 0)}% > 15%: 호흡중심 대역의 평균 중심이 무의미할 수 있음(이봉/불안정)")
        if st.spectral_note:
            flags.append(st.spectral_note)
        coh = "—"
        if b is not None and b.coherence_at_resp is not None:
            coh = (f"{b.coherence_at_resp:.2f} (L={b.n_segments}, 독립 기대 1/L {b.coherence_bias:.2f}, "
                   f"null 95% {b.coherence_null95:.2f})")
        band_txt = "—"
        if b is not None:
            band_txt = f"{_f(b.resp_centered, 1)} [{b.resp_band[0]:.3f}–{b.resp_band[1]:.3f}]"
        span = f"{_mmss(st.seg.t0)}–{_mmss(st.seg.t1)}"
        if st.seg.clipped:
            span += (f" (요청 {_mmss(st.seg.t0_req)}–{_mmss(st.seg.t1_req)}, 데이터 범위로 "
                     f"{100 * st.seg.clipped_frac:.0f}% 잘림)")
        a(f"| {_md(st.seg.label)} | {span} | "
          f"{_f(st.rate['mean'], 2)} ± {_f(st.rate['sd'], 2)} (n={st.rate['n']}) | "
          f"{_f(st.rsa['median'], 1)} [{_f(st.rsa['q1'], 1)}–{_f(st.rsa['q3'], 1)}] | "
          f"{st.rsa['n']}/{st.rsa['n_undetermined']} | {_f(st.rsa['beats_per_breath'], 1)} | "
          f"{_f(b.lf, 1) if b else '—'} | {_f(b.hf_fixed, 1) if b else '—'} | {band_txt} | {coh} | "
          f"{_f(st.mean_rr, 1)} | {'; '.join(flags) if flags else ''} |")
    a("")
    seg_notes = [f"{_md(st.seg.label)}: 세그먼트 {st.band.nperseg} 샘플 = {st.band.nperseg / 4:.0f} s, "
                 f"Δf {st.band.df:.4f} Hz, L = {st.band.n_segments}" for st in stats if st.band]
    a("스펙트럼: RR 4 Hz 선형 보간 → Welch(Hann, 50% 겹침, 세그먼트별 평균 제거). 구간별 세그먼트 길이·Δf — "
      + ("; ".join(seg_notes) if seg_notes else "스펙트럼 없음")
      + ". 결맞음은 호흡주파수에 가장 가까운 단일 빈의 MSC; 독립 신호의 기대값 ≈ 1/L, "
        "null 95% ≈ 1 − 0.05^(1/(L−1)).")
    a("")
    # ⑤ 페이싱
    if pace_bpm:
        a(f"## ⑤ 페이싱 동조 (목표 {pace_bpm:g} 회/분, ±10%)")
        a("")
        a("| 구간 | 동조 호흡 비율 | 첫 5연속 동조 시각 | 동조 중 호흡수 SD | 동조 호흡 수 |")
        a("|---|---|---|---|---|")
        for st in stats:
            p = st.pace
            if p is None:
                continue
            a(f"| {_md(st.seg.label)} | {_f(p.pct_within, 1)}% ({p.n_within}/{p.n_breaths}) | "
              f"{_mmss(p.first_entrain_t) if p.first_entrain_t is not None else '없음'} | "
              f"{_f(p.sd_entrained, 2)} | {p.n_entrained} |")
        a("")
    # ⑥ 비교·판정
    if comps:
        a("## ⑥ 조건 비교 (자극 − 기준선) 와 판정")
        a("")
        for c in comps:
            a(f"### {_md(c.base.seg.label)} → {_md(c.stim.seg.label)}")
            a("")
            Lb = c.base.band.n_segments if c.base.band else None
            Ls = c.stim.band.n_segments if c.stim.band else None
            a("| 지표 | 기준선 | 자극 | Δ | 비율 | Welch L (기준/자극) |")
            a("|---|---|---|---|---|---|")
            for d in c.deltas:
                spectral = d.metric.startswith(("LF", "HF", "호흡중심", "결맞음"))
                l_txt = f"{Lb if Lb is not None else '—'}/{Ls if Ls is not None else '—'}" if spectral else ""
                a(f"| {d.metric} | {_f(d.base, 2)} | {_f(d.stim, 2)} | {_f(d.delta, 2)} | {_f(d.ratio, 2)} | {l_txt} |")
            a("")
            for ln in c.lines:
                a(f"- {ln}")
            a("")
            lo_r, hi_r = ratio_bounds(c.margin_ln)
            L_eff_txt = f"{c.L_eff:g}" if c.L_eff == c.L_eff else "—"
            a(f"판정 기준: HF 고정 변화 = |ln(자극/기준)| > 잡음 마진 {c.margin_ln:.2f} = ±{c.margin_pct:.0f}% "
              f"(비율 {lo_r:.2f} 배 미만 또는 {hi_r:.2f} 배 초과; 정확히 경계값은 변화 없음). "
              f"잡음 마진 = max(ln {POWER_RATIO_HI:.2f}, {MARGIN_Z}·√(2/L_eff)), L_eff = min(Welch L)·(HF 대역 빈 수 × {WELCH_BIN_EFF:g}) = {L_eff_txt}"
              f" — 바닥 ±{POWER_CHANGE_FRAC * 100:.0f}%(비율 {POWER_RATIO_LO:.2f}–{POWER_RATIO_HI:.2f}) 는 L 이 클 때의 값. "
              f"호흡수 변화 = |Δ| ≥ max({RATE_CHANGE_MIN_BPM:g}회/분, 기준의 {RATE_CHANGE_FRAC * 100:.0f}%). "
              f"판정 보류: HF 고정 < {HF_FLOOR_MS2:.0f} ms² 는 {HF_FLOOR_MS2:.0f} 으로 놓고 본 |ln 비| 가 마진 안일 때, "
              f"Welch L < {MIN_WELCH_SEGMENTS}, RSA 판정불가 우세(호흡당 박동 < 3 또는 판정불가 > 판정), 스펙트럼 없음, 구간 데이터 부족. "
              f"잡음 마진(±{c.margin_pct:.0f}% — L·대역 빈 수 기반) · {HF_FLOOR_MS2:.0f} ms² · L ≥ {MIN_WELCH_SEGMENTS} 는 문헌 근거 없는 "
              "이 툴의 관례다. '동반됨'은 두 변화가 같은 기록에서 함께 관찰됐다는 뜻이며 기전을 말하지 않는다.")
            a("")
        a(f"> RSA 는 호흡수와 깊이 모두에 의존하며 **깊이는 미측정**(파형 진폭 비보정) — "
          "Grossman & Taylor 2007; Hirsch & Bishop 1981. 표의 어떤 Δ 도 인과를 뜻하지 않는다.")
        a("")
    # ⑦ 문장
    a("## ⑦ Methods / Results 문장 초안")
    a("")
    nps = sorted({st.band.nperseg for st in stats if st.band})
    Ls = [st.band.n_segments for st in stats if st.band]
    excl_txt = f"{100 * cov.rr_excluded_frac:.1f}%"
    if br.source == "events":
        det_kr = ("흡기 시작은 제공된 이벤트 목록을 그대로 썼고, 흡기 끝은 주기의 40% 지점으로 가정했다(파형이 없어 "
                  "결맞음은 계산하지 않았다).")
        det_en = ("Inspiration onsets were taken from the supplied event list; end of inspiration was assumed at 40% of each "
                  "cycle (no waveform was available, so coherence was not computed).")
    else:
        det_kr = ("호흡 신호에서 선형 디트렌드·이동 중앙값(창 %.0f s) 기준선 제거·%.1f s 이동평균 후 국소 최솟값"
                  "(돌출 ≥ max(%g × 잡음 MAD·1.4826, 0.15 × 60 s 창별 (p95−p5)), 최소 간격 %.1f s)을 골로 잡고, "
                  "봉우리에서 골 쪽으로 되오름 전까지의 누적 최소 자리를 흡기 시작으로 정해 호흡을 분할했다."
                  % (br.baseline_win_s, br.smooth_s, br.k_mad, br.min_breath_s))
        det_en = ("Troughs were identified as local minima of the linearly detrended, baseline-corrected "
                  "(%.0f-s moving median) and %.1f-s moving-average-smoothed respiration signal (prominence ≥ max(%g × noise MAD·1.4826, "
                  "0.15 × (p95−p5) in 60-s windows), minimum interval %.1f s); each inspiration onset was placed at the running minimum "
                  "reached when descending from the following peak, before the signal rose back above it."
                  % (br.baseline_win_s, br.smooth_s, br.k_mad, br.min_breath_s))
    a("**Methods (KR)** " + det_kr +
      " 주기가 %.1f–%.0f s 밖이거나 흡기 진폭이 임계 미만인 호흡은 아티팩트로 제외했다(전체의 %.1f%%). "
      "RSA 는 호흡별 peak-valley 법(호기 창 최대 RR − 흡기 창 최소 RR; Grossman et al., 1990)으로 산출했다: RR 값은 간격 중점 시각에 "
      "배정하고 흡기·호기 창을 %.1f s 뒤로 밀어(호흡→RR 위상 지연 허용치) 극값을 찾았으며, 호흡당 박동이 3개 미만인 호흡은 제외했다. "
      "RR(300–2000 ms 밖 %s 제외, 보정 없음)을 4 Hz 로 선형 보간해 Welch PSD(Hann, 세그먼트 %s 샘플 = %s s, 50%% 겹침, 세그먼트별 평균 제거, "
      "세그먼트 수 L = %s)를 구하고 LF 0.04–0.15 Hz, HF 0.15–0.40 Hz(Task Force, 1996)와 실측 평균 호흡주파수 ±%.2f Hz 의 호흡중심 대역 파워를 "
      "계산했다.%s RSA 는 호흡수와 호흡 깊이 모두에 의존하는데(Hirsch & Bishop, 1981; Grossman & Taylor, 2007) 호흡 깊이는 측정하지 않았다."
      % (br.min_breath_s, br.max_breath_s, 100 * cov.artifact_frac, cov.rsa_lag, excl_txt,
         "/".join(str(n) for n in nps) or "—", "/".join(f"{n / 4:.0f}" for n in nps) or "—",
         "/".join(str(x) for x in Ls) or "—", half_width,
         (" 호흡 파형과 RR 의 magnitude-squared coherence 는 같은 Welch 세그먼트로 구해 호흡주파수에 가장 가까운 단일 빈 값을 보고했다"
          "(독립 신호의 기대값 ≈ 1/L)." if br.source == "waveform" else "")))
    a("")
    a("**Methods (EN)** " + det_en +
      " Breaths with a period outside %.1f–%.0f s or an inspiratory amplitude below threshold were excluded as artifacts (%.1f%% of all breaths). "
      "RSA was quantified breath-by-breath with the peak-valley method (maximum RR during expiration minus minimum RR during inspiration; "
      "Grossman et al., 1990): each RR value was assigned to the midpoint of its interval, the inspiratory and expiratory windows were shifted "
      "%.1f s later to allow for the respiration-to-RR phase lag, and breaths with fewer than three beats were excluded. "
      "RR intervals outside 300–2000 ms (%s) were excluded without correction. RR series were linearly interpolated at 4 Hz and Welch PSDs "
      "(Hann window, %s-sample = %s-s segments, 50%% overlap, mean removed per segment, L = %s segments) were computed for LF (0.04–0.15 Hz), "
      "fixed HF (0.15–0.40 Hz; Task Force, 1996), and a respiration-centred band (measured mean breathing frequency ±%.2f Hz).%s "
      "RSA depends on both breathing rate and depth (Hirsch & Bishop, 1981; Grossman & Taylor, 2007); tidal volume was not measured."
      % (br.min_breath_s, br.max_breath_s, 100 * cov.artifact_frac, cov.rsa_lag, excl_txt,
         "/".join(str(n) for n in nps) or "—", "/".join(f"{n / 4:.0f}" for n in nps) or "—",
         "/".join(str(x) for x in Ls) or "—", half_width,
         (" Magnitude-squared coherence between respiration and RR was computed from the same Welch segments and reported at the "
          "single bin nearest the breathing frequency (expected value for independent signals ≈ 1/L)." if br.source == "waveform" else "")))
    a("")
    if comps:
        c = comps[0]
        d = {x.metric: x for x in c.deltas}
        pct_txt = f"±{c.margin_pct:.0f}%"
        arrow_kr = {"↑": "증가", "↓": "감소", "→": f"잡음 마진 {pct_txt} 안", "?": "없음"}
        arrow_en = {"↑": "increased", "↓": "decreased", "→": f"within the noise margin ({pct_txt})", "?": "unavailable"}
        if c.verdict_hf == "동반됨":
            kr_hf = "HF 고정 파워 변화는 호흡수 변화와 동반되었고"
            en_hf = "The change in fixed-HF power was accompanied by a change in breathing rate"
        elif c.verdict_hf == "비동반":
            kr_hf = "HF 고정 파워 변화는 호흡수 변화와 동반되지 않았고"
            en_hf = "The change in fixed-HF power was not accompanied by a change in breathing rate"
        elif "잡음 마진" in c.verdict_hf and not c.hold_reasons:
            kr_hf = f"HF 고정 파워 변화가 잡음 마진({pct_txt}, L·대역 빈 수 기반) 안이라 동반 여부는 판정하지 않았고"
            en_hf = (f"Fixed-HF power changed by less than the noise margin ({pct_txt}, from Welch L and the number of "
                     "HF-band bins), so accompaniment was not classified")
        else:
            kr_hf = f"HF 고정 파워의 동반 여부는 판정하지 않았고({'; '.join(c.hold_reasons) or c.verdict_hf})"
            en_hf = "Accompaniment of the fixed-HF change was not classified (insufficient data: %s)" % (
                "; ".join(c.hold_reasons) or c.verdict_hf)
        hd, rd = c.hf_direction, c.resp_direction
        if c.verdict_band == "동일":
            kr_band = f"호흡중심 대역으로 보아도 방향이 같았다(HF 고정 {arrow_kr[hd]}, 호흡중심 {arrow_kr[rd]})"
            en_band = f"with the respiration-centred band the direction was the same (fixed HF {arrow_en[hd]}, respiration-centred {arrow_en[rd]})"
        elif c.verdict_band == "상이":
            kr_band = f"호흡중심 대역으로 보면 방향이 달랐다(HF 고정 {arrow_kr[hd]}, 호흡중심 {arrow_kr[rd]})"
            en_band = f"with the respiration-centred band the direction differed (fixed HF {arrow_en[hd]}, respiration-centred {arrow_en[rd]})"
        else:
            kr_band = "호흡중심 대역과의 비교는 판정하지 않았다"
            en_band = "the comparison with the respiration-centred band was not classified"
        a("**Results (KR)** 호흡수는 %s → %s 회/분, RSA_pv 중앙값은 %s → %s ms, HF 고정 파워는 %s → %s ms², "
          "호흡중심 대역 파워는 %s → %s ms² 였다. %s, %s."
          % (_f(d["호흡수(회/분)"].base, 1), _f(d["호흡수(회/분)"].stim, 1),
             _f(d["RSA_pv 중앙값(ms)"].base, 1), _f(d["RSA_pv 중앙값(ms)"].stim, 1),
             _f(d["HF 고정(ms²)"].base, 1), _f(d["HF 고정(ms²)"].stim, 1),
             _f(d["호흡중심(ms²)"].base, 1), _f(d["호흡중심(ms²)"].stim, 1),
             kr_hf, kr_band))
        a("")
        a("**Results (EN)** Breathing rate changed from %s to %s breaths/min, median RSA_pv from %s to %s ms, fixed-HF power from %s to %s ms², "
          "and respiration-centred power from %s to %s ms². %s; %s."
          % (_f(d["호흡수(회/분)"].base, 1), _f(d["호흡수(회/분)"].stim, 1),
             _f(d["RSA_pv 중앙값(ms)"].base, 1), _f(d["RSA_pv 중앙값(ms)"].stim, 1),
             _f(d["HF 고정(ms²)"].base, 1), _f(d["HF 고정(ms²)"].stim, 1),
             _f(d["호흡중심(ms²)"].base, 1), _f(d["호흡중심(ms²)"].stim, 1), en_hf, en_band))
        a("")
    # ⑧ 근거
    a("## ⑧ 근거 (이 여섯 편만 인용)")
    a("")
    a("| 인용 | 출처 | 이 툴이 쓰는 내용 |")
    a("|---|---|---|")
    for name, src, use in REFS:
        a(f"| {name} | {src} | {use} |")
    a("")
    # ⑨ 면책
    a("## ⑨ 면책")
    a("")
    a(DISCLAIMER)
    a("")
    return "\n".join(L)


# ---------------------------------------------------------------- 산출물


ARTIFACT_NAMES = (REPORT_MD, BREATH_CSV, COMPARE_CSV)


def _guard_target(t: str, inputs: Sequence[str] = ()) -> None:
    """산출물 경로 하나를 쓰기 전에 검사: 심볼릭링크 / 하드링크(nlink>1) / 입력 파일과 같은 실제 경로 → exit 2."""
    if os.path.islink(t):
        raise RsalinkError(
            f"{os.path.basename(t)} 이(가) 심볼릭링크입니다 — 링크를 따라가 다른 파일을 "
            "덮어쓰지 않도록 거부합니다. 링크를 지우거나 비어 있는 --out-dir 을 쓰세요")
    if os.path.exists(t) and not os.path.isdir(t) and os.stat(t).st_nlink > 1:
        raise RsalinkError(
            f"{os.path.basename(t)} 이(가) 하드링크(연결 수 {os.stat(t).st_nlink})입니다 — "
            "다른 이름으로 연결된 파일을 함께 덮어쓰지 않도록 거부합니다. "
            "파일을 지우거나 비어 있는 --out-dir 을 쓰세요")
    rt = os.path.realpath(t)
    for inp in inputs:
        if inp and os.path.realpath(inp) == rt:
            raise RsalinkError(
                f"산출물 {os.path.basename(t)} 이(가) 입력 파일 {os.path.basename(inp)} 과 같은 파일입니다 — "
                "입력을 덮어쓰지 않도록 거부합니다. 다른 --out-dir 을 쓰세요")


def guard_artifacts(out_dir: str, inputs: Sequence[str] = ()) -> None:
    """산출물 3종을 **하나라도 쓰기 전에** 모두 검사(라운드 1 B1)."""
    for name in ARTIFACT_NAMES:
        _guard_target(os.path.join(out_dir, name), inputs)


def _strip_private(p: str) -> str:
    return p[len("/private"):] if p.startswith("/private/") else p


def path_differs_beyond_private(given_abs: str, real: str) -> bool:
    """realpath 가 준 경로와 다른가 — 단, macOS 의 /tmp·/var → /private/tmp·/private/var 접두 차이만이면
    링크로 치지 않는다(라운드 2 #8). 앞의 '/private' 를 벗긴 뒤 비교."""
    return _strip_private(real) != _strip_private(given_abs)


def prepare_out_dir(out_dir: str, inputs: Sequence[str] = ()) -> Tuple[str, Optional[str]]:
    """→ (실제 경로, 링크였다면 준 경로 else None). expanduser + realpath. 쓰기 프로브는 mkstemp(난수명, O_EXCL).
    "(링크 → 실제경로)" 표기는 realpath 가 준 경로와 /private 접두 이상으로 다를 때만(폴더가 아직 없어도 부모 링크는 잡힌다)."""
    given = os.path.expanduser(out_dir)
    real = os.path.realpath(given)
    link_note = given if path_differs_beyond_private(os.path.abspath(given), real) else None
    if os.path.exists(real) and not os.path.isdir(real):
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 은(는) 이미 있는 파일입니다 — 폴더 이름을 주세요")
    try:
        os.makedirs(real, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".rsalink_probe_", dir=real)
        os.close(fd)
        os.remove(probe)
    except PermissionError:
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 에 쓰기 권한이 없습니다")
    except OSError as e:
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 을(를) 만들 수 없습니다: {e.strerror}")
    guard_artifacts(real, inputs)
    return real, link_note


def _write_atomic(path: str, data: str, encoding: str) -> None:
    """같은 폴더의 임시파일에 쓴 뒤 os.replace — 절반만 쓰인 산출물을 남기지 않는다."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".rsalink_tmp_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.4g}"
    s = str(v)
    if s and s[0] in "=+-@\t\r":
        return "'" + s
    return s


def _write_csv(path: str, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    _write_atomic(path, buf.getvalue(), "utf-8-sig")


def write_artifacts(out_dir: str, report_md: str, rsa_rows: Sequence[BreathRSA], stats: Sequence[SegmentStats],
                    comps: Sequence[Comparison], pace_bpm: Optional[float], subject: str,
                    inputs: Sequence[str] = (), resp_offset: float = 0.0) -> List[str]:
    guard_artifacts(out_dir, inputs)      # 셋 다 쓰기 전에 다시 검사(계산 사이 변경 대비)
    written = []
    p = os.path.join(out_dir, REPORT_MD)
    _write_atomic(p, report_md, "utf-8")
    written.append(p)
    # 호흡별.csv — 시각은 호흡 시계 기준(초). 구간은 RR 시계 기준이므로 라벨 배정 때 resp_offset 을 뺀다(D10, segments.py 와 일치)
    seg_of = []
    for r in rsa_rows:
        lab = ""
        for st in stats:
            if st.seg.t0 - resp_offset <= r.breath.t_onset < st.seg.t1 - resp_offset:
                lab = st.seg.label
                break
        seg_of.append(lab)
    rows = []
    for r, lab in zip(rsa_rows, seg_of):
        b = r.breath
        rows.append([subject, b.idx, lab, round(b.t_onset, 3), round(b.t_peak, 3), round(b.t_end, 3),
                     round(b.period_s, 3), round(b.rate_bpm, 3) if b.valid else None, b.flag or "",
                     r.n_beats, r.n_insp, r.n_exp, r.rr_min_insp, r.rr_max_exp, r.rsa_pv, r.reason or "",
                     (round(r.phase_min, 3) if r.phase_min is not None else None),
                     (round(r.phase_max, 3) if r.phase_max is not None else None)])
    _write_csv(os.path.join(out_dir, BREATH_CSV),
               ["subject", "breath_idx", "segment", "t_onset_s", "t_peak_s", "t_end_s", "period_s", "rate_bpm",
                "flag", "n_beats", "n_insp", "n_exp", "rr_min_insp_ms", "rr_max_exp_ms", "rsa_pv_ms", "rsa_reason",
                "RR최소_위상", "RR최대_위상"],
               rows)
    written.append(os.path.join(out_dir, BREATH_CSV))
    # 조건비교.csv — 한 피험자 = 구간당 한 행 (statwise/longistat 투입용 long 형식)
    rows = []
    for st in stats:
        b = st.band
        rows.append([subject, st.seg.label, round(st.seg.t0, 1), round(st.seg.t1, 1), round(st.seg.duration_s, 1),
                     (round(st.seg.t0_req, 1) if st.seg.clipped else None),
                     (round(st.seg.t1_req, 1) if st.seg.clipped else None),
                     round(st.seg.clipped_frac, 4),
                     st.n_breaths, st.n_valid, st.rate["mean"], st.rate["sd"], st.rsa["median"], st.rsa["q1"],
                     st.rsa["q3"], st.rsa["n"], st.rsa["n_undetermined"], st.rsa["beats_per_breath"],
                     b.lf if b else None, b.hf_fixed if b else None, b.resp_centered if b else None,
                     b.resp_band[0] if b else None, b.resp_band[1] if b else None,
                     (b.coherence_at_resp if b and b.coherence_at_resp is not None else None),
                     (b.coherence_null95 if b and b.coherence_null95 is not None else None),
                     b.n_segments if b else None, b.nperseg if b else None, b.df if b else None, st.mean_rr,
                     st.pace.pct_within if st.pace else None,
                     (st.pace.first_entrain_t if st.pace and st.pace.first_entrain_t is not None else None),
                     st.pace.sd_entrained if st.pace else None,
                     int(st.short_flag), int(st.slow_flag), int(st.rate_cv_flag)])
    _write_csv(os.path.join(out_dir, COMPARE_CSV),
               ["subject", "segment", "t0_s", "t1_s", "duration_s", "t0_requested_s", "t1_requested_s",
                "clipped_frac", "n_breaths", "n_valid", "resp_rate_mean",
                "resp_rate_sd", "rsa_pv_median_ms", "rsa_pv_q1_ms", "rsa_pv_q3_ms", "rsa_n", "rsa_undetermined",
                "beats_per_breath", "lf_ms2", "hf_fixed_ms2", "resp_centered_ms2", "resp_band_lo_hz",
                "resp_band_hi_hz", "coherence", "coherence_null95", "welch_segments", "welch_nperseg", "welch_df_hz",
                "mean_rr_ms", "pace_pct_within", "pace_first_entrain_s", "pace_sd_entrained", "flag_short",
                "flag_slow_breath", "flag_rate_cv_gt15"],
               rows)
    written.append(os.path.join(out_dir, COMPARE_CSV))
    return written
