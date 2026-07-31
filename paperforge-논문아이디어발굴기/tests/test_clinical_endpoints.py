"""Binary (two-proportion) and time-to-event (log-rank) sample-size families.

Every expected number here is either a published table value or hand-derived
from the closed form in the docstring, not a snapshot of what the code returned.
"""
import json
import math
import pathlib

import pytest

from paperforge.cli import main
from paperforge.engine import evaluate, sample_size_justification
from paperforge.manifest import parse_manifest
from paperforge.power import (
    attained_power,
    detectable_effect,
    effect_magnitude,
    events_for_survival,
    expected_events,
    mdes_survival,
    mdes_two_proportions,
    n_for_survival,
    n_for_two_proportions,
    norm_ppf,
    power_for_survival,
    power_for_two_proportions,
    required_events,
    required_total_n,
    scale_effect,
)
from paperforge.report import render_csv, render_json, render_markdown
from paperforge.templates import TemplateError, parse_template_pack

Z_A = norm_ppf(0.975)
Z_B = norm_ppf(0.80)


# --- two proportions ---------------------------------------------------------


def _hand_n_two_prop(p1, p2, w1=0.5, za=Z_A, zb=Z_B):
    """The formula from the docstring, recomputed independently here."""
    w2 = 1.0 - w1
    pbar = w1 * p1 + w2 * p2
    pooled = math.sqrt(pbar * (1 - pbar) / (w1 * w2))
    unpooled = math.sqrt(p1 * (1 - p1) / w1 + p2 * (1 - p2) / w2)
    return math.ceil((za * pooled + zb * unpooled) ** 2 / (p1 - p2) ** 2)


@pytest.mark.parametrize("p1,p2", [(0.30, 0.50), (0.10, 0.20), (0.05, 0.15),
                                   (0.60, 0.75), (0.50, 0.30)])
def test_two_proportion_matches_hand_computation(p1, p2):
    assert n_for_two_proportions(p1, p2) == _hand_n_two_prop(p1, p2)


def test_two_proportion_classic_table_value():
    # Fleiss (no continuity correction): 30% vs 50% needs ~93/group.
    assert n_for_two_proportions(0.30, 0.50) == 186


def test_two_proportion_symmetric_in_arguments():
    assert n_for_two_proportions(0.3, 0.5) == n_for_two_proportions(0.5, 0.3)


def test_two_proportion_baseline_moves_n_through_the_variance():
    # Same 0.10 risk difference: binomial variance peaks at p=0.5, so a contrast
    # straddling the middle is the EXPENSIVE one (783 vs 281 here). This is the
    # property a Cohen's-d stand-in gets wrong, and the reason the family exists.
    assert n_for_two_proportions(0.45, 0.55) > n_for_two_proportions(0.05, 0.15)
    # ...but a fixed *relative* risk gets harder as the baseline falls.
    assert n_for_two_proportions(0.02, 0.04) > n_for_two_proportions(0.20, 0.40)


def test_two_proportion_unbalanced_allocation_costs_sample():
    balanced = n_for_two_proportions(0.3, 0.5, allocation=0.5)
    skewed = n_for_two_proportions(0.3, 0.5, allocation=0.25)
    assert skewed > balanced


def test_two_proportion_one_sided_needs_fewer():
    assert (n_for_two_proportions(0.3, 0.5, sided=1)
            < n_for_two_proportions(0.3, 0.5, sided=2))


def test_two_proportion_power_reaches_target_at_required_n():
    for p1, p2 in [(0.3, 0.5), (0.1, 0.2), (0.7, 0.9)]:
        n = n_for_two_proportions(p1, p2)
        assert power_for_two_proportions(p1, p2, n) >= 0.80
        # ...and one subject fewer is (essentially) not enough.
        assert power_for_two_proportions(p1, p2, n - 2) < 0.8005


def test_two_proportion_power_is_monotone_in_n():
    prev = 0.0
    for n in range(20, 400, 20):
        pw = power_for_two_proportions(0.3, 0.5, n)
        assert pw >= prev
        prev = pw


