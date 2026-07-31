"""Bootstrap resampling and interval construction.

Interval endpoints are checked against hand-computed reference values, not
against the package's own output.
"""

import math
from random import Random

import pytest

from helpers import write_csv
from medpath.bootstrap import (acceleration_from, bc_ci, bca_ci, ci_from_boots,
                               jackknife_acceleration, jackknife_values,
                               percentile_ci, quantile, run_bootstrap)
from medpath.dataio import build_design, load_table
from medpath.mediation import analyze
from medpath.special import norm_cdf, norm_ppf


# --------------------------------------------------------------------------
# quantile: type 7, the numpy/R default
# --------------------------------------------------------------------------
def test_quantile_matches_type7_by_hand():
    s = [1.0, 2.0, 3.0, 4.0]           # h = (n-1)q = 3q
    assert quantile(s, 0.0) == pytest.approx(1.0)
    assert quantile(s, 1.0) == pytest.approx(4.0)
    assert quantile(s, 0.5) == pytest.approx(2.5)
    assert quantile(s, 0.25) == pytest.approx(1.75)   # h=0.75 -> 1+0.75*1
    assert quantile(s, 0.1) == pytest.approx(1.3)     # h=0.30 -> 1+0.30*1


def test_quantile_edge_cases():
    assert math.isnan(quantile([], 0.5))
    assert quantile([7.0], 0.3) == 7.0
    assert quantile([1.0, 2.0], -5.0) == 1.0          # clamped
    assert quantile([1.0, 2.0], 5.0) == 2.0


def test_percentile_ci_is_the_two_matching_quantiles():
    vals = [float(i) for i in range(101)]
    lo, hi = percentile_ci(vals, 0.95)
    assert lo == pytest.approx(quantile(sorted(vals), 0.025))
    assert hi == pytest.approx(quantile(sorted(vals), 0.975))
    assert lo == pytest.approx(2.5) and hi == pytest.approx(97.5)


def test_percentile_ci_widens_with_confidence():
    vals = [float(i) for i in range(1001)]
    lo90, hi90 = percentile_ci(vals, 0.90)
    lo99, hi99 = percentile_ci(vals, 0.99)
    assert lo99 < lo90 and hi99 > hi90


# --------------------------------------------------------------------------
# BC / BCa
# --------------------------------------------------------------------------
def test_bc_equals_percentile_when_the_median_is_the_observed_value():
    """z0 = 0 makes the bias correction a no-op, so BC must equal percentile."""
    vals = [float(i) for i in range(1001)]     # observed at the exact median
    lo_p, hi_p = percentile_ci(vals, 0.95)
    lo_b, hi_b, clamped = bc_ci(vals, 500.0, 0.95)
    assert not clamped
    assert lo_b == pytest.approx(lo_p, abs=1e-9)
    assert hi_b == pytest.approx(hi_p, abs=1e-9)


def test_bca_with_zero_acceleration_equals_bc():
    vals = [float(i) ** 1.5 for i in range(500)]
    assert bca_ci(vals, 200.0, 0.95, 0.0)[:2] == pytest.approx(
        bc_ci(vals, 200.0, 0.95)[:2])


def test_bc_endpoints_match_the_hand_computed_formula():
    """Recompute the BC endpoints from the textbook formula independently."""
    vals = sorted(float(i) for i in range(1000))
    observed = 300.0
    conf = 0.95
    b = len(vals)
    less = sum(1 for v in vals if v < observed)
    ties = sum(1 for v in vals if v == observed)
    z0 = norm_ppf((less + 0.5 * ties) / b)
    a1 = norm_cdf(z0 + (z0 + norm_ppf(0.025)))
    a2 = norm_cdf(z0 + (z0 + norm_ppf(0.975)))
    lo, hi, _ = bc_ci(vals, observed, conf)
    assert lo == pytest.approx(quantile(vals, a1))
    assert hi == pytest.approx(quantile(vals, a2))


def test_bc_shifts_the_interval_toward_the_bias():
    """If most replicates sit above the estimate, BC must shift downward."""
    vals = [float(i) for i in range(1000)]
    lo_p, hi_p = percentile_ci(vals, 0.95)
    lo_b, hi_b, _ = bc_ci(vals, 100.0, 0.95)   # observed well below the median
    assert lo_b < lo_p and hi_b < hi_p


def test_z0_clamping_is_reported_as_a_warning():
    vals = [1.0] * 200                          # degenerate: every replicate equal
    _, _, warns = ci_from_boots(vals, 99.0, 0.95, "bc")
    assert warns and "절단" in warns[0]


def test_ci_from_boots_rejects_an_unknown_method():
    with pytest.raises(ValueError):
        ci_from_boots([1.0, 2.0, 3.0], 2.0, 0.95, "nonsense")


def test_ci_from_boots_with_no_samples_returns_nan_and_warns():
    lo, hi, warns = ci_from_boots([], 1.0, 0.95, "percentile")
    assert math.isnan(lo) and math.isnan(hi) and warns


def test_acceleration_is_zero_for_a_symmetric_jackknife():
    vals = [-2.0, -1.0, 0.0, 1.0, 2.0]
    assert acceleration_from(vals) == pytest.approx(0.0, abs=1e-12)


def test_acceleration_needs_at_least_three_points():
    assert acceleration_from([1.0, 2.0]) is None
    assert acceleration_from([5.0] * 10) is None      # zero variance


def test_acceleration_sign_follows_the_jackknife_skew():
    right = [0.0, 0.0, 0.0, 0.0, 10.0]                # long right tail
    left = [-v for v in right]
    assert acceleration_from(right) < 0 < acceleration_from(left)


