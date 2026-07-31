"""ANCOVA: model arithmetic, assumption checks, and degenerate inputs.

The reference numbers in ``test_matches_statsmodels_reference`` were produced
with ``statsmodels`` 0.14 (``ols("y ~ C(arm) + base + C(site)")`` with a
Treatment reference, ``anova_lm(typ=2)`` for the group term, and the emmeans
convention for the adjusted means).  statwise itself is standard-library only,
so the values are pinned here rather than recomputed at test time.
"""

import json
import math

import pytest

from statwise import linalg
from statwise.ancova import AncovaRecord, run_ancova
from statwise.analyze import EquivalenceSpec
from statwise.report import (ancova_to_dict, render_ancova_json,
                             render_ancova_text, render_csv)


# --------------------------------------------------------------------------
# the least-squares core
# --------------------------------------------------------------------------

def test_lstsq_recovers_exact_linear_relationship():
    # y = 3 + 2*x1 - 1*x2 exactly -> coefficients exact, RSS zero
    xs = [(1.0, 0.0), (2.0, 1.0), (3.0, 4.0), (4.0, 2.0), (5.0, 7.0)]
    x = [[1.0, a, b] for a, b in xs]
    y = [3.0 + 2.0 * a - 1.0 * b for a, b in xs]
    fit = linalg.lstsq(x, y)
    assert fit.beta[0] == pytest.approx(3.0, abs=1e-9)
    assert fit.beta[1] == pytest.approx(2.0, abs=1e-9)
    assert fit.beta[2] == pytest.approx(-1.0, abs=1e-9)
    assert fit.rss == pytest.approx(0.0, abs=1e-18)


def test_lstsq_simple_regression_matches_closed_form():
    x_vals = [1.0, 2.0, 3.0, 4.0, 6.0, 7.0]
    y = [2.0, 3.5, 3.0, 5.5, 6.0, 8.5]
    n = len(y)
    mx = sum(x_vals) / n
    my = sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x_vals)
    sxy = sum((v - mx) * (w - my) for v, w in zip(x_vals, y))
    slope = sxy / sxx
    intercept = my - slope * mx
    fit = linalg.lstsq([[1.0, v] for v in x_vals], y)
    assert fit.beta[0] == pytest.approx(intercept, rel=1e-12)
    assert fit.beta[1] == pytest.approx(slope, rel=1e-12)
    # SE(slope) = sigma / sqrt(Sxx)
    sigma2 = fit.rss / (n - 2)
    se = math.sqrt(sigma2 * fit.xtx_inv[1][1])
    assert se == pytest.approx(math.sqrt(sigma2 / sxx), rel=1e-12)


def test_lstsq_rejects_collinear_columns():
    x = [[1.0, 1.0, 2.0], [1.0, 2.0, 4.0], [1.0, 3.0, 6.0], [1.0, 4.0, 8.0],
         [1.0, 5.0, 10.0]]
    with pytest.raises(linalg.RankDeficientError):
        linalg.lstsq(x, [1.0, 2.0, 3.0, 4.0, 5.5])


def test_lstsq_rejects_constant_covariate_column():
    x = [[1.0, 7.0], [1.0, 7.0], [1.0, 7.0], [1.0, 7.0]]
    with pytest.raises(linalg.RankDeficientError):
        linalg.lstsq(x, [1.0, 2.0, 3.0, 4.0])


def test_lstsq_needs_more_rows_than_columns():
    with pytest.raises(ValueError, match="자유도"):
        linalg.lstsq([[1.0, 1.0], [1.0, 2.0]], [1.0, 2.0])


def test_lstsq_is_stable_across_wild_column_scaling():
    """A ng-scale covariate beside a count-scale one must not look singular."""
    xs = [(1e-9 * i, 1e6 * (i % 5)) for i in range(1, 21)]
    x = [[1.0, a, b] for a, b in xs]
    y = [1.0 + 3e9 * a - 2e-6 * b for a, b in xs]
    fit = linalg.lstsq(x, y)
    assert fit.beta[0] == pytest.approx(1.0, rel=1e-6)
    assert fit.beta[1] == pytest.approx(3e9, rel=1e-6)
    assert fit.beta[2] == pytest.approx(-2e-6, rel=1e-6)


