"""Round 6: ML 추출·적합도지수 노출, 결측 진단, 회귀 요인점수, 엑셀/TSV 입력.

새 기능은 모두 '독립적으로 재계산한 오라클'과 대조한다(공식을 코드에서 베끼지 않고
정의대로 다시 유도해 비교). 엑셀 픽스처는 서드파티 없이 원시 XML로 직접 만들어,
진짜 엑셀 방언(sharedStrings)과 openpyxl 방언(inlineStr)을 모두 시험한다.
"""
import csv
import io
import json
import math

import numpy as np
import pytest

from factorscan import efa
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import (DataError, Dataset, listwise, listwise_bias_check,
                               load_table, load_xlsx, missing_report, select_items)
from factorscan.report import loadings_table_csv, render, scores_table_csv


# ------------------------------------------------------------------ 공용 픽스처
def _two_factor_raw(n=200, seed=7):
    """2요인 구조의 (n, 6) 응답 행렬(연속형)."""
    rng = np.random.default_rng(seed)
    f1 = rng.standard_normal(n)
    f2 = rng.standard_normal(n)
    cols = [f1 + 0.5 * rng.standard_normal(n) for _ in range(3)]
    cols += [f2 + 0.5 * rng.standard_normal(n) for _ in range(3)]
    return np.column_stack(cols)


def _prep(n=200, seed=7):
    x = _two_factor_raw(n, seed)
    names = [f"Q{i+1}" for i in range(x.shape[1])]
    return listwise(Dataset(names=names, data=x))


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


# ============================ ML 추출 · 적합도지수 ============================
def test_analyze_ml_runs_and_reports_fit():
    res = analyze(_prep(), parallel_iter=0, extraction="ml", n_factors=2)
    assert res["extraction"] == "maximum_likelihood"
    fit = res["fit"]
    assert fit is not None and fit["identified"]
    for key in ("chi_square", "p_value", "rmsea", "cfi", "tli", "aic", "bic", "df"):
        assert fit[key] is not None, key


def test_ml_recovers_two_factor_structure():
    res = analyze(_prep(n=300), parallel_iter=0, extraction="ml", n_factors=2)
    L = np.array(res["loadings"])
    g1 = {int(np.argmax(np.abs(L[i]))) for i in range(3)}
    g2 = {int(np.argmax(np.abs(L[i]))) for i in range(3, 6)}
    assert len(g1) == 1 and len(g2) == 1 and g1 != g2


def test_ml_df_formula_matches_definition():
    # df = [(p-k)^2 - (p+k)] / 2 를 정의 그대로 재계산.
    for p in range(3, 12):
        for k in range(1, p):
            assert efa.ml_df(p, k) == ((p - k) ** 2 - (p + k)) // 2


def test_ml_max_factors_is_largest_k_with_positive_df():
    for p in range(3, 15):
        kmax = efa.ml_max_factors(p)
        assert efa.ml_df(p, kmax) > 0
        assert efa.ml_df(p, kmax + 1) <= 0


def test_fit_indices_recomputed_from_first_principles():
    """χ²·RMSEA·CFI·TLI·AIC/BIC를 정의식으로 독립 재계산해 대조."""
    prep = _prep(n=250)
    n, p, k = prep.matrix.shape[0], prep.matrix.shape[1], 2
    r = efa.correlation_matrix(prep.matrix)
    ml = efa.ml_factor_analysis(r, k)
    null_chi = efa.bartlett_sphericity(r, n).chi_square
    fit = efa.fit_indices(ml.criterion, n, p, k, null_chi_square=null_chi)

    df = ((p - k) ** 2 - (p + k)) // 2
    mult = (n - 1) - (2 * p + 5) / 6.0 - (2 * k) / 3.0
    chi = mult * ml.criterion
    assert fit["df"] == df
    assert fit["chi_square"] == pytest.approx(chi, rel=1e-10)
    assert fit["rmsea"] == pytest.approx(math.sqrt(max(chi - df, 0) / (df * (n - 1))), rel=1e-10)
    assert fit["aic"] == pytest.approx(chi - 2 * df, rel=1e-10)
    assert fit["bic"] == pytest.approx(chi - df * math.log(n), rel=1e-10)

    df0 = p * (p - 1) / 2.0
    d_m, d_0 = max(chi - df, 0.0), max(null_chi - df0, 0.0)
    assert fit["cfi"] == pytest.approx(1 - d_m / max(d_0, d_m), rel=1e-10)
    ratio0 = null_chi / df0
    assert fit["tli"] == pytest.approx((ratio0 - chi / df) / (ratio0 - 1.0), rel=1e-10)


def test_rmsea_ci_brackets_point_estimate_and_pclose_consistent():
    fit = efa.fit_indices(0.08, n=200, p=8, k=2, null_chi_square=400.0)
    assert fit["rmsea_lo"] <= fit["rmsea"] + 1e-9 <= fit["rmsea_hi"] + 1e-9
    assert 0.0 <= fit["p_close"] <= 1.0
    # 적합이 아주 좋으면(RMSEA≈0) PCLOSE는 커야 한다.
    good = efa.fit_indices(1e-9, n=200, p=8, k=2, null_chi_square=400.0)
    assert good["p_close"] > 0.9


def test_fit_indices_unidentified_returns_none_fields():
    fit = efa.fit_indices(0.1, n=100, p=4, k=3)   # df<=0
    assert fit["identified"] is False
    assert fit["df"] <= 0
    for key in ("chi_square", "rmsea", "cfi", "tli", "aic", "bic"):
        assert fit[key] is None


def test_cfi_bounded_even_when_model_worse_than_null():
    fit = efa.fit_indices(5.0, n=100, p=8, k=1, null_chi_square=1.0)
    assert 0.0 <= fit["cfi"] <= 1.0


def test_fit_scan_covers_all_identified_k_and_picks_true_k():
    res = analyze(_prep(n=300), parallel_iter=0, extraction="ml", n_factors=2, fit_scan=True)
    scan = res["fit_scan"]
    assert scan and [r["k"] for r in scan] == list(range(1, efa.ml_max_factors(6) + 1))
    ok = [r for r in scan if r.get("bic") is not None]
    # 진짜 구조가 2요인이므로 BIC 최소 k는 2여야 한다.
    assert min(ok, key=lambda r: r["bic"])["k"] == 2
    # 1요인 모형은 기각되어야 한다(2요인 자료이므로).
    k1 = next(r for r in scan if r["k"] == 1)
    assert k1["p_value"] < 0.05


def test_fit_scan_only_for_ml():
    res = analyze(_prep(), parallel_iter=0, extraction="pca", fit_scan=True)
    assert res["fit_scan"] is None and res["fit"] is None


def test_ml_criterion_matches_lawley_maxwell_definition():
    """F_min을 '정의식'으로 독립 재계산해 대조 — 구현의 고유값 형태를 베끼지 않는다.

    구현은 프로파일(고유값) 형태 F = Σ_{j>k}(λ_j − ln λ_j − 1) 을 쓴다. 여기서는 원래
    정의인 F = tr(RΣ⁻¹) − ln|RΣ⁻¹| − p 로(완전히 다른 식) 계산해 비교한다. 두 식은
    최적점에서 수학적으로 같아야 하므로, 목적함수에 상수배 같은 오류가 들어가면 잡힌다.
    χ²·RMSEA·CFI·TLI·AIC/BIC가 전부 F_min에 비례하므로 이 가드가 없으면 논문에 실리는
    수치 전체가 무방비다.
    """
    for seed, n, k in [(300, 300, 2), (7, 250, 1), (11, 400, 3)]:
        r = efa.correlation_matrix(_two_factor_raw(n, seed))
        ml = efa.ml_factor_analysis(r, k)
        sigma = ml.loadings @ ml.loadings.T + np.diag(ml.uniquenesses)
        m = r @ np.linalg.inv(sigma)
        f_def = np.trace(m) - np.log(np.linalg.det(m)) - r.shape[0]
        assert ml.criterion == pytest.approx(f_def, abs=1e-6), (seed, k)


