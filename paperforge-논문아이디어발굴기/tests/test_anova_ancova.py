"""ANOVA (k arms) and ANCOVA (baseline-adjusted) sizing.

The reference values are G*Power's, recomputed by hand where a published table
exists, so a regression in the non-central F machinery or in the ANCOVA variance
factor shows up as a wrong number rather than a wrong-looking one.
"""
import math
from pathlib import Path

import pytest

from paperforge.engine import evaluate
from paperforge.manifest import parse_manifest
from paperforge.power import (
    attained_power,
    detectable_effect,
    effect_magnitude,
    mdes_anova,
    mdes_ancova,
    n_for_anova,
    n_total_ancova,
    n_total_two_group,
    observation_level,
    power_for_anova,
    power_for_ancova,
    required_events,
    required_total_n,
    scale_effect,
)
from paperforge.templates import TemplateError, parse_template_pack, validate_effect

# Anchored on the repo root so the suite runs from any working directory.
CLINICAL_PACK = str(Path(__file__).resolve().parent.parent / "examples"
                    / "clinical_pack.json")


# --- one-way ANOVA ----------------------------------------------------------

@pytest.mark.parametrize("k, expected", [(2, 128), (3, 159), (4, 180), (5, 200)])
def test_anova_matches_gpower_medium_effect(k, expected):
    """f=0.25, alpha=.05, power=.80 — G*Power's one-way omnibus table."""
    assert n_for_anova(0.25, k) == expected


def test_anova_k2_is_the_exact_version_of_the_two_group_normal_target():
    """Two arms: the omnibus F *is* the t-test, so the two agree to rounding.

    The ANOVA path solves the exact non-central F (128 = 64/arm, G*Power's
    answer); the two-group path is the normal approximation the module docstring
    already flags as landing ~1 per group low (126 = 63/arm). Neither is a bug;
    this pins the size of the documented gap so it cannot silently widen.
    """
    assert n_for_anova(0.25, 2) == 128
    assert n_total_two_group(0.5) == 126


@pytest.mark.parametrize("f, k, expected", [(0.40, 3, 66), (0.10, 4, 1096),
                                            (0.25, 3, 159)])
def test_anova_known_points(f, k, expected):
    assert n_for_anova(f, k) == expected


@pytest.mark.parametrize("k", [2, 3, 5, 7])
def test_anova_n_is_a_multiple_of_the_arm_count(k):
    n = n_for_anova(0.3, k)
    assert n % k == 0


@pytest.mark.parametrize("k", [2, 3, 4, 6])
def test_anova_n_reaches_the_target_power(k):
    n = n_for_anova(0.25, k)
    assert power_for_anova(0.25, n, k) >= 0.80
    # ...and one arm fewer would not (the search returns the SMALLEST N).
    assert power_for_anova(0.25, n - k, k) < 0.80


def test_anova_power_rises_with_n():
    prev = 0.0
    for n in (30, 60, 120, 240):
        pw = power_for_anova(0.25, n, 3)
        assert pw > prev
        prev = pw


def test_anova_mdes_round_trips():
    for n in (60, 159, 300):
        f = mdes_anova(n, 3)
        assert power_for_anova(f, n, 3) == pytest.approx(0.80, abs=1e-4)
        # The MDES is the smallest detectable f, so the N it implies is <= n.
        assert n_for_anova(f, 3) <= n


def test_anova_mdes_shrinks_as_n_grows():
    assert mdes_anova(600, 3) < mdes_anova(150, 3) < mdes_anova(40, 3)


def test_anova_higher_alpha_needs_fewer_subjects():
    assert n_for_anova(0.25, 3, alpha=0.10) < n_for_anova(0.25, 3, alpha=0.05)
    assert n_for_anova(0.25, 3, alpha=0.01) > n_for_anova(0.25, 3, alpha=0.05)


def test_anova_higher_power_needs_more_subjects():
    assert n_for_anova(0.25, 3, power=0.90) > n_for_anova(0.25, 3, power=0.80)


@pytest.mark.parametrize("bad", [0, 1, -3, 1001])
def test_anova_rejects_impossible_group_counts(bad):
    with pytest.raises(ValueError):
        n_for_anova(0.25, bad)


@pytest.mark.parametrize("bad_f", [0.0, float("nan"), float("inf"), 1e7])
def test_anova_rejects_impossible_effects(bad_f):
    with pytest.raises(ValueError):
        n_for_anova(bad_f, 3)


def test_anova_treats_f_as_a_magnitude():
    """Cohen's f is non-negative by construction; a signed input is its size."""
    assert n_for_anova(-0.25, 3) == n_for_anova(0.25, 3)