def test_two_proportion_rejects_bad_inputs():
    for bad in (0.0, 1.0, -0.1, 1.5, float("nan")):
        with pytest.raises(ValueError):
            n_for_two_proportions(bad, 0.5)
    with pytest.raises(ValueError):
        n_for_two_proportions(0.4, 0.4)  # zero risk difference
    with pytest.raises(ValueError):
        n_for_two_proportions(0.3, 0.5, allocation=0.0)


def test_mdes_two_proportions_round_trips():
    n = 400
    p2 = mdes_two_proportions(n, 0.30)
    assert 0.30 < p2 < 1.0
    assert power_for_two_proportions(0.30, p2, n) >= 0.80 - 1e-9
    # The N implied by the MDES is the N we started from (± the ceil).
    assert abs(n_for_two_proportions(0.30, p2) - n) <= 2


def test_mdes_two_proportions_direction_follows_template():
    # A template expecting a *drop* (e.g. AE rate 0.30 -> 0.15) must report a
    # detectable rate below the control rate, not above it.
    det = detectable_effect({"type": "two_proportion", "p1": 0.30, "p2": 0.15}, 300)
    assert det["p2"] < 0.30
    assert det["metric"] == "delta_p"
    assert det["value"] == pytest.approx(0.30 - det["p2"], abs=1e-4)


def test_mdes_two_proportions_none_when_hopeless():
    # 6 subjects cannot detect anything at 80% power -> None, not a crash.
    assert detectable_effect(
        {"type": "two_proportion", "p1": 0.30, "p2": 0.50}, 6
    ) is None


# --- survival ----------------------------------------------------------------


@pytest.mark.parametrize("hr,expected", [(0.70, 247), (0.50, 66), (0.75, 380),
                                         (1.5, 191)])
def test_events_matches_schoenfeld_by_hand(hr, expected):
    hand = math.ceil((Z_A + Z_B) ** 2 / (0.25 * math.log(hr) ** 2))
    assert events_for_survival(hr) == hand == expected


def test_events_symmetric_on_log_scale():
    assert events_for_survival(0.5) == events_for_survival(2.0)


def test_survival_subjects_follow_event_rate():
    events = events_for_survival(0.70)
    assert n_for_survival(0.70, 1.0) == events
    assert n_for_survival(0.70, 0.60) == math.ceil(events / 0.60) == 412
    # Half the event rate, twice the enrolment.
    assert n_for_survival(0.70, 0.25) == math.ceil(events / 0.25)


