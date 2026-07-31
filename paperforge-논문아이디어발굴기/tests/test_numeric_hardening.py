"""Regressions for defects found in the 2026-07-31 statistical review.

Each test names the wrong value the old code produced, so a future refactor
that reintroduces the bug fails with an obvious message rather than a vague
inequality.
"""
import math
import time

import pytest

from paperforge import power
from paperforge.cli import main
from paperforge.engine import evaluate
from paperforge.manifest import parse_manifest


# --- 1. power <= alpha/sided produced negative MDES and non-monotone N -------

def test_target_power_at_or_below_alpha_is_rejected():
    """Old behaviour: mdes_correlation(100, 0.05, 0.01) -> -0.03718."""
    for fn, args in [
        (power.mdes_correlation, (100,)),
        (power.mdes_two_group, (100,)),
        (power.mdes_paired, (100,)),
        (power.n_for_correlation, (0.30,)),
        (power.n_for_paired, (0.5,)),
        (power.n_per_group_two_means, (0.5,)),
        (power.n_total_two_group, (0.5,)),
        (power.n_for_regression, (0.15, 3)),
    ]:
        with pytest.raises(ValueError, match="false-positive"):
            fn(*args, alpha=0.05, power=0.01)


def test_one_sided_boundary_uses_alpha_over_sided():
    # Two-sided: the floor is alpha/2 = 0.025. One-sided: alpha = 0.05.
    power.mdes_correlation(100, alpha=0.05, power=0.03)            # ok, > 0.025
    with pytest.raises(ValueError):
        power.mdes_correlation(100, alpha=0.05, power=0.03, sided=1)
    with pytest.raises(ValueError):
        power.mdes_correlation(100, alpha=0.05, power=0.02)


def test_mdes_is_never_negative_across_a_wide_grid():
    for alpha in (0.001, 0.01, 0.05, 0.10, 0.20):
        for target in (0.30, 0.50, 0.80, 0.95, 0.999):
            for sided in (1, 2):
                if target <= alpha / sided:
                    continue
                assert power.mdes_correlation(100, alpha, target, sided) > 0
                assert power.mdes_two_group(100, alpha, target, 0.5, sided) > 0
                assert power.mdes_paired(100, alpha, target, sided) > 0


def test_required_n_is_monotone_in_target_power():
    """Old behaviour: squaring (za+zb) made N turn around and grow again."""
    for eff in ({"type": "correlation", "r": 0.30},
                {"type": "paired", "d": 0.5},
                {"type": "two_group", "d": 0.5}):
        ns = [power.required_total_n(eff, power=t)
              for t in (0.30, 0.50, 0.70, 0.80, 0.90, 0.95, 0.99)]
        assert ns == sorted(ns), f"{eff} -> {ns}"


def test_cli_rejects_degenerate_power_instead_of_printing_negative_mdes(
        tmp_path, capsys):
    m = tmp_path / "m.json"
    m.write_text('{"datasets":[{"modality":"eeg","n":90},'
                 '{"modality":"respiration","n":90}]}', encoding="utf-8")
    assert main([str(m), "--power", "0.01"]) == 2
    captured = capsys.readouterr()
    assert "분석 오류" in captured.err
    assert "≥-" not in captured.out


# --- 2. float rounding made subjects_to_rows lose a whole observation -------

def test_exact_boundary_conversion_does_not_lose_a_row():
    """Old behaviour: 11*3/1.1 = 29.999999999999996 -> floor 29, not 30."""
    assert power.design_effect(3, 0.05) == 1.1
    assert power.subjects_to_rows(11, 3, 0.05) == 30


def test_rows_subjects_round_trip_never_loses_information():
    for m in (1, 2, 3, 5, 10, 30):
        for i in range(0, 101):
            icc = i / 100
            for rows in (1, 2, 7, 30, 85, 125, 499):
                subjects = power.rows_to_subjects(rows, m, icc)
                assert power.subjects_to_rows(subjects, m, icc) >= rows


def test_feasible_verdict_never_contradicts_attained_power():
    """A "충분 가능" idea must not report power below the target.

    This is what the rounding bug produced: available_n == required_n, verdict
    True, attained power 0.7855 against a 0.80 target.
    """
    from paperforge.templates import parse_template_pack
    base = {
        "id": "x", "title": "t", "required": ["eeg"], "optional": [],
        "hypothesis": "h", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "analysis_unit": "observation",
    }
    for r in (0.2484, 0.4926, 0.30, 0.15):
        for repeats, icc in ((3, 0.05), (5, 0.3), (3, 0.3), (2, 0.5)):
            tpl = parse_template_pack(
                [dict(base, effect={"type": "correlation", "r": r})]
            )
            probe = evaluate(
                parse_manifest({"datasets": [{"modality": "eeg", "n": 1}]}),
                templates=tpl, repeats=repeats, icc=icc,
            )[0]
            need = probe.required_n
            res = evaluate(
                parse_manifest({"datasets": [{"modality": "eeg", "n": need}]}),
                templates=tpl, repeats=repeats, icc=icc,
            )[0]
            assert res.feasible is True
            assert res.attained_power >= 0.80 - 1e-12, (
                f"r={r} m={repeats} icc={icc}: 충분 가능 but power "
                f"{res.attained_power}"
            )


# --- 3. norm_ppf lost its advertised precision in the upper tail ------------

