"""신호처리 원시연산 — 표준 라이브러리만 사용합니다.

이 모듈이 담는 것은 네 가지뿐입니다.

1. `rfft_mag2` — 반복형 radix-2 FFT (실수 입력의 파워 스펙트럼). 외부 툴 폴더를
   import 하지 않기 위해 직접 구현했습니다(`eegband`·`hrvkit` 전례).
2. `biquad_block` — 2차 IIR 섹션을 블록 단위로 돌리는 상태보존 필터.
   K-weighting(BS.1770)·A-weighting(IEC 61672) 양쪽이 이 위에 올라갑니다.
3. `bilinear` — 아날로그 전달함수 → 디지털 전달함수(쌍선형 변환).
   A-weighting 은 규격이 아날로그 극점으로 정의돼 있어 이것이 필요합니다.
4. `sos_freq_response_db` — 구현한 필터가 규격값과 맞는지 테스트에서 대조하기
   위한 주파수 응답 계산기.

정밀도 주의: 모든 계산은 float(배정도)입니다. 상태변수는 섹션별로 유지되므로
블록 경계에서 값이 튀지 않습니다(테스트 `test_dsp.py` 가 블록분할 불변성을 고정).
"""
from __future__ import annotations

import cmath
import math
from typing import List, Sequence, Tuple

# ---------------------------------------------------------------- FFT


def next_pow2(n: int) -> int:
    """n 이상인 가장 작은 2의 거듭제곱."""
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _fft_inplace(re: List[float], im: List[float]) -> None:
    """반복형 radix-2 Cooley–Tukey FFT (제자리 계산). len 은 2의 거듭제곱."""
    n = len(re)
    if n <= 1:
        return
    # 비트 역순 재배열
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    # 버터플라이
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wr_step = math.cos(ang)
        wi_step = math.sin(ang)
        half = length >> 1
        for i in range(0, n, length):
            wr, wi = 1.0, 0.0
            for k in range(i, i + half):
                k2 = k + half
                xr = re[k2] * wr - im[k2] * wi
                xi = re[k2] * wi + im[k2] * wr
                re[k2] = re[k] - xr
                im[k2] = im[k] - xi
                re[k] += xr
                im[k] += xi
                wr, wi = wr * wr_step - wi * wi_step, wr * wi_step + wi * wr_step
        length <<= 1


def rfft_mag2(x: Sequence[float]) -> List[float]:
    """실수 신호의 단측 파워 스펙트럼 |X[k]|^2 (k = 0 .. N/2).

    입력 길이는 2의 거듭제곱이어야 합니다(호출부에서 창함수 적용 후 제로패딩).
    반환 길이는 N//2 + 1.
    """
    n = len(x)
    if n == 0:
        return []
    if n & (n - 1):
        raise ValueError("rfft_mag2: 길이가 2의 거듭제곱이어야 합니다")
    re = list(map(float, x))
    im = [0.0] * n
    _fft_inplace(re, im)
    half = n // 2
    return [re[k] * re[k] + im[k] * im[k] for k in range(half + 1)]


def hann(n: int) -> List[float]:
    """주기형(periodic) Hann 창 — 스펙트럼 누설 억제용."""
    if n <= 1:
        return [1.0] * max(n, 0)
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n) for i in range(n)]


def parabolic_peak(mag2: Sequence[float], k: int) -> float:
    """이산 피크 인덱스 k 주변에서 포물선 보간한 실수 인덱스.

    로그 파워 3점 보간. 경계에서는 k 를 그대로 돌려줍니다.
    """
    if k <= 0 or k >= len(mag2) - 1:
        return float(k)
    eps = 1e-300
    a = math.log(mag2[k - 1] + eps)
    b = math.log(mag2[k] + eps)
    c = math.log(mag2[k + 1] + eps)
    denom = a - 2.0 * b + c
    if abs(denom) < 1e-15:
        return float(k)
    delta = 0.5 * (a - c) / denom
    if delta < -1.0 or delta > 1.0:
        return float(k)
    return k + delta


# ---------------------------------------------------------------- IIR


