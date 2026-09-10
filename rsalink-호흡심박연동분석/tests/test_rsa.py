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
    # 생리와 반대 → lag 0 이면 창 안 peak-valley 는 2A 를 내지 않는다(≈0).
    # 툴은 이걸 "정렬"로 고치지 않는다(--resp-offset 은 사람이 넣는다).
    # 기본 lag(1.0 s = 0.1 주기 + RR/2) 는 창을 뒤로 밀어 반대 위상도 일부 잡는다(측정 45.5 ms = 0.57·2A)
    # — 위상 허용치의 대가이며, 호흡별.csv 의 위상 열로 판별한다.
    A = 40.0
    br = detect_breaths(make_resp(0.1))
    rr = make_rr(0.1, A, math.pi / 2)
    assert summarize(rsa_per_breath(br, rr, lag=0.0))["median"] < 0.25 * A
    assert summarize(rsa_per_breath(br, rr))["median"] < 0.65 * 2 * A


# ---------------------------------------------------------------- 라운드 1 A1: 생리적 지연 τ
# RR_i = m − A·sin(θ(t_i − τ)), θ = 2πf·t, t_i = 끝 박동 시각(고정점). τ = 0 이면 RR 최소가 흡기 끝,
# 최대가 호기 끝(창 경계 위). 라운드 0(끝 박동 배정·lag 0)은 τ 0.5–1.0 s 에서 12/분 0.33→−0.24,
# 15/분 0.07→−0.49 로 붕괴했다. 중점 배정 + lag 1.0 의 실측 회복률은 HARDENING 라운드 1 A1 표.


def make_rr_lag(f, A, tau, mean=500.0, dur=300.0):
    rr, tb, t = [], [], 0.0
    while t < dur:
        x = mean
        for _ in range(4):
            x = mean - A * math.sin(2 * math.pi * f * (t + x / 1000.0 - tau))
        t += x / 1000.0
        rr.append(x)
        tb.append(t)
    return RRSeries(rr_ms=rr, t_beat=tb, unit_detected="ms", n_total=len(rr), n_excluded=0,
                    excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))


@pytest.mark.parametrize("bpm", [12, 15])
@pytest.mark.parametrize("mean_rr", [500.0, 900.0])
@pytest.mark.parametrize("tau, floor", [(0.0, 0.95), (0.5, 0.85), (1.0, 0.85)])
def test_rsa_pv_recovers_with_physiological_lag(bpm, mean_rr, tau, floor):
    A = 40.0
    f = bpm / 60.0
    br = detect_breaths(make_resp(f, dur=300.0))
    s = summarize(rsa_per_breath(br, make_rr_lag(f, A, tau, mean=mean_rr)))
    if tau == 0.0 and mean_rr == 900.0 and bpm == 15:
        floor = 0.90   # 호흡당 4.4 박동의 양측 이산화 바닥(lag 무관, 실측 0.929)
    assert s["n"] >= 40
    assert s["median"] >= floor * 2 * A, (bpm, mean_rr, tau, s["median"] / (2 * A))


def test_rsa_lag_zero_loses_delayed_extremes():
    # 같은 데이터에서 lag 0 은 τ = 1.0 s 를 놓친다(회복 < 0.5) — lag 옵션이 실제로 일하는지 확인
    A, f = 40.0, 0.25
    br = detect_breaths(make_resp(f, dur=300.0))
    rr = make_rr_lag(f, A, 1.0, mean=900.0)
    assert summarize(rsa_per_breath(br, rr, lag=0.0))["median"] < 0.5 * 2 * A
    assert summarize(rsa_per_breath(br, rr))["median"] >= 0.85 * 2 * A


def test_phase_columns_locate_extremes():
    # τ = 0, 6/분: RR 최소는 흡기 끝(위상 0.5), 최대는 호기 끝(위상 1.0) 근처 — 중점 배정이라 RR/2 만큼 앞
    A, f = 40.0, 0.1
    br = detect_breaths(make_resp(f, dur=300.0))
    rows = [r for r in rsa_per_breath(br, make_rr_lag(f, A, 0.0, mean=500.0)) if r.rsa_pv is not None]
    assert rows
    for r in rows:
        assert abs(r.phase_min - 0.5) < 0.08 and abs(r.phase_max - 1.0) < 0.08
    # 아티팩트/판정불가 행은 위상 없음
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=0.0, flag="low_prom")
    br2 = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                       max_breath_s=15.0, source="events")
    rr2 = RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0], t_beat=[0.9, 1.85, 2.85, 3.95], unit_detected="ms",
                   n_total=4, n_excluded=0, excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))
    r = rsa_per_breath(br2, rr2)[0]
    assert r.phase_min is None and r.phase_max is None


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


