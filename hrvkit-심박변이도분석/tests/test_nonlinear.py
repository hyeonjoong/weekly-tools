"""비선형 지표 — Poincaré 항등식과 SampEn 교차검증."""

import math
import random

import pytest

from hrvkit.nonlinear import poincare, sample_entropy
from hrvkit.timedomain import time_domain

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover
    HAVE_NUMPY = False

SERIES = [800.0, 900.0, 780.0, 810.0, 760.0]


def test_sd1_equals_sdsd_over_sqrt2():
    """정의상 SD1 = SDSD/√2 (대수 항등식)."""
    p = poincare(SERIES)
    sdsd = time_domain(SERIES)["sdsd"]
    assert p["sd1"] == pytest.approx(sdsd / math.sqrt(2.0), rel=1e-12)


def test_sd2_and_ratio_and_area():
    p = poincare(SERIES)
    # 손 계산: sd2 = sqrt(6250/3) ≈ 45.6435
    assert p["sd2"] == pytest.approx(math.sqrt(6250 / 3), rel=1e-9)
    assert p["sd1_sd2_ratio"] == pytest.approx(p["sd1"] / p["sd2"], rel=1e-12)
    assert p["ellipse_area"] == pytest.approx(math.pi * p["sd1"] * p["sd2"], rel=1e-12)


def test_poincare_needs_three():
    with pytest.raises(ValueError):
        poincare([800, 810])


def test_sampen_regular_less_than_random():
    # 규칙적(사인) 신호가 잡음보다 SampEn이 작아야 함
    reg = [math.sin(2 * math.pi * 0.1 * i) for i in range(200)]
    rng = random.Random(0)
    noise = [rng.gauss(0, 1) for _ in range(200)]
    assert sample_entropy(reg) < sample_entropy(noise)


def test_sampen_zero_variance():
    assert sample_entropy([800.0] * 50) == pytest.approx(0.0)


def test_sampen_short_series_nan():
    assert math.isnan(sample_entropy([1.0, 2.0]))


@pytest.mark.skipif(not HAVE_NUMPY, reason="numpy 없음")
def test_sampen_matches_reference():
    rng = random.Random(99)
    u = [rng.gauss(0, 1) for _ in range(120)]
    m, r = 2, 0.2 * _np.std(u, ddof=1)

    def ref(u, m, r):
        arr = _np.asarray(u, float)
        N = len(arr)

        def phi(mm):
            tmpl = _np.array([arr[i:i + mm] for i in range(N - m)])
            c = 0
            for i in range(len(tmpl)):
                d = _np.max(_np.abs(tmpl - tmpl[i]), axis=1)
                c += int(_np.sum(d <= r)) - 1  # 자기자신 제외 (ordered pairs)
            return c
        return -math.log(phi(m + 1) / phi(m))

    mine = sample_entropy(u, m=m, r=r)
    assert mine == pytest.approx(ref(u, m, r), rel=1e-9, abs=1e-9)
