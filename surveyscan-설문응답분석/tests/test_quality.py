"""응답 품질(부주의응답) 선별 모듈 + --quality CLI 경로 테스트.

quality.py 는 구현되어 있었지만 CLI 플래그도 리포트 렌더링도 없어 사용자가
전혀 볼 수 없었다(전량 dead code). 다시 그렇게 되지 않도록 여기서 고정한다.
"""
import contextlib
import io

import pytest

from surveyscan.analyze import analyze
from surveyscan.cli import run
from surveyscan.config import SurveyConfig
from surveyscan.dataio import SurveyData
from surveyscan.quality import duplicate_ids, longstring, respondent_quality
from surveyscan.report import render, render_markdown


# ---------- longstring ----------

def test_longstring_counts_consecutive_run():
    assert longstring([1, 1, 1, 2, 2]) == 3
    assert longstring([1, 2, 3, 4]) == 1
    assert longstring([]) == 0


def test_longstring_missing_breaks_the_run():
    # 결측은 '같은 값'이 아니므로 연속을 끊는다: 1,1 / 1,1 -> 최대 2
    assert longstring([1, 1, None, 1, 1]) == 2


def test_longstring_all_missing_is_zero():
    assert longstring([None, None, None]) == 0


def test_longstring_zero_values_counted():
    # 0.0 은 falsy — truthiness 로 판단하면 여기서 깨진다(ISI 전원 0 사례)
    assert longstring([0.0, 0.0, 0.0, 0.0]) == 4


# ---------- respondent_quality ----------

def _data(rows_vals, ids=None):
    cols = [f"Q{i+1}" for i in range(len(rows_vals[0]))]
    rows = [{c: v for c, v in zip(cols, vals)} for vals in rows_vals]
    kw = {}
    if ids is not None:
        kw = {"id_columns": ["ID"], "id_values": [{"ID": i} for i in ids]}
    return SurveyData(cols, rows, **kw), cols


def test_straightline_detected_and_irv_zero():
    data, cols = _data([[3, 3, 3, 3], [1, 2, 3, 4]])
    q = respondent_quality(data, cols)
    r0, r1 = q["respondents"]
    assert r0["straightline"] is True and r0["irv"] == 0.0 and r0["flagged"] is True
    assert r1["straightline"] is False and r1["flagged"] is False
    assert q["n_straightline"] == 1
    assert q["n_flagged"] == 1


def test_two_identical_answers_is_not_straightline():
    # 답한 문항이 3개 미만이면 straightline 으로 보지 않는다(경계: n_ans >= 3)
    data, cols = _data([[2, 2, None, None]])
    q = respondent_quality(data, cols)
    assert q["respondents"][0]["straightline"] is False


def test_three_identical_answers_is_straightline():
    data, cols = _data([[2, 2, 2, None]])
    q = respondent_quality(data, cols)
    assert q["respondents"][0]["straightline"] is True


def test_longstring_min_threshold_is_respected():
    data, cols = _data([[1, 1, 1, 2, 5, 4]])  # longstring = 3
    assert respondent_quality(data, cols, longstring_min=3)["n_flagged"] == 1
    assert respondent_quality(data, cols, longstring_min=4)["n_flagged"] == 0


def test_default_longstring_min_heuristic():
    # k=10 -> max(3, ceil(10/2)) = 5
    data, cols = _data([[1] * 10])
    assert respondent_quality(data, cols)["longstring_min"] == 5
    # k=4 -> max(3, 2) = 3
    data4, cols4 = _data([[1, 2, 3, 4]])
    assert respondent_quality(data4, cols4)["longstring_min"] == 3


def test_missing_pct_and_high_missing_count():
    data, cols = _data([[1, None, None, None], [1, 2, 3, 4]])
    q = respondent_quality(data, cols)
    assert q["respondents"][0]["n_missing"] == 3
    assert q["respondents"][0]["missing_pct"] == 75.0
    assert q["n_high_missing"] == 1  # 절반 초과 무응답은 1명뿐


def test_quality_rows_carry_row_number_and_ids():
    data, cols = _data([[1, 1, 1, 1], [1, 2, 3, 4]], ids=["A01", "A02"])
    q = respondent_quality(data, cols)
    assert q["respondents"][0]["row"] == 1  # 1-기반(헤더 제외)
    assert q["respondents"][0]["ids"] == {"ID": "A01"}
    assert q["respondents"][1]["row"] == 2


# ---------- duplicate_ids ----------