# --------------------------------------------------------------------------
# the model itself, pinned against statsmodels
# --------------------------------------------------------------------------

def _reference_records():
    """The dataset the pinned statsmodels numbers were computed from."""
    y = [16.2, 13.1, 19.4, 11.0, 14.8, 21.3, 12.5, 17.7, 10.4, 15.9,
         18.6, 13.8, 20.1, 12.2, 16.5, 22.4, 11.7, 18.0, 9.8, 14.3,
         17.1, 12.9, 19.9, 10.9, 15.2, 20.8, 13.3, 16.8, 11.4, 18.9]
    base = [15.0, 12.0, 18.0, 10.0, 14.0, 20.0, 11.0, 17.0, 9.0, 15.0,
            18.0, 13.0, 19.0, 11.0, 16.0, 21.0, 10.0, 17.0, 8.0, 14.0,
            16.0, 12.0, 19.0, 10.0, 15.0, 20.0, 13.0, 16.0, 11.0, 18.0]
    arms = ["drug", "placebo", "dose2"] * 10
    sites = ["A", "B"] * 15
    return [AncovaRecord(a, yy, (b,), (s,))
            for a, yy, b, s in zip(arms, y, base, sites)]


def _no_site(records=None):
    """The same records with the site factor dropped (baseline covariate only)."""
    return [AncovaRecord(r.group, r.y, r.covariates)
            for r in (records if records is not None else _reference_records())]


def test_matches_statsmodels_reference():
    res = run_ancova(_reference_records(), ["base"], ["site"],
                     outcome="isi", reference="placebo")
    # anova_lm(typ=2) group row
    assert res.f_statistic == pytest.approx(0.7215024068, rel=1e-8)
    assert res.df1 == 2.0 and res.df2 == 25.0
    assert res.pvalue == pytest.approx(0.4958668296, rel=1e-8)
    # model summary
    assert res.r_squared == pytest.approx(0.9885926799, rel=1e-9)
    assert res.adj_r_squared == pytest.approx(0.9867675087, rel=1e-9)
    assert res.sigma == pytest.approx(0.4144419714, rel=1e-9)
    # baseline slope and its SE
    slope = next(e for e in res.covariate_effects if e.name == "base")
    assert slope.coef == pytest.approx(0.9818791946, rel=1e-8)
    assert slope.se == pytest.approx(0.0213316489, rel=1e-8)
    # adjusted (LS) means, emmeans convention
    adj = {a.label: a.adjusted for a in res.adjusted_means}
    assert adj["drug"] == pytest.approx(15.6727516779, rel=1e-9)
    assert adj["dose2"] == pytest.approx(15.5672483221, rel=1e-9)
    assert adj["placebo"] == pytest.approx(15.4500000000, rel=1e-9)


def test_omnibus_f_equals_t_squared_for_two_groups():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo")
    c = res.contrasts[0]
    t = c.diff / c.se
    assert res.f_statistic == pytest.approx(t * t, rel=1e-9)
    assert res.pvalue == pytest.approx(c.pvalue_raw, rel=1e-9)


def test_contrast_direction_follows_reference():
    recs = _no_site()
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo")
    assert res.reference == "placebo"
    # every contrast that mentions the reference puts it second
    ref_pairs = [c for c in res.contrasts if "placebo" in (c.a, c.b)]
    assert ref_pairs and all(c.b == "placebo" for c in ref_pairs)
    adj = {a.label: a.adjusted for a in res.adjusted_means}
    for c in ref_pairs:
        assert c.diff == pytest.approx(adj[c.a] - adj[c.b], rel=1e-9)


