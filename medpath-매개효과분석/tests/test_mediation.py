"""Mediation model: path algebra, decomposition identity, bootstrap behaviour."""

import math
from random import Random

import pytest

from helpers import exact_ols_with_intercept, write_csv
from medpath.dataio import build_design, load_table
from medpath.mediation import analyze


def _sim(n=150, seed=3, a=2.0, b=0.8, cprime=1.0, with_cov=True):
    """X -> M -> Y with known true coefficients."""
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = float(i % 2)
        cov = round(rng.gauss(50, 10), 2)
        m = round(10 + a * x + 0.05 * cov + rng.gauss(0, 1.5), 4)
        y = round(5 + b * m + cprime * x + 0.02 * cov + rng.gauss(0, 2.0), 4)
        rows.append([x, m, y, cov])
    return rows


def _design_from(tmp_path, rows, header=("x", "m", "y", "cov"), **kw):
    t = load_table(write_csv(tmp_path / "sim.csv", list(header), rows))
    return build_design(t, kw.pop("x", "x"), kw.pop("mediators", ["m"]),
                        kw.pop("y", "y"), kw.pop("covariates", []), **kw)


# --------------------------------------------------------------------------
# Path coefficients
# --------------------------------------------------------------------------
def test_path_coefficients_match_exact_rational_ols(tmp_path):
    rows = _sim(n=80, seed=11)
    d = _design_from(tmp_path, rows, covariates=["cov"])
    res = analyze(d, n_boot=0)

    x = [r[0] for r in rows]
    m = [r[1] for r in rows]
    y = [r[2] for r in rows]
    cov = [r[3] for r in rows]

    want_a = exact_ols_with_intercept([x, cov], m)          # m ~ x + cov
    want_y = exact_ols_with_intercept([x, m, cov], y)       # y ~ x + m + cov
    want_c = exact_ols_with_intercept([x, cov], y)          # y ~ x + cov

    assert res.m_regressions[0].coef("x").estimate == pytest.approx(
        float(want_a[1]), rel=1e-11)
    assert res.y_regression.coef("x").estimate == pytest.approx(float(want_y[1]), rel=1e-11)
    assert res.y_regression.coef("m").estimate == pytest.approx(float(want_y[2]), rel=1e-11)
    assert res.total_regression.coef("x").estimate == pytest.approx(
        float(want_c[1]), rel=1e-11)


def test_indirect_effect_is_the_product_of_its_components(tmp_path):
    d = _design_from(tmp_path, _sim(n=60, seed=2), covariates=["cov"])
    res = analyze(d, n_boot=0)
    eff = res.indirect_effects[0]
    a = res.m_regressions[0].coef("x").estimate
    b = res.y_regression.coef("m").estimate
    assert eff.estimate == pytest.approx(a * b, rel=1e-13)
    assert [n for n, _, _ in eff.components] == [
        "a_1 (x → m)", "b_1 (m → y)"]


@pytest.mark.parametrize("covs", [[], ["cov"]])
def test_total_equals_direct_plus_indirect_simple(tmp_path, covs):
    d = _design_from(tmp_path, _sim(n=90, seed=7), covariates=covs)
    res = analyze(d, n_boot=0)
    total = res.effect("total").estimate
    direct = res.effect("direct").estimate
    indirect = sum(e.estimate for e in res.indirect_effects)
    assert total == pytest.approx(direct + indirect, rel=1e-10, abs=1e-12)


def test_total_equals_direct_plus_indirect_parallel(tmp_path):
    rng = Random(15)
    rows = []
    for i in range(120):
        x = float(i % 2)
        m1 = round(3 * x + rng.gauss(0, 1), 4)
        m2 = round(-1.5 * x + rng.gauss(0, 1), 4)
        cov = round(rng.gauss(0, 1), 4)
        y = round(0.5 * m1 + 0.9 * m2 + 0.4 * x + 0.3 * cov + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y, cov])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y", "cov"),
                     mediators=["m1", "m2"], covariates=["cov"])
    res = analyze(d, n_boot=0)
    assert len(res.indirect_effects) == 2
    total_ind = res.effect("indirect_total").estimate
    assert total_ind == pytest.approx(sum(e.estimate for e in res.indirect_effects),
                                      rel=1e-12)
    assert res.effect("total").estimate == pytest.approx(
        res.effect("direct").estimate + total_ind, rel=1e-10, abs=1e-12)


