"""Begg 검정 · trim-and-fill · 새 지표(상관·비율) · 절대효과(NNT) · CSV 내보내기."""

import math

import pytest

from metapool.analysis import run_analysis
from metapool.clinical import absolute_effect, pooled_control_risk
from metapool.diagnostics import begg_test, leave_one_out, trim_and_fill
from metapool.effects import Study, back_transform, fisher_z, logit_proportion
from metapool.io_csv import detect_measure, read_table
from metapool.report import funnel_plot, render_csv, render_markdown, render_text

# 정밀도가 좋아질수록 효과가 작아지는 전형적인 소규모연구 효과 자료
ASYM = [
    Study("small1", 0.90, 0.20),
    Study("small2", 0.75, 0.16),
    Study("small3", 0.60, 0.12),
    Study("mid1", 0.40, 0.05),
    Study("mid2", 0.35, 0.04),
    Study("big1", 0.20, 0.01),
    Study("big2", 0.18, 0.008),
]
SYM = [
    Study("A", 0.30, 0.02),
    Study("B", 0.50, 0.02),
    Study("C", 0.10, 0.02),
    Study("D", 0.40, 0.02),
    Study("E", 0.20, 0.02),
]


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# Begg 순위상관 검정
# --------------------------------------------------------------------------


def test_begg_score_matches_hand_computed_kendall_tau():
    """구현과 독립적으로 t_i·v_i 를 다시 만들어 일치 쌍을 직접 센다."""
    res = begg_test(ASYM)
    inv = [1.0 / s.vi for s in ASYM]
    sinv = sum(inv)
    mu = sum(w * s.yi for w, s in zip(inv, ASYM)) / sinv
    ts = [(s.yi - mu) / math.sqrt(s.vi - 1.0 / sinv) for s in ASYM]
    vs = [s.vi for s in ASYM]
    k = len(ASYM)
    score = 0
    for i in range(k):
        for j in range(i + 1, k):
            a, b = ts[j] - ts[i], vs[j] - vs[i]
            if a and b:
                score += 1 if (a > 0) == (b > 0) else -1
    assert res.tau == pytest.approx(2.0 * score / (k * (k - 1)), rel=1e-12)
    assert res.z == pytest.approx(score / math.sqrt(k * (k - 1) * (2 * k + 5) / 18.0), rel=1e-12)
    assert 0.0 <= res.p <= 1.0


def test_begg_detects_the_small_study_effect_direction():
    """작은 연구일수록(분산이 클수록) 효과가 크면 순위상관은 양수."""
    assert begg_test(ASYM).tau > 0


def test_begg_is_near_zero_for_symmetric_equal_precision_data():
    res = begg_test(SYM)
    assert res.tau == 0.0  # 분산이 모두 같아 모든 쌍이 동점 → 점수 0
    assert res.p == pytest.approx(1.0)


def test_begg_needs_three_studies():
    assert begg_test(SYM[:2]) is None


def test_begg_returns_none_when_standardisation_underflows():
    """한 연구가 전체 정밀도를 사실상 독점하면 v_i - 1/sum(1/v) 가 0으로 반올림된다."""
    lopsided = [Study("huge", 0.3, 1e-300), Study("A", 0.5, 1e300), Study("B", 0.1, 1e300)]
    assert begg_test(lopsided) is None


# --------------------------------------------------------------------------
# trim-and-fill
# --------------------------------------------------------------------------


def test_trim_and_fill_imputes_on_the_left_for_small_study_effect():
    res = trim_and_fill(ASYM)
    assert res.side == "left"
    assert res.k0 > 0
    # 왼쪽을 채웠으니 보정 추정치는 원래보다 작아져야 한다
    assert res.adjusted.estimate < 0.30
    assert len(res.imputed) == res.k0


def test_trim_and_fill_imputed_values_are_exact_mirror_images():
    """부호나 중심을 잘못 쓰면 통과할 수 없도록 채운 값을 직접 재계산한다."""
    from metapool.meta import random_effects

    res = trim_and_fill(ASYM)
    assert res.k0 > 0 and res.side == "left"
    order = sorted(range(len(ASYM)), key=lambda i: ASYM[i].yi)
    kept = [ASYM[i] for i in order[: len(ASYM) - res.k0]]
    mu = random_effects(kept, knapp_hartung=False).estimate
    expected = [2.0 * mu - ASYM[i].yi for i in order[len(ASYM) - res.k0:]]
    assert res.imputed == pytest.approx(expected, rel=1e-12)
    # 거울상이므로 채운 값들은 중심을 기준으로 원본과 반대쪽에 있어야 한다
    for y_new, i in zip(res.imputed, order[len(ASYM) - res.k0:]):
        assert (y_new - mu) == pytest.approx(-(ASYM[i].yi - mu), rel=1e-12)


def test_trim_and_fill_adjusted_pool_includes_the_imputed_studies():
    from metapool.meta import random_effects

    res = trim_and_fill(ASYM)
    assert res.adjusted.k == len(ASYM) + res.k0
    order = sorted(range(len(ASYM)), key=lambda i: ASYM[i].yi)
    filled = list(ASYM) + [
        Study("f%d" % j, y, ASYM[i].vi)
        for j, (y, i) in enumerate(zip(res.imputed, order[len(ASYM) - res.k0:]))
    ]
    assert res.adjusted.estimate == pytest.approx(
        random_effects(filled).estimate, rel=1e-12
    )


