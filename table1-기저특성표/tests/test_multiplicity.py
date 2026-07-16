"""Tests for multiple-comparison p-value adjustment.

Oracle values are frozen from statsmodels.stats.multitest.multipletests
(computed offline) so the suite stays dependency-free.
"""

import math

import pytest

from table1.multiplicity import METHODS, adjust_pvalues, normalize_method

_PS = [0.001, 0.013, 0.04, 0.2, 0.5]
# Frozen statsmodels oracles for _PS.
_ORACLE = {
    "bonferroni": [0.005, 0.065, 0.2, 1.0, 1.0],
    "holm": [0.005, 0.052, 0.12, 0.4, 0.5],
    "bh": [0.005, 0.0325, 0.0666666667, 0.25, 0.5],
    "by": [0.0114166667, 0.0742083333, 0.1522222222, 0.5708333333, 1.0],
}


@pytest.mark.parametrize("method,expected", _ORACLE.items())
def test_matches_statsmodels_oracle(method, expected):
    got = adjust_pvalues(_PS, method)
    for g, e in zip(got, expected):
        assert abs(g - e) < 1e-9, (method, g, e)


def test_none_passes_through_and_excluded_from_family():
    # A None (untestable variable) stays None and does not count toward m.
    ps = [0.001, None, 0.04, 0.2]
    out = adjust_pvalues(ps, "holm")
    assert out[1] is None
    # Family size is 3 (the three real p-values): Holm on [0.001,0.04,0.2].
    ref = adjust_pvalues([0.001, 0.04, 0.2], "holm")
    assert out[0] == pytest.approx(ref[0])
    assert out[2] == pytest.approx(ref[1])
    assert out[3] == pytest.approx(ref[2])


def test_none_method_is_identity():
    ps = [0.01, 0.5, None]
    assert adjust_pvalues(ps, "none") == ps


def test_monotone_nondecreasing_in_rank_order():
    # Adjusted p-values, sorted by original p, must be non-decreasing.
    import random
    rng = random.Random(3)
    ps = [rng.random() for _ in range(40)]
    for method in ("holm", "bh", "by", "bonferroni"):
        adj = adjust_pvalues(ps, method)
        order = sorted(range(len(ps)), key=lambda i: ps[i])
        seq = [adj[i] for i in order]
        for x, y in zip(seq, seq[1:]):
            assert x <= y + 1e-12, method


def test_adjusted_never_below_raw_and_capped_at_one():
    ps = [0.0001, 0.02, 0.3, 0.9, 0.999]
    for method in ("holm", "bh", "by", "bonferroni"):
        adj = adjust_pvalues(ps, method)
        for raw, a in zip(ps, adj):
            assert a >= raw - 1e-12, (method, raw, a)
            assert a <= 1.0 + 1e-12


def test_bonferroni_is_p_times_m():
    ps = [0.01, 0.02, 0.2]
    adj = adjust_pvalues(ps, "bonferroni")
    assert adj == [pytest.approx(0.03), pytest.approx(0.06), pytest.approx(0.6)]


def test_empty_and_all_none():
    assert adjust_pvalues([], "holm") == []
    assert adjust_pvalues([None, None], "bh") == [None, None]


def test_single_value_unchanged():
    for method in ("holm", "bh", "by", "bonferroni"):
        assert adjust_pvalues([0.03], method)[0] == pytest.approx(0.03)


def test_nan_treated_like_none():
    out = adjust_pvalues([0.01, float("nan"), 0.04], "bonferroni")
    assert math.isnan(out[1])
    # family size 2 -> 0.01*2, 0.04*2
    assert out[0] == pytest.approx(0.02)
    assert out[2] == pytest.approx(0.08)


def test_normalize_aliases():
    assert normalize_method("FDR_BH") == "bh"
    assert normalize_method("fdr") == "bh"
    assert normalize_method("bonf") == "bonferroni"
    assert normalize_method("Holm-Bonferroni") == "holm"
    assert normalize_method(None) == "none"
    assert normalize_method("") == "none"


def test_normalize_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_method("sidak")


def test_methods_list_is_canonical():
    assert METHODS[0] == "none"
    for m in METHODS[1:]:
        assert normalize_method(m) == m
