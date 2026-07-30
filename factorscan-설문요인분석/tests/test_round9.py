"""9차: 2라운드 적대적 검토 지적사항의 회귀 테스트.

각 테스트는 패널이 재현한 결함에 1:1로 대응한다.
"""
from __future__ import annotations

import csv
import io
import itertools
import json
import math
import unicodedata

import numpy as np
import pytest

from factorscan import efa, stats
from factorscan.analyze import GROUP_NULL_REPS, analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise, normalize_name
from factorscan.report import loadings_table_csv, render

from tests.test_round7 import _grouped_data, _likert, _prep, _two_factor  # noqa: F401


# ============================================ 집단 판정: 자료 기반 널 기준선
def _null_pop(ng, p, k, lam, rng):
    n = ng * 2
    f = rng.standard_normal((n, k))
    u = math.sqrt(max(1 - lam ** 2, 1e-6))
    x = np.column_stack([np.clip(np.rint(3 + lam * f[:, i % k] + u * rng.standard_normal(n)), 1, 5)
                         for i in range(p)])
    return x, ["A"] * ng + ["B"] * ng


@pytest.mark.parametrize("p,k,lam,ratio", [(12, 3, 0.55, 4.0), (12, 3, 0.75, 3.0)])
def test_null_reference_controls_false_alarms(p, k, lam, ratio):
    """고정 관문(문항당 3명)만으로는 동일 모집단에서도 오경보가 최대 89%였다.

    널 기준선(같은 자료를 무작위 분할)을 판정 기준으로 삼아 명목 5% 수준으로 내렸다.
    """
    rng = np.random.default_rng(11)
    fired = reps = 0
    for _ in range(15):
        x, labels = _null_pop(int(round(ratio * p)), p, k, lam, rng)
        res = analyze(_prep(x), n_factors=k, parallel_iter=0, group_labels=labels)
        reps += 1
        if any("재현되지 않습니다" in w for w in res["warnings"]):
            fired += 1
    assert fired <= 3, f"동일 모집단에서 {fired}/{reps} 오경보"


def test_null_reference_still_detects_real_difference():
    x, labels = _grouped_data(n=100, seed=19, broken=True)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    assert any("재현되지 않습니다" in w for w in res["warnings"])
    gr = res["group_replicability"]
    assert gr["null_reference"] is not None
    assert gr["min_congruence"] < gr["null_reference"]["p_low"]


def test_null_reference_fields_and_report_line():
    x, labels = _grouped_data(n=100, seed=23)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    nr = res["group_replicability"]["null_reference"]
    assert nr is not None
    assert 0.0 <= nr["p_low"] <= nr["median"] <= 1.0
    assert nr["n_ok"] <= nr["n_rep"] == GROUP_NULL_REPS
    assert "널 기준선" in render(res)


def test_null_reference_skipped_for_polychoric():
    """폴리코릭은 비용이 커서 널 기준선을 만들지 않고 고정 기준으로 돌아간다."""
    x, labels = _grouped_data(n=60, seed=29)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, correlation="polychoric",
                  group_labels=labels)
    assert res["group_replicability"]["null_reference"] is None


def test_congruence_null_reference_is_deterministic_and_bounded():
    x = _likert(_two_factor(n=200, p=6, seed=31))
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    a = efa.congruence_null_reference(x, [100, 100], 2, ref, n_rep=40, seed=1)
    b = efa.congruence_null_reference(x, [100, 100], 2, ref, n_rep=40, seed=1)
    assert a == b                                   # 시드 재현성
    assert a["p_low"] <= a["median"]
    assert efa.congruence_null_reference(x, [100], 2, ref, n_rep=10) is None   # 집단 1개
    assert efa.congruence_null_reference(x, [500, 500], 2, ref, n_rep=10) is None  # 표본 초과


def test_min_congruence_cleared_when_not_judged():
    """판정을 보류했는데 JSON에는 판정용 숫자가 남아 있던 문제."""
    x, labels = _grouped_data(n=22, seed=37)        # 문항당 2.75명 → 전부 판정보류
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    gr = res["group_replicability"]
    assert gr["n_judged"] < 2
    assert gr["min_congruence"] is None


def test_difference_warning_states_the_five_percent_level():
    x, labels = _grouped_data(n=100, seed=41, broken=True)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    w = [w for w in res["warnings"] if "재현되지 않습니다" in w]
    assert w and "20번에 1번" in w[0]