def test_ml_criterion_zero_for_exact_factor_model():
    """정확히 k요인 구조인 상관행렬이면 F_min ≈ 0 (스케일 오류는 이것만으로 안 잡히므로 위 테스트와 짝)."""
    rng = np.random.default_rng(2)
    p, k = 8, 2
    lam = rng.uniform(0.5, 0.8, (p, k))
    psi = 1.0 - (lam ** 2).sum(axis=1)
    # 고유분산이 ML 하한(0.005)에 붙으면 Heywood라 F_min이 0으로 안 떨어진다 — 그 경우만 배제.
    assert np.all(psi > 0.015)
    sigma = lam @ lam.T + np.diag(psi)
    d = np.sqrt(np.diag(sigma))
    r = sigma / np.outer(d, d)              # 모집단 상관행렬(표본오차 없음)
    ml = efa.ml_factor_analysis(r, k)
    assert ml.criterion < 1e-8


def test_ml_objective_minimized_at_solution():
    """반환된 Ψ가 국소 최소인지 — 좌표별 섭동이 목적함수를 낮추지 못해야 한다."""
    r = efa.correlation_matrix(_two_factor_raw(300))
    ml = efa.ml_factor_analysis(r, 2)
    f0 = efa._ml_objective(ml.uniquenesses, r, 2)
    assert f0 == pytest.approx(ml.criterion, rel=1e-8)
    for i in range(r.shape[0]):
        for step in (1e-4, -1e-4):
            psi = ml.uniquenesses.copy()
            psi[i] = float(np.clip(psi[i] + step, 0.005, 1.0))
            assert efa._ml_objective(psi, r, 2) >= f0 - 1e-9


def test_ml_reproduces_correlation_matrix_well():
    # 2요인 자료에 2요인 ML을 적합하면 ΛΛᵀ+Ψ ≈ R 이어야 한다.
    r = efa.correlation_matrix(_two_factor_raw(400))
    ml = efa.ml_factor_analysis(r, 2)
    approx = ml.loadings @ ml.loadings.T + np.diag(ml.uniquenesses)
    assert np.abs(approx - r).max() < 0.05


def test_ml_communalities_complement_uniquenesses():
    r = efa.correlation_matrix(_two_factor_raw(200))
    ml = efa.ml_factor_analysis(r, 2)
    assert ml.communalities == pytest.approx(1.0 - ml.uniquenesses)


def test_ml_invalid_k_raises():
    r = efa.correlation_matrix(_two_factor_raw(100))
    for k in (0, -1, 6, 99):
        with pytest.raises(ValueError):
            efa.ml_factor_analysis(r, k)


def test_cli_extraction_ml_end_to_end(tmp_path, capsys):
    x = _two_factor_raw(150)
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[f"{v:.4f}" for v in row] for row in x])
    assert run([path, "--extraction", "ml", "--fit-scan", "--parallel-iter", "0"]) == 0
    out = capsys.readouterr().out
    assert "모형 적합도 (최대우도 ML)" in out
    assert "RMSEA" in out and "CFI" in out and "χ²/df" in out
    assert "요인 수별 적합도 스캔" in out
    assert "최대우도(ML)" in out


def test_cli_fit_scan_requires_ml(tmp_path, capsys):
    x = _two_factor_raw(80)
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[f"{v:.4f}" for v in row] for row in x])
    assert run([path, "--fit-scan", "--parallel-iter", "0"]) == 2
    assert "--extraction ml" in capsys.readouterr().err


def test_cli_ml_json_has_fit(tmp_path, capsys):
    x = _two_factor_raw(150)
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[f"{v:.4f}" for v in row] for row in x])
    assert run([path, "--extraction", "ml", "--fit-scan", "--parallel-iter", "0", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["fit"]["rmsea"] is not None
    assert len(data["fit_scan"]) >= 2


def test_report_unidentified_fit_is_explicit():
    # p=4, k=3 → df<=0. 조용히 비우지 않고 이유를 밝혀야 한다.
    rng = np.random.default_rng(3)
    x = rng.standard_normal((60, 4))
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(4)], data=x))
    res = analyze(prep, parallel_iter=0, extraction="ml", n_factors=3)
    text = render(res)
    assert "적합도 검정이 불가" in text
    assert any("자유도" in w for w in res["warnings"])


def test_ncx2_sf_accurate_in_deep_tail():
    """PCLOSE의 아주 작은 p값이 1−CDF의 파국적 상쇄로 뭉개지지 않아야 한다(회귀 가드).

    1−ncx2_cdf 로 구하던 시절 p_close는 참값 7e-120 자리에서 3.4e-15(부동소수 잡음)를
    냈다. ncx2_sf는 상측꼬리를 직접 합산하므로 이 영역에서도 유효숫자가 남는다.
    """
    from factorscan.stats import ncx2_cdf, ncx2_sf
    deep = ncx2_sf(700.0, 13.0, 1.5)
    assert 0.0 < deep < 1e-100          # 잡음(≈1e-15)이 아니라 진짜 작은 값
    # 상쇄 방식은 이 영역에서 0 또는 1e-15 잡음을 낸다 — 직접합산이 확실히 다르다.
    assert deep < 1.0 - ncx2_cdf(700.0, 13.0, 1.5) or (1.0 - ncx2_cdf(700.0, 13.0, 1.5)) == 0.0
    # nc=0 이면 중심 카이제곱 상측꼬리와 정확히 같아야 한다.
    from factorscan.stats import chi2_sf
    assert ncx2_sf(20.0, 5.0, 0.0) == pytest.approx(chi2_sf(20.0, 5.0), rel=1e-12)
    # 꼬리가 아닌 영역에서는 sf + cdf = 1.
    assert ncx2_sf(20.0, 5.0, 3.0) + ncx2_cdf(20.0, 5.0, 3.0) == pytest.approx(1.0, abs=1e-12)


def test_ncx2_sf_monotone_and_bounded():
    from factorscan.stats import ncx2_sf
    prev = 1.1
    for x in [0.5, 1, 2, 5, 10, 20, 50, 100, 200]:
        v = ncx2_sf(float(x), 8.0, 4.0)
        assert 0.0 <= v <= 1.0
        assert v <= prev + 1e-15        # x가 커지면 상측꼬리는 감소
        prev = v
    assert ncx2_sf(0.0, 5.0, 2.0) == 1.0
    for bad in [(5.0, 0.0, 1.0), (5.0, -1.0, 1.0)]:
        with pytest.raises(ValueError):
            ncx2_sf(*bad)
    with pytest.raises(ValueError):
        ncx2_sf(5.0, 3.0, -1.0)


def test_pclose_uses_direct_tail_for_bad_fit():
    # 심하게 부적합한 모형의 PCLOSE는 0에 가깝되, 잡음이 아니라 계산된 값이어야 한다.
    bad = efa.fit_indices(1.2, n=600, p=12, k=2, null_chi_square=5000.0)
    assert bad["rmsea"] > 0.15
    assert 0.0 <= bad["p_close"] < 1e-30
    good = efa.fit_indices(1e-9, n=600, p=12, k=2, null_chi_square=5000.0)
    assert good["p_close"] > 0.9


# ================================ 결측 진단 ================================
def _raw_with_missing():
    rng = np.random.default_rng(11)
    x = rng.standard_normal((100, 4))
    x[0:10, 0] = np.nan     # Q1에 결측 10개
    x[50:53, 2] = np.nan    # Q3에 결측 3개
    return x


def test_missing_report_counts_match_manual():
    x = _raw_with_missing()
    names = [f"Q{i+1}" for i in range(4)]
    rep = missing_report(x, names)
    assert rep["per_item"] == [10, 0, 3, 0]
    assert rep["per_item_prop"] == pytest.approx([0.10, 0.0, 0.03, 0.0])
    assert rep["n_incomplete"] == 13      # 겹치지 않는 행들
    assert rep["n_complete"] == 87
    assert rep["worst_item"] == "Q1"


def test_missing_report_no_missing():
    x = np.random.default_rng(1).standard_normal((30, 3))
    rep = missing_report(x, ["a", "b", "c"])
    assert rep["per_item"] == [0, 0, 0]
    assert rep["n_incomplete"] == 0 and rep["worst_item"] is None