def test_reference_choice_does_not_change_the_model():
    recs = _no_site()
    a = run_ancova(recs, ["base"], outcome="isi", reference="placebo")
    b = run_ancova(recs, ["base"], outcome="isi", reference="drug")
    assert a.f_statistic == pytest.approx(b.f_statistic, rel=1e-12)
    assert a.pvalue == pytest.approx(b.pvalue, rel=1e-12)
    assert a.r_squared == pytest.approx(b.r_squared, rel=1e-12)
    adj_a = {m.label: m.adjusted for m in a.adjusted_means}
    adj_b = {m.label: m.adjusted for m in b.adjusted_means}
    for label in adj_a:
        assert adj_a[label] == pytest.approx(adj_b[label], rel=1e-9)


def test_no_covariate_ancova_reproduces_one_way_anova():
    """With no covariates the model IS a one-way ANOVA — check against it."""
    from statwise.tests_stat import one_way_anova
    groups = {"a": [4.0, 5.0, 6.0, 7.0], "b": [7.0, 8.0, 6.5, 9.0],
              "c": [5.0, 5.5, 4.0, 6.0]}
    recs = [AncovaRecord(g, v) for g, vals in groups.items() for v in vals]
    res = run_ancova(recs, [], [], outcome="y")
    ref = one_way_anova(list(groups.values()))
    assert res.f_statistic == pytest.approx(ref.statistic, rel=1e-10)
    assert res.pvalue == pytest.approx(ref.pvalue, rel=1e-10)
    # with no covariate, adjusted means are just the group means
    for m in res.adjusted_means:
        assert m.adjusted == pytest.approx(m.raw_mean, rel=1e-10)


def test_adjustment_beats_the_unadjusted_test_when_baseline_predicts():
    """The whole point: a predictive baseline shrinks the SE of the difference."""
    from statwise.analyze import analyze
    base = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 11.0, 13.0, 15.0, 17.0]
    recs, groups = [], {"drug": [], "placebo": []}
    for i, b in enumerate(base):
        arm = "drug" if i % 2 == 0 else "placebo"
        y = b + (-3.0 if arm == "drug" else 0.0) + (0.3 if i % 4 else -0.3)
        recs.append(AncovaRecord(arm, y, (b,)))
        groups[arm].append(y)
    adjusted = run_ancova(recs, ["base"], outcome="y", reference="placebo")
    plain = analyze([("drug", groups["drug"]), ("placebo", groups["placebo"])],
                    test="student")
    plain_se = abs(plain.mean_diff) / abs(plain.statistic)
    assert adjusted.contrasts[0].se < plain_se
    assert adjusted.pvalue < plain.pvalue


def test_slope_homogeneity_detects_an_interaction():
    """Opposite baseline slopes in the two arms must trip the check."""
    recs = []
    for i, b in enumerate([8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]):
        recs.append(AncovaRecord("drug", 2.0 * b, (b,)))
        recs.append(AncovaRecord("placebo", -2.0 * b + 60.0, (b,)))
    res = run_ancova(recs, ["base"], outcome="y", reference="placebo")
    assert res.slopes.homogeneous is False
    assert res.slopes.pvalue < 0.001
    assert any("기울기 동질성 가정이 기각" in w for w in res.warnings)


def test_slope_check_skipped_without_numeric_covariate():
    sites = ["s1", "s2", "s1", "s2"]
    recs = [AncovaRecord("a", v, (), (s,))
            for v, s in zip((1.0, 2.0, 3.0, 4.0), sites)]
    recs += [AncovaRecord("b", v, (), (s,))
             for v, s in zip((2.0, 3.5, 4.0, 5.0), sites)]
    res = run_ancova(recs, [], ["site"], outcome="y")
    assert res.slopes.homogeneous is None
    assert "기울기 동질성 검정을 생략" in res.slopes.note


