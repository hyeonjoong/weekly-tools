"""End-to-end orchestration, warnings, and rendering tests."""

import json

import pytest

from agreestat.analyze import analyze as run
from agreestat.report import render_json, render_text


def _good_pair():
    a = [14.2, 15.1, 11.9, 13.0, 16.4, 12.2, 18.1, 10.5, 19.3, 13.8,
         15.5, 17.0, 11.1, 14.9, 16.8, 12.7, 13.3, 18.6, 10.9, 15.2]
    b = [14.0, 14.8, 12.2, 12.9, 16.1, 12.5, 17.8, 10.9, 19.0, 13.5,
         15.7, 16.6, 11.4, 14.6, 17.1, 12.4, 13.6, 18.2, 11.2, 15.0]
    return a, b


def test_analyze_good_agreement():
    a, b = _good_pair()
    res = run(a, b, name_a="sensor", name_b="band")
    assert res.n == 20
    assert res.icc21.value > 0.9
    assert res.ccc.value > 0.9
    # tight LoA relative to the data range
    assert (res.ba.loa_upper - res.ba.loa_lower) < 5
    assert res.reported_icc == "ICC(2,1)"


def test_analyze_proportional_bias_warns():
    a = [10, 22, 33, 46, 58, 71, 84, 96, 110, 121, 133, 145]
    b = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
    res = run(a, b, name_a="new", name_b="ref")
    assert res.ba.prop_bias is True
    assert any("비례 편향" in w for w in res.warnings)


def test_analyze_repeated_measures_warns_and_computes():
    a = [10, 12, 20, 24, 30, 33]
    b = [10, 11, 20, 21, 30, 31]
    subs = ["A", "A", "B", "B", "C", "C"]
    res = run(a, b, subjects=subs)
    assert res.repeat.available is True
    assert any("반복측정" in w for w in res.warnings)


def test_analyze_constant_column_guarded():
    # method B constant -> ICC/CCC may be undefined; must not crash
    a = [1, 2, 3, 4, 5, 6]
    b = [7, 7, 7, 7, 7, 7]
    res = run(a, b)
    assert any("분산 0" in w for w in res.warnings)
    txt = render_text(res)
    assert "agreestat" in txt  # renders without raising


def test_analyze_two_rows():
    res = run([1.0, 2.0], [1.1, 2.2])
    assert res.n == 2
    txt = render_text(res)
    assert "paired n = 2" in txt


def test_analyze_small_n_warns():
    res = run([1, 2, 3, 4], [1.1, 2.1, 2.9, 4.2])
    assert any("표본이 작습니다" in w for w in res.warnings)


def test_analyze_rejects_mismatched_length():
    with pytest.raises(ValueError):
        run([1, 2, 3], [1, 2])


def test_render_text_sections_present():
    a, b = _good_pair()
    txt = render_text(run(a, b, name_a="A", name_b="B"))
    for marker in ("[1] 데이터 요약", "[2] Bland", "[3] ICC",
                   "[4] Lin's CCC", "[5] 반복측정", "[6] 상관",
                   "논문용 문장"):
        assert marker in txt


def test_render_json_valid_and_complete():
    a, b = _good_pair()
    res = run(a, b, name_a="sensor", name_b="band")
    obj = json.loads(render_json(res))
    assert obj["method_a"] == "sensor"
    assert obj["n"] == 20
    assert "bias" in obj["bland_altman"]
    assert obj["icc"]["icc_2_1"]["value"] is not None
    assert obj["ccc"]["value"] is not None
    assert isinstance(obj["warnings"], list)


def test_render_json_nan_becomes_null():
    a = [1, 2, 3, 4, 5, 6]
    b = [7, 7, 7, 7, 7, 7]  # constant -> NaN CCC pearson
    obj = json.loads(render_json(run(a, b)))
    assert obj["ccc"]["pearson_r"] is None  # NaN serialised as null


def test_percent_mode_end_to_end():
    a = [110, 220, 330, 440, 550]
    b = [100, 200, 300, 400, 500]
    res = run(a, b, mode="percent")
    assert res.ba.unit == "%"
    txt = render_text(res)
    assert "백분율" in txt