def test_missing_report_all_missing_item():
    x = np.ones((10, 2))
    x[:, 1] = np.nan
    rep = missing_report(x, ["a", "b"])
    assert rep["per_item"] == [0, 10]
    assert rep["n_complete"] == 0 and rep["worst_item"] == "b"


def test_bias_check_recomputes_cohens_d():
    """listwise_bias_check의 d를 pooled SD 정의로 독립 재계산해 대조."""
    rng = np.random.default_rng(5)
    x = rng.standard_normal((200, 3))
    x[:60, 2] = np.nan            # 60명이 Q3 결측 → 삭제 대상
    x[:60, 0] += 1.5              # 그 60명은 Q1 응답이 체계적으로 높음(MCAR 위배)
    names = ["Q1", "Q2", "Q3"]
    out = {b["item"]: b for b in listwise_bias_check(x, names)}

    miss = ~np.isfinite(x)
    complete = ~miss.any(axis=1)
    dropped = ~complete
    for i, nm in enumerate(names):
        obs = np.isfinite(x[:, i])
        a, b = x[complete & obs, i], x[dropped & obs, i]
        if a.size < 5 or b.size < 5:
            assert nm not in out
            continue
        pooled = math.sqrt(((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1))
                           / (a.size + b.size - 2))
        assert out[nm]["d"] == pytest.approx((a.mean() - b.mean()) / pooled, rel=1e-10)
    # Q1은 의도적으로 1.5 SD 차이를 넣었으므로 크게 음수(완전 < 삭제)로 잡혀야 한다.
    assert out["Q1"]["d"] < -1.0
    assert abs(out["Q2"]["d"]) < 0.5
    # Q3은 삭제군이 전원 결측이라 비교 불가 → 결과에 없어야 한다.
    assert "Q3" not in out


def test_bias_check_empty_when_nothing_dropped():
    x = np.random.default_rng(2).standard_normal((40, 3))
    assert listwise_bias_check(x, ["a", "b", "c"]) == []


def test_bias_check_skips_small_and_constant_groups():
    x = np.random.default_rng(2).standard_normal((40, 2))
    x[:2, 1] = np.nan          # 삭제군 2명뿐(min_group=5 미만) → 건너뜀
    assert listwise_bias_check(x, ["a", "b"]) == []
    y = np.ones((40, 2))       # 상수 → pooled SD 0
    y[:10, 1] = np.nan
    assert listwise_bias_check(y, ["a", "b"]) == []


def test_analyze_reports_missing_and_warns_on_big_loss():
    x = _two_factor_raw(120)
    x[:30, 0] = np.nan          # 25% 손실
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(6)], data=x))
    res = analyze(prep, parallel_iter=0)
    assert res["missing"]["per_item"][0] == 30
    assert any("결측 제거로 응답자의" in w for w in res["warnings"])
    assert any("Q1" in w for w in res["warnings"])          # 유발 문항을 짚어줘야 함
    assert any("결측률이 높은 문항" in nt for nt in res["notes"])


def test_analyze_flags_mcar_violation():
    x = _two_factor_raw(200)
    x[:50, 5] = np.nan
    x[:50, 0] += 2.0            # 삭제될 응답자가 Q1에서 체계적으로 높음
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(6)], data=x))
    res = analyze(prep, parallel_iter=0)
    assert any("결측 제거 편향 신호" in nt for nt in res["notes"])


def test_analyze_clean_data_no_missing_noise():
    res = analyze(_prep(), parallel_iter=0)
    assert res["missing"]["n_incomplete"] == 0
    assert "[ 0. 결측 구조 ]" not in render(res)      # 깨끗하면 표를 띄우지 않는다
    assert not any("결측 제거로" in w for w in res["warnings"])


def test_report_shows_missing_section_and_bias():
    x = _two_factor_raw(200)
    x[:50, 5] = np.nan
    x[:50, 0] += 2.0
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(6)], data=x))
    text = render(analyze(prep, parallel_iter=0))
    assert "[ 0. 결측 구조 ]" in text
    assert "삭제 편향 점검" in text and "Cohen's d" in text


def test_missing_survives_json_roundtrip(tmp_path, capsys):
    x = _two_factor_raw(100)
    x[:12, 0] = np.nan
    rows = [["" if not np.isfinite(v) else f"{v:.4f}" for v in row] for row in x]
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)], rows)
    assert run([path, "--parallel-iter", "0", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["missing"]["per_item"][0] == 12
    assert data["missing"]["n_complete"] == 88


def test_prepared_raw_is_snapshot_not_alias():
    # raw가 원본 배열을 그대로 참조하면 이후 변형이 진단을 오염시킨다.
    x = _two_factor_raw(50)
    ds = Dataset(names=[f"Q{i+1}" for i in range(6)], data=x)
    prep = listwise(ds)
    x[0, 0] = 999.0
    assert prep.raw[0, 0] != 999.0


# ============================== 회귀 요인점수 ==============================
def test_regression_scores_match_thurstone_formula():
    prep = _prep(n=200)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    L = np.array(res["loadings"])
    r = res["correlation_matrix"]
    got = efa.regression_factor_scores(prep.matrix, L, r)

    z = (prep.matrix - prep.matrix.mean(axis=0)) / prep.matrix.std(axis=0, ddof=1)
    expect = z @ np.linalg.inv(r) @ L
    assert np.allclose(got, expect)


def test_regression_scores_are_standardized_and_track_sum_scores():
    prep = _prep(n=300)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    L = np.array(res["loadings"])
    reg = efa.regression_factor_scores(prep.matrix, L, res["correlation_matrix"])
    groups = np.argmax(np.abs(L), axis=1)
    total = efa.subscale_scores(prep.matrix, groups, 2, method="sum")
    assert np.allclose(reg.mean(axis=0), 0, atol=1e-8)
    for j in range(2):
        # 회귀점수와 합산점수는 같은 구성을 재므로 강하게 상관해야 한다.
        assert abs(np.corrcoef(reg[:, j], total[:, j])[0, 1]) > 0.9


def test_regression_scores_use_phi_only_for_promax():
    prep = _prep(n=200)
    obl = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    L = np.array(obl["loadings"])
    phi = np.array(obl["factor_correlation"])
    r = obl["correlation_matrix"]
    # 사교회전은 구조행렬 S=ΛΦ 를 써야 한다.
    expect = ((prep.matrix - prep.matrix.mean(axis=0)) / prep.matrix.std(axis=0, ddof=1)) \
        @ np.linalg.inv(r) @ (L @ phi)
    assert np.allclose(efa.regression_factor_scores(prep.matrix, L, r, phi=phi), expect)

    text = scores_table_csv(obl, prep.matrix, [], method="regression")
    rows = list(csv.reader(text.splitlines()))
    got = np.array([[float(c) for c in row[1:]] for row in rows[1:]])
    assert np.allclose(got, np.round(expect, 4), atol=1e-4)


def test_regression_scores_singular_matrix_falls_back_to_pinv():
    # 완전 중복 문항이 있으면 R이 특이 → pinv로 유한값을 내야 한다(NaN 폭발 금지).
    rng = np.random.default_rng(4)
    f = rng.standard_normal(80)
    x = np.column_stack([f + 0.3 * rng.standard_normal(80) for _ in range(3)])
    x = np.column_stack([x, x[:, 0]])          # 4번째 = 1번째 복제
    r = efa.correlation_matrix(x)
    L = efa.component_loadings(r, 2)
    out = efa.regression_factor_scores(x, L, r)
    assert np.all(np.isfinite(out))


def test_scores_csv_regression_header_and_shape(tmp_path):
    prep = _prep(n=100)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    text = scores_table_csv(res, prep.matrix, [], method="regression", row_numbers=None)
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == ["row", "F1_reg(표준화)", "F2_reg(표준화)"]
    assert len(rows) == prep.matrix.shape[0] + 1


def test_cli_score_method_regression(tmp_path, capsys):
    x = _two_factor_raw(120)
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[f"{v:.4f}" for v in row] for row in x])
    out = tmp_path / "scores.csv"
    assert run([path, "--parallel-iter", "0", "--scores-out", str(out),
                "--score-method", "regression"]) == 0
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert "_reg" in rows[0][1]
    vals = np.array([[float(c) for c in r[1:]] for r in rows[1:]])
    assert vals.shape[0] == 120
    assert abs(vals.mean()) < 0.01          # 표준화 스케일


