"""Declared subject overlap, repeated-measures clustering, multiplicity, sided.

These are the features that change a feasibility verdict, so each test pins the
*number*, not just "it ran".
"""
import json
import math

import pytest

from paperforge import power
from paperforge.engine import evaluate
from paperforge.manifest import ManifestError, parse_manifest, parse_csv_manifest


def _man(datasets, **extra):
    data = {"study": "t", "datasets": datasets}
    data.update(extra)
    return parse_manifest(data)


def _by_id(results, idea_id):
    for r in results:
        if r.idea_id == idea_id:
            return r
    raise AssertionError(f"idea {idea_id} not produced")


# --- declared linkage --------------------------------------------------------

def test_linked_n_caps_available_n_below_min_of_modalities():
    datasets = [
        {"modality": "eeg", "n": 90, "variables": ["a"]},
        {"modality": "respiration", "n": 90, "variables": ["b"]},
    ]
    without = _by_id(evaluate(_man(datasets)), "eeg_resp_coupling")
    assert without.available_n == 90
    assert without.linked_declared is False

    with_link = _by_id(
        evaluate(_man(datasets, linked_n={"eeg+respiration": 40})),
        "eeg_resp_coupling",
    )
    assert with_link.available_n == 40
    assert with_link.linked_declared is True
    # And the verdict actually flips (85 needed for r=0.30).
    assert without.feasible is True and with_link.feasible is False


def test_linked_n_never_inflates_beyond_declared_dataset_n():
    # A linkage larger than the datasets themselves must not raise available_n.
    man = _man(
        [{"modality": "eeg", "n": 30, "variables": []},
         {"modality": "respiration", "n": 30, "variables": []}],
        linked_n={"eeg+respiration": 900},
    )
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 30


def test_linkage_subset_applies_to_larger_combination():
    # A declared EEG+watch overlap constrains the 3-modality composite idea too:
    # you cannot have all three in more subjects than have those two.
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "watch", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n={"eeg+watch": 55},
    )
    assert _by_id(evaluate(man), "multimodal_arousal_index").available_n == 55


def test_linkage_for_unrelated_pair_does_not_apply():
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []},
         {"modality": "watch", "n": 90, "variables": []}],
        linked_n={"watch+questionnaire": 10},
    )
    # questionnaire isn't in the eeg+respiration idea, so the cap must not apply.
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 90


def test_linked_n_aliases_separators_and_symmetry():
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n={"호흡밴드 × 뇌파": 44},
    )
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 44


def test_duplicate_linkage_keys_keep_the_smaller_count():
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n={"eeg+respiration": 60, "respiration+eeg": 45},
    )
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 45


@pytest.mark.parametrize("links,fragment", [
    ({"eeg": 30}, "2개 미만"),
    ({"eeg+unobtanium": 30}, "인식할 수 없어"),
    ({"eeg+respiration": 0}, "양의 정수가 아니라"),
    ({"eeg+respiration": "many"}, "양의 정수가 아니라"),
])
def test_bad_linkage_entries_warn_and_are_ignored(links, fragment):
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n=links,
    )
    assert man.linked_n == {}
    assert any(fragment in w for w in man.warnings)
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 90


def test_linked_n_wrong_type_raises():
    with pytest.raises(ManifestError):
        _man([{"modality": "eeg", "n": 5}], linked_n=[["eeg", "watch", 3]])


def test_linked_n_referencing_absent_modality_warns():
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n={"eeg+behavior": 20},
    )
    assert any("데이터셋에 없는 항목" in w for w in man.warnings)


def test_csv_linkage_rows_are_not_datasets():
    text = (
        "modality,n,variables\n"
        "뇌파,90,alpha\n"
        "호흡밴드,90,resp_rate\n"
        "뇌파+호흡밴드,40,\n"
    )
    man = parse_csv_manifest(text)
    assert len(man.datasets) == 2
    assert man.linked_n == {frozenset({"eeg", "respiration"}): 40}
    assert _by_id(evaluate(man), "eeg_resp_coupling").available_n == 40


