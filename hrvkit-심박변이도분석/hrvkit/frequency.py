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

# 리샘플 격자 점 수 상한 (fs 4 Hz 기준 ≈ 3일). 비생리적 입력의 OOM 방어.
_MAX_RESAMPLE_POINTS = 1_000_000

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
    # 비생리적으로 큰 RR(예: --clean none 으로 통과한 9e99)이 들어오면 격자가
    # 무한정 커져 OOM 으로 되돌아올 수 없이 멈춥니다. 상한을 두고 명확히 실패합니다.
    if n_samples > _MAX_RESAMPLE_POINTS:
        raise ValueError(
            f"리샘플 격자가 {n_samples:.3g}점으로 너무 큽니다(상한 "
            f"{_MAX_RESAMPLE_POINTS}). RR 값이 비정상적으로 크거나 --fs 가 너무 "
            "높습니다 — --max-rr 로 생리적 범위를 제한하거나 --fs 를 낮추세요.")
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


# --------------------------------------------------------------------------- #
# Lomb–Scargle 주기도 (보간 없이 불균등 표본에서 직접 PSD)
# --------------------------------------------------------------------------- #
# 균등 격자 선형보간은 저역통과 필터로 작용해 HF 파워를 체계적으로 **과소추정**하고
# (Clifford & Tarassenko 2005; Laguna et al. 1998), 이상박동을 제거(--clean remove)한
# 뒤 남은 구멍을 직선으로 메우면 그 구간에 없는 저주파 성분이 생깁니다.
# Lomb–Scargle 주기도는 박동 시각을 그대로 두고 각 주파수에서 정현파를 최소제곱
# 적합하므로 보간이 필요 없습니다 — HRV에서 널리 쓰이는 대안입니다.
DEFAULT_LS_OVERSAMPLE = 4.0
# 순수 파이썬 O(주파수 수 × 박동 수) 이므로 격자 크기에 상한을 둡니다.
MAX_LS_FREQS = 4096


def _lomb_power_direct(times: Sequence[float], xc: Sequence[float],
                       omega: float) -> float:
    """한 각주파수에서의 고전 Lomb(1976)/Scargle(1982) 주기도 값 (정규화 전).

    교과서 정의를 그대로 옮긴 **참조 구현**입니다 (느림). 격자용 고속 구현
    `lombscargle_grid` 가 이 값과 일치하는지 테스트로 고정합니다.

        tan(2ωτ) = Σ sin(2ω t_i) / Σ cos(2ω t_i)
        P(ω) = ½ [ (Σ x_i cos ω(t_i−τ))² / Σ cos²ω(t_i−τ)
                 + (Σ x_i sin ω(t_i−τ))² / Σ sin²ω(t_i−τ) ]

    xc 는 **평균을 뺀** 값이어야 합니다.
    """
    if omega <= 0.0:
        return 0.0
    s2 = 0.0
    c2 = 0.0
    for t in times:
        a = 2.0 * omega * t
        s2 += math.sin(a)
        c2 += math.cos(a)
    tau = math.atan2(s2, c2) / (2.0 * omega)
    xcs = xss = cc = ss = 0.0
    for t, x in zip(times, xc):
        w = omega * (t - tau)
        c = math.cos(w)
        s = math.sin(w)
        xcs += x * c
        xss += x * s
        cc += c * c
        ss += s * s
    p = 0.0
    if cc > 0.0:
        p += xcs * xcs / cc
    if ss > 0.0:
        p += xss * xss / ss
    return 0.5 * p


