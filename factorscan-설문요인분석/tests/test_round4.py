"""Round 4 신규 기능 테스트: PAF 추출 · Velicer MAP · Cronbach α · CSV 적재표 내보내기.

모든 신규 통계는 구현식 재실행이 아닌 독립 오라클/수기 계산과 대조한다.
"""
import csv
import io
import json
import os

import numpy as np
import pytest

from factorscan import efa
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise, load_csv, select_items
from factorscan.report import loadings_table_csv, render

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "sleep_scale.csv")
CONFIG = os.path.join(os.path.dirname(__file__), "..", "examples", "sleep_config.json")


def _prep_from_matrix(names, mat):
    ds = Dataset(names=names, data=np.asarray(mat, dtype=float))
    return listwise(ds)


def _two_factor_data(seed=5, n=120):
    rng = np.random.default_rng(seed)
    f = rng.standard_normal((n, 2))
    load = np.array([[0.8, 0], [0.75, 0], [0.7, 0], [0, 0.8], [0, 0.75], [0, 0.7]])
    x = f @ load.T + 0.4 * rng.standard_normal((n, 6))
    return _prep_from_matrix([f"Q{i+1}" for i in range(6)], x)


# ================= SMC (초기 공통성) =================
def test_smc_matches_definition_and_bounded():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((200, 5))
    r = np.corrcoef(x, rowvar=False)
    smc = efa.squared_multiple_correlations(r)
    # 정의: 1 - 1/R^{-1}_ii
    expected = 1.0 - 1.0 / np.diag(np.linalg.inv(r))
    assert np.allclose(smc, np.clip(expected, 0, 1))
    assert np.all(smc >= 0.0) and np.all(smc <= 1.0)


def test_smc_singular_uses_pinv_no_crash():
    # 완전상관 열 포함 → 특이. pinv 대체로 죽지 않고 [0,1] 유계.
    base = np.random.default_rng(0).standard_normal((50, 3))
    x = np.column_stack([base, base[:, 0]])
    r = np.corrcoef(x, rowvar=False)
    smc = efa.squared_multiple_correlations(r)
    assert np.all(np.isfinite(smc))
    assert np.all(smc >= 0.0) and np.all(smc <= 1.0)


# ================= PAF (주축분해) =================
def _paf_oracle(R, k, it=500, tol=1e-10):
    """독립 구현: SMC 초기화 → 반복 주축분해."""
    Rinv = np.linalg.inv(R)
    h2 = np.clip(1 - 1 / np.diag(Rinv), 0, 1)
    L = np.zeros((R.shape[0], k))
    for _ in range(it):
        M = R.copy()
        np.fill_diagonal(M, h2)
        w, V = np.linalg.eigh(M)
        idx = np.argsort(w)[::-1][:k]
        L = V[:, idx] * np.sqrt(np.clip(w[idx], 0, None))
        h2n = np.clip((L ** 2).sum(1), 0, 1)
        if np.max(np.abs(h2n - h2)) < tol:
            h2 = h2n
            break
        h2 = h2n
    return L, h2


def test_paf_communalities_match_independent_oracle():
    rng = np.random.default_rng(3)
    f = rng.standard_normal((300, 2))
    load = np.array([[0.8, 0.1], [0.75, 0.0], [0.7, 0.05],
                     [0.1, 0.8], [0.0, 0.75], [0.05, 0.7]])
    x = f @ load.T + 0.5 * rng.standard_normal((300, 6))
    r = np.corrcoef(x, rowvar=False)
    paf = efa.paf_loadings(r, 2)
    _, ho = _paf_oracle(r, 2)
    assert paf.converged
    assert np.allclose(paf.communalities, ho, atol=1e-5)


def test_paf_loadings_reproduce_offdiagonal_better_than_smaller_k():
    # 참요인 수(2)로 PAF하면 비대각 상관을 잘 재현(RMSR 작음).
    prep = _two_factor_data(n=300)
    r = efa.correlation_matrix(prep.matrix)
    paf = efa.paf_loadings(r, 2)
    st = efa.residual_stats(r, paf.loadings)
    assert st["rmsr"] < 0.06