# ============================== CSV 수식 인젝션 ==============================
# 기존 테스트는 '=' 페이로드만 써서, 방어를 '='만 막도록 좁혀도 초록으로 통과했다.
# OWASP가 지목하는 접두문자 전체를 문항명·ID열이름·ID값 세 경로 모두에서 확인한다.
_INJECTION = ["=cmd|' /C calc'!A1", "+1+1", "-1+1", "@SUM(1)", "\t=x", "\r=x", "\n=x"]


@pytest.mark.parametrize("payload", _INJECTION)
def test_safe_text_neutralizes_every_dangerous_prefix(payload):
    from factorscan.report import _safe_text
    out = _safe_text(payload)
    assert out.startswith("'"), payload
    assert out[1:] == payload          # 값 자체는 보존


@pytest.mark.parametrize("payload", _INJECTION)
def test_loadings_csv_item_name_injection_guarded(tmp_path, payload):
    prep = _prep(n=60)
    prep.names[0] = payload
    res = analyze(prep, parallel_iter=0, n_factors=2)
    rows = list(csv.reader(io.StringIO(loadings_table_csv(res), newline="")))
    # 셀이 위험 접두문자로 시작하지 않아야 한다(엑셀이 수식으로 실행하지 않도록).
    assert rows[1][0].startswith("'")
    assert rows[1][0].lstrip("'")[:2] == payload.lstrip()[:2] or payload in rows[1][0]


@pytest.mark.parametrize("payload", _INJECTION)
def test_scores_csv_id_header_and_value_injection_guarded(tmp_path, payload):
    prep = _prep(n=60)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    n = prep.matrix.shape[0]
    text = scores_table_csv(res, prep.matrix, [(payload, [payload] + ["x"] * (n - 1))],
                            method="regression")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0][0].startswith("'")         # 열 이름
    assert rows[1][0].startswith("'")         # ID 값


def test_safe_text_leaves_ordinary_values_alone():
    from factorscan.report import _safe_text
    for ok in ["Q1", "불면_1", "S001", "1.5", "", "가나다"]:
        assert _safe_text(ok) == ok


def test_scores_regression_needs_correlation_matrix():
    prep = _prep(n=60)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    res.pop("correlation_matrix")
    with pytest.raises(ValueError, match="상관행렬"):
        scores_table_csv(res, prep.matrix, [], method="regression")


