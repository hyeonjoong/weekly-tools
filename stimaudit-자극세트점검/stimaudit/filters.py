"""가중 필터 두 벌 — 서로 다른 규격, 서로 다른 목적입니다.

* **K-weighting (ITU-R BS.1770-4)** → LUFS. **논문 유래가 아닙니다.**
  조건 간 체감 음량을 맞추는 방송/실험 통제 관행 지표입니다.
* **A-weighting (IEC 61672-1)** → LAeq / LAmax. **논문이 쓰는 단위입니다**
  (Table 2 항목 1 "LAeq + dynamic range"). Czempik et al. (2020) 은 ICU 에서
  LAmax 가 수면시간과 더 강하게 상관(r = −0.64)한다고 보고했고, LAeq20sec 은
  r = −0.50 이었습니다 — 그래서 이 툴은 평균과 최대치를 반드시 나란히 인쇄합니다.

두 필터 모두 임의의 샘플레이트에서 재유도합니다(44.1 k·48 k 를 모두 읽어야 하므로
48 kHz 계수표를 하드코딩할 수 없습니다).

정확도 검증은 `tests/test_filters.py` 가 고정합니다:
  - K-weighting: −20 dBFS 1 kHz 사인 → −20.0 LUFS (BS.1770 정의상 정확히)
  - A-weighting: 1 kHz 에서 정확히 0 dB, 31.5 Hz ~ 4 kHz 는 IEC 61672-1 규격값과
    0.13 dB 이내. 10 kHz 는 −1.2 dB(48 k) / −1.5 dB(44.1 k) 만큼 규격값보다
    덜 감쇠합니다 — 쌍선형 변환의 주파수 왜곡이고 class 1 허용범위(+2.0/−3.0 dB)
    안입니다. 테스트가 이 실측 편차를 그대로 고정합니다.

**샘플레이트 하한**: A-weighting 은 1 kHz 에서 0 dB 가 되도록 이산영역에서
재정규화합니다. 그런데 fs 가 낮아 1 kHz 가 나이퀴스트에 가까워지면 그 지점의
응답이 −400 dB 까지 떨어져 정규화 계수가 10^20 으로 폭주하고, LAeq 가 300 dB
같은 물리적으로 불가능한 값으로 인쇄됩니다(라운드 1, 엣지케이스 파괴자 발견 5).
그래서 `MIN_A_WEIGHTING_FS` 미만에서는 **A-가중 레벨을 아예 계산하지 않고**
커버리지 자백에 "검사 안 함"으로 적습니다.
"""
from __future__ import annotations

import math
from typing import List, Tuple

from .dsp import bilinear_biquad, sos_freq_response_db

Section = Tuple[float, float, float, float, float]

#: A-가중을 계산할 수 있는 최저 샘플레이트 (Hz). 1 kHz 정규화점이 나이퀴스트의
#: 1/4 이하여야 안전합니다. 실제 자극 파일은 늘 44.1 k / 48 k 입니다.
MIN_A_WEIGHTING_FS = 8000.0

# ---------------------------------------------------------------- K-weighting
# BS.1770-4 는 48 kHz 계수표만 싣고 있습니다. 그 표를 정확히 재현하는 아날로그
# 원형 파라미터(고역선반 + 고역통과)를 써서 임의의 fs 로 재유도합니다.
_KW_SHELF_G_DB = 3.999843853973347
_KW_SHELF_Q = 0.7071752369554196
_KW_SHELF_FC = 1681.974450955533
_KW_HPF_Q = 0.5003270373238773
_KW_HPF_FC = 38.13547087602444


def _k_shelf(fs: float) -> Section:
    """BS.1770-4 1단계 — 머리 회절을 흉내 내는 +4 dB 고역선반.

    ITU 는 48 kHz 계수표만 싣습니다. 이 유도식은 그 표를 소수 12자리까지
    정확히 재현하면서 임의의 fs 로 확장됩니다(`tests/test_filters.py` 가
    ITU 표와의 일치를 고정). 일반 RBJ 선반 공식과는 미묘하게 다릅니다 —
    RBJ 를 쓰면 1 kHz 에서 0.26 dB 어긋나고, 그만큼 모든 LUFS 값이 조용히
    밀립니다(빌드 중 실제로 겪은 오차입니다).
    """
    k = math.tan(math.pi * _KW_SHELF_FC / fs)
    vh = 10.0 ** (_KW_SHELF_G_DB / 20.0)
    vb = vh ** 0.4996667741545416
    kk = k * k
    a0 = 1.0 + k / _KW_SHELF_Q + kk
    return (
        (vh + vb * k / _KW_SHELF_Q + kk) / a0,
        2.0 * (kk - vh) / a0,
        (vh - vb * k / _KW_SHELF_Q + kk) / a0,
        2.0 * (kk - 1.0) / a0,
        (1.0 - k / _KW_SHELF_Q + kk) / a0,
    )


def _k_highpass(fs: float) -> Section:
    """BS.1770-4 2단계 — RLB 고역통과. 분자는 규격대로 (1, −2, 1) 입니다."""
    k = math.tan(math.pi * _KW_HPF_FC / fs)
    kk = k * k
    den = 1.0 + k / _KW_HPF_Q + kk
    return (1.0, -2.0, 1.0, 2.0 * (kk - 1.0) / den, (1.0 - k / _KW_HPF_Q + kk) / den)


_K_CACHE: dict = {}