def test_total_equals_direct_plus_indirect_serial(tmp_path):
    rng = Random(21)
    rows = []
    for i in range(140):
        x = float(i % 2)
        m1 = round(2.0 * x + rng.gauss(0, 1), 4)
        m2 = round(0.7 * m1 + 0.5 * x + rng.gauss(0, 1), 4)
        y = round(0.6 * m2 + 0.2 * m1 + 0.3 * x + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y"),
                     mediators=["m1", "m2"])
    res = analyze(d, serial=True, n_boot=0)
    # k=2 serial -> 3 specific paths
    assert len(res.indirect_effects) == 3
    labels = [e.label for e in res.indirect_effects]
    assert labels[0].endswith("x → m1 → y")
    assert labels[1].endswith("x → m2 → y")
    assert labels[2].endswith("x → m1 → m2 → y")
    total_ind = res.effect("indirect_total").estimate
    assert res.effect("total").estimate == pytest.approx(
        res.effect("direct").estimate + total_ind, rel=1e-10, abs=1e-12)


def test_serial_three_mediators_enumerates_seven_paths(tmp_path):
    rng = Random(31)
    rows = []
    for i in range(200):
        x = float(i % 2)
        m1 = round(1.5 * x + rng.gauss(0, 1), 4)
        m2 = round(0.6 * m1 + 0.4 * x + rng.gauss(0, 1), 4)
        m3 = round(0.5 * m2 + 0.3 * m1 + 0.2 * x + rng.gauss(0, 1), 4)
        y = round(0.7 * m3 + 0.1 * m1 + 0.3 * x + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, m3, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "m3", "y"),
                     mediators=["m1", "m2", "m3"])
    res = analyze(d, serial=True, n_boot=0)
    assert len(res.indirect_effects) == 7          # 2^3 - 1 ordered sub-chains
    assert res.effect("total").estimate == pytest.approx(
        res.effect("direct").estimate + res.effect("indirect_total").estimate,
        rel=1e-9, abs=1e-11)
    longest = res.indirect_effects[-1]
    assert longest.label.endswith("x → m1 → m2 → m3 → y")
    assert len(longest.components) == 4


def test_serial_requires_two_mediators(tmp_path):
    d = _design_from(tmp_path, _sim(n=40, seed=4))
    with pytest.raises(ValueError, match="2개 이상"):
        analyze(d, serial=True, n_boot=0)


def test_serial_and_parallel_differ_for_the_same_mediators(tmp_path):
    rng = Random(41)
    rows = []
    for i in range(120):
        x = float(i % 2)
        m1 = round(2.0 * x + rng.gauss(0, 1), 4)
        m2 = round(0.8 * m1 + rng.gauss(0, 1), 4)   # m2 depends on m1
        y = round(0.7 * m2 + 0.3 * x + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y"),
                     mediators=["m1", "m2"])
    par = analyze(d, serial=False, n_boot=0)
    ser = analyze(d, serial=True, n_boot=0)
    assert len(par.indirect_effects) == 2 and len(ser.indirect_effects) == 3
    # the m2 path differs because serial controls for m1 in the m2 equation
    assert par.indirect_effects[1].estimate != pytest.approx(
        ser.indirect_effects[1].estimate, rel=1e-6)
    # ... but both decompositions still sum to the same total effect
    assert par.effect("total").estimate == pytest.approx(
        ser.effect("total").estimate, rel=1e-12)


# --------------------------------------------------------------------------
# Sobel / delta method
# --------------------------------------------------------------------------
def test_sobel_se_matches_the_textbook_formula(tmp_path):
    d = _design_from(tmp_path, _sim(n=100, seed=13))
    res = analyze(d, n_boot=0)
    eff = res.indirect_effects[0]
    a = res.m_regressions[0].coef("x")
    b = res.y_regression.coef("m")
    want = math.sqrt(b.estimate ** 2 * a.se ** 2 + a.estimate ** 2 * b.se ** 2)
    assert eff.delta_se == pytest.approx(want, rel=1e-12)
    assert eff.delta_z == pytest.approx(eff.estimate / want, rel=1e-12)


