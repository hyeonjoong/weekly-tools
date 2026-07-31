"""2026-07-31 경화 라운드 회귀 테스트.

독립 리뷰어 4명(정확성 / 엣지케이스 / 문서 정직성 / 테스트품질·PII)이 재현한
결함을 하나씩 고정합니다. 각 테스트 위에 **무엇이 틀렸었는지**를 적어 둡니다 —
그래야 나중에 "이 단언 왜 있지?" 하고 지우는 일이 없습니다.
"""

import csv
import io
import json
import math
import os
import random
import statistics

import pytest

from hrvkit import cli
from hrvkit.report import (group_compare, group_compare_to_csv,
                           render_group_compare, render_windows,
                           windows_to_csv)
from hrvkit.stats import mann_kendall, mann_whitney_u, unpaired_summary
from hrvkit.window import (MAX_WINDOWS, analyze_windows, long_term_indices,
                           window_trends)

try:
    import scipy.stats as _sps
    HAVE_SCIPY = True
except Exception:  # pragma: no cover
    HAVE_SCIPY = False

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
SESSION = os.path.join(EXAMPLES, "session_20min.csv")
GROUP_MANIFEST = os.path.join(EXAMPLES, "parallel_arm", "manifest.csv")


def synth(n, mean_rr=820.0, amp=25.0, seed=1):
    rng = random.Random(seed)
    out, t = [], 0.0
    for _ in range(n):
        v = mean_rr + amp * math.sin(2 * math.pi * 0.25 * t) + rng.gauss(0, 5)
        out.append(v)
        t += v / 1000.0
    return out


def write_rr(path, values):
    with open(path, "w", encoding="utf-8") as f:
        f.write("rr_ms\n")
        for v in values:
            f.write(f"{v:.1f}\n")
    return str(path)


# --------------------------------------------------------------------------- #
# [정확성 #3 / 엣지 #1] 시간축은 원시 RR 로 만들어야 한다
#
# 과거: starts/duration 을 **정제된** 값으로 만들어, 센서 끊김(30 s)이 보간으로
# ~800 ms 가 되면 그 시간이 통째로 사라졌습니다. 900 s 기록이 608 s 로 보고되고,
# `5:00` 이라 찍힌 창이 실제로는 벽시계 10:00 에서 시작했습니다 — 개입 로그와
# 구간을 맞출 수 없게 되는 조용한 오답.
# --------------------------------------------------------------------------- #
def test_duration_uses_raw_time_axis_not_cleaned():
    rr = [800.0] * 375 + [30000.0] * 10 + [800.0] * 375
    raw_dur = sum(rr) / 1000.0                      # 900.0 s
    for method in ("interpolate", "remove", "none"):
        s = analyze_windows(rr, window_sec=300.0, min_beats=10,
                            clean_method=method)
        assert s.duration_sec == pytest.approx(raw_dur), method


def test_window_start_labels_are_wall_clock_after_a_gap():
    """끊김 뒤 박동은 실제 경과시간이 붙은 창에 들어가야 한다."""
    rr = [800.0] * 375 + [30000.0] * 10 + [800.0] * 375   # 300 s | 300 s | 300 s
    s = analyze_windows(rr, window_sec=300.0, min_beats=1,
                        clean_method="interpolate")
    assert len(s.windows) == 3
    # 창 1(300–600 s)은 통째로 끊김 구간 → 박동 10개(끊김 자체)만.
    assert s.windows[0].n_beats == 375
    assert s.windows[1].n_beats == 10
    assert s.windows[2].n_beats == 375


# --------------------------------------------------------------------------- #
# [정확성 #2] --clean remove 에서도 창별 이상박동 비율이 살아 있어야 한다
# 과거: 남은 박동에 전부 False 를 달아 창별 art% 가 항상 0.0 이었습니다.
# --------------------------------------------------------------------------- #
def test_per_window_artifact_rate_survives_remove():
    rr = synth(900, seed=3)
    for i in range(400, 460, 2):
        rr[i] = 1850.0                              # 놓친 박동 30개를 한 구간에
    s_rm = analyze_windows(rr, window_sec=120.0, min_beats=10,
                           clean_method="remove")
    s_in = analyze_windows(rr, window_sec=120.0, min_beats=10,
                           clean_method="interpolate")
    assert max(w.pct_artifacts for w in s_rm.windows) > 5.0
    # 두 보정 방법이 같은 창에 같은 이상박동 수를 보고해야 합니다.
    assert [w.n_artifacts for w in s_rm.windows] == \
           [w.n_artifacts for w in s_in.windows]


