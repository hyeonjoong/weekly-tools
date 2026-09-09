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
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import RsalinkError, __version__
from .breath import BreathResult
from .parse import RRSeries
from .rsa import BreathRSA
from .segments import Comparison, SegmentStats, SHORT_SEGMENT_S, SLOW_BREATH_BPM

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
     "peak-valley·스펙트럼·복합 디트렌드 3법 상호상관 >.92(개인 내 평균 .96); peak-valley 가 호흡과 가장 높은 상관(.91 vs .84)"),
    ("Grossman & Taylor 2007", "Biol Psychol 74:263. doi:10.1016/j.biopsycho.2005.11.014",
     "호흡 파라미터가 RSA–미주신경 관계를 교란할 수 있음; 신체활동·β-아드레날린 톤도 영향"),
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
    offset_suggest: Optional[tuple]
    peak_assumed: bool
    time_kinds: tuple
    short_segments: List[str] = field(default_factory=list)

    def gate_reasons(self) -> List[str]:
        r = []
        if self.n_breaths and (self.n_valid / self.n_breaths) < GATE_VALID_BREATH_FRAC:
            r.append(f"유효 호흡 {100 * self.n_valid / self.n_breaths:.0f}% < {GATE_VALID_BREATH_FRAC * 100:.0f}%")
        if self.n_breaths == 0:
            r.append("검출된 호흡 없음")
        if self.usable_s < GATE_USABLE_S:
            r.append(f"사용 가능 구간 {self.usable_s / 60:.1f}분 < {GATE_USABLE_S / 60:.0f}분")
        if self.rr_excluded_frac > GATE_RR_EXCLUDED_FRAC:
            r.append(f"RR 제외 {100 * self.rr_excluded_frac:.0f}% > {GATE_RR_EXCLUDED_FRAC * 100:.0f}%")
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
        a(f"| 호흡 샘플링레이트(Δt 중앙값) | {_f(cov.fs_est, 2)} Hz |")
        a(f"| 불균등 샘플링(Δt 가 중앙값 ±20% 밖) | {_f(100 * cov.irregular_frac, 1)}% |")
        a(f"| 호흡 갭(Δt > 3×중앙값) | {cov.n_gaps}건, 합계 {_f(cov.gap_total_s, 1)} s |")
    else:
        a("| 호흡 입력 | 흡기 시작 이벤트만 — 파형이 없어 결맞음·교차상관 제안은 생략, 흡기 끝은 주기의 40% 로 가정 |")
    a(f"| 호흡 중복 타임스탬프 | {cov.dup_resp}건 |")
    a(f"| 타임스탬프 형식 | 호흡 {cov.time_kinds[0]}, RR {cov.time_kinds[1]} |")
    a(f"| 호흡 기록 길이 / RR 기록 길이 | {_f(cov.resp_duration_s / 60, 1)}분 / {_f(cov.rr_duration_s / 60, 1)}분 |")
    a(f"| 두 신호가 겹치는 사용 가능 구간 | {_f(cov.usable_s / 60, 1)}분 |")
    a(f"| RR 단위 자동 인식 / 열 | {cov.rr_unit} / `{cov.rr_col}` |")
    a(f"| RR 300–2000 ms 밖 제외 | {cov.rr_excluded}/{cov.rr_total} ({_f(100 * cov.rr_excluded_frac, 1)}%) — 보정 없이 제외만 |")
    flag_txt = ", ".join(f"{k} {v}" for k, v in cov.flags.items() if v) or "없음"
    a(f"| 검출 호흡 / 유효 호흡 | {cov.n_breaths} / {cov.n_valid} (아티팩트 {_f(100 * cov.artifact_frac, 1)}%: {flag_txt}) |")
    a(f"| 호흡당 박동 수(평균) | {_f(cov.beats_per_breath, 1)} — 3 미만 호흡은 RSA 판정 불가 ({cov.n_undetermined}건) |")
    a(f"| --resp-offset (적용값) | {_f(cov.resp_offset, 2)} s |")
    if cov.offset_suggest is not None:
        lag, r = cov.offset_suggest
        a(f"| 교차상관 제안 오프셋(표시만, 미적용) | {lag:+.2f} s (r = {r:+.2f}) — 맞다고 판단되면 `--resp-offset {lag:.2f}` 로 직접 지정 |")
    if cov.short_segments:
        a(f"| 5분 미만 구간(스펙트럼 '짧음') | {', '.join(cov.short_segments)} |")
    a("")
    # ② 호흡 검출
    a("## ② 호흡 검출")
    a("")
    rates = br.rates()
    if rates:
        from .breath import rate_summary
        s = rate_summary(rates)
        a(f"유효 호흡 {s['n']}회: 호흡수 평균 {_f(s['mean'], 2)} ± {_f(s['sd'], 2)} 회/분 "
          f"(CV {_f(100 * s['cv'], 1)}%, 중앙값 {_f(s['median'], 2)}).")
    else:
        a("유효 호흡 없음.")
    a("")
    # ③ RSA
    a("## ③ RSA peak-valley (Grossman 1990)")
    a("")
    a("호흡마다 호기 창 최대 RR − 흡기 창 최소 RR (ms). 값이 음수면 RR 극값이 창과 어긋난 것 — "
      "시계 오프셋(--resp-offset)이나 부호(--invert)를 확인.")
    a("")
    # ④~⑥ 구간표
    a("## ④ 구간별 표")
    a("")
    a("| 구간 | 길이 | 호흡수(회/분) | RSA_pv 중앙값 [IQR] (ms) | n/판정불가 | LF (ms²) | HF 고정 0.15–0.40 (ms²) | 호흡중심 ±%.2f Hz (ms²) | 결맞음 | 평균 RR (ms) | 플래그 |" % half_width)
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for st in stats:
        b = st.band
        flags = []
        if st.short_flag:
            flags.append("짧음(<5분)")
        if st.slow_flag:
            flags.append("호흡 ≤9/분: 고정 HF 는 이 구간의 RSA 를 과소평가")
        if st.spectral_note:
            flags.append(st.spectral_note)
        coh = "—"
        if b is not None and b.coherence_at_resp is not None:
            coh = f"{b.coherence_at_resp:.2f} (L={b.n_segments}, 독립 기대 {b.coherence_bias:.2f})"
        band_txt = "—"
        if b is not None:
            band_txt = f"{_f(b.resp_centered, 1)} [{b.resp_band[0]:.3f}–{b.resp_band[1]:.3f}]"
        a(f"| {st.seg.label} | {_mmss(st.seg.t0)}–{_mmss(st.seg.t1)} | "
          f"{_f(st.rate['mean'], 2)} ± {_f(st.rate['sd'], 2)} (n={st.rate['n']}) | "
          f"{_f(st.rsa['median'], 1)} [{_f(st.rsa['q1'], 1)}–{_f(st.rsa['q3'], 1)}] | "
          f"{st.rsa['n']}/{st.rsa['n_undetermined']} | "
          f"{_f(b.lf, 1) if b else '—'} | {_f(b.hf_fixed, 1) if b else '—'} | {band_txt} | {coh} | "
          f"{_f(st.mean_rr, 1)} | {'; '.join(flags) if flags else ''} |")
    a("")
    a("스펙트럼: RR 4 Hz 선형 보간 → Welch(Hann, 50% 겹침"
      + (f", 세그먼트 {stats[0].band.nperseg} 샘플 = {stats[0].band.nperseg / 4:.0f} s, Δf {stats[0].band.df:.4f} Hz"
         if stats and stats[0].band else "") + "). 결맞음은 호흡주파수 빈의 MSC.")
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
            a(f"| {st.seg.label} | {_f(p.pct_within, 1)}% ({p.n_within}/{p.n_breaths}) | "
              f"{_mmss(p.first_entrain_t) if p.first_entrain_t is not None else '없음'} | "
              f"{_f(p.sd_entrained, 2)} | {p.n_entrained} |")
        a("")
    # ⑥ 비교·판정
    if comps:
        a("## ⑥ 조건 비교 (자극 − 기준선) 와 판정")
        a("")
        for c in comps:
            a(f"### {c.base.seg.label} → {c.stim.seg.label}")
            a("")
            a("| 지표 | 기준선 | 자극 | Δ | 비율 |")
            a("|---|---|---|---|---|")
            for d in c.deltas:
                a(f"| {d.metric} | {_f(d.base, 2)} | {_f(d.stim, 2)} | {_f(d.delta, 2)} | {_f(d.ratio, 2)} |")
            a("")
            for ln in c.lines:
                a(f"- {ln}")
            a("")
            a("판정 기준: HF 고정 변화 = 비율 ±20% 밖, 호흡수 변화 = |Δ| ≥ max(1회/분, 기준의 10%). "
              "'동반됨'은 두 변화가 같은 기록에서 함께 관찰됐다는 뜻이며 방향·기전을 말하지 않는다.")
            a("")
        a(f"> RSA 는 호흡수와 깊이 모두에 의존하며 **깊이는 미측정**(파형 진폭 비보정) — "
          "Grossman & Taylor 2007; Hirsch & Bishop 1981. 표의 어떤 Δ 도 인과를 뜻하지 않는다.")
        a("")
    # ⑦ 문장
    a("## ⑦ Methods / Results 문장 초안")
    a("")
    a("**Methods (KR)** 호흡 신호에서 선형 디트렌드·이동 중앙값 기준선 제거 후 국소 최솟값(돌출 ≥ 1×MAD, "
      "최소 간격 %.1f s)을 흡기 시작으로 정해 호흡을 분할했다. RSA 는 호흡별 peak-valley 법(호기 창 최대 RR − 흡기 창 최소 RR; "
      "Grossman et al., 1990)으로 산출하고 호흡당 박동이 3개 미만인 호흡은 제외했다. RR 을 4 Hz 로 선형 보간해 Welch PSD(Hann, 50%% 겹침)를 구하고 "
      "LF 0.04–0.15 Hz, HF 0.15–0.40 Hz(Task Force, 1996)와 실측 평균 호흡주파수 ±%.2f Hz 의 호흡중심 대역 파워, "
      "호흡 파형과의 magnitude-squared coherence 를 계산했다. 호흡 깊이는 측정하지 않았다(Grossman & Taylor, 2007)."
      % (br.min_breath_s, half_width))
    a("")
    a("**Methods (EN)** Inspiration onsets were identified as local minima of the linearly detrended, baseline-corrected "
      "respiration signal (prominence ≥ 1 MAD, minimum interval %.1f s). RSA was quantified breath-by-breath with the peak-valley "
      "method (maximum RR during expiration minus minimum RR during inspiration; Grossman et al., 1990); breaths with fewer than three beats were excluded. "
      "RR series were linearly interpolated at 4 Hz and Welch PSDs (Hann, 50%% overlap) were computed for LF (0.04–0.15 Hz), fixed HF (0.15–0.40 Hz; Task Force, 1996), "
      "and a respiration-centred band (measured mean breathing frequency ±%.2f Hz), together with magnitude-squared coherence between respiration and RR. "
      "Tidal volume was not measured (Grossman & Taylor, 2007)." % (br.min_breath_s, half_width))
    a("")
    if comps:
        c = comps[0]
        d = {x.metric: x for x in c.deltas}
        a("**Results (KR)** 호흡수는 %s → %s 회/분, RSA_pv 중앙값은 %s → %s ms, HF 고정 파워는 %s → %s ms², "
          "호흡중심 대역 파워는 %s → %s ms² 였다. HF 변화는 호흡수 변화와 %s이었고, 호흡중심 대역으로 보면 결론이 %s했다."
          % (_f(d["호흡수(회/분)"].base, 1), _f(d["호흡수(회/분)"].stim, 1),
             _f(d["RSA_pv 중앙값(ms)"].base, 1), _f(d["RSA_pv 중앙값(ms)"].stim, 1),
             _f(d["HF 고정(ms²)"].base, 1), _f(d["HF 고정(ms²)"].stim, 1),
             _f(d["호흡중심(ms²)"].base, 1), _f(d["호흡중심(ms²)"].stim, 1),
             c.verdict_hf, c.verdict_band))
        a("")
        en_hf = {"동반됨": "accompanied by a change in breathing rate", "비동반": "not accompanied by a change in breathing rate"}.get(
            c.verdict_hf, "not classifiable")
        en_band = {"동일": "the same", "상이": "different"}.get(c.verdict_band, "not classifiable")
        a("**Results (EN)** Breathing rate changed from %s to %s breaths/min, median RSA_pv from %s to %s ms, fixed-HF power from %s to %s ms², "
          "and respiration-centred power from %s to %s ms². The change in fixed HF was %s; with the respiration-centred band the conclusion was %s."
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


def _guard_target(t: str) -> None:
    if os.path.islink(t):
        raise RsalinkError(
            f"{os.path.basename(t)} 이(가) 심볼릭링크입니다 — 링크를 따라가 다른 파일을 "
            "덮어쓰지 않도록 거부합니다. 링크를 지우거나 비어 있는 --out-dir 을 쓰세요")
    if os.path.exists(t) and not os.path.isdir(t) and os.stat(t).st_nlink > 1:
        raise RsalinkError(
            f"{os.path.basename(t)} 이(가) 하드링크(연결 수 {os.stat(t).st_nlink})입니다 — "
            "다른 이름으로 연결된 파일을 함께 덮어쓰지 않도록 거부합니다. "
            "파일을 지우거나 비어 있는 --out-dir 을 쓰세요")


def prepare_out_dir(out_dir: str) -> str:
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 은(는) 이미 있는 파일입니다 — 폴더 이름을 주세요")
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".rsalink_write_probe")
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
    except PermissionError:
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 에 쓰기 권한이 없습니다")
    except OSError as e:
        raise RsalinkError(f"--out-dir {os.path.basename(out_dir)} 을(를) 만들 수 없습니다: {e.strerror}")
    return out_dir


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
    _guard_target(path)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(buf.getvalue())