def test_survival_power_reaches_target():
    n = n_for_survival(0.70, 0.60)
    assert power_for_survival(0.70, n, 0.60) >= 0.80
    assert power_for_survival(0.70, n // 2, 0.60) < 0.80


def test_expected_events_floors_and_handles_none():
    assert expected_events(100, 0.605) == 60
    assert expected_events(None, 0.5) is None
    assert expected_events(10, 1.0) == 10
    # Exact-integer products must not be knocked down by float error.
    assert expected_events(30, 0.1) == 3


def test_mdes_survival_round_trips_and_reports_both_directions():
    hr = mdes_survival(400, 0.60)
    assert hr > 1.0
    events = expected_events(400, 0.60)
    assert events_for_survival(hr) <= events + 1
    det = detectable_effect({"type": "survival", "hr": 0.7, "event_rate": 0.6}, 400)
    assert det["metric"] == "hr"
    assert det["hr_protective"] == pytest.approx(1 / det["value"], abs=1e-3)
    assert det["events"] == 240


def test_survival_rejects_degenerate_inputs():
    for bad in (0.0, -1.0, float("nan"), float("inf"), 1.0):
        with pytest.raises(ValueError):
            events_for_survival(bad)
    with pytest.raises(ValueError):
        n_for_survival(0.7, 0.0)
    with pytest.raises(ValueError):
        n_for_survival(0.7, 1.5)
    # A cohort too small to yield a single event is not sizeable, not a crash.
    assert attained_power(
        {"type": "survival", "hr": 0.7, "event_rate": 0.01}, 50
    ) is None


# --- dispatch, scaling, magnitude -------------------------------------------


def test_required_total_n_dispatches_new_families():
    assert required_total_n(
        {"type": "two_proportion", "p1": 0.3, "p2": 0.5}
    ) == 186
    assert required_total_n(
        {"type": "survival", "hr": 0.7, "event_rate": 0.6}
    ) == 412


def test_required_events_only_for_survival():
    assert required_events({"type": "survival", "hr": 0.7, "event_rate": 0.6}) == 247
    assert required_events({"type": "correlation", "r": 0.3}) is None


def test_effect_magnitude_new_families():
    assert effect_magnitude(
        {"type": "two_proportion", "p1": 0.3, "p2": 0.5}
    ) == pytest.approx(0.2)
    assert effect_magnitude({"type": "survival", "hr": 0.7}) == 0.7


def test_scale_effect_hazard_ratio_is_log_scaled():
    halved = scale_effect({"type": "survival", "hr": 0.5}, 0.5)
    assert halved["hr"] == pytest.approx(math.sqrt(0.5))
    # A weaker assumed effect must cost more events, never fewer.
    assert events_for_survival(halved["hr"]) > events_for_survival(0.5)


def test_scale_effect_risk_difference_stays_a_probability():
    shrunk = scale_effect({"type": "two_proportion", "p1": 0.3, "p2": 0.9}, 0.5)
    assert shrunk["p2"] == pytest.approx(0.6)
    blown = scale_effect({"type": "two_proportion", "p1": 0.3, "p2": 0.9}, 10.0)
    assert 0.0 < blown["p2"] < 1.0  # clipped, not 6.3
    assert n_for_two_proportions(0.3, blown["p2"]) > 0


def test_sensitivity_strip_is_monotone_for_new_families():
    manifest = parse_manifest({
        "study": "s",
        "datasets": [{"modality": "clinical", "n": 500, "variables": ["pfs"]}],
    })
    tpl = [{
        "id": "surv", "title": "t", "required": ["clinical"], "optional": [],
        "hypothesis": "h", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "effect": {"type": "survival", "hr": 0.7, "event_rate": 0.6},
    }]
    (res,) = evaluate(manifest, templates=tpl)
    ns = [s["required_n"] for s in res.n_sensitivity]
    assert ns[0] > ns[1] > ns[2]  # conservative > planned > optimistic
    assert res.required_events == 247
    assert res.expected_events == 300


# --- template pack validation ------------------------------------------------


def _pack(effect):
    return [{
        "id": "x", "title": "t", "required": ["clinical"], "optional": [],
        "hypothesis": "h", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "effect": effect,
    }]


def test_pack_accepts_new_effect_types():
    out = parse_template_pack(
        _pack({"type": "two_proportion", "p1": 0.3, "p2": 0.5, "allocation": 0.4})
    )
    assert out[0]["effect"]["allocation"] == 0.4
    out = parse_template_pack(
        _pack({"type": "survival", "hr": 0.7, "event_rate": 1.0})
    )
    assert out[0]["effect"]["event_rate"] == 1.0


@pytest.mark.parametrize("effect", [
    {"type": "two_proportion", "p1": 0.3},                     # missing p2
    {"type": "two_proportion", "p1": 0.3, "p2": 0.3},          # zero difference
    {"type": "two_proportion", "p1": 0.0, "p2": 0.5},          # not a probability
    {"type": "two_proportion", "p1": 0.3, "p2": 1.0},
    {"type": "two_proportion", "p1": 0.3, "p2": "0.5"},        # wrong dtype
    {"type": "survival", "hr": 0.7},                           # missing event_rate
    {"type": "survival", "hr": 1.0, "event_rate": 0.5},        # no effect
    {"type": "survival", "hr": -0.7, "event_rate": 0.5},
    {"type": "survival", "hr": 0.7, "event_rate": 1.5},        # not a probability
    {"type": "survival", "hr": 0.7, "event_rate": 0.0},
    {"type": "survival", "hr": True, "event_rate": 0.5},       # bool is not a number
])
def test_pack_rejects_malformed_clinical_effects(effect):
    with pytest.raises(TemplateError):
        parse_template_pack(_pack(effect))


# --- reporting ---------------------------------------------------------------


def _clinical_run(**kwargs):
    manifest = parse_manifest({
        "study": "onco",
        "datasets": [
            {"modality": "임상", "n": 240, "variables": ["pfs", "event"]},
            {"modality": "바이오마커", "n": 240, "variables": ["pdl1"]},
        ],
    })
    tpl = parse_template_pack({"templates": [
        dict(_pack({"type": "survival", "hr": 0.7, "event_rate": 0.6})[0],
             id="surv", required=["clinical", "lab"]),
        dict(_pack({"type": "two_proportion", "p1": 0.3, "p2": 0.5})[0],
             id="prop", required=["clinical", "lab"]),
    ]})
    return manifest, evaluate(manifest, templates=tpl, **kwargs)


def test_markdown_reports_events_and_justification():
    manifest, results = _clinical_run()
    md = render_markdown(manifest, results, 0.05, 0.80)
    assert "필요 사건 수(시간-사건 설계)** : 247" .replace(" :", ":") in md
    assert "247건" in md
    assert "표본수 산출 근거" in md
    assert "Schoenfeld" in md
    assert "HR≥" in md and "Δp≥" in md


def test_json_and_csv_carry_the_new_fields():
    manifest, results = _clinical_run(max_n=300)
    payload = json.loads(render_json(manifest, results, 0.05, 0.80))
    by_id = {i["idea_id"]: i for i in payload["ideas"]}
    assert by_id["surv"]["required_events"] == 247
    assert by_id["surv"]["expected_events"] == 144
    assert by_id["surv"]["within_max_n"] is False   # needs 412 > 300
    assert by_id["prop"]["within_max_n"] is True    # needs 186 <= 300
    assert by_id["prop"]["required_events"] is None
    assert "총 186명" in by_id["prop"]["sample_size_justification"]
    csv_text = render_csv(results)
    assert "required_events" in csv_text.splitlines()[0]
    assert "sample_size_justification" in csv_text.splitlines()[0]
    assert "247" in csv_text


def test_justification_mentions_every_active_correction():
    text = sample_size_justification(
        {"type": "correlation", "r": 0.3},
        alpha=0.05, alpha_eff=0.01, power=0.80, sided=1, required_n=46,
        required_rows=131, events=None, recruit_n=58, dropout=0.2, n_tests=5,
        repeats=3, icc=0.3, cluster_applies=True,
    )
    assert "단측" in text
    assert "α=0.01" in text and "Bonferroni" in text
    assert "설계효과" in text and "131" in text
    assert "58명" in text  # attrition-inflated recruitment target
    assert "계획용 근사치" in text


def test_justification_for_exploratory_designs_makes_no_claims():
    text = sample_size_justification(
        {"type": "exploratory"}, alpha=0.05, alpha_eff=0.05, power=0.8, sided=2,
        required_n=None, required_rows=None, events=None, recruit_n=None,
        dropout=0.0, n_tests=1, repeats=1, icc=0.0, cluster_applies=False,
    )
    assert "표본수 산출 공식을 적용하지 않는다" in text
    assert "명이다" not in text


# --- CLI ---------------------------------------------------------------------


PACK = str(pathlib.Path(__file__).resolve().parents[1]
           / "examples" / "clinical_pack.json")


@pytest.fixture()
def onco_manifest(tmp_path):
    path = tmp_path / "onco.json"
    path.write_text(json.dumps({
        "study": "onco",
        "datasets": [
            {"modality": "임상", "n": 240, "variables": ["pfs"]},
            {"modality": "lab", "n": 240, "variables": ["pdl1"]},
        ],
    }), encoding="utf-8")
    return str(path)


def test_cli_max_n_flags_unreachable_ideas(onco_manifest, capsys):
    assert main(["--templates", PACK, "--no-builtin", "--max-n", "300",
                 onco_manifest]) == 0
    assert "모집 상한" in capsys.readouterr().out


def test_cli_rejects_bad_max_n(onco_manifest, capsys):
    for bad in ("0", "-5", "abc", "3.5"):
        with pytest.raises(SystemExit) as exc:
            main([onco_manifest, "--max-n", bad])
        assert exc.value.code == 2
        assert "--max-n" in capsys.readouterr().err


def test_cli_feasible_only_filters(onco_manifest, capsys):
    assert main(["--templates", PACK, "--no-builtin", onco_manifest]) == 0
    full = capsys.readouterr().out
    assert main(["--templates", PACK, "--no-builtin", "--feasible-only",
                 onco_manifest]) == 0
    only = capsys.readouterr().out
    assert "표본 부족 우려" in full
    assert "표본 부족 우려" not in only
    assert "충분 가능" in only


def test_cli_feasible_only_applies_before_top(onco_manifest, capsys):
    # --top must count *kept* ideas, not slice first and filter after.
    assert main(["--templates", PACK, "--no-builtin", "--feasible-only",
                 "--top", "1", onco_manifest]) == 0
    out = capsys.readouterr().out
    assert "생성된 아이디어: 1개" in out
    assert "충분 가능" in out


def test_cli_feasible_only_empty_result_warns(tmp_path, capsys):
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps({
        "study": "tiny",
        "datasets": [{"modality": "임상", "n": 4, "variables": ["pfs"]},
                     {"modality": "lab", "n": 4, "variables": ["x"]}],
    }), encoding="utf-8")
    assert main(["--templates", PACK, "--no-builtin", "--feasible-only",
                 str(path)]) == 0
    out = capsys.readouterr().out
    assert "--feasible-only" in out
    assert "매칭되는 아이디어가 없습니다" in out


