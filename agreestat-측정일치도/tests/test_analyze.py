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


# --------------------------------------------------------------------------
# Acceptance limits / interchangeability verdict
# --------------------------------------------------------------------------
def test_accept_interchangeable():
    a = list(range(1, 21))
    b = [x + 0.1 for x in a]  # tiny, consistent offset
    res = run(a, b, accept=(-1.0, 1.0))
    assert res.interchangeable is True
    assert any("교환가능" in w for w in res.warnings)
    obj = json.loads(render_json(res))
    assert obj["acceptance"]["interchangeable"] is True
    assert obj["acceptance"]["lower"] == -1.0


def test_accept_not_interchangeable():
    a = list(range(1, 21))
    b = [x * 1.4 for x in a]  # growing difference
    res = run(a, b, accept=(-0.5, 0.5))
    assert res.interchangeable is False
    txt = render_text(res)
    assert "교환 불가" in txt


def test_accept_absent_by_default():
    res = run([1, 2, 3, 4], [1.1, 2.1, 2.9, 4.2])
    assert res.interchangeable is None
    obj = json.loads(render_json(res))
    assert obj["acceptance"]["interchangeable"] is None


# --------------------------------------------------------------------------
# Warnings: CI-based grade, non-finite, percent-mode near-zero mean
# --------------------------------------------------------------------------
def test_sentence_grades_from_ci_lower_not_point():
    # Regression pin for the flagship Koo & Li fix. The HRV example has a high
    # ICC(2,1) point (~0.92) but a CI lower bound (~0.009) that grades "poor",
    # so the sentence MUST say '낮음' (from the CI lower bound), never '매우 좋음'.
    from agreestat.dataio import load_pairs
    d = load_pairs("examples/hrv_rmssd_proportional.csv",
                   "watch_rmssd_ms", "ecg_rmssd_ms", "subject")
    res = run(d.a, d.b, subjects=d.subjects, name_a=d.name_a, name_b=d.name_b)
    assert res.icc21.value > 0.9 and res.icc21.ci_lower < 0.5  # grades must differ
    txt = render_text(res)
    sentence = txt.split("논문용 문장")[1]
    assert "'낮음' 수준" in sentence
    assert "매우 좋음" not in sentence
    assert any("Koo & Li" in w for w in res.warnings)


def test_percent_sign_mixed_means_warns():
    a = [-10, -5, 5, 10, 15]
    b = [-11, -4, 6, 9, 16]
    res = run(a, b, mode="percent")
    assert any("부호가 섞" in w for w in res.warnings)


# --------------------------------------------------------------------------
# Repeated-measures LoA + precision + markdown/plot outputs
# --------------------------------------------------------------------------
def test_repeated_measures_headlines_in_report_and_sentence():
    a = [10, 12, 13, 20, 24, 22, 30, 33, 31, 40, 44, 42]
    b = [10, 11, 12, 20, 21, 20, 30, 31, 30, 40, 41, 40]
    subs = ["A", "A", "A", "B", "B", "B", "C", "C", "C", "D", "D", "D"]
    res = run(a, b, subjects=subs)
    assert res.rm_ba is not None and res.rm_ba.available
    txt = render_text(res)
    assert "[2c] 반복측정 보정 LoA" in txt
    assert any("반복측정 보정 LoA" in w for w in res.warnings)
    sentence = txt.split("논문용 문장")[1]
    assert "반복측정 보정" in sentence
    obj = json.loads(render_json(res))
    assert obj["bland_altman_repeated_measures"]["available"] is True
    assert obj["bland_altman_repeated_measures"]["variance_components"]["m0"] == 3.0


def test_precision_required_n_and_json():
    a = [14.2, 15.1, 11.9, 13.0, 16.4, 12.2, 18.1, 10.5, 19.3, 13.8]
    b = [14.0, 14.8, 12.2, 12.9, 16.1, 12.5, 17.8, 10.9, 19.0, 13.5]
    res = run(a, b, target_loa_hw=0.5)
    assert res.precision_required_n is not None and res.precision_required_n >= 2
    txt = render_text(res)
    assert "LoA 추정 정밀도" in txt and "필요 표본" in txt
    obj = json.loads(render_json(res))
    assert obj["precision"]["required_n"] == res.precision_required_n
    assert obj["precision"]["target_halfwidth"] == 0.5


