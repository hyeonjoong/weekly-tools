import importlib.util
import math
import os
import re

import pytest

from rsalink.parse import RespWaveform, RRSeries, TimeInfo
from rsalink.breath import detect_breaths
from rsalink.rsa import rsa_per_breath
from rsalink.segments import (Segment, build_segments, analyze_segments, compare, compare_all,
                              parse_segments_csv, SegmentStats)
from rsalink import RsalinkError

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "..", "examples", "_generate.py")
spec = importlib.util.spec_from_file_location("_generate", GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def _run_synth(rate_fn, amp_fn, rr_mean=900.0, rr_noise=6.0, seed=gen.SEED, dur_s=900.0,
               base="0:00-5:00", stim="5:00-15:00", pace_bpm=6.0):
    resp_t, resp_v, rr = gen.synth(rate_fn, amp_fn, dur_s=dur_s, seed=seed, rr_mean=rr_mean, rr_noise=rr_noise)
    resp = RespWaveform(t=resp_t, v=resp_v, fs_est=10.0, irregular_frac=0.0, n_gaps=0, gap_total_s=0.0,
                        dup_ts=0, time=TimeInfo("seconds", None))
    tb, t = [], 0.0
    for x in rr:
        t += x / 1000.0
        tb.append(t)
    rrs = RRSeries(rr_ms=rr, t_beat=tb, unit_detected="ms", n_total=len(rr), n_excluded=0,
                   excluded_frac=0.0, col_used="rr_ms", time=TimeInfo("seconds", None))
    br = detect_breaths(resp)
    rows = rsa_per_breath(br, rrs)
    segs = build_segments(base, stim, None, dur_s)
    stats = analyze_segments(segs, br, rows, rrs, br.t, br.detrended, pace_bpm=pace_bpm)
    return stats, compare(stats[0], stats[1])


def _run(name):
    rate_fn, amp_fn = gen.SCENARIOS[name]
    return _run_synth(rate_fn, amp_fn)


def test_paced_scenario_verdict_accompanied():
    stats, c = _run("paced_6bpm")
    b, s = stats
    assert abs(b.rate["mean"] - 12.0) < 0.5 and abs(s.rate["mean"] - 6.0) < 0.3
    # RSA_pv: 자극(6/분, 호흡당 ≈11 박동) 2A = 90 → 92.5, 기준선(12/분, 호흡당 ≈5.6 박동) 2A = 50 → 51.6
    # (라운드 1 A1 중점 배정·lag 1.0 뒤. 라운드 0 은 끝 박동 배정으로 기준선이 40.6 이었다.)
    # 2A 보다 약간 큰 것은 LF 8 ms 성분 + 잡음 6 ms 가 max−min 을 키우기 때문(잡음의 극값 편향).
    assert 46 < b.rsa["median"] < 56 and 86 < s.rsa["median"] < 98
    assert abs(b.rsa["beats_per_breath"] - 5.6) < 0.3 and abs(s.rsa["beats_per_breath"] - 10.9) < 0.5
    # 파워(A²/2 이론: 기준 312, 자극 1012 + LF 8 ms → 32): 손계산 대역
    assert 240 < b.band.hf_fixed < 310 and b.band.resp_over_hf > 0.9
    assert s.band.lf > 900 and s.band.hf_fixed < 30
    assert c.rate_changed and c.hf_changed
    assert c.verdict_hf == "동반됨"
    # 6/분에서 RSA 파워는 HF 고정 밖 → HF 고정 ↓, 호흡중심 ↑ → 결론 상이
    assert c.hf_direction == "↓" and c.resp_direction == "↑"
    assert c.verdict_band == "상이"
    assert s.slow_flag and not b.slow_flag
    assert s.band.resp_over_hf > 5
    assert s.pace.pct_within > 80 and s.pace.first_entrain_t is not None
    assert b.pace.pct_within < 10
    assert re.search(r"^HF 변화가 호흡수 변화와 \*\*동반됨\*\* \(HF 고정 ↓, L=\d+/\d+, 호흡수 12\.\d→6\.\d/분 변화\)", c.lines[0])
    assert re.search(r"^호흡중심 대역 기준 결론 \*\*상이\*\* \(HF 고정 ↓ vs 호흡중심 ↑", c.lines[1])
    d = {x.metric: x for x in c.deltas}
    assert abs(d["호흡수(회/분)"].delta - (-6.0)) < 0.6
    # 손계산: RSA_pv Δ = 자극 − 기준 = 92.5 − 51.6 ≈ 40.9 (2A 90 − 50 = 40 에 LF·잡음 극값 편향 +1)
    assert 36 < d["RSA_pv 중앙값(ms)"].delta < 46


def test_sham_scenario_verdict_not_accompanied():
    stats, c = _run("sham")
    b, s = stats
    assert abs(b.rate["mean"] - 12.0) < 0.5 and abs(s.rate["mean"] - 12.0) < 0.5
    assert 240 < b.band.hf_fixed < 310 and 900 < s.band.hf_fixed < 1150   # A 25 → 50
    assert 46 < b.rsa["median"] < 56 and 90 < s.rsa["median"] < 106   # 2A 50 → 100 (측정 51.6 → 97.2)
    assert not c.rate_changed and c.hf_changed and c.hf_direction == "↑"
    assert c.verdict_hf == "비동반"
    assert c.verdict_band == "동일"          # 호흡중심도 ↑
    assert re.search(r"^HF 변화가 호흡수 변화와 \*\*비동반\*\* \(HF 고정 ↑, L=\d+/\d+, 호흡수 12\.\d→12\.\d/분 임계 미만\)", c.lines[0])


def test_segments_csv_and_conflicts(tmp_path):
    p = tmp_path / "seg.csv"
    p.write_text("start,end,label\n0:00,5:00,기준선\n5:00,15:00,자극\n", encoding="utf-8")
    segs = parse_segments_csv(str(p))
    assert [s.label for s in segs] == ["기준선", "자극"] and segs[1].t1 == 900.0
    with pytest.raises(RsalinkError):
        build_segments("0:00-5:00", None, str(p), 900.0)
    assert build_segments(None, None, None, 123.0)[0].label == "전체"


def test_hold_when_hf_flat():
    stats, c = _run("sham")
    # 기준선을 자기 자신과 비교하면 HF 변화 없음 → 판정 보류
    c2 = compare(stats[0], stats[0])
    assert "보류" in c2.verdict_hf and c2.verdict_band == "동일"


# ---------------------------------------------------------------- 라운드 1 A3 · D17: 판정 보류·경계값

from rsalink.spectral import BandResult
from rsalink.segments import (_direction, hold_reasons, POWER_RATIO_LO, POWER_RATIO_HI, POWER_CHANGE_FRAC,
                              HF_FLOOR_MS2, MIN_WELCH_SEGMENTS)


def _band(hf, rc=None, L=10):
    rc = hf if rc is None else rc
    return BandResult(lf=50.0, hf_fixed=hf, resp_centered=rc, resp_band=(0.16, 0.24), resp_freq=0.2,
                      coherence_at_resp=0.9, n_segments=L, nperseg=256, df=1 / 64, duration_s=600.0,
                      rr_peak_freq=0.2, coherence_bias=1 / L)


def _stats(label, rate, hf, rc=None, L=10, rsa_n=20, rsa_und=0, band=True):
    seg = Segment(label, 0.0, 600.0)
    rsa = {"n": rsa_n, "median": 50.0, "q1": 40.0, "q3": 60.0, "mean": 50.0, "n_undetermined": rsa_und,
           "n_breaths": rsa_n + rsa_und, "beats_per_breath": 5.0, "mean_rr": 900.0}
    rate_d = {"n": 60, "mean": rate, "sd": 0.5, "cv": 0.5 / rate, "median": rate, "skew": 0.0}
    return SegmentStats(seg=seg, n_breaths=60, n_valid=60, rate=rate_d, rsa=rsa,
                        band=_band(hf, rc, L) if band else None, pace=None, mean_rr=900.0,
                        short_flag=False, slow_flag=False, spectral_note="" if band else "스펙트럼 생략")


def test_power_ratio_constants_single_source():
    assert POWER_RATIO_LO == pytest.approx(1 - POWER_CHANGE_FRAC) and POWER_RATIO_HI == pytest.approx(1 / (1 - POWER_CHANGE_FRAC))
    assert (POWER_RATIO_LO, POWER_RATIO_HI) == pytest.approx((0.80, 1.25))


@pytest.mark.parametrize("ratio, expect", [(0.79, "↓"), (0.80, "→"), (0.81, "→"), (1.24, "→"), (1.25, "→"), (1.26, "↑")])
def test_direction_boundaries(ratio, expect):
    assert _direction(100.0, 100.0 * ratio) == expect


def test_hold_low_welch_segments():
    c = compare(_stats("기준선", 12.0, 200.0, L=5), _stats("자극", 6.0, 100.0, L=17))
    assert c.verdict_hf.startswith("판정 보류") and "L=5" in c.verdict_hf
    assert c.verdict_band.startswith("판정 보류")
    assert re.search(r"HF 변화가 호흡수 변화와 \*\*판정 보류\(.*\)\*\*", c.lines[0])


def test_hold_hf_floor_clamp():
    # 둘 다 바닥 아래(5 → 20 ms², 비율 4배지만 클램프 뒤 1.0) → 보류
    c = compare(_stats("기준선", 12.0, 5.0), _stats("자극", 6.0, 20.0))
    assert "판정 보류" in c.verdict_hf and f"{HF_FLOOR_MS2:.0f}" in c.verdict_hf
    # 기준 276 → 자극 11: 클램프 뒤 30/276 = 0.11 → ↓ 유지, 주석만
    c = compare(_stats("기준선", 12.0, 276.0, rc=270.0), _stats("자극", 6.0, 11.0, rc=1000.0))
    assert c.verdict_hf == "동반됨" and c.hf_direction == "↓" and c.verdict_band == "상이"
    assert any("30 으로 처리" in ln for ln in c.lines)
    # 기준 35 → 자극 11: 클램프 뒤 30/35 = 0.857 → ±20% 안 → 보류
    c = compare(_stats("기준선", 12.0, 35.0), _stats("자극", 6.0, 11.0))
    assert "판정 보류" in c.verdict_hf


def test_hold_all_rsa_undetermined():
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 30.0, 100.0, rsa_n=0, rsa_und=40))
    assert "판정 보류" in c.verdict_hf and "판정불가" in c.verdict_hf