def test_shipped_clinical_pack_loads_and_runs(onco_manifest, capsys):
    assert main(["--templates", PACK, onco_manifest]) == 0
    assert "247건" in capsys.readouterr().out


# --- regressions from the hardening round -----------------------------------


MANIFEST = str(pathlib.Path(__file__).resolve().parents[1]
               / "examples" / "clinical_manifest.json")


def test_shipped_clinical_manifest_exercises_both_new_families(capsys):
    # The docs point at this pair; if it stops producing a binary AND a
    # time-to-event idea, the documented feature is unreachable again.
    assert main(["--templates", PACK, MANIFEST]) == 0
    out = capsys.readouterr().out
    assert "Δp≥" in out and "HR≥" in out
    assert "247건" in out


def test_analysis_unit_observation_cannot_shrink_subject_level_designs():
    # Honouring the override divided the survival target by the design effect
    # (412 -> 134) and flipped the verdict, while the same record still said
    # 247 events were required.
    manifest = parse_manifest({
        "study": "s",
        "datasets": [{"modality": "clinical", "n": 300, "variables": ["pfs"]}],
    })
    for effect in ({"type": "survival", "hr": 0.7, "event_rate": 0.6},
                   {"type": "two_proportion", "p1": 0.3, "p2": 0.5}):
        tpl = [dict(_pack(effect)[0], analysis_unit="observation")]
        (plain,) = evaluate(manifest, templates=tpl)
        (clustered,) = evaluate(manifest, templates=tpl, repeats=4, icc=0.1)
        assert clustered.required_n == plain.required_n
        assert clustered.feasible == plain.feasible


