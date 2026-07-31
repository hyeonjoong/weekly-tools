"""구간(epoch)별 분석과 Mann–Kendall 추세 — 1차 원리 검증 + 경계 조건."""

import math
import random

import pytest

from hrvkit.analyze import analyze_rr
from hrvkit.stats import inversion_counts, mann_kendall
from hrvkit.window import (analyze_windows, long_term_indices, window_trends)


def synth_rr(n, mean_rr=800.0, amp=25.0, resp_hz=0.25, seed=7, ramp=0.0):
    """RSA 사인파 + 잡음으로 만든 합성 RR(ms). ramp>0 이면 진폭이 시간에 따라 증가."""
    rng = random.Random(seed)
    out, t = [], 0.0
    for i in range(n):
        a = amp + ramp * (i / max(1, n - 1))
        v = mean_rr + a * math.sin(2 * math.pi * resp_hz * t) + rng.gauss(0, 5)
        out.append(v)
        t += v / 1000.0
    return out


# --------------------------------------------------------------------------- #
# Mann–Kendall 추세 검정
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 5, 6, 7])
def test_inversion_counts_total_is_factorial(n):
    assert sum(inversion_counts(n)) == math.factorial(n)
    assert len(inversion_counts(n)) == n * (n - 1) // 2 + 1


def test_inversion_counts_known_small_cases():
    assert inversion_counts(3) == [1, 2, 2, 1]
    assert inversion_counts(4) == [1, 3, 5, 6, 5, 3, 1]


def test_inversion_counts_rejects_negative():
    with pytest.raises(ValueError):
        inversion_counts(-1)


def test_mann_kendall_perfect_increase():
    r = mann_kendall([1, 2, 3, 4, 5])
    assert r["s"] == pytest.approx(10.0)       # C(5,2) = 10 쌍 모두 concordant
    assert r["tau"] == pytest.approx(1.0)
    assert r["method"] == "exact"
    # 정확 양측 p = 2/5! = 2/120
    assert r["p_value"] == pytest.approx(2.0 / 120.0)
    assert r["slope"] == pytest.approx(1.0)


def test_mann_kendall_perfect_decrease_is_mirror():
    up = mann_kendall([1, 2, 3, 4, 5])
    down = mann_kendall([5, 4, 3, 2, 1])
    assert down["s"] == pytest.approx(-up["s"])
    assert down["tau"] == pytest.approx(-1.0)
    assert down["p_value"] == pytest.approx(up["p_value"])


def test_mann_kendall_s_matches_brute_force_pair_count():
    x = [3.0, 1.0, 4.0, 1.5, 9.0, 2.6]
    s = sum((1 if x[j] > x[i] else -1 if x[j] < x[i] else 0)
            for i in range(len(x)) for j in range(i + 1, len(x)))
    assert mann_kendall(x)["s"] == pytest.approx(s)


def test_mann_kendall_theil_sen_slope():
    r = mann_kendall([0.0, 2.0, 4.0, 6.0])
    assert r["slope"] == pytest.approx(2.0)


def test_mann_kendall_constant_series_has_no_trend():
    r = mann_kendall([5.0] * 6)
    assert r["s"] == pytest.approx(0.0)
    assert r["p_value"] == pytest.approx(1.0)


def test_mann_kendall_ties_use_approximation():
    r = mann_kendall([1.0, 2.0, 2.0, 3.0, 4.0])
    assert r["method"] == "approx"
    with pytest.raises(ValueError):
        mann_kendall([1.0, 2.0, 2.0], method="exact")


def test_mann_kendall_too_short_returns_nan_p():
    r = mann_kendall([1.0, 2.0])
    assert r["p_value"] != r["p_value"]        # NaN — n<3 은 검정 불가
    assert r["tau"] == pytest.approx(1.0)      # tau/slope 는 계산 가능
    assert mann_kendall([])["n"] == 0


def test_mann_kendall_ignores_nan_values():
    nan = float("nan")
    r = mann_kendall([1.0, nan, 2.0, 3.0, nan, 4.0])
    assert r["n"] == 4
    assert r["tau"] == pytest.approx(1.0)


def test_mann_kendall_unknown_method_rejected():
    with pytest.raises(ValueError):
        mann_kendall([1, 2, 3], method="bogus")