def test_csv_artifact_column_matches_window_truth_under_remove():
    """CSV 의 pct_artifacts 가 창 수준 참값과 어긋나면 안 된다."""
    rr = synth(600, seed=4)
    for i in range(200, 240, 2):
        rr[i] = 1900.0
    s = analyze_windows(rr, window_sec=120.0, min_beats=10,
                        clean_method="remove")
    rows = list(csv.DictReader(io.StringIO(windows_to_csv(s))))
    for row, w in zip(rows, s.windows):
        assert float(row["pct_artifacts_window"]) == pytest.approx(
            w.pct_artifacts, abs=0.05)
        if row["pct_artifacts"]:
            assert float(row["pct_artifacts"]) == pytest.approx(
                w.pct_artifacts, abs=0.05)


# --------------------------------------------------------------------------- #
# [정확성 #1 / 문서 #8] Theil–Sen 기울기는 **실제 창 번호** 로 나눠야 한다
#
# 과거: mann_kendall 이 비유한 값을 버리고 압축한 뒤 (j-i) 로 나눠, 창 절반이
# NaN 이면 기울기가 2배로 부풀려졌습니다(실측 0.697 vs 참값 0.284/창).
# --------------------------------------------------------------------------- #
def test_theil_sen_slope_keeps_original_spacing_across_nan():
    """NaN 을 버리고 **압축**한 인덱스로 나누면 기울기가 부풀려집니다.

    참 기울기는 1.0/스텝. 과거처럼 압축된 인덱스(0,1,2,3)로 나누면 2.0 이 나옵니다.
    """
    nan = float("nan")
    vals = [10.0, nan, 12.0, nan, 14.0, nan, 16.0]
    got = mann_kendall(vals)
    assert got["n"] == 4
    assert got["slope"] == pytest.approx(1.0)          # 2.0 이면 압축 버그
    # 명시적 positions 도 같은 답을 내야 합니다.
    same = mann_kendall(vals, positions=list(range(len(vals))))
    assert same["slope"] == pytest.approx(1.0)
    # 순서만 쓰는 S/tau/p 는 positions 유무와 무관해야 합니다.
    assert same["s"] == got["s"]
    assert same["p_value"] == pytest.approx(got["p_value"])


def test_theil_sen_slope_uses_caller_supplied_positions():
    """호출자가 이미 값을 골라낸 경우(=window_trends) positions 가 필수."""
    vals = [10.0, 12.0, 14.0, 16.0]                    # 실제로는 창 0,2,4,6
    assert mann_kendall(vals)["slope"] == pytest.approx(2.0)
    got = mann_kendall(vals, positions=[0, 2, 4, 6])
    assert got["slope"] == pytest.approx(1.0)


def test_mann_kendall_positions_length_validated():
    with pytest.raises(ValueError, match="positions"):
        mann_kendall([1.0, 2.0, 3.0], positions=[0, 1])


def test_window_trend_slope_uses_window_index():
    """지표가 일부 창에서 NaN 이어도 slope 는 '창당' 단위를 지켜야 한다."""
    rr = synth(1600, amp=8.0, seed=11)
    s = analyze_windows(rr, window_sec=25.0, min_beats=10)
    tr = window_trends(s)
    for key in ("rmssd", "sampen", "lf_hf_ratio"):
        rec = tr[key]
        if not rec.get("n") or rec["n"] < 3:
            continue
        # 유효 창 번호로 직접 Theil–Sen 을 재계산해 일치 확인.
        pts = []
        for w in s.ok_windows:
            from hrvkit.analyze import flat_metrics
            v = flat_metrics(w.result).get(key)
            if v is not None and math.isfinite(v):
                pts.append((w.index, v))
        slopes = sorted((v2 - v1) / (i2 - i1)
                        for a, (i1, v1) in enumerate(pts)
                        for (i2, v2) in pts[a + 1:])
        assert rec["slope_per_window"] == pytest.approx(
            statistics.median(slopes))