def test_anova_power_needs_enough_subjects_for_residual_df():
    with pytest.raises(ValueError):
        power_for_anova(0.25, 3, 3)  # n == k leaves zero denominator df


# --- ANCOVA -----------------------------------------------------------------

def test_ancova_with_no_covariate_information_is_the_plain_two_group_target():
    """rho=0 buys nothing, so the target is the t-test N plus the df it costs."""
    assert n_total_ancova(0.4, 0.0) == n_total_two_group(0.4) + 2


def test_ancova_shrinks_by_one_minus_rho_squared():
    """The whole point: 1 - 0.6^2 = 0.64 of the unadjusted residual variance.

    Recomputed from first principles rather than from the shipped constant:
    N = (1-rho^2)(z_.975 + z_.80)^2 / (0.25 d^2), ceiled once, + (k_cov + 1).
    """
    za, zb = 1.959963985, 0.841621234
    exact = 0.64 * (za + zb) ** 2 / (0.25 * 0.4 ** 2)
    assert exact == pytest.approx(125.59, abs=0.01)
    assert n_total_ancova(0.4, 0.6) == math.ceil(exact) + 2 == 128
    # The unadjusted target is the same expression without the 0.64.
    assert n_total_two_group(0.4) == math.ceil(exact / 0.64) == 197


@pytest.mark.parametrize("rho", [0.0, 0.2, 0.5, 0.8, 0.95])
def test_ancova_is_monotone_in_rho(rho):
    """A better covariate never costs more subjects."""
    assert n_total_ancova(0.4, rho) <= n_total_ancova(0.4, 0.0)


def test_ancova_sign_of_rho_is_irrelevant():
    assert n_total_ancova(0.4, -0.6) == n_total_ancova(0.4, 0.6)


def test_ancova_extra_covariates_cost_one_subject_each():
    base = n_total_ancova(0.4, 0.6, 1)
    assert n_total_ancova(0.4, 0.6, 2) == base + 1
    assert n_total_ancova(0.4, 0.6, 5) == base + 4


def test_ancova_unbalanced_allocation_costs_subjects():
    assert n_total_ancova(0.4, 0.6, allocation=0.3) > n_total_ancova(0.4, 0.6)


def test_ancova_mdes_is_the_exact_inverse():
    for n in (64, 128, 300):
        d = mdes_ancova(n, 0.6)
        assert n_total_ancova(d, 0.6) == n
        assert power_for_ancova(d, n, 0.6) == pytest.approx(0.80, abs=1e-5)


def test_ancova_attained_power_reaches_target_at_required_n():
    for rho in (0.0, 0.3, 0.7):
        n = n_total_ancova(0.5, rho)
        assert power_for_ancova(0.5, n, rho) >= 0.80


def test_ancova_one_sided_needs_fewer_than_two_sided():
    assert n_total_ancova(0.4, 0.6, sided=1) < n_total_ancova(0.4, 0.6, sided=2)


@pytest.mark.parametrize("bad_rho", [1.0, -1.0, 1.5, float("nan")])
def test_ancova_rejects_impossible_correlations(bad_rho):
    with pytest.raises(ValueError):
        n_total_ancova(0.4, bad_rho)


def test_ancova_rejects_zero_covariates():
    with pytest.raises(ValueError):
        n_total_ancova(0.4, 0.5, 0)


def test_ancova_power_needs_more_than_the_covariate_df():
    with pytest.raises(ValueError):
        power_for_ancova(0.4, 4, 0.5, 1)


# --- dispatcher integration -------------------------------------------------

ANOVA_EFFECT = {"type": "anova", "f": 0.25, "k_groups": 3}
ANCOVA_EFFECT = {"type": "ancova", "d": 0.4, "r_covariate": 0.6,
                 "k_covariates": 1, "allocation": 0.5}


def test_required_total_n_dispatches_both_families():
    assert required_total_n(ANOVA_EFFECT) == 159
    assert required_total_n(ANCOVA_EFFECT) == 128


def test_required_events_is_none_for_both():
    assert required_events(ANOVA_EFFECT) is None
    assert required_events(ANCOVA_EFFECT) is None


def test_detectable_effect_reports_the_right_metric():
    assert detectable_effect(ANOVA_EFFECT, 240)["metric"] == "f"
    assert detectable_effect(ANCOVA_EFFECT, 240)["metric"] == "d"