def test_trim_and_fill_finds_nothing_in_symmetric_data():
    res = trim_and_fill(SYM)
    assert res.k0 == 0
    assert res.imputed == []
    assert res.adjusted.estimate == pytest.approx(
        run_effect(SYM), rel=1e-12
    )


def run_effect(studies):
    from metapool.meta import random_effects

    return random_effects(list(studies)).estimate


def test_trim_and_fill_mirrored_data_gives_mirrored_answer():
    """부호를 뒤집으면 채우는 쪽도 뒤집혀야 한다 (대칭성)."""
    flipped = [Study(s.label, -s.yi, s.vi) for s in ASYM]
    a, b = trim_and_fill(ASYM), trim_and_fill(flipped)
    assert a.side == "left" and b.side == "right"
    assert a.k0 == b.k0
    assert b.adjusted.estimate == pytest.approx(-a.adjusted.estimate, rel=1e-9)


def test_r0_counts_the_rightmost_run_of_positive_deviations():
    """R0 = (가장 오른쪽 연속된 양의 편차 길이) - 1 — 손으로 센 값과 맞춘다."""
    from metapool.diagnostics import _l0, _r0

    ys = [-3.0, -1.0, 0.5, 2.0, 4.0]
    center = 0.0
    # |편차| 오름차순: 0.5(+), 1.0(-), 2.0(+), 3.0(-), 4.0(+)
    # 가장 오른쪽에서 연속된 양의 편차는 4.0 하나뿐 → gamma=1 → R0 = 0
    assert _r0(ys, center) == 0.0
    # 위쪽 두 개를 모두 양수로 만들면 gamma=2 → R0 = 1
    assert _r0([-3.0, -1.0, 0.5, 3.5, 4.0], center) == 1.0
    # L0 은 양의 편차들의 순위합에서 나온다: 순위 1,3,5 → Tn=9, k=5
    # L0 = (4*9 - 5*6)/(2*5-1) = 6/9
    assert _l0(ys, center) == pytest.approx(6.0 / 9.0, rel=1e-12)


def test_trim_and_fill_r0_and_l0_agree_on_the_side_and_both_impute():
    """두 추정량은 k0 이 다를 수 있지만(정해진 대소 관계는 없다) 방향은 같아야 한다."""
    l0 = trim_and_fill(ASYM, estimator="L0")
    r0 = trim_and_fill(ASYM, estimator="R0")
    assert r0.estimator == "R0" and l0.estimator == "L0"
    assert r0.side == l0.side == "left"
    assert l0.k0 > 0 and r0.k0 > 0
    assert len(r0.imputed) == r0.k0 and len(l0.imputed) == l0.k0
    # 어느 쪽이든 왼쪽을 채웠으니 보정 추정치는 원래보다 작다
    for res in (l0, r0):
        assert res.adjusted.estimate < 0.30


def test_trim_and_fill_rejects_bad_arguments():
    with pytest.raises(ValueError):
        trim_and_fill(ASYM, estimator="X0")
    with pytest.raises(ValueError):
        trim_and_fill(ASYM, side="up")


def test_trim_and_fill_needs_three_studies():
    assert trim_and_fill(SYM[:2]) is None


# --------------------------------------------------------------------------
# 영향력 진단
# --------------------------------------------------------------------------


def test_leave_one_out_reports_i2_and_standardized_residual():
    rows = leave_one_out(ASYM)
    assert len(rows) == len(ASYM)
    assert all(r.i2 is not None and 0 <= r.i2 <= 100 for r in rows)
    assert all(r.std_resid is not None and math.isfinite(r.std_resid) for r in rows)


def test_standardized_residual_matches_definition():
    from metapool.meta import random_effects

    rows = leave_one_out(ASYM)
    target = ASYM[0]
    rest = ASYM[1:]
    p = random_effects(rest)
    expected = (target.yi - p.estimate) / math.sqrt(target.vi + p.tau2 + p.se_model ** 2)
    assert rows[0].std_resid == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------------------------------
# 새 지표: 상관계수
# --------------------------------------------------------------------------


def test_fisher_z_matches_formula():
    yi, vi = fisher_z(0.5, 53)
    assert yi == pytest.approx(0.5493061443340549, rel=1e-12)  # atanh(0.5)
    assert vi == pytest.approx(1.0 / 50.0, rel=1e-12)


def test_fisher_z_rejects_out_of_range_and_tiny_n():
    for bad in (1.0, -1.0, 1.5):
        with pytest.raises(Exception):
            fisher_z(bad, 50)
    with pytest.raises(Exception):
        fisher_z(0.4, 3)


def test_correlation_pipeline_reports_on_r_scale(tmp_path, capsys):
    path = write(tmp_path, "cor.csv", "study,r,n\nA,0.42,88\nB,0.31,120\nC,0.55,64\nD,0.28,210\n")
    records, header, _ = read_table(path)
    assert detect_measure(records, header) == "cor"
    a = run_analysis(records, "cor")
    assert a.scale == "fisherz"
    assert -1.0 < a.back(a.random.estimate) < 1.0
    # 통합값을 손으로 계산해 고정한다 (범위 검사는 거의 아무것도 잡지 못한다)
    zs = [(math.atanh(r), 1.0 / (n - 3.0)) for r, n in
          ((0.42, 88), (0.31, 120), (0.55, 64), (0.28, 210))]
    tau2 = a.random.tau2
    w = [1.0 / (v + tau2) for _, v in zs]
    expected_z = sum(wi * z for wi, (z, _) in zip(w, zs)) / sum(w)
    assert a.random.estimate == pytest.approx(expected_z, rel=1e-12)
    assert a.back(a.random.estimate) == pytest.approx(math.tanh(expected_z), rel=1e-12)
    text = render_text(a)
    assert "Fisher z" in text