# ============================== 엑셀 / TSV 입력 ==============================
# 엑셀 픽스처는 openpyxl이 아니라 '원시 XML'로 만든다. 이유가 둘 있다:
#  (1) openpyxl은 문자열을 t="inlineStr"로 쓰지만, 진짜 마이크로소프트 엑셀은
#      xl/sharedStrings.xml + t="s"로 쓴다. openpyxl로만 시험하면 실제 임상 .xlsx가
#      타는 경로(공유 문자열 테이블)가 통째로 미검증으로 남는다.
#  (2) 테스트가 서드파티 패키지에 의존하지 않아 어느 환경에서도 조용히 skip되지 않는다.
_XLSX_WB = ('<?xml version="1.0"?><workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets>{sheets}</sheets></workbook>')
_XLSX_RELS = ('<?xml version="1.0"?><Relationships '
              'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '{rels}</Relationships>')


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _write_xlsx(path, sheets, shared=True, styles=None, date1904=False):
    """원시 XML로 .xlsx를 만든다. sheets: [(시트명, [[셀,...], ...]), ...].

    shared=True 면 문자열을 sharedStrings 테이블(진짜 엑셀 방식)로, False 면
    inlineStr(openpyxl 방식)로 쓴다. 숫자는 그대로 <v>. None은 빈 셀(셀 자체를 생략).
    styles: {(행,열): style_index} 로 특정 셀에 s= 를 붙인다(날짜 서식 시험용).
    """
    import zipfile
    styles = styles or {}
    table, index = [], {}

    def sref(v):
        if v not in index:
            index[v] = len(table)
            table.append(v)
        return index[v]

    sheet_xml = []
    for si, (_name, rows) in enumerate(sheets):
        body = []
        for ri, row in enumerate(rows, start=1):
            cells = []
            for ci, val in enumerate(row):
                if val is None:
                    continue
                ref = ""
                n = ci
                while True:
                    ref = chr(ord("A") + n % 26) + ref
                    n = n // 26 - 1
                    if n < 0:
                        break
                sattr = f' s="{styles[(ri, ci)]}"' if (ri, ci) in styles else ""
                if isinstance(val, str):
                    if shared:
                        cells.append(f'<c r="{ref}{ri}"{sattr} t="s"><v>{sref(val)}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}{ri}"{sattr} t="inlineStr">'
                                     f'<is><t>{_esc(val)}</t></is></c>')
                else:
                    cells.append(f'<c r="{ref}{ri}"{sattr}><v>{val}</v></c>')
            body.append(f'<row r="{ri}">{"".join(cells)}</row>')
        sheet_xml.append(
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
            f'/spreadsheetml/2006/main"><sheetData>{"".join(body)}</sheetData></worksheet>')

    zf = zipfile.ZipFile(str(path), "w")
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    sheets_tag = "".join(f'<sheet name="{_esc(nm)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
                         for i, (nm, _) in enumerate(sheets))
    zf.writestr("xl/workbook.xml", _XLSX_WB.format(sheets=sheets_tag).replace(
        "<sheets>", pr + "<sheets>"))
    zf.writestr("xl/_rels/workbook.xml.rels", _XLSX_RELS.format(rels="".join(
        f'<Relationship Id="rId{i+1}" Type="x" Target="worksheets/sheet{i+1}.xml"/>'
        for i in range(len(sheets)))))
    for i, xml in enumerate(sheet_xml):
        zf.writestr(f"xl/worksheets/sheet{i+1}.xml", xml)
    if shared:
        zf.writestr("xl/sharedStrings.xml",
                    '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org'
                    '/spreadsheetml/2006/main">'
                    + "".join(f"<si><t>{_esc(t)}</t></si>" for t in table) + "</sst>")
    zf.writestr("xl/styles.xml",
                '<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats.org'
                '/spreadsheetml/2006/main">'
                '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/></numFmts>'
                '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="14"/>'
                '</cellXfs></styleSheet>')
    zf.close()
    return str(path)


def _make_xlsx(path, header, rows, sheet="Sheet1", extra_sheet=None):
    sheets = [(sheet, [list(header)] + [list(r) for r in rows])]
    if extra_sheet:
        sheets.append((extra_sheet, [["x"], [1]]))
    return _write_xlsx(path, sheets)


def test_xlsx_matches_csv_path_exactly(tmp_path):
    header = [f"Q{i+1}" for i in range(6)]
    x = _two_factor_raw(60)
    rows = [[float(v) for v in row] for row in x]
    xpath = _make_xlsx(tmp_path / "d.xlsx", header, rows)
    cpath = _write_csv(tmp_path / "d.csv", header, [[repr(v) for v in row] for row in rows])

    a = listwise(select_items(load_table(cpath)))
    b = listwise(select_items(load_table(xpath)))
    assert a.names == b.names
    assert np.allclose(a.matrix, b.matrix)


def test_xlsx_reads_strings_numbers_and_blanks(tmp_path):
    path = _make_xlsx(tmp_path / "m.xlsx", ["ID", "Q1", "Q2"],
                      [["S1", 1, 2.5], ["S2", None, 3], ["S3", 4, 5]])
    cols = load_xlsx(path)
    assert list(cols) == ["ID", "Q1", "Q2"]
    assert list(cols["ID"]) == ["S1", "S2", "S3"]
    assert cols["Q1"][1] == ""              # 빈 셀은 빈 문자열 → 뒤에서 NaN
    ds = select_items(cols, items=["Q1", "Q2"])
    assert np.isnan(ds.data[1, 0])
    assert ds.data[0, 1] == 2.5


def test_xlsx_sheet_selection_and_unknown_sheet(tmp_path):
    path = _make_xlsx(tmp_path / "s.xlsx", ["Q1", "Q2"], [[1, 2], [3, 4]],
                      sheet="응답", extra_sheet="메모")
    assert list(load_xlsx(path, sheet="응답")) == ["Q1", "Q2"]
    assert list(load_xlsx(path)) == ["Q1", "Q2"]        # 기본 = 첫 시트
    with pytest.raises(DataError, match="시트를 찾을 수 없습니다"):
        load_xlsx(path, sheet="없는시트")


def test_xlsx_error_paths(tmp_path):
    fake = tmp_path / "fake.xlsx"
    fake.write_text("Q1,Q2\n1,2\n", encoding="utf-8")     # 확장자만 xlsx인 CSV
    with pytest.raises(DataError, match="엑셀"):
        load_table(str(fake))
    with pytest.raises(DataError, match="구형식"):
        load_table(str(tmp_path / "old.xls"))
    with pytest.raises(FileNotFoundError):
        load_table(str(tmp_path / "nope.xlsx"))


def test_xlsx_header_only_and_duplicate_headers(tmp_path):
    p1 = _make_xlsx(tmp_path / "h.xlsx", ["Q1", "Q2"], [])
    with pytest.raises(DataError, match="데이터 행이 없습니다"):
        load_xlsx(p1)
    p2 = _make_xlsx(tmp_path / "dup.xlsx", ["Q1", "Q1"], [[1, 2]])
    with pytest.raises(DataError, match="중복된 열 이름"):
        load_xlsx(p2)


def test_xlsx_blank_header_cell_rejected(tmp_path):
    # 가운데가 빈 헤더는 열 정렬이 어긋난 신호 — 조용히 넘기면 안 된다.
    p = _make_xlsx(tmp_path / "b.xlsx", ["Q1", None, "Q3"], [[1, 2, 3]])
    with pytest.raises(DataError, match="이름이 빈 열"):
        load_xlsx(p)


def test_xlsx_trailing_empty_header_trimmed(tmp_path):
    p = _make_xlsx(tmp_path / "t.xlsx", ["Q1", "Q2", None], [[1, 2, None], [3, 4, None]])
    cols = load_xlsx(p)
    assert list(cols) == ["Q1", "Q2"]


def test_col_index_handles_multiletter_refs():
    from factorscan.dataio import _col_index
    assert _col_index("A1") == 0
    assert _col_index("Z9") == 25
    assert _col_index("AA1") == 26
    assert _col_index("AB10") == 27
    assert _col_index("BA100") == 52


def test_xlsx_end_to_end_cli_matches_csv(tmp_path, capsys):
    header = [f"Q{i+1}" for i in range(6)]
    x = _two_factor_raw(80)
    rows = [[float(v) for v in row] for row in x]
    xpath = _make_xlsx(tmp_path / "d.xlsx", header, rows)
    cpath = _write_csv(tmp_path / "d.csv", header, [[repr(v) for v in row] for row in rows])

    assert run([xpath, "--parallel-iter", "0", "--json"]) == 0
    a = json.loads(capsys.readouterr().out)
    assert run([cpath, "--parallel-iter", "0", "--json"]) == 0
    b = json.loads(capsys.readouterr().out)
    assert a["eigenvalues"] == pytest.approx(b["eigenvalues"])
    assert np.allclose(np.array(a["loadings"]), np.array(b["loadings"]))


def test_tsv_autodetected(tmp_path, capsys):
    header = [f"Q{i+1}" for i in range(6)]
    x = _two_factor_raw(60)
    p = tmp_path / "d.tsv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(header)
        for row in x:
            w.writerow([f"{v:.4f}" for v in row])
    cols = load_table(str(p))
    assert list(cols) == header
    assert run([str(p), "--parallel-iter", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n_items"] == 6


def test_custom_delimiter(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Q1;Q2\n1;2\n3;4\n", encoding="utf-8")
    cols = load_table(str(p), delimiter=";")
    assert list(cols) == ["Q1", "Q2"]
    assert list(cols["Q2"]) == ["2", "4"]


def test_cli_delimiter_option(tmp_path, capsys):
    rng = np.random.default_rng(9)
    x = rng.standard_normal((40, 3))
    lines = ["Q1;Q2;Q3"] + [";".join(f"{v:.3f}" for v in row) for row in x]
    p = tmp_path / "semi.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert run([str(p), "--delimiter", ";", "--parallel-iter", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n_items"] == 3


@pytest.mark.parametrize("shared", [True, False], ids=["sharedStrings(real-Excel)", "inlineStr"])
def test_xlsx_both_string_dialects_equal_csv(tmp_path, shared):
    """진짜 엑셀(sharedStrings)과 openpyxl(inlineStr) 두 방언 모두 CSV 경로와 일치해야 한다."""
    header = ["ID", "Q1", "Q2", "Q3"]
    body = [["S1", 1, 2.5, -3], ["S2", 4, 5, 6], ["S3", 2, 3, 1], ["S4", 5, 1, 4]]
    p = _write_xlsx(tmp_path / f"d{int(shared)}.xlsx", [("응답", [header] + body)], shared=shared)
    cols = load_xlsx(p)
    assert list(cols) == header
    assert list(cols["ID"]) == ["S1", "S2", "S3", "S4"]
    assert list(cols["Q2"]) == ["2.5", "5", "3", "1"]
    assert list(cols["Q3"]) == ["-3", "6", "1", "4"]      # 음수 보존


def test_xlsx_shared_string_index_reused_across_cells(tmp_path):
    # 같은 문자열은 테이블 항목 하나를 여러 셀이 참조한다 — 인덱스 매핑이 어긋나면 값이 뒤섞인다.
    p = _write_xlsx(tmp_path / "s.xlsx",
                    [("S", [["G", "Q1"], ["a", 1], ["b", 2], ["a", 3], ["b", 4]])])
    cols = load_xlsx(p)
    assert list(cols["G"]) == ["a", "b", "a", "b"]


def test_xlsx_corrupt_shared_string_index_errors(tmp_path):
    import zipfile
    p = _write_xlsx(tmp_path / "c.xlsx", [("S", [["Q1", "Q2"], ["a", 1]])])
    zin = zipfile.ZipFile(p)
    data = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    # 문자열 테이블을 비워 참조 인덱스를 범위 밖으로 만든다.
    data["xl/sharedStrings.xml"] = (
        b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org'
        b'/spreadsheetml/2006/main"></sst>')
    zo = zipfile.ZipFile(p, "w")
    for n, v in data.items():
        zo.writestr(n, v)
    zo.close()
    with pytest.raises(DataError, match="공유 문자열"):
        load_xlsx(p)


def test_xlsx_phonetic_ruby_not_merged_into_names(tmp_path):
    """한중일 엑셀이 넣는 <rPh>(후리가나)가 열 이름에 섞이면 안 된다."""
    import zipfile
    p = _write_xlsx(tmp_path / "p.xlsx", [("S", [["Q1", "Q2"], [1, 2], [3, 4]])])
    zin = zipfile.ZipFile(p)
    data = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    data["xl/sharedStrings.xml"] = (
        '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org'
        '/spreadsheetml/2006/main">'
        '<si><t>Q1</t><rPh sb="0" eb="2"><t>ルビ</t></rPh></si>'
        '<si><r><t>Q</t></r><r><t>2</t></r></si>'      # 리치텍스트 런은 이어붙여야 한다
        '</sst>').encode()
    zo = zipfile.ZipFile(p, "w")
    for n, v in data.items():
        zo.writestr(n, v)
    zo.close()
    assert list(load_xlsx(p)) == ["Q1", "Q2"]


def test_xlsx_date_cells_not_treated_as_likert(tmp_path):
    """날짜 서식 셀은 일련번호(43831)가 아니라 날짜 문자열이어야 한다.

    '검사일' 같은 열이 숫자로 들어오면 분산도 있고 결측도 없어 자동선택을 통과해
    리커트 문항인 척 요인분석에 섞인다(임상 엑셀 내보내기에서 흔한 사고).
    """
    rows = [["Q1", "검사일", "Q2"]]
    for i in range(6):
        rows.append([i % 5 + 1, 43831 + i, (i * 3) % 5 + 1])
    styles = {(r, 1): 1 for r in range(2, 8)}      # 2행부터 B열에 날짜 서식(numFmtId=164)
    p = _write_xlsx(tmp_path / "d.xlsx", [("S", rows)], styles=styles)
    cols = load_xlsx(p)
    assert list(cols["검사일"])[:2] == ["2020-01-01", "2020-01-02"]
    # 자동선택에서 빠지고, 왜 빠졌는지 보고돼야 한다.
    ds = select_items(cols)
    assert ds.names == ["Q1", "Q2"]
    assert "검사일" in ds.dropped


def test_xlsx_builtin_date_format_id(tmp_path):
    # numFmtId=14 는 내장 날짜 서식(styles.xml에 formatCode가 없어도 날짜로 인식해야 한다).
    p = _write_xlsx(tmp_path / "b.xlsx", [("S", [["Q1", "D"], [1, 43831], [2, 43832]])],
                    styles={(2, 1): 2, (3, 1): 2})
    assert list(load_xlsx(p)["D"]) == ["2020-01-01", "2020-01-02"]


def test_xlsx_date1904_workbook(tmp_path):
    p = _write_xlsx(tmp_path / "m.xlsx", [("S", [["Q1", "D"], [1, 0], [2, 1]])],
                    styles={(2, 1): 1, (3, 1): 1}, date1904=True)
    assert list(load_xlsx(p)["D"]) == ["1904-01-01", "1904-01-02"]


def test_serial_to_iso_epoch_boundaries():
    from factorscan.dataio import _serial_to_iso
    assert _serial_to_iso("43831", False) == "2020-01-01"
    assert _serial_to_iso("61", False) == "1900-03-01"      # 가짜 윤일 직후
    assert _serial_to_iso("59", False) == "1900-02-28"      # 가짜 윤일 직전
    assert _serial_to_iso("60", False) == "1900-02-29"      # 엑셀만 존재한다고 믿는 날
    assert _serial_to_iso("1", False) == "1900-01-01"
    # 1904 체계는 밀림 보정이 없다.
    assert _serial_to_iso("0", True) == "1904-01-01"
    assert _serial_to_iso("nonsense", False) == "nonsense"  # 변환 불가는 원문 유지
    assert _serial_to_iso("1e400", False) == "1e400"        # 오버플로도 원문 유지


def test_col_index_rejects_out_of_range_and_malformed():
    """비-ASCII 셀 참조가 20만 칸짜리 행으로 전개되던 메모리 증폭 회귀 가드."""
    from factorscan.dataio import _col_index
    assert _col_index("XFD1") == 16383          # 엑셀 최대 열
    for bad in ["AAAAAA1", "가1", "1", "𪘀1"]:
        with pytest.raises(DataError):
            _col_index(bad)


def test_xlsx_cell_ref_amplification_is_rejected_fast(tmp_path):
    """3KB 파일이 수십만 칸 행을 만들어 메모리를 고갈시키던 공격의 회귀 가드."""
    import zipfile
    rows = "".join(f'<row r="{i}"><c r="\U0002A600{i}" t="inlineStr"><is><t>x</t></is></c></row>'
                   for i in range(1, 201))
    p = tmp_path / "amp.xlsx"
    zf = zipfile.ZipFile(str(p), "w", zipfile.ZIP_DEFLATED)
    zf.writestr("xl/workbook.xml", _XLSX_WB.format(
        sheets='<sheet name="S" sheetId="1" r:id="rId1"/>'))
    zf.writestr("xl/_rels/workbook.xml.rels", _XLSX_RELS.format(
        rels='<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/>'))
    zf.writestr("xl/worksheets/sheet1.xml",
                '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
                f'/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>')
    zf.close()
    with pytest.raises(DataError):
        load_xlsx(str(p))


def test_xlsx_oversized_member_rejected(tmp_path):
    """압축폭탄: 압축 해제 크기가 상한을 넘으면 읽지 않고 거부한다."""
    import zipfile
    from factorscan import dataio
    p = _write_xlsx(tmp_path / "z.xlsx", [("S", [["Q1", "Q2"], [1, 2]])])
    orig = dataio._XLSX_MAX_MEMBER_BYTES
    try:
        dataio._XLSX_MAX_MEMBER_BYTES = 10      # 상한을 낮춰 동일 코드경로를 시험
        with pytest.raises(DataError, match="너무 큽니다"):
            load_xlsx(p)
    finally:
        dataio._XLSX_MAX_MEMBER_BYTES = orig
    assert list(load_xlsx(p)) == ["Q1", "Q2"]   # 상한 복구 후 정상


def test_xlsx_rless_cell_does_not_clobber(tmp_path):
    """r 속성이 없는 셀이 이미 채워진 열을 덮어써 값이 사라지던 회귀 가드."""
    import zipfile
    body = ('<row r="1"><c r="A1" t="inlineStr"><is><t>Q1</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>Q2</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>Q3</t></is></c></row>')
    for i in range(2, 6):
        body += f'<row r="{i}"><c r="A{i}"><v>1</v></c><c r="B{i}"><v>2</v></c><c><v>3</v></c></row>'
    p = tmp_path / "r.xlsx"
    zf = zipfile.ZipFile(str(p), "w")
    zf.writestr("xl/workbook.xml", _XLSX_WB.format(
        sheets='<sheet name="S" sheetId="1" r:id="rId1"/>'))
    zf.writestr("xl/_rels/workbook.xml.rels", _XLSX_RELS.format(
        rels='<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/>'))
    zf.writestr("xl/worksheets/sheet1.xml",
                '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
                f'/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>')
    zf.close()
    cols = load_xlsx(str(p))
    # r 없는 셀은 B 다음인 C에 놓여야 한다(B를 덮어쓰지 않음).
    assert list(cols["Q2"]) == ["2", "2", "2", "2"]
    assert list(cols["Q3"]) == ["3", "3", "3", "3"]


def test_xlsx_no_xxe_or_entity_expansion(tmp_path):
    """외부 엔티티/엔티티 폭탄이 해석되지 않아야 한다(신뢰할 수 없는 파일 입력)."""
    import zipfile
    p = tmp_path / "x.xlsx"
    zf = zipfile.ZipFile(str(p), "w")
    zf.writestr("xl/workbook.xml", _XLSX_WB.format(
        sheets='<sheet name="S" sheetId="1" r:id="rId1"/>'))
    zf.writestr("xl/_rels/workbook.xml.rels", _XLSX_RELS.format(
        rels='<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/>'))
    zf.writestr("xl/worksheets/sheet1.xml",
                '<?xml version="1.0"?>'
                '<!DOCTYPE t [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&xxe;</t></is></c></row>'
                '</sheetData></worksheet>')
    zf.close()
    with pytest.raises(DataError):          # 엔티티 미해석 → 파싱 오류로 거부
        load_xlsx(str(p))


def test_xlsx_non_utf8_safe_and_korean_headers(tmp_path):
    # xlsx는 내부적으로 UTF-8 XML이라 --encoding과 무관하게 한글이 안전해야 한다.
    p = _make_xlsx(tmp_path / "k.xlsx", ["환자ID", "불면_1", "불면_2"],
                   [["가001", 3, 4], ["가002", 2, 5], ["가003", 1, 1]])
    cols = load_table(p, encoding="cp949")     # encoding은 xlsx에 영향 없음
    assert list(cols) == ["환자ID", "불면_1", "불면_2"]
    assert cols["환자ID"][0] == "가001"


# ==================== 라운드1 패널 수정: 역문항·기술통계·판정 ====================
def _likert_two_factor(n=200, seed=3, reverse_item=None):
    """1~5 리커트 2요인 자료. reverse_item 인덱스는 역문항으로(6-x) 바꿔 둔다."""
    rng = np.random.default_rng(seed)
    f1, f2 = rng.standard_normal(n), rng.standard_normal(n)
    cols = [f1 + 0.5 * rng.standard_normal(n) for _ in range(3)]
    cols += [f2 + 0.5 * rng.standard_normal(n) for _ in range(3)]
    x = np.clip(np.round(np.column_stack(cols) * 1.2 + 3), 1, 5)
    if reverse_item is not None:
        x[:, reverse_item] = 6 - x[:, reverse_item]
    names = [f"Q{i+1}" for i in range(6)]
    return listwise(Dataset(names=names, data=x))


def test_unreversed_item_is_named_with_actionable_fix():
    """역문항 미처리를 '증상'이 아니라 '원인'으로 짚고, 실행할 명령을 제시해야 한다."""
    res = analyze(_likert_two_factor(reverse_item=2), parallel_iter=0, n_factors=2)
    assert res["negative_loading_items"] == ["Q3"]
    w = " ".join(res["warnings"])
    assert "역문항" in w and "--reverse Q3" in w


def test_no_false_reverse_flag_on_clean_data():
    res = analyze(_likert_two_factor(), parallel_iter=0, n_factors=2)
    assert res["negative_loading_items"] == []
    assert not any("역문항" in w for w in res["warnings"])


def test_sum_scores_refused_when_item_direction_conflicts():
    """역문항이 섞인 채 합산하면 그 문항이 거꾸로 더해진다 — 조용히 쓰지 말고 거부."""
    prep = _likert_two_factor(reverse_item=2)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    for method in ("sum", "mean"):
        with pytest.raises(ValueError, match="거꾸로"):
            scores_table_csv(res, prep.matrix, [], method=method)
    # 회귀점수는 부호를 가중치가 품으므로 허용된다.
    assert scores_table_csv(res, prep.matrix, [], method="regression")


def test_cli_scores_out_refuses_and_writes_no_file(tmp_path, capsys):
    x = _likert_two_factor(reverse_item=2).matrix
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[int(v) for v in row] for row in x])
    out = tmp_path / "s.csv"
    assert run([path, "--parallel-iter", "0", "--scores-out", str(out)]) == 1
    assert "--reverse" in capsys.readouterr().err
    assert not out.exists()          # 오염된 파일을 남기지 않는다


def test_declaring_reverse_restores_alpha_and_clears_flag(tmp_path):
    """역문항을 선언하면 음수 적재가 사라지고 α가 정상으로 돌아와야 한다."""
    bad = analyze(_likert_two_factor(reverse_item=2), parallel_iter=0, n_factors=2)
    # 미처리 상태에서는 해당 요인의 α가 무너진다(음수까지 갈 수 있다).
    assert min(a for a in bad["alpha"] if a is not None) < 0.5

    prep = _likert_two_factor(reverse_item=2)
    prep.matrix[:, 2] = 6 - prep.matrix[:, 2]        # 올바르게 재점수화
    good = analyze(prep, parallel_iter=0, n_factors=2)
    assert good["negative_loading_items"] == []
    assert all(a > 0.7 for a in good["alpha"] if a is not None)


# ---------------- 문항 기술통계 · 바닥/천장 ----------------
def test_item_descriptives_match_manual_moments():
    x = np.array([[1.0], [2.0], [2.0], [3.0], [5.0]])
    d = efa.item_descriptives(x)[0]
    col = x[:, 0]
    mu = col.mean()
    m2 = ((col - mu) ** 2).mean()
    assert d["mean"] == pytest.approx(mu)
    assert d["sd"] == pytest.approx(col.std(ddof=1))
    assert d["skew"] == pytest.approx(((col - mu) ** 3).mean() / m2 ** 1.5)
    assert d["kurtosis"] == pytest.approx(((col - mu) ** 4).mean() / m2 ** 2 - 3.0)
    assert d["min"] == 1.0 and d["max"] == 5.0
    assert d["floor_prop"] == pytest.approx(0.2)     # 1이 1개/5
    assert d["ceiling_prop"] == pytest.approx(0.2)   # 5가 1개/5


def test_item_descriptives_use_declared_scale_range():
    # 아무도 1이나 5를 고르지 않았어도, 선언된 척도범위 기준이면 바닥/천장은 0이어야 한다.
    x = np.array([[2.0], [3.0], [3.0], [4.0]])
    d = efa.item_descriptives(x, scale_min=1, scale_max=5)[0]
    assert d["floor_prop"] == 0.0 and d["ceiling_prop"] == 0.0
    d2 = efa.item_descriptives(x)[0]                 # 범위 미지정 → 관측 최소/최대 기준
    assert d2["floor_prop"] == pytest.approx(0.25)


def test_item_descriptives_constant_column_is_finite():
    d = efa.item_descriptives(np.full((10, 1), 3.0))[0]
    assert d["sd"] == 0.0 and d["skew"] == 0.0 and d["kurtosis"] == 0.0
    assert np.isfinite(d["floor_prop"])


def test_floor_ceiling_threshold_is_category_aware():
    """5점 리커트는 균등응답만으로도 끝 범주가 20%다 — 15% 고정 기준은 거짓 경보를 낸다."""
    assert efa.floor_ceiling_threshold(5) == pytest.approx(0.30)
    assert efa.floor_ceiling_threshold(7) == pytest.approx(1.5 / 7)
    assert efa.floor_ceiling_threshold(50) == pytest.approx(0.15)   # 연속형은 관례값으로 수렴
    assert efa.floor_ceiling_threshold(1) == 1.0                    # 상수는 여기서 안 잡음


def test_uniform_likert_not_flagged_but_real_floor_effect_is():
    rng = np.random.default_rng(5)
    n = 300
    f = rng.standard_normal(n)
    cols = [np.clip(np.round(f * 1.2 + 3), 1, 5) for _ in range(4)]
    x = np.column_stack(cols)
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(4)], data=x))
    res = analyze(prep, parallel_iter=0, n_factors=1, scale_min=1, scale_max=5)
    assert not any("바닥" in " ".join(f["problems"]) for f in res["item_flags"])

    x2 = x.copy()
    x2[rng.choice(n, int(0.6 * n), replace=False), 0] = 1     # Q1에 진짜 바닥효과
    prep2 = listwise(Dataset(names=[f"Q{i+1}" for i in range(4)], data=x2))
    res2 = analyze(prep2, parallel_iter=0, n_factors=1, scale_min=1, scale_max=5)
    q1 = next(f for f in res2["item_flags"] if f["item"] == "Q1")
    assert any("바닥효과" in p for p in q1["problems"])


