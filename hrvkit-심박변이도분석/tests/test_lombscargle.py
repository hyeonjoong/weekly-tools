"""Lomb–Scargle PSD (--psd lomb) — 첫 원리 검산과 파이프라인 통합 테스트.

핵심 주장 3가지를 각각 독립적으로 고정합니다.

1) **고속 구현 == 교과서 정의.** 위상자 점화식을 쓰는 `lombscargle_grid` 가
   Lomb(1976)/Scargle(1982) 정의를 그대로 옮긴 `_lomb_power_direct` 와 일치.
2) **절대 스케일이 맞다(Parseval).** PSD = 2·P/f_eff 정규화 덕분에 진폭 A 정현파의
   대역 적분이 A²/2(=분산)가 되고, 균등 표본이면 Welch 와 같은 스케일이 된다.
3) **보간을 하지 않는다.** 선형보간(Welch 경로)은 저역통과로 작용해 HF 를
   과소추정하는데, 같은 신호에서 Lomb 이 더 큰(참값에 가까운) HF 를 낸다.
"""

import csv
import io
import json
import math
import random
import subprocess
import sys

import pytest

from hrvkit.analyze import analyze_rr
from hrvkit.frequency import (HF_BAND, LF_BAND, VLF_BAND, _band_power,
                              _beat_times, _lomb_power_direct, frequency_domain,
                              lombscargle_grid, lombscargle_psd, welch_psd)


# --------------------------------------------------------------------------- #
# 합성 데이터
# --------------------------------------------------------------------------- #
def _irregular_times(duration: float, mean_dt: float, jitter: float, seed: int):
    """단조 증가하는 불균등 시각열."""
    rng = random.Random(seed)
    t = [0.0]
    while t[-1] < duration:
        t.append(t[-1] + mean_dt + rng.uniform(-jitter, jitter))
    return t


def _rr_with_rsa(n, mean=800.0, amp=40.0, resp_hz=0.25, seed=11, noise=0.0):
    """호흡 주파수 resp_hz 의 RSA 가 실린 RR(ms) 시계열."""
    rng = random.Random(seed)
    rr = []
    t = 0.0
    for _ in range(n):
        v = mean + amp * math.sin(2.0 * math.pi * resp_hz * t)
        if noise:
            v += rng.gauss(0.0, noise)
        rr.append(v)
        t += v / 1000.0
    return rr


# --------------------------------------------------------------------------- #
# 1) 고속 격자 구현 == 교과서 정의
# --------------------------------------------------------------------------- #
def test_fast_grid_matches_textbook_definition():
    """위상자 점화식이 sin/cos 를 직접 부르는 정의식과 부동소수점 수준으로 일치."""
    times = _irregular_times(120.0, 0.8, 0.15, seed=7)
    vals = [800.0 + 40.0 * math.sin(2 * math.pi * 0.25 * t)
            + 15.0 * math.cos(2 * math.pi * 0.09 * t) for t in times]
    mean_v = sum(vals) / len(vals)
    xc = [v - mean_v for v in vals]

    df, nfreq = 0.002, 220
    freqs, power = lombscargle_grid(times, vals, df, nfreq)
    assert len(freqs) == nfreq
    # 격자는 df 부터 시작하는 균등 격자여야 합니다(대역 적분이 df 를 씁니다).
    assert freqs[0] == pytest.approx(df, rel=1e-12)
    assert freqs[1] - freqs[0] == pytest.approx(df, rel=1e-12)

    for k in range(0, nfreq, 5):
        ref = _lomb_power_direct(times, xc, 2.0 * math.pi * freqs[k])
        assert power[k] == pytest.approx(ref, rel=1e-9, abs=1e-9)


def test_phasor_renormalisation_holds_over_long_grid():
    """256스텝마다 재정규화하므로 격자 끝(>1000점)에서도 드리프트가 없어야 합니다."""
    times = _irregular_times(60.0, 0.75, 0.2, seed=21)
    vals = [900.0 + 30.0 * math.sin(2 * math.pi * 0.2 * t) for t in times]
    mean_v = sum(vals) / len(vals)
    xc = [v - mean_v for v in vals]
    df, nfreq = 0.0004, 1100
    freqs, power = lombscargle_grid(times, vals, df, nfreq)
    for k in (900, 1000, nfreq - 1):
        ref = _lomb_power_direct(times, xc, 2.0 * math.pi * freqs[k])
        assert power[k] == pytest.approx(ref, rel=1e-8, abs=1e-8)


def test_tau_makes_periodogram_invariant_to_time_origin():
    """τ(시간 오프셋)의 **존재 이유**를 직접 검정합니다.

    주의: `lombscargle_psd` 는 수치 조건을 위해 내부에서 t0 를 빼므로, 그 함수로
    원점 이동을 시험하면 τ 가 아니라 그 뺄셈을 시험하게 됩니다(τ=0 으로 만들어도
    통과). 재정렬을 하지 않는 `lombscargle_grid` 를 직접 불러야 진짜 검정입니다.
    """
    times = _irregular_times(90.0, 0.8, 0.2, seed=5)
    vals = [850.0 + 25.0 * math.sin(2 * math.pi * 0.22 * t) for t in times]
    shifted = [t + 1234.5 for t in times]
    _f1, p1 = lombscargle_grid(times, vals, 0.004, 80)
    _f2, p2 = lombscargle_grid(shifted, vals, 0.004, 80)
    for a, b in zip(p1, p2):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9)


