"""analyze 파이프라인 + CLI 통합/엣지케이스 검증."""
import json
import os

import numpy as np
import pytest

from factorscan.analyze import analyze
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise, load_csv, select_items

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


def test_default_prefers_parallel_over_kaiser():
    # 잡음 데이터: Kaiser는 과대추정, 평행분석은 보수적. 기본값은 평행분석을 따라야 한다(P1).
    rng = np.random.default_rng(9)
    x = rng.standard_normal((150, 10))
    prep = _prep_from_matrix([f"Q{i}" for i in range(10)], x)
    res = analyze(prep, parallel_iter=50, seed=1)
    assert res["k_source"] == "parallel"
    # 최소 1개 요인은 유지(floor). 잡음이면 평행분석은 0을 제안하지만 1로 내려앉는다.
    assert res["n_factors"] == max(1, res["parallel_k"])
    # Kaiser는 잡음에서 과대추정 → 평행분석보다 크고, 불일치 안내가 있어야 한다.
    assert res["kaiser_k"] > res["parallel_k"]
    assert any("불일치" in n for n in res["notes"])


def test_disagreement_note_reports_applied_k_not_zero():
    # 평행분석이 0을 제안해 1로 내려앉는 경우, 안내문은 '적용=0'이 아니라 '1개 적용'이어야 한다(P1).
    rng = np.random.default_rng(9)
    x = rng.standard_normal((150, 10))
    prep = _prep_from_matrix([f"Q{i}" for i in range(10)], x)
    res = analyze(prep, parallel_iter=50, seed=1)
    note = next(n for n in res["notes"] if "불일치" in n)
    assert f"{res['n_factors']}개를 적용" in note
    assert "0개를 적용" not in note


def test_no_disagreement_note_when_user_overrides():
    # 사용자가 --n-factors 로 직접 지정하면 불일치 안내를 내지 않는다(P1).
    rng = np.random.default_rng(9)
    x = rng.standard_normal((150, 10))
    prep = _prep_from_matrix([f"Q{i}" for i in range(10)], x)
    res = analyze(prep, n_factors=3, parallel_iter=50, seed=1)
    assert res["k_source"] == "user"
    assert not any("불일치" in n for n in res["notes"])


def test_min_loading_threads_into_report():
    # report의 별표(*) 기준이 --min-loading 을 따른다(P3).
    from factorscan.report import render
    prep = _two_factor_data()
    out_low = render(analyze(prep, parallel_iter=0, min_loading=0.40))
    out_high = render(analyze(prep, parallel_iter=0, min_loading=0.99))
    assert "|0.40|" in out_low and "|0.99|" in out_high
    # 임계값이 높아지면 주요 적재(별표)가 줄어든다 → report가 min_loading을 반영
    assert out_high.count("*") < out_low.count("*")


def test_report_display_width_alignment():
    # 한글(전각) 문항명이 있어도 적재량 표의 각 데이터행 폭이 일정하게 정렬돼야 한다.
    from factorscan.report import _dwidth, _pad, _truncate, render
    assert _dwidth("문항") == 4          # 전각 2 x 2
    assert _dwidth("Q1") == 2
    assert _dwidth(_pad("문항", 18)) == 18
    assert _dwidth(_truncate("가" * 20, 18)) == 18   # '..' 포함 18

    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    new_names = ["수면잠들기어려움문항", "짧다", "Q3", "혼합Mixed문항", "다섯", "여섯"]
    res["items"] = new_names
    for f, nm in zip(res["item_flags"], new_names):
        f["item"] = nm
    out = render(res)
    # 데이터 행(→F 로 끝나는 줄)들의 표시 폭이 모두 동일
    widths = {_dwidth(ln) for ln in out.splitlines() if ln.strip().endswith(("→F1", "→F2"))}
    assert len(widths) == 1


def test_parallel_disabled_falls_back_to_kaiser():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    assert res["k_source"] == "kaiser"


def test_zero_variance_after_listwise_errors():
    # 결측 제거 후 상수가 된 열은 명확히 오류(NaN 상관행렬 오염 방지).
    mat = [[1., 2., 5.], [2., 4., 5.], [3., 5., 5.], [np.nan, 1., 2.]]
    prep = _prep_from_matrix(["Q1", "Q2", "Q3"], mat)
    with pytest.raises(ValueError, match="모두 동일"):
        analyze(prep, parallel_iter=0)


