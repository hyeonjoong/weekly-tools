"""임상 심각도 구간(severity_bands) 테스트: config 검증 · 분류 · 분포표 · 리포트."""
import json

import pytest

from surveyscan import report
from surveyscan.analyze import analyze
from surveyscan.config import ConfigError, SurveyConfig, band_label, _from_dict
from surveyscan.dataio import load_csv

ISI_BANDS = [[0, 7, "없음"], [8, 14, "역치하"], [15, 21, "중등도"], [22, 28, "중증"]]


def _cfg(**kw):
    base = dict(subscales={"S": ["A", "B"]}, scale_min=0, scale_max=4)
    base.update(kw)
    return _from_dict(base)


def test_bands_parsed_and_sorted():
    cfg = _cfg(severity_bands={"S": [[5, 8, "높음"], [0, 4, "낮음"]]})
    assert cfg.severity_bands["S"] == [(0.0, 4.0, "낮음"), (5.0, 8.0, "높음")]


def test_bands_reject_unknown_subscale():
    with pytest.raises(ConfigError) as e:
        _cfg(severity_bands={"없는척도": [[0, 1, "x"]]})
    assert "subscales 에 없는" in str(e.value)


def test_bands_reject_overlap():
    # 겹치면 같은 점수가 두 심각도로 분류되어 표가 통째로 틀어진다.
    with pytest.raises(ConfigError) as e:
        _cfg(severity_bands={"S": [[0, 7, "a"], [7, 14, "b"]]})
    assert "겹칩니다" in str(e.value)


def test_bands_reject_bad_shapes():
    for bad in (
        {"S": [[0, 7]]},                    # 원소 2개
        {"S": [[7, 0, "역전"]]},            # 하한>상한
        {"S": [["0", 7, "문자경계"]]},       # 문자 경계
        {"S": [[0, 7, ""]]},                # 빈 라벨
        {"S": [[0, 7, 3]]},                 # 라벨이 숫자
        {"S": []},                          # 빈 리스트
        {"S": "0-7"},                       # 리스트 아님
    ):
        with pytest.raises(ConfigError):
            _cfg(severity_bands=bad)
    with pytest.raises(ConfigError):
        _cfg(severity_bands={})             # 빈 객체
    with pytest.raises(ConfigError):
        _cfg(severity_bands=[[0, 7, "x"]])  # 객체 아님


def test_bands_reject_bool_bounds():
    # True 는 파이썬에서 int 지만 경계로는 명백한 실수다.
    with pytest.raises(ConfigError):
        _cfg(severity_bands={"S": [[True, 7, "x"]]})


def test_band_label_boundaries_inclusive_and_gaps_none():
    bands = [(0.0, 7.0, "없음"), (8.0, 14.0, "역치하")]
    assert band_label(0.0, bands) == "없음"
    assert band_label(7.0, bands) == "없음"
    assert band_label(8.0, bands) == "역치하"
    assert band_label(7.5, bands) is None      # 구간 사이 빈틈
    assert band_label(99.0, bands) is None     # 범위 밖
    assert band_label(None, bands) is None
    # 부동소수 잡음(7.000000000000001)이 경계를 넘겨 오분류하지 않아야 한다.
    assert band_label(7.0 + 1e-12, bands) == "없음"


def _write(tmp_path, text, name="d.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_band_distribution_counts_and_pct(tmp_path):
    # 4문항·0~4 척도 → 가능한 총점 0~16. 합점수: 0, 8, 16, 12 → 구간별 1명씩.
    csv = "A,B,C,D\n0,0,0,0\n2,2,2,2\n4,4,4,4\n" + "3,3,3,3\n"
    path = _write(tmp_path, csv)
    data = load_csv(path)
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C", "D"]},
        scale_min=0, scale_max=4, score_method="sum",
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 11.0, "역치하"),
                              (12.0, 14.0, "중등도"), (15.0, 16.0, "중증")]},
    )
    res = analyze(data, cfg)
    s = res["subscales"][0]
    counts = {b["label"]: b["n"] for b in s["bands"]}
    assert counts == {"없음": 1, "역치하": 1, "중등도": 1, "중증": 1}
    assert sum(counts.values()) == s["n_scored"] == 4
    assert s["n_unbanded"] == 0
    assert s["bands_out_of_range"] is False
    assert [b["pct"] for b in s["bands"]] == [25.0, 25.0, 25.0, 25.0]
    assert s["band_scores"][0] == "없음"