def test_skew_kurtosis_warning_suggests_polychoric():
    rng = np.random.default_rng(9)
    n = 300
    f = rng.standard_normal(n)
    cols = [np.clip(np.round(f * 1.2 + 3), 1, 5) for _ in range(4)]
    x = np.column_stack(cols)
    x[:, 0] = 1.0
    x[rng.choice(n, 6, replace=False), 0] = 5.0      # 극단적으로 치우친 문항(|왜도| 큼)
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(4)], data=x))
    res = analyze(prep, parallel_iter=0, n_factors=1)
    assert any("polychoric" in nt for nt in res["notes"])


def test_report_shows_descriptives_table():
    out = render(analyze(_likert_two_factor(), parallel_iter=0, n_factors=2))
    assert "[ 1-1. 문항 기술통계 ]" in out
    assert "왜도" in out and "천장" in out


# ---------------- α if deleted ----------------
def test_alpha_if_deleted_matches_direct_recomputation():
    prep = _likert_two_factor(n=150)
    res = analyze(prep, parallel_iter=0, n_factors=2)
    L = np.array(res["loadings"])
    groups = np.argmax(np.abs(L), axis=1)
    aid = res["alpha_if_deleted"]
    for i in range(prep.matrix.shape[1]):
        mates = [j for j in range(prep.matrix.shape[1])
                 if groups[j] == groups[i] and j != i]
        expect = efa.cronbach_alpha(prep.matrix[:, mates]) if len(mates) >= 2 else None
        if expect is None:
            assert np.isnan(aid[i])
        else:
            assert aid[i] == pytest.approx(expect)