def test_attained_power_dispatches_both_families():
    """Pinned to literals, not to the callee.

    Comparing the dispatcher against `power_for_anova(...)` only proves the
    wiring: replacing that function's body with `return 0.123456` still passed.
    These two constants are the values scipy's `ncf.sf` / the normal CDF give
    at the shipped reference designs, so a change in the math fails here.
    """
    assert attained_power(ANOVA_EFFECT, 159) == pytest.approx(0.80489, abs=1e-5)
    assert attained_power(ANCOVA_EFFECT, 128) == pytest.approx(0.80130, abs=1e-5)
    # ...and the dispatcher must still route to the family-specific function.
    assert attained_power(ANOVA_EFFECT, 159) == power_for_anova(0.25, 159, 3)
    assert attained_power(ANCOVA_EFFECT, 128) == power_for_ancova(0.4, 128, 0.6)


def test_attained_power_returns_none_below_the_usable_n():
    assert attained_power(ANOVA_EFFECT, 3) is None
    assert attained_power(ANCOVA_EFFECT, 4) is None
    assert detectable_effect(ANOVA_EFFECT, 3) is None
    assert detectable_effect(ANCOVA_EFFECT, 4) is None


def test_scale_effect_moves_the_right_quantity():
    assert scale_effect(ANOVA_EFFECT, 0.5)["f"] == pytest.approx(0.125)
    scaled = scale_effect(ANCOVA_EFFECT, 0.5)
    assert scaled["d"] == pytest.approx(0.2)
    # rho is a property of the measurement, not of the effect being planned for.
    assert scaled["r_covariate"] == 0.6


def test_effect_magnitude_matches_the_sensitivity_column():
    assert effect_magnitude(ANOVA_EFFECT) == 0.25
    assert effect_magnitude(ANCOVA_EFFECT) == 0.4


def test_both_families_are_sized_in_subjects_not_observations():
    """--repeats/--icc must not divide a per-arm headcount by the design effect."""
    assert observation_level(ANOVA_EFFECT) is False
    assert observation_level(ANCOVA_EFFECT) is False


# --- template validation ----------------------------------------------------

def _pack(effect):
    return [{
        "id": "t1", "title": "T", "required": ["clinical"],
        "hypothesis": "H", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "effect": effect,
    }]


def test_template_pack_accepts_both_families():
    got = parse_template_pack(_pack(dict(ANOVA_EFFECT)))
    assert got[0]["effect"] == ANOVA_EFFECT
    got = parse_template_pack(_pack(dict(ANCOVA_EFFECT)))
    assert got[0]["effect"]["r_covariate"] == 0.6


def test_ancova_covariate_defaults_are_filled_in():
    out = validate_effect({"type": "ancova", "d": 0.5}, "<t>")
    assert out == {"type": "ancova", "d": 0.5, "r_covariate": 0.0,
                   "k_covariates": 1}


@pytest.mark.parametrize("effect, fragment", [
    ({"type": "anova", "f": 0.25, "k_groups": 1}, "k_groups"),
    ({"type": "anova", "f": 0.25}, "k_groups"),
    ({"type": "anova", "k_groups": 3}, "f"),
    ({"type": "anova", "f": 0.25, "k_groups": 2.5}, "whole number"),
    ({"type": "anova", "f": 0.25, "k_groups": 5000}, "1000"),
    ({"type": "ancova", "d": 0.4, "r_covariate": 1.0}, "r_covariate"),
    ({"type": "ancova", "d": 0.4, "r_covariate": "0.5"}, "r_covariate"),
    ({"type": "ancova", "d": 0.0}, "d"),
    ({"type": "ancova", "d": 0.4, "k_covariates": 0}, "k_covariates"),
    ({"type": "ancova", "d": 0.4, "allocation": 1.0}, "allocation"),
])
def test_template_pack_rejects_bad_specs(effect, fragment):
    with pytest.raises(TemplateError) as exc:
        parse_template_pack(_pack(effect))
    assert fragment in str(exc.value)


def test_noninferiority_is_not_defined_for_these_families():
    for etype, extra in (("anova", {"f": 0.25, "k_groups": 3}),
                         ("ancova", {"d": 0.4})):
        effect = {"type": etype, "design": "noninferiority"}
        effect.update(extra)
        with pytest.raises(TemplateError):
            parse_template_pack(_pack(effect))


# --- end-to-end through evaluate() ------------------------------------------

MANIFEST = {
    "study": "S",
    "datasets": [
        {"name": "crf", "modality": "clinical", "n": 240,
         "variables": ["arm", "endpoint", "baseline"]},
    ],
}