def test_tau_orthogonalises_the_sine_and_cosine_bases():
    """τ 는 Σcos·sin = 0 이 되도록 고른 값입니다 — 정의를 직접 확인합니다.

    두 구현을 서로 비교하는 것만으로는 둘 다 같은 방식으로 틀렸을 때를 못 잡습니다.
    여기서는 어떤 구현도 참조하지 않고 **성질 자체**를 검정합니다.
    """
    times = _irregular_times(120.0, 0.8, 0.25, seed=17)
    for f in (0.05, 0.1, 0.2, 0.35):
        omega = 2.0 * math.pi * f
        s2 = sum(math.sin(2 * omega * t) for t in times)
        c2 = sum(math.cos(2 * omega * t) for t in times)
        tau = math.atan2(s2, c2) / (2.0 * omega)
        cross = sum(math.cos(omega * (t - tau)) * math.sin(omega * (t - tau))
                    for t in times)
        scale = sum(math.cos(omega * (t - tau)) ** 2 for t in times)
        assert abs(cross) < 1e-9 * max(1.0, scale)


def test_grid_start_offset_is_not_re_zeroed_away():
    """lombscargle_grid 는 시각을 재정렬하지 않습니다(재정렬은 psd 래퍼의 몫)."""
    times = _irregular_times(60.0, 0.8, 0.2, seed=23)
    vals = [800.0 + 30.0 * math.sin(2 * math.pi * 0.25 * t) for t in times]
    mean_v = sum(vals) / len(vals)
    xc = [v - mean_v for v in vals]
    shifted = [t + 5000.0 for t in times]
    _f, p = lombscargle_grid(shifted, vals, 0.005, 40)
    for k in range(0, 40, 6):
        ref = _lomb_power_direct(shifted, xc, 2.0 * math.pi * (k + 1) * 0.005)
        assert p[k] == pytest.approx(ref, rel=1e-8, abs=1e-8)


# --------------------------------------------------------------------------- #
# 2) 절대 스케일 (Parseval) — 손 검산
# --------------------------------------------------------------------------- #
def test_sinusoid_band_power_equals_half_amplitude_squared():
    """진폭 A 정현파 → 대역 적분 = A²/2 (= 분산). 정규화 상수의 근본 검증."""
    A, f0 = 40.0, 0.25
    times = _irregular_times(300.0, 0.8, 0.2, seed=3)
    vals = [A * math.cos(2 * math.pi * f0 * t) for t in times]
    freqs, psd, meta = lombscargle_psd(times, vals, oversample=8)
    got = _band_power(freqs, psd, 0.0, 0.45)
    assert got == pytest.approx(A * A / 2.0, rel=0.05)
    # 유효 표본율 = (N−1)/기록길이 = 1/평균 간격
    assert meta["ls_fs_eff"] == pytest.approx(
        (len(times) - 1) / (times[-1] - times[0]), rel=1e-12)


def test_even_sampling_total_power_equals_variance():
    """균등 표본에서 전 대역 적분 ≈ 신호 분산 (Parseval)."""
    dt, n = 0.25, 512
    times = [i * dt for i in range(n)]
    vals = [3.0 * math.cos(2 * math.pi * 0.1 * t)
            + 1.5 * math.sin(2 * math.pi * 0.3 * t) for t in times]
    freqs, psd, _ = lombscargle_psd(times, vals, oversample=4, f_max=0.5)
    total = _band_power(freqs, psd, 0.0, 0.5)
    mean_v = sum(vals) / n
    var = sum((v - mean_v) ** 2 for v in vals) / n
    assert total == pytest.approx(var, rel=0.02)


def test_even_sampling_matches_welch_scale():
    """균등 표본이면 Lomb 과 Welch(사각창 아닌 Hann이지만 밀도 스케일 동일)의
    대역 파워가 같은 크기여야 합니다 — 두 경로의 정규화가 호환됨을 고정."""
    dt, n = 0.25, 1024
    fs = 1.0 / dt
    times = [i * dt for i in range(n)]
    vals = [5.0 * math.sin(2 * math.pi * 0.2 * t) for t in times]
    f_w, p_w, _ = welch_psd(vals, fs, nperseg=512)
    w_band = _band_power(f_w, p_w, 0.15, 0.25)
    f_l, p_l, _ = lombscargle_psd(times, vals, oversample=4, f_max=0.5)
    l_band = _band_power(f_l, p_l, 0.15, 0.25)
    assert l_band == pytest.approx(w_band, rel=0.10)
    assert l_band == pytest.approx(5.0 ** 2 / 2.0, rel=0.10)


def test_reported_resolution_is_one_over_duration_not_grid_spacing():
    """과표본 격자 간격을 '해상도'로 광고하면 과대선전입니다 — 1/T 를 보고해야."""
    rr = _rr_with_rsa(300)
    f = frequency_domain(rr, method="lomb", ls_oversample=8.0)
    span = f["ls_span_sec"]
    assert f["freq_resolution_hz"] == pytest.approx(1.0 / span, rel=1e-12)
    assert f["ls_df_hz"] == pytest.approx(1.0 / (8.0 * span), rel=1e-12)
    assert f["ls_df_hz"] < f["freq_resolution_hz"]


# --------------------------------------------------------------------------- #
# 3) 보간 없음 — HF 과소추정 회피
# --------------------------------------------------------------------------- #
def test_lomb_recovers_more_hf_than_interpolated_welch():
    """4 Hz 선형보간은 저역통과라 HF 를 깎습니다. 같은 신호에서 Lomb 이 참값
    (A²/2)에 더 가까워야 합니다."""
    amp, resp = 45.0, 0.30          # HF 대역 상단 근처 → 보간 손실이 큼
    rr = _rr_with_rsa(400, mean=800.0, amp=amp, resp_hz=resp)
    truth = amp * amp / 2.0
    f_w = frequency_domain(rr, method="welch")
    f_l = frequency_domain(rr, method="lomb")
    assert f_l["hf_power"] > f_w["hf_power"]
    assert abs(f_l["hf_power"] - truth) < abs(f_w["hf_power"] - truth)


