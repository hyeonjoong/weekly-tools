"""DFA(Detrended Fluctuation Analysis) — 스케일링 지수 속성 검증.

이론적 기대: 백색잡음 α≈0.5, 적분(브라운) 잡음 α≈1.5.
numpy 가 있으면 독립 참조 구현과 교차검증, 없어도 속성 검사만으로 통과.
"""

import math
import random

import pytest

from hrvkit.nonlinear import dfa, dfa_alpha, dfa_fluctuations, _detrend_rms_sq

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover
    HAVE_NUMPY = False


def test_detrend_removes_linear_trend_exactly():
    # 완전한 직선 → 잔차 0
    seg = [3.0 + 2.0 * t for t in range(10)]
    assert _detrend_rms_sq(seg) == pytest.approx(0.0, abs=1e-9)


def test_detrend_short_segment_zero():
    assert _detrend_rms_sq([5.0]) == 0.0


def test_white_noise_alpha_near_half():
    rng = random.Random(2024)
    x = [rng.gauss(0, 1) for _ in range(3000)]
    a = dfa_alpha(x, 4, 64)
    assert 0.4 < a < 0.7  # 소구간 편향으로 0.5보다 약간 큼


def test_integrated_noise_alpha_near_one_and_half():
    rng = random.Random(11)
    walk, acc = [], 0.0
    for _ in range(3000):
        acc += rng.gauss(0, 1)
        walk.append(acc)
    a = dfa_alpha(walk, 4, 64)
    assert 1.3 < a < 1.7


def test_alpha_increases_with_correlation():
    rng = random.Random(7)
    white = [rng.gauss(0, 1) for _ in range(2000)]
    walk, acc = [], 0.0
    for _ in range(2000):
        acc += rng.gauss(0, 1)
        walk.append(acc)
    assert dfa_alpha(white, 4, 32) < dfa_alpha(walk, 4, 32)


def test_dfa_short_series_alpha2_nan_alpha1_finite():
    # 20박동: α1(4–16)은 계산 가능(유한), α2(16–64)는 데이터 부족 → NaN
    d = dfa([800.0 + 5 * math.sin(i) for i in range(20)])
    assert math.isfinite(d["dfa_alpha1"])
    assert math.isnan(d["dfa_alpha2"])


def test_dfa_alpha1_nan_when_too_short_for_two_scales():
    # 4박동: 4–16 스케일 중 유효 스케일은 n=4 하나뿐 → 회귀 불가 → α1 NaN
    d = dfa([800.0, 810.0, 790.0, 805.0])
    assert math.isnan(d["dfa_alpha1"])


def test_dfa_alpha2_finite_and_near_one_and_half_for_walk():
    rng = random.Random(5)
    walk, acc = [], 0.0
    for _ in range(3000):
        acc += rng.gauss(0, 1)
        walk.append(acc)
    d = dfa(walk)
    assert math.isfinite(d["dfa_alpha2"])
    assert 1.3 < d["dfa_alpha2"] < 1.7


def test_dfa_alpha2_gating_boundary():
    # α2 는 n >= 2*long_range[1] (기본 128) 에서만 유한.
    base = [800.0 + 3 * math.sin(i / 2.0) for i in range(200)]
    assert math.isnan(dfa(base[:127])["dfa_alpha2"])
    assert math.isfinite(dfa(base[:128])["dfa_alpha2"])


def test_dfa_alpha_invalid_range_nan():
    assert math.isnan(dfa_alpha([1.0] * 100, 20, 10))


def test_fluctuations_monotone_for_walk():
    # 브라운 운동은 F(n) 이 n 에 따라 대체로 증가
    rng = random.Random(3)
    walk, acc = [], 0.0
    for _ in range(2000):
        acc += rng.gauss(0, 1)
        walk.append(acc)
    fl = dfa_fluctuations(walk, list(range(4, 40)))
    fs = [f for _, f in fl]
    assert fs[-1] > fs[0]


@pytest.mark.skipif(not HAVE_NUMPY, reason="numpy 없음")
def test_dfa_matches_numpy_reference():
    """독립 numpy 참조(양방향 구간, 선형 detrend)와 α 일치."""
    rng = random.Random(42)
    x = [rng.gauss(0, 1) for _ in range(1500)]
    scales = list(range(4, 40))

    def ref_alpha(x, scales):
        arr = _np.asarray(x, float)
        N = len(arr)
        prof = _np.cumsum(arr - arr.mean())
        logn, logf = [], []
        for n in scales:
            nb = N // n
            if nb < 1:
                continue
            segs = []
            for b in range(nb):
                segs.append(prof[b * n:(b + 1) * n])
            if N % n != 0:
                for b in range(nb):
                    segs.append(prof[N - (b + 1) * n:N - b * n])
            t = _np.arange(n)
            ss = 0.0
            used = 0
            for s in segs:
                c = _np.polyfit(t, s, 1)
                fit = _np.polyval(c, t)
                ss += float(_np.sum((s - fit) ** 2))
                used += n
            f = math.sqrt(ss / used)
            if f > 0:
                logn.append(math.log(n))
                logf.append(math.log(f))
        A = _np.polyfit(logn, logf, 1)
        return A[0]

    mine = dfa_alpha(x, 4, 39)
    ref = ref_alpha(x, scales)
    assert mine == pytest.approx(ref, rel=1e-6, abs=1e-6)