# ---------------------------------------------------------------- 라운드 2 #1: 판정불가 "우세" 보류


def test_hold_when_undetermined_dominates_hr40_20bpm():
    # HR 40(RR 1500 ms)·자극 호흡 20/분 → 호흡당 박동 ≈ 2 → 판정 3 / 판정불가 198. 라운드 1 은 n == 0 일 때만
    # 보류라 '동반됨'이 나왔다(HARDENING 라운드 2 #1). 이제 호흡당 박동 < 3 또는 판정불가 > 판정 이면 보류.
    stats, c = _run_synth(lambda t: 12.0 if t < 300.0 else 20.0, lambda t: 25.0, rr_mean=1500.0, pace_bpm=None)
    s = stats[1]
    assert s.rsa["beats_per_breath"] < 3.0 and s.rsa["n_undetermined"] > s.rsa["n"] > 0
    assert c.verdict_hf.startswith("판정 보류(") and "RSA 판정불가 우세" in c.verdict_hf
    assert c.verdict_band.startswith("판정 보류(")
    assert re.search(r"자극 RSA 판정불가 우세\(호흡당 박동 \d\.\d, 판정 \d+/판정불가 \d+\)", c.verdict_hf)


# ---------------------------------------------------------------- 라운드 2 #2: 잡음 마진(로그비)