def test_pack_rejects_observation_unit_on_subject_level_effects():
    for effect in ({"type": "survival", "hr": 0.7, "event_rate": 0.6},
                   {"type": "two_proportion", "p1": 0.3, "p2": 0.5}):
        raw = dict(_pack(effect)[0], analysis_unit="observation")
        with pytest.raises(TemplateError):
            parse_template_pack([raw])
    # 'subject' stays legal, and correlation may still opt into observation.
    parse_template_pack([dict(_pack(
        {"type": "survival", "hr": 0.7, "event_rate": 0.6})[0],
        analysis_unit="subject")])
    parse_template_pack([dict(_pack({"type": "correlation", "r": 0.3})[0],
                              analysis_unit="observation")])


@pytest.mark.parametrize("hr,scale", [(1.5, 1e9), (10.0, 1e6), (0.7, 1000.0),
                                      (1e300, 1.5), (1e-300, 1.5)])
def test_scale_effect_survival_never_overflows(hr, scale):
    out = scale_effect({"type": "survival", "hr": hr}, scale)
    assert math.isfinite(out["hr"]) and out["hr"] > 0.0


def test_cli_survives_extreme_effect_scale_on_survival(tmp_path, capsys):
    pack = tmp_path / "hr.json"
    pack.write_text(json.dumps({"templates": [
        dict(_pack({"type": "survival", "hr": 1.5, "event_rate": 0.6})[0],
             required=["clinical"]),
    ]}), encoding="utf-8")
    man = tmp_path / "m.json"
    man.write_text(json.dumps({
        "datasets": [{"modality": "clinical", "n": 300, "variables": ["pfs"]}],
    }), encoding="utf-8")
    # Used to escape as an uncaught OverflowError (exit 1) from math.exp.
    rc = main(["--templates", str(pack), "--no-builtin", "--effect-scale",
               "1e9", str(man)])
    assert rc in (0, 2)
    assert "Traceback" not in capsys.readouterr().err