# ============================================ 정확한 요인 대응(Hungarian)
def test_hungarian_matches_brute_force_for_small_k():
    rng = np.random.default_rng(53)
    for _ in range(120):
        k = int(rng.integers(2, 8))
        s = rng.random((k, k))
        got = efa._hungarian_max(s)
        assert sorted(got) == list(range(k))
        best = max(itertools.permutations(range(k)),
                   key=lambda pm: sum(s[j, pm[j]] for j in range(k)))
        assert (sum(s[j, got[j]] for j in range(k))
                == pytest.approx(sum(s[j, best[j]] for j in range(k)), abs=1e-12))


def test_match_factors_large_k_is_optimal_not_greedy():
    """탐욕법은 k=8에서 91%, k=12에서 98% 최적해를 놓쳤다(손실 최대 30%)."""
    rng = np.random.default_rng(59)
    worse = 0
    for _ in range(60):
        k = int(rng.integers(8, 13))
        load = rng.random((k * 2, k))
        target = np.zeros((k * 2, k))
        for i in range(k * 2):
            target[i, i % k] = 1.0
        perm, phi = efa.match_factors(load, target)
        assert sorted(perm) == list(range(k))
        # 탐욕 해와 비교해 절대 나쁘지 않아야 한다.
        score = np.zeros((k, k))
        for j in range(k):
            col = np.repeat(target[:, [j]], k, axis=1)
            score[j] = np.abs(np.nan_to_num(efa.tucker_congruence(load, col)))
        greedy, used = [-1] * k, set()
        for j in np.argsort(-score.max(axis=1)):
            for c in np.argsort(-score[j]):
                if int(c) not in used:
                    greedy[int(j)] = int(c)
                    used.add(int(c))
                    break
        if (sum(score[j, perm[j]] for j in range(k))
                < sum(score[j, greedy[j]] for j in range(k)) - 1e-12):
            worse += 1
    assert worse == 0


def test_hungarian_rejects_non_square():
    with pytest.raises(ValueError):
        efa._hungarian_max(np.zeros((3, 4)))