def test_delta_method_generalises_to_three_factor_serial_path(tmp_path):
    rng = Random(51)
    rows = []
    for i in range(150):
        x = float(i % 2)
        m1 = round(2.0 * x + rng.gauss(0, 1), 4)
        m2 = round(0.7 * m1 + rng.gauss(0, 1), 4)
        y = round(0.6 * m2 + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y"),
                     mediators=["m1", "m2"])
    res = analyze(d, serial=True, n_boot=0)
    chain = res.indirect_effects[2]
    vals = [v for _, v, _ in chain.components]
    ses = [s for _, _, s in chain.components]
    want = math.sqrt(sum((math.prod(vals[:j] + vals[j + 1:]) * ses[j]) ** 2
                         for j in range(3)))
    assert chain.delta_se == pytest.approx(want, rel=1e-12)


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------
def test_bootstrap_is_deterministic_for_a_seed(tmp_path):
    d = _design_from(tmp_path, _sim(n=80, seed=5))
    r1 = analyze(d, n_boot=400, seed=123)
    r2 = analyze(d, n_boot=400, seed=123)
    e1, e2 = r1.indirect_effects[0], r2.indirect_effects[0]
    assert (e1.ci_lo, e1.ci_hi, e1.se) == (e2.ci_lo, e2.ci_hi, e2.se)


def test_different_seeds_give_close_but_not_identical_intervals(tmp_path):
    d = _design_from(tmp_path, _sim(n=80, seed=5))
    e1 = analyze(d, n_boot=600, seed=1).indirect_effects[0]
    e2 = analyze(d, n_boot=600, seed=2).indirect_effects[0]
    assert e1.ci_lo != e2.ci_lo
    assert e1.ci_lo == pytest.approx(e2.ci_lo, rel=0.25)


def test_parallel_jobs_give_identical_results(tmp_path):
    d = _design_from(tmp_path, _sim(n=70, seed=9))
    serial_run = analyze(d, n_boot=750, seed=77, jobs=1)
    parallel_run = analyze(d, n_boot=750, seed=77, jobs=3)
    a, b = serial_run.indirect_effects[0], parallel_run.indirect_effects[0]
    assert (a.ci_lo, a.ci_hi) == (b.ci_lo, b.ci_hi)
    assert serial_run.boot_ok == parallel_run.boot_ok


def test_bootstrap_can_be_switched_off(tmp_path):
    d = _design_from(tmp_path, _sim(n=50, seed=6))
    res = analyze(d, n_boot=0)
    eff = res.indirect_effects[0]
    assert math.isnan(eff.ci_lo) and math.isnan(eff.ci_hi)
    assert eff.significant is False
    assert res.boot_ok == 0
    assert res.contrasts == []


@pytest.mark.parametrize("method", ["percentile", "bc", "bca"])
def test_all_ci_methods_produce_ordered_intervals_around_the_estimate(tmp_path, method):
    d = _design_from(tmp_path, _sim(n=90, seed=8))
    res = analyze(d, n_boot=800, seed=4, ci_method=method)
    eff = res.indirect_effects[0]
    assert eff.ci_lo < eff.ci_hi
    assert eff.ci_lo < eff.estimate < eff.ci_hi
    assert eff.significant                      # a=2, b=0.8 is a strong true effect


def test_bootstrap_ci_covers_the_true_indirect_effect(tmp_path):
    """The known truth is a*b = 2.0*0.8 = 1.6."""
    d = _design_from(tmp_path, _sim(n=250, seed=101, a=2.0, b=0.8))
    res = analyze(d, n_boot=1500, seed=99)
    eff = res.indirect_effects[0]
    assert eff.ci_lo < 1.6 < eff.ci_hi
    assert eff.estimate == pytest.approx(1.6, rel=0.25)


def test_null_indirect_effect_is_not_flagged_significant(tmp_path):
    d = _design_from(tmp_path, _sim(n=200, seed=55, a=0.0, b=0.0, cprime=1.0))
    res = analyze(d, n_boot=1500, seed=5)
    assert not res.indirect_effects[0].significant


def test_contrast_equals_difference_of_specific_effects(tmp_path):
    rng = Random(61)
    rows = []
    for i in range(140):
        x = float(i % 2)
        m1 = round(3.0 * x + rng.gauss(0, 1), 4)
        m2 = round(0.5 * x + rng.gauss(0, 1), 4)
        y = round(0.8 * m1 + 0.8 * m2 + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y"),
                     mediators=["m1", "m2"])
    res = analyze(d, n_boot=800, seed=3)
    assert len(res.contrasts) == 1
    c = res.contrasts[0]
    assert c.estimate == pytest.approx(
        res.indirect_effects[0].estimate - res.indirect_effects[1].estimate, rel=1e-12)
    assert c.significant                        # 3.0*0.8 clearly beats 0.5*0.8