def test_mann_kendall_exact_p_matches_permutation_enumeration():
    """n=6 의 정확 p를 모든 6! 순열의 S 분포로 독립 검증."""
    import itertools
    x = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0]

    def s_of(seq):
        return sum((1 if seq[j] > seq[i] else -1 if seq[j] < seq[i] else 0)
                   for i in range(len(seq)) for j in range(i + 1, len(seq)))

    obs = s_of(x)
    all_s = [s_of(p) for p in itertools.permutations(range(6))]
    lower = sum(1 for v in all_s if v <= obs) / len(all_s)
    upper = sum(1 for v in all_s if v >= obs) / len(all_s)
    assert mann_kendall(x)["p_value"] == pytest.approx(
        min(1.0, 2 * min(lower, upper)))


# --------------------------------------------------------------------------- #
# 창 분할
# --------------------------------------------------------------------------- #
def test_windows_partition_covers_expected_count():
    """1000 ms 고정 RR 600박동 = 600 s → 100 s 창이면 정확히 6개."""
    rr = [1000.0] * 600
    s = analyze_windows(rr, window_sec=100.0, min_beats=10, clean_method="none")
    assert len(s.windows) == 6
    assert all(w.n_beats == 100 for w in s.windows)
    assert s.duration_sec == pytest.approx(600.0)


def test_window_boundaries_are_contiguous_and_non_overlapping():
    rr = [1000.0] * 600
    s = analyze_windows(rr, window_sec=100.0, min_beats=10, clean_method="none")
    for i, w in enumerate(s.windows):
        assert w.start_sec == pytest.approx(i * 100.0)
        assert w.end_sec == pytest.approx(w.start_sec + 100.0)
    # 창들이 원본 박동을 정확히 한 번씩만 덮는다.
    assert sum(w.n_beats for w in s.windows) == 600


def test_incomplete_tail_is_dropped_and_reported():
    rr = [1000.0] * 650                       # 650 s
    s = analyze_windows(rr, window_sec=300.0, min_beats=10, clean_method="none")
    assert len(s.windows) == 2                # 0–300, 300–600; 마지막 50 s 버림
    assert any("완전한 창" in n for n in s.notes)


def test_overlapping_windows_slide_by_step():
    rr = [1000.0] * 400
    s = analyze_windows(rr, window_sec=200.0, step_sec=100.0, min_beats=10,
                        clean_method="none")
    assert s.overlapping
    assert [w.start_sec for w in s.windows] == [0.0, 100.0, 200.0]
    assert any("겹칩니다" in n for n in s.notes)


def test_step_larger_than_window_rejected():
    rr = [1000.0] * 400
    with pytest.raises(ValueError, match="빈틈"):
        analyze_windows(rr, window_sec=100.0, step_sec=200.0, min_beats=10)


def test_recording_shorter_than_window_rejected():
    rr = [1000.0] * 50
    with pytest.raises(ValueError, match="짧습니다"):
        analyze_windows(rr, window_sec=300.0)


def test_nonpositive_window_or_step_rejected():
    rr = [1000.0] * 400
    with pytest.raises(ValueError):
        analyze_windows(rr, window_sec=0.0)
    with pytest.raises(ValueError):
        analyze_windows(rr, window_sec=100.0, step_sec=0.0)


def test_too_few_beats_overall_rejected():
    with pytest.raises(ValueError):
        analyze_windows([800.0], window_sec=10.0)


def test_short_windows_are_flagged_not_silently_dropped():
    """박동이 적은 창은 행을 남기고 error 를 채운다(시간대가 사라지면 안 됨).

    앞 절반은 RR 1000 ms(창당 60박동), 뒤 절반은 1900 ms(창당 ~31박동).
    min_beats=45 면 뒤쪽 창만 걸러져야 하고, 그 창도 목록에서 사라지면 안 됩니다.
    """
    rr = [1000.0] * 300 + [1900.0] * 150
    s = analyze_windows(rr, window_sec=60.0, min_beats=45, clean_method="none")
    ok = [w for w in s.windows if w.ok]
    bad = [w for w in s.windows if not w.ok]
    assert ok and bad                          # 둘 다 존재
    assert all(w.error and "최소" in w.error for w in bad)
    assert any("박동 부족" in n for n in s.notes)
    # 걸러진 창도 start/end/n_beats 는 그대로 보고된다.
    assert all(w.n_beats > 0 and w.end_sec > w.start_sec for w in bad)