def test_norm_ppf_upper_tail_round_trips_through_the_cdf():
    """Old behaviour: catastrophic cancellation in the Halley step left ~1e-9
    error above p=1-1e-6 — the ONLY tail _z_alpha ever evaluates.

    The honest accuracy statement is a CDF round-trip: feeding norm_ppf(p) back
    through norm_cdf must return p. (Comparing against a decimal literal is not
    meaningful up there: the double nearest 1-1e-6 is not 1-1e-6, so the exact
    answer differs from -norm_ppf(1e-6) by ~1e-12 for input-representation
    reasons alone.)
    """
    for p_ in (1 - 1e-4, 1 - 1e-6, 1 - 1e-8, 1 - 1e-10, 1 - 1e-14,
               0.975, 0.995, 0.9995):
        assert math.isclose(
            power.norm_cdf(power.norm_ppf(p_)), p_, rel_tol=1e-14
        ), p_


def test_norm_ppf_reproduces_the_published_constants():
    # Values where the input double is unproblematic, so a literal comparison
    # is a real check on the algorithm rather than on float representation.
    for p_, expected in [
        (0.975, 1.9599639845400545),
        (0.995, 2.5758293035489004),
        (0.95, 1.6448536269514722),
        (0.80, 0.8416212335729143),
        (0.90, 1.2815515655446004),
    ]:
        assert math.isclose(power.norm_ppf(p_), expected, rel_tol=5e-15), p_


def test_norm_ppf_reflection_is_symmetric_where_p_is_representable():
    # 1-p must round-trip exactly for the comparison to test the algorithm.
    for p_ in (0.1, 0.25, 0.3, 0.4, 0.49, 0.05, 0.005):
        if 1.0 - (1.0 - p_) != p_:
            continue
        assert math.isclose(
            power.norm_ppf(p_), -power.norm_ppf(1.0 - p_), rel_tol=1e-13
        ), p_


def test_norm_ppf_matches_scipy_in_the_upper_tail():
    scipy_stats = pytest.importorskip("scipy.stats")
    for p_ in (1 - 1e-4, 1 - 1e-6, 1 - 1e-8, 1 - 1e-12, 1 - 1e-16,
               0.975, 0.9999):
        assert math.isclose(
            power.norm_ppf(p_), scipy_stats.norm.ppf(p_), rel_tol=1e-14
        ), p_


def test_norm_ppf_stays_finite_and_monotone_at_the_extremes():
    xs = [power.norm_ppf(p_) for p_ in
          (5e-324, 1e-300, 1e-100, 1e-16, 0.5, 1 - 1e-16)]
    assert all(math.isfinite(x) for x in xs)
    assert xs == sorted(xs)


# --- 4. _f_quantile silently clipped at a fixed 1e9 bracket -----------------

def test_f_quantile_grows_its_bracket_instead_of_clipping():
    """Old behaviour: returned exactly 1e9 (true value ~1.62e10), which
    understates f_crit and therefore OVERstates power."""
    q = power._f_quantile(1 - 5e-6, 1, 1)
    assert q > 1e10
    assert math.isclose(q, 1.6211384532e10, rel_tol=1e-6)
    # Round-trips through the CDF it inverts.
    assert math.isclose(power._f_cdf(q, 1, 1), 1 - 5e-6, rel_tol=1e-9)


def test_extreme_multiplicity_still_produces_a_sane_answer():
    n = power.n_for_regression(0.15, 3, alpha=0.05 / 10000)
    assert 150 < n < 400
    assert power.power_for_regression(0.15, n, 3, alpha=0.05 / 10000) >= 0.80
    assert power.power_for_regression(0.15, n - 1, 3, alpha=0.05 / 10000) < 0.80


# --- 5. MDES bisection returned the midpoint (below target power) -----------

def test_regression_mdes_actually_attains_the_target_power():
    """Old behaviour: mdes_regression(40,1) gave power 0.79999999992 and
    n_for_regression(mdes) came back as 41 instead of 40."""
    for n, k in [(40, 1), (30, 2), (90, 3), (200, 5), (25, 1), (60, 4)]:
        f2 = power.mdes_regression(n, k)
        assert power.power_for_regression(f2, n, k) >= 0.80
        assert power.n_for_regression(f2, k) == n


def test_regression_change_mdes_attains_the_target_power():
    for n, kt, kc in [(40, 1, 1), (90, 2, 1), (120, 3, 2), (30, 1, 0)]:
        f2 = power.mdes_regression_change(n, kt, kc)
        assert power.power_for_regression_change(f2, n, kt, kc) >= 0.80
        assert power.n_for_regression_change(f2, kt, kc) == n


# --- 6. the linear N scan took minutes for small effects -------------------

def test_tiny_effect_sizes_solve_quickly():
    """Old behaviour: f2=1.5e-4 stepped N one at a time to ~73,000 (~73 s)."""
    start = time.monotonic()
    n = power.n_for_regression(1.5e-4, 3)
    elapsed = time.monotonic() - start
    assert 70_000 < n < 76_000
    assert elapsed < 5.0, f"took {elapsed:.1f}s"


def test_bracketing_reproduces_the_gpower_reference_series():
    # The published anchor must survive the switch from scan to bisection.
    assert [power.n_for_regression(0.15, k) for k in range(1, 6)] == [
        55, 68, 77, 85, 92
    ]
    for f2, k in [(0.02, 1), (0.35, 3), (0.15, 5), (0.005, 1)]:
        n = power.n_for_regression(f2, k)
        assert power.power_for_regression(f2, n, k) >= 0.80
        assert power.power_for_regression(f2, n - 1, k) < 0.80


def test_absurdly_small_effect_raises_rather_than_hanging():
    with pytest.raises(ValueError, match="exceeds"):
        power.n_for_regression(1e-9, 1)