# ============================================ 목표φ: 가설 밖 문항 제외
def test_target_congruence_ignores_items_outside_hypothesis():
    """가설 밖 문항이 분모에만 들어가 φ를 기계적으로 끌어내리던 문제(0.87까지)."""
    rng = np.random.default_rng(61)
    n, p, k = 400, 24, 3
    f = rng.standard_normal((n, k))
    x = np.column_stack([0.8 * f[:, i // 8] + 0.6 * rng.standard_normal(n) for i in range(p)])
    names = [f"Q{i+1}" for i in range(p)]
    prep = listwise(Dataset(names=names, data=x))
    full = {"A": names[:8], "B": names[8:16], "C": names[16:]}
    part = {"A": names[:6], "B": names[8:14], "C": names[16:22]}
    tc_full = analyze(prep, n_factors=3, parallel_iter=0,
                      structure=full)["hypothesis"]["target_congruence"]
    tc_part = analyze(prep, n_factors=3, parallel_iter=0,
                      structure=part)["hypothesis"]["target_congruence"]
    assert min(tc_full) > 0.95 and min(tc_part) > 0.95
    assert abs(min(tc_full) - min(tc_part)) < 0.05


def test_target_congruence_is_reported_as_magnitude():
    """부호는 요인 부호 관례에 좌우돼 임의다 — 크기만 보고해야 한다."""
    x, _ = _grouped_data(n=120, seed=67)
    names = [f"Q{i+1}" for i in range(8)]
    res = analyze(listwise(Dataset(names=names, data=x)), n_factors=2, parallel_iter=0,
                  structure={"A": names[:4], "B": names[4:]})
    tc = res["hypothesis"]["target_congruence"]
    assert all(v is None or v >= 0 for v in tc)
    assert res["hypothesis"]["target_congruence_flipped"] is not None


# ============================================ 부트스트랩 게이트가 CSV까지
def test_csv_out_respects_bootstrap_reliability_gate(tmp_path, capsys):
    """본문은 구간을 거부했는데 논문 부록 CSV에는 폭 0짜리 '95% CI'가 실렸다."""
    rng = np.random.default_rng(71)
    n, p = 300, 20
    f = rng.standard_normal((n, 4))
    x = np.column_stack([np.clip(np.rint(3 + 0.8 * f[:, i // 5] + 0.8 * rng.standard_normal(n)),
                                 1, 5) for i in range(p)])
    res = analyze(_prep(x), n_factors=8, parallel_iter=0, extraction="paf", bootstrap=3)
    text = loadings_table_csv(res)
    if res["bootstrap"].get("reliable") is False:
        assert "primary_loading_ci_lo" not in text
        assert "bootstrap" not in text
        assert "신뢰구간을 만들지 않았습니다" in render(res)


def test_csv_out_includes_ci_when_reliable(tmp_path, capsys):
    x = _likert(_two_factor(n=200, p=6, seed=73))
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, bootstrap=120)
    assert res["bootstrap"]["reliable"] is True
    text = loadings_table_csv(res)
    assert "primary_loading_ci_lo" in text


# ============================================ ω/α 괴리 경고 보정
@pytest.mark.parametrize("items_per,lam,extraction", [(2, 0.60, "pca"), (3, 0.60, "pca"),
                                                      (2, 0.75, "pca")])
def test_omega_alpha_gap_no_false_alarm_on_clean_short_pca_subscales(items_per, lam, extraction):
    """PCA의 ω는 정의상 α보다 낙관적이고 문항이 적을수록 격차가 커진다.
    고정 0.20을 쓰면 2문항 하위척도가 정상인데도 20/20 발화했다."""
    rng = np.random.default_rng(79)
    fired = 0
    for _ in range(10):
        n, k = 300, 2
        p = items_per * k
        f = rng.standard_normal((n, k))
        u = math.sqrt(1 - lam ** 2)
        x = np.column_stack([np.clip(np.rint(3 + lam * f[:, i // items_per]
                                             + u * rng.standard_normal(n)), 1, 5)
                             for i in range(p)])
        res = analyze(_prep(x), n_factors=2, parallel_iter=0, extraction=extraction)
        if any("ω와 Cronbach α가 크게 다른" in w for w in res["warnings"]):
            fired += 1
    assert fired == 0


def test_omega_alpha_gap_still_fires_on_reversed_items():
    """보정을 넣으면서 진짜 신호(역문항 미처리로 α 붕괴)를 놓치면 안 된다."""
    rng = np.random.default_rng(83)
    n = 300
    f = rng.standard_normal((n, 2))
    cols = []
    for i in range(8):
        s = -1 if i in (1, 5) else 1
        cols.append(np.clip(np.rint(3 + s * 0.9 * f[:, i // 4] + 0.6 * rng.standard_normal(n)), 1, 5))
    res = analyze(_prep(np.column_stack(cols)), n_factors=2, parallel_iter=0)
    assert any("ω와 Cronbach α가 크게 다른" in w for w in res["warnings"])


# ============================================ 이질 척도 탐지 보정
def test_scale_outlier_no_false_alarm_on_mixed_format_battery():
    """이분문항 7개 + 0~4 중증도 문항은 **정상적인** 혼합 배터리다.
    이전에는 가장 정보량 많은 문항을 지우라고 안내했다."""
    rng = np.random.default_rng(89)
    n = 300
    f = rng.standard_normal(n)
    cols = [(0.7 * f + 0.7 * rng.standard_normal(n) > 1.2).astype(float) for _ in range(7)]
    cols.append(np.clip(np.rint(2 + 0.9 * f + 0.7 * rng.standard_normal(n)), 0, 4))
    names = [f"증상{i+1}" for i in range(7)] + ["중증도0_4"]
    res = analyze(listwise(Dataset(names=names, data=np.column_stack(cols))),
                  n_factors=1, parallel_iter=0)
    assert not any("척도가 크게 다른 열" in w for w in res["warnings"])


def test_scale_outlier_still_flags_a_covariate():
    """나이(고유값 수십 개)는 여전히 잡아야 한다."""
    rng = np.random.default_rng(97)
    n = 200
    f = rng.standard_normal((n, 2))
    cols = [np.clip(np.rint(3 + 0.9 * f[:, i // 4] + 0.6 * rng.standard_normal(n)), 1, 5)
            for i in range(8)]
    cols.append(rng.integers(20, 80, n).astype(float))
    names = [f"Q{i+1}" for i in range(8)] + ["연령"]
    res = analyze(listwise(Dataset(names=names, data=np.column_stack(cols))),
                  n_factors=2, parallel_iter=0)
    assert any("척도가 크게 다른 열" in w and "연령" in w for w in res["warnings"])


# ============================================ 이름 정규화(NFC/제어문자)
@pytest.mark.parametrize("raw,expect", [
    ("Q1", "Q1"), ("  Q1  ", "Q1"), ("Q1​A", "Q1A"), ("﻿Q1", "Q1"),
    ("Q2\nEVIL", "Q2 EVIL"), ("Q3\tX", "Q3 X"), ("Q4\r\nY", "Q4 Y"),
])
def test_normalize_name(raw, expect):
    assert normalize_name(raw) == expect


def test_normalize_name_unifies_nfc_and_nfd():
    nfc = unicodedata.normalize("NFC", "수면질")
    nfd = unicodedata.normalize("NFD", "수면질")
    assert nfc != nfd                       # 바이트는 다르다
    assert normalize_name(nfc) == normalize_name(nfd)   # 비교는 같아야 한다


def test_nfd_header_matches_nfc_config(tmp_path, capsys):
    """macOS가 만든 CSV(NFD 한글)와 손으로 친 설정(NFC)이 어긋나
    '보이는데 못 찾는' 오류를 내던 문제."""
    rng = np.random.default_rng(101)
    n = 120
    f = rng.standard_normal((n, 2))
    x = np.column_stack([np.clip(np.rint(3 + 0.9 * f[:, i // 3] + 0.6 * rng.standard_normal(n)),
                                 1, 5).astype(int) for i in range(6)])
    nfc = ["수면질", "수면량", "주간졸림", "피로", "기분", "불안"]
    path = tmp_path / "nfd.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([unicodedata.normalize("NFD", "환자번호")]
                   + [unicodedata.normalize("NFD", c) for c in nfc])
        for i, row in enumerate(x):
            w.writerow([f"P{i:03d}"] + list(row))
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"id_cols": ["환자번호"],
                               "structure": {"수면": nfc[:3], "기분": nfc[3:]}},
                              ensure_ascii=False), encoding="utf-8")
    rc = run([str(path), "--config", str(cfg), "--n-factors", "2", "--parallel-iter", "0"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "가설 요인구조 대조" in out.out
    assert "6/6 (100%)" in out.out


def test_newline_in_header_does_not_break_report_table(tmp_path, capsys):
    rng = np.random.default_rng(103)
    x = rng.integers(1, 6, (100, 4))
    path = tmp_path / "nl.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Q1", "Q2\nEVIL ROW: 9,9,9", "Q3", "Q4"])
        w.writerows(x)
    rc = run([str(path), "--n-factors", "2", "--parallel-iter", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "EVIL ROW" in out
    # 헤더 줄바꿈이 표를 쪼개면 이 줄이 단독 행으로 남는다.
    assert not any(l.strip().startswith("EVIL ROW") for l in out.splitlines())


# ============================================ 범주표 오버플로 방어
def test_category_frequencies_rejects_values_beyond_int64_precision():
    """1e19 값이 int64로 포화해 9223372036854775807 이라는 가짜 범주를 100%로 만들었다."""
    x = np.array([[1e19], [2e19], [1e19]])
    assert efa.category_frequencies(x, ["Q1"]) is None


def test_category_frequencies_still_works_at_normal_scale():
    x = np.array([[1.0], [2.0], [3.0]])
    assert efa.category_frequencies(x, ["Q1"]) is not None


# ============================================ 수치 정확도(2라운드 지적)
@pytest.mark.parametrize("x,d1,d2", [(0.5, 1e5, 1000), (0.1, 1e5, 50), (2.0, 1000, 1e5)])
def test_f_distribution_far_tail_does_not_collapse_to_zero(x, d1, d2):
    scipy_stats = pytest.importorskip("scipy.stats")
    assert stats.f_cdf(x, d1, d2) == pytest.approx(scipy_stats.f.cdf(x, d1, d2), rel=1e-8)
    assert stats.f_sf(x, d1, d2) == pytest.approx(scipy_stats.f.sf(x, d1, d2), rel=1e-8)


@pytest.mark.parametrize("x,df,nc", [(1e-6, 10, 100), (20, 500, 100), (100, 30, 1000)])
def test_ncx2_cdf_deep_tail_does_not_collapse(x, df, nc):
    scipy_stats = pytest.importorskip("scipy.stats")
    got = stats.ncx2_cdf(x, df, nc)
    assert got > 0.0
    assert got == pytest.approx(scipy_stats.ncx2.cdf(x, df, nc), rel=1e-2)


def test_f_ppf_tiny_p_keeps_relative_accuracy():
    scipy_stats = pytest.importorskip("scipy.stats")
    for p, d1, d2 in [(1e-15, 1, 10000), (1e-12, 1, 100), (1e-9, 3, 7)]:
        assert stats.f_ppf(p, d1, d2) == pytest.approx(scipy_stats.f.ppf(p, d1, d2), rel=1e-8)


def test_ncx2_nc_for_quantile_returns_quickly_at_huge_chi_square():
    import time
    t0 = time.time()
    v = stats.ncx2_nc_for_quantile(3e6, 10, 0.95)
    # 이전에는 상한을 1에서 2배씩 올리며 매번 급수를 돌아 20초를 넘겼다(멈춘 줄 안다).
    assert time.time() - t0 < 15.0
    assert v >= 0.0


# ============================================ 요인 자동 명명
def test_factor_names_derived_from_structure():
    x, _ = _grouped_data(n=120, seed=107)
    names = [f"Q{i+1}" for i in range(8)]
    res = analyze(listwise(Dataset(names=names, data=x)), n_factors=2, parallel_iter=0,
                  structure={"수면": names[:4], "기능": names[4:]})
    fn = res["factor_names"]
    assert fn is not None and sorted(v for v in fn if v) == ["기능", "수면"]
    text = render(res)
    assert "요인 이름:" in text
    assert "(수면)" in text or "(기능)" in text


def test_factor_names_absent_without_structure():
    x, _ = _grouped_data(n=60, seed=109)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0)
    assert res["factor_names"] is None
    assert "요인 이름:" not in render(res)


# ============================================ 비례배분 채점
def test_prorated_scores_recover_partially_missing_respondents():
    x = np.array([[1.0, 2.0, 3.0, 4.0],
                  [np.nan, 2.0, 3.0, 4.0],
                  [np.nan, np.nan, 3.0, 4.0]])
    groups = [0, 0, 0, 0]
    s, imp = efa.prorated_subscale_scores(x, groups, 1, method="sum", max_missing_prop=0.25)
    assert s[0, 0] == pytest.approx(10.0)
    assert s[1, 0] == pytest.approx(3.0 * 4)        # mean(2,3,4)=3 → 3×4
    assert np.isnan(s[2, 0])                        # 50% 결측 → 허용치 초과
    assert list(imp) == [0, 1, 0]


def test_prorated_mean_method_and_validation():
    x = np.array([[1.0, 3.0], [np.nan, 3.0]])
    s, _ = efa.prorated_subscale_scores(x, [0, 0], 1, method="mean", max_missing_prop=0.5)
    assert s[0, 0] == pytest.approx(2.0) and s[1, 0] == pytest.approx(3.0)
    with pytest.raises(ValueError):
        efa.prorated_subscale_scores(x, [0, 0], 1, method="regression")
    with pytest.raises(ValueError):
        efa.prorated_subscale_scores(x, [0, 0], 1, max_missing_prop=1.5)


def test_cli_prorate_scores_all_respondents(tmp_path, capsys):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "sc.csv"
    rc = run([str(root / "examples/sleep_scale.csv"),
              "--config", str(root / "examples/sleep_config.json"),
              "--parallel-iter", "0", "--scores-out", str(out),
              "--score-missing", "prorate", "--max-missing-prop", "0.25"])
    cap = capsys.readouterr()
    assert rc == 0
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) - 1 == 80                       # listwise 였다면 77명
    assert rows[0][-1] == "대체된_문항응답수"
    imputed = sum(1 for r in rows[1:] if int(r[-1]) > 0)
    assert imputed == 3                              # 번들 예시의 결측 응답자 3명
    assert "비례배분" in cap.err


def test_cli_prorate_rejects_regression_method(capsys):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rc = run([str(root / "examples/sleep_scale.csv"),
              "--config", str(root / "examples/sleep_config.json"),
              "--scores-out", "/tmp/x.csv", "--score-missing", "prorate",
              "--score-method", "regression"])
    assert rc == 2
    assert "prorate" in capsys.readouterr().err


def test_cli_rejects_bad_max_missing_prop(capsys):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rc = run([str(root / "examples/sleep_scale.csv"),
              "--config", str(root / "examples/sleep_config.json"),
              "--max-missing-prop", "1.5"])
    assert rc == 2
    assert "max-missing-prop" in capsys.readouterr().err
