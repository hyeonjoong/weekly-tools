"""적대적 리뷰에서 발견된 결함에 대한 회귀 테스트."""
import pytest

from surveyscan.analyze import analyze
from surveyscan.config import SurveyConfig
from surveyscan.dataio import SurveyData, load_csv


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_all_zero_column_not_dropped(tmp_path):
    # P1: 전부 0인 문항(예: ISI 전원 '0=문제없음')이 제외되면 안 됨
    path = write(tmp_path, "d.csv", "Q1,Q2\n0,1\n0,2\n0,3\n")
    data = load_csv(path)
    assert data.numeric_columns() == ["Q1", "Q2"]
    assert data.nonnumeric_columns() == []


def test_all_missing_column_is_nonnumeric(tmp_path):
    # P3: 전부 결측인 컬럼은 nonnumeric으로 분류되어 자동설정에서 경고 대상
    path = write(tmp_path, "d.csv", "Q1,Q2\nNA,1\n,2\nNA,3\n")
    data = load_csv(path)
    assert data.numeric_columns() == ["Q2"]
    assert data.nonnumeric_columns() == ["Q1"]


def test_unknown_id_column_tracked(tmp_path):
    # P4: 헤더에 없는 --id-col 은 unknown_id_columns 로 기록
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    data = load_csv(path, id_columns=["NOPE"])
    assert data.unknown_id_columns == ["NOPE"]
    data2 = load_csv(path, id_columns=["Q1"])
    assert data2.unknown_id_columns == []


def test_out_of_range_detected():
    # P2: 선언된 척도 범위를 벗어난 값 탐지
    cols = ["Q1", "Q2"]
    rows = [
        {"Q1": 9, "Q2": 3},  # Q1=9 는 1~5 범위 밖
        {"Q1": 2, "Q2": 3},
        {"Q1": -1, "Q2": 4},  # Q1=-1 도 밖
    ]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    oor = {o["item"]: o for o in res["out_of_range"]}
    assert "Q1" in oor
    assert oor["Q1"]["count"] == 2
    assert "Q2" not in oor


def test_no_out_of_range_when_in_bounds():
    cols = ["Q1"]
    data = SurveyData(cols, [{"Q1": 1}, {"Q1": 5}, {"Q1": 3}])
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    assert res["out_of_range"] == []


def test_out_of_range_examples_are_int_when_integer():
    # P3(round2): 텍스트/JSON 일관성 — 정수값은 int로 저장
    cols = ["Q1"]
    data = SurveyData(cols, [{"Q1": 9}, {"Q1": 2}])
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    assert res["out_of_range"][0]["examples"] == [9]
    assert all(isinstance(v, int) for v in res["out_of_range"][0]["examples"])


def test_fully_missing_item_flagged(tmp_path):
    # P2(round2): 한 문항이 전부 결측이면 items_no_data 로 표시되고 리포트에 경고
    from surveyscan.report import render
    cols = ["A", "B"]
    rows = [{"A": 1, "B": None}, {"A": 5, "B": None}, {"A": 3, "B": None}]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["items_no_data"] == ["B"]
    assert "전부 결측인 문항" in render(res)
