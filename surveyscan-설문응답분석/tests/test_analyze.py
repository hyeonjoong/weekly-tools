"""분석 파이프라인 테스트 — 손계산 기대값과 비교."""
import math

import pytest

from surveyscan.analyze import analyze, response_frequencies, subscale_scores
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


def test_sum_scoring_and_proration():
    cols = ["Q1", "Q2", "Q3", "Q4"]
    rows = [
        {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4},          # 완전 -> 합 10
        {"Q1": 2, "Q2": 2, "Q3": 2, "Q4": None},        # 3/4 응답, 평균 2 -> 비례합 8
        {"Q1": 4, "Q2": None, "Q3": None, "Q4": None},  # 1/4 < 50% -> None
    ]
    data = make_data(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=0, scale_max=4,
                       min_valid_ratio=0.5, score_method="sum")
    scores = subscale_scores(data, cols, cfg)
    assert scores[0] == pytest.approx(10.0)   # 실제 합
    assert scores[1] == pytest.approx(8.0)    # 비례배분 합 (평균2 × 4문항)
    assert scores[2] is None
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["score_method"] == "sum"
    # 가능한 점수 범위 = k*min ~ k*max = 0 ~ 16
    assert sub["possible_min"] == 0
    assert sub["possible_max"] == 16


def test_floor_ceiling_effects():
    cols = ["Q1", "Q2"]
    # mean 방식, 척도 1~5. R1 모두 최소(1)->바닥, R2 모두 최대(5)->천장
    rows = [
        {"Q1": 1, "Q2": 1},
        {"Q1": 5, "Q2": 5},
        {"Q1": 3, "Q2": 4},
        {"Q1": 2, "Q2": 3},
    ]
    data = make_data(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["floor"]["n"] == 1 and sub["floor"]["pct"] == 25.0
    assert sub["ceiling"]["n"] == 1 and sub["ceiling"]["pct"] == 25.0
    assert sub["possible_min"] == 1 and sub["possible_max"] == 5


def test_floor_ceiling_none_without_scale():
    cols = ["Q1", "Q2"]
    data = make_data(cols, _rows(cols, [[1, 2], [3, 4], [5, 5]]))
    cfg = SurveyConfig(subscales={"S": cols})  # 척도범위 미지정
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["floor"] is None and sub["ceiling"] is None
    assert sub["possible_min"] is None


def test_alpha_ci_and_sem_and_inter_item():
    cols = ["Q1", "Q2", "Q3"]
    matrix = [[4, 5, 3], [3, 2, 4], [5, 5, 4], [2, 3, 2], [4, 4, 3], [1, 2, 1]]
    data = make_data(cols, _rows(cols, matrix))
    cfg = SurveyConfig(subscales={"S": cols})
    res = analyze(data, cfg, conf=0.90)
    sub = res["subscales"][0]
    assert sub["alpha"] is not None
    lo, hi = sub["alpha_ci"]
    assert lo < sub["alpha"] < hi  # 점 추정치는 CI 내부에 있어야 함
    assert sub["sem"] is not None and sub["sem"] >= 0
    assert sub["mean_inter_item_r"] is not None
    assert sub["min_inter_item_r"] <= sub["mean_inter_item_r"] <= sub["max_inter_item_r"]


def test_inter_item_warning_flags_rendered():
    from surveyscan.report import render
    # 매우 높은 상관(중복) 경고
    cols = ["Q1", "Q2", "Q3"]
    redundant = [[1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4], [5, 5, 5]]
    data = make_data(cols, _rows(cols, redundant))
    cfg = SurveyConfig(subscales={"S": cols})
    out = render(analyze(data, cfg))
    assert "문항 중복 의심(>.70)" in out


def test_names_with_newline_do_not_break_tables():
    from surveyscan.report import render, render_markdown
    cols = ["Q1", "Q2"]
    data = make_data(cols, _rows(cols, [[1, 2], [3, 4], [5, 5]]))
    cfg = SurveyConfig(subscales={"라인1\n라인2": cols})
    md = render_markdown(analyze(data, cfg))
    # 각 표 행은 한 줄 — 하위척도 이름의 개행이 셀 안에서 공백으로 치환돼야 함
    for line in md.splitlines():
        assert "\n" not in line  # 자명하지만 구조 확인
    assert "라인1 라인2" in md
    txt = render(analyze(data, cfg))
    assert "라인1 라인2" in txt


def test_score_ci_present():
    cols = ["Q1", "Q2"]
    data = make_data(cols, _rows(cols, [[1, 2], [3, 4], [5, 5], [2, 3]]))
    cfg = SurveyConfig(subscales={"S": cols})
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    lo, hi = sub["score_ci"]
    assert lo < sub["score_mean"] < hi


def test_mdc_and_standardized_alpha():
    cols = ["Q1", "Q2", "Q3"]
    matrix = [[4, 5, 3], [3, 2, 4], [5, 5, 4], [2, 3, 2], [4, 4, 3], [1, 2, 1]]
    data = make_data(cols, _rows(cols, matrix))
    cfg = SurveyConfig(subscales={"S": cols})
    sub = analyze(data, cfg)["subscales"][0]
    # MDC95 = 1.96*sqrt(2)*SEM
    assert sub["mdc95"] == pytest.approx(1.959963984540054 * math.sqrt(2) * sub["sem"])
    # 표준화 α = k*rbar/(1+(k-1)*rbar)
    k, rbar = sub["n_items"], sub["mean_inter_item_r"]
    assert sub["alpha_std"] == pytest.approx(k * rbar / (1 + (k - 1) * rbar))


def test_alpha_std_none_when_alpha_uncomputable():
    # R2-F1: 총점 분산 0 -> alpha None. 표준화 α도 나오면 안 됨(무의미/폭발 방지).
    from surveyscan.report import render
    cols = ["Q1", "Q2"]
    # 두 응답자의 총점이 동일(3) -> 총점 분산 0 -> alpha None
    rows = [{"Q1": 1, "Q2": 2}, {"Q1": 2, "Q2": 1}]
    data = make_data(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols})
    sub = analyze(data, cfg)["subscales"][0]
    assert sub["alpha"] is None
    assert sub["alpha_std"] is None
    assert sub["mdc95"] is None and sub["sem"] is None
    # 리포트에 이상값이 새지 않아야 함
    assert "표준화 α" not in render(analyze(data, cfg))