def test_ci_lower_grade_in_json():
    a, b = _good_pair()
    obj = json.loads(render_json(run(a, b)))
    assert obj["icc"]["ci_lower_grade"] is not None
    # perfect agreement (a==b) -> mse=0 -> NaN ICC CI -> grade is null
    obj2 = json.loads(render_json(run([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6])))
    assert obj2["icc"]["ci_lower_grade"] is None


def test_accept_percent_mode_verdict():
    a = [110, 220, 330, 440, 550]
    b = [100, 200, 300, 400, 500]  # ~10% high
    res = run(a, b, mode="percent", accept=(-15.0, 15.0))
    assert res.interchangeable is True
    txt = render_text(res)
    assert "%" in txt and "교환가능" in txt


def _rm_pair():
    a = [1, 2, 3, 2, 3, 4, 3, 4, 5, 4, 5, 6, 5, 6, 7]
    b = [1.1, 1.9, 3.2, 1.8, 3.1, 4.2, 3.1, 3.9, 5.1, 4.2, 4.8, 6.1, 5.1, 5.9, 7.2]
    subs = ["A"] * 3 + ["B"] * 3 + ["C"] * 3 + ["D"] * 3 + ["E"] * 3
    return a, b, subs


def test_interchangeability_verdict_uses_repeated_measures_loa():
    # verdict must be judged on the headlined RM LoA, not the naive LoA
    a, b, subs = _rm_pair()
    res = run(a, b, subjects=subs)
    naive_hi = res.ba.loa_upper
    rm_hi = res.rm_ba.loa_upper
    assert rm_hi > naive_hi  # RM LoA is wider
    # acceptance limit between the two upper LoA -> naive would pass, RM must fail
    acc = (res.rm_ba.loa_lower - 0.01, (naive_hi + rm_hi) / 2.0)
    res2 = run(a, b, subjects=subs, accept=acc)
    assert res2.interchangeable is False
    assert any("반복측정 95% LoA" in w and "허용한계" in w for w in res2.warnings)


def test_markdown_includes_repeated_measures_rows():
    from agreestat.report import render_markdown
    a, b, subs = _rm_pair()
    md = render_markdown(run(a, b, subjects=subs))
    assert "Repeated-measures lower LoA" in md
    assert "B&A 2007 (recommended)" in md


def test_rm_ba_json_not_available_shape():
    obj = json.loads(render_json(run([1, 2, 3], [1.1, 2.1, 3.1],
                                      subjects=["A", "B", "C"])))
    rm = obj["bland_altman_repeated_measures"]
    assert rm["available"] is False
    assert rm["note"]


def test_precision_already_met_branch():
    a = [14.2, 15.1, 11.9, 13.0, 16.4, 12.2, 18.1, 10.5, 19.3, 13.8]
    b = [14.0, 14.8, 12.2, 12.9, 16.1, 12.5, 17.8, 10.9, 19.0, 13.5]
    txt = render_text(run(a, b, target_loa_hw=50.0))  # trivially met
    assert "이미 충족" in txt


def test_required_n_is_the_true_minimum():
    # hw(n) <= target < hw(n-1) must hold exactly (down-corrected minimum)
    import math
    from agreestat.analyze import _required_n_for_loa_hw
    from agreestat.special import t_ppf

    def hw(n, sd):
        return t_ppf(0.975, n - 1) * sd * math.sqrt(
            1.0 / n + 1.96 ** 2 / (2.0 * (n - 1)))

    for sd, target in [(0.6289, 0.2), (2.5, 0.5), (10.0, 1.0), (1.0, 0.05)]:
        n, _approx = _required_n_for_loa_hw(sd, target)
        assert n is not None and n >= 2
        assert hw(n, sd) <= target
        if n > 2:
            assert hw(n - 1, sd) > target


def test_precision_target_too_tight():
    a = [10, 20, 30, 40, 50, 15, 25, 35, 45, 55]
    b = [11, 19, 32, 38, 51, 14, 27, 33, 47, 53]
    txt = render_text(run(a, b, target_loa_hw=1e-9))
    assert "매우 큼" in txt
    obj = json.loads(render_json(run(a, b, target_loa_hw=1e-9)))
    assert obj["precision"]["required_n"] is None


