"""파일 하나를 **한 번만** 훑어 모든 지표를 뽑는 스트리밍 분석기.

설계 원칙
---------
* 파일 전체를 메모리에 올리지 않습니다. 블록(기본 64 k 프레임)마다 누적기만
  갱신하고, 남기는 것은 ① 10 ms 프레임 요약(제곱합 3종 + 피크), ② 트루피크
  후보 창 상위 64개, ③ 머리/꼬리 2초 샘플, ④ 스펙트럼 세그먼트 제한 개수뿐입니다.
  메모리는 파일 길이에 선형이지만 계수가 아주 작습니다(200초 스테레오 ≈ 수 MB).
* 10 ms 를 모든 프레임 계산의 공통 단위로 씁니다. 44.1 kHz → 441 샘플,
  48 kHz → 480 샘플로 정확히 나누어떨어져 경계 오차가 없습니다.
* K-가중(LUFS)·A-가중(LAeq)·무가중(위생 지표)을 같은 패스에서 동시에 굽습니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import levels
from .dsp import hann, parabolic_peak, rfft_mag2
from .filters import (MIN_A_WEIGHTING_FS, a_weighting_sos,
                      envelope_lowpass_sos, k_weighting_sos)
from .dsp import new_states, primed_states, sos_block
from .wavread import WavError, WavInfo, iter_blocks

#: 클리핑 판정 문턱 — −0.1 dBFS.
CLIP_THRESHOLD = 10.0 ** (-0.1 / 20.0)
#: 클리핑 '구간'으로 인정하는 최소 연속 샘플 수.
CLIP_MIN_RUN = 3
#: 무음 프레임 판정 문턱 (10 ms RMS, dBFS).
SILENCE_RMS_DBFS = -60.0
#: DC 오프셋 경고 문턱 (dBFS).
DC_WARN_DBFS = -60.0
#: 머리/꼬리에서 샘플 단위로 보존하는 길이 (초) — 상승/하강 시간 측정용.
EDGE_SECONDS = 2.0
#: 스펙트럼 평균에 쓰는 세그먼트 개수 상한 (순수 파이썬 FFT 비용 통제).
MAX_SPECTRUM_SEGMENTS = 24
#: 스펙트럼 FFT 길이.
SPECTRUM_NFFT = 16384

#: 반송주파수 후보의 가청 하한 (Hz). 이보다 낮은 스펙트럼 최대 빈은 소리의
#: 음높이가 아니라 스펙트럼 기울기의 끝이므로 반송음으로 보지 않습니다.
MIN_CARRIER_HZ = 20.0
#: 두드러짐(prominence)을 재는 국소 이웃의 폭 — 피크의 ±1 옥타브.
PROMINENCE_OCTAVES = 1.0
#: 피크 자신의 스커트(창 함수의 主lobe)를 이웃에서 빼는 폭 (빈 개수).
PROMINENCE_SKIRT_BINS = 4


@dataclass
class ClipRun:
    channel: int
    start_s: float
    length_samples: int


@dataclass
class FileMetrics:
    """한 파일의 전 지표. 판정(치명/경고)은 여기 없습니다 — `setcheck` 소관."""

    info: WavInfo
    frame_len: int
    n_frames_10ms: int

    lufs_i: Optional[float] = None
    lra: Optional[float] = None
    gated_blocks: int = 0
    total_blocks: int = 0

    laeq_dbfs: Optional[float] = None
    lamax_dbfs: Optional[float] = None
    dynamic_range_db: Optional[float] = None

    sample_peak_dbfs: Optional[float] = None
    true_peak_dbfs: Optional[float] = None
    rms_dbfs: List[Optional[float]] = field(default_factory=list)
    dc_dbfs: List[Optional[float]] = field(default_factory=list)
    dc_linear: List[float] = field(default_factory=list)

    clip_runs: List[ClipRun] = field(default_factory=list)
    clip_run_count: int = 0
    clip_sample_count: int = 0

    lead_silence_ms: float = 0.0
    tail_silence_ms: float = 0.0
    onset_rise_ms: Optional[float] = None
    offset_fall_ms: Optional[float] = None
    edge_window_ms: float = EDGE_SECONDS * 1000.0

    lr_rms_diff_db: Optional[float] = None
    dead_reason: Optional[str] = None

    spectral_peak_hz: List[Optional[float]] = field(default_factory=list)
    #: 피크가 배경 스펙트럼보다 몇 dB 솟았는가. 낮으면 '반송음'이라 부를 수 없습니다
    #: (핑크노이즈의 최대 빈은 그냥 1/f 기울기의 끝이지 반송음이 아닙니다).
    spectral_peak_prominence_db: List[Optional[float]] = field(default_factory=list)
    spectral_centroid_hz: Optional[float] = None
    env_mod_hz: Optional[float] = None
    env_mod_ratio: Optional[float] = None
    #: 포락선의 AC RMS / 평균 — "얼마나 깊게" 변조되었는가.
    env_mod_depth: Optional[float] = None
    env_rate_hz: float = 100.0

    analysis_seconds: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.info.duration_s

    @property
    def name(self) -> str:
        import os
        return os.path.basename(self.info.path)


class _TruePeakTracker:
    """트루피크를 스트리밍 중에 **즉석에서** 보간해 최댓값만 들고 갑니다.

    처음에는 후보 창을 최소힙에 모았다가 마지막에 보간했는데, 클리핑으로
    표본이 정확히 만점(1.0)에 붙은 파일에서는 동점 후보가 수천 개 나와
    힙이 먼저 들어온 것들로 가득 차고, 실제로 가장 크게 튀는 지점이 버려졌습니다
    (실물 S1_SO-CLAS.wav 에서 ffmpeg 대비 0.9 dB 과소평가). 그래서 보류하지 않고
    바로 계산합니다.

    비용 통제: 현재까지의 최대 표본값 대비 12 dB 이내인 프레임만 보간합니다.
    그보다 조용한 프레임은 트루피크 최댓값을 가질 수 없습니다.
    """

    RELATIVE_GATE = 0.25  # −12 dB
    #: 완전히 뭉개진 파일에서 보간이 폭주하지 않도록 두는 총 예산. 예산이 떨어져도
    #: 프레임 최댓점 보간은 계속되며, 그런 파일은 이미 클리핑으로 치명 판정을
    #: 받으므로 트루피크의 소수점은 결론을 바꾸지 않습니다.
    EXTRA_BUDGET = 50000

    def __init__(self) -> None:
        self.sample_max = 0.0
        self.true_max = 0.0
        self.extra_budget = self.EXTRA_BUDGET

    def feed(self, value: float, window: List[float]) -> None:
        if value > self.sample_max:
            self.sample_max = value
        if value <= 0.0 or value < self.RELATIVE_GATE * self.sample_max:
            return
        tp = levels.interpolated_peak(window)
        if tp > self.true_max:
            self.true_max = tp

    def feed_extra(self, value: float, window: List[float]) -> None:
        """클리핑 평탄부의 동점 표본용 — 예산 안에서만 추가 보간합니다."""
        if self.extra_budget <= 0:
            return
        self.extra_budget -= 1
        self.feed(value, window)

    def result(self) -> Optional[float]:
        best = max(self.true_max, self.sample_max)
        return best if best > 0 else None


def analyze_file(info: WavInfo, block_frames: int = 65536) -> FileMetrics:
    """한 파일을 스트리밍으로 훑어 `FileMetrics` 를 만듭니다."""
    import time

    t0 = time.monotonic()
    fs = info.sample_rate
    nch = info.n_channels
    frame_len = max(1, int(round(fs * 0.01)))

    ksos = k_weighting_sos(fs)
    asos = a_weighting_sos(fs)
    esos = envelope_lowpass_sos(fs)
    kstates = [new_states(ksos) for _ in range(nch)]
    astates = [new_states(asos) for _ in range(nch)]
    estates: List[Optional[List[List[float]]]] = [None] * nch

    sq_raw: List[List[float]] = [[] for _ in range(nch)]
    #: 반송음의 2배 성분이 포락선 대역으로 접히지 않도록 저역통과한 전력.
    sq_env: List[List[float]] = [[] for _ in range(nch)]
    pend_env: List[List[float]] = [[] for _ in range(nch)]
    sq_k: List[List[float]] = [[] for _ in range(nch)]
    sq_a: List[List[float]] = [[] for _ in range(nch)]
    pend_raw: List[List[float]] = [[] for _ in range(nch)]
    pend_k: List[List[float]] = [[] for _ in range(nch)]
    pend_a: List[List[float]] = [[] for _ in range(nch)]

    dc_sum = [0.0] * nch
    sq_sum = [0.0] * nch
    peak = [0.0] * nch
    n_samples = 0

    clip_len = [0] * nch
    clip_start = [0] * nch
    clip_runs: List[ClipRun] = []
    clip_run_count = 0
    clip_sample_count = 0

    half = levels._TAP_HALF
    tp = [_TruePeakTracker() for _ in range(nch)]
    tp_carry: List[List[float]] = [[0.0] * (2 * half) for _ in range(nch)]

    edge_n = int(round(EDGE_SECONDS * fs))
    head: List[List[float]] = [[] for _ in range(nch)]
    tail: List[List[float]] = [[] for _ in range(nch)]

    seg_starts = _segment_starts(info.n_frames, SPECTRUM_NFFT, MAX_SPECTRUM_SEGMENTS)
    seg_next = 0
    active: List[Tuple[List[List[float]], int]] = []
    spec = _SpectrumAccumulator(nch, fs)

    global_pos = 0
    for block in iter_blocks(info, block_frames):
        blen = len(block[0])
        for c in range(nch):
            x = block[c]
            # --- 무가중 통계
            sqs = [v * v for v in x]
            sq_sum[c] += sum(sqs)
            dc_sum[c] += sum(x)
            # 32비트 float WAV 는 NaN/Inf 를 담을 수 있습니다. 그대로 두면
            # 라우드니스가 NaN → `lufs_i = None` 이 되어 **조건 간 판정이 조용히
            # 꺼지고**(치명 1건짜리 세트가 exit 0 으로 통과), 전 구간 NaN 인
            # 파일은 "전 구간 무음"이라는 **틀린 진단**을 받습니다. 잴 수 없는
            # 파일은 읽지 못한 것으로 세는 편이 정직합니다 → 종료코드 3.
            # (적대적 검토 라운드 1, 엣지케이스 파괴자 발견 2)
            if not (math.isfinite(sq_sum[c]) and math.isfinite(dc_sum[c])):
                raise WavError(
                    "유한하지 않은 표본(NaN/Inf)이 들어 있음 — {:.2f}초 이후 블록 "
                    "채널 {} · 이 파일의 측정값은 신뢰할 수 없습니다"
                    .format(global_pos / float(fs), c + 1))
            bp = max((abs(v) for v in x), default=0.0)
            if bp > peak[c]:
                peak[c] = bp
            # --- 클리핑 구간
            cl, cs, runs, cnt, tot = _scan_clipping(
                x, global_pos, fs, c, clip_len[c], clip_start[c])
            clip_len[c], clip_start[c] = cl, cs
            if runs:
                clip_runs.extend(runs[: max(0, 40 - len(clip_runs))])
            clip_run_count += cnt
            clip_sample_count += tot
            # --- 트루피크 후보
            _scan_true_peak(tp[c], tp_carry[c], x, half, frame_len)
            # --- 머리/꼬리 샘플 보존
            if len(head[c]) < edge_n:
                head[c].extend(x[: edge_n - len(head[c])])
            tail[c].extend(x)
            if len(tail[c]) > edge_n:
                del tail[c][: len(tail[c]) - edge_n]
            # --- 가중 필터
            kx = sos_block(x, ksos, kstates[c])
            ax = sos_block(x, asos, astates[c])
            if estates[c] is None:
                # DC 를 통과시키는 필터라 0 초기화의 과도응답이 그대로 포락선
                # 앞머리의 가짜 저주파가 됩니다. 정상상태 값으로 프라이밍하되,
                # **첫 표본이 아니라 첫 10 ms 의 평균 전력**을 씁니다 — 사인파는
                # 0 에서 시작하므로 첫 표본으로 잡으면 여전히 큰 과도응답이 남고,
                # 그것만으로 순수 톤의 변조 깊이가 0.014 % → 0.79 % 로 부풀어
                # 깊이 문턱을 넘어 버립니다(라운드 1 검토 중 실측).
                prime = sum(sqs[:frame_len]) / frame_len if sqs else 0.0
                estates[c] = primed_states(esos, prime)
            ex = sos_block(sqs, esos, estates[c])
            _accumulate_env(ex, pend_env[c], sq_env[c], frame_len)
            _accumulate_frames(sqs, [v * v for v in kx], [v * v for v in ax],
                               pend_raw[c], pend_k[c], pend_a[c],
                               sq_raw[c], sq_k[c], sq_a[c], frame_len)
        # --- 스펙트럼 세그먼트 캡처 (0번 채널 기준으로 위치를 잡고 전 채널 수집)
        seg_next, active = _capture_segments(
            block, global_pos, blen, seg_starts, seg_next, active, spec, nch)
        n_samples += blen
        global_pos += blen

    # 후처리: 남은 자투리 프레임은 버립니다(BS.1770 과 동일한 처리).
    for c in range(nch):
        _flush_true_peak(tp[c], tp_carry[c], half, frame_len)

    # 파일 끝에서 아직 열려 있는 클리핑 구간을 닫습니다. 이걸 빠뜨리면
    # **파일 전체가 클리핑된 자극**(마지막 샘플까지 만점에 붙어 있는 렌더)이
    # 클리핑 0건으로 통과합니다 — 치명 판정 넷 중 하나가 정확히 그 병리에서
    # 침묵하는 셈입니다. (적대적 검토 라운드 1, 정확성 감사 발견 1)
    for c in range(nch):
        if clip_len[c] >= CLIP_MIN_RUN:
            if len(clip_runs) < 40:
                clip_runs.append(ClipRun(c, clip_start[c] / float(fs), clip_len[c]))
            clip_run_count += 1
            clip_sample_count += clip_len[c]

    m = FileMetrics(info=info, frame_len=frame_len,
                    n_frames_10ms=len(sq_raw[0]) if sq_raw else 0)

    if n_samples == 0:
        m.dead_reason = "오디오 샘플이 0개"
        m.analysis_seconds = time.monotonic() - t0
        return m

    m.rms_dbfs = [levels.dbfs(math.sqrt(sq_sum[c] / n_samples)) for c in range(nch)]
    m.dc_linear = [dc_sum[c] / n_samples for c in range(nch)]
    m.dc_dbfs = [levels.dbfs(abs(v)) for v in m.dc_linear]
    m.sample_peak_dbfs = levels.dbfs(max(peak))
    m.true_peak_dbfs = levels.dbfs(max((tp[c].result() or 0.0) for c in range(nch)))
    m.clip_runs = clip_runs
    m.clip_run_count = clip_run_count
    m.clip_sample_count = clip_sample_count

    if sq_raw[0]:
        m.lufs_i, m.gated_blocks, m.total_blocks = levels.integrated_lufs(sq_k, frame_len, fs)
        m.lra = levels.loudness_range(sq_k, frame_len, fs)
        # 낮은 샘플레이트에서는 1 kHz 재정규화가 폭주해 LAeq 가 300 dB 로
        # 인쇄됩니다 — 잴 수 없으면 값을 지어내지 않고 비워 둡니다.
        if fs >= MIN_A_WEIGHTING_FS:
            m.laeq_dbfs, m.lamax_dbfs, m.dynamic_range_db = levels.a_weighted_levels(
                sq_a, frame_len, fs)

    if nch == 2 and m.rms_dbfs[0] is not None and m.rms_dbfs[1] is not None:
        m.lr_rms_diff_db = m.rms_dbfs[0] - m.rms_dbfs[1]

    m.lead_silence_ms, m.tail_silence_ms = _silence_edges(sq_raw, frame_len, nch)
    m.onset_rise_ms = _rise_time_ms(head, max(peak), fs, reverse=False)
    m.offset_fall_ms = _rise_time_ms(tail, max(peak), fs, reverse=True)
    m.dead_reason = _dead_reason(m, n_samples)

    m.spectral_peak_hz, m.spectral_peak_prominence_db, m.spectral_centroid_hz = spec.result()
    env = _envelope(sq_env, frame_len, nch)
    m.env_rate_hz = fs / float(frame_len)
    m.env_mod_hz, m.env_mod_ratio, m.env_mod_depth = _envelope_mod(env, m.env_rate_hz)

    m.analysis_seconds = time.monotonic() - t0
    return m


# ------------------------------------------------------------------ 내부


def _accumulate_frames(sq, sqk, sqa, pend_r, pend_k, pend_a,
                       out_r, out_k, out_a, frame_len) -> None:
    """블록의 제곱열(무가중·K가중·A가중)을 10 ms 프레임 합으로 접습니다.

    경계에 걸친 자투리는 `pend` 로 이월되므로 블록 크기를 바꿔도 결과가 같습니다.
    """
    pend_r.extend(sq)
    pend_k.extend(sqk)
    pend_a.extend(sqa)
    n = len(pend_r) // frame_len
    if n == 0:
        return
    for i in range(n):
        s = i * frame_len
        e = s + frame_len
        out_r.append(sum(pend_r[s:e]))
        out_k.append(sum(pend_k[s:e]))
        out_a.append(sum(pend_a[s:e]))
    used = n * frame_len
    del pend_r[:used]
    del pend_k[:used]
    del pend_a[:used]


def _accumulate_env(values, pend, out, frame_len) -> None:
    """저역통과된 전력을 10 ms 프레임 합으로 접습니다(경계는 pend 로 이월)."""
    pend.extend(values)
    n = len(pend) // frame_len
    if n == 0:
        return
    for i in range(n):
        s = i * frame_len
        out.append(sum(pend[s:s + frame_len]))
    del pend[:n * frame_len]


def _scan_clipping(x: Sequence[float], global_pos: int, fs: int, ch: int,
                   run_len: int, run_start: int):
    """연속 CLIP_MIN_RUN 샘플 이상 |x| ≥ −0.1 dBFS 인 구간을 셉니다."""
    runs: List[ClipRun] = []
    count = 0
    total = 0
    thr = CLIP_THRESHOLD
    for i, v in enumerate(x):
        if v >= thr or v <= -thr:
            if run_len == 0:
                run_start = global_pos + i
            run_len += 1
        else:
            if run_len >= CLIP_MIN_RUN:
                runs.append(ClipRun(ch, run_start / float(fs), run_len))
                count += 1
                total += run_len
            run_len = 0
    return run_len, run_start, runs, count, total


def _scan_true_peak(tracker: _TruePeakTracker, carry: List[float],
                    x: Sequence[float], half: int, chunk: int) -> None:
    """`chunk` 샘플(=10 ms)마다 최댓점을 찾아 그 자리에서 보간합니다.

    프레임 최댓점 **하나만** 보는 방식은 클리핑 구간에서 틀립니다: 같은 프레임에
    만점 표본이 여러 개면 `max` 는 첫 번째를 고르는데, 표본 사이 오버슈트가 가장
    큰 곳은 다른 표본 옆일 수 있습니다(실물 S1_SO-CLAS.wav 에서 82486 을 고르는
    바람에 82575 의 +1.54 dBTP 를 놓치고 +0.66 을 보고했습니다).
    그래서 클리핑 문턱(−0.1 dBFS)을 넘는 표본은 **전부** 추가로 보간합니다.
    클리핑이 없는 파일에서는 추가 비용이 0 이고, 전 구간이 뭉개진 병적인 파일은
    `_TruePeakTracker.EXTRA_BUDGET` 이 총량을 막습니다.
    """
    ext = carry + list(x)
    n = len(ext)
    start = half
    limit = n - half
    thr = CLIP_THRESHOLD
    while start < limit:
        end = min(start + chunk, limit)
        bi, bv = start, -1.0
        extra = []
        for i in range(start, end):
            v = ext[i]
            a = v if v >= 0 else -v
            if a > bv:
                bv, bi = a, i
            if a >= thr:
                extra.append(i)
        tracker.feed(bv, ext[bi - half:bi + half])
        for i in extra:
            if i != bi:
                tracker.feed_extra(abs(ext[i]), ext[i - half:i + half])
        start = end
    keep = 2 * half
    del carry[:]
    carry.extend(ext[-keep:] if n >= keep else ([0.0] * (keep - n)) + ext)


def _flush_true_peak(tracker: _TruePeakTracker, carry: List[float], half: int, chunk: int) -> None:
    """마지막 half 샘플도 후보에 들어가도록 0 패딩으로 한 번 더 훑습니다."""
    _scan_true_peak(tracker, carry, [0.0] * half, half, chunk)


def _segment_starts(n_frames: int, nfft: int, max_segments: int) -> List[int]:
    """스펙트럼 세그먼트 시작 위치를 파일 전체에 고르게 배치합니다."""
    if n_frames < nfft:
        return [0] if n_frames >= 64 else []
    span = n_frames - nfft
    k = min(max_segments, max(1, span // (nfft // 2) + 1))
    if k == 1:
        return [span // 2]
    return [int(round(i * span / (k - 1))) for i in range(k)]


def _capture_segments(block, global_pos, blen, seg_starts, seg_next, active, spec, nch):
    """블록을 지나가면서 예약된 세그먼트를 채웁니다(블록 경계를 걸쳐도 됩니다).

    세그먼트가 다 차는 즉시 FFT 해서 누적기에 더하고 버퍼는 버립니다 —
    24개를 다 들고 있으면 200초 스테레오에서만 25 MB 가 놀게 됩니다.
    """
    while seg_next < len(seg_starts) and seg_starts[seg_next] < global_pos + blen:
        off = seg_starts[seg_next] - global_pos
        active.append(([[] for _ in range(nch)], max(0, off)))
        seg_next += 1
    still: List[Tuple[List[List[float]], int]] = []
    for buf, offset in active:
        need = SPECTRUM_NFFT - len(buf[0])
        take = min(need, blen - offset)
        if take > 0:
            for c in range(nch):
                buf[c].extend(block[c][offset:offset + take])
        if len(buf[0]) >= SPECTRUM_NFFT:
            spec.add(buf)
        else:
            still.append((buf, 0))
    return seg_next, still


class _SpectrumAccumulator:
    """세그먼트 평균 파워스펙트럼 → 채널별 최대 피크 + 스펙트럼 중심."""

    def __init__(self, nch: int, fs: int) -> None:
        self.nch = nch
        self.fs = fs
        self.win = hann(SPECTRUM_NFFT)
        self.acc = [[0.0] * (SPECTRUM_NFFT // 2 + 1) for _ in range(nch)]
        self.count = 0

    def add(self, buf: Sequence[Sequence[float]]) -> None:
        win = self.win
        for c in range(self.nch):
            seg = buf[c]
            if len(seg) < SPECTRUM_NFFT:
                continue
            mag2 = rfft_mag2([seg[i] * win[i] for i in range(SPECTRUM_NFFT)])
            row = self.acc[c]
            for k, v in enumerate(mag2):
                row[k] += v
        self.count += 1

    def result(self):
        if self.count == 0:
            return [None] * self.nch, [None] * self.nch, None
        df = self.fs / float(SPECTRUM_NFFT)
        peaks: List[Optional[float]] = []
        proms: List[Optional[float]] = []
        cent_num = 0.0
        cent_den = 0.0
        # 가청 하한 아래는 반송주파수 후보가 아닙니다. 이 하한이 없으면 1/f
        # 잡음의 최대 빈(≈ 5 Hz, 그냥 기울기의 끝)이 '반송음'으로 뽑힙니다.
        k_lo = max(2, int(math.ceil(MIN_CARRIER_HZ / df)))
        for c in range(self.nch):
            row = self.acc[c]
            # 최저 2빈(DC 포함)은 제외 — DC 오프셋이 '반송주파수'로 잡히면 안 됩니다.
            best, bestv = None, 0.0
            for k in range(k_lo, len(row)):
                if row[k] > bestv:
                    bestv, best = row[k], k
            if best is None or bestv <= 0:
                peaks.append(None)
                proms.append(None)
            else:
                peaks.append(parabolic_peak(row, best) * df)
                proms.append(_local_prominence_db(row, best))
            for k in range(1, len(row)):
                cent_num += row[k] * (k * df)
                cent_den += row[k]
        return peaks, proms, (cent_num / cent_den if cent_den > 0 else None)


def _local_prominence_db(row: Sequence[float], best: int) -> Optional[float]:
    """피크가 **자기 이웃보다** 얼마나 솟아 있는지 (dB).

    왜 전역 중앙값이 아니라 국소 이웃인가
    ------------------------------------
    처음 구현은 `10·log10(최대빈 / 전체빈 중앙값)` 이었습니다. 그런데 1/f
    (핑크) 잡음은 최대 빈이 맨 아래, 중앙값 빈이 스펙트럼 한가운데에 있어
    이 값이 사실상 **스펙트럼 기울기**가 됩니다 — 핑크노이즈가 32 dB 로
    "뚜렷한 반송음 있음" 판정을 받았습니다(적대적 검토 라운드 1, 정확성 감사
    발견 2). 대조군이 핑크노이즈인 실험에서 이건 그냥 오작동이 아니라
    **대조군에 가짜 반송주파수를 붙이는** 것입니다.

    그래서 피크의 ±1 옥타브 이웃(피크 자신의 스커트는 제외)의 중앙값과
    비교합니다. 순수 톤은 이웃이 잡음 바닥이라 크게 나오고, 매끄러운 1/f
    스펙트럼은 이웃도 비슷하게 높아 작게 나옵니다.
    """
    bestv = row[best]
    if bestv <= 0:
        return None
    lo = max(2, int(math.floor(best / (2.0 ** PROMINENCE_OCTAVES))))
    hi = min(len(row) - 1, int(math.ceil(best * (2.0 ** PROMINENCE_OCTAVES))))
    body = [row[k] for k in range(lo, hi + 1)
            if abs(k - best) > PROMINENCE_SKIRT_BINS]
    if not body:
        return None
    body.sort()
    med = body[len(body) // 2]
    if med <= 0:
        return None
    return 10.0 * math.log10(bestv / med)


def _envelope(sq_env, frame_len: int, nch: int) -> List[float]:
    """10 ms 프레임 RMS 포락선 (전 채널 에너지 평균).

    입력은 **저역통과된** 전력이어야 합니다(`envelope_lowpass_sos`) — 원신호
    전력을 그대로 쓰면 반송주파수의 2배 성분이 데시메이션에서 접혀 들어옵니다.
    저역통과 뒤 값은 음수가 될 수 있으므로(필터 링잉) 0 으로 자릅니다.
    """
    if not sq_env or not sq_env[0]:
        return []
    n = len(sq_env[0])
    out = []
    for i in range(n):
        acc = 0.0
        for c in range(nch):
            acc += sq_env[c][i]
        out.append(math.sqrt(max(0.0, acc / (nch * frame_len))))
    return out


#: 포락선에서 변조로 인정하는 최저 주기 수 — 파일 안에 이만큼은 들어가야 합니다.
MIN_ENVELOPE_CYCLES = 1.5
#: 변조로 인정하는 최소 깊이 (포락선의 AC RMS / 평균).
#: 변조가 전혀 없는 순수 톤은 저역통과 뒤에도 잔류 리플이 남는데(반송음 2배
#: 성분의 접힘), 그 깊이는 0.1 % 수준입니다. 실제 진폭변조는 수십 %입니다.
#: 이 문턱이 없으면 잔류 리플이 "유일한 성분"이라는 이유만으로 상대강도 0.7 을
#: 받아 순수 톤이 "20 Hz 로 변조됨"으로 보고됩니다(라운드 1 검토에서 발견).
MIN_ENVELOPE_DEPTH = 0.005
#: 포락선 분석 대역의 상한(Hz). 10 ms 프레임(100 Hz)의 나이퀴스트가 50 Hz 이고
#: 앞단 저역통과가 40 Hz 이므로 20 Hz 위는 신뢰할 수 없습니다.
ENVELOPE_BAND_HI_HZ = 20.0


def _envelope_mod(env: Sequence[float], rate_hz: float):
    """포락선의 지배적 변조 주파수(Hz) · 상대 강도 · 변조 깊이.

    포락선에서 평균을 뺀 뒤 FFT 하고, 하한 ~ 20 Hz 대역의 최대 피크를 봅니다.
    상대 강도 = 피크 파워 / 대역 전체 파워 (1 에 가까울수록 순수한 주기 변조).

    하한이 왜 0.02 Hz 고정이 아닌가: 파일 양끝의 페이드인/아웃 자체가 파일 길이만큼
    긴 '초저주파 변조'로 보입니다. 8초짜리 정상 톤(20 ms 페이드)에서 0.014 Hz 가
    상대강도 0.96 으로 잡혀 "주기적으로 변조된 자극"으로 오인됐습니다. 그래서 하한을
    `MIN_ENVELOPE_CYCLES / 길이` 로 두어 페이드 모양을 대역 밖으로 밀어냅니다.
    """
    n = len(env)
    if n < 64:
        return None, None, None
    mean = sum(env) / n
    x = [v - mean for v in env]
    depth = (math.sqrt(sum(v * v for v in x) / n) / mean) if mean > 0 else 0.0
    if depth < MIN_ENVELOPE_DEPTH:
        # 사실상 평평한 포락선입니다 — 어떤 주파수를 지목해도 잔류 리플입니다.
        return None, None, depth
    from .dsp import next_pow2
    nfft = next_pow2(n)
    win = hann(n)
    padded = [x[i] * win[i] for i in range(n)] + [0.0] * (nfft - n)
    mag2 = rfft_mag2(padded)
    df = rate_hz / nfft
    duration_s = n / rate_hz
    lo_hz = max(0.02, MIN_ENVELOPE_CYCLES / duration_s)
    lo = max(2, int(math.ceil(lo_hz / df)))
    hi = min(len(mag2) - 1, int(math.floor(ENVELOPE_BAND_HI_HZ / df)))
    if hi <= lo:
        return None, None, depth
    best, bestv = None, 0.0
    total = 0.0
    for k in range(lo, hi + 1):
        total += mag2[k]
        if mag2[k] > bestv:
            bestv, best = mag2[k], k
    if best is None or bestv <= 0 or total <= 0:
        return None, None, depth
    if best >= hi:
        # 피크가 대역 맨 끝에 붙어 있으면 진짜 최댓값은 대역 밖입니다.
        # 40 Hz AM 을 "19.999 Hz" 로 자신 있게 보고하던 결함(라운드 1)을 막습니다.
        return None, None, depth
    # 포물선 보간이 대역 밖으로 미끄러지지 않게 하한에서 반 빈까지만 허용합니다.
    est = max(parabolic_peak(mag2, best), lo - 0.5) * df
    return est, bestv / total, depth


def _silence_edges(sq_raw, frame_len: int, nch: int):
    """앞/뒤 무음 길이 (ms). 모든 채널이 무음인 프레임만 무음으로 셉니다."""
    if not sq_raw or not sq_raw[0]:
        return 0.0, 0.0
    n = len(sq_raw[0])
    thr = (10.0 ** (SILENCE_RMS_DBFS / 20.0)) ** 2 * frame_len
    lead = 0
    for i in range(n):
        if any(sq_raw[c][i] > thr for c in range(nch)):
            break
        lead += 1
    if lead == n:
        return n * 10.0, n * 10.0
    tail = 0
    for i in range(n - 1, -1, -1):
        if any(sq_raw[c][i] > thr for c in range(nch)):
            break
        tail += 1
    return lead * 10.0, tail * 10.0


def _rise_time_ms(edge: Sequence[Sequence[float]], file_peak: float, fs: int,
                  reverse: bool) -> Optional[float]:
    """상승(하강) 시간 = 진폭이 파일 피크의 1 % → 50 % 에 이르기까지의 시간(ms).

    onset dynamics 를 값으로 보고하기 위한 측정이며, **판정하지 않습니다**
    (논문 수치는 reference value 이지 임계값이 아니기 때문 — `refs.py` 참조).
    측정창은 머리/꼬리 EDGE_SECONDS 초입니다. 창 안에서 50 % 에 도달하지 못하면
    None 을 돌려주고 리포트는 "측정창 밖" 이라고 적습니다.
    """
    if file_peak <= 0 or not edge or not edge[0]:
        return None
    n = len(edge[0])
    nch = len(edge)
    lo = 0.01 * file_peak
    hi = 0.50 * file_peak
    order = range(n - 1, -1, -1) if reverse else range(n)
    start = None
    for i in order:
        a = max(abs(edge[c][i]) for c in range(nch))
        if a >= lo and start is None:
            start = i
        if start is not None and a >= hi:
            return abs(i - start) / float(fs) * 1000.0
    return None


def _dead_reason(m: FileMetrics, n_samples: int) -> Optional[str]:
    """전부 무음 / 전부 DC 인 '죽은 파일' 판정."""
    if m.sample_peak_dbfs is None:
        return "전 구간 무음 (샘플 피크 0)"
    # 파일 길이가 아니라 **분석한 구간**과 비교합니다. `lead_silence_ms` 는 완전한
    # 10 ms 프레임만 세므로, 길이가 10 ms 의 배수가 아닌 파일(DAW 렌더는 대개
    # 그렇습니다)에서는 파일 길이에 영원히 도달하지 못합니다 — 3.005초짜리
    # 완전 무음 파일이 "치명 0건 · 세트로 써도 됩니다"로 통과했습니다.
    analysed_ms = m.n_frames_10ms * 10.0
    if m.n_frames_10ms > 0 and m.lead_silence_ms >= analysed_ms - 1e-6:
        return "전 구간 무음 (10 ms RMS 가 모두 −60 dBFS 미만)"
    ac_rms = []
    for c in range(len(m.rms_dbfs)):
        rms_lin = 10.0 ** ((m.rms_dbfs[c] or -400.0) / 20.0)
        dc = abs(m.dc_linear[c])
        ac = max(0.0, rms_lin * rms_lin - dc * dc) ** 0.5
        ac_rms.append(ac)
    if ac_rms and max(ac_rms) < 10.0 ** (-80.0 / 20.0) and max(abs(v) for v in m.dc_linear) > 1e-4:
        return "전 구간 DC (교류 성분이 −80 dBFS 미만)"
    return None