def test_all_windows_too_short_raises_actionable_error():
    rr = [1000.0] * 300
    with pytest.raises(ValueError, match="분석 가능한 창이 없습니다"):
        analyze_windows(rr, window_sec=10.0, min_beats=500,
                        clean_method="none")


def test_cleaning_happens_once_over_whole_record():
    """이상박동 비율이 창 단위로 리셋되지 않고 전체 정제 결과를 반영해야 한다.

    창마다 다시 탐지하면 이미 보간된 값 위에서 돌아 0% 로 잘못 보고됩니다.
    """
    rr = synth_rr(600, seed=11)
    rr[150] = 2400.0                          # 명백한 이상박동(범위 초과)
    s = analyze_windows(rr, window_sec=100.0, min_beats=20)
    assert s.n_artifacts >= 1
    hit = [w for w in s.windows if w.n_artifacts > 0]
    assert len(hit) == 1                      # 정확히 그 창에서만 잡힌다
    assert hit[0].pct_artifacts > 0.0


def test_remove_method_still_reports_which_window_had_artifacts():
    """`--clean remove` 여도 창별 이상박동 비율이 0 으로 지워지면 안 된다.

    제거된 박동은 시계열에서 사라지지만 '그 시간대에 이상박동이 있었다'는 사실은
    사용자가 그 구간을 믿을지 판단하는 근거입니다. 과거엔 남은 박동에 전부
    False 를 달아 remove 경로의 창별 art% 가 항상 0 이었습니다.
    """
    rr = synth_rr(600, seed=13)
    rr[200] = 2500.0
    s = analyze_windows(rr, window_sec=100.0, min_beats=20,
                        clean_method="remove")
    assert s.n_artifacts >= 1
    hit = [w for w in s.windows if w.n_artifacts > 0]
    assert len(hit) == 1
    assert hit[0].pct_artifacts > 0.0
    assert len(s.ok_windows) >= 4


def test_remove_method_time_axis_keeps_removed_beats_duration():
    """제거된 박동의 **시간**은 사라지면 안 된다 (창 라벨이 밀리면 안 됨)."""
    rr = [1000.0] * 300 + [2500.0] * 20 + [1000.0] * 300
    s = analyze_windows(rr, window_sec=100.0, min_beats=10,
                        clean_method="remove")
    # 원시 기록 길이 = 300 + 50 + 300 = 650 s
    assert s.duration_sec == pytest.approx(650.0)
    assert len(s.windows) == 6


def test_window_metrics_match_direct_analysis_of_that_slice():
    """창 0 의 지표는 그 구간 박동을 직접 analyze_rr 한 결과와 같아야 한다."""
    # RR 이 ~1000 ms 이므로 100 s 창 = 정확히 100박동. 구현이 아니라 **픽스처가
    # 보장하는 수**로 잘라 비교합니다(구현의 n_beats 를 쓰면, 잘못 잘라도 참조
    # 슬라이스가 똑같이 잘못 잘려 테스트가 통과합니다).
    rr = [1000.0] * 600
    s = analyze_windows(rr, window_sec=100.0, min_beats=10,
                        clean_method="none")
    w0 = s.windows[0]
    assert w0.n_beats == 100
    direct = analyze_rr(rr[:100], clean_method="none")
    assert w0.result.time["rmssd"] == pytest.approx(direct.time["rmssd"])
    assert w0.result.time["sdnn"] == pytest.approx(direct.time["sdnn"])


# --------------------------------------------------------------------------- #
# 장기 지표 (SDANN / SDNN index)
# --------------------------------------------------------------------------- #
def test_sdann_is_sd_of_window_mean_nn():
    import statistics
    rr = synth_rr(900, seed=3, ramp=0.0)
    s = analyze_windows(rr, window_sec=200.0, min_beats=20)
    lt = long_term_indices(s)
    means = [w.result.time["mean_nn"] for w in s.ok_windows]
    assert lt["sdann"] == pytest.approx(statistics.stdev(means))
    sdnns = [w.result.time["sdnn"] for w in s.ok_windows]
    assert lt["sdnn_index"] == pytest.approx(statistics.fmean(sdnns))