def test_events_round_trip_is_not_inflated_by_one_ulp():
    # events/event_rate landing on 40.00000000000001 used to ceil to 41.
    assert n_for_survival(math.exp(
        (Z_A + Z_B) / math.sqrt(40 * 0.25)), 0.20) == 200


def test_justification_states_both_arms_and_the_allocation():
    text = sample_size_justification(
        {"type": "two_proportion", "p1": 0.05, "p2": 0.10},
        alpha=0.05, alpha_eff=0.05, power=0.80, sided=2, required_n=869,
        required_rows=None, events=None, recruit_n=None, dropout=0.0,
        n_tests=1, repeats=1, icc=0.0, cluster_applies=False,
    )
    # 434+434 = 868 misses the target power; the arms must sum to the total.
    assert "435" in text and "434" in text
    surv = sample_size_justification(
        {"type": "survival", "hr": 0.7, "event_rate": 0.6, "allocation": 1 / 3},
        alpha=0.05, alpha_eff=0.05, power=0.80, sided=2, required_n=463,
        required_rows=None, events=278, recruit_n=None, dropout=0.0,
        n_tests=1, repeats=1, icc=0.0, cluster_applies=False,
    )
    # Without the split, "278 events" is not reproducible from the stated inputs.
    assert "33.33%:66.67%" in surv
    assert "비례위험" in surv


def test_justification_quotes_the_observation_target_when_clustered():
    text = sample_size_justification(
        {"type": "correlation", "r": 0.3}, alpha=0.05, alpha_eff=0.05,
        power=0.80, sided=2, required_n=46, required_rows=85, events=None,
        recruit_n=None, dropout=0.0, n_tests=1, repeats=3, icc=0.3,
        cluster_applies=True,
    )
    # Previously read "필요 표본은 총 46명이다 … 85개를 46명으로 환산했다".
    assert "85개이다" in text
    assert "총 46명이다" not in text


def test_justification_leaves_a_hole_for_effect_size_provenance():
    text = sample_size_justification(
        {"type": "correlation", "r": 0.3}, alpha=0.05, alpha_eff=0.05,
        power=0.80, sided=2, required_n=85, required_rows=None, events=None,
        recruit_n=None, dropout=0.0, n_tests=1, repeats=1, icc=0.0,
        cluster_applies=False,
    )
    assert "출처 기재" in text  # cannot be submitted unedited unnoticed
    assert "G*Power" in text