def test_single_mediator_has_no_contrast_or_total_indirect_row(tmp_path):
    d = _design_from(tmp_path, _sim(n=60, seed=12))
    res = analyze(d, n_boot=300, seed=1)
    assert res.contrasts == []
    assert res.effect("indirect_total") is None
    assert len(res.effects) == 3                # total, direct, one indirect


# --------------------------------------------------------------------------
# Effect sizes, proportions, warnings
# --------------------------------------------------------------------------
def test_dichotomous_x_uses_partially_standardized_effects(tmp_path):
    from medpath.model import sd
    rows = _sim(n=80, seed=14)
    d = _design_from(tmp_path, rows)
    res = analyze(d, n_boot=0)
    assert "부분표준화" in res.standardized_kind
    eff = res.effect("total")
    assert eff.standardized == pytest.approx(eff.estimate / sd(d.y), rel=1e-12)


def test_continuous_x_uses_completely_standardized_effects(tmp_path):
    from medpath.model import sd
    rng = Random(17)
    rows = [[round(rng.gauss(0, 1), 4)] for _ in range(90)]
    data = []
    for (x,) in rows:
        m = round(1.2 * x + rng.gauss(0, 1), 4)
        y = round(0.7 * m + 0.4 * x + rng.gauss(0, 1), 4)
        data.append([x, m, y])
    d = _design_from(tmp_path, data, header=("x", "m", "y"))
    res = analyze(d, n_boot=0)
    assert "완전표준화" in res.standardized_kind
    eff = res.effect("total")
    assert eff.standardized == pytest.approx(
        eff.estimate * sd(d.x) / sd(d.y), rel=1e-12)


def test_suppression_case_refuses_to_report_a_proportion(tmp_path):
    """Indirect and total with opposite signs -> proportion is meaningless."""
    rng = Random(71)
    rows = []
    for i in range(120):
        x = float(i % 2)
        m = round(2.0 * x + rng.gauss(0, 1), 4)
        y = round(-1.0 * m + 3.0 * x + rng.gauss(0, 1), 4)   # indirect < 0, total > 0
        rows.append([x, m, y])
    d = _design_from(tmp_path, rows, header=("x", "m", "y"))
    res = analyze(d, n_boot=0)
    assert res.effect("total").estimate > 0
    assert res.indirect_effects[0].estimate < 0
    assert math.isnan(res.proportion_mediated)
    assert "억제" in res.proportion_note


def test_binary_outcome_triggers_a_warning(tmp_path):
    rng = Random(81)
    rows = []
    for i in range(80):
        x = float(i % 2)
        m = round(1.0 * x + rng.gauss(0, 1), 4)
        y = 1 if (m + rng.gauss(0, 1)) > 0.5 else 0
        rows.append([x, m, y])
    d = _design_from(tmp_path, rows, header=("x", "m", "y"))
    res = analyze(d, n_boot=0)
    assert any("이분형" in w for w in res.warnings)


def test_small_sample_triggers_a_warning(tmp_path):
    d = _design_from(tmp_path, _sim(n=24, seed=19))
    res = analyze(d, n_boot=200, seed=1)
    assert any("표본" in w for w in res.warnings)


def test_collinear_mediators_trigger_a_vif_warning(tmp_path):
    rng = Random(91)
    rows = []
    for i in range(100):
        x = float(i % 2)
        m1 = round(2.0 * x + rng.gauss(0, 1), 4)
        m2 = round(m1 + rng.gauss(0, 0.05), 4)      # nearly identical to m1
        y = round(0.5 * m1 + 0.2 * x + rng.gauss(0, 1), 4)
        rows.append([x, m1, m2, y])
    d = _design_from(tmp_path, rows, header=("x", "m1", "m2", "y"),
                     mediators=["m1", "m2"])
    res = analyze(d, n_boot=0)
    assert any("VIF" in w for w in res.warnings)


def test_result_serialises_to_json_safe_dict(tmp_path):
    import json
    d = _design_from(tmp_path, _sim(n=60, seed=23), covariates=["cov"])
    res = analyze(d, n_boot=300, seed=2)
    payload = res.to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    again = json.loads(text)
    assert again["model"]["type"] == "parallel"
    assert again["sample"]["n_analysed"] == 60
    assert again["settings"]["bootstrap_used"] == 300
    assert len(again["effects"]) == 3
    assert again["effects"][2]["kind"] == "indirect"
    assert "components" in again["effects"][2]
