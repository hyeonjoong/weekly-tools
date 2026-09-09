import math

import pytest

from rsalink.breath import Breath
from rsalink.pace import pace_analysis


def seq(rates, flags=None, t_start=0.0):
    """호흡수 열(회/분) → 연속 Breath 목록 (주기 = 60/rate)."""
    out, t = [], t_start
    for i, r in enumerate(rates):
        p = 60.0 / r
        b = Breath(idx=i, t_onset=t, t_peak=t + 0.4 * p, t_end=t + p, prominence=1.0,
                   flag=(flags[i] if flags else ""))
        out.append(b)
        t += p
    return out


def test_pct_and_first_entrainment_hand():
    # 목표 6/분, ±10% → [5.4, 6.6]
    # 12, 12, 6.2, 7.0, 6.0, 6.1, 5.8, 6.4, 6.0, 6.0  → 안: 6.2,6.0,6.1,5.8,6.4,6.0,6.0 = 7/10
    # 연속 5: 인덱스 4..9 (6.0,6.1,5.8,6.4,6.0,6.0) → 첫 동조 = 4번째 호흡의 onset
    rates = [12, 12, 6.2, 7.0, 6.0, 6.1, 5.8, 6.4, 6.0, 6.0]
    br = seq(rates)
    r = pace_analysis(br, 6.0)
    assert r.n_breaths == 10 and r.n_within == 7
    assert r.pct_within == pytest.approx(70.0)
    # onset of breath 4 = 5 + 5 + 60/6.2 + 60/7.0
    assert r.first_entrain_t == pytest.approx(5 + 5 + 60 / 6.2 + 60 / 7.0)
    assert r.n_entrained == 6
    assert r.mean_entrained == pytest.approx((6.0 + 6.1 + 5.8 + 6.4 + 6.0 + 6.0) / 6)
    m = r.mean_entrained
    sd = math.sqrt(sum((x - m) ** 2 for x in [6.0, 6.1, 5.8, 6.4, 6.0, 6.0]) / 5)
    assert r.sd_entrained == pytest.approx(sd)


def test_no_entrainment_when_runs_short():
    rates = [6, 6, 6, 6, 12, 6, 6, 6, 6, 12]  # 최대 연속 4
    r = pace_analysis(seq(rates), 6.0)
    assert r.n_within == 8 and r.first_entrain_t is None
    assert r.n_entrained == 0 and math.isnan(r.sd_entrained)


def test_artifact_breaks_run_and_counts_in_denominator():
    rates = [6, 6, 6, 6, 6, 6, 6]
    flags = ["", "", "", "low_prom", "", "", ""]
    r = pace_analysis(seq(rates, flags), 6.0)
    assert r.n_breaths == 7 and r.n_within == 6
    assert r.first_entrain_t is None  # 3 + 3 → 5 연속 없음


def test_segment_window():
    rates = [12] * 5 + [6] * 6
    br = seq(rates)
    t_stim = br[5].t_onset
    r = pace_analysis(br, 6.0, t0=t_stim)
    assert r.n_breaths == 6 and r.pct_within == 100.0 and r.first_entrain_t == t_stim
    r0 = pace_analysis(br, 6.0, t1=t_stim)
    assert r0.n_within == 0