def test_band_out_of_range_flag_when_method_mismatch(tmp_path):
    # 총합 기준 구간(0~28)을 평균 점수(0~4)에 적용 → 경고 플래그 + 대부분 미분류
    path = _write(tmp_path, "A,B,C,D\n0,0,0,0\n2,2,2,2\n4,4,4,4\n")
    data = load_csv(path)
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C", "D"]},
        scale_min=0, scale_max=4, score_method="mean",
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 14.0, "역치하"),
                              (15.0, 21.0, "중등도"), (22.0, 28.0, "중증")]},
    )
    res = analyze(data, cfg)
    s = res["subscales"][0]
    assert s["bands_out_of_range"] is True
    txt = report.render(res)
    assert "구간 경계가 가능한 점수 범위" in txt


def test_unbanded_counted_for_prorated_fraction(tmp_path):
    # 결측 비례배분으로 15.0 이 아닌 소수 점수(7.5)가 나오면 구간 빈틈에 떨어진다.
    path = _write(tmp_path, "A,B,C,D\n3,2,,2\n1,1,1,1\n")
    data = load_csv(path)
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C", "D"]},
        scale_min=0, scale_max=4, score_method="sum", min_valid_ratio=0.5,
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 14.0, "역치하")]},
    )
    res = analyze(data, cfg)
    s = res["subscales"][0]
    assert s["scores"][0] == pytest.approx(9.333333333333334)  # (3+2+2)/3*4
    assert s["scores"][1] == pytest.approx(4.0)
    assert s["n_unbanded"] == 0
    # 점수를 빈틈(7.5)에 떨어뜨리는 자료
    path2 = _write(tmp_path, "A,B,C,D\n3,2,,0\n", name="e.csv")
    res2 = analyze(load_csv(path2), cfg)
    s2 = res2["subscales"][0]
    assert s2["scores"][0] == pytest.approx(6.666666666666667)
    assert s2["n_unbanded"] == 0
    # 확실히 빈틈에 놓이는 값: 합 7.5
    path3 = _write(tmp_path, "A,B,C,D\n2,2,,1.625\n", name="f.csv")
    res3 = analyze(load_csv(path3), cfg)
    s3 = res3["subscales"][0]
    assert s3["scores"][0] == pytest.approx(7.5)
    assert s3["n_unbanded"] == 1
    assert s3["band_scores"][0] is None
    assert "미분류" in report.render(res3)


def test_no_bands_means_empty_structures(tmp_path):
    path = _write(tmp_path, "A,B\n1,2\n3,4\n")
    res = analyze(load_csv(path), SurveyConfig(subscales={"S": ["A", "B"]}))
    s = res["subscales"][0]
    assert s["bands"] == [] and s["band_scores"] == [] and s["n_unbanded"] == 0
    # 구간이 없으면 리포트에도 심각도 섹션이 없어야 한다.
    assert "심각도 구간 분포" not in report.render(res)
    assert "임상 심각도 구간 분포" not in report.render_markdown(res)


def test_bands_render_in_text_and_markdown(tmp_path):
    path = _write(tmp_path, "A,B,C,D\n0,0,0,0\n4,4,4,4\n2,2,2,2\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C", "D"]},
        scale_min=0, scale_max=4, score_method="sum",
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 16.0, "있음")]},
    )
    res = analyze(load_csv(path), cfg)
    txt = report.render(res)
    md = report.render_markdown(res)
    assert "심각도 구간 분포" in txt and "없음" in txt
    assert "## 임상 심각도 구간 분포" in md
    assert "| 심각도 | 점수 구간 | N | % |" in md


def test_bands_survive_json_roundtrip(tmp_path):
    path = _write(tmp_path, "A,B,C,D\n0,0,0,0\n4,4,4,4\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C", "D"]},
        scale_min=0, scale_max=4, score_method="sum",
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 16.0, "있음")]},
    )
    res = analyze(load_csv(path), cfg)
    dumped = json.loads(json.dumps(res, ensure_ascii=False, allow_nan=False))
    assert dumped["subscales"][0]["bands"][0]["label"] == "없음"