def test_alpha_if_deleted_nan_when_factor_too_small():
    x = np.random.default_rng(1).standard_normal((50, 3))
    out = efa.alpha_if_deleted(x, [0, 0, 1], 2)
    assert np.isnan(out[2])          # 혼자인 요인
    assert np.all(np.isnan(out[:2]))  # 빼고 나면 1문항 → α 정의 불가


def test_bad_item_flagged_by_alpha_if_deleted():
    # 요인에 무관한 잡음 문항을 넣으면 '빼면 α가 오른다'로 잡혀야 한다.
    rng = np.random.default_rng(4)
    n = 200
    f = rng.standard_normal(n)
    cols = [f + 0.3 * rng.standard_normal(n) for _ in range(4)]
    cols.append(rng.standard_normal(n))          # Q5 = 순수 잡음
    x = np.column_stack(cols)
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(5)], data=x))
    res = analyze(prep, parallel_iter=0, n_factors=1)
    q5 = next(f for f in res["item_flags"] if f["item"] == "Q5")
    assert any("제거시 α↑" in p for p in q5["problems"])


# ---------------- 작은 표본 판정 ----------------
def test_absolute_small_n_warns_even_when_ratio_rule_passes():
    """n=60·p=10 은 문항당 6명이라 비율 규칙을 통과하지만 절대 표본이 위험하다."""
    rng = np.random.default_rng(2)
    x = rng.standard_normal((60, 10))
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(10)], data=x))
    res = analyze(prep, parallel_iter=0, n_factors=2)
    assert not any("문항당" in w and "권장" in w for w in res["warnings"])  # 비율 규칙은 통과
    assert any("절대 표본 수가 작습니다" in w for w in res["warnings"])


