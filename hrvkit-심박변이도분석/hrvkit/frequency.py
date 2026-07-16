"""주파수영역 HRV — 대역별 파워(VLF/LF/HF), LF/HF, 정규화 단위.

RR 시계열은 불균등 표본이므로 다음 순서로 처리합니다 (모두 순수 파이썬):
  1) 박동 시각 t_i = 누적 NN(초)을 만들고 tachogram (t_i, NN_i)을 구성.
  2) fs(기본 4 Hz) 균등 격자로 선형보간(re-sampling).
  3) Welch 방식 주기도로 PSD를 추정 — 신호를 겹치는(50 %) 구간으로 나눠
     각 구간을 Hann 창으로 곱하고, **직접 구현한 radix-2 Cooley–Tukey FFT**
     (2의 거듭제곱으로 zero-pad)로 스펙트럼을 구한 뒤 구간별 주기도를 평균.
  4) 표준 단측 PSD 밀도(ms²/Hz)로 대역별 파워를 적분.

정규화(scipy.signal.welch 의 scaling='density' 와 동일):
  P_k = |X_k|² / (fs · Σ w_i²),  DC/Nyquist가 아닌 빈은 단측화를 위해 ×2.
  이렇게 하면 Σ P_k · df ≈ 신호 분산 (Parseval) 이 성립해, 합성 정현파로
  절대 스케일(ms²)까지 손 검산할 수 있습니다.

대역(Task Force 1996):
  VLF 0.003–0.04 Hz, LF 0.04–0.15 Hz, HF 0.15–0.40 Hz.
"""

from __future__ import annotations

import cmath
import math
import statistics
from typing import Dict, List, Sequence, Tuple

VLF_BAND = (0.003, 0.04)
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.40)

# 주파수영역 분석의 최소 기록 길이(초). HF 대역 최저 주파수(0.15 Hz)의 한 주기는
# 6.7 s — 그 2배(≈13 s)는 있어야 HF에 빈이 잡힙니다. 여유를 둬 20 s로 둡니다.
MIN_DURATION_SEC = 20.0

# 기본 Welch 구간 길이 상한(표본). fs=4 Hz → 256/4 = 64 s 구간.
# LF(최저 0.04 Hz → 주기 25 s)·HF는 64 s 구간으로 충분히 해상되고, 구간을 짧게 유지해
# 평균할 구간 수를 늘리면 PSD 분산이 줄어듭니다. 반면 VLF(최저 0.003 Hz → 주기 333 s)는
# 이 구간으로 해상되지 않아 과소추정됩니다 — VLF가 필요한 긴 기록에서는 --nperseg 로
# 구간을 키우세요(vlf_reliable 플래그로 상태를 보고합니다).
DEFAULT_MAX_NPERSEG = 256


# --------------------------------------------------------------------------- #
# FFT — iterative radix-2 Cooley–Tukey (표준 라이브러리만)
# --------------------------------------------------------------------------- #
def _next_pow2(n: int) -> int:
    """n 이상인 가장 작은 2의 거듭제곱."""
    p = 1
    while p < n:
        p <<= 1
    return p


def _prev_pow2(n: int) -> int:
    """n 이하인 가장 큰 2의 거듭제곱 (n>=1). n<1이면 1."""
    if n < 1:
        return 1
    p = 1
    while (p << 1) <= n:
        p <<= 1
    return p


def fft(a: Sequence[complex]) -> List[complex]:
    """반복형 radix-2 Cooley–Tukey FFT. len(a)는 반드시 2의 거듭제곱.

    O(N log N). 결과는 X_k = Σ_n a_n · exp(-2πi·kn/N).
    """
    n = len(a)
    if n == 0:
        return []
    if n & (n - 1) != 0:
        raise ValueError("FFT 길이는 2의 거듭제곱이어야 합니다 (zero-pad 필요).")

    out = [complex(x) for x in a]

    # 비트 반전 순열(bit-reversal permutation)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]

    # 나비 연산(butterflies)
    length = 2
    while length <= n:
        wlen = cmath.exp(-2j * math.pi / length)
        half = length >> 1
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = out[start + k]
                v = out[start + k + half] * w
                out[start + k] = u + v
                out[start + k + half] = u - v
                w *= wlen
        length <<= 1
    return out


