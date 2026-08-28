"""가중 필터가 **규격 자체와** 맞는지 — 이 툴의 모든 숫자가 여기 걸려 있습니다.

이 파일이 실패하면 LUFS·LAeq 가 조용히 밀리고, 그러면 조건 간 음량 판정이
전부 거짓 정밀도가 됩니다. 빌드 중 실제로 RBJ 선반 공식을 쓰다가 1 kHz 에서
0.26 dB 어긋나 모든 LUFS 가 0.25 LU 밀린 적이 있습니다 — 그 회귀를 여기서 잡습니다.
"""
from __future__ import annotations

import math

import pytest

from stimaudit.dsp import sos_freq_response_db
from stimaudit.filters import (IEC_61672_A_WEIGHTING_DB, a_weighting_sos,
                               k_weighting_sos)

#: ITU-R BS.1770-4 본문 표의 48 kHz 계수 (검증 기준).
ITU_48K = [
    (1.53512485958697, -2.69169618940638, 1.19839281085285,
     -1.69065929318241, 0.73248077421585),
    (1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621),
]


def test_k_weighting_matches_itu_table_at_48k():
    """BS.1770-4 이 싣고 있는 48 kHz 계수표와 소수 12자리까지 일치해야 합니다."""
    sos = k_weighting_sos(48000.0)
    assert len(sos) == 2
    for got, want in zip(sos, ITU_48K):
        for g, w in zip(got, want):
            assert g == pytest.approx(w, abs=1e-12)


def test_k_weighting_shelf_gain_is_about_4db():
    """1단계는 +4 dB 고역선반입니다."""
    shelf = [k_weighting_sos(48000.0)[0]]
    assert sos_freq_response_db(shelf, 20000.0, 48000.0) == pytest.approx(3.99, abs=0.1)
    assert sos_freq_response_db(shelf, 20.0, 48000.0) == pytest.approx(0.0, abs=0.02)


def test_k_weighting_1khz_gain():
    """1 kHz 에서 +0.6977 dB — 이 값이 −0.691 오프셋과 짝을 이뤄 교정점을 만듭니다."""
    assert sos_freq_response_db(k_weighting_sos(48000.0), 1000.0, 48000.0) == \
        pytest.approx(0.697704, abs=1e-5)


def test_k_weighting_highpass_blocks_dc():
    hp = [k_weighting_sos(48000.0)[1]]
    assert sos_freq_response_db(hp, 0.01, 48000.0) < -100.0


@pytest.mark.parametrize("fs", [44100.0, 48000.0, 88200.0, 96000.0])
def test_k_weighting_derives_at_any_rate(fs):
    """48 kHz 표를 하드코딩하지 않았음을 확인 — 다른 fs 에서도 형태가 유지됩니다."""
    sos = k_weighting_sos(fs)
    assert sos_freq_response_db(sos, 1000.0, fs) == pytest.approx(0.6977, abs=0.02)
    assert sos_freq_response_db(sos, 20.0, fs) < -10.0


def test_k_weighting_is_cached_per_rate():
    assert k_weighting_sos(48000.0) is k_weighting_sos(48000.0)
    assert k_weighting_sos(44100.0) is not k_weighting_sos(48000.0)


@pytest.mark.parametrize("freq,spec", sorted(IEC_61672_A_WEIGHTING_DB.items()))
def test_a_weighting_matches_iec_61672_up_to_4khz(freq, spec):
    """IEC 61672-1 규격값 대조. 4 kHz 까지는 0.15 dB 안, 그 위는 쌍선형 왜곡이 커집니다."""
    fs = 48000.0
    got = sos_freq_response_db(a_weighting_sos(fs), freq, fs)
    tol = 0.15 if freq <= 4000.0 else 1.6
    assert got == pytest.approx(spec, abs=tol)


def test_a_weighting_is_exactly_zero_at_1khz():
    """정의상 1 kHz 는 0 dB — 이산영역에서 재정규화했음을 고정합니다."""
    for fs in (44100.0, 48000.0, 96000.0):
        assert sos_freq_response_db(a_weighting_sos(fs), 1000.0, fs) == \
            pytest.approx(0.0, abs=1e-9)


def test_a_weighting_high_frequency_deviation_is_documented():
    """10 kHz 편차는 IEC 61672 class 1 허용범위(+2.0/−3.0 dB) 안에 있어야 합니다."""
    for fs in (44100.0, 48000.0):
        got = sos_freq_response_db(a_weighting_sos(fs), 10000.0, fs)
        dev = got - IEC_61672_A_WEIGHTING_DB[10000.0]
        assert -3.0 < dev < 2.0
        assert dev < 0.0          # 쌍선형 변환은 나이키스트 근처를 눌러 과소평가합니다


def test_a_weighting_shape_is_bandpass():
    """저역과 고역이 모두 깎여야 합니다(첫 구현은 저역통과 모양이라 20 dB 이상 틀렸습니다)."""
    fs = 48000.0
    sos = a_weighting_sos(fs)
    assert sos_freq_response_db(sos, 20.0, fs) < -45.0
    assert sos_freq_response_db(sos, 2500.0, fs) > 0.0
    assert sos_freq_response_db(sos, 16000.0, fs) < -5.0


def test_a_weighting_has_three_sections():
    assert len(a_weighting_sos(48000.0)) == 3


def test_a_weighting_cached():
    assert a_weighting_sos(44100.0) is a_weighting_sos(44100.0)