def test_back_transform_saturates_instead_of_overflowing():
    assert back_transform(1e6, "log") == math.inf
    assert back_transform(-1e6, "log") == 0.0
    assert back_transform(1e6, "fisherz") == 1.0
    assert back_transform(-1e6, "fisherz") == -1.0
    assert back_transform(1e6, "logit") == 1.0
    assert back_transform(-1e6, "logit") == 0.0
    assert back_transform(2.5, "raw") == 2.5


# --------------------------------------------------------------------------
# 새 지표: 단일군 비율
# --------------------------------------------------------------------------


def test_logit_proportion_matches_formula():
    yi, vi, corrected = logit_proportion(12, 80)
    assert corrected is False
    assert yi == pytest.approx(math.log(12.0 / 68.0), rel=1e-12)
    assert vi == pytest.approx(1.0 / 12.0 + 1.0 / 68.0, rel=1e-12)


def test_logit_proportion_applies_continuity_correction_at_zero():
    yi, vi, corrected = logit_proportion(0, 55, cc=0.5)
    assert corrected is True
    assert yi == pytest.approx(math.log(0.5 / 55.5), rel=1e-12)


def test_logit_proportion_without_correction_is_refused():
    with pytest.raises(Exception):
        logit_proportion(0, 55, cc=0.0)


def test_logit_proportion_rejects_events_over_n():
    with pytest.raises(Exception):
        logit_proportion(60, 50)


def test_proportion_pipeline_has_no_null_line_and_no_significance_claim(tmp_path):
    path = write(tmp_path, "p.csv", "study,events,n\nA,12,80\nB,25,140\nC,3,40\nD,7,55\n")
    records, header, _ = read_table(path)
    assert detect_measure(records, header) == "prop"
    a = run_analysis(records, "prop")
    assert a.scale == "logit"
    assert a.has_null_line is False
    assert 0.0 < a.back(a.random.estimate) < 1.0
    text = render_text(a)
    assert "무효과 검정은 보고하지 않는다" in text
    assert "통계적으로 유의" not in text


# --------------------------------------------------------------------------
# 절대효과 · NNT
# --------------------------------------------------------------------------


def test_absolute_effect_from_odds_ratio_matches_hand_computation():
    log_or = math.log(0.5)
    acr = 0.20
    res = absolute_effect("or", log_or, log_or - 0.2, log_or + 0.2, acr)
    odds = acr / (1 - acr)
    eer = (odds * 0.5) / (1 + odds * 0.5)
    assert res.exp_risk == pytest.approx(eer, rel=1e-12)
    assert res.risk_diff == pytest.approx(eer - acr, rel=1e-12)
    assert res.nnt == pytest.approx(1.0 / abs(eer - acr), rel=1e-12)
    assert res.is_harm is False
    assert res.per_1000 == pytest.approx(1000.0 * (eer - acr), rel=1e-12)


def test_absolute_effect_from_risk_ratio_is_multiplicative():
    res = absolute_effect("rr", math.log(2.0), math.log(1.5), math.log(3.0), 0.10)
    assert res.exp_risk == pytest.approx(0.20, rel=1e-12)
    assert res.is_harm is True
    assert res.nnt == pytest.approx(10.0, rel=1e-12)
    assert res.nnt_low < res.nnt < res.nnt_high  # 위험차가 클수록 NNT 는 작다


def test_absolute_effect_ci_spanning_null_has_no_finite_nnt_bounds():
    res = absolute_effect("rr", math.log(1.05), math.log(0.8), math.log(1.4), 0.20)
    assert res.spans_null is True
    assert res.nnt_low is None and res.nnt_high is None
    assert res.nnt is not None


def test_absolute_effect_refuses_impossible_baseline_risk():
    for bad in (0.0, 1.0, -0.2, 1.5, float("nan")):
        assert absolute_effect("or", 0.3, 0.1, 0.5, bad) is None


def test_absolute_effect_not_defined_for_continuous_measures():
    assert absolute_effect("smd", 0.3, 0.1, 0.5, 0.2) is None


def test_pooled_control_risk_uses_summed_counts():
    studies = [
        Study("A", 0.1, 0.1, extra={"events2": 10.0, "n2": 100.0}),
        Study("B", 0.2, 0.1, extra={"events2": 30.0, "n2": 100.0}),
    ]
    assert pooled_control_risk(studies) == pytest.approx(0.20, rel=1e-12)


def test_pooled_control_risk_is_none_without_count_data():
    assert pooled_control_risk([Study("A", 0.1, 0.1)]) is None