def rfft_pow2(x: Sequence[float], nfft: int) -> List[complex]:
    """실수 신호 x를 nfft(2의 거듭제곱)로 zero-pad 후 FFT, 단측 절반(0..N/2) 반환."""
    if nfft & (nfft - 1) != 0:
        raise ValueError("nfft는 2의 거듭제곱이어야 합니다.")
    padded: List[complex] = [complex(v, 0.0) for v in x]
    if len(padded) < nfft:
        padded.extend([0j] * (nfft - len(padded)))
    elif len(padded) > nfft:
        raise ValueError("신호 길이가 nfft보다 큽니다.")
    spec = fft(padded)
    return spec[: nfft // 2 + 1]


# --------------------------------------------------------------------------- #
# 리샘플링 & 창
# --------------------------------------------------------------------------- #
def _beat_times(nn: Sequence[float]) -> List[float]:
    """NN(ms) → 박동 시각(초). t_i = NN[:i+1] 누적합 / 1000."""
    times = []
    acc = 0.0
    for v in nn:
        acc += v / 1000.0
        times.append(acc)
    return times


def _interpolate_uniform(times: Sequence[float], values: Sequence[float], fs: float
                         ) -> Tuple[List[float], List[float]]:
    """(times, values)를 [t0, t_last] 위 fs Hz 균등 격자로 선형보간."""
    t0, t1 = times[0], times[-1]
    duration = t1 - t0
    n_samples = int(math.floor(duration * fs)) + 1
    grid = [t0 + k / fs for k in range(n_samples)]

    resampled = []
    j = 0
    n = len(times)
    for t in grid:
        while j < n - 2 and times[j + 1] < t:
            j += 1
        t_a, t_b = times[j], times[j + 1]
        v_a, v_b = values[j], values[j + 1]
        if t_b == t_a:
            resampled.append(v_a)
        else:
            frac = (t - t_a) / (t_b - t_a)
            resampled.append(v_a + (v_b - v_a) * frac)
    return grid, resampled


def _hann_periodic(n: int) -> List[float]:
    """주기(periodic, DFT-even) Hann 창 — scipy get_window('hann') 기본과 동일."""
    if n == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * k / n) for k in range(n)]


