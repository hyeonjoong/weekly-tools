"""Responder / MCID and reliable-change checks against published worked values."""

from __future__ import annotations

import math

import pytest

from longistat.dataio import Panel
from longistat.responder import (chi2_2x2, fisher_exact_2x2, improvement,
                                 _newcombe_rd, _ratio_ci, rci_analysis,
                                 responder_analysis)


def _panel(values, groups=None, times=("기저", "8주")):
    return Panel(subjects=[f"S{i}" for i in range(len(values))],
                 times=list(times), values=[list(v) for v in values],
                 groups=list(groups) if groups else None,
                 group_name="군" if groups else None, value_name="ISI")


def test_fisher_exact_hand_computed():
    # 2x2 with all margins 4: P(x) = C(4,x)^2 / 70; observed x = 3 → 16/70.
    # Tables no more likely: x in {0,1,3,4} → (1+16+16+1)/70 = 34/70
    assert math.isclose(fisher_exact_2x2(3, 1, 1, 3), 34 / 70, rel_tol=1e-12)
    assert fisher_exact_2x2(2, 2, 2, 2) == pytest.approx(1.0)
    assert fisher_exact_2x2(0, 0, 3, 4) == 1.0          # empty row
    with pytest.raises(ValueError):
        fisher_exact_2x2(-1, 1, 1, 1)


def test_chi2_hand_computed():
    # n=40, a=15,b=5,c=5,d=15 → chi2 = 40*(225-25)^2 / (20*20*20*20) = 10.0
    chi2, p = chi2_2x2(15, 5, 5, 15)
    assert math.isclose(chi2, 10.0, rel_tol=1e-12)
    assert 0.001 < p < 0.002
    yates, _ = chi2_2x2(15, 5, 5, 15, yates=True)
    assert yates < chi2
    assert math.isnan(chi2_2x2(0, 0, 3, 4)[0])


def test_newcombe_risk_difference_matches_the_published_example():
    """Newcombe (1998) example: 56/70 vs 48/80 → RD 0.20, CI (0.0524, 0.3339)."""
    diff, lo, hi = _newcombe_rd(56, 70, 48, 80, 0.05)
    assert math.isclose(diff, 0.2, rel_tol=1e-12)
    assert math.isclose(lo, 0.05242, abs_tol=1e-4)
    assert math.isclose(hi, 0.33387, abs_tol=1e-4)


def test_risk_and_odds_ratio_hand_computed():
    rr, lo, hi = _ratio_ci(20, 50, 10, 50, 0.05, odds=False)
    assert math.isclose(rr, 2.0, rel_tol=1e-12)
    se = math.sqrt(1 / 20 - 1 / 50 + 1 / 10 - 1 / 50)
    assert math.isclose(lo, math.exp(math.log(2.0) - 1.959963984540054 * se),
                        rel_tol=1e-9)
    orr, _, _ = _ratio_ci(20, 50, 10, 50, 0.05, odds=True)
    assert math.isclose(orr, (20 * 40) / (30 * 10), rel_tol=1e-12)


def test_zero_cell_gets_the_haldane_correction():
    rr, lo, hi = _ratio_ci(0, 20, 5, 20, 0.05, odds=False)
    assert math.isfinite(rr) and lo > 0 and hi > rr


def test_improvement_direction():
    assert improvement(20.0, 12.0, lower_is_better=True) == 8.0
    assert improvement(20.0, 12.0, lower_is_better=False) == -8.0


def test_responder_counts_and_direction():
    # lower is better; MCID 5.  Improvements: 8, 4, 5, 0  → responders 8 and 5
    panel = _panel([[20, 12], [20, 16], [20, 15], [20, 20]])
    res = responder_analysis(panel, 0, 5.0, lower_is_better=True)
    rate = res.rates[0]
    assert rate.responders == 2 and rate.n == 4
    assert math.isclose(rate.rate, 0.5)
    flipped = responder_analysis(panel, 0, 5.0, lower_is_better=False)
    assert flipped.rates[0].responders == 0


def test_percent_responder_uses_relative_improvement():
    panel = _panel([[20, 10], [10, 8]])       # 50 % and 20 % improvement
    res = responder_analysis(panel, 0, 30.0, lower_is_better=True, percent=True)
    assert res.rates[0].responders == 1
    assert res.kind == "%"


def test_percent_responder_skips_zero_baselines():
    panel = _panel([[0, 5], [20, 10]])
    res = responder_analysis(panel, 0, 30.0, lower_is_better=True, percent=True)
    assert res.rates[0].n == 1
    assert any("기준값이 0" in n for n in res.notes)


def test_group_contrast_reports_rd_rr_or_and_nnt():
    values = [[20, 10]] * 8 + [[20, 19]] * 2 + [[20, 19]] * 8 + [[20, 10]] * 2
    groups = ["능동"] * 10 + ["가짜"] * 10
    res = responder_analysis(_panel(values, groups), 0, 5.0,
                             lower_is_better=True)
    con = res.contrasts[0]
    assert con.rate_a == pytest.approx(0.8)
    assert con.rate_b == pytest.approx(0.2)
    assert con.risk_difference == pytest.approx(0.6)
    assert con.risk_ratio == pytest.approx(4.0)
    assert con.odds_ratio == pytest.approx((8 * 8) / (2 * 2))
    assert con.nnt == pytest.approx(1 / 0.6)
    assert "NNT" in con.nnt_note
    assert con.p_raw < 0.05


def test_responder_requires_a_positive_threshold():
    with pytest.raises(ValueError):
        responder_analysis(_panel([[1, 2], [2, 3]]), 0, 0.0)
    with pytest.raises(ValueError):
        responder_analysis(_panel([[1, 2], [2, 3]]), 5, 1.0)


def test_rci_hand_computed():
    """SD = 10, r = .8 → S_diff = √2·10·√.2 = 6.3246.

    A 7-point gain gives RCI = 1.107 (unchanged); 13 points gives 2.055
    (reliably improved); −13 points gives reliable deterioration.
    """
    panel = _panel([[20, 13], [20, 7], [20, 33]])
    res = rci_analysis(panel, 0, reliability=0.8, lower_is_better=True,
                       sd_baseline=10.0)
    assert math.isclose(res.s_diff, math.sqrt(2) * 10 * math.sqrt(0.2),
                        rel_tol=1e-12)
    row = res.rows[0]
    assert (row.improved, row.unchanged, row.deteriorated) == (1, 1, 1)


def test_rci_recovery_uses_the_direction():
    panel = _panel([[20, 5], [20, 9]])
    res = rci_analysis(panel, 0, reliability=0.9, lower_is_better=True,
                       sd_baseline=10.0, recovery_cutoff=7.0)
    row = res.rows[0]
    assert row.improved == 2 and row.recovered == 1


def test_rci_validates_its_inputs():
    panel = _panel([[20, 10], [18, 12]])
    with pytest.raises(ValueError):
        rci_analysis(panel, 0, reliability=1.0)
    with pytest.raises(ValueError):
        rci_analysis(panel, 0, reliability=0.9, sd_baseline=0.0)


def test_rci_defaults_to_the_observed_baseline_sd():
    panel = _panel([[10, 5], [20, 15], [30, 25], [40, 35]])
    res = rci_analysis(panel, 0, reliability=0.9)
    from longistat.basics import sd
    assert math.isclose(res.sd_baseline, sd([10.0, 20.0, 30.0, 40.0]))
    assert any("표본이 작아" in n for n in res.notes)