from rsalink.segments import (noise_margin_ln, margin_pct, ratio_bounds, LN_RATIO_FLOOR, WELCH_BIN_EFF,
                              check_no_overlap)
from rsalink.spectral import band_bin_count, HF_BAND


def test_hf_band_bin_count_literal():
    assert band_bin_count(1 / 64, *HF_BAND) == 16      # nperseg 256: k = 10..25 (0.15625–0.390625 Hz)
    assert band_bin_count(1 / 32, *HF_BAND) == 8       # 128: k = 5..12
    assert band_bin_count(1 / 16, *HF_BAND) == 4       # 64: k = 3..6
    assert _band(100.0).hf_n_bins == 0                 # 수동 BandResult 는 df 로 계산(fallback)


def test_noise_margin_hand_literal():
    # L=8/17, B=16: L_eff = 8·0.5·16 = 64, 1.96·√(2/64) = 1.96·0.176777 = 0.346482 > ln 1.25 = 0.223144
    m = noise_margin_ln(8, 17, 16)
    assert m == pytest.approx(0.346482, abs=1e-6)
    assert margin_pct(m) == pytest.approx(29.28, abs=0.02)            # 100·(1 − e^−0.3465)
    assert ratio_bounds(m) == pytest.approx((0.707172, 1.414084), abs=1e-5)   # e^∓0.346482 (√2 = e^0.346574 와는 다름)
    # 바닥: L=30/30, B=16 → 1.96·√(2/240) = 0.1789 < ln 1.25 → 0.2231 (= ±20%, 비율 0.80–1.25)
    assert noise_margin_ln(30, 30, 16) == pytest.approx(math.log(1.25))
    assert margin_pct(LN_RATIO_FLOOR) == pytest.approx(20.0)
    assert ratio_bounds(LN_RATIO_FLOOR) == pytest.approx((0.80, 1.25))
    assert WELCH_BIN_EFF == 0.5
    assert noise_margin_ln(8, 8, 0) == pytest.approx(LN_RATIO_FLOOR)   # 빈 수 0 → 바닥