# --- repeated measures / design effect ---------------------------------------

def test_design_effect_formula_and_guards():
    assert power.design_effect(1, 0.5) == 1.0
    assert power.design_effect(3, 0.0) == 1.0
    assert math.isclose(power.design_effect(3, 0.3), 1.6)
    assert math.isclose(power.design_effect(4, 1.0), 4.0)
    for bad in [(0, 0.3), (3, -0.1), (3, 1.1)]:
        with pytest.raises(ValueError):
            power.design_effect(*bad)


def test_rows_to_subjects_and_back_are_consistent():
    # 85 rows, 3 repeats, ICC .3 -> DE 1.6 -> ceil(85*1.6/3) = 46 subjects,
    # and 46 subjects supply floor(46*3/1.6) = 86 >= 85 rows.
    assert power.rows_to_subjects(85, 3, 0.3) == 46
    assert power.subjects_to_rows(46, 3, 0.3) >= 85
    assert power.subjects_to_rows(45, 3, 0.3) < 85
    # ICC=1 means repeats add nothing: subjects == rows.
    assert power.rows_to_subjects(85, 5, 1.0) == 85
    assert power.subjects_to_rows(85, 5, 1.0) == 85
    # Never claim fewer effective rows than subjects.
    assert power.subjects_to_rows(10, 1, 0.0) == 10


def test_repeats_reduce_subject_requirement_for_observation_level_designs():
    datasets = [
        {"modality": "eeg", "n": 50, "variables": []},
        {"modality": "respiration", "n": 50, "variables": []},
    ]
    plain = _by_id(evaluate(_man(datasets)), "eeg_resp_coupling")
    assert plain.required_n == 85 and plain.feasible is False

    # 3 nights/subject at ICC .3 -> DE 1.6 -> 46 subjects suffice, so 50 in hand
    # flips the verdict. This is the whole point of the repeated-measures flag.
    clustered = _by_id(
        evaluate(_man(datasets), repeats=3, icc=0.3), "eeg_resp_coupling"
    )
    assert clustered.required_rows == 85
    assert clustered.required_n == 46
    assert clustered.analysis_n == power.subjects_to_rows(50, 3, 0.3)
    assert clustered.feasible is True
    assert any("설계효과" in n for n in clustered.notes)


def test_repeats_do_not_rescale_subject_level_designs():
    datasets = [
        {"modality": "eeg", "n": 40, "variables": []},
        {"modality": "moa", "n": 40, "variables": []},
    ]
    plain = _by_id(evaluate(_man(datasets)), "moa_responder_profiling")
    clustered = _by_id(
        evaluate(_man(datasets), repeats=4, icc=0.2), "moa_responder_profiling"
    )
    assert clustered.required_n == plain.required_n
    assert clustered.analysis_n == plain.analysis_n
    assert any("적용하지 않았습니다" in n for n in clustered.notes)


def test_icc_one_makes_repeats_worthless():
    datasets = [
        {"modality": "eeg", "n": 40, "variables": []},
        {"modality": "respiration", "n": 40, "variables": []},
    ]
    plain = _by_id(evaluate(_man(datasets)), "eeg_resp_coupling")
    clustered = _by_id(
        evaluate(_man(datasets), repeats=10, icc=1.0), "eeg_resp_coupling"
    )
    assert clustered.required_n == plain.required_n


# --- multiplicity ------------------------------------------------------------

def test_n_tests_bonferroni_inflates_required_n():
    datasets = [
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ]
    base = _by_id(evaluate(_man(datasets)), "eeg_resp_coupling")
    corrected = _by_id(evaluate(_man(datasets), n_tests=5), "eeg_resp_coupling")
    assert corrected.required_n > base.required_n
    # Exactly the alpha/5 answer, not an ad-hoc fudge.
    assert corrected.required_n == power.n_for_correlation(0.30, alpha=0.05 / 5)
    assert corrected.attained_power < base.attained_power
    assert any("Bonferroni" in n for n in corrected.notes)