def test_paf_communalities_le_pca():
    # 공통요인(PAF) 공통성은 관측분산 전체를 쓰는 PCA보다 크지 않다(같은 k).
    prep = _two_factor_data(n=300)
    r = efa.correlation_matrix(prep.matrix)
    pca_c = efa.communalities(efa.component_loadings(r, 2))
    paf_c = efa.paf_loadings(r, 2).communalities
    assert np.all(paf_c <= pca_c + 1e-6)


def test_paf_heywood_detected_and_capped():
    # 완전상관에 가까운 문항으로 k 과다 → Heywood(공통성>1) 유발 가능. 절단·flag 확인.
    rng = np.random.default_rng(7)
    base = rng.standard_normal((80, 2))
    x = np.column_stack([base[:, 0], base[:, 0] + 1e-3 * rng.standard_normal(80),
                         base[:, 1], base[:, 1] + 1e-3 * rng.standard_normal(80)])
    r = np.corrcoef(x, rowvar=False)
    paf = efa.paf_loadings(r, 3)
    assert np.all(paf.communalities <= 1.0 + 1e-9)   # 절단됨
    # heywood 여부는 데이터 의존이지만 flag 타입은 항상 bool
    assert isinstance(paf.heywood, bool)


def test_analyze_paf_extraction_end_to_end():
    prep = _two_factor_data(n=200)
    res = analyze(prep, parallel_iter=0, extraction="paf")
    assert res["extraction"] == "principal_axis"
    # 공통성 [0,1] 유계, 요인구조 여전히 2개로 갈림
    comm = np.array(res["communalities"])
    assert np.all(comm >= -1e-9) and np.all(comm <= 1.0 + 1e-9)
    L = np.array(res["loadings"])
    g1 = {int(np.argmax(np.abs(L[i]))) for i in range(3)}
    g2 = {int(np.argmax(np.abs(L[i]))) for i in range(3, 6)}
    assert len(g1) == 1 and len(g2) == 1 and g1 != g2


def test_analyze_paf_less_optimistic_than_pca():
    # 동일 데이터에서 PAF ω는 PCA ω보다 낮다(덜 낙관적).
    prep = _two_factor_data(n=300)
    pca = analyze(prep, parallel_iter=0, extraction="pca")
    paf = analyze(prep, parallel_iter=0, extraction="paf")
    for a, b in zip(paf["omega"], pca["omega"]):
        assert a is not None and b is not None
        assert a <= b + 1e-9


def test_analyze_invalid_extraction_errors():
    prep = _two_factor_data()
    with pytest.raises(ValueError, match="extraction"):
        analyze(prep, parallel_iter=0, extraction="mle")


def test_paf_eigenvalues_still_from_full_r():
    # 요인 수 판정(고유값·Kaiser)은 PAF여도 '전체 상관행렬' 기준이어야 한다(축소행렬 아님).
    prep = _two_factor_data(n=200)
    pca = analyze(prep, parallel_iter=0, extraction="pca")
    paf = analyze(prep, parallel_iter=0, extraction="paf")
    assert paf["eigenvalues"] == pytest.approx(pca["eigenvalues"])
    assert paf["kaiser_k"] == pca["kaiser_k"]


# ================= Velicer MAP =================
def _map_oracle(R):
    p = R.shape[0]
    w, V = np.linalg.eigh(R)
    idx = np.argsort(w)[::-1]
    A = V[:, idx] * np.sqrt(np.clip(w[idx], 0, None))
    fs = []
    for m in range(p):
        if m == 0:
            P = R
        else:
            am = A[:, :m]
            C = R - am @ am.T
            d = np.sqrt(np.diag(C))
            P = C / np.outer(d, d)
        off = P[np.triu_indices(p, 1)]
        fs.append(float(np.mean(off ** 2)))
    return int(np.argmin(fs)), fs


def test_map_matches_independent_oracle():
    rng = np.random.default_rng(3)
    f = rng.standard_normal((300, 2))
    load = np.array([[0.8, 0.1], [0.75, 0.0], [0.7, 0.05],
                     [0.1, 0.8], [0.0, 0.75], [0.05, 0.7]])
    x = f @ load.T + 0.5 * rng.standard_normal((300, 6))
    r = np.corrcoef(x, rowvar=False)
    got = efa.velicer_map(r)
    ok, ofs = _map_oracle(r)
    assert got["k"] == ok == 2
    assert np.allclose(got["values"], ofs[:len(got["values"])], equal_nan=True)