def test_analyze_recovers_two_factors():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=30, seed=1)
    assert res["kaiser_k"] == 2
    assert res["n_factors"] == 2
    assert res["parallel_k"] == 2
    assert res["kmo"]["overall"] > 0.6
    assert res["bartlett"]["p_value"] < 1e-6
    # Q1-Q3 vs Q4-Q6 이 서로 다른 요인에 주적재
    load = np.array(res["loadings"])
    g1 = {int(np.argmax(np.abs(load[i]))) for i in range(3)}
    g2 = {int(np.argmax(np.abs(load[i]))) for i in range(3, 6)}
    assert len(g1) == 1 and len(g2) == 1 and g1 != g2


def test_analyze_communalities_bounded():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0)
    comm = np.array(res["communalities"])
    assert np.all(comm >= -1e-9) and np.all(comm <= 1.0 + 1e-9)


def test_ss_loadings_sum_preserved_under_rotation():
    prep = _two_factor_data()
    res = analyze(prep, parallel_iter=0, rotation="varimax")
    # 회전 후 요인별 SS 합 == 유지된 요인들의 고유값 합
    total_ss = sum(res["ss_loadings"])
    top_eig = sum(res["eigenvalues"][: res["n_factors"]])
    assert total_ss == pytest.approx(top_eig, abs=1e-6)


def test_analyze_requires_two_items():
    prep = _prep_from_matrix(["Q1"], [[1.], [2.], [3.]])
    with pytest.raises(ValueError, match="2개 이상"):
        analyze(prep, parallel_iter=0)


def test_analyze_too_few_respondents():
    prep = _prep_from_matrix(["Q1", "Q2"], [[1., 2.], [2., 3.]])
    with pytest.raises(ValueError, match="너무 적"):
        analyze(prep, parallel_iter=0)


def test_analyze_singular_matrix_warns_not_crash():
    # n < p → 특이 상관행렬. KMO/Bartlett 생략하되 죽지 않아야 함.
    rng = np.random.default_rng(0)
    x = rng.standard_normal((4, 6))
    prep = _prep_from_matrix([f"Q{i}" for i in range(6)], x)
    res = analyze(prep, parallel_iter=0)
    assert res["kmo"] is None
    assert res["bartlett"] is None
    assert any("정부호" in w or "특이" in w for w in res["warnings"])
    # 고유값은 여전히 계산됨
    assert len(res["eigenvalues"]) == 6


def test_user_n_factors_out_of_range():
    prep = _two_factor_data()
    with pytest.raises(ValueError, match="범위"):
        analyze(prep, n_factors=99, parallel_iter=0)


# ---------- CLI ----------
def test_cli_on_bundled_example(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "20"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "factorscan" in out
    assert "KMO 전체" in out
    assert "요인 적재량" in out


def test_cli_json_output(capsys):
    rc = run([os.path.abspath(EXAMPLE), "--config", os.path.abspath(CONFIG),
              "--parallel-iter", "0", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_items"] == 8
    assert data["n_factors"] == 2
    assert len(data["loadings"]) == 8
    assert "correlation_matrix" in data


def test_cli_missing_file():
    rc = run(["/nonexistent/path/does_not_exist.csv"])
    assert rc == 1


def test_cli_json_is_valid_when_nan_present(capsys):
    from factorscan.cli import _sanitize
    # _sanitize 는 NaN/Inf 를 None 으로 바꿔 JSON 유효성을 보장
    cleaned = _sanitize({"a": float("nan"), "b": [1.0, float("inf")], "c": 2.0})
    assert cleaned == {"a": None, "b": [1.0, None], "c": 2.0}
    json.dumps(cleaned, allow_nan=False)   # 예외 없이 직렬화


def test_cli_reverse_out_of_range_warns_but_runs(tmp_path, capsys):
    # 범위 밖 값이 있어도 실행은 되지만 경고를 출력해야 한다(P4).
    rng = np.random.default_rng(2)
    lines = ["Q1,Q2,Q3,Q4"]
    for _ in range(40):
        vals = rng.integers(1, 6, 4)
        lines.append(",".join(map(str, vals)))
    lines[1] = "9,1,1,1"   # 범위(1~5) 밖 값 주입
    p = tmp_path / "d.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = run([str(p), "--reverse", "Q1", "--scale-min", "1", "--scale-max", "5",
              "--parallel-iter", "0"])
    assert rc == 0
    assert "범위" in capsys.readouterr().err


def test_cli_reverse_without_scale(tmp_path, capsys):
    p = tmp_path / "d.csv"
    p.write_text("Q1,Q2,Q3\n1,2,3\n4,3,2\n2,5,1\n5,1,4\n", encoding="utf-8")
    rc = run([str(p), "--reverse", "Q1"])
    assert rc == 2
    assert "scale" in capsys.readouterr().err.lower()
