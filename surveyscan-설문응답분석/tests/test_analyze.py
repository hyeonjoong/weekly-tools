"""분석 파이프라인 테스트 — 손계산 기대값과 비교."""
import pytest

from surveyscan.analyze import analyze, subscale_scores
from surveyscan.config import SurveyConfig
from surveyscan.dataio import SurveyData


def make_data(columns, rows_of_dicts):
    return SurveyData(columns=columns, rows=rows_of_dicts)


def _rows(cols, matrix):
    return [dict(zip(cols, vals)) for vals in matrix]


def test_reverse_coding_and_alpha_and_score():
    # Q3는 역문항(scale 1~5). 역코딩 후 [Q1,Q2,Q3rev]는
    # test_stats 의 alpha=0.75 컬럼과 동일해지도록 raw 값을 설계.
    cols = ["Q1", "Q2", "Q3"]
    matrix = [
        [4, 5, 3],  # R1  Q3rev = 6-3 = 3
        [3, 2, 2],  # R2  Q3rev = 6-2 = 4
        [5, 5, 2],  # R3  Q3rev = 6-2 = 4
        [2, 3, 4],  # R4  Q3rev = 6-4 = 2
    ]
    data = make_data(cols, _rows(cols, matrix))
    cfg = SurveyConfig(
        subscales={"S": ["Q1", "Q2", "Q3"]},
        reverse_items=["Q3"],
        scale_min=1,
        scale_max=5,
    )
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["alpha"] == pytest.approx(0.75)
    # 하위척도 점수 평균 = 3.5 (손계산)
    assert sub["score_mean"] == pytest.approx(3.5)
    assert sub["n_scored"] == 4
    # 개별 점수 확인
    scores = subscale_scores(data, ["Q1", "Q2", "Q3"], cfg)
    assert scores[0] == pytest.approx(4.0)
    assert scores[3] == pytest.approx(7 / 3)


def test_descriptives_use_raw_values():
    cols = ["Q1"]
    data = make_data(cols, _rows(cols, [[1], [2], [3], [4]]))
    cfg = SurveyConfig(subscales={"S": ["Q1"]})
    res = analyze(data, cfg)
    d = res["descriptives"][0]
    assert d["n"] == 4
    assert d["mean"] == pytest.approx(2.5)
    assert d["min"] == 1 and d["max"] == 4
    assert d["median"] == pytest.approx(2.5)


def test_missing_summary_and_min_valid_ratio():
    cols = ["Q1", "Q2", "Q3", "Q4"]
    # R1 완전, R2 한 칸 결측, R3 세 칸 결측(=1개만 응답 -> 25% < 50% -> 점수 None)
    rows = [
        {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4},
        {"Q1": 2, "Q2": None, "Q3": 3, "Q4": 4},
        {"Q1": 5, "Q2": None, "Q3": None, "Q4": None},
    ]
    data = make_data(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, min_valid_ratio=0.5)
    res = analyze(data, cfg)
    m = res["missing"]
    assert m["total_cells"] == 12
    assert m["missing_cells"] == 4
    assert m["complete_respondents"] == 1
    sub = res["subscales"][0]
    # listwise 완전응답자는 1명 -> alpha 계산불가(None)
    assert sub["alpha"] is None
    assert sub["n_complete"] == 1
    # 점수: R1 (응답4/4) O, R2 (3/4=75%>=50%) O, R3 (1/4=25%<50%) X
    scores = subscale_scores(data, cols, cfg)
    assert scores[0] is not None
    assert scores[1] is not None
    assert scores[2] is None
    assert sub["n_scored"] == 2


def test_missing_config_item_raises():
    cols = ["Q1"]
    data = make_data(cols, _rows(cols, [[1], [2]]))
    cfg = SurveyConfig(subscales={"S": ["Q1", "QX"]})
    with pytest.raises(ValueError):
        analyze(data, cfg)


def test_item_total_corr_present():
    cols = ["Q1", "Q2", "Q3"]
    matrix = [[4, 5, 3], [3, 2, 4], [5, 5, 4], [2, 3, 2]]
    data = make_data(cols, _rows(cols, matrix))
    cfg = SurveyConfig(subscales={"S": cols})
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert set(sub["item_total_corr"].keys()) == {"Q1", "Q2", "Q3"}
    # 모든 문항-총점 상관이 계산되어야 함
    assert all(v is not None for v in sub["item_total_corr"].values())
    # 문항 제거 시 alpha 도 3->2문항이라 계산 가능
    assert all(v is not None for v in sub["alpha_if_deleted"].values())