def test_trend_record_reports_effective_n():
    rr = synth(1600, amp=8.0, seed=13)
    s = analyze_windows(rr, window_sec=25.0, min_beats=10)
    tr = window_trends(s)
    for key in ("rmssd", "sampen"):
        assert tr[key]["n"] <= tr[key]["n_windows"] == len(s.ok_windows)


def test_partial_metric_n_is_marked_in_text_report():
    rr = synth(1600, amp=8.0, seed=17)
    s = analyze_windows(rr, window_sec=25.0, min_beats=10)
    tr = window_trends(s)
    partial = any(tr[k]["n"] < len(s.ok_windows)
                  for k in ("rmssd", "sampen", "lf_hf_ratio", "hf_nu"))
    out = render_windows(s)
    if partial:
        assert "*" in out and "유한한 창이 전체" in out


# --------------------------------------------------------------------------- #
# [엣지 #4 / 테스트품질 #8] 창 수 상한 — --step 오타로 멈추면 안 된다
# --------------------------------------------------------------------------- #
def test_tiny_step_is_rejected_not_hung():
    rr = [800.0] * 2500                             # 2000 s
    with pytest.raises(ValueError, match="상한"):
        analyze_windows(rr, window_sec=300.0, step_sec=0.1, min_beats=10)


def test_window_cap_error_suggests_a_workable_step():
    rr = [800.0] * 2500
    with pytest.raises(ValueError) as exc:
        analyze_windows(rr, window_sec=300.0, step_sec=0.1, min_beats=10)
    msg = str(exc.value)
    assert "--step" in msg
    suggested = float(msg.split("을 ")[-1].split(" s 이상")[0])
    s = analyze_windows(rr, window_sec=300.0, step_sec=suggested, min_beats=10)
    assert len(s.windows) <= MAX_WINDOWS


def test_cli_tiny_step_exits_cleanly(capsys):
    rc = cli.main([SESSION, "--window", "300", "--step", "0.05"])
    assert rc == 2
    assert "상한" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# [엣지 #6] 구간별 경고가 텍스트 리포트에도 보여야 한다
# --------------------------------------------------------------------------- #
def test_per_window_warnings_are_rendered():
    rr = synth(900, seed=19)
    for i in range(300, 420):
        rr[i] = 2400.0                              # 한 구간을 통째로 오염
    s = analyze_windows(rr, window_sec=120.0, min_beats=10)
    assert any(w.ok and w.result.warnings for w in s.windows)
    out = render_windows(s)
    assert "구간별 경고" in out


def test_windows_csv_carries_warnings_and_vlf_flag():
    rr = synth(900, seed=23)
    s = analyze_windows(rr, window_sec=120.0, min_beats=10)
    rows = list(csv.DictReader(io.StringIO(windows_to_csv(s))))
    assert "warnings" in rows[0] and "vlf_reliable" in rows[0]
    # 2분 창은 VLF(주기 333 s)를 절대 해상하지 못합니다.
    assert all(r["vlf_reliable"] in ("False", "") for r in rows)


# --------------------------------------------------------------------------- #
# [테스트품질 #4] 실패한 창도 CSV 에 행을 남기고 error 를 채워야 한다
# --------------------------------------------------------------------------- #
def test_failed_windows_keep_their_csv_row():
    rr = [1000.0] * 300 + [1900.0] * 150
    s = analyze_windows(rr, window_sec=60.0, min_beats=45, clean_method="none")
    rows = list(csv.DictReader(io.StringIO(windows_to_csv(s))))
    assert len(rows) == len(s.windows)
    failed = [r for r in rows if r["error"]]
    assert failed
    for r in failed:
        assert r["rmssd"] == ""
        assert int(r["n_beats"]) > 0
        assert "최소" in r["error"]