def write_artifacts(out_dir: str, report_md: str, rsa_rows: Sequence[BreathRSA], stats: Sequence[SegmentStats],
                    comps: Sequence[Comparison], pace_bpm: Optional[float], subject: str) -> List[str]:
    written = []
    p = os.path.join(out_dir, REPORT_MD)
    _guard_target(p)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(report_md)
    written.append(p)
    # 호흡별.csv — 시각은 호흡 시계 기준(초)
    seg_of = []
    for r in rsa_rows:
        lab = ""
        for st in stats:
            if st.seg.t0 <= r.breath.t_onset < st.seg.t1:
                lab = st.seg.label
                break
        seg_of.append(lab)
    rows = []
    for r, lab in zip(rsa_rows, seg_of):
        b = r.breath
        rows.append([subject, b.idx, lab, round(b.t_onset, 3), round(b.t_peak, 3), round(b.t_end, 3),
                     round(b.period_s, 3), round(b.rate_bpm, 3) if b.valid else None, b.flag or "",
                     r.n_beats, r.n_insp, r.n_exp, r.rr_min_insp, r.rr_max_exp, r.rsa_pv, r.reason or ""])
    _write_csv(os.path.join(out_dir, BREATH_CSV),
               ["subject", "breath_idx", "segment", "t_onset_s", "t_peak_s", "t_end_s", "period_s", "rate_bpm",
                "flag", "n_beats", "n_insp", "n_exp", "rr_min_insp_ms", "rr_max_exp_ms", "rsa_pv_ms", "rsa_reason"],
               rows)
    written.append(os.path.join(out_dir, BREATH_CSV))
    # 조건비교.csv — 한 피험자 = 구간당 한 행 (statwise/longistat 투입용 long 형식)
    rows = []
    for st in stats:
        b = st.band
        rows.append([subject, st.seg.label, round(st.seg.t0, 1), round(st.seg.t1, 1), round(st.seg.duration_s, 1),
                     st.n_breaths, st.n_valid, st.rate["mean"], st.rate["sd"], st.rsa["median"], st.rsa["q1"],
                     st.rsa["q3"], st.rsa["n"], st.rsa["n_undetermined"], st.rsa["beats_per_breath"],
                     b.lf if b else None, b.hf_fixed if b else None, b.resp_centered if b else None,
                     b.resp_band[0] if b else None, b.resp_band[1] if b else None,
                     (b.coherence_at_resp if b and b.coherence_at_resp is not None else None),
                     b.n_segments if b else None, st.mean_rr,
                     st.pace.pct_within if st.pace else None,
                     (st.pace.first_entrain_t if st.pace and st.pace.first_entrain_t is not None else None),
                     st.pace.sd_entrained if st.pace else None,
                     int(st.short_flag), int(st.slow_flag)])
    _write_csv(os.path.join(out_dir, COMPARE_CSV),
               ["subject", "segment", "t0_s", "t1_s", "duration_s", "n_breaths", "n_valid", "resp_rate_mean",
                "resp_rate_sd", "rsa_pv_median_ms", "rsa_pv_q1_ms", "rsa_pv_q3_ms", "rsa_n", "rsa_undetermined",
                "beats_per_breath", "lf_ms2", "hf_fixed_ms2", "resp_centered_ms2", "resp_band_lo_hz",
                "resp_band_hi_hz", "coherence", "welch_segments", "mean_rr_ms", "pace_pct_within",
                "pace_first_entrain_s", "pace_sd_entrained", "flag_short", "flag_slow_breath"],
               rows)
    written.append(os.path.join(out_dir, COMPARE_CSV))
    return written
