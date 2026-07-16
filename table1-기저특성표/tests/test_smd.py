"""Standardized mean difference tests against hand-computed values."""

import math

from table1.smd import categorical_smd, continuous_smd


def test_continuous_smd_handcomputed():
    a = [1, 2, 3, 4, 5]   # mean 3, var 2.5
    b = [3, 4, 5, 6, 7]   # mean 5, var 2.5
    # d = |3-5| / sqrt((2.5+2.5)/2) = 2 / sqrt(2.5) = 1.264911
    assert abs(continuous_smd(a, b) - 1.2649110640673518) < 1e-12


def test_continuous_smd_too_small():
    assert continuous_smd([1.0], [2.0, 3.0]) is None


def test_continuous_smd_zero_spread():
    assert continuous_smd([5, 5, 5], [5, 5, 5]) == 0.0
    assert math.isinf(continuous_smd([5, 5, 5], [6, 6, 6]))


def test_binary_smd_handcomputed():
    # group1: 3/10 -> p1=0.3 ; group2: 7/10 -> p2=0.7
    # d = |0.3-0.7| / sqrt((0.3*0.7 + 0.7*0.3)/2) = 0.4 / sqrt(0.21) = 0.872872
    d = categorical_smd([3, 7], [7, 3])
    assert abs(d - 0.8728715609439694) < 1e-12


def test_multivariate_reduces_to_binary():
    # A 2-level categorical_smd must equal the binary closed form.
    d2 = categorical_smd([4, 6], [6, 4])
    p1, p2 = 0.4, 0.6
    ref = abs(p1 - p2) / math.sqrt((p1 * (1 - p1) + p2 * (1 - p2)) / 2)
    assert abs(d2 - ref) < 1e-12


def test_multivariate_identical_is_zero():
    assert abs(categorical_smd([5, 3, 2], [50, 30, 20])) < 1e-12


def test_multivariate_smd_k3_known_value():
    # Yang-Dalton k=3 with a genuinely non-zero diff, so the covariance build
    # and 2x2 Gauss-Jordan inverse are actually exercised (a wrong sign in cov
    # or a bad inverse would fail here). Oracle: numpy diff @ inv(S) @ diff.
    d = categorical_smd([10, 20, 30], [30, 20, 10])
    assert abs(d - 0.8944271909999159) < 1e-12


def test_multivariate_smd_k4_known_value():
    # k=4 exercises a 3x3 inverse; asymmetric, non-round oracle.
    d = categorical_smd([10, 20, 30, 40], [40, 25, 20, 15])
    assert abs(d - 0.8715591687403585) < 1e-12


def test_multivariate_smd_symmetry():
    # SMD is invariant under swapping the two groups (a balance metric property).
    for c1, c2 in ([[10, 20, 30], [30, 20, 10]],
                   [[10, 20, 30, 40], [40, 25, 20, 15]],
                   [[3, 7], [7, 3]]):
        assert abs(categorical_smd(c1, c2) - categorical_smd(c2, c1)) < 1e-12


def test_continuous_smd_symmetry():
    a = [1, 2, 3, 4, 5, 9]
    b = [2, 2, 7, 8, 8, 10]
    assert abs(continuous_smd(a, b) - continuous_smd(b, a)) < 1e-12


def test_multivariate_smd_capped_on_many_levels():
    # A high-cardinality categorical would make the O(k^3) inverse hang; the
    # tool declines (None) beyond MAX_SMD_LEVELS instead of computing it.
    from table1.smd import MAX_SMD_LEVELS
    k = MAX_SMD_LEVELS + 5
    assert categorical_smd([1] * k, [1] * k) is None
    # ...but a table at the cap is still computed.
    assert categorical_smd([2] * MAX_SMD_LEVELS, [1] * MAX_SMD_LEVELS) is not None


def test_multivariate_singular_returns_none():
    # Perfectly separated 3-level: group1 all level0, group2 all level1.
    # Averaged covariance is singular -> None (documented behaviour).
    assert categorical_smd([10, 0, 0], [0, 10, 0]) is None


def test_categorical_empty_group():
    assert categorical_smd([0, 0], [3, 4]) is None
