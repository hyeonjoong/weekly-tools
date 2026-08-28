"""레벨 지표 — 두 계통을 **반드시 나란히** 냅니다.

(a) 논문 단위 — LAeq · LAmax · 다이내믹 레인지
    NBR 리뷰 Table 2 항목 1 이 "LAeq + dynamic range (dB)" 입니다. 그리고 Czempik
    et al. (2020) 은 ICU 에서 LAmax 가 수면시간과 r = −0.64, LAeq20sec 이 r = −0.50
    으로 **순간 최대치가 평균보다 강하게** 관련된다고 보고했습니다. 그래서 평균만
    인쇄하는 일이 없도록 LAmax 를 같은 표에 강제로 넣습니다.

(b) 조건 매칭용 — LUFS (ITU-R BS.1770-4) · LRA · 트루피크
    **정직 고지: LUFS 는 논문에 한 번도 나오지 않습니다.** 논문은 라우드니스를
    파라미터로 채점하지 않았습니다. LUFS 를 쓰는 이유는 오직 하나, 조건 간 체감
    음량을 맞췄는지 보는 **실험 통제 관행의 표준 수단**이기 때문입니다.
    이 구분을 흐리면 안 됩니다.

왜 피크가 아니라 레벨인가
-------------------------
"피크 노멀라이즈 했다"는 말은 음량이 맞았다는 뜻이 아닙니다. 파형 충실도
(crest factor)가 다르면 피크가 같아도 체감 음량이 6 dB 넘게 벌어집니다.
핑크노이즈와 드론을 같은 세트에 넣는 순간 이게 바로 문제가 됩니다.

게이팅 상수 (BS.1770-4)
-----------------------
* 블록 400 ms, 겹침 75 % (100 ms 홉)
* 채널 가중 G: L/R = 1.0 (이 툴은 모노·스테레오만 다루므로 서라운드 1.41 은 미사용)
* 절대 게이트 Γa = −70 LUFS
* 상대 게이트 Γr = (절대 게이트 통과 블록의 에너지 평균) − 10 LU
* 오프셋 −0.691 dB
LRA 는 3 s 블록 / **100 ms 홉**, 절대 −70 LUFS, 상대 −20 LU, 10 ~ 95 백분위.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

#: BS.1770 라우드니스 오프셋.
LOUDNESS_OFFSET = -0.691
#: 절대 게이트 (LUFS).
ABS_GATE_LUFS = -70.0
#: 통합 라우드니스의 상대 게이트 (LU).
REL_GATE_LU = -10.0
#: LRA 의 상대 게이트 (LU).
LRA_REL_GATE_LU = -20.0

_SILENCE = -400.0  # dB 대신 쓰는 '사실상 무음' 바닥값
#: 다이내믹 레인지 계산에서 제외하는 창의 기준 (LAmax 아래 몇 dB).
DR_FLOOR_BELOW_MAX = 60.0


def _db(power: float) -> float:
    if power <= 0.0:
        return _SILENCE
    return 10.0 * math.log10(power)


def block_means(frame_sums: Sequence[Sequence[float]], frame_len: int,
                block_frames: int, hop_frames: int) -> List[List[float]]:
    """10 ms 프레임의 제곱합 배열 → 블록별 채널 평균제곱.

    `frame_sums[c][i]` 는 채널 c 의 i번째 프레임 제곱합입니다. 마지막 자투리
    프레임은 길이가 짧을 수 있으므로 제곱합/샘플수를 따로 관리하지 않고,
    **완전한 프레임만** 블록에 넣습니다(BS.1770 도 자투리 블록을 버립니다).
    """
    nch = len(frame_sums)
    if nch == 0:
        return []
    nframes = len(frame_sums[0])
    out: List[List[float]] = []
    denom = float(block_frames * frame_len)
    start = 0
    while start + block_frames <= nframes:
        block = []
        for c in range(nch):
            fs_c = frame_sums[c]
            block.append(sum(fs_c[start:start + block_frames]) / denom)
        out.append(block)
        start += hop_frames
    return out


def block_loudness(blocks: Sequence[Sequence[float]], weights: Sequence[float]) -> List[float]:
    """블록별 라우드니스 l_j = −0.691 + 10 log10( Σ_c G_c · z_jc )."""
    out = []
    for block in blocks:
        acc = 0.0
        for c, z in enumerate(block):
            acc += weights[c] * z
        out.append(LOUDNESS_OFFSET + _db(acc))
    return out


def channel_weights(nch: int) -> List[float]:
    """BS.1770 채널 가중. 모노·스테레오는 전부 1.0 입니다.

    3채널 이상은 채널 배치를 알 수 없으면 서라운드 가중(1.41)을 붙일 근거가
    없으므로 **전부 1.0 으로 두고 리포트에 그 사실을 자백**합니다.
    """
    return [1.0] * nch


def integrated_lufs(frame_sums: Sequence[Sequence[float]], frame_len: int,
                    fs: int) -> Tuple[Optional[float], int, int]:
    """통합 라우드니스(LUFS). 반환 = (값, 절대게이트 통과 블록수, 총 블록수).

    게이트를 통과한 블록이 하나도 없으면(사실상 무음 파일) None 을 돌려줍니다 —
    이때 임의의 숫자를 지어내지 않는 것이 중요합니다.
    """
    fpb = max(1, int(round(0.400 * fs / frame_len)))   # 400 ms
    hop = max(1, int(round(0.100 * fs / frame_len)))   # 100 ms
    blocks = block_means(frame_sums, frame_len, fpb, hop)
    if not blocks:
        return None, 0, 0
    w = channel_weights(len(frame_sums))
    ls = block_loudness(blocks, w)
    idx_abs = [j for j, l in enumerate(ls) if l > ABS_GATE_LUFS]
    if not idx_abs:
        return None, 0, len(blocks)
    # 상대 게이트 기준값은 '절대 게이트 통과 블록의 에너지 평균'에서 구합니다.
    acc = 0.0
    for j in idx_abs:
        for c, z in enumerate(blocks[j]):
            acc += w[c] * z
    mean_pow = acc / len(idx_abs)
    gamma_r = LOUDNESS_OFFSET + _db(mean_pow) + REL_GATE_LU
    idx = [j for j in idx_abs if ls[j] > gamma_r]
    if not idx:
        return None, len(idx_abs), len(blocks)
    acc = 0.0
    for j in idx:
        for c, z in enumerate(blocks[j]):
            acc += w[c] * z
    return LOUDNESS_OFFSET + _db(acc / len(idx)), len(idx_abs), len(blocks)


def loudness_range(frame_sums: Sequence[Sequence[float]], frame_len: int,
                   fs: int) -> Optional[float]:
    """LRA (LU) — EBU Tech 3342. 3 s 블록 / **100 ms 홉**, 10 ~ 95 백분위.

    홉이 왜 1 s 가 아닌가: Tech 3342 v3(2016)·v4(2023) 는 연속 창 사이에
    **2.9 s 이상 겹침**(= 홉 ≤ 100 ms, 라우드니스를 10 Hz 이상으로 표본화)을
    요구합니다. 1 s 홉은 철회된 2011년 v2 의 문구입니다. 20초 파일에서 그 차이는
    치명적입니다 — 1 s 홉이면 짧은구간 블록이 18개뿐이라 10 백분위가 사실상
    2개 표본에서 추정되고, 실측 `S6_breath-pacing.wav` 에서 14.8 LU 대 17.7 LU
    (ffmpeg 17.7)로 2.9 LU 어긋났습니다. (라운드 1 정확성 검토에서 발견.)
    """
    fpb = max(1, int(round(3.000 * fs / frame_len)))
    hop = max(1, int(round(0.100 * fs / frame_len)))
    blocks = block_means(frame_sums, frame_len, fpb, hop)
    if len(blocks) < 2:
        return None
    w = channel_weights(len(frame_sums))
    ls = block_loudness(blocks, w)
    above = [l for l in ls if l > ABS_GATE_LUFS]
    if not above:
        return None
    powers = [10.0 ** ((l - LOUDNESS_OFFSET) / 10.0) for l in above]
    gamma_r = LOUDNESS_OFFSET + _db(sum(powers) / len(powers)) + LRA_REL_GATE_LU
    kept = sorted(l for l in above if l > gamma_r)
    if len(kept) < 2:
        return 0.0
    return percentile(kept, 95.0) - percentile(kept, 10.0)


def percentile(sorted_vals: Sequence[float], pct: float) -> float:
    """선형보간 백분위 (입력은 오름차순 정렬돼 있어야 합니다)."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("percentile: 빈 배열")
    if n == 1:
        return sorted_vals[0]
    pos = (pct / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def a_weighted_levels(frame_sums: Sequence[Sequence[float]], frame_len: int,
                      fs: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """A-가중 (LAeq, LAmax, 다이내믹 레인지) — 전부 **dBFS 기준**입니다.

    * LAeq  : 전체 구간의 A-가중 등가레벨 (채널 에너지 평균)
    * LAmax : 100 ms 등가레벨의 최댓값.
              **근사 고지**: IEC 61672 의 Fast(τ = 125 ms) 지수시간가중이 아니라
              100 ms 직사각 창의 최댓값입니다. 지수가중보다 약간 크게 나옵니다.
    * DR    : 100 ms 레벨의 95 백분위 − 5 백분위 (Table 2 의 "dynamic range").
              **암소음/디지털 무음 구간은 제외합니다** — 앞뒤에 무음이 붙은 파일에서
              p5 가 −300 dB 로 내려가 다이내믹 레인지가 수천 dB 로 튀는 것을 막습니다.
              제외 기준은 `DR_FLOOR_BELOW_MAX` (LAmax 아래 60 dB)입니다.

    **절대 SPL(dB SPL / dB HL)이 아닙니다.** 재생 체인 보정 없이는 알 수 없습니다.
    """
    nch = len(frame_sums)
    if nch == 0 or not frame_sums[0]:
        return None, None, None
    nframes = len(frame_sums[0])
    per10 = max(1, int(round(0.100 * fs / frame_len)))
    total = 0.0
    for c in range(nch):
        total += sum(frame_sums[c])
    mean_pow = total / (nch * nframes * frame_len)
    laeq = _db(mean_pow) if mean_pow > 0 else None
    win_levels: List[float] = []
    start = 0
    while start + per10 <= nframes:
        acc = 0.0
        for c in range(nch):
            acc += sum(frame_sums[c][start:start + per10])
        p = acc / (nch * per10 * frame_len)
        if p > 0:
            win_levels.append(_db(p))
        start += per10
    if not win_levels:
        return laeq, None, None
    lamax = max(win_levels)
    floor = lamax - DR_FLOOR_BELOW_MAX
    sounding = sorted(v for v in win_levels if v >= floor)
    if len(sounding) < 2:
        return laeq, lamax, 0.0
    dr = percentile(sounding, 95.0) - percentile(sounding, 5.0)
    return laeq, lamax, dr


# ------------------------------------------------------------ 트루피크

#: 보간 창의 반폭(샘플). 창 전체 길이는 2*_TAP_HALF.
_TAP_HALF = 16
#: 표본점 사이를 4분할해 앞뒤 1샘플 범위(−1 … +0.75)를 훑습니다.
_FRACTIONS = [k / 4.0 for k in range(-4, 4)]


def _sinc_phase_taps(frac: float, half: int) -> List[float]:
    """중심에서 `frac` 샘플만큼 떨어진 지점을 복원하는 창 씌운 sinc 계수.

    BS.1770-4 Annex 2 는 4배 오버샘플용 계수표를 싣고 있지만 임의의 fs 로
    쓰려면 재유도가 필요합니다. 여기서는 Hann 창 sinc 로 **근사**하고,
    리포트에서 반드시 '근사'라고 표시합니다.
    """
    taps = []
    n = 2 * half
    for i in range(n):
        x = (i - half) - frac
        s = 1.0 if abs(x) < 1e-12 else math.sin(math.pi * x) / (math.pi * x)
        w = 0.5 - 0.5 * math.cos(2.0 * math.pi * (i / n))
        taps.append(s * w)
    # DC 이득을 1 로 정규화합니다. 창이 **창 중심**에 걸려 있고 평가점은 그보다
    # `frac` 만큼 떨어져 있어서, 정규화하지 않으면 위상마다 계수 합이 0.990~1.000
    # 으로 달라집니다. 그 차이는 주파수에 평평한 순수 이득 손실이라 트루피크를
    # 최대 0.16 dB 과소평가하고, −1.0 dBTP 경고 경계를 뒤집을 수 있습니다.
    total = sum(taps)
    if total != 0.0:
        taps = [t / total for t in taps]
    return taps


_PHASES = [_sinc_phase_taps(f, _TAP_HALF) for f in _FRACTIONS]


def interpolated_peak(window: Sequence[float]) -> float:
    """길이 2*_TAP_HALF 인 창의 중심 ±1 샘플 구간을 4분할해 최대 진폭을 구합니다.

    창의 인덱스 _TAP_HALF 가 표본 최댓점입니다. 대역제한 신호의 표본 사이
    오버슈트는 표본 극값 바로 옆에서만 나타나므로 이 범위로 충분합니다.
    표본값 자체도 후보에 넣으므로 **결과는 표본 피크 이상**입니다.

    검증: 클리핑된 실물 자산(S1_SO-CLAS.wav)에서 `ffmpeg -af ebur128=peak=true`
    의 +1.5 dBTP 와 0.05 dB 안에서 일치했습니다(`HARDENING.md` 기록).
    """
    best = 0.0
    for v in window:
        a = v if v >= 0 else -v
        if a > best:
            best = a
    if len(window) != 2 * _TAP_HALF:
        return best
    for taps in _PHASES:
        acc = 0.0
        for t, v in zip(taps, window):
            acc += t * v
        a = acc if acc >= 0 else -acc
        if a > best:
            best = a
    return best


def dbfs(amplitude: Optional[float]) -> Optional[float]:
    """선형 진폭 → dBFS. 0 이나 None 은 None."""
    if amplitude is None or amplitude <= 0.0:
        return None
    return 20.0 * math.log10(amplitude)