def test_response_frequencies_huge_range_gated_fast():
    # R2: 거대한 정수 척도 범위는 range() 물질화 전에 즉시 None (OOM/행 방지)
    cols = ["Q1"]
    data = make_data(cols, _rows(cols, [[0], [1], [2]]))
    assert response_frequencies(data, cols, 0, 10 ** 11) is None
    # 21개 초과도 None
    assert response_frequencies(data, cols, 0, 21) is None
    # 21개 이하는 정상
    assert response_frequencies(data, cols, 0, 20) is not None


def test_sum_equals_mean_times_k_when_complete():
    cols = ["Q1", "Q2", "Q3"]
    matrix = [[4, 5, 3], [3, 2, 4], [5, 5, 4]]
    data = make_data(cols, _rows(cols, matrix))
    cfg_mean = SurveyConfig(subscales={"S": cols}, score_method="mean")
    cfg_sum = SurveyConfig(subscales={"S": cols}, score_method="sum")
    ms = subscale_scores(data, cols, cfg_mean)
    ss = subscale_scores(data, cols, cfg_sum)
    for m, s in zip(ms, ss):
        assert s == pytest.approx(m * 3)


def test_negative_item_total_flag_rendered():
    from surveyscan.report import render
    # Q3 이 나머지와 음의 상관 -> 음수 문항-총점 r
    cols = ["Q1", "Q2", "Q3"]
    matrix = [[1, 1, 5], [2, 2, 4], [3, 3, 3], [4, 4, 2], [5, 5, 1]]
    data = make_data(cols, _rows(cols, matrix))
    cfg = SurveyConfig(subscales={"S": cols})
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["item_total_corr"]["Q3"] < 0
    assert "음수 r(역코딩 확인)" in render(res)


def test_response_frequencies():
    cols = ["Q1", "Q2"]
    rows = [
        {"Q1": 0, "Q2": 4},
        {"Q1": 0, "Q2": 2},
        {"Q1": 2, "Q2": 9},  # Q2=9 는 범위 밖 -> 기타
    ]
    data = make_data(cols, rows)
    fr = response_frequencies(data, cols, 0, 4)
    assert fr["levels"] == [0, 1, 2, 3, 4]
    q1 = fr["items"][0]
    assert q1["counts"][0] == 2 and q1["counts"][2] == 1
    q2 = fr["items"][1]
    assert q2["other"] == 1  # 9 는 기타
    # 척도 범위가 정수가 아니면 None
    assert response_frequencies(data, cols, 0.5, 4.5) is None
    # 범위가 없으면 None
    assert response_frequencies(data, cols, None, None) is None


def test_item_descriptives_have_skew_kurt():
    cols = ["Q1"]
    data = make_data(cols, _rows(cols, [[1], [2], [3], [4], [5], [4], [3], [2]]))
    cfg = SurveyConfig(subscales={"S": ["Q1"]})
    res = analyze(data, cfg)
    d = res["descriptives"][0]
    assert "skew" in d and "kurtosis" in d
    assert d["q1"] is not None and d["q3"] is not None


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