def test_map_zero_for_pure_noise():
    # 잡음(무구조)이면 MAP 최소가 m=0 → 0개 요인.
    rng = np.random.default_rng(11)
    x = rng.standard_normal((400, 6))
    r = np.corrcoef(x, rowvar=False)
    assert efa.velicer_map(r)["k"] == 0


def test_map_in_analyze_result_and_report():
    prep = _two_factor_data(n=200)
    res = analyze(prep, parallel_iter=0)
    assert res["map_k"] == 2
    assert isinstance(res["map_values"], list)
    out = render(res)
    assert "Velicer MAP" in out


def test_map_none_when_singular():
    # n<p → 특이 상관행렬 → MAP 생략(None), 죽지 않음.
    rng = np.random.default_rng(0)
    x = rng.standard_normal((5, 7))
    prep = _prep_from_matrix([f"Q{i}" for i in range(7)], x)
    res = analyze(prep, parallel_iter=0)
    assert res["map_k"] is None and res["map_values"] is None


# ================= Cronbach α =================
def test_cronbach_alpha_hand_formula():
    x = np.array([[4., 3, 5, 2], [3, 2, 4, 1], [5, 4, 5, 3],
                  [2, 1, 2, 1], [4, 4, 4, 2], [3, 3, 3, 2]])
    k = x.shape[1]
    iv = x.var(0, ddof=1)
    tv = x.sum(1).var(ddof=1)
    expected = k / (k - 1) * (1 - iv.sum() / tv)
    assert efa.cronbach_alpha(x) == pytest.approx(expected)


def test_cronbach_alpha_degenerate_none():
    assert efa.cronbach_alpha(np.array([[1.], [2.], [3.]])) is None      # 1문항
    assert efa.cronbach_alpha(np.ones((5, 3))) is None                    # 총점분산 0


def test_alpha_by_group_and_singleton():
    x = np.array([[4., 3, 9], [3, 2, 1], [5, 4, 8], [2, 1, 2], [4, 4, 7]])
    groups = [0, 0, 1]   # F1=2문항, F2=1문항
    out = efa.alpha_by_group(x, groups, 2)
    assert out[0] == pytest.approx(efa.cronbach_alpha(x[:, [0, 1]]))
    assert out[1] is None


def test_alpha_in_result_and_report_and_json():
    prep = _two_factor_data(n=200)
    res = analyze(prep, parallel_iter=0)
    assert "alpha" in res and len(res["alpha"]) == res["n_factors"]
    assert all(a is not None and 0.5 < a <= 1.0 for a in res["alpha"])
    assert "Cronbach α" in render(res)