def test_margin_widens_verdict_and_is_printed():
    # L=10 (수동 통계, df 1/64 → B 16): M = 1.96·√(2/80) = 0.3099 → ±27%, 비율 0.73–1.36
    m = noise_margin_ln(10, 10, 16)
    assert m == pytest.approx(0.30996, abs=1e-4)
    # 비율 1.30 (ln 0.262): 라운드 1 ±20% 는 ↑ 였지만 이제 마진 안 → 보류
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 260.0))
    assert c.hf_direction == "→" and c.verdict_hf == "판정 보류(HF 변화 |ln비| ≤ 잡음 마진 ±27%)"
    assert c.margin_ln == pytest.approx(m) and c.L_eff == 80 and c.hf_n_bins == 16
    assert c.hf_ln_ratio == pytest.approx(math.log(1.3))
    assert any(ln.startswith("잡음 마진 ±27% (비율 0.73 배 미만 또는 1.36 배 초과면 변화") and "이 툴의 관례" in ln
               for ln in c.lines)
    # 비율 1.40 (ln 0.336 > 0.310) → ↑ → 동반됨
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 280.0))
    assert c.hf_direction == "↑" and c.verdict_hf == "동반됨"
    # L 이 크면 바닥(±20%)으로 돌아간다: L=40 → 비율 1.30 은 ↑
    c = compare(_stats("기준선", 12.0, 200.0, L=40), _stats("자극", 6.0, 260.0, L=40))
    assert c.hf_direction == "↑" and c.margin_ln == pytest.approx(LN_RATIO_FLOOR)
    # 호흡중심 대역도 같은 마진: 200 → 260 은 →, 200 → 280 은 ↑
    c = compare(_stats("기준선", 12.0, 200.0, rc=200.0), _stats("자극", 6.0, 100.0, rc=260.0))
    assert c.resp_direction == "→" and c.verdict_band == "상이"