# --------------------------------------------------------------------------- #
# Welch PSD
# --------------------------------------------------------------------------- #
def welch_psd(x: Sequence[float], fs: float, nperseg: int = None,
              noverlap: int = None) -> Tuple[List[float], List[float], Dict[str, int]]:
    """Welch 방식 단측 PSD(ms²/Hz)를 추정.

    scipy.signal.welch(window='hann', detrend='constant', scaling='density',
    return_onesided=True) 와 동일한 수식을 순수 파이썬으로 구현합니다.
    구간 길이 nperseg는 2의 거듭제곱으로 강제되어(FFT용 zero-pad 조건 충족)
    구간별 Hann-창 주기도를 평균합니다.

    반환: (freqs, psd, meta) — meta = {nperseg, nfft, noverlap, n_segments}.
    """
    x = [float(v) for v in x]
    N = len(x)
    if N < 2:
        raise ValueError("PSD 추정에는 최소 2개의 표본이 필요합니다.")

    if nperseg is None:
        # 겹치는 구간을 몇 개 확보하도록 N/2 부근의 2의 거듭제곱, 상한 256.
        nperseg = min(DEFAULT_MAX_NPERSEG, _prev_pow2(max(2, N // 2)))
    nperseg = min(nperseg, N)
    nperseg = _prev_pow2(nperseg)   # 2의 거듭제곱으로 강제
    if nperseg < 2:
        nperseg = 2
    nfft = nperseg                  # 이미 2의 거듭제곱 → 추가 zero-pad 불필요

    if noverlap is None:
        noverlap = nperseg // 2
    noverlap = min(noverlap, nperseg - 1)
    step = nperseg - noverlap

    win = _hann_periodic(nperseg)
    winsum2 = sum(w * w for w in win)
    scale = 1.0 / (fs * winsum2)

    starts = list(range(0, N - nperseg + 1, step)) or [0]
    n_seg = len(starts)
    n_bins = nfft // 2 + 1
    psd_acc = [0.0] * n_bins

    for s in starts:
        seg = x[s:s + nperseg]
        mean_v = statistics.fmean(seg)           # detrend='constant'
        seg_w = [(seg[i] - mean_v) * win[i] for i in range(nperseg)]
        spec = rfft_pow2(seg_w, nfft)
        for k in range(n_bins):
            p = (spec[k].real ** 2 + spec[k].imag ** 2) * scale
            if k != 0 and not (nfft % 2 == 0 and k == n_bins - 1):
                p *= 2.0                          # 단측화
            psd_acc[k] += p

    psd = [v / n_seg for v in psd_acc]
    df = fs / nfft
    freqs = [k * df for k in range(n_bins)]
    meta = {"nperseg": nperseg, "nfft": nfft, "noverlap": noverlap,
            "n_segments": n_seg}
    return freqs, psd, meta


def _band_bins(freqs: Sequence[float], lo: float, hi: float) -> int:
    """[lo, hi) 대역에 들어가는 PSD 빈의 개수."""
    return sum(1 for f in freqs if lo <= f < hi)


def _band_power(freqs: Sequence[float], psd: Sequence[float], lo: float, hi: float) -> float:
    """[lo, hi) 대역의 PSD를 적분(직사각형 규칙, 폭 = df).

    대역 안에 빈이 하나도 없으면 **NaN**을 반환합니다. 0.0 을 반환하면 "파워가
    실제로 0" 과 "주파수 해상도가 부족해 추정 불가"를 구분할 수 없어, 짧은 기록의
    VLF가 진짜 0인 것처럼 보고되는 침묵의 오답이 됩니다(예: 64 s 기록 → df=0.0625 Hz
    → VLF(0.003–0.04 Hz)에 빈 0개 → 과거엔 "0.0 ms²"로 출력).
    """
    if len(freqs) < 2:
        return float("nan")
    df = freqs[1] - freqs[0]
    total = 0.0
    n = 0
    for f, p in zip(freqs, psd):
        if lo <= f < hi:
            total += p * df
            n += 1
    return total if n else float("nan")


def _peak(freqs: Sequence[float], psd: Sequence[float], lo: float, hi: float):
    """[lo, hi) 대역의 최대 PSD 주파수. 대역에 **양의 파워가 없으면 None**.

    과거엔 시작값이 -1.0 이라 PSD가 전부 0.0 인 신호(예: RR이 전부 동일)에서도
    대역의 첫 빈이 '피크'로 뽑혔습니다. 그 결과 평탄 신호가 호흡 피크를 가진 것으로
    보여 '느린/공명 호흡 레짐'이 오탐되었습니다(hf_nu 도 0 이라 조건이 성립).
    파워가 0 인 대역에는 피크가 없습니다.
    """
    best_f, best_p = None, 0.0
    for f, p in zip(freqs, psd):
        if lo <= f < hi and p > best_p:
            best_p, best_f = p, f
    return best_f


def frequency_domain(nn: Sequence[float], fs: float = 4.0,
                     nperseg: int = None) -> Dict[str, float]:
    """정제된 NN 간격(ms)으로부터 주파수영역 지표를 계산.

    반환 키:
      vlf_power, lf_power, hf_power : 각 대역 절대 파워 (ms²)
      total_power                   : VLF+LF+HF (ms²)
      lf_hf_ratio                   : LF/HF
      lf_nu, hf_nu                  : 정규화 단위 = 대역/(LF+HF)·100
      lf_pct, hf_pct, vlf_pct       : 총 파워 대비 비율(%)
      peak_lf, peak_hf              : 각 대역 내 최대 PSD 주파수 (Hz)
      resample_fs, duration_sec, n_resampled : 리샘플 메타
      welch_nperseg, welch_nfft, welch_segments : Welch 메타
    """
    nn = [float(x) for x in nn]
    if len(nn) < 4:
        raise ValueError("주파수영역 지표는 최소 4개의 박동이 필요합니다.")

    times = _beat_times(nn)
    _grid, resampled = _interpolate_uniform(times, nn, fs)
    if len(resampled) < 4:
        raise ValueError("기록이 너무 짧아 주파수영역 분석을 할 수 없습니다.")

    # 최소 길이 방어: HF 대역(0.15–0.40 Hz)조차 해상되지 않으면 어떤 지표도
    # 의미가 없습니다(예: 4박동 → df=1 Hz → 모든 대역에 빈 0개 → 과거엔 전부 0.0,
    # lf_hf_ratio=inf 를 조용히 반환). HF 한 주기(≈6.7 s)의 2배는 필요합니다.
    duration = times[-1]
    if duration < MIN_DURATION_SEC:
        raise ValueError(
            f"기록이 {duration:.1f} s 로 너무 짧습니다 — 주파수영역 분석에는 최소 "
            f"{MIN_DURATION_SEC:.0f} s 가 필요합니다(HF 대역 해상 한계).")

    freqs, psd, meta = welch_psd(resampled, fs, nperseg=nperseg)

    # Welch 구간 길이 = 주파수 해상도의 근본 한계. 구간보다 느린 주기는 구간별
    # 평균 제거(detrend='constant')로 사라지므로, 구간이 대역의 최저 주파수 한
    # 주기보다 짧으면 그 대역 파워는 심하게 과소추정됩니다.
    seg_sec = meta["nperseg"] / fs
    vlf_bins = _band_bins(freqs, *VLF_BAND)
    lf_bins = _band_bins(freqs, *LF_BAND)
    hf_bins = _band_bins(freqs, *HF_BAND)
    # VLF 최저 주파수(0.003 Hz)의 한 주기 = 333 s. 구간이 그보다 짧으면 VLF는
    # 참고용(과소추정)임을 플래그로 알립니다. 기본 설정(구간 64 s)에서는 항상 False.
    vlf_reliable = bool(vlf_bins >= 2 and seg_sec >= 1.0 / VLF_BAND[0])

    vlf = _band_power(freqs, psd, *VLF_BAND)
    lf = _band_power(freqs, psd, *LF_BAND)
    hf = _band_power(freqs, psd, *HF_BAND)
    # total 은 Task Force 정의상 VLF를 포함하므로, VLF가 해상 불가(NaN)면 total 도
    # 알 수 없습니다 — 0 으로 눙치지 않고 NaN 을 전파합니다.
    total = vlf + lf + hf
    lf_hf = lf / hf if hf > 0 else float("inf")
    lf_plus_hf = lf + hf
    lf_nu = 100.0 * lf / lf_plus_hf if lf_plus_hf > 0 else 0.0
    hf_nu = 100.0 * hf / lf_plus_hf if lf_plus_hf > 0 else 0.0

    # 호흡수 추정 & 호흡 레짐 감지.
    # 자발 호흡의 RSA는 HF 대역(0.15–0.40 Hz, 9–24회/분)에 실리므로 기본적으로
    # HF 최대 PSD 주파수를 호흡 주파수로 봅니다(표준 HRV 기반 호흡 추정).
    # 그러나 느린/공명 호흡(예: 6회/분=0.1 Hz)은 LF 대역에 실려 HF가 거의 비고
    # LF/HF·HF n.u. 의 '부교감 방향' 해석이 역전됩니다. 전체 호흡대역(0.04–0.40 Hz)의
    # 최대 피크가 LF에 있고 HF n.u. 가 매우 낮으면(<20) '느린/공명 호흡 레짐'으로 보고
    # 호흡수를 LF 피크에서 추정하며 플래그를 세웁니다.
    resp_peak_hz = _peak(freqs, psd, LF_BAND[0], HF_BAND[1])
    peak_in_lf = resp_peak_hz is not None and resp_peak_hz < HF_BAND[0]
    slow_regime = bool(peak_in_lf and hf_nu < 20.0)
    if slow_regime:
        resp_hz = resp_peak_hz
        resp_source = "LF"
    else:
        resp_hz = _peak(freqs, psd, *HF_BAND)
        resp_source = "HF"
    resp_brpm = resp_hz * 60.0 if resp_hz is not None else None
    ln_hf = math.log(hf) if hf > 0 else float("nan")

    return {
        "vlf_power": vlf,
        "lf_power": lf,
        "hf_power": hf,
        "total_power": total,
        "lf_hf_ratio": lf_hf,
        "lf_nu": lf_nu,
        "hf_nu": hf_nu,
        "ln_hf": ln_hf,
        "vlf_pct": 100.0 * vlf / total if total > 0 else 0.0,
        "lf_pct": 100.0 * lf / total if total > 0 else 0.0,
        "hf_pct": 100.0 * hf / total if total > 0 else 0.0,
        "peak_lf": _peak(freqs, psd, *LF_BAND),
        "peak_hf": _peak(freqs, psd, *HF_BAND),
        "resp_rate_hz": resp_hz,
        "resp_rate_brpm": resp_brpm,
        "resp_source": resp_source,
        "slow_breathing_regime": slow_regime,
        "resample_fs": fs,
        "duration_sec": duration,
        "n_resampled": len(resampled),
        "welch_nperseg": meta["nperseg"],
        "welch_nfft": meta["nfft"],
        "welch_segments": meta["n_segments"],
        "welch_segment_sec": seg_sec,
        "freq_resolution_hz": fs / meta["nfft"],
        "vlf_bins": vlf_bins,
        "lf_bins": lf_bins,
        "hf_bins": hf_bins,
        "vlf_reliable": vlf_reliable,
    }