@pytest.mark.parametrize("number,expected", [
    ("0.70", "을"), ("0.30", "을"), ("84", "를"), ("186", "을"),
    ("1.60", "을"), ("0.25", "를"), ("412", "를"),
    # "…퍼센트" ends in a vowel whatever the digits are.
    ("5%", "를"), ("20%", "를"), ("60%", "를"), ("5.5%", "를"),
])
def test_korean_object_particle_follows_the_final_digit(number, expected):
    from paperforge.engine import _obj
    assert _obj(number) == expected


def test_generated_sentences_have_no_wrong_particles():
    manifest, results = _clinical_run()
    for r in results:
        for wrong in ("0.70를", "0.30를", "1.60를", "0.50를"):
            assert wrong not in r.justification


def test_percentages_do_not_round_away_the_event_rate():
    text = sample_size_justification(
        {"type": "survival", "hr": 0.7, "event_rate": 0.055},
        alpha=0.05, alpha_eff=0.05, power=0.80, sided=2, required_n=4491,
        required_rows=None, events=247, recruit_n=None, dropout=0.0,
        n_tests=1, repeats=1, icc=0.0, cluster_applies=False,
    )
    assert "5.5%" in text and "6%" not in text


def test_one_sided_caveat_covers_the_new_families():
    manifest, results = _clinical_run(sided=1)
    for r in results:
        assert any("단측검정" in n for n in r.notes), r.idea_id


def test_sensitivity_strip_names_its_metric():
    manifest, results = _clinical_run()
    md = render_markdown(manifest, results, 0.05, 0.80)
    assert "HR 0.7" in md and "Δp 0.2" in md
    surv = next(r for r in results if r.idea_id == "surv")
    assert all(s["metric"] == "HR" for s in surv.n_sensitivity)


def test_unmatched_templates_are_reported_not_dropped_silently():
    manifest = parse_manifest({
        "study": "s",
        "datasets": [{"modality": "eeg", "n": 50, "variables": ["alpha"]}],
    })
    evaluate(manifest, templates=parse_template_pack({"templates": [
        dict(_pack({"type": "survival", "hr": 0.7, "event_rate": 0.6})[0],
             id="surv", required=["clinical", "lab"]),
    ]}))
    assert any("제외됐습니다" in w and "surv" in w for w in manifest.warnings)


def test_json_warnings_are_collapsed_like_the_markdown():
    manifest = parse_manifest({
        "study": "s",
        "datasets": [{"modality": "eeg", "n": 50, "variables": ["a"]}],
    })
    manifest.warnings.extend(["같은 경고"] * 5000)
    payload = json.loads(render_json(manifest, [], 0.05, 0.80))
    assert len(payload["warnings"]) < 10
    assert any("5000건" in w for w in payload["warnings"])


def test_utf16_csv_is_decoded_with_an_honest_warning(tmp_path):
    from paperforge.manifest import load_manifest
    path = tmp_path / "excel.csv"
    path.write_bytes(
        "modality,n,variables\n임상,120,pfs\n검사,110,pdl1\n".encode("utf-16")
    )
    manifest = load_manifest(str(path))
    assert {d.modality for d in manifest.datasets} == {"clinical", "lab"}
    assert any("UTF-16" in w for w in manifest.warnings)


def test_conflicting_n_warning_stays_short():
    datasets = [{"modality": "clinical", "n": n, "variables": ["pfs"]}
                for n in range(1000, 4000)]
    manifest = parse_manifest({"study": "s", "datasets": datasets})
    evaluate(manifest, templates=_pack({"type": "correlation", "r": 0.3}))
    conflict = [w for w in manifest.warnings if "서로 다른 n" in w]
    assert conflict and len(conflict[0]) < 300
    assert "1000" in conflict[0]  # the conservative minimum is still named