def test_peak_frequency_recovers_known_respiration():
    """알려진 호흡 주파수를 피크로 되찾아야 합니다(격자 해상도 이내)."""
    for resp_hz in (0.20, 0.30):
        rr = _rr_with_rsa(400, amp=40.0, resp_hz=resp_hz)
        f = frequency_domain(rr, method="lomb")
        assert f["peak_hf"] == pytest.approx(resp_hz, abs=0.02)
        assert f["resp_rate_brpm"] == pytest.approx(resp_hz * 60.0, abs=1.5)


def test_lomb_resolves_vlf_that_welch_segments_cannot():
    """Welch 는 기록을 64 s 구간으로 쪼개 VLF(주기 333 s)를 못 봅니다.
    Lomb 은 기록 전체를 쓰므로 충분히 긴 기록에서 VLF 를 신뢰할 수 있습니다."""
    rr = _rr_with_rsa(1600, mean=800.0, amp=30.0, resp_hz=0.25, noise=8.0)
    f_w = frequency_domain(rr, method="welch")
    f_l = frequency_domain(rr, method="lomb")
    assert f_w["vlf_reliable"] is False
    assert f_l["vlf_reliable"] is True
    assert f_l["ls_span_sec"] >= 3.0 / VLF_BAND[0]


def test_vlf_not_called_reliable_on_a_short_record():
    """VLF 주기(333 s)를 겨우 넘겼다고 신뢰할 수는 없습니다 — Task Force 1996 은
    단기(≈5분) 기록의 VLF 를 '의심스러운 지표'로 보고 피하라고 합니다.
    최소 3주기(999 s)를 요구합니다."""
    short = _rr_with_rsa(450, mean=800.0)          # ≈360 s > 333 s
    f = frequency_domain(short, method="lomb")
    assert f["ls_span_sec"] > 1.0 / VLF_BAND[0]
    assert f["vlf_reliable"] is False


# --------------------------------------------------------------------------- #
# 엣지 케이스
# --------------------------------------------------------------------------- #
def test_rejects_unknown_method():
    with pytest.raises(ValueError, match="알 수 없는 PSD 방법"):
        frequency_domain(_rr_with_rsa(80), method="fft")


def test_short_record_raises_same_as_welch():
    """20 s 미만 방어는 방법과 무관하게 동일해야 합니다."""
    rr = [800.0] * 10                       # 8 s
    for m in ("welch", "lomb"):
        with pytest.raises(ValueError, match="너무 짧습니다"):
            frequency_domain(rr, method=m)


def test_constant_rr_gives_zero_power_not_spurious_peak():
    """분산 0 → 모든 대역 파워 0, 피크 없음(가짜 '느린 호흡 레짐' 오탐 금지)."""
    f = frequency_domain([800.0] * 200, method="lomb")
    assert f["hf_power"] == pytest.approx(0.0, abs=1e-9)
    assert f["lf_power"] == pytest.approx(0.0, abs=1e-9)
    assert f["peak_hf"] is None and f["peak_lf"] is None
    assert f["slow_breathing_regime"] is False


def test_bradycardia_flags_aliasing_risk():
    """평균 HR < 48 bpm → 평균 Nyquist < 0.40 Hz. 조용히 넘어가면 안 됩니다."""
    rr = _rr_with_rsa(200, mean=1400.0, amp=40.0, resp_hz=0.20)   # ≈43 bpm
    f = frequency_domain(rr, method="lomb")
    assert f["ls_nyquist_hz"] < HF_BAND[1]
    assert f["ls_above_nyquist"] is True
    res = analyze_rr(rr, psd_method="lomb")
    assert any("앨리어싱" in w for w in res.warnings)


def test_normal_hr_does_not_flag_aliasing():
    res = analyze_rr(_rr_with_rsa(300), psd_method="lomb")
    assert res.freq["ls_above_nyquist"] is False
    assert not any("앨리어싱" in w for w in res.warnings)


def test_grid_size_is_capped_and_oversample_reported_honestly():
    """과표본을 크게 줘도 격자는 상한에 걸리고, 그때 **실제** 배수를 보고해야
    합니다(요청값을 그대로 찍으면 거짓말)."""
    from hrvkit.frequency import MAX_LS_FREQS
    times = _irregular_times(4000.0, 0.8, 0.15, seed=9)
    vals = [800.0 + 20.0 * math.sin(2 * math.pi * 0.25 * t) for t in times]
    _f, _p, meta = lombscargle_psd(times, vals, oversample=32.0)
    assert meta["ls_nfreq"] == MAX_LS_FREQS
    assert meta["ls_oversample"] < 32.0
    assert meta["ls_oversample"] == pytest.approx(
        1.0 / (meta["ls_df_hz"] * meta["ls_span_sec"]), rel=1e-12)