def test_residual_checks_flag_a_heteroscedastic_fit():
    recs = [AncovaRecord("tight", v, (float(i),))
            for i, v in enumerate([5.0, 5.1, 4.9, 5.0, 5.1, 4.9, 5.0, 5.05])]
    recs += [AncovaRecord("loose", v, (float(i),))
             for i, v in enumerate([1.0, 9.0, 2.0, 8.5, 0.5, 9.5, 1.5, 8.0])]
    res = run_ancova(recs, ["idx"], outcome="y", reference="tight")
    assert res.resid_levene_p is not None and res.resid_levene_p < 0.05
    assert any("잔차의 분산이 서로 다릅니다" in w for w in res.warnings)


def test_always_warns_about_covariate_timing():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi")
    assert any("무작위배정 전에 측정된" in w for w in res.warnings)
    assert any("emmeans" in w for w in res.warnings)


def test_holm_and_bh_corrections_differ_and_are_bounded():
    recs = _no_site()
    holm = run_ancova(recs, ["base"], outcome="isi", correction="holm")
    bh = run_ancova(recs, ["base"], outcome="isi", correction="bh")
    for h, b in zip(holm.contrasts, bh.contrasts):
        assert b.pvalue_adj <= h.pvalue_adj + 1e-12
        assert h.pvalue_raw <= h.pvalue_adj + 1e-12
        assert 0.0 <= b.pvalue_adj <= 1.0


def test_bad_correction_is_rejected():
    with pytest.raises(ValueError, match="holm"):
        run_ancova(_no_site(), ["base"], correction="bonferroni")


# --------------------------------------------------------------------------
# degenerate and hostile inputs
# --------------------------------------------------------------------------

def test_rejects_single_group():
    recs = [AncovaRecord("only", float(v), (float(v) / 2,)) for v in range(6)]
    with pytest.raises(ValueError, match="그룹이 2개 이상"):
        run_ancova(recs, ["base"])


def test_rejects_group_with_one_observation():
    recs = [AncovaRecord("a", 1.0, (1.0,)), AncovaRecord("a", 2.0, (2.0,)),
            AncovaRecord("a", 3.5, (2.5,)), AncovaRecord("b", 3.0, (3.0,))]
    with pytest.raises(ValueError, match="관측치가 2개 이상"):
        run_ancova(recs, ["base"])


def test_rejects_unknown_reference():
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        run_ancova(_no_site(), ["base"], reference="vehicle")


def test_rejects_nan_outcome():
    recs = [AncovaRecord("a", float("nan"), (1.0,)),
            AncovaRecord("a", 2.0, (2.0,)), AncovaRecord("b", 3.0, (3.0,)),
            AncovaRecord("b", 4.0, (4.0,))]
    with pytest.raises(ValueError, match="NaN"):
        run_ancova(recs, ["base"])


def test_rejects_ragged_records():
    recs = [AncovaRecord("a", 1.0, (1.0,)), AncovaRecord("a", 2.0, ()),
            AncovaRecord("b", 3.0, (3.0,)), AncovaRecord("b", 4.0, (4.0,))]
    with pytest.raises(ValueError, match="공변량/보정인자 개수"):
        run_ancova(recs, ["base"])


def test_rejects_single_level_factor():
    recs = [AncovaRecord("a", float(i), (), ("only",)) for i in range(4)]
    recs += [AncovaRecord("b", float(i) + 2, (), ("only",)) for i in range(4)]
    with pytest.raises(ValueError, match="수준이 1개뿐"):
        run_ancova(recs, [], ["site"])


def test_rejects_covariate_that_is_an_alias_of_the_group():
    """A "covariate" that is constant within each arm is the arm itself."""
    recs = [AncovaRecord("a", float(i), (0.0,)) for i in range(1, 6)]
    recs += [AncovaRecord("b", float(i) + 3, (1.0,)) for i in range(1, 6)]
    with pytest.raises(ValueError, match="선형종속"):
        run_ancova(recs, ["arm_code"])


def test_rejects_more_parameters_than_observations():
    recs = [AncovaRecord("a", 1.0, (1.0, 2.0, 3.0)),
            AncovaRecord("a", 2.0, (2.0, 1.0, 5.0)),
            AncovaRecord("b", 3.0, (3.0, 4.0, 1.0)),
            AncovaRecord("b", 4.0, (4.0, 3.0, 2.0))]
    with pytest.raises(ValueError, match="모수"):
        run_ancova(recs, ["c1", "c2", "c3"])