def test_no_small_n_warning_for_large_sample():
    res = analyze(_prep(n=400), parallel_iter=0, n_factors=2)
    assert not any("절대 표본 수" in w for w in res["warnings"])


def test_chi_square_nonrejection_not_called_fit_at_small_n():
    """작은 표본의 χ² 비유의는 '적합의 증거'가 아니다 — 그렇게 읽히면 안 된다."""
    rng = np.random.default_rng(6)
    f = rng.standard_normal(60)
    cols = [f + 0.6 * rng.standard_normal(60) for _ in range(5)]
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(5)], data=np.column_stack(cols)))
    res = analyze(prep, parallel_iter=0, extraction="ml", n_factors=1)
    if res["fit"] and res["fit"].get("p_value", 0) >= 0.05:
        text = render(res)
        assert "검정력이 낮습니다" in text


def test_rmsea_verdict_reads_confidence_interval_not_point():
    prep = _prep(n=250)
    res = analyze(prep, parallel_iter=0, extraction="ml", n_factors=2)
    fit = res["fit"]
    text = render(res)
    if fit["rmsea_hi"] is not None and fit["rmsea_hi"] > 0.10 >= fit["rmsea"]:
        assert "결론 보류" in text          # 점추정만 보고 '우수'라 하지 않는다


def test_tli_above_one_is_explained_not_praised():
    res = analyze(_prep(n=300), parallel_iter=0, extraction="ml", n_factors=2, fit_scan=True)
    scan = res["fit_scan"]
    over = [r for r in scan if (r.get("tli") or 0) > 1.0]
    if over:                              # 이 자료에서 과다모수화 k가 있으면
        res2 = analyze(_prep(n=300), parallel_iter=0, extraction="ml",
                       n_factors=over[-1]["k"])
        if (res2["fit"].get("tli") or 0) > 1.0:
            assert "과하다는 신호" in render(res2)


# ---------------- 자동 제외 열 · 결측 전멸 ----------------
def test_dropped_columns_are_reported_not_silent(tmp_path):
    cols = {
        "Q1": np.array(["1,234"] * 30, dtype=object),         # 모호한 자릿수 쉼표
        "Q2": np.array([str(i % 5 + 1) for i in range(30)], dtype=object),
        "Q3": np.array([str((i * 3) % 5 + 1) for i in range(30)], dtype=object),
        "Qconst": np.array(["2"] * 30, dtype=object),          # 상수 열
    }
    ds = select_items(cols)
    assert ds.names == ["Q2", "Q3"]
    assert "Q1" in ds.dropped and "Qconst" in ds.dropped
    res = analyze(listwise(ds), parallel_iter=0, n_factors=1)
    w = " ".join(res["warnings"])
    assert "자동 제외된 열" in w and "Q1" in w and "Qconst" in w
    assert set(res["dropped_columns"]) == {"Q1", "Qconst"}


def test_listwise_wipeout_names_real_cause():
    x = np.array([[1.0, 2, 3, 4]] * 30)
    for i in range(30):
        x[i, i % 4] = np.nan          # 모든 행이 한 칸씩 결측 → 전멸
    prep = listwise(Dataset(names=[f"Q{i+1}" for i in range(4)], data=x))
    with pytest.raises(ValueError, match="결측 제거 후 남은 응답자"):
        analyze(prep, parallel_iter=0)


def test_genuinely_tiny_sample_message_is_about_sample_size():
    x = np.array([[1.0, 2, 3], [2, 3, 4]])
    prep = listwise(Dataset(names=["a", "b", "c"], data=x))
    with pytest.raises(ValueError, match="응답자 수가 너무 적습니다"):
        analyze(prep, parallel_iter=0)


def test_extreme_values_get_korean_error_not_numpy_spew():
    x = np.array([[1e308, 1.0, 2.0], [0.5e308, 2.0, 3.0], [0.9e308, 3.0, 1.0],
                  [0.2e308, 4.0, 5.0], [0.7e308, 5.0, 2.0]])
    prep = listwise(Dataset(names=["Q1", "Q2", "Q3"], data=x))
    with pytest.raises(ValueError, match="값이 너무 커서"):
        analyze(prep, parallel_iter=0)


# ---------------- 고유값(스크리) CSV ----------------
def test_eigen_table_csv_matches_result():
    from factorscan.report import eigen_table_csv
    res = analyze(_prep(n=200), parallel_iter=20, seed=1)
    rows = list(csv.reader(io.StringIO(eigen_table_csv(res), newline="")))
    assert rows[0] == ["factor", "eigenvalue", "parallel_95th",
                       "pct_variance", "cum_pct_variance", "retained"]
    assert len(rows) == len(res["eigenvalues"]) + 1
    for i, row in enumerate(rows[1:]):
        assert int(row[0]) == i + 1
        assert float(row[1]) == pytest.approx(res["eigenvalues"][i], abs=1e-4)
        assert float(row[2]) == pytest.approx(res["parallel_eigenvalues"][i], abs=1e-4)
        assert float(row[4]) == pytest.approx(res["cum_variance"][i] * 100, abs=1e-3)
    retained = [int(r[5]) for r in rows[1:]]
    assert sum(retained) == res["n_factors"]


def test_eigen_table_csv_without_parallel():
    from factorscan.report import eigen_table_csv
    res = analyze(_prep(n=100), parallel_iter=0)
    rows = list(csv.reader(io.StringIO(eigen_table_csv(res), newline="")))
    assert "parallel_95th" not in rows[0]


def test_cli_eigen_out(tmp_path, capsys):
    x = _two_factor_raw(120)
    path = _write_csv(tmp_path / "d.csv", [f"Q{i+1}" for i in range(6)],
                      [[f"{v:.4f}" for v in row] for row in x])
    out = tmp_path / "scree.csv"
    assert run([path, "--parallel-iter", "20", "--eigen-out", str(out)]) == 0
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8-sig"), newline="")))
    assert rows[0][0] == "factor" and len(rows) == 7


def test_factor_correlation_csv_label_states_provenance():
    """직교(varimax) 해에서도 Φ 행이 나가므로, promax '추정치'임을 이름에 박아야 한다."""
    prep = _prep(n=120)
    vari = loadings_table_csv(analyze(prep, parallel_iter=0, n_factors=2, rotation="varimax"))
    obli = loadings_table_csv(analyze(prep, parallel_iter=0, n_factors=2, rotation="promax"))
    vlab = [r[0] for r in csv.reader(io.StringIO(vari, newline=""))
            if r and r[0].startswith("_factor_correlation")][0]
    olab = [r[0] for r in csv.reader(io.StringIO(obli, newline=""))
            if r and r[0].startswith("_factor_correlation")][0]
    assert "promax_estimate" in vlab and "Φ=I" in vlab   # 직교: 추정치임을 명시
    assert vlab != olab and "estimate" not in olab       # 사교: 실제 해