def test_lombscargle_psd_rejects_degenerate_input():
    with pytest.raises(ValueError, match="최소 4개"):
        lombscargle_psd([0.0, 1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="단조 증가"):
        lombscargle_psd([0.0] * 6, [1.0] * 6)
    t4, v4 = [0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 1.0, 2.0]
    for bad in (0.0, -1.0, float("inf"), float("nan")):
        # inf 는 df=0 → ZeroDivisionError 로 죽던 경로. 깔끔한 ValueError 여야 합니다.
        with pytest.raises(ValueError, match="oversample"):
            lombscargle_psd(t4, v4, oversample=bad)
    with pytest.raises(ValueError, match="길이가 다릅니다"):
        lombscargle_psd(t4, [1.0, 2.0, 3.0])
    for bad_fmax in (0.0, -0.4, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="f_max"):
            lombscargle_psd(t4, v4, f_max=bad_fmax)
    with pytest.raises(ValueError, match="단조 증가"):
        lombscargle_psd([0.0, 1.0, 2.0, float("nan")], v4)


def test_lombscargle_grid_handles_empty_request():
    assert lombscargle_grid([0.0, 1.0], [1.0, 2.0], 0.1, 0) == ([], [])
    assert lombscargle_grid([0.0], [1.0], 0.1, 5) == ([], [])


def test_removed_beats_do_not_crash_lomb():
    """--clean remove 로 구멍이 난 시계열도 Lomb 경로가 처리해야 합니다."""
    rr = _rr_with_rsa(300)
    rr[50] = 2400.0     # 놓친 박동
    rr[120] = 250.0     # 조기수축
    res = analyze_rr(rr, clean_method="remove", psd_method="lomb")
    assert res.freq["psd_method"] == "lomb"
    assert math.isfinite(res.freq["hf_power"])


# --------------------------------------------------------------------------- #
# 파이프라인 통합 (analyze / CSV / JSON / CLI)
# --------------------------------------------------------------------------- #
def test_analyze_rr_defaults_to_welch():
    res = analyze_rr(_rr_with_rsa(200))
    assert res.freq["psd_method"] == "welch"
    assert "ls_df_hz" not in res.freq


def test_failed_frequency_analysis_reports_no_method():
    """주파수영역이 생략되면 psd_method 는 None — 'welch 로 계산됨' 처럼 보이면
    안 됩니다."""
    res = analyze_rr([800.0] * 6, psd_method="lomb")   # 4.8 s → 너무 짧음
    assert res.freq["psd_method"] is None
    assert any("주파수영역 분석 생략" in w for w in res.warnings)


def _run(args):
    return subprocess.run([sys.executable, "-m", "hrvkit.cli"] + args,
                          capture_output=True, text=True)


def test_cli_lomb_text_report_names_the_method(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("rr_ms\n" + "\n".join(f"{v:.3f}" for v in _rr_with_rsa(300)),
                 encoding="utf-8")
    out = _run([str(p), "--psd", "lomb", "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "Lomb–Scargle" in out.stdout
    assert "보간 없음" in out.stdout
    assert "Welch PSD" not in out.stdout
    # welch 기본값에서는 반대여야 합니다.
    out_w = _run([str(p), "--no-sampen"])
    assert "Welch PSD" in out_w.stdout and "Lomb–Scargle" not in out_w.stdout


def test_cli_csv_and_json_carry_psd_method(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(250)),
                 encoding="utf-8")
    out = _run([str(p), "--psd", "lomb", "--format", "csv", "--no-sampen"])
    assert out.returncode == 0, out.stderr
    rows = list(csv.reader(io.StringIO(out.stdout)))
    assert "psd_method" in rows[0]
    assert rows[1][rows[0].index("psd_method")] == "lomb"

    out = _run([str(p), "--psd", "lomb", "--json", "--no-sampen"])
    d = json.loads(out.stdout)
    assert d["frequency_domain"]["psd_method"] == "lomb"
    assert d["frequency_domain"]["ls_nfreq"] > 0


def test_cli_window_accepts_lomb(tmp_path):
    p = tmp_path / "long.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(900)),
                 encoding="utf-8")
    out = _run([str(p), "--window", "300", "--psd", "lomb", "--no-sampen",
                "--format", "csv"])
    assert out.returncode == 0, out.stderr
    rows = list(csv.reader(io.StringIO(out.stdout)))
    idx = rows[0].index("psd_method")
    assert [r[idx] for r in rows[1:]] == ["lomb"] * (len(rows) - 1)


def test_cli_rejects_bad_oversample(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(120)),
                 encoding="utf-8")
    for bad in ("0", "-2", "64"):
        out = _run([str(p), "--psd", "lomb", "--ls-oversample", bad])
        assert out.returncode == 2
        assert "--ls-oversample" in out.stderr


def test_cli_rejects_unknown_psd_choice(tmp_path):
    p = tmp_path / "rr.csv"
    p.write_text("800\n800\n800\n", encoding="utf-8")
    out = _run([str(p), "--psd", "burg"])
    assert out.returncode != 0
    assert "burg" in out.stderr


def test_compare_and_paired_work_under_lomb(tmp_path):
    base = tmp_path / "b.csv"
    interv = tmp_path / "i.csv"
    base.write_text("\n".join(f"{v:.3f}" for v in
                              _rr_with_rsa(300, amp=15.0, resp_hz=0.25)),
                    encoding="utf-8")
    interv.write_text("\n".join(f"{v:.3f}" for v in
                                _rr_with_rsa(300, amp=45.0, resp_hz=0.25,
                                             seed=12)),
                      encoding="utf-8")
    out = _run([str(base), str(interv), "--compare", "--psd", "lomb",
                "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "RMSSD" in out.stdout

    # 피험자마다 서로 다른 기록이어야 합니다(도구가 중복 짝을 거부합니다).
    lines = []
    for i in range(6):
        b = tmp_path / f"s{i}_base.csv"
        v = tmp_path / f"s{i}_post.csv"
        b.write_text("\n".join(f"{x:.3f}" for x in _rr_with_rsa(
            300, amp=15.0, resp_hz=0.25, seed=100 + i, noise=5.0)),
            encoding="utf-8")
        v.write_text("\n".join(f"{x:.3f}" for x in _rr_with_rsa(
            300, amp=45.0, resp_hz=0.25, seed=200 + i, noise=5.0)),
            encoding="utf-8")
        lines.append(f"{b},{v},S{i}")
    man = tmp_path / "man.csv"
    man.write_text("baseline,intervention,label\n" + "\n".join(lines),
                   encoding="utf-8")
    out = _run(["--paired", str(man), "--psd", "lomb", "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "Wilcoxon" in out.stdout


def test_clean_remove_preserves_the_timeline_instead_of_splicing_it():
    """회귀: `--clean remove` 는 박동을 지우면서 **시간까지** 지우면 안 됩니다.

    과거엔 정제된 NN 의 누적합으로 시각을 다시 만들었기 때문에, 박동 하나를
    제거할 때마다 그 간격만큼 기록이 짧아지고 뒤따르는 모든 박동이 앞으로
    당겨졌습니다(스펙트럼 전체가 위로 밀림). 이제 원본 시각을 살려 결측을
    구멍으로 보존합니다.
    """
    rr = _rr_with_rsa(400, mean=800.0, amp=40.0, resp_hz=0.25)
    # 확실히 제거될 이상박동 3개를 심습니다.
    for i in (80, 180, 300):
        rr[i] = 2500.0
    removed_ms = sum(rr[i] for i in (80, 180, 300))
    true_span = sum(rr) / 1000.0                 # 원본 기록의 실제 길이

    drop = analyze_rr(rr, clean_method="remove", psd_method="lomb",
                      do_sampen=False)

    # 이어붙이기(splice)였다면 제거된 간격의 합(7.5 s)만큼 짧아졌을 것입니다.
    spliced = true_span - removed_ms / 1000.0
    assert drop.freq["duration_sec"] == pytest.approx(true_span, abs=0.5)
    assert drop.freq["duration_sec"] > spliced + 5.0

    # 시각이 보존되므로 알려진 호흡 주파수도 그대로 나와야 합니다.
    # (이어붙이면 시간축이 압축돼 피크가 위로 밀립니다.)
    assert drop.freq["peak_hf"] == pytest.approx(0.25, abs=0.02)
    # Welch 경로도 같은 시간축을 씁니다(구멍을 가로질러 보간).
    drop_w = analyze_rr(rr, clean_method="remove", psd_method="welch",
                        do_sampen=False)
    assert drop_w.freq["duration_sec"] == pytest.approx(true_span, abs=0.5)


def test_provided_times_are_validated():
    """외부에서 넘긴 times 는 길이·단조성을 검사해야 합니다."""
    nn = _rr_with_rsa(60)
    with pytest.raises(ValueError, match="길이가 다릅니다"):
        frequency_domain(nn, times=[0.0, 1.0, 2.0])
    bad = _beat_times(nn)
    bad[10], bad[11] = bad[11], bad[10]      # 순서 뒤집기
    with pytest.raises(ValueError, match="단조 증가"):
        frequency_domain(nn, times=bad)


def test_explicit_times_reproduce_default_cumsum_path():
    """times 를 명시하지 않았을 때와 누적합을 그대로 넘겼을 때가 같아야 합니다."""
    nn = _rr_with_rsa(200)
    a = frequency_domain(nn, method="lomb")
    b = frequency_domain(nn, method="lomb", times=_beat_times(nn))
    assert a["hf_power"] == pytest.approx(b["hf_power"], rel=1e-12)
    assert a["duration_sec"] == pytest.approx(b["duration_sec"], rel=1e-12)


def test_groups_and_batch_work_under_lomb():
    """동봉 예제 매니페스트가 --psd lomb 에서도 그대로 돌아야 합니다."""
    import os
    ex = os.path.join(os.path.dirname(__file__), "..", "examples")
    man = os.path.join(ex, "parallel_arm", "manifest.csv")
    out = _run(["--groups", man, "--psd", "lomb", "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "Mann–Whitney" in out.stdout or "Mann" in out.stdout

    out = _run([os.path.join(ex, "resting.csv"),
                os.path.join(ex, "slow_breathing.csv"),
                "--psd", "lomb", "--no-sampen", "--format", "csv"])
    assert out.returncode == 0, out.stderr
    rows = list(csv.reader(io.StringIO(out.stdout)))
    idx = rows[0].index("psd_method")
    assert [r[idx] for r in rows[1:]] == ["lomb", "lomb"]


def test_every_multi_record_mode_states_the_psd_method(tmp_path):
    """회귀: 여러 기록을 한 표에 모으는 모드(--compare/--paired/--groups/--window)는
    **어느 추정기로 낸 숫자인지 반드시 적어야** 합니다.

    Welch 와 Lomb 의 절대 파워는 같은 기록에서도 20 % 넘게 다릅니다. 방법이 안 적힌
    코호트 표가 논문 표로 굳으면 나중에 복구할 수 없습니다 — 예전에는 --compare
    텍스트와 --paired/--groups 의 텍스트·CSV·JSON 어디에도 없었습니다.
    """
    files = []
    for i in range(6):
        b = tmp_path / f"p{i}_b.csv"
        v = tmp_path / f"p{i}_v.csv"
        b.write_text("\n".join(f"{x:.3f}" for x in _rr_with_rsa(
            260, amp=15.0, seed=300 + i, noise=5.0)), encoding="utf-8")
        v.write_text("\n".join(f"{x:.3f}" for x in _rr_with_rsa(
            260, amp=45.0, seed=400 + i, noise=5.0)), encoding="utf-8")
        files.append((b, v))

    man = tmp_path / "paired.csv"
    man.write_text("baseline,intervention,label\n"
                   + "\n".join(f"{b},{v},S{i}" for i, (b, v) in enumerate(files)),
                   encoding="utf-8")
    arms = tmp_path / "arms.csv"
    arms.write_text("file,group\n"
                    + "\n".join(f"{b},control" for b, _ in files) + "\n"
                    + "\n".join(f"{v},drug" for _, v in files), encoding="utf-8")
    long_f = tmp_path / "long.csv"
    long_f.write_text("\n".join(f"{x:.3f}" for x in _rr_with_rsa(900)),
                      encoding="utf-8")
    b0, v0 = files[0]

    for label, argv in (
        ("compare", [str(b0), str(v0), "--compare"]),
        ("paired", ["--paired", str(man)]),
        ("groups", ["--groups", str(arms)]),
        ("window", [str(long_f), "--window", "300"]),
    ):
        out = _run(argv + ["--psd", "lomb", "--no-sampen"])
        assert out.returncode == 0, f"{label}: {out.stderr}"
        assert "Lomb–Scargle" in out.stdout, f"{label} 텍스트에 방법 없음"

    # CSV 도 자기설명적이어야 합니다(--paired/--groups 는 지표당 1행).
    for argv in (["--paired", str(man)], ["--groups", str(arms)]):
        out = _run(argv + ["--psd", "lomb", "--no-sampen", "--format", "csv"])
        assert out.returncode == 0, out.stderr
        rows = list(csv.reader(io.StringIO(out.stdout)))
        idx = rows[0].index("psd_method")
        assert {r[idx] for r in rows[1:]} == {"lomb"}

    # JSON 의 _meta 에도 남아야 합니다.
    for argv in (["--paired", str(man)], ["--groups", str(arms)]):
        out = _run(argv + ["--psd", "lomb", "--no-sampen", "--json"])
        assert json.loads(out.stdout)["_meta"]["psd_method"] == "lomb"

    # 기본(welch)에서는 Welch 라고 적혀야 합니다.
    out = _run([str(b0), str(v0), "--compare", "--no-sampen"])
    assert "Welch" in out.stdout and "Lomb" not in out.stdout


def test_mixed_psd_methods_are_flagged_not_silently_merged():
    """서로 다른 방법으로 낸 결과를 한 표에 섞으면 경고해야 합니다."""
    from hrvkit.report import psd_method_of
    rr = _rr_with_rsa(200)
    w = analyze_rr(rr, psd_method="welch", do_sampen=False)
    l = analyze_rr(rr, psd_method="lomb", do_sampen=False)
    assert psd_method_of([w, w]) == "welch"
    assert psd_method_of([l, l]) == "lomb"
    assert psd_method_of([w, l]) == "mixed"
    assert psd_method_of([]) == ""
    # 주파수영역이 생략된 결과만 있으면 방법을 주장하지 않습니다.
    short = analyze_rr([800.0] * 6, psd_method="lomb")
    assert psd_method_of([short]) == ""


def test_example_record_total_power_tracks_sdnn_squared():
    """Parseval 정합성을 **동봉 예제**로 고정합니다. total power(VLF+LF+HF)는
    분산=SDNN² 에 가까워야 하고, Lomb 이 Welch 보다 확실히 가까워야 합니다.
    README 의 비교 표가 주장하는 바로 그 성질입니다."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "session_20min.csv")
    from hrvkit.dataio import load_series
    rr, _meta = load_series(path)
    w = analyze_rr(rr, do_sampen=False, psd_method="welch")
    l = analyze_rr(rr, do_sampen=False, psd_method="lomb")
    sdnn_sq = w.time["sdnn"] ** 2
    err_w = abs(w.freq["total_power"] - sdnn_sq) / sdnn_sq
    err_l = abs(l.freq["total_power"] - sdnn_sq) / sdnn_sq
    assert err_l < 0.05, f"lomb total power off by {err_l:.1%}"
    assert err_w > 0.15, f"welch expected to lose >15%, lost {err_w:.1%}"
    assert err_l < err_w
    # 20분 기록이면 Lomb 은 VLF 를 신뢰 가능(README 주장), Welch 는 아님.
    assert l.freq["vlf_reliable"] is True
    assert w.freq["vlf_reliable"] is False


# --------------------------------------------------------------------------- #
# numpy/scipy 독립 참조 (있을 때만)
# --------------------------------------------------------------------------- #
def test_matches_scipy_lombscargle_if_available():
    scipy_signal = pytest.importorskip("scipy.signal")
    times = _irregular_times(150.0, 0.8, 0.2, seed=31)
    vals = [800.0 + 40.0 * math.sin(2 * math.pi * 0.25 * t) for t in times]
    mean_v = sum(vals) / len(vals)
    xc = [v - mean_v for v in vals]
    df, nfreq = 0.004, 90
    freqs, power = lombscargle_grid(times, vals, df, nfreq)
    ref = scipy_signal.lombscargle(
        times, xc, [2.0 * math.pi * f for f in freqs], normalize=False)
    for got, exp in zip(power, ref):
        assert got == pytest.approx(float(exp), rel=1e-8, abs=1e-8)


# --------------------------------------------------------------------------- #
# 변이 검정(mutation testing)이 드러낸 빈틈에 대한 회귀 테스트
# --------------------------------------------------------------------------- #
def test_upper_hf_band_is_covered_by_the_grid():
    """격자 상한이 HF 상단(0.40 Hz)까지 실제로 덮어야 합니다.

    이전 테스트는 호흡을 0.20–0.30 Hz 로만 잡아서, f_max 를 0.30 으로 줄여도
    아무 테스트도 깨지지 않았습니다(HF 파워가 500배 틀려도 조용히 통과).
    빠른 호흡(21회/분 = 0.35 Hz)은 임상에서 흔합니다.
    """
    amp, resp = 45.0, 0.35
    rr = _rr_with_rsa(500, mean=800.0, amp=amp, resp_hz=resp)
    f = frequency_domain(rr, method="lomb")
    assert f["peak_hf"] == pytest.approx(resp, abs=0.02)
    assert f["resp_rate_brpm"] == pytest.approx(resp * 60.0, abs=1.5)
    assert f["hf_power"] == pytest.approx(amp * amp / 2.0, rel=0.25)
    freqs, _psd, _m = lombscargle_psd(_beat_times(rr), rr)
    assert freqs[-1] >= HF_BAND[1]


def test_slow_breathing_regime_is_detected_under_lomb():
    """BELL-001 의 핵심 개입(6회/분 = 0.1 Hz)이 lomb 에서도 인식돼야 합니다.

    이 분기는 HF n.u./LF-HF 의 방향 해석을 통째로 뒤집는 안전장치인데,
    lomb 경로에서는 한 번도 검정된 적이 없었습니다.
    """
    rr = _rr_with_rsa(400, mean=1000.0, amp=60.0, resp_hz=0.10)
    f = frequency_domain(rr, method="lomb")
    assert f["slow_breathing_regime"] is True
    assert f["resp_source"] == "LF"
    assert f["resp_rate_brpm"] == pytest.approx(6.0, abs=0.6)

    res = analyze_rr(rr, psd_method="lomb", do_sampen=False)
    assert any("느린/공명 호흡 레짐" in w for w in res.warnings)
    assert "느린/공명 호흡 레짐" in res.takeaway
    # 자발 호흡(0.25 Hz)에서는 오탐이 없어야 합니다.
    calm = frequency_domain(_rr_with_rsa(400), method="lomb")
    assert calm["slow_breathing_regime"] is False
    assert calm["resp_source"] == "HF"


def test_ls_oversample_is_honoured_end_to_end(tmp_path):
    """--ls-oversample 이 CLI → analyze_rr → frequency_domain 까지 실제로 전달되는지.

    이전에는 세 군데 모두 4.0 으로 하드코딩해도 424개 테스트가 전부 통과했습니다.
    """
    rr = _rr_with_rsa(300)
    prev = None
    for k in (1.0, 4.0, 16.0):
        f = analyze_rr(rr, psd_method="lomb", ls_oversample=k,
                       do_sampen=False).freq
        assert f["ls_df_hz"] == pytest.approx(1.0 / (k * f["ls_span_sec"]),
                                              rel=1e-12)
        assert prev is None or f["ls_nfreq"] > prev
        prev = f["ls_nfreq"]

    p = tmp_path / "rr.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in rr), encoding="utf-8")
    seen = []
    for k in ("1", "8"):
        out = _run([str(p), "--psd", "lomb", "--ls-oversample", k,
                    "--json", "--no-sampen"])
        assert out.returncode == 0, out.stderr
        f = json.loads(out.stdout)["frequency_domain"]
        assert f["ls_df_hz"] == pytest.approx(
            1.0 / (float(k) * f["ls_span_sec"]), rel=1e-9)
        seen.append(f["ls_nfreq"])
    assert seen[1] > seen[0]

    # --window 경로도 같은 값을 전달해야 합니다(창 JSON 은 평탄 스키마라
    # ls_* 키를 싣지 않으므로 라이브러리에서 직접 확인합니다).
    from hrvkit.window import analyze_windows
    for k in (2.0, 16.0):
        ws = analyze_windows(_rr_with_rsa(900), window_sec=300.0,
                             psd_method="lomb", ls_oversample=k,
                             do_sampen=False)
        f0 = ws.ok_windows[0].result.freq
        assert f0["ls_df_hz"] == pytest.approx(
            1.0 / (k * f0["ls_span_sec"]), rel=1e-12)


def test_lomb_report_text_does_not_suggest_nperseg(tmp_path):
    """lomb 리포트의 VLF 주석이 Welch 문구('--nperseg 로 확대')를 쓰면 안 됩니다 —
    --nperseg 는 lomb 에서 아무 일도 하지 않습니다."""
    p = tmp_path / "short.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(300)),
                 encoding="utf-8")
    out = _run([str(p), "--psd", "lomb", "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "더 긴 기록 필요" in out.stdout
    assert "--nperseg" not in out.stdout

    long_f = tmp_path / "long.csv"
    long_f.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(900)),
                      encoding="utf-8")
    out = _run([long_f.as_posix(), "--window", "300", "--psd", "lomb",
                "--no-sampen"])
    assert out.returncode == 0, out.stderr
    assert "각 구간(epoch)" in out.stdout
    assert "Welch 구간이" not in out.stdout


def test_unresolvable_band_yields_nan_not_infinite_lf_hf():
    """해상 불가(NaN) 대역을 0 처럼 다뤄 LF/HF=∞ 를 내면 안 됩니다.

    예전에는 hf=NaN 일 때 `hf > 0` 이 False 라 LF/HF 가 inf 가 되고, 해석 엔진이
    그것을 '교감 우세 — 각성·스트레스 부하' 로 단정했습니다(데이터가 없는데!).
    """
    rr = _rr_with_rsa(300)
    f = frequency_domain(rr, method="lomb", ls_oversample=0.002)
    assert math.isnan(f["hf_power"])
    assert math.isnan(f["lf_hf_ratio"]), "NaN 대역인데 LF/HF 가 유한/무한"
    assert math.isnan(f["hf_nu"]) and math.isnan(f["lf_nu"])
    assert f["slow_breathing_regime"] is False

    res = analyze_rr(rr, psd_method="lomb", ls_oversample=0.002,
                     do_sampen=False)
    assert "판단하지 않습니다" in res.takeaway
    assert "교감 쪽으로 치우침" not in res.takeaway


def test_grid_cap_never_goes_below_resolution():
    """격자 상한이 걸려도 과표본 배수는 1 아래로 떨어지지 않아야 합니다
    (그 아래는 거부 — test_record_too_long_for_lomb_is_refused... 참조)."""
    # 상한에 걸리되 아직 1 이상인 구간(≈2시간).
    times = _irregular_times(7000.0, 0.8, 0.15, seed=13)
    vals = [800.0 + 20.0 * math.sin(2 * math.pi * 0.25 * t) for t in times]
    _f, _p, meta = lombscargle_psd(times, vals, oversample=4.0)
    assert meta["ls_grid_capped"] is True
    assert 1.0 <= meta["ls_oversample"] < 4.0
    assert meta["ls_df_hz"] <= 1.0 / meta["ls_span_sec"]
    # 짧은 기록은 상한에 걸리지 않습니다.
    _f2, _p2, m2 = lombscargle_psd(*_pair(_rr_with_rsa(300)), oversample=4.0)
    assert m2["ls_grid_capped"] is False
    assert m2["ls_oversample"] == pytest.approx(4.0)


def _pair(rr):
    return _beat_times(rr), rr


def test_lomb_reports_no_resample_fs():
    """lomb 은 리샘플하지 않으므로 resample_fs 를 숫자로 내보내면 거짓말입니다."""
    rr = _rr_with_rsa(200)
    assert frequency_domain(rr, method="lomb", fs=8.0)["resample_fs"] is None
    assert frequency_domain(rr, method="welch", fs=8.0)["resample_fs"] == 8.0


def test_lomb_grid_rejects_nonpositive_df():
    assert lombscargle_grid([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 1.0, 2.0],
                            0.0, 5) == ([], [])
    assert lombscargle_grid([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 1.0, 2.0],
                            -0.1, 5) == ([], [])


def test_ls_n_beats_matches_input():
    rr = _rr_with_rsa(237)
    assert frequency_domain(rr, method="lomb")["ls_n_beats"] == 237


def test_record_too_long_for_lomb_is_refused_not_silently_wrong():
    """기록이 격자 상한을 넘어 해상도보다 성긴 격자가 되면 **거부**해야 합니다.

    4시간 기록에서 조용히 LF −30 %·HF +46 %·LF/HF 2.1배 오차를 내던 경로입니다.
    거부는 analyze_rr 이 잡아 경고 + NaN 지표로 우아하게 낮춰집니다.
    """
    times = _irregular_times(11000.0, 0.8, 0.15, seed=31)   # ≈3.06 h > 2.84 h
    vals = [800.0 + 20.0 * math.sin(2 * math.pi * 0.25 * t) for t in times]
    with pytest.raises(ValueError, match="너무 길어"):
        lombscargle_psd(times, vals, oversample=4.0)

    rr = [t2 - t1 for t1, t2 in zip(times, times[1:])]
    rr = [v * 1000.0 for v in rr]
    res = analyze_rr(rr, psd_method="lomb", do_sampen=False)
    assert res.freq["psd_method"] is None
    assert any("--window" in w for w in res.warnings)
    # welch 는 같은 기록을 정상 처리해야 합니다(대안 안내가 실제로 통해야 함).
    assert analyze_rr(rr, psd_method="welch",
                      do_sampen=False).freq["psd_method"] == "welch"


def test_cli_rejects_oversample_below_one(tmp_path):
    """K<1 은 격자를 해상도보다 성기게 만들어 대역 파워를 크게 틀어 놓습니다."""
    p = tmp_path / "rr.csv"
    p.write_text("\n".join(f"{v:.3f}" for v in _rr_with_rsa(200)),
                 encoding="utf-8")
    for bad in ("0.2", "0.99"):
        out = _run([str(p), "--psd", "lomb", "--ls-oversample", bad])
        assert out.returncode == 2
        assert "--ls-oversample" in out.stderr
    out = _run([str(p), "--psd", "lomb", "--ls-oversample", "1", "--no-sampen"])
    assert out.returncode == 0, out.stderr


def test_bands_above_mean_nyquist_are_nan_not_invented():
    """평균 표본율이 담을 수 없는 대역은 값을 내면 안 됩니다.

    4박동·20 s 기록이 HF 파워 25410 ms² 와 호흡수 17.3회/분 을 내던 경로입니다.
    """
    f = frequency_domain([5000.0, 5200.0, 5100.0, 5300.0], method="lomb")
    assert f["ls_nyquist_hz"] < HF_BAND[0]
    assert math.isnan(f["hf_power"])
    assert f["peak_hf"] is None
    assert f["resp_rate_brpm"] is None


def test_extreme_values_fail_fast_instead_of_exhausting_memory(tmp_path):
    """비생리적으로 큰 RR 이 히스토그램/리샘플 격자를 무한정 키워 OOM 으로
    멈추던 기존 결함 — 이제 즉시 깔끔한 경고로 끝나야 합니다."""
    p = tmp_path / "extreme.csv"
    p.write_text("rr_ms\n" + "9e99\n" * 3 + "800\n" * 100, encoding="utf-8")
    for method in ("welch", "lomb"):
        out = subprocess.run(
            [sys.executable, "-m", "hrvkit.cli", str(p), "--psd", method,
             "--clean", "none", "--no-sampen"],
            capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert "주파수영역 분석 생략" in out.stdout
    # 기하학적 지표만 NaN 이 되고 나머지 시간영역 지표는 살아 있어야 합니다.
    from hrvkit.timedomain import geometric_indices, time_domain
    wide = [9e99, 9e99, 800.0, 810.0, 790.0]
    assert math.isnan(geometric_indices(wide)["hti"])
    assert math.isfinite(time_domain([800.0, 810.0, 790.0, 805.0])["rmssd"])