def test_duplicate_covariate_columns_are_rejected_not_silently_fitted():
    recs = [AncovaRecord("a", float(i), (float(i), float(i)))
            for i in range(1, 7)]
    recs += [AncovaRecord("b", float(i) + 2, (float(i), float(i)))
             for i in range(1, 7)]
    with pytest.raises(ValueError, match="선형종속"):
        run_ancova(recs, ["base", "base_copy"])


def test_perfect_fit_is_flagged_not_celebrated():
    """R^2 = 1 means the covariate contains the outcome, not a great trial."""
    recs = [AncovaRecord("a", 2.0 * b, (b,)) for b in (1.0, 2.0, 3.0, 4.0)]
    recs += [AncovaRecord("b", 2.0 * b + 5.0, (b,)) for b in (1.0, 2.0, 3.0, 4.0)]
    res = run_ancova(recs, ["base"], outcome="y")
    assert res.r_squared == pytest.approx(1.0, abs=1e-12)
    assert any("잔차가 사실상 0" in w for w in res.warnings)
    render_ancova_text(res)
    json.loads(render_ancova_json(res))     # must stay JSON-valid


def test_exactly_zero_residual_f_stays_reportable():
    """The 0/0 branch: F is inf with p = 0, and JSON must survive it."""
    from statwise.ancova import _nested_f
    assert _nested_f(4.0, 0.0, 1, 3) == (math.inf, 0.0)
    f, p = _nested_f(0.0, 0.0, 1, 3)
    assert f != f and p != p


def test_long_and_control_character_labels_are_sanitised():
    long_label = "arm" + "x" * 200
    recs = [AncovaRecord(long_label, float(i), (float(i) * 0.5,))
            for i in range(1, 6)]
    recs += [AncovaRecord("ctrl\x07\n", float(i) + 2.0, (float(i) * 0.5,))
             for i in range(1, 6)]
    res = run_ancova(recs, ["base"])
    for label in res.group_labels:
        assert len(label) <= 41
        assert not any(ord(ch) < 32 for ch in label)


# --------------------------------------------------------------------------
# equivalence on the adjusted difference
# --------------------------------------------------------------------------

def test_tost_on_the_adjusted_difference_uses_the_model_df():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo",
                     equivalence=EquivalenceSpec(margin=(-2.0, 2.0)))
    assert res.equivalence is not None
    assert res.equivalence.kind == "tost"
    assert res.equivalence.df == res.df2
    assert res.equivalence.diff == pytest.approx(res.contrasts[0].diff,
                                                 rel=1e-12)
    assert res.equivalence.se == pytest.approx(res.contrasts[0].se, rel=1e-12)
    assert res.equivalence.model == "ancova"


def test_equivalence_skipped_with_three_arms():
    res = run_ancova(_no_site(), ["base"], outcome="isi",
                     equivalence=EquivalenceSpec(margin=(-2.0, 2.0)))
    assert res.equivalence is None
    assert any("두 그룹 비교에서만" in w for w in res.warnings)


def test_noninferiority_on_the_adjusted_difference():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo",
                     equivalence=EquivalenceSpec(ni_margin=3.0,
                                                 ni_direction="higher_is_better"))
    assert res.equivalence is not None
    assert res.equivalence.kind == "noninferiority"
    assert res.equivalence.concluded is True


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_text_report_has_every_section_and_the_direction():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    text = render_ancova_text(res)
    for section in ("[1] 모형", "[2] 보정평균", "[3] 그룹 효과",
                    "[4] 보정된 그룹 차이", "[5] 공변량 효과", "[6] 가정 점검",
                    "논문용 문장"):
        assert section in text
    assert "다른 군 − placebo" in text
    # every *table* row must stay readable on one line (the warning block and
    # the paste-ready sentence are prose and are allowed to be long)
    table = text.split("[!] 주의")[0]
    assert all(len(line) < 200 for line in table.splitlines())