def _hand_rr():
    # 박동 시각 0.9, 1.85, 2.85, 3.95, 5.0 → 간격 중점 0.45, 1.375, 2.35, 3.4, 4.475
    return RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0, 1050.0], t_beat=[0.9, 1.85, 2.85, 3.95, 5.0 - 1e-9],
                    unit_detected="ms", n_total=5, n_excluded=0, excluded_frac=0.0, col_used="rr",
                    time=TimeInfo("seconds", None))


def _hand_breath():
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=1.0)
    return BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                        max_breath_s=15.0, source="events", peak_assumed=True)


def test_t_mid_is_beat_minus_half_rr():
    assert _hand_rr().t_mid == pytest.approx([0.45, 1.375, 2.35, 3.4, 4.475])


def test_hand_values_lag0():
    # lag 0: 흡기 [0,2) 중점 0.45, 1.375 → 900, 950 / 호기 [2,5) 중점 2.35, 3.4, 4.475 → 1000, 1100, 1050
    # → RSA_pv = 1100 − 900 = 200. 위상: 최소 0.45/5 = 0.09, 최대 3.4/5 = 0.68
    r = rsa_per_breath(_hand_breath(), _hand_rr(), lag=0.0)[0]
    assert r.n_insp == 2 and r.n_exp == 3 and r.rsa_pv == 200.0
    assert r.rr_max_exp == 1100.0 and r.rr_min_insp == 900.0
    assert r.phase_min == pytest.approx(0.09) and r.phase_max == pytest.approx(0.68)


def test_hand_values_default_lag():
    # 기본 lag 1.0: 흡기 [1,3) 중점 1.375, 2.35 → 950, 1000 / 호기 [3,6) 중점 3.4, 4.475 → 1100, 1050 → 150
    r = rsa_per_breath(_hand_breath(), _hand_rr())[0]
    assert r.n_insp == 2 and r.n_exp == 2 and r.rsa_pv == 150.0
    assert r.phase_min == pytest.approx(1.375 / 5) and r.phase_max == pytest.approx(0.68)


def test_resp_offset_shifts_windows():
    # lag 0, 호흡 창을 +1 s 밀면 흡기 [1,3): 950,1000 / 호기 [3,6): 1100,1050 → 150.
    # 위상은 호흡 시계 기준이라 오프셋을 뺀다: 최소 (1.375−1)/5, 최대 (3.4−1)/5
    r = rsa_per_breath(_hand_breath(), _hand_rr(), resp_offset=1.0, lag=0.0)[0]
    assert r.rsa_pv == 150.0
    assert r.phase_min == pytest.approx(0.075) and r.phase_max == pytest.approx(0.48)


def test_resp_offset_docstring_sign_matches_cli():
    # 라운드 2 #6: rsa.py 의 부호 설명이 CLI 도움말과 같은 규약("호흡 기록이 RR 기록보다 늦게 시작했으면 양수")
    from rsalink.cli import build_parser
    doc = rsa_per_breath.__doc__
    assert "늦게 시작했으면 양수" in doc and "앞서면 양수" not in doc
    h = [a.help for a in build_parser()._actions if "--resp-offset" in a.option_strings][0]
    assert "늦게 시작했으면 양수" in h


def test_artifact_breath_skipped():
    b = Breath(idx=0, t_onset=0.0, t_peak=2.0, t_end=5.0, prominence=0.0, flag="low_prom")
    br = BreathResult(breaths=[b], detrended=[], t=[], mad=1.0, k_mad=1.0, min_breath_s=1.5,
                      max_breath_s=15.0, source="events")
    rr = RRSeries(rr_ms=[900.0, 950.0, 1000.0, 1100.0], t_beat=[0.9, 1.85, 2.85, 3.95], unit_detected="ms",
                  n_total=4, n_excluded=0, excluded_frac=0.0, col_used="rr", time=TimeInfo("seconds", None))
    r = rsa_per_breath(br, rr)[0]
    assert r.rsa_pv is None and r.reason == "artifact"