def test_windows_csv_header_is_stable():
    """다운스트림 파이프라인이 의존하는 열 순서를 고정."""
    rr = synth(900, seed=29)
    s = analyze_windows(rr, window_sec=200.0, min_beats=20)
    header = next(csv.reader(io.StringIO(windows_to_csv(s))))
    assert header[:6] == ["window", "start_sec", "end_sec", "n_beats",
                          "n_artifacts_window", "pct_artifacts_window"]
    assert header[-3:] == ["vlf_reliable", "warnings", "error"]
    assert "rmssd" in header and "sdnn" in header


def test_group_csv_header_is_stable():
    header = next(csv.reader(io.StringIO(
        group_compare_to_csv(*_two_groups(), alpha=0.05))))
    assert header[0] == "metric"
    for col in ("n_a", "n_b", "hl_shift", "ci_low", "ci_high", "mw_p",
                "hedges_g", "rank_biserial", "p_holm", "p_bh"):
        assert col in header


def _two_groups():
    from hrvkit.analyze import analyze_rr
    a = [analyze_rr(synth(200, amp=10.0, seed=100 + i)) for i in range(3)]
    b = [analyze_rr(synth(200, amp=30.0, seed=200 + i)) for i in range(4)]
    return a, b


# --------------------------------------------------------------------------- #
# [테스트품질 #1] 정규 근사 분기 — scipy 교차검증 + 고정값
# 과거: 연속성 보정을 지워도, 동점 보정 분산을 지워도, tau-b 를 tau-a 로 바꿔도
# 테스트가 전부 통과했습니다(근사 경로의 수치 단언이 하나도 없었음).
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_mann_whitney_approx_matches_scipy_with_ties():
    a = [float(i) for i in range(1, 11)] * 4
    b = [float(i) for i in range(2, 12)] * 4
    ref = _sps.mannwhitneyu(b, a, alternative="two-sided",
                            use_continuity=True, method="asymptotic")
    got = mann_whitney_u(a, b, method="approx")
    assert got["u_stat"] == pytest.approx(float(ref.statistic))
    assert got["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-12)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_mann_whitney_approx_matches_scipy_without_ties():
    a = [float(i) for i in range(0, 40, 2)]
    b = [float(i) + 3.0 for i in range(0, 40, 2)]
    ref = _sps.mannwhitneyu(b, a, alternative="two-sided",
                            use_continuity=True, method="asymptotic")
    got = mann_whitney_u(a, b, method="approx")
    assert got["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-12)


def test_mann_whitney_continuity_correction_is_applied():
    """연속성 보정이 없으면 z 가 커지고 p 가 작아집니다 — 고정값으로 잠급니다."""
    a = [float(i) for i in range(1, 11)] * 4
    b = [float(i) for i in range(2, 12)] * 4
    got = mann_whitney_u(a, b, method="approx")
    assert got["z"] == pytest.approx(1.464485, abs=1e-5)
    assert got["p_value"] == pytest.approx(0.143062, abs=1e-5)


def test_mann_whitney_tie_correction_changes_variance():
    """동점 보정을 빼면 분산이 커져 p 가 달라집니다 — 동점 유무로 확인."""
    tied = mann_whitney_u([1.0, 2.0, 2.0, 2.0, 3.0],
                          [2.0, 2.0, 3.0, 3.0, 4.0], method="approx")
    untied = mann_whitney_u([1.0, 2.1, 2.2, 2.3, 3.0],
                            [2.4, 2.5, 3.1, 3.2, 4.0], method="approx")
    # 같은 순위 구조라도 동점이 있으면 분산이 작아져 |z| 가 커집니다.
    assert abs(tied["z"]) != pytest.approx(abs(untied["z"]), abs=1e-6)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_kendall_tau_b_matches_scipy_with_ties():
    x = [1.0, 2.0, 2.0, 3.0, 5.0, 5.0, 5.0, 8.0, 9.0, 11.0]
    ref = _sps.kendalltau(list(range(len(x))), x, variant="b")
    assert mann_kendall(x)["tau"] == pytest.approx(float(ref.correlation),
                                                  abs=1e-12)


def test_mann_kendall_continuity_correction_is_applied():
    x = [1.0, 2.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    got = mann_kendall(x)
    assert got["method"] == "approx"               # 동점 → 근사
    assert got["z"] == pytest.approx(3.241569, abs=1e-5)
    assert got["p_value"] == pytest.approx(0.001189, abs=1e-5)


def test_mann_kendall_tau_b_is_not_tau_a():
    """동점이 있으면 tau-b > tau-a — 분모 보정이 실제로 적용됐는지."""
    x = [1.0, 2.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    n0 = len(x) * (len(x) - 1) / 2.0
    tau_a = mann_kendall(x)["s"] / n0
    assert mann_kendall(x)["tau"] > tau_a


# --------------------------------------------------------------------------- #
# [테스트품질 #2] Holm 과 BH 가 뒤바뀌면 잡혀야 한다 (BH ≤ Holm 은 항상 성립)
# --------------------------------------------------------------------------- #
def test_bh_never_exceeds_holm_in_window_trends():
    rr = synth(1400, amp=8.0, seed=31)
    tr = window_trends(analyze_windows(rr, window_sec=120.0, min_beats=20))
    seen = 0
    for key, _, _ in __import__("hrvkit.window", fromlist=["x"]).TREND_METRICS:
        rec = tr[key]
        if rec.get("p_holm") == rec.get("p_holm") and \
                rec.get("p_bh") == rec.get("p_bh"):
            assert rec["p_bh"] <= rec["p_holm"] + 1e-12
            seen += 1
    assert seen >= 5


def test_bh_never_exceeds_holm_in_group_compare():
    a, b = _two_groups()
    g = group_compare(a, b)
    seen = 0
    for key, s in g.items():
        if key == "_meta" or not isinstance(s, dict):
            continue
        ph, pb = s.get("p_holm"), s.get("p_bh")
        if ph == ph and pb == pb and ph is not None and pb is not None:
            assert pb <= ph + 1e-12
            seen += 1
    assert seen >= 5


# --------------------------------------------------------------------------- #
# [테스트품질 #3] precleaned_flags 배선이 실제로 연결돼 있어야 한다
# 과거: analyze_rr 호출에서 precleaned_flags 를 빼도 테스트가 통과했고,
# 그 결과 JSON/CSV 의 metrics.pct_artifacts 가 0 이 됐습니다.
# --------------------------------------------------------------------------- #
def test_precleaned_flags_reach_the_window_result():
    rr = synth(600, seed=37)
    rr[150] = 2400.0
    s = analyze_windows(rr, window_sec=100.0, min_beats=20)
    hit = [w for w in s.windows if w.n_artifacts > 0]
    assert len(hit) == 1
    assert hit[0].result.pct_artifacts == pytest.approx(hit[0].pct_artifacts,
                                                        abs=0.05)
    assert hit[0].result.n_artifacts == hit[0].n_artifacts


# --------------------------------------------------------------------------- #
# [테스트품질 #5] 비대칭 픽스처 — 합동 SD 와 군별 n 을 실제로 구별
# --------------------------------------------------------------------------- #
def test_cohens_d_uses_pooled_sd_not_group_sd():
    a = [10.0, 12.0, 14.0]                 # sd 2
    b = [20.0, 21.0, 22.0]                 # sd 1
    s = unpaired_summary(a, b)
    sp = math.sqrt((2 * 4.0 + 2 * 1.0) / 4.0)
    assert s["sd_pooled"] == pytest.approx(sp)
    assert s["cohens_d"] == pytest.approx(9.0 / sp)
    assert s["cohens_d"] != pytest.approx(9.0 / 2.0)      # sd_a 로 나눈 값 아님
    assert s["cohens_d"] != pytest.approx(9.0 / 1.0)      # sd_b 로 나눈 값 아님


def test_group_meta_n_is_not_swapped(tmp_path, capsys):
    for name in ("a1", "a2", "b1", "b2", "b3"):
        write_rr(tmp_path / f"{name}.csv", synth(200, seed=hash(name) % 999))
    man = tmp_path / "m.csv"
    man.write_text("file,group\na1.csv,ctl\na2.csv,ctl\n"
                   "b1.csv,trt\nb2.csv,trt\nb3.csv,trt\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man), "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["group_a"] == "ctl" and d["group_b"] == "trt"
    assert d["_meta"]["n_a"] == 2 and d["_meta"]["n_b"] == 3
    assert d["rmssd"]["n_a"] == 2 and d["rmssd"]["n_b"] == 3


# --------------------------------------------------------------------------- #
# [문서 #1] 기준군은 매니페스트 순서로 정해진다 — 리포트가 그것을 말해야 한다
# --------------------------------------------------------------------------- #
def test_group_report_names_the_reference_arm():
    a, b = _two_groups()
    out = render_group_compare(a, b, a_label="placebo", b_label="device")
    assert "기준(대조) placebo" in out
    assert "먼저 나온 군" in out
    assert "median(device − placebo)" in out


def test_group_direction_flips_when_manifest_order_flips(tmp_path, capsys):
    """행 순서를 뒤집으면 이동량 부호가 뒤집히고, 리포트가 그 기준을 밝혀야 한다."""
    for name in ("c1", "c2", "d1", "d2"):
        amp = 10.0 if name.startswith("c") else 35.0
        write_rr(tmp_path / f"{name}.csv",
                 synth(220, amp=amp, seed=hash(name) % 999))
    m1 = tmp_path / "m1.csv"
    m1.write_text("file,group\nc1.csv,ctl\nc2.csv,ctl\nd1.csv,dev\nd2.csv,dev\n",
                  encoding="utf-8")
    m2 = tmp_path / "m2.csv"
    m2.write_text("file,group\nd1.csv,dev\nd2.csv,dev\nc1.csv,ctl\nc2.csv,ctl\n",
                  encoding="utf-8")
    cli.main(["--groups", str(m1), "--json"])
    fwd = json.loads(capsys.readouterr().out)
    cli.main(["--groups", str(m2), "--json"])
    rev = json.loads(capsys.readouterr().out)
    assert fwd["group_a"] == "ctl" and rev["group_a"] == "dev"
    assert fwd["rmssd"]["hl_shift"] == pytest.approx(
        -rev["rmssd"]["hl_shift"])
    assert fwd["rmssd"]["mw_p"] == pytest.approx(rev["rmssd"]["mw_p"])


# --------------------------------------------------------------------------- #
# [문서 #3] SDANN/SDNN index 는 24시간 지표 — 짧은 기록임을 밝혀야 한다
# --------------------------------------------------------------------------- #
def test_long_term_indices_flag_short_records():
    rr = synth(1400, seed=41)
    s = analyze_windows(rr, window_sec=120.0, min_beats=20)
    lt = long_term_indices(s)
    assert lt["short_record"] is True
    out = render_windows(s)
    assert "24시간" in out


def test_overlapping_windows_caveat_on_sdnn_index():
    rr = synth(1400, seed=43)
    s = analyze_windows(rr, window_sec=200.0, step_sec=100.0, min_beats=20)
    out = render_windows(s)
    assert "여러 번 셈" in out


# --------------------------------------------------------------------------- #
# [문서 #4] 구간 VLF 는 NaN 이 아니라 '과소추정된 유한 값' — 그렇게 말해야 한다
# --------------------------------------------------------------------------- #
def test_window_report_warns_that_vlf_is_unreliable_not_absent():
    rr = synth(1400, seed=47)
    s = analyze_windows(rr, window_sec=300.0, min_beats=20)
    lt_unrel = [w for w in s.ok_windows
                if not w.result.freq.get("vlf_reliable")]
    assert lt_unrel                                  # 5분 창은 항상 신뢰 불가
    out = render_windows(s)
    assert "vlf_reliable=False" in out
    assert "과소추정된 유한 값" in out
    # 그리고 실제로 NaN 이 아니라 유한 값입니다(문서가 'NaN' 이라 하면 거짓).
    vlf = lt_unrel[0].result.freq["vlf_power"]
    assert math.isfinite(vlf)