def test_json_round_trips_and_never_emits_nan():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi")
    payload = json.loads(render_ancova_json(res))
    assert payload["analysis"] == "ancova"
    assert payload["covariates"] == ["base"] and payload["factors"] == ["site"]
    assert len(payload["adjusted_means"]) == 3
    assert len(payload["contrasts"]) == 3
    assert "NaN" not in render_ancova_json(res)
    assert payload["sentence"]


def test_dict_and_json_agree():
    res = run_ancova(_no_site(), ["base"], outcome="isi")
    assert json.loads(render_ancova_json(res)) == json.loads(
        json.dumps(ancova_to_dict(res), ensure_ascii=False, allow_nan=False))


def test_csv_rows_cover_means_contrasts_and_covariates():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    text = render_csv(res)
    kinds = [line.split(",")[1] for line in text.splitlines()[1:]]
    assert kinds.count("ancova") == 1
    assert kinds.count("adjusted-mean") == 3
    assert kinds.count("adjusted-contrast") == 3
    assert kinds.count("covariate") == 2       # base + site=B


def test_sentence_reports_the_two_arm_difference_with_its_ci():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo")
    sentence = ancova_to_dict(res)["sentence"]
    assert "보정된 평균차(drug − placebo)" in sentence
    assert "95% CI" in sentence
    assert "ANCOVA" in sentence


# --------------------------------------------------------------------------
# regression tests for the arithmetic an adversarial review found untested:
# every number below was cross-checked against statsmodels/emmeans, and each
# assertion was verified to fail under a plausible single-line mutation of the
# code it covers.
# --------------------------------------------------------------------------

def test_every_interval_is_exactly_tcrit_times_its_own_se():
    """Guards the two-sided t quantile and the `estimate ± tcrit*se` form.

    Without this, `t_ppf(1-alpha, df)` instead of `t_ppf(1-alpha/2, df)` — a
    silently one-sided interval — passes the whole suite.
    """
    from statwise.special import t_ppf
    for alpha in (0.05, 0.01, 0.10):
        res = run_ancova(_reference_records(), ["base"], ["site"],
                         outcome="isi", reference="placebo", alpha=alpha)
        tcrit = t_ppf(1.0 - alpha / 2.0, res.df2)
        for m in res.adjusted_means:
            assert m.ci[0] == pytest.approx(m.adjusted - tcrit * m.se, rel=1e-12)
            assert m.ci[1] == pytest.approx(m.adjusted + tcrit * m.se, rel=1e-12)
        for c in res.contrasts:
            assert c.ci[0] == pytest.approx(c.diff - tcrit * c.se, rel=1e-12)
            assert c.ci[1] == pytest.approx(c.diff + tcrit * c.se, rel=1e-12)
        for e in res.covariate_effects:
            assert e.ci[0] == pytest.approx(e.coef - tcrit * e.se, rel=1e-12)
            assert e.ci[1] == pytest.approx(e.coef + tcrit * e.se, rel=1e-12)


def test_adjusted_mean_standard_errors_are_pinned():
    """LS-mean SEs depend on the OFF-diagonal of (X'X)^-1 and on sigma.

    Pinning only the slope's SE leaves the unscaling of xtx_inv and the
    `sigma2 *` factor in the LS-mean variance completely unguarded.
    """
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    se = {m.label: m.se for m in res.adjusted_means}
    assert se["drug"] == pytest.approx(0.1313355285, rel=1e-8)
    assert se["dose2"] == pytest.approx(0.1313355285, rel=1e-8)
    assert se["placebo"] == pytest.approx(0.1310580588, rel=1e-8)


def test_non_reference_contrast_value_and_se_are_pinned():
    """The pair that does NOT involve the reference exercises c[i]=+1, c[j]=-1."""
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    c = next(c for c in res.contrasts if set((c.a, c.b)) == {"drug", "dose2"})
    assert (c.a, c.b) == ("drug", "dose2")
    assert c.diff == pytest.approx(0.1055033557, rel=1e-8)
    assert c.se == pytest.approx(0.1861280598, rel=1e-8)
    assert c.pvalue_raw == pytest.approx(0.5758826827, rel=1e-8)