def biquad_block(x: Sequence[float], coeffs: Tuple[float, ...], state: List[float]) -> List[float]:
    """전치 직접형 II 2차 섹션. coeffs = (b0, b1, b2, a1, a2), a0 는 1 로 정규화.

    `state` 는 [s1, s2] 로 호출자가 보관합니다(블록 간 연속성). 제자리 갱신됩니다.
    """
    b0, b1, b2, a1, a2 = coeffs
    s1, s2 = state[0], state[1]
    out = [0.0] * len(x)
    i = 0
    for v in x:
        y = b0 * v + s1
        s1 = b1 * v - a1 * y + s2
        s2 = b2 * v - a2 * y
        out[i] = y
        i += 1
    state[0] = s1
    state[1] = s2
    return out


def sos_block(x: Sequence[float], sos: Sequence[Tuple[float, ...]], states: List[List[float]]) -> List[float]:
    """2차 섹션 캐스케이드를 블록에 적용합니다(섹션마다 상태 보관)."""
    y = list(x)
    for i, sec in enumerate(sos):
        y = biquad_block(y, sec, states[i])
    return y


def new_states(sos: Sequence[Tuple[float, ...]]) -> List[List[float]]:
    """캐스케이드용 0 초기화 상태 배열."""
    return [[0.0, 0.0] for _ in sos]


def primed_states(sos: Sequence[Tuple[float, ...]], value: float) -> List[List[float]]:
    """입력이 `value` 로 계속 들어온 것처럼 정상상태로 초기화합니다.

    0 초기화로 시작하면 필터가 첫 수십 ms 동안 0에서 차오르는데, 그 과도응답이
    포락선 앞머리에 없던 저주파 성분을 만듭니다. 포락선 저역통과처럼 **DC 를
    통과시켜야 하는** 필터에서는 반드시 프라이밍해야 합니다.
    """
    states = []
    v = value
    for b0, b1, b2, a1, a2 in sos:
        gain = (b0 + b1 + b2) / (1.0 + a1 + a2)
        y = v * gain
        states.append([y - b0 * v, b2 * v - a2 * y])
        v = y
    return states


def sos_freq_response_db(sos: Sequence[Tuple[float, ...]], f: float, fs: float) -> float:
    """캐스케이드의 f Hz 에서의 크기 응답(dB). 테스트에서 규격값 대조에 씁니다."""
    z = cmath.exp(-2j * math.pi * f / fs)
    h = 1.0 + 0j
    for b0, b1, b2, a1, a2 in sos:
        num = b0 + b1 * z + b2 * z * z
        den = 1.0 + a1 * z + a2 * z * z
        h *= num / den
    mag = abs(h)
    if mag <= 0.0:
        return -400.0
    return 20.0 * math.log10(mag)


# ---------------------------------------------------------- 쌍선형 변환


def bilinear_biquad(num_s: Sequence[float], den_s: Sequence[float], fs: float) -> Tuple[float, ...]:
    """아날로그 2차 섹션 → 디지털 2차 섹션. s = 2*fs*(1-z^-1)/(1+z^-1).

    `num_s`, `den_s` 는 s 의 내림차순 계수 3개씩: [n2, n1, n0] / [d2, d1, d0].
    반환은 `biquad_block` 이 받는 (b0, b1, b2, a1, a2) 로, a0 정규화돼 있습니다.

    고차 필터를 하나의 다항식으로 변환한 뒤 근을 찾아 분해하는 방식은 극점이
    z=1 근처(A-가중의 20.6 Hz 이중극점)일 때 수치적으로 무너집니다. 그래서
    아날로그 단계에서 이미 인수분해된 2차 섹션을 **각각** 변환합니다 —
    쌍선형 변환은 등각사상이라 인수분해와 교환됩니다.
    """
    n2, n1, n0 = num_s
    d2, d1, d0 = den_s
    c = 2.0 * fs
    cc = c * c
    b = [n2 * cc + n1 * c + n0, -2.0 * n2 * cc + 2.0 * n0, n2 * cc - n1 * c + n0]
    a = [d2 * cc + d1 * c + d0, -2.0 * d2 * cc + 2.0 * d0, d2 * cc - d1 * c + d0]
    if a[0] == 0.0:
        raise ValueError("bilinear_biquad: 분모 선두 계수가 0 입니다")
    return (b[0] / a[0], b[1] / a[0], b[2] / a[0], a[1] / a[0], a[2] / a[0])