def _evaluate(effect, **kwargs):
    manifest = parse_manifest(dict(MANIFEST))
    return evaluate(manifest, templates=parse_template_pack(_pack(effect)),
                    **kwargs)[0]


def test_evaluate_reports_the_anova_arm_split_in_the_justification():
    r = _evaluate(dict(ANOVA_EFFECT))
    assert r.required_n == 159
    assert "군당 53명 × 3군" in r.justification
    assert "일원배치 분산분석" in r.justification
    assert r.feasible is True


def test_evaluate_quotes_the_unadjusted_target_for_ancova():
    """The saved N is the reason to run an ANCOVA; state both numbers."""
    r = _evaluate(dict(ANCOVA_EFFECT))
    assert r.required_n == 128
    assert "ρ=0.60" in r.justification
    assert "198명이 필요" in r.justification  # the unadjusted two-group target
    assert "분석계획서에 명시" in r.justification


def test_ancova_justification_omits_the_comparison_when_rho_is_zero():
    r = _evaluate({"type": "ancova", "d": 0.4, "r_covariate": 0.0})
    assert "공변량 보정 없이" not in r.justification


def test_evaluate_sensitivity_strip_names_the_metric():
    assert [s["metric"] for s in _evaluate(dict(ANOVA_EFFECT)).n_sensitivity] \
        == ["f", "f", "f"]
    assert [s["metric"] for s in _evaluate(dict(ANCOVA_EFFECT)).n_sensitivity] \
        == ["d", "d", "d"]


def test_evaluate_detectable_labels_render():
    assert _evaluate(dict(ANOVA_EFFECT)).detectable_label.startswith("f≥")
    assert _evaluate(dict(ANCOVA_EFFECT)).detectable_label.startswith("d≥")


def test_repeats_do_not_shrink_a_per_arm_headcount():
    """3 nights per subject yields no extra trial arms and no extra subjects."""
    plain = _evaluate(dict(ANOVA_EFFECT))
    clustered = _evaluate(dict(ANOVA_EFFECT), repeats=3, icc=0.3)
    assert clustered.required_n == plain.required_n
    assert any("표본 단위가 피험자" in n for n in clustered.notes)


def test_bonferroni_correction_flows_through_both_families():
    for effect in (dict(ANOVA_EFFECT), dict(ANCOVA_EFFECT)):
        base = _evaluate(effect)
        corrected = _evaluate(effect, n_tests=5)
        assert corrected.required_n > base.required_n


def test_one_sided_note_is_explicit_for_both_families():
    """--one-sided moves an ANCOVA target and cannot move an omnibus F one."""
    anova_1 = _evaluate(dict(ANOVA_EFFECT), sided=1)
    assert anova_1.required_n == _evaluate(dict(ANOVA_EFFECT)).required_n
    assert any("단측 개념이 없어" in n for n in anova_1.notes)

    ancova_1 = _evaluate(dict(ANCOVA_EFFECT), sided=1)
    assert ancova_1.required_n < _evaluate(dict(ANCOVA_EFFECT)).required_n
    assert any("방향 가설일 때만" in n for n in ancova_1.notes)


# --- regression: misplaced non-inferiority declaration (review round 3) ------
#
# A template carries two `design` fields. Writing "noninferiority" in the
# free-text top-level one used to be silent, and the run reported a SUPERIORITY
# sample size as a confident number for a regulatory-facing design.

from paperforge.knowledge import IDEA_TEMPLATES  # noqa: E402
from paperforge.templates import merge_templates  # noqa: E402


def _merge(design_text, effect):
    pack = parse_template_pack(_pack(effect))
    pack[0]["design"] = design_text
    return merge_templates(IDEA_TEMPLATES, [pack])[1]


@pytest.mark.parametrize("text", [
    "randomised, double-blind, non-inferiority trial",
    "noninferiority",
    "비열등성 병행군 설계",
    "NONINFERIORITY",
])
def test_free_text_noninferiority_without_the_effect_switch_warns(text):
    warnings = _merge(text, {"type": "two_group", "d": 0.3})
    assert any("우월성(superiority) 기준" in w for w in warnings)
    assert any("margin_d/margin/margin_hr" in w for w in warnings)


def test_a_properly_declared_ni_template_does_not_warn():
    effect = {"type": "two_group", "design": "noninferiority",
              "d": 0.0, "margin_d": 0.3}
    warnings = _merge("randomised non-inferiority trial", effect)
    assert not any("우월성" in w for w in warnings)


def test_an_ordinary_superiority_template_does_not_warn():
    warnings = _merge("randomised parallel-group", {"type": "two_group", "d": 0.3})
    assert not any("우월성" in w for w in warnings)