def test_factor_level_coefficient_is_pinned_with_its_sign():
    """The adjustment-factor dummy uses the FIRST level as reference.

    LS means are invariant to that choice, so only the coefficient itself can
    catch a `levels[1:]` -> `levels[:-1]` slip — which flips the reported sign
    while leaving the printed label 'site=B (vs A)' untouched.
    """
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    e = next(e for e in res.covariate_effects if e.kind == "factor")
    assert e.name == "site=B (vs A)"
    assert e.coef == pytest.approx(-0.0430872483, rel=1e-7)
    assert e.se == pytest.approx(0.1526368548, rel=1e-8)


def test_bh_adjusted_pvalues_are_pinned_not_merely_bounded():
    res = run_ancova(_no_site(), ["base"], outcome="isi", correction="bh")
    adj = {(c.a, c.b): c.pvalue_adj for c in res.contrasts}
    # BH over raw p = .2332, .5711, .5245 -> all pulled to .5711
    assert adj[("drug", "placebo")] == pytest.approx(0.5710725037, rel=1e-8)
    assert adj[("drug", "dose2")] == pytest.approx(0.5710725037, rel=1e-8)
    holm = run_ancova(_no_site(), ["base"], outcome="isi", correction="holm")
    hadj = {(c.a, c.b): c.pvalue_adj for c in holm.contrasts}
    assert hadj[("drug", "placebo")] == pytest.approx(0.6994838376, rel=1e-8)
    assert hadj[("drug", "dose2")] == pytest.approx(1.0)
    assert hadj[("drug", "placebo")] > adj[("drug", "placebo")]


def test_partial_eta_squared_is_pinned():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    assert res.partial_eta_sq == pytest.approx(0.0545703797, rel=1e-8)


def test_slope_homogeneity_df_and_f_are_pinned():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi")
    s = res.slopes
    assert (s.df1, s.df2) == (2.0, 23.0)      # (k-1)*n_cov, n - p - (k-1)*n_cov
    assert s.statistic == pytest.approx(0.6440641882, rel=1e-8)
    assert s.pvalue == pytest.approx(0.5343656615, rel=1e-8)


def test_significance_stars_follow_the_adjusted_p_not_the_raw_one():
    """A family where raw p < alpha but Holm-adjusted p >= alpha."""
    recs = []
    for i, b in enumerate([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]):
        recs.append(AncovaRecord("a", b + 0.0 + (0.4 if i % 2 else -0.4), (b,)))
        recs.append(AncovaRecord("b", b + 1.0 + (0.4 if i % 2 else -0.4), (b,)))
        recs.append(AncovaRecord("c", b + 1.9 + (0.4 if i % 2 else -0.4), (b,)))
        recs.append(AncovaRecord("d", b + 2.7 + (0.4 if i % 2 else -0.4), (b,)))
    res = run_ancova(recs, ["base"], outcome="y", alpha=0.0003)
    borderline = [c for c in res.contrasts
                  if c.pvalue_raw < 0.0003 <= c.pvalue_adj]
    assert borderline, "expected at least one raw-significant, Holm-rejected pair"
    for c in borderline:
        assert c.significant is False


def test_normality_is_checked_on_residuals_not_on_the_raw_outcome():
    """Two arms that are each skewed but whose residuals are not.

    Shapiro on the pooled outcome rejects; Shapiro on the model residuals does
    not. If the check ever slips back onto `y`, this flips.
    """
    from statwise.normality import shapiro_wilk
    from statwise.special import norm_ppf
    recs, ys = [], []
    n = 40
    # residuals drawn as exact normal quantiles, arms 50 units apart
    resid = [0.5 * norm_ppf((i + 0.5) / n) for i in range(n)]
    for i, b in enumerate(range(1, 21)):
        for j, (arm, shift) in enumerate((("a", 0.0), ("b", 50.0))):
            y = 1.0 * b + shift + resid[(i * 2 + j) * 7 % n]
            recs.append(AncovaRecord(arm, y, (float(b),)))
            ys.append(y)
    res = run_ancova(recs, ["base"], outcome="y", reference="a")
    assert shapiro_wilk(ys)[1] < 0.05           # raw outcome: clearly non-normal
    assert res.resid_normal_p > 0.05            # residuals: fine
    assert not any("잔차의 정규성이 기각" in w for w in res.warnings)


