"""Tests for between-group effect sizes with 95% CI.

Oracle values are frozen from statsmodels (CompareMeans.tconfint_diff,
confint_proportions_2indep with Newcombe) computed offline.
"""

import math

import pytest

from table1.effects import (
    Effect,
    hodges_lehmann,
    mean_difference,
    risk_difference,
)

_A = [10.0, 12.0, 11.5, 9.0, 13.0, 10.5]
_B = [8.0, 7.5, 9.0, 8.5, 7.0]


def test_mean_difference_student_matches_oracle():
    e = mean_difference(_A, _B, kind="student")
    assert e.kind == "mean_diff"
    assert e.estimate == pytest.approx(3.0)
    assert e.lo == pytest.approx(1.3537002554825843, abs=1e-9)
    assert e.hi == pytest.approx(4.646299744517416, abs=1e-9)


def test_mean_difference_welch_matches_oracle():
    e = mean_difference(_A, _B, kind="welch")
    assert e.estimate == pytest.approx(3.0)
    assert e.lo == pytest.approx(1.4087017042200995, abs=1e-9)
    assert e.hi == pytest.approx(4.5912982957799, abs=1e-9)


def test_mean_difference_ci_brackets_estimate():
    e = mean_difference(_A, _B, kind="student")
    assert e.lo < e.estimate < e.hi


def test_mean_difference_tiny_group_returns_none():
    assert mean_difference([1.0], [2.0, 3.0], kind="welch") is None
    assert mean_difference([1.0, 2.0], [3.0], kind="student") is None


def test_mean_difference_zero_spread_returns_none():
    # Both groups constant -> zero SE, no CI.
    assert mean_difference([5.0, 5.0, 5.0], [5.0, 5.0], kind="welch") is None


def test_hodges_lehmann_estimate_is_median_of_pairwise_diffs():
    e = hodges_lehmann(_A, _B)
    pw = sorted(x - y for x in _A for y in _B)
    m = len(pw)
    med = pw[m // 2] if m % 2 else 0.5 * (pw[m // 2 - 1] + pw[m // 2])
    assert e.kind == "hl_shift"
    assert e.estimate == pytest.approx(med)
    assert e.lo <= e.estimate <= e.hi


def test_hodges_lehmann_shift_invariance():
    # Adding a constant c to group A shifts the HL estimate by exactly c.
    e0 = hodges_lehmann(_A, _B)
    e1 = hodges_lehmann([x + 4.0 for x in _A], _B)
    assert e1.estimate == pytest.approx(e0.estimate + 4.0)


def test_hodges_lehmann_tiny_group_returns_none():
    assert hodges_lehmann([1.0], [2.0, 3.0]) is None


def test_risk_difference_newcombe_matches_oracle():
    # 3/6 vs 4/5 -> RD = -0.3; Newcombe interval frozen from statsmodels.
    e = risk_difference(3, 6, 4, 5)
    assert e.kind == "risk_diff"
    assert e.estimate == pytest.approx(-0.3)
    assert e.lo == pytest.approx(-0.6527125097991044, abs=1e-9)
    assert e.hi == pytest.approx(0.22702411947308243, abs=1e-9)


def test_risk_difference_zero_cells_are_finite():
    # 0/10 vs 5/10 -> RD = -0.5 with a finite Newcombe interval (no blow-up).
    e = risk_difference(0, 10, 5, 10)
    assert e.estimate == pytest.approx(-0.5)
    assert math.isfinite(e.lo) and math.isfinite(e.hi)
    assert e.lo <= e.estimate <= e.hi
    assert e.lo >= -1.0 and e.hi <= 1.0


def test_risk_difference_empty_denominator_returns_none():
    assert risk_difference(0, 0, 3, 5) is None
    assert risk_difference(3, 5, 0, 0) is None


def test_risk_difference_sign_flips_with_index_swap():
    e1 = risk_difference(2, 8, 6, 8)
    e2 = risk_difference(6, 8, 2, 8)
    assert e1.estimate == pytest.approx(-e2.estimate)


def test_conf_level_widens_interval():
    e95 = mean_difference(_A, _B, kind="student", conf=0.95)
    e99 = mean_difference(_A, _B, kind="student", conf=0.99)
    assert (e99.hi - e99.lo) > (e95.hi - e95.lo)


# --------------------------------------------------------------------------- #
# Round-2 hardening: property fuzzing, tie correction, fallback branch, index
# --------------------------------------------------------------------------- #
def test_risk_difference_always_in_unit_range():
    import random
    rng = random.Random(11)
    for _ in range(5000):
        n1 = rng.randint(1, 60)
        n2 = rng.randint(1, 60)
        x1 = rng.randint(0, n1)
        x2 = rng.randint(0, n2)
        e = risk_difference(x1, n1, x2, n2)
        assert -1.0 <= e.estimate <= 1.0
        assert -1.0 - 1e-9 <= e.lo <= e.hi <= 1.0 + 1e-9


def test_hodges_lehmann_ci_brackets_estimate_fuzz():
    import random
    rng = random.Random(12)
    for _ in range(1000):
        n1 = rng.randint(2, 20)
        n2 = rng.randint(2, 20)
        a = [rng.gauss(0, 1) for _ in range(n1)]
        b = [rng.gauss(0, 1) for _ in range(n2)]
        e = hodges_lehmann(a, b)
        assert e.lo <= e.estimate <= e.hi


def test_hodges_lehmann_full_shift_invariance_both_groups():
    # Shifting BOTH groups by the same constant leaves the shift estimate fixed.
    e0 = hodges_lehmann(_A, _B)
    e1 = hodges_lehmann([x + 3.0 for x in _A], [y + 3.0 for y in _B])
    assert e1.estimate == pytest.approx(e0.estimate)
    assert (e1.hi - e1.lo) == pytest.approx(e0.hi - e0.lo)


def test_hodges_lehmann_tie_correction_narrows_ci():
    # Heavy ties: the tie-corrected variance is smaller than the naive one, so
    # the CI must be no wider than the naive-formula CI would be.
    import math
    a = [1.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 4.0, 4.0]
    b = [0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0]
    n1, n2, n = len(a), len(b), len(a) + len(b)
    naive_sd = math.sqrt(n1 * n2 * (n + 1) / 12.0)
    counts = {}
    for v in a + b:
        counts[v] = counts.get(v, 0) + 1
    tie = sum(c ** 3 - c for c in counts.values() if c > 1)
    corr_sd = math.sqrt((n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1))))
    assert corr_sd < naive_sd          # correction really shrinks the SD
    e = hodges_lehmann(a, b)
    assert e.lo <= e.estimate <= e.hi


def test_hodges_lehmann_wide_ci_falls_back_to_full_range():
    # Tiny groups at extreme confidence: K underflows -> CI = full pairwise range.
    e = hodges_lehmann([1.0, 2.0], [3.0, 4.0], conf=0.999)
    pw = sorted(x - y for x in [1.0, 2.0] for y in [3.0, 4.0])
    assert e.lo == pytest.approx(pw[0])
    assert e.hi == pytest.approx(pw[-1])


def test_effect_dataclass_index_fields_default_none():
    e = mean_difference(_A, _B, kind="student")
    assert e.index_level is None and e.reference_level is None