def test_binary_report_includes_nnt_section(tmp_path):
    path = write(
        tmp_path, "b.csv",
        "study,events1,n1,events2,n2\nA,42,80,30,80\nB,55,120,38,118\nC,18,45,12,44\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "or")
    assert a.absolute is not None
    assert a.absolute.baseline_source == "data"
    text = render_text(a)
    ab = a.absolute
    # 사건이 늘어나는 방향이므로 NNH 로 이름 붙어야 한다 (방향이 뒤집히면 실패)
    assert ab.is_harm is True
    assert "NNH(사건이 1건 더 생기는 데 필요한 인원) ≈ %.0f" % ab.nnt in text
    assert "1000명당 %+.0f명" % ab.per_1000 in text
    assert 0 < ab.nnt_low < ab.nnt < ab.nnt_high


def test_user_baseline_risk_overrides_the_data_derived_one(tmp_path):
    path = write(
        tmp_path, "b.csv",
        "study,events1,n1,events2,n2\nA,42,80,30,80\nB,55,120,38,118\nC,18,45,12,44\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "or", baseline_risk=0.05)
    assert a.absolute.baseline_risk == pytest.approx(0.05)
    assert a.absolute.baseline_source == "user"


# --------------------------------------------------------------------------
# 깔때기그림 · CSV 내보내기
# --------------------------------------------------------------------------


def test_funnel_plot_places_exactly_k_points_at_the_right_coordinates(tmp_path):
    """헤더·축 라벨을 빼고 격자만 세어, 점 개수와 위치를 독립적으로 재계산해 맞춘다."""
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.10\nB,0.3,0.20\nC,0.7,0.05\nD,0.1,0.30\n")
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    width, height = 57, 13
    rows = funnel_plot(a, width=width, height=height)
    # rows[0] = 범례, rows[1:1+height] = 격자, 그 뒤 축
    grid = [r.split("│", 1)[1] for r in rows[1:1 + height]]
    assert len(grid) == height
    assert sum(cell == "o" for line in grid for cell in line) == len(a.studies)

    # 좌표를 함수 밖에서 다시 계산 (구현과 같은 정의를 쓰되 손으로 유도한 식)
    ses = [s.sei for s in a.studies]
    se_max = max(ses)
    mu = a.primary.estimate
    z95 = 1.959963984540054
    lo = min(min(s.yi for s in a.studies), mu - z95 * se_max)
    hi = max(max(s.yi for s in a.studies), mu + z95 * se_max)
    pad = 0.05 * (hi - lo)
    lo, hi = lo - pad, hi + pad
    for s in a.studies:
        r = min(height - 1, int(s.sei / se_max * (height - 1) + 0.5))
        c = max(0, min(width - 1, int(round((s.yi - lo) / (hi - lo) * (width - 1)))))
        assert grid[r][c] == "o", "연구 %s 가 (%d,%d) 에 없습니다" % (s.label, r, c)
    # 세로축은 표준오차 순서를 지켜야 한다: 더 정밀한 연구가 더 윗줄에 있다
    rows_of = {}
    for s in a.studies:
        rows_of[s.label] = min(height - 1, int(s.sei / se_max * (height - 1) + 0.5))
    by_se = sorted(a.studies, key=lambda s: s.sei)
    assert [rows_of[s.label] for s in by_se] == sorted(rows_of[s.label] for s in by_se)
    assert all(len(r) < 200 for r in rows)


def test_funnel_plot_marks_overlapping_studies_with_a_count(tmp_path):
    path = write(
        tmp_path, "e.csv",
        "study,effect,se\nA,0.5,0.10\nB,0.5,0.10\nC,0.5,0.10\nD,0.1,0.30\n",
    )
    records, _, _ = read_table(path)
    body = "\n".join(funnel_plot(run_analysis(records, "generic")))
    assert "3" in body  # 같은 칸에 3편이 겹치면 개수를 적는다
    assert body.count("o") <= 2  # 범례의 'o' 를 빼면 실제 단독 점은 1개뿐


def test_funnel_plot_survives_degenerate_inputs(tmp_path):
    """모든 효과크기가 같고 표준오차도 같은 축퇴 자료에서도 그림이 무너지지 않는다."""
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.4,0.1\nB,0.4,0.1\nC,0.4,0.1\n")
    records, _, _ = read_table(path)
    rows = funnel_plot(run_analysis(records, "generic"))
    assert rows and all(isinstance(r, str) and len(r) < 200 for r in rows)


def test_render_csv_has_one_row_per_study_plus_summaries(tmp_path):
    import csv as _csv
    import io as _io

    path = write(
        tmp_path, "e.csv",
        "study,effect,se,subgroup\nA,0.5,0.10,x\nB,0.3,0.20,x\nC,0.7,0.05,y\nD,0.1,0.3,y\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    rows = list(_csv.DictReader(_io.StringIO(render_csv(a))))
    kinds = [r["row_type"] for r in rows]
    assert kinds.count("study") == 4
    assert kinds.count("pooled") == 2
    assert kinds.count("subgroup") == 2
    assert kinds.count("leave_one_out") == 4
    first = rows[0]
    assert float(first["effect"]) == pytest.approx(0.5, rel=1e-12)
    assert float(first["ci_low"]) < 0.5 < float(first["ci_high"])
    assert 0 < float(first["weight_fixed_pct"]) < 100


def test_render_csv_uses_report_scale_for_log_measures(tmp_path):
    path = write(
        tmp_path, "b.csv",
        "study,events1,n1,events2,n2\nA,42,80,30,80\nB,55,120,38,118\nC,18,45,12,44\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "or")
    import csv as _csv
    import io as _io

    rows = list(_csv.DictReader(_io.StringIO(render_csv(a))))
    study_rows = [r for r in rows if r["row_type"] == "study"]
    for r in study_rows:
        assert float(r["effect"]) > 0  # OR 척도이므로 양수
        assert float(r["effect"]) == pytest.approx(
            math.exp(float(r["effect_analysis_scale"])), rel=1e-12
        )


def test_markdown_report_includes_new_sections(tmp_path):
    path = write(
        tmp_path, "b.csv",
        "study,events1,n1,events2,n2\nA,42,80,30,80\nB,55,120,38,118\nC,18,45,12,44\nD,20,60,25,60\n",
    )
    records, _, _ = read_table(path)
    md = render_markdown(run_analysis(records, "or"))
    assert "## 절대효과 · NNT" in md
    assert "Begg 순위상관" in md
    assert "trim-and-fill" in md
    assert "Q-profile" in render_text(run_analysis(records, "or"))


# --------------------------------------------------------------------------
# R1 적대적 리뷰에서 나온 결함의 회귀 시험
# --------------------------------------------------------------------------


def _prop_analysis(tmp_path):
    path = write(
        tmp_path, "prop.csv",
        "study,events,n\nA,12,80\nB,25,140\nC,3,40\nD,31,155\nE,9,72\nF,44,190\n",
    )
    records, _, _ = read_table(path)
    return run_analysis(records, "prop")


def test_proportion_report_never_shows_the_meaningless_null_test(tmp_path):
    """logit = 0 (=50%) 검정은 의미가 없다 — 가장 눈에 띄는 통합 효과 표에서 빼야 한다."""
    a = _prop_analysis(tmp_path)
    text = render_text(a)
    pooled_block = text.split("── 통합 효과")[1].split("── 이질성")[0]
    assert "p <" not in pooled_block and "p =" not in pooled_block
    assert "z =" not in pooled_block and "t(" not in pooled_block
    assert "검정통계량·p값을 보고하지 않습니다" in pooled_block
    md = render_markdown(a)
    md_block = md.split("## 통합 효과")[1].split("## 이질성")[0]
    assert "logit 0 = 50%" in md_block


def test_forest_legend_omits_the_null_line_when_none_is_drawn(tmp_path):
    a = _prop_analysis(tmp_path)
    header = funnel_and_forest_header(a)
    assert "무효과선" not in header
    assert "■ 연구" in header


def funnel_and_forest_header(a):
    from metapool.report import forest_plot

    return forest_plot(a)[0]


def test_forest_legend_keeps_the_null_line_for_ordinary_measures(tmp_path):
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.7,0.15\n")
    records, _, _ = read_table(path)
    assert "무효과선" in funnel_and_forest_header(run_analysis(records, "generic"))


def test_proportion_bias_section_refuses_to_interpret_asymmetry(tmp_path):
    """분산이 효과크기의 함수라 비대칭이 구조적으로 보장된다 — 근거로 쓰면 안 된다."""
    text = render_text(_prop_analysis(tmp_path))
    assert "구조적으로 비대칭" in text
    assert "깔때기그림 비대칭의 근거가 있습니다" not in text


def test_json_egger_note_carries_the_measure_specific_caveat(tmp_path):
    note = _prop_analysis(tmp_path).to_dict()["egger_test"]["note"]
    assert "구조적으로" in note
    path = write(
        tmp_path, "b.csv",
        "study,events1,n1,events2,n2\nA,42,80,30,80\nB,55,120,38,118\nC,18,45,12,44\n",
    )
    records, _, _ = read_table(path)
    assert "위양성" in run_analysis(records, "or").to_dict()["egger_test"]["note"]


def test_csv_keeps_one_meaning_per_column(tmp_path):
    """statistic 열이 행 종류에 따라 z 였다가 표준화 잔차였다가 하면 표를 잘못 만든다."""
    import csv as _csv
    import io as _io

    path = write(
        tmp_path, "e.csv",
        "study,effect,se\nA,0.5,0.10\nB,0.3,0.20\nC,0.7,0.05\nD,0.1,0.30\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    rows = list(_csv.DictReader(_io.StringIO(render_csv(a))))
    loo = [r for r in rows if r["row_type"] == "leave_one_out"]
    assert loo and all(r["statistic"] == "" for r in loo)
    assert all(r["std_residual"] for r in loo)
    for r, expected in zip(loo, a.loo):
        assert float(r["std_residual"]) == pytest.approx(expected.std_resid, rel=1e-12)
        assert float(r["p_value"]) == pytest.approx(expected.p, rel=1e-12)
    pooled = [r for r in rows if r["row_type"] == "pooled"]
    assert all(r["std_residual"] == "" for r in pooled)
    assert float(pooled[0]["statistic"]) == pytest.approx(a.fixed.stat, rel=1e-12)


def test_csv_blanks_the_meaningless_test_columns_for_proportions(tmp_path):
    import csv as _csv
    import io as _io

    rows = list(_csv.DictReader(_io.StringIO(render_csv(_prop_analysis(tmp_path)))))
    for r in rows:
        if r["row_type"] in ("pooled", "leave_one_out", "subgroup", "trim_and_fill"):
            assert r["statistic"] == "" and r["p_value"] == ""


def test_english_sentence_has_no_placeholder_plurals(tmp_path):
    path = write(
        tmp_path, "e.csv",
        "study,effect,se\nA,0.50,0.10\nB,0.30,0.20\nC,0.70,0.05\nD,0.10,0.30\n",
    )
    records, _, _ = read_table(path)
    from metapool.report import sentences

    ko, en = sentences(run_analysis(records, "generic"))
    assert "study(ies)" not in en
    assert "meta-analysis of 4 studies" in en


def test_english_singular_is_used_for_one_imputed_study():
    from metapool.report import _plural

    assert _plural(1, "study", "studies") == "study"
    assert _plural(0, "study", "studies") == "studies"
    assert _plural(2, "study", "studies") == "studies"


def test_trim_and_fill_numbers_stay_out_of_the_manuscript_sentence_below_k10(tmp_path):
    """Egger 를 k<10 에서 빼면서 trim-and-fill 만 넣으면 정책이 어긋난다."""
    from metapool.report import sentences

    small = write(
        tmp_path, "s.csv",
        "study,effect,se\n" + "".join(
            "S%d,%.2f,%.2f\n" % (i, 0.9 - 0.1 * i, 0.30 - 0.03 * i) for i in range(7)
        ),
    )
    records, _, _ = read_table(small)
    a = run_analysis(records, "generic")
    assert a.trimfill is not None
    ko, en = sentences(a)
    if a.trimfill.k0 > 0:
        assert "trim-and-fill" not in ko and "trim-and-fill" not in en


def test_trim_and_fill_numbers_do_appear_once_there_are_ten_studies(tmp_path):
    from metapool.report import sentences

    big = write(
        tmp_path, "b.csv",
        "study,effect,se\n" + "".join(
            "S%d,%.3f,%.3f\n" % (i, 0.95 - 0.07 * i, 0.32 - 0.025 * i) for i in range(12)
        ),
    )
    records, _, _ = read_table(big)
    a = run_analysis(records, "generic")
    if a.trimfill and a.trimfill.k0 > 0:
        ko, en = sentences(a)
        assert "trim-and-fill" in ko and "trim-and-fill" in en


def test_reml_non_convergence_is_reported_not_swallowed():
    """수렴 실패를 조용히 넘기면 잘못된 tau² 가 그대로 논문에 들어간다."""
    from metapool.meta import tau2_reml, tau2_reml_converged

    hetero = [Study("A", 0.10, 0.010), Study("B", 0.55, 0.020),
              Study("C", -0.20, 0.015), Study("D", 0.80, 0.040)]
    value, ok = tau2_reml_converged(hetero)
    assert ok is True and value > 0
    assert value == pytest.approx(tau2_reml(hetero), rel=1e-12)
    # 반복을 1회로 묶으면 수렴했다고 말하면 안 된다
    stunted, ok2 = tau2_reml_converged(hetero, max_iter=1, tol=0.0)
    assert ok2 is False
    assert math.isfinite(stunted)


def test_reml_solves_the_score_equation_where_the_fixed_point_iteration_crawled():
    """연구 하나만 아주 정밀한 배치에서 고정점 반복은 500회로도 해에 못 갔다."""
    from metapool.meta import _reml_score, tau2_reml

    tricky = [
        Study("A", 0.183584, 3.1410505758 ** 2),
        Study("B", 0.791189, 1.0405161267 ** 2),
        Study("C", -0.439893, 0.0194221523 ** 2),
    ]
    t2 = tau2_reml(tricky)
    assert t2 == pytest.approx(0.16231654, rel=1e-6)
    assert abs(_reml_score(tricky, t2)) < 1e-8  # 진짜 정상점이어야 한다


def test_reml_returns_zero_only_when_zero_is_the_boundary_solution():
    from metapool.meta import _reml_score, tau2_reml

    for data in (ASYM, SYM):
        t2 = tau2_reml(data)
        if t2 == 0.0:
            assert _reml_score(data, 0.0) <= 0.0
        else:
            assert abs(_reml_score(data, t2)) < 1e-6


def test_reml_non_convergence_surfaces_as_a_user_warning(monkeypatch, tmp_path):
    import metapool.analysis as analysis_mod

    monkeypatch.setattr(
        "metapool.meta.tau2_reml_converged",
        lambda studies, **kw: (0.05, False),
    )
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.1\nB,0.9,0.2\nC,0.1,0.15\n")
    records, _, _ = read_table(path)
    a = analysis_mod.run_analysis(records, "generic", tau2_method="REML")
    assert any("수렴하지 않아" in w for w in a.warnings)


# --------------------------------------------------------------------------
# 외부 기준·손계산 앵커 (구현을 베끼지 않는 검증)
# --------------------------------------------------------------------------


def test_reml_matches_the_closed_form_for_two_equal_variance_studies():
    """k=2·등분산이면 REML tau^2 = d^2/2 - v 라는 닫힌 해가 있다."""
    from metapool.meta import tau2_reml

    v, d = 0.01, 0.5
    pair = [Study("A", 0.0, v), Study("B", d, v)]
    assert tau2_reml(pair) == pytest.approx(d * d / 2.0 - v, rel=1e-9)
    # 흩어짐이 분산보다 작으면 0 으로 절단된다
    tiny = [Study("A", 0.0, 0.5), Study("B", 0.05, 0.5)]
    assert tau2_reml(tiny) == 0.0


def test_reml_maximizes_the_restricted_log_likelihood():
    """구현과 무관하게 제한로그가능도를 직접 최대화해 같은 값이 나오는지 본다."""
    from metapool.meta import tau2_reml

    def rell(t2):
        w = [1.0 / (s.vi + t2) for s in ASYM]
        sw = math.fsum(w)
        mu = math.fsum(wi * s.yi for wi, s in zip(w, ASYM)) / sw
        return (-0.5 * math.fsum(math.log(s.vi + t2) for s in ASYM)
                - 0.5 * math.log(sw)
                - 0.5 * math.fsum(wi * (s.yi - mu) ** 2 for wi, s in zip(w, ASYM)))

    lo, hi = 0.0, 5.0
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(300):  # 황금분할 탐색
        a1, b1 = hi - phi * (hi - lo), lo + phi * (hi - lo)
        if rell(a1) < rell(b1):
            lo = a1
        else:
            hi = b1
    assert tau2_reml(ASYM) == pytest.approx(0.5 * (lo + hi), rel=1e-5)


def test_sidik_jonkman_stays_positive_where_dersimonian_laird_collapses():
    """DL 이 0 으로 절단되는 약한 이질성 자료에서 SJ 는 유의미하게 양수다."""
    from metapool.meta import tau2_dersimonian_laird, tau2_sidik_jonkman

    mild = [Study("A", 0.30, 0.01), Study("B", 0.34, 0.02), Study("C", 0.26, 0.03)]
    assert tau2_dersimonian_laird(mild) == 0.0
    assert tau2_sidik_jonkman(mild) > 1e-6


def test_begg_against_hand_enumerated_pairs():
    """4편 자료의 일치/불일치 쌍을 손으로 세어 tau 와 z 를 고정한다."""
    studies = [
        Study("A", 0.10, 0.01),
        Study("B", 0.30, 0.04),
        Study("C", 0.50, 0.09),
        Study("D", 0.70, 0.16),
    ]
    res = begg_test(studies)
    # 분산이 커질수록 효과가 커지는 완전 단조 자료 → 모든 6쌍이 일치
    # tau_a = 2*6/(4*3) = 1.0,  Var = 4*3*13/18 = 8.6667,  z = 6/sqrt(8.6667)
    assert res.k == 4 and res.score == 6
    assert res.tau == pytest.approx(1.0, rel=1e-12)
    assert res.z == pytest.approx(6.0 / math.sqrt(4 * 3 * 13 / 18.0), rel=1e-12)
    assert res.z == pytest.approx(2.03810, abs=1e-4)
    # k=4 에서 완전 일치일 확률은 순열 24개 중 2개 = 1/12 — 정확검정의 최소 p값이다.
    # 정규근사(0.0415)를 쓰면 "p < .05" 라는 불가능한 결론이 나온다.
    assert res.method == "exact"
    assert res.p == pytest.approx(1.0 / 12.0, rel=1e-12)
    assert res.p > 0.05


def test_begg_exact_p_matches_brute_force_enumeration():
    """역위 분포 DP 를 전수 순열과 대조한다."""
    import itertools

    from metapool.diagnostics import _kendall_exact_p

    for k in (3, 4, 5, 6):
        m = k * (k - 1) // 2
        counts = {}
        for perm in itertools.permutations(range(k)):
            d = sum(1 for i in range(k) for j in range(i + 1, k) if perm[i] > perm[j])
            counts[m - 2 * d] = counts.get(m - 2 * d, 0) + 1
        total = sum(counts.values())
        for s in sorted(counts):
            brute = sum(n for sv, n in counts.items() if abs(sv) >= abs(s)) / total
            assert _kendall_exact_p(k, s) == pytest.approx(brute, rel=1e-12), (k, s)


def test_begg_falls_back_to_the_normal_approximation_with_ties():
    """동점이 있으면 정확분포가 성립하지 않는다 — 그 사실을 숨기지 않는다."""
    tied = [
        Study("A", 0.10, 0.02),
        Study("B", 0.50, 0.02),
        Study("C", 0.30, 0.02),
        Study("D", 0.70, 0.05),
    ]
    res = begg_test(tied)
    assert res.method == "normal"


def test_begg_exact_p_is_never_smaller_than_the_smallest_attainable_value():
    """k편으로 낼 수 있는 최소 p값(2/k!)보다 작은 p가 나오면 안 된다."""
    import math as _m

    from metapool.diagnostics import _kendall_exact_p

    for k in range(3, 9):
        m = k * (k - 1) // 2
        assert _kendall_exact_p(k, m) == pytest.approx(2.0 / _m.factorial(k), rel=1e-12)


def test_begg_sign_flips_with_the_data():
    studies = [
        Study("A", 0.70, 0.01),
        Study("B", 0.50, 0.04),
        Study("C", 0.30, 0.09),
        Study("D", 0.10, 0.16),
    ]
    assert begg_test(studies).tau == pytest.approx(-1.0, rel=1e-12)


# --------------------------------------------------------------------------
# 절대효과: 위험차 지표와 축퇴 경로
# --------------------------------------------------------------------------


def test_absolute_effect_for_risk_difference_is_additive():
    res = absolute_effect("rd", 0.10, 0.02, 0.18, 0.30)
    assert res.exp_risk == pytest.approx(0.40, rel=1e-12)
    assert res.risk_diff == pytest.approx(0.10, rel=1e-12)
    assert res.nnt == pytest.approx(10.0, rel=1e-12)
    assert res.per_1000 == pytest.approx(100.0, rel=1e-12)
    assert res.is_harm is True
    assert res.nnt_low == pytest.approx(1 / 0.18, rel=1e-12)
    assert res.nnt_high == pytest.approx(1 / 0.02, rel=1e-12)


def test_absolute_effect_with_zero_risk_difference_has_no_nnt():
    from metapool.clinical import format_absolute

    res = absolute_effect("rd", 0.0, -0.05, 0.05, 0.30)
    assert res.risk_diff == 0.0
    assert res.nnt is None
    assert res.spans_null is True
    assert "위험차가 0이라 NNT를 정의할 수 없습니다." in format_absolute(res)


def test_absolute_effect_clamps_instead_of_overflowing():
    """극단적인 로그 효과크기에서도 위험은 0~1 을 벗어나지 않는다."""
    for measure in ("or", "rr"):
        res = absolute_effect(measure, 1e5, 1e4, 1e6, 0.30)
        assert 0.0 <= res.exp_risk <= 1.0
        assert math.isfinite(res.risk_diff)
    res = absolute_effect("rd", 5.0, 4.0, 6.0, 0.30)
    assert res.exp_risk == 1.0


def test_absolute_effect_rejects_a_measure_it_cannot_convert():
    from metapool.clinical import _exp_risk

    with pytest.raises(ValueError):
        _exp_risk("smd", 0.3, 0.2)


# --------------------------------------------------------------------------
# 주입 방어
# --------------------------------------------------------------------------


_HOSTILE = "=cmd|' /C calc'!A0"


def _hostile_analysis(tmp_path):
    path = write(
        tmp_path, "inj.csv",
        'study,effect,se,subgroup\n'
        '"%s",0.5,0.10,x\n'
        '"+1+1",0.7,0.05,"@SUM(A1)"\n'
        '"-2-2",0.1,0.30,"=HYPERLINK(""http://evil"")"\n'
        '"[click](javascript:alert(1))",0.4,0.12,x\n' % _HOSTILE,
    )
    records, _, _ = read_table(path)
    return run_analysis(records, "generic")


def test_csv_export_neutralises_excel_formula_cells(tmp_path):
    """--csv 는 엑셀로 열라고 만든 출력이다 — 수식으로 실행되면 안 된다."""
    import csv as _csv
    import io as _io

    body = render_csv(_hostile_analysis(tmp_path))
    for row in _csv.reader(_io.StringIO(body)):
        for cellv in row:
            assert cellv[:1] not in ("=", "+", "@", "\t", "\r"), cellv
            if cellv[:1] == "-":
                float(cellv)  # 음수만 허용 — 텍스트가 '-' 로 시작하면 안 된다
    assert "'" + _HOSTILE in body  # 값 자체는 보존한다


def test_markdown_export_neutralises_link_injection(tmp_path):
    md = render_markdown(_hostile_analysis(tmp_path))
    assert "[click](javascript:alert(1))" not in md
    assert "\\[click\\]" in md


def test_header_names_are_sanitised_before_being_echoed(tmp_path):
    """헤더도 사용자 입력이다 — ANSI 이스케이프가 살아나가면 숫자를 덮어쓸 수 있다."""
    from metapool.io_csv import TableError

    path = tmp_path / "hdr.csv"
    path.write_bytes(b"study,\x1b[31m es,se\nA,0.5,0.1\nB,0.3,0.2\n")
    records, _, warns = read_table(str(path))
    assert "\x1b" not in "".join(warns)
    with pytest.raises(TableError) as exc:
        read_table(str(path), label_column="없는열")
    assert "\x1b" not in str(exc.value)


def test_header_echo_is_bounded_so_identifiers_do_not_flood_the_screen(tmp_path):
    """잘못 저장된 임상 CSV 는 헤더에 식별자가 통째로 들어가기도 한다."""
    from metapool.io_csv import TableError, detect_measure

    huge = "환자_홍길동_주민번호_900101-1234567_" * 3000
    path = write(tmp_path, "big.csv", "studyX,%s\nA,1\nB,2\n" % huge)
    records, header, _ = read_table(path)
    with pytest.raises(TableError) as exc:
        detect_measure(records, header)
    assert len(str(exc.value)) < 3000
    assert huge not in str(exc.value)


def test_many_columns_are_summarised_not_listed(tmp_path):
    from metapool.io_csv import TableError, detect_measure

    header = ",".join("col%d" % i for i in range(200))
    path = write(tmp_path, "wide.csv", header + "\n" + ",".join("1" for _ in range(200)) + "\n")
    records, hdr, _ = read_table(path)
    with pytest.raises(TableError) as exc:
        detect_measure(records, hdr)
    assert "외 180개" in str(exc.value)


# --------------------------------------------------------------------------
# CSV 요약 행
# --------------------------------------------------------------------------


def test_csv_export_contains_prediction_and_trim_fill_rows(tmp_path):
    import csv as _csv
    import io as _io

    path = write(
        tmp_path, "a.csv",
        "study,effect,se\n" + "".join(
            "S%d,%.3f,%.3f\n" % (i, 0.95 - 0.07 * i, 0.32 - 0.025 * i) for i in range(10)
        ),
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    rows = {r["row_type"]: r for r in _csv.DictReader(_io.StringIO(render_csv(a)))}
    assert float(rows["prediction"]["ci_low"]) == pytest.approx(a.pred[0], rel=1e-12)
    assert float(rows["prediction"]["ci_high"]) == pytest.approx(a.pred[1], rel=1e-12)
    if a.trimfill and a.trimfill.k0 > 0:
        tf = rows["trim_and_fill"]
        assert int(tf["k"]) == len(a.studies) + a.trimfill.k0
        assert float(tf["effect"]) == pytest.approx(a.trimfill.adjusted.estimate, rel=1e-12)
