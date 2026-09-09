import importlib.util
import math
import os

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


def _run(name):
    rate_fn, amp_fn = gen.SCENARIOS[name]
    resp_t, resp_v, rr = gen.synth(rate_fn, amp_fn)
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
    segs = build_segments("0:00-5:00", "5:00-15:00", None, 900.0)
    stats = analyze_segments(segs, br, rows, rrs, br.t, br.detrended, pace_bpm=6.0)
    return stats, compare(stats[0], stats[1])


def test_paced_scenario_verdict_accompanied():
    stats, c = _run("paced_6bpm")
    b, s = stats
    assert abs(b.rate["mean"] - 12.0) < 0.5 and abs(s.rate["mean"] - 6.0) < 0.3
    # RSA_pv: 자극(6/분, 호흡당 ≈11 박동) 은 2A = 90 에 가깝게(87.7) 복원.
    # 기준선(12/분, 호흡당 ≈5.6 박동) 은 극값을 놓쳐 2A = 50 보다 작게(40.6) 나온다 —
    # peak-valley 의 박동 이산화 손실(극값당 최대 A(1−cos(π·f·RR)) ≈ 3.9 ms) + LF 8 ms 성분.
    assert 34 < b.rsa["median"] < 47 and 82 < s.rsa["median"] < 94
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
    assert "동반됨" in c.lines[0] and "상이" in c.lines[1]
    d = {x.metric: x for x in c.deltas}
    assert abs(d["호흡수(회/분)"].delta - (-6.0)) < 0.6
    assert d["RSA_pv 중앙값(ms)"].delta == pytest.approx(s.rsa["median"] - b.rsa["median"])


def test_sham_scenario_verdict_not_accompanied():
    stats, c = _run("sham")
    b, s = stats
    assert abs(b.rate["mean"] - 12.0) < 0.5 and abs(s.rate["mean"] - 12.0) < 0.5
    assert 240 < b.band.hf_fixed < 310 and 900 < s.band.hf_fixed < 1150   # A 25 → 50
    assert 34 < b.rsa["median"] < 47 and 72 < s.rsa["median"] < 88
    assert not c.rate_changed and c.hf_changed and c.hf_direction == "↑"
    assert c.verdict_hf == "비동반"
    assert c.verdict_band == "동일"          # 호흡중심도 ↑
    assert "비동반" in c.lines[0]


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