def test_duplicate_ids_groups_rows():
    data, _ = _data([[1, 2], [1, 2], [3, 4]], ids=["P1", "P1", "P2"])
    dups = duplicate_ids(data)
    assert len(dups) == 1
    assert dups[0]["id"] == "P1"
    assert dups[0]["rows"] == [1, 2]
    assert dups[0]["count"] == 2


def test_blank_ids_are_not_duplicates():
    data, _ = _data([[1, 2], [1, 2], [3, 4]], ids=["", "", "P2"])
    assert duplicate_ids(data) == []


def test_no_id_columns_yields_no_duplicates():
    data, _ = _data([[1, 2], [1, 2]])
    assert duplicate_ids(data) == []


# ---------- analyze()/report 통합 ----------

def test_analyze_quality_off_by_default():
    data, cols = _data([[1, 1, 1, 1]])
    res = analyze(data, SurveyConfig(subscales={"S": cols}))
    assert res["quality"] is None
    assert "응답 품질" not in render(res)
    assert "응답 품질" not in render_markdown(res)


def test_analyze_quality_on_renders_in_both_formats():
    data, cols = _data([[3, 3, 3, 3], [1, 2, 3, 4], [0, 1, 2, 1]], ids=["A", "B", "C"])
    res = analyze(data, SurveyConfig(subscales={"S": cols}), quality_check=True)
    assert res["quality"]["n_flagged"] == 1
    for out in (render(res), render_markdown(res)):
        assert "응답 품질" in out
        assert "전부 동일값" in out
        assert "A" in out
        # 과잉해석 경고문이 반드시 함께 나가야 한다
        assert "자동 제외 기준이" in out


def test_analyze_quality_uses_all_config_items_including_reverse_raw():
    """품질 지표는 역코딩 '전' 원자료 기준이어야 한다."""
    cols = ["A", "B", "C"]
    rows = [{"A": 1.0, "B": 1.0, "C": 1.0}]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(
        subscales={"S": cols}, reverse_items=["C"], scale_min=0, scale_max=4
    )
    res = analyze(data, cfg, quality_check=True)
    r = res["quality"]["respondents"][0]
    # 역코딩 후라면 C=3 이 되어 longstring 이 2로 떨어진다 -> 3 이어야 raw 기준
    assert r["longstring"] == 3
    assert r["straightline"] is True


# ---------- CLI ----------

def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run(argv)
    return rc, out.getvalue(), err.getvalue()


def _csv(tmp_path, text, name="d.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_quality_flag_produces_section(tmp_path):
    path = _csv(tmp_path, "ID,Q1,Q2,Q3,Q4\nP1,3,3,3,3\nP2,1,2,3,4\nP3,0,1,2,1\n")
    rc, out, _ = _run([path, "--id-col", "ID", "--quality"])
    assert rc == 0
    assert "응답 품질" in out
    assert "P1" in out


def test_cli_without_quality_flag_has_no_section(tmp_path):
    path = _csv(tmp_path, "ID,Q1,Q2,Q3,Q4\nP1,3,3,3,3\nP2,1,2,3,4\nP3,0,1,2,1\n")
    rc, out, _ = _run([path, "--id-col", "ID"])
    assert rc == 0
    assert "응답 품질" not in out


def test_cli_longstring_min_override(tmp_path):
    path = _csv(tmp_path, "Q1,Q2,Q3,Q4\n1,1,1,2\n1,2,3,4\n0,1,2,1\n")
    _, out3, _ = _run([path, "--quality", "--longstring-min", "3"])
    assert "플래그 1명" in out3
    _, out4, _ = _run([path, "--quality", "--longstring-min", "4"])
    assert "플래그 0명" in out4


def test_cli_longstring_min_rejects_too_small(tmp_path):
    path = _csv(tmp_path, "Q1,Q2\n1,2\n3,4\n")
    rc, _, err = _run([path, "--quality", "--longstring-min", "1"])
    assert rc == 2
    assert "longstring-min" in err


def test_cli_longstring_min_without_quality_warns(tmp_path):
    path = _csv(tmp_path, "Q1,Q2\n1,2\n3,4\n")
    rc, _, err = _run([path, "--longstring-min", "3"])
    assert rc == 0
    assert "경고" in err and "--quality" in err


def test_cli_quality_json_includes_quality_block(tmp_path):
    import json
    path = _csv(tmp_path, "ID,Q1,Q2,Q3,Q4\nP1,3,3,3,3\nP2,1,2,3,4\nP3,0,1,2,1\n")
    rc, out, _ = _run([path, "--id-col", "ID", "--quality", "--json"])
    assert rc == 0
    q = json.loads(out)["quality"]
    assert q["n_flagged"] == 1
    assert len(q["respondents"]) == 3