def lombscargle_grid(times: Sequence[float], values: Sequence[float],
                     df: float, nfreq: int) -> Tuple[List[float], List[float]]:
    """균등 주파수 격자 f_k = (k+1)·df 위의 Lomb–Scargle 주기도(정규화 전).

    격자가 균등하므로 박동마다 위상자(phasor) exp(i·Δω·t_i)를 곱해 나가는
    점화식으로 sin/cos 재계산을 없앱니다 — 순수 파이썬에서 결정적으로 빠릅니다.
    |z|=1 이 반복 곱셈으로 미세하게 흔들리므로 주기적으로 재정규화합니다.

    반환: (freqs, power) — power 는 ms² 스케일(밀도 아님).
    """
    n = len(times)
    if n < 2 or nfreq < 1 or df <= 0.0:
        return [], []
    mean_v = statistics.fmean(values)
    xc = [v - mean_v for v in values]

    dw = 2.0 * math.pi * df
    step = [cmath.exp(1j * dw * t) for t in times]
    z = list(step)                      # k=0 → ω = dw

    freqs: List[float] = []
    power: List[float] = []
    for k in range(nfreq):
        cc = cs = xcs = xss = 0.0
        for i in range(n):
            zi = z[i]
            c = zi.real
            s = zi.imag
            cc += c * c
            cs += c * s
            xi = xc[i]
            xcs += xi * c
            xss += xi * s
        ss = n - cc                     # |z|=1 → Σcos² + Σsin² = n
        # tan(2ωτ) = Σsin2ωt / Σcos2ωt = 2·Σcs / (Σcc − Σss)
        wtau = 0.5 * math.atan2(2.0 * cs, cc - ss)
        ct = math.cos(wtau)
        st = math.sin(wtau)
        ct2, st2, cst = ct * ct, st * st, ct * st
        xc_t = xcs * ct + xss * st
        xs_t = xss * ct - xcs * st
        cc_t = cc * ct2 + 2.0 * cs * cst + ss * st2
        ss_t = ss * ct2 - 2.0 * cs * cst + cc * st2
        # 임계값은 **상대**여야 합니다: cc_t/ss_t 는 n/2 규모라 절대값 1e-12 는
        # n 이 커질수록 무의미해지고, |z| 드리프트로 ss 가 미세하게 음수가 될 수도
        # 있습니다(완전 균등 표본을 f_eff/2 에서 찔러 보면 Σsin²≈0 이 실제로 발생).
        eps = 1e-12 * n
        p = 0.0
        if cc_t > eps:
            p += xc_t * xc_t / cc_t
        if ss_t > eps:
            p += xs_t * xs_t / ss_t
        freqs.append((k + 1) * df)
        power.append(0.5 * p)

        if k + 1 < nfreq:
            renorm = (k % 256) == 255
            for i in range(n):
                zi = z[i] * step[i]
                if renorm:
                    m = abs(zi)
                    if m > 0.0:
                        zi /= m
                z[i] = zi
    return freqs, power