def test_regression_loa_sd_negative_renders_warning():
    a = [6.862, 22.335, 30.305, 37.515, 47.239,
         56.149, 65.51, 74.683, 82.708, 91.684]
    b = [13.138, 13.665, 21.695, 30.485, 36.761,
         43.851, 50.49, 57.317, 65.292, 72.316]
    txt = render_text(run(a, b))
    assert "[2b] 회귀 기반 LoA" in txt
    assert "음수로 외삽" in txt


def test_svg_acceptance_band_and_xml_escape():
    from agreestat.report import render_svg
    import xml.dom.minidom as m
    a, b = _good_pair()
    res = run(a, b, name_a='A<&>"x', name_b="band", accept=(-1.0, 1.0))
    svg = render_svg(res)
    m.parseString(svg)                    # hostile names must still parse
    assert 'fill-opacity="0.08"' in svg   # acceptance band present
    assert "&amp;" in svg and "&lt;" in svg


def test_markdown_and_plot_and_svg_render():
    from agreestat.report import render_markdown, render_plot_data, render_svg
    a, b = _good_pair()
    res = run(a, b, name_a="sensor", name_b="band", accept=(-1.0, 1.0))
    md = render_markdown(res)
    assert md.startswith("# agreestat")
    assert "| Metric | Estimate |" in md
    assert "Interchangeability" in md
    pd = render_plot_data(res)
    assert pd.startswith("# agreestat plot data")
    assert "mean,diff,outside_loa" in pd
    assert len(pd.strip().splitlines()) == res.n + 2  # header comment + col + n rows
    svg = render_svg(res)
    import xml.dom.minidom as m
    m.parseString(svg)  # must be well-formed XML
    assert "Bland" in svg


def test_nonfinite_warning_surfaced():
    res = run([1, 2, 3, 4], [1, 2, 3, 4], nonfinite=2)
    assert any("무한대" in w for w in res.warnings)


def test_percent_near_zero_mean_warns():
    a = [0.02, 10, 12, 14, 16]
    b = [0.08, 11, 13, 15, 17]
    res = run(a, b, mode="percent")
    assert any("0에 가까운" in w for w in res.warnings)


def test_extra_warnings_passed_through():
    res = run([1, 2, 3, 4], [1.1, 2.1, 2.9, 4.2],
              extra_warnings=["자동 선택 경고 테스트"])
    assert any("자동 선택 경고 테스트" in w for w in res.warnings)


# --------------------------------------------------------------------------
# PII: subject ids must never appear in report or JSON output
# --------------------------------------------------------------------------
def test_subject_ids_not_leaked_in_output():
    a = [10, 12, 20, 24, 30, 33]
    b = [10, 11, 20, 21, 30, 31]
    subs = ["PATIENT-12345", "PATIENT-12345", "KIM-CHULSOO",
            "KIM-CHULSOO", "LEE-YOUNGHEE", "LEE-YOUNGHEE"]
    res = run(a, b, subjects=subs)
    txt = render_text(res)
    js = render_json(res)
    for sid in set(subs):
        assert sid not in txt
        assert sid not in js


# --------------------------------------------------------------------------
# Report renders exact numbers (not just section markers)
# --------------------------------------------------------------------------
def test_report_renders_exact_numbers():
    # a - b = [-1, 1, 1, -1] -> bias 0.000
    a = [10, 12, 14, 16]
    b = [11, 11, 13, 17]
    txt = render_text(run(a, b, name_a="A", name_b="B"))
    assert "bias (평균차) = 0.000" in txt
    assert "LoA 밖 관측치:" in txt


def test_report_percent_unit_on_ci():
    a = [110, 220, 330, 440, 550]
    b = [100, 200, 300, 400, 500]
    txt = render_text(run(a, b, mode="percent"))
    # the CI must carry the % unit, not just the point estimate
    assert "% CI" in txt
    assert "%," in txt  # e.g. "[95% CI 8.12%, ...]"


# --------------------------------------------------------------------------
# Method-comparison regression (Deming / Passing–Bablok) integration
# --------------------------------------------------------------------------
def test_analyze_includes_regression_blocks():
    a, b = _good_pair()
    res = run(a, b, name_a="sensor", name_b="band")
    assert res.deming is not None and res.deming.available
    assert res.passing_bablok is not None and res.passing_bablok.available
    txt = render_text(res)
    assert "방법비교 회귀" in txt
    assert "Passing–Bablok" in txt and "Deming" in txt
    js = json.loads(render_json(res))
    assert js["regression"]["deming"]["available"] is True
    assert js["regression"]["passing_bablok"]["available"] is True
    assert "slope" in js["regression"]["deming"]


