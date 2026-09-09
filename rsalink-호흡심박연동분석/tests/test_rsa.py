import math

import pytest

from rsalink.parse import RespWaveform, RRSeries, TimeInfo
from rsalink.breath import detect_breaths, Breath, BreathResult
from rsalink.rsa import rsa_per_breath, summarize


def make_resp(f, dur=120.0, fs=10.0):
    t = [i / fs for i in range(int(dur * fs))]
    v = [math.sin(2 * math.pi * f * x) for x in t]
    return RespWaveform(t=t, v=v, fs_est=fs, irregular_frac=0.0, n_gaps=0, gap_total_s=0.0,
                        dup_ts=0, time=TimeInfo("seconds", None))


def make_rr(f, A, phi, mean=500.0, dur=120.0):
    """RR_i = mean + A·sin(2πf·t_i + φ), t_i = 그 RR 이 끝나는 박동 시각(툴의 규약).
    t_i 는 자기 자신에 의존하므로 고정점 반복 3회로 푼다. mean 500 ms 면 박동 이산화로
    극값을 놓치는 오차가 극값당 < 0.5 ms (A=40, f=0.1) → RSA_pv 오차 < 1 ms."""
    rr, tb = [], []
    t = 0.0
    while t < dur:
        x = mean
        for _ in range(3):
            x = mean + A * math.sin(2 * math.pi * f * (t + x / 1000.0) + phi)
        t += x / 1000.0
        rr.append(x)
        tb.append(t)
    return RRSeries(rr_ms=rr, t_beat=tb, unit_detected="ms", n_total=len(rr), n_excluded=0,
                    excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))


# 호흡 sin(2π·0.1·t): 흡기 시작(골) 7.5+10k, 흡기 끝(봉우리) 12.5+10k.
# 생리적 정렬 = RR 최소(심박 최고)가 흡기 끝 근처, RR 최대가 호기 끝 근처 → φ ≈ π.
# φ = π 정확히면 극값이 창 경계(제외 경계) 위에 놓여 한쪽 박동만 세어지므로
# 0.3 / 0.45 / 0.6 rad(= 0.48 / 0.72 / 0.95 s 앞당김) 의 "정렬 어긋남" 세 가지로 검사.
# 박동 간격 0.5 s → 극값에서 가장 가까운 박동은 ±0.25 s 안 → 극값당 오차 ≤ 0.49 ms.
@pytest.mark.parametrize("phi", [math.pi + 0.3, math.pi + 0.45, math.pi + 0.6])
def test_rsa_pv_equals_2A(phi):
    A = 40.0
    br = detect_breaths(make_resp(0.1))
    rows = rsa_per_breath(br, make_rr(0.1, A, phi))
    vals = [r.rsa_pv for r in rows if r.rsa_pv is not None]
    assert len(vals) >= 10
    s = summarize(rows)
    assert abs(s["median"] - 2 * A) < 1.0          # 80 ±1 ms
    assert all(abs(v - 2 * A) < 1.0 for v in vals)  # 호흡별로도
    assert s["n_undetermined"] == 0
    assert abs(s["beats_per_breath"] - 20.0) < 0.6  # 10 s / 0.5 s


def test_rsa_pv_is_phase_sensitive_anti_phase():
    # φ = π/2: RR 최대가 흡기 창 한가운데(t=10), 최소가 호기 창 한가운데(t=15) —
    # 생리와 반대 → 창 안 peak-valley 는 2A 를 내지 않는다(≈0).
    # 툴은 이걸 "정렬"로 고치지 않는다(--resp-offset 은 사람이 넣는다).
    A = 40.0
    br = detect_breaths(make_resp(0.1))
    s = summarize(rsa_per_breath(br, make_rr(0.1, A, math.pi / 2)))
    assert s["median"] < 0.25 * A


def test_few_beats_undetermined():
    # 2 초 호흡(30/분), RR 1000 ms → 호흡당 2 박동 → 판정 불가
    b = Breath(idx=0, t_onset=0.0, t_peak=0.8, t_end=2.0, prominence=1.0)
    br = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                      max_breath_s=15.0, source="events", peak_assumed=True)
    rr = RRSeries(rr_ms=[1000.0, 1000.0, 1000.0], t_beat=[1.0, 2.0, 3.0], unit_detected="ms",
                  n_total=3, n_excluded=0, excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))
    rows = rsa_per_breath(br, rr)
    assert rows[0].rsa_pv is None and rows[0].reason == "few_beats"
    s = summarize(rows)
    assert s["n"] == 0 and s["n_undetermined"] == 1 and math.isnan(s["median"])


def test_hand_values():
    # 흡기 [0,2): RR 900, 950 / 호기 [2,5): 1000, 1100, 1050 → RSA_pv = 1100 − 900 = 200
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=1.0)
    br = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                      max_breath_s=15.0, source="events", peak_assumed=True)
    rr = RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0, 1050.0], t_beat=[0.9, 1.85, 2.85, 3.95, 5.0 - 1e-9],
                  unit_detected="ms", n_total=5, n_excluded=0, excluded_frac=0.0, col_used="rr",
                  time=TimeInfo("seconds", None))
    r = rsa_per_breath(br, rr)[0]
    assert r.n_insp == 2 and r.n_exp == 3 and r.rsa_pv == 200.0
    assert r.rr_max_exp == 1100.0 and r.rr_min_insp == 900.0


def test_resp_offset_shifts_windows():
    # 위와 같은 RR, 호흡 창을 +1 s 밀면 흡기 [1,3): 950,1000 / 호기 [3,6): 1100,1050 → 150
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=1.0)
    br = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                      max_breath_s=15.0, source="events", peak_assumed=True)
    rr = RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0, 1050.0], t_beat=[0.9, 1.85, 2.85, 3.95, 5.0 - 1e-9],
                  unit_detected="ms", n_total=5, n_excluded=0, excluded_frac=0.0, col_used="rr",
                  time=TimeInfo("seconds", None))
    r = rsa_per_breath(br, rr, resp_offset=1.0)[0]
    assert r.rsa_pv == 150.0


def test_artifact_breath_skipped():
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=0.0, flag="low_prom")
    br = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                      max_breath_s=15.0, source="events")
    rr = RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0], t_beat=[0.9, 1.85, 2.85, 3.95], unit_detected="ms",
                  n_total=4, n_excluded=0, excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))
    r = rsa_per_breath(br, rr)[0]
    assert r.rsa_pv is None and r.reason == "artifact"
