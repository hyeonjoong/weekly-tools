"""--missing pairwise(쌍별 삭제 상관)와 --html-out(단일 파일 HTML 보고서) 검증.

쌍별 삭제는 '정보를 더 쓰는 대신 셀마다 표본이 달라지는' 절충이라, 검증의 초점은
⑴ 상관값이 손으로 계산한 값과 일치하는가, ⑵ 손실된 표본을 실제로 되찾는가,
⑶ 되찾지 못하는 것(α·부트스트랩)을 정직하게 표시하는가에 있다.
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys

import numpy as np
import pytest

from factorscan import efa, polychoric
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise
from factorscan.report import render, render_html


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


def _prep(mat, names=None):
    mat = np.asarray(mat, dtype=float)
    names = names or [f"Q{i+1}" for i in range(mat.shape[1])]
    return listwise(Dataset(names=list(names), data=mat))


def _sim(n=200, p=8, miss=0.10, seed=3):
    """2요인 구조 + MCAR 결측이 흩어진 리커트 자료."""
    rng = np.random.default_rng(seed)
    f1, f2 = rng.normal(size=n), rng.normal(size=n)
    cols = []
    for i in range(p):
        lat = f1 if i < p // 2 else f2
        v = 0.8 * lat + 0.6 * rng.normal(size=n)
        cols.append(np.clip(np.round(v * 1.2 + 3), 1, 5))
    x = np.array(cols, dtype=float).T
    x[rng.random((n, p)) < miss] = np.nan
    return x


# ---------------------------------------------------------------- 수치 정확성

def test_pairwise_correlation_matches_manual():
    """쌍별 상관이 '둘 다 응답한 행'만으로 손계산한 피어슨 r과 일치한다."""
    x = np.array([
        [1.0, 2.0, 3.0],
        [2.0, np.nan, 1.0],
        [3.0, 4.0, np.nan],
        [4.0, 5.0, 2.0],
        [5.0, 1.0, 5.0],
    ])
    r, counts = efa.pairwise_correlation(x)
    m = np.isfinite(x[:, 0]) & np.isfinite(x[:, 1])
    expect = np.corrcoef(x[m, 0], x[m, 1])[0, 1]
    assert counts[0, 1] == int(m.sum()) == 4
    assert r[0, 1] == pytest.approx(expect, abs=1e-12)
    assert r[0, 1] == pytest.approx(r[1, 0])
    assert np.allclose(np.diag(r), 1.0)
    assert counts[1, 1] == 4          # 대각은 그 문항의 관측 수


def test_pairwise_equals_listwise_when_no_missing():
    """결측이 없으면 쌍별 상관은 전체 상관과 완전히 같아야 한다."""
    rng = np.random.default_rng(11)
    x = rng.normal(size=(60, 5))
    r, counts = efa.pairwise_correlation(x)
    assert np.allclose(r, np.corrcoef(x, rowvar=False), atol=1e-12)
    assert (counts == 60).all()


def test_pairwise_uses_more_respondents_than_listwise():
    """쌍별 삭제가 실제로 표본을 되찾는다(완전응답자보다 훨씬 큰 쌍별 N)."""
    x = _sim()
    prep = _prep(x)
    res = analyze(prep, parallel_iter=0, missing="pairwise")
    pw = res["pairwise"]
    assert pw["n_complete"] == prep.n_used
    assert pw["n_min"] > 1.5 * prep.n_used     # 완전응답자보다 크게 늘어야 의미가 있다
    assert pw["n_min"] <= pw["n_median"] <= pw["n_max"] <= 200
    assert res["missing_method"] == "pairwise"


def test_pairwise_recovers_true_correlation_better_than_listwise():
    """MCAR 결측에서 쌍별 상관이 완전자료 상관에 더 가깝다(정보를 더 쓰므로)."""
    rng = np.random.default_rng(21)
    n, p = 400, 8
    f = rng.normal(size=n)
    full = np.array([0.7 * f + 0.7 * rng.normal(size=n) for _ in range(p)]).T
    truth = np.corrcoef(full, rowvar=False)
    x = full.copy()
    x[rng.random((n, p)) < 0.15] = np.nan
    r_pair, _ = efa.pairwise_correlation(x)
    complete = x[np.all(np.isfinite(x), axis=1)]
    r_list = np.corrcoef(complete, rowvar=False)
    off = ~np.eye(p, dtype=bool)
    err_pair = np.abs(r_pair - truth)[off].mean()
    err_list = np.abs(r_list - truth)[off].mean()
    assert err_pair < err_list


def test_smooth_correlation_makes_positive_definite():
    """비양정부호 행렬을 PD로 만들고 대각을 1로 유지한다."""
    bad = np.array([[1.0, 0.9, -0.9],
                    [0.9, 1.0, 0.9],
                    [-0.9, 0.9, 1.0]])
    assert not efa.is_positive_definite(bad)
    out, sm = efa.smooth_correlation(bad)
    assert efa.is_positive_definite(out)
    assert np.allclose(np.diag(out), 1.0, atol=1e-9)
    assert np.allclose(out, out.T, atol=1e-12)
    assert sm["max_delta"] > 0
    assert sm["min_eig_before"] < 0          # 보정 전 최소 고유값이 음수였다
    assert sm["n_clipped"] >= 1
    assert np.all(np.abs(out) <= 1.0 + 1e-9)


def test_smooth_correlation_leaves_good_matrix_untouched():
    rng = np.random.default_rng(5)
    z = rng.normal(size=(80, 4))
    r = np.corrcoef(z, rowvar=False)
    out, sm = efa.smooth_correlation(r)
    assert sm["max_delta"] == pytest.approx(0.0, abs=1e-12)
    assert sm["n_clipped"] == 0
    assert np.allclose(out, r, atol=1e-12)


def test_effective_n_is_conservative_minimum():
    """검정용 유효 N은 쌍별 표본의 최솟값(낙관적으로 부풀리지 않음)."""
    x = _sim(seed=9)
    res = analyze(_prep(x), parallel_iter=0, missing="pairwise")
    obs = np.isfinite(x).astype(int)
    counts = obs.T @ obs
    off = counts[np.triu_indices(x.shape[1], k=1)]
    assert res["pairwise"]["n_min"] == int(off.min())
    # Bartlett χ²가 그 유효 N으로 계산됐는지 직접 재계산해 확인한다.
    b = efa.bartlett_sphericity(np.asarray(res["correlation_matrix"]),
                                int(off.min()))
    assert res["bartlett"]["chi_square"] == pytest.approx(b.chi_square, rel=1e-9)


# ------------------------------------------------------------------ 정직성

def test_pairwise_reports_split_samples():
    """상관용 표본과 α용 표본이 다르다는 사실을 보고서에 남긴다."""
    res = analyze(_prep(_sim(seed=13)), parallel_iter=0, missing="pairwise")
    joined = " ".join(res["notes"])
    assert "pairwise" in joined and "완전응답자" in joined
    text = render(res)
    assert "쌍별 삭제(pairwise)" in text
    assert "유효 N" in text


def test_listwise_result_unchanged_by_default():
    """기본값(listwise)에서는 pairwise 키가 None이고 기존 동작과 동일하다."""
    x = _sim(seed=17)
    res = analyze(_prep(x), parallel_iter=0)
    assert res["pairwise"] is None
    assert res["missing_method"] == "listwise"
    complete = x[np.all(np.isfinite(x), axis=1)]
    assert np.allclose(np.asarray(res["correlation_matrix"]),
                       np.corrcoef(complete, rowvar=False), atol=1e-12)


def test_pairwise_rejects_unestimable_pair():
    """두 문항을 함께 응답한 사람이 없으면 0으로 메우지 않고 명확히 거절한다."""
    n = 40
    rng = np.random.default_rng(2)
    x = rng.normal(size=(n, 3))
    x[: n // 2, 0] = np.nan          # 앞 절반은 Q1 결측
    x[n // 2:, 1] = np.nan           # 뒷 절반은 Q2 결측 → Q1·Q2 동시 응답자 0명
    with pytest.raises(ValueError, match="쌍별"):
        analyze(_prep(x), parallel_iter=0, missing="pairwise")


def test_pairwise_rejects_all_missing_item():
    x = _sim(n=80, p=5, seed=4)
    x[:, 2] = np.nan
    with pytest.raises(ValueError, match="전부 결측"):
        analyze(_prep(x), parallel_iter=0, missing="pairwise")


def test_pairwise_works_when_no_complete_case():
    """완전응답자가 0명이어도 요인구조는 나오고, α는 정직하게 비운다."""
    rng = np.random.default_rng(8)
    n, p = 150, 6
    f = rng.normal(size=n)
    x = np.array([0.8 * f + 0.6 * rng.normal(size=n) for _ in range(p)]).T
    # 모든 행에 정확히 한 개씩 결측을 심어 완전응답자를 0명으로 만든다.
    for i in range(n):
        x[i, i % p] = np.nan
    prep = _prep(x)
    assert prep.n_used == 0
    with pytest.raises(ValueError):
        analyze(prep, parallel_iter=0)          # listwise는 당연히 실패
    res = analyze(prep, parallel_iter=0, missing="pairwise", bootstrap=200)
    assert res["n_factors"] >= 1
    assert all(a is None for a in res["alpha"])
    assert all(not np.isfinite(v) for v in res["item_total_by_factor"])
    assert res["bootstrap"] is None
    assert any("완전응답자가 0명" in n for n in res["notes"])
    assert any("부트스트랩을 생략" in w for w in res["warnings"])
    render(res)                                  # 렌더링이 깨지지 않아야 한다


def test_pairwise_descriptives_use_all_observations():
    """기술통계가 완전응답자가 아니라 '그 문항에 응답한 사람 전부'를 쓴다."""
    x = _sim(seed=19)
    res = analyze(_prep(x), parallel_iter=0, missing="pairwise")
    for i, d in enumerate(res["item_descriptives"]):
        col = x[:, i][np.isfinite(x[:, i])]
        assert d["n_obs"] == col.size
        assert d["mean"] == pytest.approx(float(col.mean()), rel=1e-12)
        assert d["sd"] == pytest.approx(float(col.std(ddof=1)), rel=1e-12)


def test_category_frequencies_ignore_missing():
    """범주표가 결측을 하나의 '범주'로 세지 않고 문항별 응답 수로 비율을 낸다."""
    x = np.array([[1.0, 1.0], [2.0, np.nan], [2.0, 3.0], [np.nan, 3.0]])
    cf = efa.category_frequencies(x, ["A", "B"], 1, 3)
    assert cf is not None
    a, b = cf["items"]
    assert a["n"] == 3 and b["n"] == 3
    assert a["counts"] == [1, 2, 0]
    assert b["counts"] == [1, 0, 2]
    assert sum(a["props"]) == pytest.approx(1.0)


def test_polychoric_pairwise_matches_masked_pair():
    """폴리코릭 쌍별 경로가 마스킹 후 직접 호출한 값과 같다."""
    rng = np.random.default_rng(6)
    n = 160
    f = rng.normal(size=n)
    x = np.array([np.clip(np.round(0.9 * f + 0.7 * rng.normal(size=n) + 3), 1, 5)
                  for _ in range(3)], dtype=float).T
    x[:20, 0] = np.nan
    x[30:45, 1] = np.nan
    r, counts = polychoric.polychoric_matrix(x, pairwise=True)
    m = np.isfinite(x[:, 0]) & np.isfinite(x[:, 1])
    direct = polychoric.polychoric_corr(x[m, 0].astype(np.int64),
                                        x[m, 1].astype(np.int64))
    assert counts[0, 1] == int(m.sum())
    assert r[0, 1] == pytest.approx(direct, abs=1e-9)


def test_polychoric_max_categories_ignores_nan():
    x = np.array([[1.0, np.nan], [2.0, 1.0], [3.0, 2.0], [np.nan, 2.0]])
    assert polychoric.max_categories(x) == 3


def test_analyze_rejects_bad_missing_argument():
    with pytest.raises(ValueError, match="missing"):
        analyze(_prep(_sim(n=60, p=4, miss=0.0, seed=1)), missing="fiml")


# ---------------------------------------------------------------------- CLI

def test_cli_pairwise_json(tmp_path, capsys):
    x = _sim(seed=23)
    rows = [["" if not np.isfinite(v) else int(v) for v in row] for row in x]
    path = _write_csv(tmp_path / "s.csv", [f"Q{i+1}" for i in range(x.shape[1])], rows)
    assert run([path, "--missing", "pairwise", "--parallel-iter", "0", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["missing_method"] == "pairwise"
    assert out["pairwise"]["n_min"] > out["n_used"]
    json.dumps(out)             # NaN/Inf 없이 직렬화 가능해야 한다


def test_cli_html_out(tmp_path, capsys):
    x = _sim(seed=29, miss=0.0)
    rows = [[int(v) for v in row] for row in x]
    path = _write_csv(tmp_path / "s.csv", [f"Q{i+1}" for i in range(x.shape[1])], rows)
    html = tmp_path / "r.html"
    assert run([path, "--html-out", str(html), "--parallel-iter", "20"]) == 0
    text = html.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert text.rstrip().endswith("</html>")
    assert "<svg" in text and "스크리 도표" in text
    assert "요인적재량" in text
    assert "ω" in text          # 신뢰도 표(라벨은 추출 방식에 따라 달라진다)
    # 값이 텍스트 보고서와 같은 자료에서 나왔는지 최소 확인(문항명 전부 포함)
    for i in range(x.shape[1]):
        assert f"Q{i+1}" in text


def test_html_escapes_item_names():
    """문항명에 HTML 특수문자가 있어도 문서가 깨지지 않는다(주입 방지)."""
    rng = np.random.default_rng(31)
    x = rng.normal(size=(60, 3))
    names = ["<script>a</script>", 'B"q', "C&d"]
    res = analyze(_prep(x, names), parallel_iter=0)
    html = render_html(res)
    assert "<script>a</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;d" in html


def test_html_survives_missing_optional_sections():
    """부트스트랩·적합도·평행분석이 없어도 HTML이 정상 생성된다."""
    rng = np.random.default_rng(37)
    x = rng.normal(size=(40, 4))
    res = analyze(_prep(x), parallel_iter=0)
    html = render_html(res)
    assert "</html>" in html
    assert html.count("<table") >= 3


def test_html_out_write_failure_is_reported(tmp_path, capsys):
    x = _sim(seed=41, miss=0.0)
    rows = [[int(v) for v in row] for row in x]
    path = _write_csv(tmp_path / "s.csv", [f"Q{i+1}" for i in range(x.shape[1])], rows)
    bad = tmp_path / "nope" / "r.html"          # 없는 디렉터리
    assert run([path, "--html-out", str(bad), "--parallel-iter", "0"]) == 1
    assert "HTML 저장 실패" in capsys.readouterr().err


def test_scree_svg_handles_single_factor():
    """문항이 2개(고유값 2개)여도 SVG 좌표가 NaN/무한대가 되지 않는다."""
    from factorscan.report import _scree_svg
    svg = _scree_svg({"eigenvalues": [1.6, 0.4], "parallel_eigenvalues": None})
    assert svg.startswith("<svg") and "nan" not in svg.lower()
    assert "inf" not in svg.lower()


def test_pairwise_bootstrap_uses_complete_case_parallel_reference(monkeypatch):
    """부트스트랩 평행분석 기준선은 '재표본 크기(=완전응답자 수)'로 만들어야 한다.

    쌍별 유효 N(더 큼)으로 만든 기준선을 그대로 쓰면 문턱이 낮아 재표본마다 요인이
    과대 유지되고 '안정적'이라는 착시가 생긴다.
    """
    from factorscan import analyze as A
    calls = []
    real = efa.parallel_analysis

    def spy(n, p, iters, seed, **kw):
        calls.append(int(n))
        return real(n, p, iters=iters, seed=seed, **kw)

    monkeypatch.setattr(A.efa, "parallel_analysis", spy)
    prep = _prep(_sim(n=240, p=8, miss=0.12, seed=43))
    res = A.analyze(prep, parallel_iter=30, missing="pairwise", bootstrap=60)
    # 첫 호출은 상관행렬 기준(쌍별 유효 N), 두 번째는 부트스트랩용(완전응답자 수)
    assert len(calls) == 2
    assert calls[0] == res["pairwise"]["n_min"]
    assert calls[1] == prep.n_used < calls[0]


# --------------------------------------------- HTML이 텍스트 보고서와 어긋나지 않는가
# (round10 리뷰: HTML은 그대로 공동연구자에게 전달되는 산출물이라, 라벨 하나가
#  텍스트 보고서보다 관대하게 읽히면 그 문자열이 그대로 논문에 복사된다.)

def _res_from_example(extraction="pca"):
    import json as _json
    from factorscan.dataio import load_table, select_items
    cfg = _json.load(open("examples/sleep_config.json", encoding="utf-8"))
    cols = load_table("examples/sleep_scale.csv")
    ds = select_items(cols, items=cfg.get("items"), id_cols=cfg.get("id_cols") or [])
    return analyze(listwise(ds), extraction=extraction, parallel_iter=20)


def test_html_omega_label_follows_extraction():
    """PCA 적재 기반 ω를 'McDonald ω'라고 부르면 안 된다(텍스트 보고서와 동일 규칙)."""
    pca = render_html(_res_from_example("pca"))
    assert "McDonald" not in pca
    assert "PCA 근사" in pca
    ml = render_html(_res_from_example("ml"))
    assert "McDonald ω" in ml


def test_html_residual_row_is_not_blank():
    """잔차 요약이 라벨만 있고 값이 빈 채로 나가지 않는다(키 이름 오타 회귀)."""
    html = render_html(_res_from_example("pca"))
    import re
    m = re.search(r"잔차 RMSR = ([0-9.]+) · \|잔차\|&gt;([0-9.]+) 비율 ([0-9.]+)", html)
    assert m, html[html.find("잔차") - 50:html.find("잔차") + 200]
    assert float(m.group(3)) >= 0.0
    assert "비율 </p>" not in html


def test_html_rmsea_carries_ci_and_verdict():
    """RMSEA 점추정만 싣지 않는다 — 90% CI와 판정 문구가 텍스트와 같아야 한다."""
    res = _res_from_example("ml")
    html = render_html(res)
    text = render(res)
    from factorscan.report import _rmsea_verdict
    verdict = _rmsea_verdict(res["fit"])
    assert "90% CI" in html
    assert verdict in html
    assert verdict in text            # 두 출력이 같은 판정을 쓴다


def test_readme_no_longer_claims_pairwise_unsupported():
    """문서가 '결측은 listwise만 지원'이라고 말하면서 --missing pairwise를 제공하면 안 된다."""
    readme = open("README.md", encoding="utf-8").read()
    assert "pairwise 미지원" not in readme
    assert "--missing pairwise" in readme


# ============================================================================
# round10 독립 리뷰에서 확인된 결함의 회귀 테스트
# ============================================================================

def test_fit_indices_refuse_when_bartlett_multiplier_nonpositive():
    """표본이 너무 작아 χ² 보정계수가 0 이하면 '완벽한 적합'을 만들어 내면 안 된다.

    예전에는 chi = max(mult, 0)·F 로 χ²=0 이 되어 RMSEA=0·CI[0,0]·PCLOSE=1·CFI=1 이
    나왔고, 같은 표에서 TLI가 −4.05로 자기모순이었다.
    """
    fit = efa.fit_indices(10.09, n=6, p=8, k=3, null_chi_square=50.0)
    assert fit["identified"] is False
    assert fit["chi_square"] is None and fit["rmsea"] is None and fit["cfi"] is None
    assert "보정계수" in fit["unidentified_reason"]
    # 정상 표본에서는 그대로 계산된다
    ok = efa.fit_indices(0.05, n=300, p=8, k=2, null_chi_square=900.0)
    assert ok["identified"] is True and ok["chi_square"] > 0


def test_bartlett_refuses_negative_chi_square():
    """보정계수가 음수면 χ²가 음수로 나오고 p가 1.0으로 잘려 '근거 약함'으로 오독된다."""
    rng = np.random.default_rng(3)
    z = rng.normal(size=(400, 12))
    r = np.corrcoef(z, rowvar=False)
    with pytest.raises(ValueError, match="보정계수"):
        efa.bartlett_sphericity(r, 5)
    b = efa.bartlett_sphericity(r, 400)
    assert b.chi_square > 0


def test_pairwise_does_not_report_ml_fit_indices():
    """χ² ∝ (N−1) 이므로 최솟값 N을 넣으면 기각될 모형이 완벽해 보인다 → 아예 내지 않는다."""
    res = analyze(_prep(_sim(n=300, p=8, miss=0.12, seed=47)),
                  parallel_iter=0, extraction="ml", missing="pairwise")
    assert res["fit"] is None
    assert res["fit_scan"] is None
    assert any("적합도지수" in n and "단일한 N" in n for n in res["notes"])
    assert res["residual"]["rmsr"] >= 0          # 잔차 RMSR은 계속 제공된다
    assert len(res["loadings"]) == 8             # 적재량도 그대로
    # listwise 에서는 그대로 나온다
    ls = analyze(_prep(_sim(n=300, p=8, miss=0.12, seed=47)),
                 parallel_iter=0, extraction="ml")
    assert ls["fit"] is not None and ls["fit"]["identified"]


def test_pairwise_fit_scan_is_refused_not_silently_ignored():
    res = analyze(_prep(_sim(n=300, p=8, miss=0.12, seed=51)),
                  parallel_iter=0, extraction="ml", fit_scan=True, missing="pairwise")
    assert res["fit_scan"] is None
    assert any("--fit-scan" in w for w in res["warnings"])


def test_smoothing_suppresses_bartlett_kmo_and_map():
    """중복 문항 때문에 고유값이 0이면, 보정 하한이 Bartlett χ²의 99%를 만들어 낸다.

    보정은 적재량 추출을 위해서만 쓰고, |R|·R⁻¹에 기대는 추론은 생략해야 한다.
    """
    rng = np.random.default_rng(63)
    n, p = 400, 5
    f = rng.normal(size=n)
    x = np.array([0.8 * f + 0.6 * rng.normal(size=n) for _ in range(p)]).T
    x = np.column_stack([x, x[:, 0]])        # Q6 = Q1 완전 중복
    x[3, 2] = np.nan                          # 산발 결측 하나 → 쌍별 경로 진입
    res = analyze(_prep(x), parallel_iter=0, missing="pairwise")
    pw = res["pairwise"]
    assert pw["smoothed"] is True
    assert pw["n_eigenvalues_clipped"] >= 1
    assert pw["min_eigenvalue_before"] < 1e-6
    assert res["bartlett"] is None and res["kmo"] is None and res["map_k"] is None
    assert any("KMO·Bartlett·ML 적합도는 생략" in w for w in res["warnings"])
    # 적재량·고유값은 계속 제공된다(보정된 행렬 기준)
    assert len(res["eigenvalues"]) == 6 and len(res["loadings"]) == 6


def test_polychoric_does_not_fill_unestimable_pairs_with_zero():
    """범주가 하나뿐인 문항은 r=0('무상관')으로 메우지 않고 원인을 짚어 거절한다."""
    rng = np.random.default_rng(67)
    n = 200
    f = rng.normal(size=n)
    x = np.array([np.clip(np.round(0.85 * f + 0.55 * rng.normal(size=n) + 3), 1, 5)
                  for _ in range(4)], dtype=float).T
    vas = 0.6 + 0.8 * rng.random(n)           # 반올림하면 전부 1 → 범주 1개
    x = np.column_stack([x, vas])
    with pytest.raises(ValueError, match="상관을 추정할 수 없는"):
        analyze(_prep(x), parallel_iter=0, correlation="polychoric")


def test_polychoric_rejects_int64_overflow_values():
    """1e19 같은 값은 int64로 포화해 단일 범주가 되고 예전에는 r=0으로 메워졌다."""
    rng = np.random.default_rng(71)
    n = 120
    f = rng.normal(size=n)
    x = np.array([np.clip(np.round(0.85 * f + 0.55 * rng.normal(size=n) + 3), 1, 5)
                  for _ in range(4)], dtype=float).T
    x = np.column_stack([x, x[:, 0] * 1e19])
    with pytest.raises(ValueError, match=r"2\^53"):
        analyze(_prep(x), parallel_iter=0, correlation="polychoric")


def test_low_response_item_is_never_dropped_silently():
    """응답률이 낮아 자동선택에서 빠진 열은 반드시 사유와 함께 보고된다."""
    from factorscan.dataio import select_items
    n = 200
    col = np.array([str(1 + (i % 5)) for i in range(n)], dtype=object)
    sparse = np.array([("" if i % 3 else str(1 + (i % 4))) for i in range(n)], dtype=object)
    cols = {"Q1": col, "Q2": np.array([str(1 + (i % 4)) for i in range(n)], dtype=object),
            "Q3": sparse}
    ds = select_items(cols)
    assert "Q3" not in ds.names
    assert "Q3" in ds.dropped and "응답률" in ds.dropped["Q3"]
    # pairwise 용 문턱을 낮추면 살아난다
    ds2 = select_items(cols, min_finite_prop=0.05)
    assert "Q3" in ds2.names


def test_cli_pairwise_keeps_low_response_item(tmp_path, capsys):
    """--missing pairwise 는 '응답률 낮은 문항을 살리려는' 옵션이므로 실제로 살려야 한다."""
    n = 200
    rng = np.random.default_rng(73)
    f = rng.normal(size=n)
    base = [np.clip(np.round(0.85 * f + 0.55 * rng.normal(size=n) + 3), 1, 5)
            for _ in range(4)]
    branch = np.clip(np.round(0.9 * f + 0.4 * rng.normal(size=n) + 3), 1, 5)
    drop = set(rng.choice(n, size=118, replace=False).tolist())
    rows = [[int(c[i]) for c in base] + ["" if i in drop else int(branch[i])]
            for i in range(n)]
    path = _write_csv(tmp_path / "b.csv", ["Q1", "Q2", "Q3", "Q4", "Q5분기"], rows)

    assert run([path, "--parallel-iter", "0", "--json"]) == 0
    listwise_out = json.loads(capsys.readouterr().out)
    assert "Q5분기" not in listwise_out["items"]
    assert "Q5분기" in listwise_out["dropped_columns"]       # 조용히 사라지지 않는다

    assert run([path, "--missing", "pairwise", "--parallel-iter", "0", "--json"]) == 0
    pw_out = json.loads(capsys.readouterr().out)
    assert "Q5분기" in pw_out["items"]


def test_pairwise_still_warns_about_small_complete_case_sample():
    """쌍별로 바꿨다고 α·부트스트랩의 작은 표본 문제가 사라지는 건 아니다."""
    res = analyze(_prep(_sim(n=400, p=30, miss=0.10, seed=79)),
                  parallel_iter=0, missing="pairwise")
    assert res["pairwise"]["n_min"] >= 5 * 30          # 상관행렬 표본은 충분
    assert res["n_used"] < 5 * 30                      # 완전응답자는 부족
    assert any("완전응답자가 적습니다" in w for w in res["warnings"])


def test_empty_output_path_is_an_error_not_a_silent_noop(tmp_path, capsys):
    x = _sim(seed=83, miss=0.0)
    rows = [[int(v) for v in row] for row in x]
    path = _write_csv(tmp_path / "s.csv", [f"Q{i+1}" for i in range(x.shape[1])], rows)
    for flag in ("--html-out", "--csv-out", "--eigen-out", "--scores-out"):
        assert run([path, flag, "", "--parallel-iter", "0"]) == 2
        assert "빈 경로" in capsys.readouterr().err