def test_builtin_and_example_packs_are_clean():
    """The shipped templates must not trip the guard themselves."""
    from paperforge.templates import load_template_pack

    pack = load_template_pack(CLINICAL_PACK)
    assert not any("우월성" in w
                   for w in merge_templates(IDEA_TEMPLATES, [pack])[1])


def test_example_pack_ships_a_working_noninferiority_template():
    """The README points at ni_response_rate; it must exist and size as NI."""
    from paperforge.templates import load_template_pack

    pack = load_template_pack(CLINICAL_PACK)
    ni = next(t for t in pack if t["id"] == "ni_response_rate")
    assert ni["effect"]["design"] == "noninferiority"
    assert required_total_n(ni["effect"]) == 660


# --- regression: correctness review round 3 ---------------------------------

def test_observation_override_cannot_shrink_a_per_arm_headcount():
    """Each subject is randomised to ONE arm; 4 nights yield no extra arms.

    Honouring `analysis_unit: "observation"` here cut a 3-arm ANOVA from 159 to
    64 and an ANCOVA from 128 to 52, flipping both verdicts to "충분 가능" — the
    same defect already guarded for survival/two_proportion.
    """
    for effect in (dict(ANOVA_EFFECT), dict(ANCOVA_EFFECT)):
        raw = _pack(effect)
        raw[0]["analysis_unit"] = "observation"
        with pytest.raises(TemplateError) as exc:
            parse_template_pack(raw)
        assert "per subject" in str(exc.value)


def test_subject_level_override_is_still_allowed():
    for effect in (dict(ANOVA_EFFECT), dict(ANCOVA_EFFECT)):
        raw = _pack(effect)
        raw[0]["analysis_unit"] = "subject"
        assert parse_template_pack(raw)[0]["analysis_unit"] == "subject"


@pytest.mark.parametrize("power", [0.05, 0.04, 0.03, 0.026])
def test_anova_mdes_refuses_a_power_target_at_or_below_alpha(power):
    """An F test rejects with probability alpha at zero effect, so below that
    every effect is 'detectable' — it used to print `f≥0.00`."""
    with pytest.raises(ValueError) as exc:
        mdes_anova(150, 3, 0.05, power)
    assert "alpha" in str(exc.value)
    assert detectable_effect(dict(ANOVA_EFFECT), 150, 0.05, power) is None


def test_anova_mdes_still_works_just_above_alpha():
    assert mdes_anova(150, 3, 0.05, 0.06) > 0.0


@pytest.mark.parametrize("n", [4, 5])
def test_anova_mdes_is_suppressed_when_only_an_absurd_effect_would_show(n):
    """f>3 is eta^2>0.9; the f2>9 guard for regression is exactly the same rule.

    n=4 (one residual df) gives f=12.8 and n=5 gives f=3.53 — both suppressed.
    n=6 gives f=2.27, which is still enormous but sits inside the same bound the
    regression families already accept, so it is reported rather than hidden.
    """
    assert detectable_effect(dict(ANOVA_EFFECT), n) is None
    assert detectable_effect(dict(ANOVA_EFFECT), 6)["value"] == pytest.approx(
        2.274, abs=0.01
    )


def test_anova_mdes_is_reported_once_there_are_residual_df_to_spare():
    got = detectable_effect(dict(ANOVA_EFFECT), 240)
    assert got["metric"] == "f" and got["value"] < 3.0


def test_ancova_never_returns_an_n_its_own_power_function_refuses():
    """d>=1.5 with a strong covariate returned N=5, which power_for_ancova then
    refused — the row read "권장 N=5 / 충분 가능" with blank power and MDES."""
    for d in (1.5, 2.0, 3.0):
        for rho in (0.0, 0.6, 0.9):
            n = n_total_ancova(d, rho)
            assert attained_power(
                {"type": "ancova", "d": d, "r_covariate": rho}, n
            ) is not None
            assert n_total_ancova(mdes_ancova(n, rho), rho) == n


def test_ancova_sentence_does_not_claim_a_saving_it_did_not_make():
    r = _evaluate({"type": "ancova", "d": 0.4, "r_covariate": 0.05})
    assert "표본수 감소가 정당화된다" not in r.justification
    assert "표본 감소가 사실상 없으므로" in r.justification


def test_over_large_anova_n_names_cohens_f_not_f_squared():
    with pytest.raises(ValueError) as exc:
        n_for_anova(1e-9, 3)
    assert "(f)" in str(exc.value)