def test_analyze_regression_flags_proportional_bias_warning():
    # 25% proportional bias -> regression slope CI should exclude 1 and warn
    import random
    random.seed(101)
    x = [random.gauss(50, 15) for _ in range(60)]
    a = [1.25 * xi + random.gauss(0, 2) for xi in x]  # test
    b = x                                              # reference
    res = run(a, b, name_a="test", name_b="ref")
    assert res.passing_bablok.proportional_bias is True
    assert any("방법비교 회귀" in w for w in res.warnings)
    # the paste-ready sentence should mention Passing–Bablok
    assert "Passing–Bablok 회귀" in render_text(res)


def test_analyze_regression_constant_bias_detected():
    # pure +5 offset: intercept CI excludes 0, slope CI includes 1
    import random
    random.seed(202)
    x = [random.gauss(30, 8) for _ in range(50)]
    a = [xi + 5.0 + random.gauss(0, 0.5) for xi in x]
    b = x
    res = run(a, b, name_a="test", name_b="ref")
    pb = res.passing_bablok
    assert pb.constant_bias is True
    assert pb.slope_ci[0] <= 1.0 <= pb.slope_ci[1]  # no proportional bias


def test_analyze_deming_lambda_passthrough():
    a, b = _good_pair()
    r1 = run(a, b, deming_lambda=1.0)
    r4 = run(a, b, deming_lambda=4.0)
    assert r1.deming.lam == 1.0 and r4.deming.lam == 4.0
    assert r1.deming.slope != r4.deming.slope


def test_analyze_regression_skipped_when_constant():
    # method B constant -> Deming/PB unavailable but analysis still runs
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [7.0, 7.0, 7.0, 7.0, 7.0]
    res = run(a, b)
    assert not res.deming.available
    assert not res.passing_bablok.available
    # no crash rendering
    render_text(res)
    json.loads(render_json(res))


def test_decision_point_bias_never_carries_percent_unit():
    # regression is always fit on RAW values -> bias(Xc) is absolute, must not
    # be labelled with the Bland-Altman % unit, and --accept (a % limit in
    # percent mode) must NOT be compared to the absolute bias(Xc).
    a = [110, 220, 330, 440, 550, 120, 230, 340]
    b = [100, 200, 300, 400, 500, 110, 210, 310]
    res = run(a, b, mode="percent", decision_point=300.0,
              accept=(-10.0, 10.0))
    txt = render_text(res)
    assert "XC=300.000" in txt
    assert "직접 비교하지 않습니다" in txt  # percent accept comparison skipped
    # the regression bias value lines must not carry the % unit (ignore the
    # "95% CI" label inside the bracket, which is not a unit on the value)
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("Passing–Bablok:") or s.startswith("Deming:"):
            head = line.split("[")[0]  # drop the "[95% CI ...]" part
            assert "%" not in head


def test_decision_point_accept_verdict_absolute_mode():
    a = [12.1, 14.0, 16.2, 18.1, 20.0, 13.0, 15.1, 17.2, 19.0, 11.0]
    b = [12.0, 14.1, 16.0, 18.0, 20.1, 13.1, 15.0, 17.0, 19.1, 11.1]
    res = run(a, b, decision_point=15.0, accept=(-1.0, 1.0))
    txt = render_text(res)
    assert "허용한계" in txt and "bias(XC)" in txt


def test_analyze_survives_overflow_magnitudes():
    # values within dataio's 1e150 cap can overflow ICC/CCC internals; analyze()
    # must degrade gracefully (warn, NaN) rather than raise OverflowError.
    a = [1e150, 9e149, 5e149, 8e149, 3e149, 7e149]
    b = [1.0e150, 9.1e149, 5.2e149, 7.9e149, 3.1e149, 7.2e149]
    res = run(a, b)  # must not raise
    txt = render_text(res)
    json.loads(render_json(res))  # valid JSON
    assert isinstance(txt, str) and len(txt) > 0