def test_alpha_drives_the_verdict_and_alpha_norm_does_not():
    recs = _no_site()
    strict = run_ancova(recs, ["base"], outcome="isi", alpha=0.4,
                        alpha_norm=0.001)
    assert strict.pvalue == pytest.approx(0.4841292220, rel=1e-8)
    assert strict.significant is False          # p = 0.484 >= alpha = 0.4
    loose = run_ancova(recs, ["base"], outcome="isi", alpha=0.49,
                       alpha_norm=0.4)
    assert loose.pvalue == pytest.approx(strict.pvalue, rel=1e-12)
    assert loose.significant is True            # same p, verdict follows alpha


def test_too_many_arms_is_refused_rather_than_hung():
    from statwise.ancova import MAX_ANCOVA_GROUPS
    recs = []
    for g in range(MAX_ANCOVA_GROUPS + 1):
        for j in range(3):
            recs.append(AncovaRecord(f"site_{g}", float(g + j), (float(j),)))
    with pytest.raises(ValueError, match="상한"):
        run_ancova(recs, ["base"])


def test_equivalence_section_labels_the_direction_it_actually_computed():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo",
                     equivalence=EquivalenceSpec(margin=(-2.0, 2.0)))
    text = render_ancova_text(res)
    assert "(drug − placebo)" in text
    assert "(placebo − drug)" not in text
    # and the JSON/CSV equivalence rows exist and agree with the contrast
    payload = json.loads(render_ancova_json(res))
    assert payload["equivalence"]["difference"] == pytest.approx(
        res.contrasts[0].diff, rel=1e-12)
    assert payload["equivalence"]["model"] == "ancova"
    kinds = [line.split(",")[1] for line in render_csv(res).splitlines()[1:]]
    assert "tost" in kinds


def test_csv_contrast_row_carries_the_signed_t_statistic():
    recs = [r for r in _no_site() if r.group in ("drug", "placebo")]
    res = run_ancova(recs, ["base"], outcome="isi", reference="placebo")
    row = next(line.split(",") for line in render_csv(res).splitlines()[1:]
               if line.split(",")[1] == "adjusted-contrast")
    c = res.contrasts[0]
    assert float(row[12]) == pytest.approx(c.diff / c.se, rel=1e-12)
    assert float(row[8]) == pytest.approx(c.diff, rel=1e-12)


def test_p_phrase_only_says_below_0001_when_it_is():
    from statwise.report import _p_phrase
    assert _p_phrase(0.0009) == "p < 0.001"
    assert _p_phrase(0.031) == "p = 0.031"
    assert _p_phrase(0.0011) == "p = 0.001"
    assert "NaN" in _p_phrase(float("nan"))


def test_json_schema_id_is_present_and_stable():
    res = run_ancova(_no_site(), ["base"], outcome="isi")
    assert json.loads(render_ancova_json(res))["schema"] == "statwise/ancova/1"


def test_three_arm_report_says_non_reference_pairs_are_included():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi",
                     reference="placebo")
    text = render_ancova_text(res)
    assert "기준군을 포함하지 않는 쌍도" in text
    assert "drug − dose2" in text


def test_paste_ready_sentence_calls_a_factor_a_factor():
    res = run_ancova(_reference_records(), ["base"], ["site"], outcome="isi")
    sentence = ancova_to_dict(res)["sentence"]
    assert "base을(를) 공변량으로" in sentence
    assert "site을(를) 보정인자로" in sentence