def test_n_tests_one_is_a_no_op():
    datasets = [{"modality": "eeg", "n": 90, "variables": []},
                {"modality": "respiration", "n": 90, "variables": []}]
    a = _by_id(evaluate(_man(datasets)), "eeg_resp_coupling")
    b = _by_id(evaluate(_man(datasets), n_tests=1), "eeg_resp_coupling")
    assert (a.required_n, a.attained_power) == (b.required_n, b.attained_power)


def test_invalid_engine_arguments_raise():
    man = _man([{"modality": "eeg", "n": 90, "variables": []}])
    for kwargs in [{"sided": 3}, {"n_tests": 0}, {"repeats": 0},
                   {"icc": 1.5}, {"alpha": 0.0}, {"power": 1.0}]:
        with pytest.raises(ValueError):
            evaluate(man, **kwargs)


def test_huge_n_tests_is_rejected_not_silently_zero():
    man = _man([{"modality": "eeg", "n": 90, "variables": []}])
    # alpha/n_tests must stay > 0; an absurd K that underflows is an error.
    with pytest.raises(ValueError):
        evaluate(man, n_tests=10 ** 400)


# --- one-sided ---------------------------------------------------------------

def test_one_sided_lowers_required_n_for_z_designs_only():
    datasets = [
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
        {"modality": "watch", "n": 90, "variables": []},
    ]
    two = evaluate(_man(datasets))
    one = evaluate(_man(datasets), sided=1)
    corr_two = _by_id(two, "eeg_resp_coupling")
    corr_one = _by_id(one, "eeg_resp_coupling")
    assert corr_one.required_n < corr_two.required_n
    assert any("단측검정" in n for n in corr_one.notes)

    # The ΔR² (F-based) idea must be untouched, and must say so.
    f_two = _by_id(two, "multimodal_arousal_index")
    f_one = _by_id(one, "multimodal_arousal_index")
    assert f_one.required_n == f_two.required_n
    assert any("적용되지 않았습니다" in n for n in f_one.notes)


def test_sided_one_matches_hand_computed_correlation_n():
    # z_{0.95}=1.6448536, z_{0.80}=0.8416212; atanh(0.3)=0.3095196
    expected = math.ceil(((1.6448536269514722 + 0.8416212335729143)
                          / math.atanh(0.3)) ** 2 + 3)
    assert power.n_for_correlation(0.30, sided=1) == expected


# --- report/JSON surface -----------------------------------------------------

def test_json_exposes_linkage_and_effective_alpha():
    from paperforge.report import render_json
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []}],
        linked_n={"eeg+respiration": 50},
    )
    results = evaluate(man, n_tests=4)
    payload = json.loads(render_json(
        man, results, 0.05, 0.80, 0.0,
        settings={"n_tests": 4, "sided": 2, "repeats": 1, "icc": 0.0},
    ))
    assert payload["linked_n"] == {"eeg+respiration": 50}
    assert math.isclose(payload["parameters"]["alpha_effective"], 0.0125)
    idea = next(i for i in payload["ideas"] if i["idea_id"] == "eeg_resp_coupling")
    assert idea["linked_declared"] is True
    assert idea["available_n"] == 50
    assert 0.0 <= idea["attained_power"] <= 1.0


# --- review round 2026-07-31: regressions for reported defects ---------------

def test_declared_linkage_is_reported_even_when_not_binding():
    """A user who declared an overlap must never be told they didn't.

    Regression: `linked_declared` used to be `declared <= base`, so declaring a
    linkage LARGER than the smallest modality n made the report say "연결
    표본수가 선언되지 않아 ... 최소값을 사용했습니다".
    """
    man = _man(
        [{"modality": "eeg", "n": 50, "variables": []},
         {"modality": "respiration", "n": 92, "variables": []}],
        linked_n={"eeg+respiration": 90},
    )
    r = _by_id(evaluate(man), "eeg_resp_coupling")
    assert r.linked_declared is True
    assert r.available_n == 50  # the contradiction resolves to the smaller value
    assert not any("선언되지 않아" in n for n in r.notes)
    assert any("모순" in n for n in r.notes)


