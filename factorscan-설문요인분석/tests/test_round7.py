"""7차: 부트스트랩 안정성 · α 신뢰구간(Feldt) · 집단별 구조 재현성(Tucker φ).

이 라운드에서 새로 만든 기능의 회귀·정확성 테스트. 통계값은 가능한 한
**구현과 다른 식**(정의식 재계산 또는 scipy 오라클)으로 검증한다.
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from factorscan import efa, stats
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise

ROOT = Path(__file__).resolve().parents[1]


def _two_factor(n=200, p=8, seed=3, noise=0.6):
    rng = np.random.default_rng(seed)
    f1 = rng.standard_normal(n)
    f2 = rng.standard_normal(n)
    cols = []
    for i in range(p):
        base = f1 if i < p // 2 else f2
        cols.append(base + noise * rng.standard_normal(n))
    return np.column_stack(cols)


def _likert(x, lo=1, hi=5):
    z = (x - x.mean(axis=0)) / x.std(axis=0)
    return np.clip(np.rint(3 + z), lo, hi)


# ----------------------------------------------------------- Procrustes 정렬
def test_procrustes_recovers_known_rotation():
    """알려진 직교변환으로 뒤튼 적재를 정렬하면 원본으로 정확히 돌아와야 한다."""
    rng = np.random.default_rng(11)
    ref = rng.standard_normal((10, 3))
    q, _ = np.linalg.qr(rng.standard_normal((3, 3)))
    scrambled = ref @ q
    aligned, t = efa.procrustes_align(scrambled, ref)
    assert np.allclose(aligned, ref, atol=1e-10)
    # T는 직교행렬이어야 한다.
    assert np.allclose(t.T @ t, np.eye(3), atol=1e-10)


def test_procrustes_handles_column_swap_and_sign_flip():
    """요인 순서 교환 + 부호 반전(부트스트랩의 전형적 임의성)도 되돌려야 한다."""
    ref = np.array([[0.9, 0.1], [0.8, 0.0], [0.1, 0.85], [0.0, 0.9]])
    scrambled = np.column_stack([-ref[:, 1], ref[:, 0]])   # 열 교환 + 1열 부호반전
    aligned, _ = efa.procrustes_align(scrambled, ref)
    assert np.allclose(aligned, ref, atol=1e-10)


def test_procrustes_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        efa.procrustes_align(np.zeros((4, 2)), np.zeros((4, 3)))


# ------------------------------------------------------- Tucker 일치계수
def test_tucker_congruence_matches_hand_computation():
    a = np.array([[0.8, 0.1], [0.7, 0.2], [0.1, 0.9]])
    b = np.array([[0.75, 0.0], [0.65, 0.3], [0.2, 0.85]])
    got = efa.tucker_congruence(a, b)
    for j in range(2):
        num = float(np.dot(a[:, j], b[:, j]))
        den = math.sqrt(float(np.dot(a[:, j], a[:, j]) * np.dot(b[:, j], b[:, j])))
        assert got[j] == pytest.approx(num / den, abs=1e-12)


def test_tucker_congruence_identical_and_flipped():
    a = np.array([[0.8, 0.1], [0.2, 0.9]])
    assert np.allclose(efa.tucker_congruence(a, a), 1.0)
    assert np.allclose(efa.tucker_congruence(a, -a), -1.0)


def test_tucker_congruence_zero_column_is_nan():
    a = np.array([[0.0, 0.5], [0.0, 0.6]])
    b = np.array([[0.3, 0.5], [0.4, 0.6]])
    v = efa.tucker_congruence(a, b)
    assert np.isnan(v[0]) and v[1] == pytest.approx(1.0)


def test_tucker_is_scale_invariant_but_not_mean_invariant():
    """φ는 크기 배율에 불변(코사인)이지만, 상관과 달리 평균 이동에는 불변이 아니다."""
    a = np.array([[0.8], [0.6], [0.2]])
    assert efa.tucker_congruence(a, 3.0 * a)[0] == pytest.approx(1.0)
    shifted = a + 0.5
    assert efa.tucker_congruence(a, shifted)[0] < 1.0


# ----------------------------------------------------------- F 분포 / betai
@pytest.mark.parametrize("d1,d2,x", [(5, 10, 2.0), (1, 1, 0.5), (99, 891, 1.3),
                                     (2.5, 3.5, 0.02), (200, 2000, 1.36)])
def test_f_cdf_matches_scipy(d1, d2, x):
    scipy_stats = pytest.importorskip("scipy.stats")
    assert stats.f_cdf(x, d1, d2) == pytest.approx(scipy_stats.f.cdf(x, d1, d2), rel=1e-9)


@pytest.mark.parametrize("d1,d2,p", [(99, 891, 0.975), (99, 891, 0.025), (3, 7, 0.5),
                                     (1, 1, 0.9), (10, 4, 0.99)])
def test_f_ppf_matches_scipy(d1, d2, p):
    scipy_stats = pytest.importorskip("scipy.stats")
    assert stats.f_ppf(p, d1, d2) == pytest.approx(scipy_stats.f.ppf(p, d1, d2), rel=1e-7)


def test_f_ppf_roundtrips_through_cdf():
    """오라클 없이도 성립해야 하는 성질: F_cdf(F_ppf(p)) == p."""
    for p in (0.001, 0.05, 0.5, 0.95, 0.999):
        for d1, d2 in ((4, 9), (30, 120), (1, 3)):
            x = stats.f_ppf(p, d1, d2)
            assert stats.f_cdf(x, d1, d2) == pytest.approx(p, abs=1e-8)


def test_betai_symmetry_identity():
    """I_x(a,b) = 1 − I_{1−x}(b,a) — 구현의 두 분기를 서로 검증한다."""
    for a, b, x in ((2.0, 3.0, 0.3), (0.5, 7.0, 0.9), (11.0, 2.0, 0.4)):
        assert stats.betai(a, b, x) == pytest.approx(1.0 - stats.betai(b, a, 1.0 - x), abs=1e-12)


def test_betai_rejects_bad_domain():
    with pytest.raises(ValueError):
        stats.betai(1.0, 1.0, 1.5)
    with pytest.raises(ValueError):
        stats.betai(0.0, 1.0, 0.5)


# ------------------------------------------------------- Cronbach α 신뢰구간
def test_alpha_ci_feldt_matches_definition():
    """Feldt 구간을 정의식(F 분위수)으로 독립 재계산해 대조."""
    scipy_stats = pytest.importorskip("scipy.stats")
    alpha, n, k = 0.80, 100, 10
    lo, hi = efa.alpha_ci_feldt(alpha, n, k)
    df1, df2 = n - 1, (n - 1) * (k - 1)
    exp_lo = 1.0 - (1.0 - alpha) * scipy_stats.f.ppf(0.975, df1, df2)
    exp_hi = 1.0 - (1.0 - alpha) * scipy_stats.f.ppf(0.025, df1, df2)
    assert lo == pytest.approx(exp_lo, abs=1e-7)
    assert hi == pytest.approx(exp_hi, abs=1e-7)


def test_alpha_ci_brackets_point_estimate_and_narrows_with_n():
    lo_small, hi_small = efa.alpha_ci_feldt(0.85, 30, 6)
    lo_big, hi_big = efa.alpha_ci_feldt(0.85, 1000, 6)
    assert lo_small < 0.85 < hi_small
    assert lo_big < 0.85 < hi_big
    assert (hi_big - lo_big) < (hi_small - lo_small)


def test_alpha_ci_undefined_cases():
    assert efa.alpha_ci_feldt(None, 100, 5) is None
    assert efa.alpha_ci_feldt(float("nan"), 100, 5) is None
    assert efa.alpha_ci_feldt(1.0, 100, 5) is None      # α=1이면 (1−α)=0 → 퇴화
    assert efa.alpha_ci_feldt(0.8, 1, 5) is None
    assert efa.alpha_ci_feldt(0.8, 100, 1) is None


def test_alpha_ci_handles_negative_alpha():
    """역문항 미처리로 α가 음수여도 구간은 정의되고 순서가 유지돼야 한다."""
    lo, hi = efa.alpha_ci_feldt(-0.30, 50, 4)
    assert lo < hi < 1.0


def test_feldt_ci_agrees_with_bootstrap_on_clean_data():
    """서로 다른 원리의 두 구간(모수 Feldt vs 비모수 부트스트랩)이 대략 일치해야 한다."""
    x = _likert(_two_factor(n=250, p=6, seed=21))
    a = efa.cronbach_alpha(x[:, :3])
    lo, hi = efa.alpha_ci_feldt(a, x.shape[0], 3)
    rng = np.random.default_rng(5)
    boots = []
    for _ in range(400):
        idx = rng.integers(0, x.shape[0], x.shape[0])
        v = efa.cronbach_alpha(x[idx, :3])
        if v is not None:
            boots.append(v)
    b_lo, b_hi = np.percentile(boots, [2.5, 97.5])
    assert abs(lo - b_lo) < 0.06 and abs(hi - b_hi) < 0.06


# ---------------------------------------------------------------- 부트스트랩
def test_bootstrap_stability_basic_shape_and_determinism():
    x = _two_factor(n=150, p=6, seed=4)
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    a = efa.bootstrap_stability(x, 2, ref, n_boot=40, seed=1)
    b = efa.bootstrap_stability(x, 2, ref, n_boot=40, seed=1)
    assert a.lo.shape == (6, 2) and a.hi.shape == (6, 2)
    assert np.allclose(a.lo, b.lo) and np.allclose(a.hi, b.hi)   # 시드 재현성
    assert np.all(a.lo <= a.hi + 1e-12)
    assert np.all(a.lo <= a.mean + 1e-12) and np.all(a.mean <= a.hi + 1e-12)
    assert a.n_ok == 40


def test_bootstrap_different_seed_gives_different_intervals():
    x = _two_factor(n=120, p=6, seed=8)
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    a = efa.bootstrap_stability(x, 2, ref, n_boot=40, seed=1)
    b = efa.bootstrap_stability(x, 2, ref, n_boot=40, seed=999)
    assert not np.allclose(a.lo, b.lo)


def test_bootstrap_ci_covers_point_estimate_for_most_items():
    """정렬이 제대로 되면 대부분의 주적재가 자기 구간 안에 들어와야 한다."""
    x = _two_factor(n=300, p=8, seed=12)
    r = efa.correlation_matrix(x)
    ref = efa.apply_sign_convention(efa.varimax(efa.component_loadings(r, 2)))
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=200, seed=2)
    inside = 0
    for i in range(8):
        j = int(np.argmax(np.abs(ref[i])))
        if bs.lo[i, j] - 1e-9 <= ref[i, j] <= bs.hi[i, j] + 1e-9:
            inside += 1
    assert inside >= 7


def test_bootstrap_alignment_beats_no_alignment():
    """Procrustes 정렬이 실제로 구간을 좁히는지(거짓 불안정 제거) 직접 확인."""
    x = _two_factor(n=200, p=6, seed=15)
    r = efa.correlation_matrix(x)
    ref = efa.apply_sign_convention(efa.varimax(efa.component_loadings(r, 2)))
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=120, seed=3)
    aligned_width = float(np.mean(bs.hi - bs.lo))

    # 정렬 없이(부호·순서 임의 그대로) 모았을 때의 폭
    rng = np.random.default_rng(3)
    raw = []
    for _ in range(120):
        idx = rng.integers(0, x.shape[0], x.shape[0])
        rb = efa.correlation_matrix(x[idx])
        raw.append(efa.varimax(efa.component_loadings(rb, 2)))
    stack = np.stack(raw)
    naive_width = float(np.mean(np.percentile(stack, 97.5, axis=0)
                                - np.percentile(stack, 2.5, axis=0)))
    assert aligned_width < naive_width


def test_bootstrap_reports_pa_agreement_and_k_counts():
    x = _two_factor(n=200, p=8, seed=6)
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    pa = efa.parallel_analysis(200, 8, iters=40, seed=0)
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=60, seed=1, pa_reference=pa)
    assert bs.pa_agreement is not None
    assert 0.0 <= bs.pa_agreement <= 1.0
    assert sum(bs.k_counts.values()) == bs.n_ok
    assert bs.pa_agreement == pytest.approx(bs.k_counts.get(2, 0) / bs.n_ok)


def test_bootstrap_without_pa_reference_has_no_agreement():
    x = _two_factor(n=100, p=6, seed=9)
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=10, seed=1)
    assert bs.pa_agreement is None and bs.k_counts == {}


def test_bootstrap_alpha_omega_ci_bracket_full_sample_values():
    x = _likert(_two_factor(n=250, p=6, seed=17))
    r = efa.correlation_matrix(x)
    ref = efa.apply_sign_convention(efa.varimax(efa.component_loadings(r, 2)))
    groups = np.argmax(np.abs(ref), axis=1)
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=150, seed=4)
    full_alpha = efa.alpha_by_group(x, groups, 2)
    for f in range(2):
        ci = bs.alpha_ci[f]
        if ci is not None and full_alpha[f] is not None:
            assert ci[0] - 0.05 <= full_alpha[f] <= ci[1] + 0.05


@pytest.mark.parametrize("extraction,rotation",
                         [("pca", "varimax"), ("pca", "promax"), ("pca", "none"),
                          ("paf", "varimax"), ("ml", "varimax"), ("ml", "promax")])
def test_bootstrap_runs_for_all_extraction_rotation_combos(extraction, rotation):
    x = _two_factor(n=140, p=6, seed=13)
    r = efa.correlation_matrix(x)
    raw = efa.extract_loadings(r, 2, extraction)
    ref, _ = efa.rotate_loadings(raw, rotation)
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=15, seed=1,
                                 extraction=extraction, rotation=rotation)
    assert bs.n_ok >= 10
    assert np.all(np.isfinite(bs.lo)) and np.all(np.isfinite(bs.hi))


def test_bootstrap_survives_constant_column_resamples():
    """재표본에서 상수가 되는 문항이 있어도 예외 없이 n_ok로 보고해야 한다."""
    rng = np.random.default_rng(1)
    x = np.column_stack([rng.integers(1, 6, 30).astype(float) for _ in range(4)])
    x[:, 3] = 1.0
    x[0, 3] = 2.0          # 값이 거의 상수 → 재표본에서 자주 상수가 됨
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    bs = efa.bootstrap_stability(x, 2, ref, n_boot=40, seed=1)
    assert 0 <= bs.n_ok <= 40


def test_bootstrap_all_failures_yield_nan_intervals():
    """모든 재표본이 실패하면 조용히 0을 내지 말고 NaN 구간 + n_ok=0 이어야 한다."""
    x = np.array([[1.0, 2.0], [1.0, 2.0], [1.0, 2.0]])   # 모든 열이 상수
    ref = np.zeros((2, 1))
    bs = efa.bootstrap_stability(x, 1, ref, n_boot=5, seed=1)
    assert bs.n_ok == 0
    assert np.all(np.isnan(bs.lo)) and np.all(np.isnan(bs.hi))
    assert bs.alpha_ci == [None] and bs.omega_ci == [None]


def test_bootstrap_rejects_bad_arguments():
    x = _two_factor(n=50, p=4, seed=1)
    ref = np.zeros((4, 2))
    with pytest.raises(ValueError):
        efa.bootstrap_stability(x, 2, np.zeros((3, 2)), n_boot=5)   # 모양 불일치
    with pytest.raises(ValueError):
        efa.bootstrap_stability(x, 2, ref, n_boot=0)
    with pytest.raises(ValueError):
        efa.bootstrap_stability(x, 2, ref, n_boot=5, conf=1.5)


def test_bootstrap_conf_level_widens_interval():
    x = _two_factor(n=200, p=6, seed=19)
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.component_loadings(r, 2))
    narrow = efa.bootstrap_stability(x, 2, ref, n_boot=100, seed=1, conf=0.80)
    wide = efa.bootstrap_stability(x, 2, ref, n_boot=100, seed=1, conf=0.99)
    assert float(np.mean(wide.hi - wide.lo)) > float(np.mean(narrow.hi - narrow.lo))


def test_extract_and_rotate_helpers_reject_unknown_names():
    r = np.eye(4)
    with pytest.raises(ValueError):
        efa.extract_loadings(r, 2, "oblimin")
    with pytest.raises(ValueError):
        efa.rotate_loadings(np.zeros((4, 2)), "quartimax")


def test_extract_loadings_matches_direct_calls():
    """공용 진입점이 원래 함수와 완전히 같은 값을 내야 한다(경로 분기 방지)."""
    x = _two_factor(n=120, p=6, seed=23)
    r = efa.correlation_matrix(x)
    assert np.allclose(efa.extract_loadings(r, 2, "pca"), efa.component_loadings(r, 2))
    assert np.allclose(efa.extract_loadings(r, 2, "paf"), efa.paf_loadings(r, 2).loadings)
    assert np.allclose(efa.extract_loadings(r, 2, "ml"), efa.ml_factor_analysis(r, 2).loadings)


# ------------------------------------------------- 집단별 구조 재현성(분석 수준)
def _prep(matrix, names=None):
    names = names or [f"Q{i+1}" for i in range(matrix.shape[1])]
    return listwise(Dataset(names=names, data=matrix))


def _grouped_data(n=100, seed=31, broken=True):
    """A집단은 깨끗한 2요인, B집단은 (broken이면) F2가 뒤섞인 구조."""
    rng = np.random.default_rng(seed)
    blocks, labels = [], []
    for tag in ("A", "B"):
        f1, f2 = rng.standard_normal(n), rng.standard_normal(n)
        cols = []
        for i in range(8):
            if i < 4:
                base = f1
            elif tag == "B" and broken:
                base = -f2 if i >= 6 else f2
            else:
                base = f2
            cols.append(np.clip(np.rint(3 + 0.9 * base + 0.5 * rng.standard_normal(n)), 1, 5))
        blocks.append(np.column_stack(cols))
        labels += [tag] * n
    return np.vstack(blocks), labels


def test_group_replicability_flags_broken_structure():
    x, labels = _grouped_data(broken=True)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels,
                  group_name="사이트")
    gr = res["group_replicability"]
    assert gr["column"] == "사이트"
    assert gr["levels"] == ["A", "B"]
    assert gr["min_congruence"] < 0.85
    assert any("재현되지 않습니다" in w for w in res["warnings"])


def test_group_replicability_passes_on_homogeneous_groups():
    x, labels = _grouped_data(broken=False, seed=41)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels,
                  group_name="사이트")
    gr = res["group_replicability"]
    assert gr["min_congruence"] > 0.85
    assert not any("재현되지 않습니다" in w for w in res["warnings"])


def test_group_replicability_congruence_recomputable_from_reported_loadings():
    """보고된 집단별 적재로 φ를 다시 계산하면 보고된 φ와 같아야 한다(자기일관성)."""
    x, labels = _grouped_data(broken=True, seed=53)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    ref = np.array(res["loadings"])
    for row in res["group_replicability"]["groups"]:
        if row.get("skipped"):
            continue
        got = efa.tucker_congruence(np.array(row["loadings"]), ref)
        assert np.allclose(got, np.array(row["congruence"], dtype=float), atol=1e-10)


def test_group_pairwise_catches_disagreement_invisible_vs_overall():
    """전체 대비 φ만 보면 놓치는 '집단끼리의 차이'를 쌍별 비교가 잡아야 한다."""
    x, labels = _grouped_data(broken=True, seed=61)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    pw = res["group_replicability"]["pairwise"]
    assert len(pw) == 1 and {pw[0]["a"], pw[0]["b"]} == {"A", "B"}
    assert min(v for v in pw[0]["congruence"] if v is not None) < 0.85


def test_group_skips_small_groups_with_reason():
    x, labels = _grouped_data(n=60, seed=71)
    labels = list(labels)
    labels[:5] = ["C"] * 5          # 5명짜리 집단 → 표본 부족으로 건너뜀
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    rows = {r["level"]: r for r in res["group_replicability"]["groups"]}
    assert "표본 부족" in rows["C"]["skipped"]
    assert rows["C"]["congruence"] is None


def test_group_blank_labels_excluded_with_note():
    x, labels = _grouped_data(n=60, seed=73)
    labels = list(labels)
    labels[0] = ""
    labels[1] = "   "               # 공백만 있는 값도 결측 취급
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    gr = res["group_replicability"]
    assert gr["n_blank"] == 2
    assert any("비어 있는" in n for n in res["notes"])
    assert sum(r["n"] for r in gr["groups"]) == x.shape[0] - 2


def test_group_single_level_warns_and_skips():
    x, _ = _grouped_data(n=50, seed=77)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0,
                  group_labels=["only"] * x.shape[0])
    assert res["group_replicability"]["groups"] == []
    assert any("유효한 집단이" in w for w in res["warnings"])


def test_group_too_many_levels_warns_about_continuous_variable():
    x, _ = _grouped_data(n=60, seed=79)
    labels = [str(i) for i in range(x.shape[0])]      # 연속형을 잘못 넘긴 경우
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    assert any("너무 많습니다" in w for w in res["warnings"])
    assert res["group_replicability"]["groups"] == []


def test_group_label_count_mismatch_raises():
    x, _ = _grouped_data(n=40, seed=83)
    with pytest.raises(ValueError):
        analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=["A", "B"])


def test_group_constant_item_within_group_is_skipped_not_crashed():
    x, labels = _grouped_data(n=60, seed=89)
    x = x.copy()
    sel = np.array([lab == "B" for lab in labels])
    x[sel, 3] = 4.0                 # B집단에서만 상수가 된 문항
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    rows = {r["level"]: r for r in res["group_replicability"]["groups"]}
    assert "값이 모두 동일" in (rows["B"]["skipped"] or "")
    assert any("2개 미만" in w for w in res["warnings"])


def test_group_alpha_uses_overall_item_assignment():
    """집단별 α는 전체 해의 문항 배정으로 계산돼야 비교가 성립한다."""
    x, labels = _grouped_data(n=80, seed=97, broken=False)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    groups = np.argmax(np.abs(np.array(res["loadings"])), axis=1)
    lab = np.array(labels)
    for row in res["group_replicability"]["groups"]:
        expect = efa.alpha_by_group(x[lab == row["level"]], groups, 2)
        assert row["alpha"] == pytest.approx([expect[0], expect[1]])


def test_group_analysis_is_optional_and_absent_by_default():
    x, _ = _grouped_data(n=50, seed=101)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0)
    assert res["group_replicability"] is None


# --------------------------------------------------------------- CLI 통합
def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def test_cli_bootstrap_does_not_crash(tmp_path, capsys):
    """회귀: --bootstrap 이 존재하지 않는 함수를 불러 AttributeError로 죽던 버그."""
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"),
              "--bootstrap", "120", "--parallel-iter", "30"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "부트스트랩 안정성" in out
    assert "요인 수 안정성" in out


def test_cli_bootstrap_json_has_intervals(tmp_path, capsys):
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"),
              "--bootstrap", "60", "--parallel-iter", "20", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    bs = data["bootstrap"]
    assert bs["n_ok"] > 0
    assert len(bs["loading_lo"]) == data["n_items"]
    assert len(bs["loading_lo"][0]) == data["n_factors"]
    assert bs["conf"] == 0.95


def test_cli_alpha_ci_in_json_and_report(capsys):
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"),
              "--parallel-iter", "0", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for a, ci in zip(data["alpha"], data["alpha_ci"]):
        assert ci is not None and ci[0] < a < ci[1]


def test_cli_group_col_end_to_end(tmp_path, capsys):
    x, labels = _grouped_data(n=70, seed=103)
    header = ["ID", "사이트"] + [f"Q{i+1}" for i in range(8)]
    rows = [[f"S{i:03d}", labels[i]] + [int(v) for v in x[i]] for i in range(x.shape[0])]
    path = tmp_path / "g.csv"
    _write_csv(path, header, rows)
    rc = run([str(path), "--id-col", "ID", "--group-col", "사이트",
              "--n-factors", "2", "--parallel-iter", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "집단별 요인구조 재현성" in out
    assert "Tucker 일치계수" in out


def test_cli_group_col_missing_column_is_error(tmp_path, capsys):
    x, labels = _grouped_data(n=30, seed=107)
    header = ["ID"] + [f"Q{i+1}" for i in range(8)]
    rows = [[f"S{i}"] + [int(v) for v in x[i]] for i in range(x.shape[0])]
    path = tmp_path / "g.csv"
    _write_csv(path, header, rows)
    rc = run([str(path), "--id-col", "ID", "--group-col", "없는열"])
    assert rc == 2
    assert "없는열" in capsys.readouterr().err


def test_cli_group_col_excluded_from_auto_selected_items(tmp_path, capsys):
    """숫자 코드 집단 열(0/1)이 '문항'으로 자동선택되면 안 된다."""
    x, labels = _grouped_data(n=70, seed=109)
    header = ["ID", "성별"] + [f"Q{i+1}" for i in range(8)]
    rows = [[f"S{i:03d}", 1 if labels[i] == "A" else 2] + [int(v) for v in x[i]]
            for i in range(x.shape[0])]
    path = tmp_path / "g.csv"
    _write_csv(path, header, rows)
    rc = run([str(path), "--id-col", "ID", "--group-col", "성별",
              "--n-factors", "2", "--parallel-iter", "0", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "성별" not in data["items"]
    assert data["n_items"] == 8
    assert data["group_replicability"]["levels"] == ["1", "2"]


def test_cli_group_labels_stay_aligned_after_listwise_deletion(tmp_path, capsys):
    """결측 삭제로 행이 빠져도 집단 라벨이 어긋나면 안 된다."""
    x, labels = _grouped_data(n=70, seed=113)
    header = ["ID", "사이트"] + [f"Q{i+1}" for i in range(8)]
    rows = []
    dropped = 0
    for i in range(x.shape[0]):
        vals = [int(v) for v in x[i]]
        if i % 9 == 0:              # 일부 행에 결측을 심는다
            vals[2] = ""
            dropped += 1
        rows.append([f"S{i:03d}", labels[i]] + vals)
    path = tmp_path / "g.csv"
    _write_csv(path, header, rows)
    rc = run([str(path), "--id-col", "ID", "--group-col", "사이트",
              "--n-factors", "2", "--parallel-iter", "0", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    gr = data["group_replicability"]
    assert sum(r["n"] for r in gr["groups"]) == data["n_used"]
    # 남은 A/B 인원을 직접 세어 대조
    kept = [labels[i] for i in range(x.shape[0]) if i % 9 != 0]
    counts = {r["level"]: r["n"] for r in gr["groups"]}
    assert counts["A"] == kept.count("A") and counts["B"] == kept.count("B")


def test_cli_negative_bootstrap_rejected(capsys):
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"), "--bootstrap", "-5"])
    assert rc == 2
    assert "0 이상" in capsys.readouterr().err


def test_help_lists_new_options():
    out = subprocess.run([sys.executable, "-m", "factorscan.cli", "--help"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    assert "--group-col" in out
    assert "--bootstrap" in out


def test_group_case_variant_levels_are_warned():
    """'F'/'f' 처럼 대소문자만 다른 집단값은 입력 오류일 가능성이 높아 경고해야 한다."""
    x, labels = _grouped_data(n=60, seed=127)
    labels = ["a" if (i % 3 == 0 and lab == "A") else lab
              for i, lab in enumerate(labels)]
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    assert any("대소문자만 다른" in w for w in res["warnings"])


def test_group_label_whitespace_is_stripped():
    """' A ' 와 'A' 는 같은 집단이어야 한다(엑셀 붙여넣기의 흔한 공백)."""
    x, labels = _grouped_data(n=40, seed=131)
    labels = [f" {lab} " if i % 2 else lab for i, lab in enumerate(labels)]
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    assert res["group_replicability"]["levels"] == ["A", "B"]


def test_csv_out_carries_alpha_ci_rows(tmp_path, capsys):
    out = tmp_path / "load.csv"
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"),
              "--parallel-iter", "0", "--csv-out", str(out)])
    capsys.readouterr()
    assert rc == 0
    text = out.read_text(encoding="utf-8-sig")
    assert "_alpha_ci95_lo(Feldt)" in text and "_alpha_ci95_hi(Feldt)" in text
    # 부트스트랩을 안 돌렸으면 부트스트랩 행·열은 없어야 한다.
    assert "bootstrap" not in text
    assert "primary_loading_ci_lo" not in text


def test_csv_out_carries_bootstrap_columns_and_rows(tmp_path, capsys):
    out = tmp_path / "load.csv"
    rc = run([str(ROOT / "examples/sleep_scale.csv"),
              "--config", str(ROOT / "examples/sleep_config.json"),
              "--parallel-iter", "0", "--bootstrap", "120", "--csv-out", str(out)])
    capsys.readouterr()
    assert rc == 0
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    header = rows[0]
    assert header[-2:] == ["primary_loading_ci_lo", "primary_loading_ci_hi"]
    # 각 문항 행의 주적재가 자기 구간 안에 있어야 한다(열 정렬이 어긋나지 않았는지 확인).
    body = [r for r in rows[1:9]]
    assert len(body) == 8
    for r in body:
        primary = int(r[header.index("primary_factor")])
        lam = float(r[primary])          # F1은 인덱스 1, F2는 2 …
        assert float(r[-2]) <= lam <= float(r[-1])
    text = "\n".join(",".join(r) for r in rows)
    assert "_omega_ci95_lo(bootstrap)" in text
    assert "_alpha_ci95_lo(bootstrap)" in text


# ------------------------------------------------- 가설(a priori) 요인구조 대조
def _hypo_prep(seed=201, n=200):
    x = _likert(_two_factor(n=n, p=6, seed=seed))
    return _prep(x)


def test_match_factors_finds_optimal_permutation():
    """요인 번호가 뒤바뀌어도 가설 요인과 올바르게 짝지어야 한다."""
    load = np.array([[0.1, 0.9], [0.0, 0.85], [0.88, 0.1], [0.9, 0.0]])
    target = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    perm, phi = efa.match_factors(load, target)
    assert perm == [1, 0]                    # 가설 F1 ↔ 경험 F2
    assert all(v > 0.9 for v in phi)


def test_match_factors_beats_identity_when_identity_is_wrong():
    """대응을 하지 않으면(항등) 훨씬 낮은 φ가 나온다는 것을 직접 확인."""
    load = np.array([[0.1, 0.9], [0.0, 0.85], [0.88, 0.1], [0.9, 0.0]])
    target = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    _, phi = efa.match_factors(load, target)
    identity_phi = [efa.tucker_congruence(load[:, [j]], target[:, [j]])[0] for j in range(2)]
    assert float(np.sum(np.abs(phi))) > float(np.sum(np.abs(identity_phi)))


def test_match_factors_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        efa.match_factors(np.zeros((4, 2)), np.zeros((4, 3)))


def test_match_factors_greedy_path_for_many_factors():
    """k>7 이면 탐욕 경로를 타되 여전히 1:1 대응이어야 한다."""
    k = 8
    load = np.eye(k) * 0.9 + 0.05
    target = np.eye(k)[:, ::-1]              # 완전히 뒤집힌 순서
    perm, phi = efa.match_factors(load, target)
    assert sorted(perm) == list(range(k))    # 순열(중복 없음)
    assert len(phi) == k


def test_hypothesis_perfect_match_reports_100_percent():
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2", "Q3"], "B": ["Q4", "Q5", "Q6"]}
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    h = res["hypothesis"]
    assert h["agreement"] == 1.0
    assert h["agreement_strict"] == 1.0
    assert h["mismatches"] == [] and h["weak"] == []
    assert h["labels"] == ["A", "B"]
    assert sorted(h["matched_factor"]) == [1, 2]
    assert len(h["items"]) == 6
    assert all(r["status"] == "ok" for r in h["items"])
    assert any("재현되었습니다" in n for n in res["notes"])


def test_hypothesis_detects_swapped_items():
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2", "Q4"], "B": ["Q3", "Q5", "Q6"]}   # Q3/Q4 뒤바꿈
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    h = res["hypothesis"]
    assert h["agreement"] == pytest.approx(4 / 6)
    bad = {d["item"] for d in h["mismatches"]}
    assert bad == {"Q3", "Q4"}
    for d in h["mismatches"]:
        # 실제 실린 요인의 적재가 가설 요인의 적재보다 커야 대조가 의미 있다.
        assert abs(d["loading"]) > abs(d["loading_on_hypothesized"])
    assert any("가설과 다른 하위척도" in w for w in res["warnings"])


def test_hypothesis_alpha_uses_hypothesized_grouping_not_argmax():
    """배정이 어긋나도 α는 '연구자가 정한 문항 묶음'으로 계산돼야 한다."""
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2", "Q4"], "B": ["Q3", "Q5", "Q6"]}
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    x = prep.matrix
    names = prep.names
    idx = {nm: i for i, nm in enumerate(names)}
    for j, lab in enumerate(["A", "B"]):
        cols = [idx[nm] for nm in st[lab]]
        assert res["hypothesis"]["alpha"][j] == pytest.approx(efa.cronbach_alpha(x[:, cols]))


def test_hypothesis_factor_count_mismatch_warns_and_skips_assignment():
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2"], "B": ["Q3", "Q4"], "C": ["Q5", "Q6"]}
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    h = res["hypothesis"]
    assert h["agreement"] is None and h["matched_factor"] is None
    assert h["n_hypothesized"] == 3 and h["n_applied"] == 2
    assert len(h["alpha"]) == 3        # α는 그래도 제공
    assert any("요인 수" in w and "다릅니다" in w for w in res["warnings"])


def test_hypothesis_unknown_item_raises_with_actionable_message():
    prep = _hypo_prep()
    with pytest.raises(ValueError) as exc:
        analyze(prep, n_factors=2, parallel_iter=0,
                structure={"A": ["Q1", "없는문항"], "B": ["Q4", "Q5"]})
    assert "없는문항" in str(exc.value)


def test_hypothesis_duplicate_item_across_subscales_raises():
    prep = _hypo_prep()
    with pytest.raises(ValueError) as exc:
        analyze(prep, n_factors=2, parallel_iter=0,
                structure={"A": ["Q1", "Q2"], "B": ["Q2", "Q4"]})
    assert "둘 이상" in str(exc.value)


def test_hypothesis_partial_coverage_is_noted_and_excluded():
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2"], "B": ["Q4", "Q5"]}     # Q3, Q6 미포함
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    h = res["hypothesis"]
    assert set(h["uncovered_items"]) == {"Q3", "Q6"}
    assert h["n_items_checked"] == 4
    assert any("가설 구조에 포함되지 않은" in n for n in res["notes"])


def test_hypothesis_absent_by_default():
    assert analyze(_hypo_prep(), n_factors=2, parallel_iter=0)["hypothesis"] is None


def test_hypothesis_target_congruence_recomputable():
    prep = _hypo_prep()
    st = {"A": ["Q1", "Q2", "Q3"], "B": ["Q4", "Q5", "Q6"]}
    res = analyze(prep, n_factors=2, parallel_iter=0, structure=st)
    h = res["hypothesis"]
    load = np.array(res["loadings"])
    names = prep.names
    target = np.zeros((len(names), 2))
    for j, lab in enumerate(["A", "B"]):
        for nm in st[lab]:
            target[names.index(nm), j] = 1.0
    for j in range(2):
        c = int(h["matched_factor"][j]) - 1
        expect = efa.tucker_congruence(load[:, [c]], target[:, [j]])[0]
        assert h["target_congruence"][j] == pytest.approx(expect, abs=1e-12)


# ------------------------------------------------------- structure 설정 파싱
def _cfg_run(tmp_path, capsys, structure, extra=()):
    cfg = {
        "id_cols": ["ID"],
        "items": ["Q1_잠들기어려움", "Q2_자주깸", "Q3_아침개운함", "Q4_수면만족",
                  "Q5_주간졸림", "Q6_집중력", "Q7_주간피로", "Q8_활력"],
        "reverse": ["Q1_잠들기어려움", "Q2_자주깸", "Q5_주간졸림", "Q7_주간피로"],
        "scale_range": [1, 5],
        "structure": structure,
    }
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    rc = run([str(ROOT / "examples/sleep_scale.csv"), "--config", str(path),
              "--parallel-iter", "0", *extra])
    return rc, capsys.readouterr()


def test_cli_structure_end_to_end(tmp_path, capsys):
    rc, cap = _cfg_run(tmp_path, capsys, {
        "수면의질": ["Q1_잠들기어려움", "Q2_자주깸", "Q3_아침개운함", "Q4_수면만족"],
        "주간기능": ["Q5_주간졸림", "Q6_집중력", "Q7_주간피로", "Q8_활력"]})
    assert rc == 0
    assert "가설 요인구조 대조" in cap.out
    assert "8/8 (100%)" in cap.out


def test_cli_structure_json_output(tmp_path, capsys):
    rc, cap = _cfg_run(tmp_path, capsys, {
        "수면의질": ["Q1_잠들기어려움", "Q2_자주깸", "Q3_아침개운함", "Q4_수면만족"],
        "주간기능": ["Q5_주간졸림", "Q6_집중력", "Q7_주간피로", "Q8_활력"]},
        extra=("--json",))
    assert rc == 0
    h = json.loads(cap.out)["hypothesis"]
    assert h["agreement"] == 1.0 and h["counts"] == [4, 4]


@pytest.mark.parametrize("bad", [
    [],                                          # 객체가 아님
    {},                                          # 비어 있음
    {"": ["Q1_잠들기어려움"]},                     # 이름 없음
    {"A": []},                                   # 문항 없음
    {"A": 3},                                    # 목록이 아님
    {"A": ["Q1_잠들기어려움", "Q1_잠들기어려움"]},   # 하위척도 내 중복
])
def test_cli_structure_bad_config_is_rejected(tmp_path, capsys, bad):
    rc, cap = _cfg_run(tmp_path, capsys, bad)
    assert rc == 2
    assert "설정 파일 오류" in cap.err


def test_cli_structure_accepts_comma_string(tmp_path, capsys):
    """문항 목록을 쉼표 문자열로 줘도 받아 준다(사람이 손으로 쓰는 형식)."""
    rc, cap = _cfg_run(tmp_path, capsys, {
        "수면의질": "Q1_잠들기어려움,Q2_자주깸,Q3_아침개운함,Q4_수면만족",
        "주간기능": "Q5_주간졸림,Q6_집중력,Q7_주간피로,Q8_활력"})
    assert rc == 0
    assert "8/8 (100%)" in cap.out


def test_cli_structure_unknown_item_exits_cleanly(tmp_path, capsys):
    rc, cap = _cfg_run(tmp_path, capsys, {
        "A": ["Q1_잠들기어려움", "오타문항"], "B": ["Q5_주간졸림", "Q6_집중력"]})
    assert rc == 1
    assert "오타문항" in cap.err
    assert "Traceback" not in cap.err