def test_false_change_rate_pure_noise_20_seeds():
    # 라운드 2 #2(a): 호흡 12/분 고정, RSA A=3 ms, RR 백색잡음 σ 15–30 ms, 5분/5분(L=8/8), 20 seed.
    # 라운드 1 ±20% 규칙은 5/20 = 25% 를 '변화'로 판정했다(HARDENING 라운드 2 #2). 마진 뒤 1/20.
    n_new = n_old = 0
    for k in range(20):
        sigma = 15.0 + 15.0 * k / 19.0
        stats, c = _run_synth(lambda t: 12.0, lambda t: 3.0, rr_noise=sigma, seed=1000 + k, dur_s=600.0,
                              base="0:00-5:00", stim="5:00-10:00", pace_bpm=None)
        assert not c.hold_reasons and c.base.band.n_segments == 8 == c.stim.band.n_segments
        assert c.margin_ln == pytest.approx(noise_margin_ln(8, 8, 16))
        n_new += c.hf_changed
        n_old += abs(c.hf_ln_ratio) > LN_RATIO_FLOOR
    assert n_new <= 2, n_new          # ≤ 10%
    assert n_old >= 4, n_old          # 옛 규칙이면 ≥ 20% — 마진이 실제로 일하는지(뮤턴트 방어)


# ---------------------------------------------------------------- 라운드 2 #9: 구간 겹침


def test_overlap_check_unit():
    check_no_overlap([Segment("a", 0.0, 300.0), Segment("b", 300.0, 900.0)])       # 끝 = 시작 허용
    with pytest.raises(RsalinkError, match="구간 겹침"):
        check_no_overlap([Segment("a", 0.0, 360.0), Segment("b", 300.0, 900.0)])
    with pytest.raises(RsalinkError, match="구간 겹침"):
        build_segments("0:00-6:00", "5:00-15:00", None, 900.0)


def test_hold_undetermined_majority_even_with_enough_beats():
    # 호흡당 박동 평균 3.2 여도 판정불가 21 > 판정 19 면 보류; 판정 21 > 판정불가 19 면 보류 아님
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 100.0, rsa_n=19, rsa_und=21))
    assert "RSA 판정불가 우세" in c.verdict_hf
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 100.0, rsa_n=21, rsa_und=19))
    assert "판정불가" not in c.verdict_hf and c.verdict_hf == "동반됨"


def test_verdict_line_shows_L_and_no_nan():
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 100.0, L=17))
    assert "L=10/17" in c.lines[0] and "nan" not in c.lines[0].lower()
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", float("nan"), 100.0, L=17))
    assert "호흡 없음" in c.lines[0] and "nan" not in c.lines[0].lower()


def test_direction_note_rolloff_mismatch():
    # 호흡수 ↓ 와 HF ↓ (같은 방향) → 롤오프 불일치 주석; 호흡수 ↓ · HF ↑ → 주석 없음
    c = compare(_stats("기준선", 12.0, 200.0), _stats("자극", 6.0, 100.0))
    assert any("롤오프" in ln for ln in c.lines)
    c = compare(_stats("기준선", 12.0, 100.0), _stats("자극", 6.0, 200.0))
    assert not any("롤오프" in ln for ln in c.lines)


def test_segments_csv_headerless_and_duplicates(tmp_path):
    p = tmp_path / "nohdr.csv"
    p.write_text("0:00,5:00,기준선\n5:00,15:00,자극\n", encoding="utf-8")
    segs = parse_segments_csv(str(p))
    assert [s.label for s in segs] == ["기준선", "자극"] and segs[0].t0 == 0.0 and segs[1].t1 == 900.0
    p2 = tmp_path / "num.csv"
    p2.write_text("0,300,a\n300,900,b\n", encoding="utf-8")
    assert [s.label for s in parse_segments_csv(str(p2))] == ["a", "b"]
    p3 = tmp_path / "badhdr.csv"
    p3.write_text("from,to,name\n0:00,5:00,a\n", encoding="utf-8")
    with pytest.raises(RsalinkError, match="start,end,label"):
        parse_segments_csv(str(p3))
    p4 = tmp_path / "dup.csv"
    p4.write_text("start,end,label\n0:00,5:00,a\n5:00,9:00,a\n", encoding="utf-8")
    with pytest.raises(RsalinkError, match="중복"):
        parse_segments_csv(str(p4))
    p5 = tmp_path / "badtime.csv"
    p5.write_text("start,end,label\n0:00,x:yy,a\n", encoding="utf-8")
    with pytest.raises(RsalinkError, match="시간 형식"):
        parse_segments_csv(str(p5))