def test_sdann_omitted_for_overlapping_windows():
    rr = synth_rr(900, seed=5)
    s = analyze_windows(rr, window_sec=200.0, step_sec=100.0, min_beats=20)
    lt = long_term_indices(s)
    assert lt["sdann"] != lt["sdann"]          # NaN — 정의되지 않음
    assert lt["overlapping"] is True


def test_nonstandard_window_is_flagged():
    rr = synth_rr(900, seed=5)
    lt = long_term_indices(analyze_windows(rr, window_sec=200.0, min_beats=20))
    assert lt["nonstandard_window"] is True


def test_sdann_nan_with_single_window():
    rr = [1000.0] * 320
    s = analyze_windows(rr, window_sec=300.0, min_beats=10, clean_method="none")
    assert len(s.ok_windows) == 1
    assert long_term_indices(s)["sdann"] != long_term_indices(s)["sdann"]


# --------------------------------------------------------------------------- #
# 추세 요약
# --------------------------------------------------------------------------- #
def test_trends_detect_ramped_rmssd():
    """RSA 진폭을 시간에 따라 키우면 RMSSD 가 단조 증가로 잡혀야 한다."""
    rr = synth_rr(1400, amp=8.0, ramp=45.0, seed=21)
    s = analyze_windows(rr, window_sec=120.0, min_beats=20)
    tr = window_trends(s)
    assert tr["rmssd"]["tau"] > 0.7
    assert tr["rmssd"]["slope_per_window"] > 0
    assert tr["rmssd"]["trend_p"] < 0.05


def test_trends_summary_statistics_are_consistent():
    import statistics
    rr = synth_rr(900, seed=9)
    s = analyze_windows(rr, window_sec=200.0, min_beats=20)
    tr = window_trends(s)
    vals = [w.result.time["rmssd"] for w in s.ok_windows]
    assert tr["rmssd"]["mean"] == pytest.approx(statistics.fmean(vals))
    assert tr["rmssd"]["min"] == pytest.approx(min(vals))
    assert tr["rmssd"]["max"] == pytest.approx(max(vals))
    # sd 는 **표본** 표준편차(ddof=1)여야 합니다 — pstdev 로 바뀌면 잡히도록
    # 구현 출력이 아니라 독립 계산과 비교합니다(과거엔 sd/mean 을 다시 나눠
    # 자기 자신과 비교하는 항진 단언이라 pstdev 변이가 살아남았습니다).
    assert tr["rmssd"]["sd"] == pytest.approx(statistics.stdev(vals))
    assert tr["rmssd"]["sd"] != pytest.approx(statistics.pstdev(vals))
    assert tr["rmssd"]["cv"] == pytest.approx(
        statistics.stdev(vals) / abs(statistics.fmean(vals)))


def test_trends_include_multiplicity_adjusted_p():
    rr = synth_rr(1400, amp=8.0, ramp=45.0, seed=23)
    tr = window_trends(analyze_windows(rr, window_sec=120.0, min_beats=20))
    for key in ("rmssd", "sdnn", "mean_hr"):
        assert tr[key]["p_holm"] >= tr[key]["trend_p"] - 1e-12
        assert tr[key]["p_bh"] >= tr[key]["trend_p"] - 1e-12
    assert tr["_meta"]["n_tests"] >= 1


def test_series_to_dict_is_json_serialisable():
    import json
    rr = synth_rr(900, seed=31)
    s = analyze_windows(rr, window_sec=200.0, min_beats=20, source="x.csv")
    d = s.to_dict()
    assert d["n_windows"] == len(s.windows)
    assert d["windows"][0]["metrics"]["rmssd"] > 0
    json.dumps(d, allow_nan=True)              # 구조가 순수 파이썬 타입인지 확인


# --------------------------------------------------------------------------- #
# analyze_rr 의 precleaned_flags 경로
# --------------------------------------------------------------------------- #
def test_precleaned_flags_skip_redetection():
    rr = [800.0] * 50
    res = analyze_rr(rr, precleaned_flags=[True] + [False] * 49)
    assert res.n_artifacts == 1
    assert res.pct_artifacts == pytest.approx(2.0)


def test_precleaned_flags_length_mismatch_rejected():
    with pytest.raises(ValueError, match="precleaned_flags"):
        analyze_rr([800.0] * 10, precleaned_flags=[False] * 3)