def lombscargle_psd(times: Sequence[float], values: Sequence[float],
                    *, oversample: float = DEFAULT_LS_OVERSAMPLE,
                    f_max: float = HF_BAND[1],
                    max_freqs: int = MAX_LS_FREQS
                    ) -> Tuple[List[float], List[float], Dict[str, float]]:
    """불균등 표본 (times[s], values) → 단측 PSD 밀도(값단위²/Hz).

    정규화: PSD(f) = 2·P_lomb(f) / f_eff,  f_eff = (N−1)/기록길이.
    균등 표본이면 이 식이 사각창 주기도의 밀도 스케일과 **정확히** 일치하므로
    (진폭 A 정현파 → 대역 적분 = A²/2 = 분산) 합성 신호로 손 검산할 수 있습니다.

    격자 간격 df = 1/(oversample·T) 로 **과표본**합니다. 실제 주파수 해상도는
    1/T 이며(과표본은 해상도를 늘리지 않습니다), 대역 경계에서의 사각적분 오차만
    줄입니다. 격자 점 수는 max_freqs 로 제한합니다.

    반환: (freqs, psd, meta).
    """
    n = len(times)
    if n < 4:
        raise ValueError("Lomb–Scargle PSD 에는 최소 4개의 표본이 필요합니다.")
    if len(values) != n:
        raise ValueError(
            f"times 와 values 의 길이가 다릅니다 ({n} != {len(values)}).")
    span = times[-1] - times[0]
    if not (span > 0.0) or not math.isfinite(span):
        raise ValueError("박동 시각이 단조 증가하지 않습니다.")
    # NaN/inf 는 비교로 걸러야 합니다: oversample=inf 면 df=0 이 되어 격자 크기
    # 계산에서 ZeroDivisionError 로 죽습니다(CLI 는 막지만 라이브러리 API 는 아님).
    if not (oversample > 0.0) or not math.isfinite(oversample):
        raise ValueError("oversample 은 유한한 양수여야 합니다.")
    if not math.isfinite(f_max) or f_max <= 0.0:
        raise ValueError("f_max 는 유한한 양수여야 합니다.")

    f_eff = (n - 1) / span              # 평균 표본율 = 1/평균 NN
    df = 1.0 / (oversample * span)
    nfreq = int(math.ceil(f_max / df))
    eff_oversample = oversample
    capped = False
    if nfreq > max_freqs:               # 너무 촘촘하면 과표본 배수를 낮춥니다
        nfreq = max_freqs
        df = f_max / nfreq
        eff_oversample = 1.0 / (df * span)
        capped = True
        if eff_oversample < 1.0:
            # 격자가 해상도(1/T)보다 성글어지면 좁은 피크가 격자 사이로 빠지고,
            # 사각적분이 지나치게 넓은 df 를 곱해 대역 파워가 크게 틀립니다
            # (측정: 4시간 기록에서 LF -30 %, HF +46 %, LF/HF 2.1배 오차).
            # 조용히 틀린 숫자를 내느니 거부하고 대안을 안내합니다.
            raise ValueError(
                f"기록이 {span / 3600.0:.3g}시간으로 너무 길어 Lomb–Scargle 격자"
                f"(최대 {max_freqs}점)가 주파수 해상도(1/T)보다 성글어집니다 — "
                "대역 파워가 크게 틀어지므로 계산하지 않습니다. "
                "`--window` 로 구간을 나눠 분석하거나(권장) `--psd welch` 를 "
                "쓰세요. (순수 파이썬으로 이보다 긴 기록을 전체 해상도로 "
                "적합하는 것은 현실적인 시간 안에 끝나지 않습니다.)")
    if nfreq < 1:
        raise ValueError("주파수 격자를 만들 수 없습니다 (기록이 너무 짧습니다).")

    # 시간 원점을 옮겨도 Lomb 주기도는 불변이지만, t를 0 근처로 두면 수치 조건이 좋습니다.
    t0 = times[0]
    rel = [t - t0 for t in times]
    freqs, power = lombscargle_grid(rel, values, df, nfreq)
    psd = [2.0 * p / f_eff for p in power]

    meta = {
        "ls_df_hz": df,
        "ls_nfreq": nfreq,
        "ls_oversample": eff_oversample,
        "ls_fs_eff": f_eff,
        "ls_span_sec": span,
        "ls_nyquist_hz": 0.5 * f_eff,
        # 격자 상한에 걸렸는지(과표본 배수가 요청값보다 낮아졌는지) — 이 경우에도
        # eff_oversample >= 1 은 보장됩니다(그 아래면 위에서 거부).
        "ls_grid_capped": capped,
    }
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
                     nperseg: int = None, method: str = "welch",
                     ls_oversample: float = DEFAULT_LS_OVERSAMPLE,
                     times: Optional[Sequence[float]] = None
                     ) -> Dict[str, float]:
    """정제된 NN 간격(ms)으로부터 주파수영역 지표를 계산.

    method:
      "welch" (기본) — fs Hz 균등 격자 선형보간 → Welch PSD.
      "lomb"         — 보간 없이 박동 시각 위에서 Lomb–Scargle 주기도.

    times: 각 NN 값에 대응하는 **실제 박동 시각(초)**. 생략하면 NN 의 누적합으로
      만듭니다. `--clean remove` 처럼 박동이 **삭제된** 경우 누적합은 삭제된 시간을
      통째로 없애 뒤따르는 모든 박동의 시각을 앞으로 당깁니다(기록이 짧아지고
      주파수가 전부 위로 밀립니다). 그 경우 원본 시각을 그대로 넘기면 결측 구간이
      **구멍으로 보존**됩니다 — Lomb 은 구멍을 건너뛰어 적합하고, Welch 는 구멍을
      가로질러 보간합니다.

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
    if method not in ("welch", "lomb"):
        raise ValueError(f"알 수 없는 PSD 방법: {method!r} (welch 또는 lomb).")
    nn = [float(x) for x in nn]
    if len(nn) < 4:
        raise ValueError("주파수영역 지표는 최소 4개의 박동이 필요합니다.")

    if times is None:
        times = _beat_times(nn)
    else:
        times = [float(t) for t in times]
        if len(times) != len(nn):
            raise ValueError(
                f"times 와 nn 의 길이가 다릅니다 ({len(times)} != {len(nn)}).")
        if any(times[i] >= times[i + 1] for i in range(len(times) - 1)):
            raise ValueError("박동 시각(times)이 단조 증가해야 합니다.")

    # 최소 길이 방어: HF 대역(0.15–0.40 Hz)조차 해상되지 않으면 어떤 지표도
    # 의미가 없습니다(예: 4박동 → df=1 Hz → 모든 대역에 빈 0개 → 과거엔 전부 0.0,
    # lf_hf_ratio=inf 를 조용히 반환). HF 한 주기(≈6.7 s)의 2배는 필요합니다.
    duration = times[-1] - times[0] + nn[0] / 1000.0
    if duration < MIN_DURATION_SEC:
        raise ValueError(
            f"기록이 {duration:.1f} s 로 너무 짧습니다 — 주파수영역 분석에는 최소 "
            f"{MIN_DURATION_SEC:.0f} s 가 필요합니다(HF 대역 해상 한계).")

    ls_meta: Dict[str, float] = {}
    if method == "lomb":
        # 보간하지 않습니다 — 박동 시각 위에서 직접 적합.
        freqs, psd, ls_meta = lombscargle_psd(times, nn, oversample=ls_oversample)
        resampled: List[float] = []
        meta = {"nperseg": 0, "nfft": 0, "noverlap": 0, "n_segments": 0}
        seg_sec = 0.0
        # 과표본 격자 간격이 아니라 **실제** 해상도 1/T 를 보고합니다.
        resolution_hz = 1.0 / ls_meta["ls_span_sec"]
    else:
        _grid, resampled = _interpolate_uniform(times, nn, fs)
        if len(resampled) < 4:
            raise ValueError("기록이 너무 짧아 주파수영역 분석을 할 수 없습니다.")
        freqs, psd, meta = welch_psd(resampled, fs, nperseg=nperseg)
        # Welch 구간 길이 = 주파수 해상도의 근본 한계. 구간보다 느린 주기는 구간별
        # 평균 제거(detrend='constant')로 사라지므로, 구간이 대역의 최저 주파수 한
        # 주기보다 짧으면 그 대역 파워는 심하게 과소추정됩니다.
        seg_sec = meta["nperseg"] / fs
        resolution_hz = fs / meta["nfft"]

    if method == "lomb":
        # Lomb 격자는 해상도(1/T)보다 oversample 배 **촘촘하게** 찍습니다. 격자점을
        # 그냥 세면 실제 해상 능력을 과표본 배수만큼 부풀려 말하게 되고, 대역에
        # 빈이 0개일 때 NaN 을 내는 `_band_power` 의 안전장치가 **영원히 발동하지
        # 않습니다**(30 s 기록에서도 VLF 가 유한한 값으로 나왔습니다).
        # 독립적인 해상 요소 수 = 대역폭 × 기록길이 로 셉니다.
        span_ls = ls_meta["ls_span_sec"]
        vlf_bins = int((VLF_BAND[1] - VLF_BAND[0]) * span_ls)
        lf_bins = int((LF_BAND[1] - LF_BAND[0]) * span_ls)
        hf_bins = int((HF_BAND[1] - HF_BAND[0]) * span_ls)
    else:
        vlf_bins = _band_bins(freqs, *VLF_BAND)
        lf_bins = _band_bins(freqs, *LF_BAND)
        hf_bins = _band_bins(freqs, *HF_BAND)
    # VLF 최저 주파수(0.003 Hz)의 한 주기 = 333 s. 그보다 짧으면 VLF는 참고용
    # (과소추정)임을 플래그로 알립니다. Welch 는 **구간** 길이가, Lomb 는 **기록**
    # 전체 길이가 한계입니다 — Lomb 는 기록을 쪼개지 않으므로 같은 기록에서 VLF를
    # 신뢰할 수 있게 되는 경우가 많습니다.
    if method == "lomb":
        # 기록이 VLF 최저 주파수의 한 주기(333 s)를 겨우 넘는다고 VLF 를 믿을 수는
        # 없습니다 — Task Force 1996 은 단기(≈5분) 기록의 VLF 를 "의심스러운 지표"로
        # 보고 피하라고 명시합니다. 최소 **3주기**(999 s ≈ 16.7분)를 요구합니다.
        vlf_reliable = bool(vlf_bins >= 2 and
                            ls_meta["ls_span_sec"] >= 3.0 / VLF_BAND[0])
    else:
        vlf_reliable = bool(vlf_bins >= 2 and seg_sec >= 1.0 / VLF_BAND[0])

    vlf = _band_power(freqs, psd, *VLF_BAND)
    lf = _band_power(freqs, psd, *LF_BAND)
    hf = _band_power(freqs, psd, *HF_BAND)
    if method == "lomb":
        # 해상 요소가 2개 미만인 대역은 "값이 0" 이 아니라 "추정 불가" 입니다.
        # 또 대역 하단이 평균 Nyquist(=평균 HR/2) 위면 그 대역은 표본율이 원리적으로
        # 담을 수 없는 주파수라 값을 내면 안 됩니다(예: 4박동·20 s 기록이 HF 파워
        # 25410 ms² 와 호흡수 17.3회/분 을 내던 경우).
        nan = float("nan")
        nyq = ls_meta["ls_nyquist_hz"]
        if vlf_bins < 2 or VLF_BAND[0] >= nyq:
            vlf = nan
        if lf_bins < 2 or LF_BAND[0] >= nyq:
            lf = nan
        if hf_bins < 2 or HF_BAND[0] >= nyq:
            hf = nan
    # total 은 Task Force 정의상 VLF를 포함하므로, VLF가 해상 불가(NaN)면 total 도
    # 알 수 없습니다 — 0 으로 눙치지 않고 NaN 을 전파합니다.
    total = vlf + lf + hf
    # NaN(=추정 불가)을 0 처럼 다루면 조용한 오답이 됩니다: 과거엔 hf=NaN 일 때
    # `hf > 0` 이 False 라 LF/HF 가 **inf** 가 되고, 해석 엔진이 그걸 "교감 우세"로
    # 읽어 "각성·스트레스 부하" 라고 단정했습니다. 해상 불가는 방향이 없습니다.
    nan = float("nan")
    if math.isnan(lf) or math.isnan(hf):
        lf_hf = nan
        lf_nu = nan
        hf_nu = nan
    else:
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
    # 대역 파워가 NaN(추정 불가)이면 그 대역의 피크·호흡수도 주장하면 안 됩니다.
    if (resp_source == "HF" and math.isnan(hf)) or \
       (resp_source == "LF" and math.isnan(lf)):
        resp_hz = None
    resp_brpm = resp_hz * 60.0 if resp_hz is not None else None
    ln_hf = math.log(hf) if hf > 0 else float("nan")

    out = {
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
        "peak_lf": None if math.isnan(lf) else _peak(freqs, psd, *LF_BAND),
        "peak_hf": None if math.isnan(hf) else _peak(freqs, psd, *HF_BAND),
        "resp_rate_hz": resp_hz,
        "resp_rate_brpm": resp_brpm,
        "resp_source": resp_source,
        "slow_breathing_regime": slow_regime,
        # lomb 은 리샘플하지 않으므로 fs 를 되돌려 주면 거짓말이 됩니다(내보낸 JSON을
        # 읽는 쪽이 "4 Hz 로 리샘플됨"으로 기록하게 됩니다).
        "resample_fs": None if method == "lomb" else fs,
        "duration_sec": duration,
        "n_resampled": len(resampled),
        "welch_nperseg": meta["nperseg"],
        "welch_nfft": meta["nfft"],
        "welch_segments": meta["n_segments"],
        "welch_segment_sec": seg_sec,
        "freq_resolution_hz": resolution_hz,
        "vlf_bins": vlf_bins,
        "lf_bins": lf_bins,
        "hf_bins": hf_bins,
        "vlf_reliable": vlf_reliable,
        "psd_method": method,
    }
    if method == "lomb":
        out.update(ls_meta)
        out["ls_n_beats"] = len(nn)
        # 평균 표본율의 절반(≈ 평균 HR/2)보다 높은 주파수는 앨리어싱 위험 구간입니다.
        # 서맥(평균 HR < 48 bpm)이면 HF 대역 상단이 여기에 걸립니다.
        out["ls_above_nyquist"] = bool(ls_meta["ls_nyquist_hz"] < HF_BAND[1])
    return out