def k_weighting_sos(fs: float) -> List[Section]:
    """BS.1770-4 K-weighting: 1단 고역선반(+4 dB) → 2단 고역통과(RLB)."""
    key = round(float(fs), 6)
    if key not in _K_CACHE:
        _K_CACHE[key] = [_k_shelf(fs), _k_highpass(fs)]
    return _K_CACHE[key]


# ---------------------------------------------------------------- A-weighting
# IEC 61672-1 아날로그 원형: 20.6 Hz 이중극점, 107.7 Hz, 737.9 Hz, 12194 Hz 이중극점.
_AW_F1 = 20.598997
_AW_F2 = 107.65265
_AW_F3 = 737.86223
_AW_F4 = 12194.217

_A_CACHE: dict = {}


def a_weighting_sos(fs: float) -> List[Section]:
    """IEC 61672-1 A-weighting 을 쌍선형 변환으로 디지털화한 SOS 캐스케이드.

    아날로그 원형 H(s) = w4^2 * s^4 / [(s+w1)^2 (s+w2)(s+w3)(s+w4)^2] 를
    이미 인수분해된 2차 섹션 3개로 나눠 각각 변환합니다:

        A:  s^2 / (s+w1)^2              (20.6 Hz 이중극점 · 저역 차단)
        B:  s^2 / ((s+w2)(s+w3))        (107.7 / 737.9 Hz)
        C:  w4^2 / (s+w4)^2             (12194 Hz 이중극점 · 고역 롤오프)

    그 뒤 1 kHz 에서 정확히 0 dB 가 되도록 **이산영역에서** 게인을 재정규화합니다.
    아날로그 정규화상수(A1000)를 그대로 쓰면 쌍선형 변환의 주파수 왜곡 때문에
    1 kHz 가 0 dB 를 조금 벗어납니다.

    한계(정직 고지): 쌍선형 변환은 나이키스트 근처를 압축하므로 10 kHz 부근에서
    규격값과 수 dB 어긋납니다. 실제 편차는 `tests/test_filters.py` 가 측정해
    고정하며, README 와 리포트에 그대로 적습니다.
    """
    key = round(float(fs), 6)
    if key in _A_CACHE:
        return _A_CACHE[key]
    w1 = 2.0 * math.pi * _AW_F1
    w2 = 2.0 * math.pi * _AW_F2
    w3 = 2.0 * math.pi * _AW_F3
    w4 = 2.0 * math.pi * _AW_F4
    sos = [
        bilinear_biquad([1.0, 0.0, 0.0], [1.0, 2.0 * w1, w1 * w1], fs),
        bilinear_biquad([1.0, 0.0, 0.0], [1.0, w2 + w3, w2 * w3], fs),
        bilinear_biquad([0.0, 0.0, w4 * w4], [1.0, 2.0 * w4, w4 * w4], fs),
    ]
    g_db = sos_freq_response_db(sos, 1000.0, fs)
    scale = 10.0 ** (-g_db / 20.0)
    b0, b1, b2, a1, a2 = sos[0]
    sos[0] = (b0 * scale, b1 * scale, b2 * scale, a1, a2)
    _A_CACHE[key] = sos
    return sos


# ------------------------------------------------------- 포락선 저역통과
#: 포락선 추출용 저역통과 차단주파수(Hz). 2차 버터워스.
ENVELOPE_LPF_HZ = 40.0

_ENV_CACHE: dict = {}


def envelope_lowpass_sos(fs: float) -> List[Section]:
    """포락선을 뽑기 전에 **제곱 신호**에 거는 2차 버터워스 저역통과.

    왜 필요한가: 신호를 제곱하면 반송주파수의 **2배** 성분이 생깁니다(349 Hz 톤
    → 698 Hz). 이것을 그대로 10 ms 프레임 평균으로 100 Hz 까지 데시메이션하면
    698 Hz 가 |698 − 700| = **2 Hz 로 접혀** 들어와, 변조가 전혀 없는 순수 톤이
    "2.0 Hz (120 BPM) 로 변조됨"으로 보고됩니다(라운드 1 정확성 검토에서 실제로
    발견된 결함 — 210/220/440 Hz 는 20 Hz 로, 349 Hz 는 2 Hz 로 접혔습니다).

    40 Hz 2차 버터워스는 698 Hz 를 약 50 dB 깎고, 프레임 평균의 sinc 응답
    (700 Hz 영점 부근에서 −51 dB)과 합쳐 100 dB 이상 눌러 줍니다. 검사 대역
    상단인 20 Hz 에서의 감쇠는 0.5 dB 미만이라 실제 변조는 그대로 통과합니다.
    """
    key = round(float(fs), 6)
    if key not in _ENV_CACHE:
        w0 = 2.0 * math.pi * ENVELOPE_LPF_HZ
        q = math.sqrt(0.5)                     # 버터워스
        _ENV_CACHE[key] = [bilinear_biquad(
            [0.0, 0.0, w0 * w0], [1.0, w0 / q, w0 * w0], fs)]
    return _ENV_CACHE[key]


# IEC 61672-1 표 3 의 A-가중 값(규격값, dB). 테스트 대조용.
IEC_61672_A_WEIGHTING_DB = {
    31.5: -39.4,
    63.0: -26.2,
    125.0: -16.1,
    250.0: -8.6,
    500.0: -3.2,
    1000.0: 0.0,
    2000.0: 1.2,
    4000.0: 1.0,
    8000.0: -1.1,
    100.0: -19.1,
    10000.0: -2.5,
}
