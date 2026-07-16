"""1차 하드닝에서 추가된 견고성·기능·회귀 테스트.

- 인코딩(CP949) / 자릿수구분 쉼표 / 헤더보다 긴 행 / 비숫자 강제결측 카운트
- 설정 파일 구조 검증(scale_range/최상위 dict/문자열 items)
- 인자 검증(--min-loading, --scale-min/max 순서)
- 평행분석: n<=p 생략, 첫 교차 규칙, 키 항상 존재
- 신규 통계: 요인별 문항-총점 / McDonald ω / RMSR — 독립 계산과 대조
- report에 MSA·ω·RMSR·PCA 표기가 실제로 나타나는지
"""
import json
import os

import numpy as np
import pytest

from factorscan import efa
from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import (DataError, Dataset, apply_reverse, listwise,
                               load_csv, select_items)
from factorscan.report import render

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


# ---------------- 인코딩 ----------------
def test_cp949_file_gives_friendly_error(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text("문항1,문항2\n1,2\n3,4\n5,1\n", encoding="cp949")
    with pytest.raises(DataError, match="cp949|CP949|EUC-KR"):
        load_csv(str(p))  # 기본 utf-8-sig 로는 못 읽음 → 안내 포함 오류


def test_cp949_reads_with_encoding(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text("문항1,문항2\n1,2\n3,4\n", encoding="cp949")
    cols = load_csv(str(p), encoding="cp949")
    assert "문항1" in cols and "문항2" in cols


def test_unknown_encoding_reports_clean(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text("Q1,Q2\n1,2\n", encoding="utf-8")
    with pytest.raises(DataError, match="알 수 없는 인코딩"):
        load_csv(str(p), encoding="definitely-not-an-encoding")


# ---------------- 자릿수구분 쉼표 ----------------
def test_thousands_separator_unambiguous_parsed():
    # 소수점이 있거나(그룹+소수) 그룹이 2개 이상이면 자릿수구분이 명확 → 파싱.
    from factorscan.dataio import _to_float
    assert _to_float("1,000.5", []) == 1000.5
    assert _to_float("12,345.60", []) == 12345.60
    assert _to_float("1,000,000", []) == 1000000.0
    assert _to_float("-2,500,000", []) == -2500000.0


def test_thousands_separator_ambiguous_surfaced_not_silently_wrong(tmp_path):
    # 단일 그룹·소수 없음("1,000")은 유럽식 "1,234"=1.234 와 구분 불가 → 조용히 1000배로
    # 오독하지 않고 결측(강제변환 경고)으로 남긴다.
    from factorscan.dataio import _to_float
    assert np.isnan(_to_float("1,000", []))   # 모호 → NaN(경고로 표면화)
    assert np.isnan(_to_float("1,234", []))
    p = tmp_path / "d.csv"
    p.write_text('Q1,Q2\n"1,000",2\n"2,500",4\n1200,3\n1300,5\n', encoding="utf-8")
    cols = load_csv(str(p))
    ds = select_items(cols, items=["Q1", "Q2"])
    assert ds.coercion.get("Q1") == 2   # "1,000","2,500" 이 강제변환 경고로 잡힘


# ---------------- 헤더보다 긴/짧은 행 ----------------
def test_row_longer_than_header_errors(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Q1,Q2,Q3\n1,2,3\n4,5,6,999\n", encoding="utf-8")
    with pytest.raises(DataError, match="열 정렬|열 개수"):
        load_csv(str(p))


def test_trailing_empty_cells_beyond_header_tolerated(tmp_path):
    # 헤더보다 길지만 넘치는 칸이 전부 빈칸이면(엑셀 흔한 케이스) 무해하게 잘라낸다.
    p = tmp_path / "d.csv"
    p.write_text("Q1,Q2\n1,2,\n3,4,\n5,1,\n", encoding="utf-8")
    cols = load_csv(str(p))
    assert list(cols.keys()) == ["Q1", "Q2"]
    assert cols["Q1"].shape == (3,)


# ---------------- 비숫자 강제결측 카운트 ----------------
def test_coercion_count_surfaced(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Q1,Q2,Q3\n1,2,3\nabc,4,5\n3,xx,4\n4,5,6\n5,1,2\n", encoding="utf-8")
    cols = load_csv(str(p))
    ds = select_items(cols, items=["Q1", "Q2", "Q3"])
    assert ds.coercion == {"Q1": 1, "Q2": 1}   # 'abc'와 'xx'만; 진짜 NA는 제외
    prep = listwise(ds)
    res = analyze(prep, parallel_iter=0)
    assert any("결측처리된 값" in w for w in res["warnings"])


def test_na_tokens_not_counted_as_coercion(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Q1,Q2\nNA,2\n.,4\n3,5\n4,1\n", encoding="utf-8")
    cols = load_csv(str(p))
    ds = select_items(cols, items=["Q1", "Q2"])
    assert ds.coercion == {}   # NA/. 는 정상 결측


# ---------------- 설정 파일 구조 검증(크래시 대신 rc=2) ----------------
def _write(tmp_path, name, text, enc="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=enc)
    return str(p)


def test_config_bad_scale_range_len(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '{"scale_range":[1,5,9]}')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2
    assert "scale_range" in capsys.readouterr().err


def test_config_scale_range_non_numeric(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '{"scale_range":"15"}')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2


def test_config_not_a_dict(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '["Q1","Q2"]')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2
    assert "객체" in capsys.readouterr().err


def test_config_string_items_split(tmp_path):
    # 설정의 items 가 문자열이면 쉼표분리(문자단위 순회 방지).
    from factorscan.cli import _cfg_list
    assert _cfg_list({"items": "Q1,Q2,Q3"}, "items") == ["Q1", "Q2", "Q3"]
    assert _cfg_list({"items": ["Q1", "Q2"]}, "items") == ["Q1", "Q2"]
    assert _cfg_list({}, "items") == []


# ---------------- 인자 검증 ----------------
def test_min_loading_out_of_range_rc2(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    rc = run([csv, "--min-loading", "-0.5", "--parallel-iter", "0"])
    assert rc == 2
    assert "min-loading" in capsys.readouterr().err


def test_scale_min_ge_max_rc2(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    rc = run([csv, "--reverse", "Q1", "--scale-min", "5", "--scale-max", "1"])
    assert rc == 2
    assert "scale-min" in capsys.readouterr().err


# ---------------- 평행분석 견고성 ----------------
def test_parallel_skipped_when_n_le_p():
    rng = np.random.default_rng(0)
    x = rng.standard_normal((6, 6))       # n == p
    prep = _prep_from_matrix([f"Q{i}" for i in range(6)], x)
    res = analyze(prep, parallel_iter=50)
    assert res["parallel_k"] is None
    assert res["parallel_eigenvalues"] is None
    assert any("평행분석 생략" in w for w in res["warnings"])
    assert res["k_source"] == "kaiser"


def test_parallel_keys_always_present_even_when_disabled():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    assert "parallel_eigenvalues" in res and res["parallel_eigenvalues"] is None
    assert "parallel_k" in res and res["parallel_k"] is None


def test_retained_by_parallel_first_crossing():
    # 비단조 상황: 뒤에서 다시 커져도 첫 교차에서 멈춘다.
    obs = np.array([3.0, 0.9, 1.2, 0.5])
    ref = np.array([1.5, 1.0, 1.0, 0.8])
    assert efa.retained_by_parallel(obs, ref) == 1
    # 단조 감소(정상 상관행렬)에서는 합산 규칙과 동일
    obs2 = np.array([3.0, 1.4, 0.9, 0.6])
    ref2 = np.array([1.3, 1.1, 1.0, 0.8])
    assert efa.retained_by_parallel(obs2, ref2) == 2
    # 전부 초과
    assert efa.retained_by_parallel(np.array([2.0, 1.5]), np.array([1.0, 1.0])) == 2
    # 전부 미달
    assert efa.retained_by_parallel(np.array([0.5, 0.4]), np.array([1.0, 1.0])) == 0


# ---------------- 신규 통계: 요인별 문항-총점 ----------------
def test_item_total_by_factor_matches_manual():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    x = prep.matrix
    L = np.array(res["loadings"])
    groups = np.argmax(np.abs(L), axis=1)
    it = np.array(res["item_total_by_factor"])
    for i in range(x.shape[1]):
        mates = [c for c in range(x.shape[1]) if groups[c] == groups[i] and c != i]
        rest = x[:, mates].sum(axis=1)
        assert it[i] == pytest.approx(np.corrcoef(x[:, i], rest)[0, 1], abs=1e-9)


def test_item_total_by_factor_singleton_is_nan():
    # 요인 내 문항이 1개면 비교 대상이 없어 NaN.
    x = np.array([[1., 2, 9], [2, 3, 1], [3, 4, 8], [4, 5, 2], [5, 1, 7]])
    it = efa.corrected_item_total_by_group(x, [0, 0, 1])
    assert np.isnan(it[2])
    assert np.all(np.isfinite(it[:2]))


def test_item_total_by_factor_equals_overall_when_k1():
    prep = _two_factor_data()
    res = analyze(prep, n_factors=1, parallel_iter=0)
    assert res["item_total_by_factor"] == pytest.approx(res["item_total_overall"], nan_ok=True)


# ---------------- 신규 통계: McDonald ω ----------------
def test_omega_hand_computed_literal():
    # 독립 수기값과 대조(구현식 재실행이 아님). 단일 요인 3문항, 각 적재 0.7.
    # h(자기요인)=0.7 → ω = (2.1)² / [(2.1)² + 3·(1−0.49)] = 4.41 / 5.94 = 0.742424…
    L = np.array([[0.7], [0.7], [0.7]])
    om = efa.omega_by_group(L, [0, 0, 0])
    assert om[0] == pytest.approx(0.7424242424, abs=1e-9)


def test_omega_uses_own_factor_residual():
    # 분자·분모 모두 자기 요인 적재 기준(자기요인 오차분산 Σ(1−λ²)).
    L = np.array([[0.8, 0.1], [0.7, 0.0], [0.1, 0.75], [0.0, 0.7]])
    groups = np.argmax(np.abs(L), axis=1)   # [0,0,1,1]
    om = efa.omega_by_group(L, groups)
    for f, idx in {0: [0, 1], 1: [2, 3]}.items():
        lam = L[idx, f]
        num = lam.sum() ** 2
        den = num + (1 - lam ** 2).sum()
        assert om[f] == pytest.approx(num / den)


def test_omega_none_for_small_factor():
    # 문항 1개 요인은 정의 불가(None). 2개 이상이면 값 존재.
    L = np.array([[0.9, 0.0], [0.1, 0.8], [0.0, 0.7]])
    groups = np.argmax(np.abs(L), axis=1)   # [0,1,1]
    om = efa.omega_by_group(L, groups)
    assert om[0] is None        # F0 = 문항 1개
    assert om[1] is not None     # F1 = 문항 2개


def test_omega_high_for_clean_two_factor():
    prep = _two_factor_data(n=300)
    res = analyze(prep, parallel_iter=0)
    L = np.array(res["loadings"])
    groups = np.argmax(np.abs(L), axis=1).tolist()
    # 깔끔한 2요인 구조가 3+3으로 갈렸는지 먼저 확인(실패 시 진단 명확)
    assert sorted(groups) == [0, 0, 0, 1, 1, 1]
    assert all(o is not None and 0.6 < o <= 1.0 for o in res["omega"])


# ---------------- 신규 통계: RMSR/잔차 ----------------
def test_residual_stats_perfect_fit_is_zero():
    # 적재가 상관을 완벽히 재현하면(R = LL^T) 잔차 0.
    L = np.array([[0.8, 0.1], [0.6, 0.2], [0.1, 0.7], [0.2, 0.6]])
    r = L @ L.T
    st = efa.residual_stats(r, L)
    assert st["rmsr"] == pytest.approx(0.0, abs=1e-12)
    assert st["n_large"] == 0
    assert st["n_resid"] == 6   # 4*3/2 비대각


def test_residual_stats_in_result_and_bounds():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    st = res["residual"]
    assert 0.0 <= st["rmsr"] < 1.0
    assert 0.0 <= st["prop_large"] <= 1.0
    assert st["n_resid"] == 6 * 5 // 2


# ---------------- 설명분산 비율 합 ----------------
def test_prop_variance_sums_to_one():
    rng = np.random.default_rng(7)
    x = rng.standard_normal((200, 6))
    r = np.corrcoef(x, rowvar=False)
    eig = efa.eigen_summary(r)
    assert eig.prop_variance.sum() == pytest.approx(1.0, abs=1e-12)
    assert eig.cum_variance[-1] == pytest.approx(1.0, abs=1e-12)


# ---------------- report: 신규 항목 표시 ----------------
def test_report_shows_msa_omega_rmsr_pca():
    prep = _two_factor_data()
    out = render(analyze(prep, parallel_iter=0))
    assert "MSA" in out
    assert "RMSR" in out
    assert "PCA" in out
    # PCA 적재 기반 ω는 McDonald's ω가 아니므로 그 이름을 쓰면 안 된다(라벨이 복사된다).
    assert "McDonald ω" not in out
    assert "ω, PCA 근사" in out


def test_report_msa_dash_when_singular():
    # n<p → KMO 없음 → MSA 열이 '—' 로 나오되 정렬 유지, 죽지 않음.
    rng = np.random.default_rng(0)
    x = rng.standard_normal((5, 7))
    prep = _prep_from_matrix([f"Q{i}" for i in range(7)], x)
    out = render(analyze(prep, parallel_iter=0))
    assert "계산 불가" in out          # KMO/Bartlett 생략 렌더 경로
    assert "MSA" in out


# ---------------- 결정성(재현성) 전체 파이프라인 ----------------
def test_full_pipeline_deterministic_json():
    def once():
        cols = load_csv(os.path.abspath(EXAMPLE))
        ds = select_items(cols, id_cols=["ID"])
        res = analyze(listwise(ds), parallel_iter=30, seed=123)
        out = dict(res)
        out.pop("correlation_matrix", None)
        from factorscan.cli import _sanitize
        return json.dumps(_sanitize(out), ensure_ascii=False, sort_keys=True)
    assert once() == once()


# ---------------- 중복/완전상관 열 → 비양정부호 경로 ----------------
def test_duplicate_columns_non_positive_definite():
    rng = np.random.default_rng(1)
    base = rng.standard_normal((60, 3))
    x = np.column_stack([base, base[:, 0]])   # Q4 == Q1 (완전상관)
    prep = _prep_from_matrix(["Q1", "Q2", "Q3", "Q4"], x)
    res = analyze(prep, parallel_iter=0)
    assert res["kmo"] is None and res["bartlett"] is None
    assert any("정부호" in w or "특이" in w for w in res["warnings"])


# ---------------- CLI: 신규 JSON 키/스키마 ----------------
def test_cli_json_has_new_keys(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    for key in ("omega", "residual", "item_total_by_factor", "item_total_overall",
                "extraction", "parallel_eigenvalues", "parallel_k"):
        assert key in d
    assert d["extraction"] == "principal_component"
    assert d["parallel_eigenvalues"] is None   # 끈 경우 null(키 존재)


def test_cli_encoding_flag_cp949(tmp_path, capsys):
    p = tmp_path / "k.csv"
    rng = np.random.default_rng(0)
    lines = ["문항1,문항2,문항3,문항4"]
    for _ in range(40):
        lines.append(",".join(map(str, rng.integers(1, 6, 4))))
    p.write_text("\n".join(lines) + "\n", encoding="cp949")
    rc = run([str(p), "--encoding", "cp949", "--parallel-iter", "0"])
    assert rc == 0
    assert "KMO" in capsys.readouterr().out


# ---------------- Round 2 후속: 미보강 경로 회귀 가드 ----------------
def test_config_scale_range_min_ge_max_via_config(tmp_path, capsys):
    # scale_min>=max 검증이 CLI 플래그뿐 아니라 config 경로에서도 걸려야 한다.
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '{"scale_range":[5,1],"reverse":["Q1"]}')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2
    assert "scale-min" in capsys.readouterr().err


def test_config_scale_range_nonfinite_rejected(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '{"scale_range":[NaN,5]}')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2
    assert "scale_range" in capsys.readouterr().err


def test_config_items_as_dict_rc2(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    cfg = _write(tmp_path, "c.json", '{"items":{"a":1}}')
    rc = run([csv, "--config", cfg, "--parallel-iter", "0"])
    assert rc == 2
    assert "items" in capsys.readouterr().err


def test_cli_coercion_warning_end_to_end_json(tmp_path, capsys):
    # 강제변환 경고가 CLI --json 출력까지 살아있는지(analyze 단위가 아니라).
    csv = _write(tmp_path, "d.csv",
                 "Q1,Q2,Q3\n1,2,3\nabc,4,5\n3,2,4\n4,5,6\n5,1,2\n2,3,4\n")
    rc = run([csv, "--items", "Q1,Q2,Q3", "--parallel-iter", "0", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any("결측처리된 값" in w for w in data["warnings"])


def test_cli_wrong_encoding_clean_error(tmp_path, capsys):
    # 잘못된 인코딩으로 CLI 실행 시 트레이스백이 아니라 깔끔한 오류 + rc=1.
    p = tmp_path / "k.csv"
    p.write_text("문항1,문항2\n1,2\n3,4\n5,1\n", encoding="cp949")
    rc = run([str(p), "--encoding", "utf-8", "--parallel-iter", "0"])
    assert rc == 1
    assert "읽을 수 없습니다" in capsys.readouterr().err


def test_na_flag_excluded_from_coercion(tmp_path):
    # --na 로 지정한 토큰은 강제변환(오류) 카운트에 들어가면 안 된다.
    csv = _write(tmp_path, "d.csv", "Q1,Q2\nX,2\nY,4\n3,5\n4,1\n")
    cols = load_csv(csv)
    ds = select_items(cols, items=["Q1", "Q2"], na_values=["X", "Y"])
    assert ds.coercion == {}


def test_report_omega_dash_for_empty_factor():
    # 어떤 요인에도 argmax 배정 문항이 없으면 ω 표기가 '—' 이고 죽지 않아야 한다.
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    # 인위적으로 3요인 자리로 만들되 F3에는 아무 문항도 배정되지 않게(loadings 2열만 의미)
    res["n_factors"] = 2
    res["omega"] = [0.9, None]
    out = render(res)
    assert "F2=—" in out


def test_duplicate_items_rejected(tmp_path):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n")
    cols = load_csv(csv)
    with pytest.raises(DataError, match="중복 지정"):
        select_items(cols, items=["Q1", "Q1", "Q2"])


def test_rotation_none_multifactor_note():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, n_factors=2, rotation="none")
    assert res["rotation"] == "none"
    assert any("비회전" in n for n in res["notes"])


def test_id_col_not_found_warns(tmp_path, capsys):
    csv = _write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n")
    rc = run([csv, "--id-col", "없는열", "--parallel-iter", "0"])
    assert rc == 0
    assert "id-col" in capsys.readouterr().err


# ---------------- Round 3: Promax 사교회전 · 요인상관 · 행렬식 ----------------
def test_promax_phi_valid_correlation():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    phi = np.array(res["factor_correlation"])
    assert phi.shape == (2, 2)
    assert np.allclose(np.diag(phi), 1.0)          # 대각 1
    assert np.allclose(phi, phi.T)                  # 대칭
    assert np.all(np.abs(phi) <= 1.0 + 1e-9)        # |r| <= 1


def test_promax_communalities_rotation_invariant():
    # 공통성은 회전 불변 → promax(diag PΦPᵀ)가 varimax와 (거의) 동일해야 한다.
    prep = _two_factor_data()
    v = analyze(prep, parallel_iter=0, n_factors=2, rotation="varimax")
    pmx = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    assert pmx["communalities"] == pytest.approx(v["communalities"], abs=1e-6)


def test_promax_rmsr_uses_phi():
    # 사교회전 RMSR은 R̂=PΦPᵀ 로 계산해야 하며, PPᵀ(잘못된) 값과 달라야 한다.
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    r = np.array(res["correlation_matrix"])
    P = np.array(res["loadings"])
    phi = np.array(res["factor_correlation"])
    iu = np.triu_indices(P.shape[0], 1)
    manual = np.sqrt((((r - P @ phi @ P.T)[iu]) ** 2).mean())
    assert res["residual"]["rmsr"] == pytest.approx(manual, abs=1e-9)
    wrong = np.sqrt((((r - P @ P.T)[iu]) ** 2).mean())
    assert abs(res["residual"]["rmsr"] - wrong) > 1e-3   # PPᵀ와는 확실히 다름


def test_factor_correlation_estimated_under_varimax():
    # 직교(varimax) 회전이어도 요인상관 추정치를 제공하고, 크면 사교회전 권고.
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, n_factors=2, rotation="varimax")
    assert res["factor_correlation"] is not None
    assert res["factor_correlation_max"] is not None


def test_factor_correlation_none_for_single_factor():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, n_factors=1)
    assert res["factor_correlation"] is None


def test_promax_matches_reference_algorithm():
    # 독립 구현(직접 Hendrickson-White 절차)과 대조.
    rng = np.random.default_rng(3)
    f = rng.standard_normal((300, 2))
    load = np.array([[0.8, 0.1], [0.75, 0.0], [0.1, 0.8], [0.0, 0.75]])
    x = f @ load.T + 0.4 * rng.standard_normal((300, 4))
    r = np.corrcoef(x, rowvar=False)
    raw = efa.component_loadings(r, 2)
    P, phi = efa.promax(raw, power=4)
    # 독립 재계산(Kaiser 정규화 → 목표/최소제곱 → 정규화 복원)
    V = efa.varimax(raw)
    h = np.sqrt((V ** 2).sum(axis=1))
    Vn = V / h[:, None]
    tgt = Vn * np.abs(Vn) ** 3
    coef = np.linalg.lstsq(Vn, tgt, rcond=None)[0]
    coef = coef @ np.diag(np.sqrt(np.diag(np.linalg.inv(coef.T @ coef))))
    P2 = (Vn @ coef) * h[:, None]
    cinv = np.linalg.inv(coef)
    phi2 = cinv @ cinv.T
    assert np.allclose(P, P2, atol=1e-10)
    assert np.allclose(phi, phi2, atol=1e-10)


def test_promax_k1_returns_identity_phi():
    L = efa.component_loadings(np.corrcoef(np.random.default_rng(0).standard_normal((50, 3)), rowvar=False), 1)
    P, phi = efa.promax(L)
    assert phi.shape == (1, 1) and phi[0, 0] == 1.0
    assert np.allclose(P, L)


def test_determinant_present_and_multicollinearity_warn():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    assert res["r_determinant"] > 0
    # 거의 중복인 문항 → 행렬식 매우 작음(다중공선성) 경고
    rng = np.random.default_rng(1)
    base = rng.standard_normal((200, 3))
    x = np.column_stack([base, base[:, 0] + 0.001 * rng.standard_normal(200)])
    prep2 = _prep_from_matrix(["Q1", "Q2", "Q3", "Q4"], x)
    res2 = analyze(prep2, parallel_iter=0)
    assert 0.0 < res2["r_determinant"] < 1e-5
    assert any("행렬식" in w or "다중공선성" in w for w in res2["warnings"])


def test_promax_omega_bounded_under_correlated_factors():
    # 요인이 강하게 상관되어 패턴계수가 커져도 ω는 구조행렬 기준이라 [0,1] 유계.
    rng = np.random.default_rng(7)
    f0 = rng.standard_normal((400, 1))
    f1 = 0.9 * f0 + np.sqrt(1 - 0.81) * rng.standard_normal((400, 1))
    x = np.hstack([f0 * 0.9 + 0.3 * rng.standard_normal((400, 1)),
                   f0 * 0.85 + 0.3 * rng.standard_normal((400, 1)),
                   f1 * 0.9 + 0.3 * rng.standard_normal((400, 1)),
                   f1 * 0.85 + 0.3 * rng.standard_normal((400, 1))])
    prep = _prep_from_matrix(["Q1", "Q2", "Q3", "Q4"], x)
    res = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    assert abs(res["factor_correlation"][0][1]) > 0.5   # 실제로 강하게 상관됨
    assert all(o is not None and 0.0 <= o <= 1.0 for o in res["omega"])


def test_promax_and_varimax_omega_equal_when_orthogonal():
    # 요인이 사실상 무상관이면 promax≈varimax → ω도 거의 같아야 한다.
    prep = _two_factor_data(seed=5, n=200)
    v = analyze(prep, parallel_iter=0, n_factors=2, rotation="varimax")
    pmx = analyze(prep, parallel_iter=0, n_factors=2, rotation="promax")
    assert abs(pmx["factor_correlation"][0][1]) < 0.35
    for a, b in zip(v["omega"], pmx["omega"]):
        assert abs(a - b) < 0.05


def test_cli_promax_json_has_factor_correlation(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--rotation", "promax", "--parallel-iter", "0", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["rotation"] == "promax"
    assert d["factor_correlation"] is not None
    assert "r_determinant" in d


def test_report_shows_factor_correlation():
    prep = _two_factor_data()
    out = render(analyze(prep, parallel_iter=0, n_factors=2, rotation="promax"))
    assert "요인 간 상관행렬" in out
    assert "Promax 사교회전" in out


def test_readme_example_output_matches(capsys):
    # README '실제 출력 예시'의 핵심 수치가 실제 렌더와 일치(문서 표류 방지 골든 가드).
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "20"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "문항 수: 8    응답자: 77명 사용 (전체 80, 결측제거 3)" in out
    assert "χ²(28) = 335.49" in out
    assert "KMO 전체: 0.813" in out
    assert "F1=0.911  F2=0.911" in out          # McDonald ω
    assert "Cronbach α, 응답분산기반·추출방식 무관): F1=0.881  F2=0.880" in out
    assert "Velicer MAP 기준(최소평균편상관): 2개 요인" in out
    assert "RMSR=0.068" in out
    assert "14/28 = 50%" in out