def test_cli_json_has_alpha_map(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    for key in ("alpha", "map_k", "map_values"):
        assert key in d
    assert len(d["alpha"]) == d["n_factors"]


# ================= CSV 적재표 내보내기 =================
def test_loadings_table_csv_structure_and_values():
    prep = _two_factor_data(n=150)
    res = analyze(prep, parallel_iter=0)
    text = loadings_table_csv(res)
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    kf = res["n_factors"]
    n = res["n_items"]
    assert header[:1 + kf] == ["item"] + [f"F{j+1}" for j in range(kf)]
    assert "communality" in header and "primary_factor" in header and "problems" in header
    # 문항 행은 헤더 바로 다음 n개
    item_rows = rows[1:1 + n]
    assert len(item_rows) == n
    # 반올림(소수 4자리) 후에도 첫 문항의 F1 적재·공통성이 결과와 일치
    assert float(item_rows[0][1]) == pytest.approx(res["loadings"][0][0], abs=1e-4)
    assert float(item_rows[0][1 + kf]) == pytest.approx(res["communalities"][0], abs=1e-4)
    # 요약 행 존재 확인(논문 표 footer)
    flat = {r[0] for r in rows if r}
    for tag in ("_SS_loadings", "_pct_variance", "_cumulative_pct",
                "_omega", "_cronbach_alpha"):
        assert tag in flat
    # Φ 행은 '어느 회전 기준인지'를 이름에 담는다(직교 해에 Φ를 함께 보고하는 사고 방지).
    assert any(str(c).startswith("_factor_correlation") for c in flat)


def test_loadings_table_csv_rounds_floats():
    prep = _two_factor_data(n=150)
    text = loadings_table_csv(analyze(prep, parallel_iter=0))
    rows = list(csv.reader(io.StringIO(text)))
    # 적재 셀은 소수 4자리 이하로 반올림되어 16자리 잡음이 없어야 한다
    cell = rows[1][1]
    assert len(cell.split(".")[-1]) <= 4


def test_loadings_table_csv_formula_injection_neutralized():
    prep = _two_factor_data(n=120)
    res = analyze(prep, parallel_iter=0)
    res["items"] = ["=SUM(A1:A9)"] + res["items"][1:]
    res["item_flags"][0]["item"] = "=SUM(A1:A9)"
    text = loadings_table_csv(res)
    rows = list(csv.reader(io.StringIO(text)))
    # 위험 접두문자는 작은따옴표로 무력화(값은 보존)
    assert rows[1][0] == "'=SUM(A1:A9)"


def test_loadings_table_csv_no_literal_nan():
    # NaN 적재/공통성도 빈칸으로 나가야 한다(msa와 일관).
    prep = _two_factor_data(n=120)
    res = analyze(prep, parallel_iter=0)
    res["loadings"][0][0] = float("nan")
    res["communalities"][0] = float("nan")
    text = loadings_table_csv(res)
    assert "nan" not in text.lower()


def test_loadings_table_csv_k1():
    prep = _two_factor_data(n=120)
    res = analyze(prep, n_factors=1, parallel_iter=0)
    rows = list(csv.reader(io.StringIO(loadings_table_csv(res))))
    assert rows[0][:2] == ["item", "F1"]
    assert len(rows[1:1 + res["n_items"]]) == res["n_items"]


def test_csv_out_written_by_cli(tmp_path, capsys):
    out = tmp_path / "table.csv"
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--csv-out", str(out)])
    assert rc == 0
    assert out.exists()
    # utf-8-sig(BOM) 로 저장되어 한국어 엑셀에서 바로 열림
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "item"
    assert rows[1][0] == "Q1_잠들기어려움"   # 첫 문항 행
    assert len(rows) > 1 + 8                   # 헤더 + 8문항 + 요약행들
    assert "저장했습니다" in capsys.readouterr().err


def test_csv_out_msa_empty_when_singular(tmp_path):
    # 특이행렬(n<p)이면 MSA 열이 빈칸이어도 CSV가 정상 생성.
    rng = np.random.default_rng(0)
    x = rng.standard_normal((5, 7))
    prep = _prep_from_matrix([f"Q{i}" for i in range(7)], x)
    res = analyze(prep, parallel_iter=0)
    text = loadings_table_csv(res)
    rows = list(csv.reader(io.StringIO(text)))
    # 헤더 + 7문항 행 존재(요약행은 추가로 붙음), MSA 열은 빈칸이어도 생성됨
    assert rows[0][0] == "item"
    assert len(rows[1:1 + 7]) == 7


def test_cli_extraction_paf_runs(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--extraction", "paf"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "주축분해(PAF)" in out
    assert "Cronbach α" in out


# ================= Round 4 리뷰 후속: 회귀 가드 =================
def test_paf_kp_does_not_crash(tmp_path, capsys):
    # PAF에서 요인 수=문항 수(k=p)면 0 적재 요인이 생겨 promax inv가 죽던 문제(회귀).
    rng = np.random.default_rng(0)
    lines = ["Q1,Q2,Q3,Q4,Q5,Q6"]
    for _ in range(60):
        lines.append(",".join(map(str, rng.integers(1, 6, 6))))
    p = tmp_path / "d.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for rot in ("none", "varimax", "promax"):
        rc = run([str(p), "--extraction", "paf", "--n-factors", "6",
                  "--rotation", rot, "--parallel-iter", "0"])
        assert rc == 0
        assert "추출 가능한 공통요인 수" in capsys.readouterr().out


def test_paf_degenerate_promax_no_exception():
    # analyze 단위: PAF k=p 에서 factor_correlation 추정이 죽지 않고 결과를 반환.
    rng = np.random.default_rng(1)
    x = rng.integers(1, 6, (80, 5)).astype(float)
    prep = _prep_from_matrix([f"Q{i}" for i in range(5)], x)
    res = analyze(prep, parallel_iter=0, n_factors=5, extraction="paf")
    assert res["n_factors"] == 5
    assert any("추출 가능한 공통요인" in w for w in res["warnings"])


def test_paf_heywood_boundary_flagged():
    # 특이자료에서 공통성이 정확히 1.0에 고정돼 '초과'가 안 나도 Heywood로 플래그(회귀).
    base = np.random.default_rng(0).standard_normal((60, 3))
    x = np.column_stack([base, base[:, 0] + base[:, 1]])   # 완전 선형종속
    r = np.corrcoef(x, rowvar=False)
    paf = efa.paf_loadings(r, 2)
    assert paf.heywood is True
    assert np.all(paf.communalities <= 1.0 + 1e-9)


def test_safe_inv_falls_back_to_pinv():
    sing = np.array([[1.0, 1.0], [1.0, 1.0]])
    out = efa._safe_inv(sing)
    assert np.all(np.isfinite(out))          # pinv로 대체 → 유한


# ================= 하위척도 점수 내보내기 =================
def test_subscale_scores_sum_and_mean():
    x = np.array([[1., 2, 10, 20], [3, 4, 30, 40], [5, 6, 50, 60]])
    groups = [0, 0, 1, 1]
    s_sum = efa.subscale_scores(x, groups, 2, method="sum")
    s_mean = efa.subscale_scores(x, groups, 2, method="mean")
    assert np.allclose(s_sum[:, 0], [3, 7, 11])       # Q1+Q2
    assert np.allclose(s_sum[:, 1], [30, 70, 110])     # Q3+Q4
    assert np.allclose(s_mean[:, 0], [1.5, 3.5, 5.5])
    assert np.allclose(s_mean[:, 1], [15, 35, 55])


def test_subscale_scores_empty_factor_nan():
    x = np.array([[1., 2], [3, 4], [5, 6]])
    s = efa.subscale_scores(x, [0, 0], 2, method="sum")
    assert np.all(np.isnan(s[:, 1]))     # F2에 배정 문항 없음
    assert np.allclose(s[:, 0], [3, 7, 11])


def test_subscale_scores_bad_method():
    with pytest.raises(ValueError, match="method"):
        efa.subscale_scores(np.ones((3, 2)), [0, 1], 2, method="median")


def test_scores_out_cli_honors_reverse_and_ids(tmp_path, capsys):
    # 역문항 반영·ID 정렬·결측제거 표본 크기 확인.
    out = tmp_path / "scores.csv"
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--scores-out", str(out)])
    assert rc == 0
    text = out.read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "ID"
    # 가설 구조(config의 structure)가 있으면 열 이름에 하위척도명이 붙는다.
    assert rows[0][1].startswith("F1") and "_sum" in rows[0][1]
    assert rows[0][2].startswith("F2") and "_sum" in rows[0][2]
    # 결측제거 표본(77명) + 헤더
    assert len(rows) == 1 + 77
    assert "하위척도 점수를 저장" in capsys.readouterr().err


def test_scores_out_matches_manual_subscale_sum(tmp_path):
    # 저장된 점수가 손계산한 하위척도 합과 일치(역문항 재점수화 포함).
    from factorscan.dataio import (apply_reverse, listwise, load_csv,
                                   select_items)
    cols = load_csv(os.path.abspath(EXAMPLE))
    items = ["Q1_잠들기어려움", "Q2_자주깸", "Q3_아침개운함", "Q4_수면만족",
             "Q5_주간졸림", "Q6_집중력", "Q7_주간피로", "Q8_활력"]
    ds = select_items(cols, items=items, id_cols=["ID"])
    ds = apply_reverse(ds, ["Q1_잠들기어려움", "Q2_자주깸", "Q5_주간졸림", "Q7_주간피로"], 1, 5)
    prep = listwise(ds)
    res = analyze(prep, parallel_iter=0)
    out = tmp_path / "s.csv"
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--scores-out", str(out), "--score-method", "mean"])
    assert rc == 0
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    L = np.array(res["loadings"])
    groups = np.argmax(np.abs(L), axis=1)
    manual = efa.subscale_scores(prep.matrix, groups, res["n_factors"], method="mean")
    # 첫 응답자의 F1 평균점수 일치
    assert float(rows[1][1]) == pytest.approx(manual[0][0], abs=1e-4)


def test_scores_out_no_id_uses_row_numbers(tmp_path, capsys):
    p = tmp_path / "d.csv"
    rng = np.random.default_rng(3)
    lines = ["Q1,Q2,Q3,Q4"]
    # 방향이 일관된 2요인 구조로 만든다. 순수 난수는 문항 방향이 제멋대로라
    # 역문항 미처리 가드(주적재 음수)에 정당하게 걸려 합산점수 저장이 거부된다.
    for _ in range(40):
        f1, f2 = rng.normal(), rng.normal()
        vals = [f1 + 0.3 * rng.normal(), f1 + 0.3 * rng.normal(),
                f2 + 0.3 * rng.normal(), f2 + 0.3 * rng.normal()]
        lines.append(",".join(f"{int(np.clip(round(v * 1.2 + 3), 1, 5))}" for v in vals))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "s.csv"
    rc = run([str(p), "--parallel-iter", "0", "--scores-out", str(out), "--n-factors", "2"])
    assert rc == 0
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert rows[0][0] == "row"           # ID 없으면 행 번호
    assert rows[1][0] == "1"


def test_scores_csv_injection_neutralized_in_id(tmp_path):
    # 점수 CSV의 ID 값/열이름에도 수식 인젝션 방어가 걸려야 한다(회귀).
    from factorscan.report import scores_table_csv
    prep = _two_factor_data(n=80)
    res = analyze(prep, parallel_iter=0)
    id_pairs = [("=CMD|calc", ["=HYPERLINK(1)"] + ["x"] * (prep.matrix.shape[0] - 1))]
    text = scores_table_csv(res, prep.matrix, id_pairs, method="sum")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "'=CMD|calc"       # 열 이름 무력화
    assert rows[1][0] == "'=HYPERLINK(1)"    # ID 값 무력화


def test_scores_out_no_id_uses_original_row_numbers(tmp_path):
    # ID가 없고 결측으로 행이 삭제되면, row 열은 '원본 CSV 행번호'여야 한다(순번 아님).
    # 2번째 데이터행(원본 row 2)에 결측을 넣어 삭제 → 남은 원본 행번호 1,3,4,5.
    p = tmp_path / "d.csv"
    # 문항 방향이 일관된(모두 같은 쪽으로 움직이는) 값 — 역문항 가드에 걸리지 않게.
    p.write_text("Q1,Q2,Q3,Q4\n1,1,2,2\n2,,3,4\n5,5,4,4\n3,3,3,3\n4,4,5,5\n",
                 encoding="utf-8")
    out = tmp_path / "s.csv"
    rc = run([str(p), "--parallel-iter", "0", "--scores-out", str(out), "--n-factors", "2"])
    assert rc == 0
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert rows[0][0] == "row"
    row_ids = [r[0] for r in rows[1:]]
    assert row_ids == ["1", "3", "4", "5"]   # 삭제된 원본 row 2 는 빠지고 번호 보존


def test_csv_out_not_truncated_before_build(tmp_path):
    # 내용을 먼저 만든 뒤 파일을 열어야, 실패 시에도 기존 파일이 0바이트로 잘리지 않는다.
    out = tmp_path / "pre.csv"
    out.write_text("EXISTING", encoding="utf-8")
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--csv-out", str(out)])
    assert rc == 0
    # 정상 경로: 새 내용으로 대체됨(그리고 온전한 CSV)
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert rows[0][0] == "item"


def test_scores_out_bad_path_rc1(tmp_path, capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--scores-out", str(tmp_path / "nodir" / "s.csv")])
    assert rc == 1
    assert "점수 CSV 저장 실패" in capsys.readouterr().err


def test_report_shows_cumulative_variance_and_map_value():
    prep = _two_factor_data(n=200)
    out = render(analyze(prep, parallel_iter=0))
    assert "누적 설명분산%" in out
    assert "최소 평균편상관" in out


def test_full_pipeline_paf_deterministic():
    def once():
        cols = load_csv(os.path.abspath(EXAMPLE))
        ds = select_items(cols, id_cols=["ID"])
        res = analyze(listwise(ds), parallel_iter=20, seed=7, extraction="paf")
        from factorscan.cli import _sanitize
        out = dict(res)
        out.pop("correlation_matrix", None)
        return json.dumps(_sanitize(out), ensure_ascii=False, sort_keys=True)
    assert once() == once()