# --------------------------------------------------------------------------
# End-to-end bootstrap behaviour
# --------------------------------------------------------------------------
def _design(tmp_path, n=90, seed=4, k=2):
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = float(i % 2)
        m1 = round(10 + 2.0 * x + rng.gauss(0, 1.5), 4)
        m2 = round(4 + 1.0 * x + 0.3 * m1 + rng.gauss(0, 1.0), 4)
        y = round(5 + 0.8 * m1 + 0.5 * m2 + 1.0 * x + rng.gauss(0, 2.0), 4)
        rows.append([x, m1, m2, y])
    t = load_table(write_csv(tmp_path / "b.csv", ["x", "m1", "m2", "y"], rows))
    return build_design(t, "x", ["m1", "m2"][:k], "y", [])


def test_same_seed_gives_identical_intervals(tmp_path):
    d = _design(tmp_path)
    a = analyze(d, n_boot=400, seed=99)
    b = analyze(d, n_boot=400, seed=99)
    for ea, eb in zip(a.indirect_effects, b.indirect_effects):
        assert ea.ci_lo == eb.ci_lo and ea.ci_hi == eb.ci_hi


def test_different_seed_changes_the_interval(tmp_path):
    d = _design(tmp_path)
    a = analyze(d, n_boot=400, seed=1)
    b = analyze(d, n_boot=400, seed=2)
    assert (a.indirect_effects[0].ci_lo != b.indirect_effects[0].ci_lo
            or a.indirect_effects[0].ci_hi != b.indirect_effects[0].ci_hi)


def test_bootstrap_count_is_honoured(tmp_path):
    d = _design(tmp_path)
    res = analyze(d, n_boot=300, seed=7)
    assert res.n_boot == 300
    assert res.boot_ok + res.boot_failed == 300


def test_zero_bootstrap_leaves_intervals_undefined_but_estimates_intact(tmp_path):
    d = _design(tmp_path)
    res = analyze(d, n_boot=0)
    eff = res.indirect_effects[0]
    assert math.isfinite(eff.estimate)
    assert math.isnan(eff.ci_lo) and math.isnan(eff.ci_hi)
    assert eff.significant is False


def test_run_bootstrap_with_zero_reps_returns_empty_columns(tmp_path):
    d = _design(tmp_path)
    res = analyze(d, n_boot=0)
    assert res.boot_ok == 0 and res.contrasts == []


# --------------------------------------------------------------------------
# Contrasts under BCa  (regression: they used to degrade to BC silently)
# --------------------------------------------------------------------------
def test_contrast_records_the_ci_method_it_actually_used(tmp_path):
    d = _design(tmp_path)
    res = analyze(d, n_boot=400, seed=3, ci_method="bca")
    assert res.contrasts
    for c in res.contrasts:
        assert c.ci_method == "BCa 부트스트랩"
        assert not c.warnings, "contrast fell back off BCa: %s" % c.warnings


def test_contrast_bca_uses_a_real_acceleration_not_zero(tmp_path):
    """A BCa contrast must differ from the BC contrast on skewed data.

    Regression: `ci_from_boots(..., 'bca', None)` was called for contrasts,
    which set acc=0 — i.e. a BC interval reported under the BCa label.
    """
    d = _design(tmp_path)
    bca = analyze(d, n_boot=800, seed=11, ci_method="bca").contrasts[0]
    bc = analyze(d, n_boot=800, seed=11, ci_method="bc").contrasts[0]
    assert bca.estimate == pytest.approx(bc.estimate)
    assert (bca.ci_lo != bc.ci_lo) or (bca.ci_hi != bc.ci_hi)


def test_contrast_acceleration_matches_a_direct_jackknife(tmp_path):
    """The reused jackknife sweep gives the same answer as a fresh one."""
    from medpath.linalg import GramCache
    d = _design(tmp_path)
    res = analyze(d, n_boot=200, seed=5, ci_method="bca")
    cols = [[1.0] * d.n_used, list(d.x)]
    for _, v in d.mediators:
        cols.append(list(v))
    cols.append(list(d.y))
    cache = GramCache(cols)
    # rebuild the same plan the analysis used
    from medpath.mediation import _paths
    from medpath.bootstrap import EffectPlan
    k = len(d.mediators)
    plan = EffectPlan(k, False, [[0, 1] for _ in range(k)],
                      [2 + j for j in range(k)], [1] * k, [{} for _ in range(k)],
                      [0, 1] + [2 + i for i in range(k)], 2 + k, 1,
                      [2 + i for i in range(k)], [0, 1], 2 + k, 1,
                      _paths(k, False))
    jack = jackknife_values(cache, plan)
    assert len(jack) == d.n_used
    reused = jackknife_acceleration(cache, plan, 2, 3, jack=jack)
    fresh = jackknife_acceleration(cache, plan, 2, 3)
    assert reused == pytest.approx(fresh)


def test_jackknife_values_is_computed_once_per_analysis(tmp_path, monkeypatch):
    """Guard the O(effects x N) regression that the shared sweep removed."""
    import medpath.mediation as med
    calls = []
    real = med.jackknife_values

    def counting(cache, plan):
        calls.append(1)
        return real(cache, plan)

    monkeypatch.setattr(med, "jackknife_values", counting)
    analyze(_design(tmp_path), n_boot=200, seed=8, ci_method="bca")
    assert len(calls) == 1


def test_percentile_method_does_no_jackknife_work(tmp_path, monkeypatch):
    import medpath.mediation as med
    calls = []
    monkeypatch.setattr(med, "jackknife_values",
                        lambda c, p: calls.append(1) or [])
    analyze(_design(tmp_path), n_boot=200, seed=8, ci_method="percentile")
    assert calls == []