def test_consistent_declaration_reports_no_contradiction():
    man = _man(
        [{"modality": "eeg", "n": 92, "variables": []},
         {"modality": "respiration", "n": 92, "variables": []}],
        linked_n={"eeg+respiration": 60},
    )
    r = _by_id(evaluate(man), "eeg_resp_coupling")
    assert r.available_n == 60 and r.linked_declared is True
    assert not any("모순" in n for n in r.notes)


def test_repeats_do_not_shrink_subject_level_correlation_templates():
    """Psychometric validation and device agreement are one row per subject.

    Regression: `--repeats/--icc` keyed only off the effect family, so it cut
    the required N of a Cronbach-alpha study from 85 to 46 — measuring the same
    people three times does not substitute for more people.
    """
    psycho = _man([{"modality": "questionnaire", "n": 90, "variables": []}])
    plain = _by_id(evaluate(psycho), "questionnaire_psychometrics")
    clustered = _by_id(
        evaluate(psycho, repeats=3, icc=0.3), "questionnaire_psychometrics"
    )
    assert clustered.required_n == plain.required_n == 85

    devices = _man([{"modality": "eeg", "n": 90, "variables": []},
                    {"modality": "watch", "n": 90, "variables": []}])
    plain = _by_id(evaluate(devices), "watch_vs_eeg_validation")
    clustered = _by_id(
        evaluate(devices, repeats=3, icc=0.3), "watch_vs_eeg_validation"
    )
    assert clustered.required_n == plain.required_n


def test_analysis_unit_override_is_honoured_both_ways():
    from paperforge.templates import parse_template_pack
    base = {
        "id": "x", "title": "t", "required": ["eeg"], "optional": [],
        "hypothesis": "h", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "effect": {"type": "correlation", "r": 0.3},
    }
    man = _man([{"modality": "eeg", "n": 90, "variables": []}])
    as_subject = parse_template_pack([dict(base, analysis_unit="subject")])
    as_obs = parse_template_pack([dict(base, analysis_unit="observation")])
    n_sub = evaluate(man, templates=as_subject, repeats=3, icc=0.3)[0].required_n
    n_obs = evaluate(man, templates=as_obs, repeats=3, icc=0.3)[0].required_n
    assert n_sub == 85
    assert n_obs == 46


def test_optional_modality_shortfall_is_flagged():
    """Held N comes from required modalities only — say so when it misleads.

    With 90 EEG + 90 respiration but only 5 questionnaires, the EEG-respiration
    idea still reports N=90, yet its hypothesis is about subjective sleep
    quality. The report must name the 5.
    """
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
        {"modality": "questionnaire", "n": 5, "variables": ["psqi"]},
    ])
    r = _by_id(evaluate(man), "eeg_resp_coupling")
    assert r.available_n == 90
    note = [n for n in r.notes if "선택 모달리티" in n]
    assert note and "5명뿐" in note[0]


def test_no_shortfall_note_when_optional_modality_is_large_enough():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
        {"modality": "questionnaire", "n": 95, "variables": ["psqi"]},
    ])
    r = _by_id(evaluate(man), "eeg_resp_coupling")
    assert not any("선택 모달리티" in n for n in r.notes)


def test_optional_shortfall_respects_declared_linkage():
    man = _man(
        [{"modality": "eeg", "n": 90, "variables": []},
         {"modality": "respiration", "n": 90, "variables": []},
         {"modality": "questionnaire", "n": 90, "variables": ["psqi"]}],
        linked_n={"eeg+questionnaire": 12},
    )
    r = _by_id(evaluate(man), "eeg_resp_coupling")
    assert any("12명뿐" in n for n in r.notes)


def test_behavior_korean_alias_matches_the_printed_label():
    man = _man([{"modality": "행동", "n": 30, "variables": []},
                {"modality": "watch", "n": 30, "variables": []}])
    assert man.warnings == []
    assert _by_id(evaluate(man), "behavior_physio_link").available_n == 30
