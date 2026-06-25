"""Engine matching / feasibility / ranking tests (fully offline)."""
import json
import os

from paperforge import manifest as M
from paperforge.engine import evaluate
from paperforge.report import render_csv, render_markdown

EXAMPLE = os.path.join(
    os.path.dirname(__file__), "..", "examples", "sleep_moa_manifest.json"
)


def _man(datasets, study="t"):
    return M.parse_manifest({"study": study, "datasets": datasets})


def test_multimodal_idea_appears_for_eeg_plus_resp():
    man = _man([
        {"modality": "eeg", "n": 40, "variables": ["delta_power"]},
        {"modality": "respiration", "n": 40, "variables": ["resp_rate", "rsa"]},
    ])
    ids = {r.idea_id for r in evaluate(man)}
    assert "eeg_resp_coupling" in ids
    # Single-modality EEG idea should also be present.
    assert "eeg_spectral_profile" in ids


def test_idea_not_offered_when_modality_missing():
    man = _man([{"modality": "eeg", "n": 40, "variables": ["delta_power"]}])
    ids = {r.idea_id for r in evaluate(man)}
    assert "eeg_resp_coupling" not in ids  # needs respiration too
    assert "hrv_questionnaire_arousal" not in ids  # needs watch+questionnaire


def test_multimodal_ranked_above_single_modality():
    man = _man([
        {"modality": "eeg", "n": 200, "variables": ["delta_power"]},
        {"modality": "respiration", "n": 200, "variables": ["resp_rate"]},
    ])
    results = evaluate(man)
    pos = {r.idea_id: i for i, r in enumerate(results)}
    assert pos["eeg_resp_coupling"] < pos["eeg_spectral_profile"]


def test_feasibility_flags_small_sample():
    # eeg_resp_coupling assumes correlation r=.30 -> needs N=85.
    small = _man([
        {"modality": "eeg", "n": 20, "variables": []},
        {"modality": "respiration", "n": 20, "variables": []},
    ])
    big = _man([
        {"modality": "eeg", "n": 120, "variables": []},
        {"modality": "respiration", "n": 120, "variables": []},
    ])
    r_small = next(r for r in evaluate(small) if r.idea_id == "eeg_resp_coupling")
    r_big = next(r for r in evaluate(big) if r.idea_id == "eeg_resp_coupling")
    assert r_small.required_n == 85
    assert r_small.feasible is False
    assert r_big.feasible is True


def test_limiting_sample_size_is_minimum():
    man = _man([
        {"modality": "eeg", "n": 100, "variables": []},
        {"modality": "respiration", "n": 30, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    assert r.available_n == 30  # the smaller of the two


def test_unknown_n_gives_unknown_feasibility():
    man = _man([
        {"modality": "eeg", "variables": []},
        {"modality": "respiration", "n": 50, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    assert r.available_n is None
    assert r.feasible is None
    assert r.feasibility_label == "표본수 미상"


def test_matched_variables_detected():
    man = _man([
        {"modality": "watch", "n": 100, "variables": ["RMSSD", "mean_HR"]},
        {"modality": "questionnaire", "n": 100, "variables": ["psqi_total"]},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "hrv_questionnaire_arousal")
    assert "rmssd" in r.matched_variables  # case-insensitive match


def test_example_manifest_full_run():
    with open(EXAMPLE, encoding="utf-8") as fh:
        man = M.parse_manifest(json.load(fh))
    results = evaluate(man)
    assert len(results) >= 6
    # Top idea must be multimodal.
    assert len(results[0].modalities) >= 2
    md = render_markdown(man, results, 0.05, 0.80)
    assert "논문 아이디어 매트릭스" in md
    assert "요약 매트릭스" in md
    csv_text = render_csv(results)
    assert csv_text.startswith("rank,idea_id")
    assert len(csv_text.strip().splitlines()) == len(results) + 1


def test_no_results_message():
    man = _man([{"modality": "behavior", "n": 10, "variables": []}])
    results = evaluate(man)
    # behavior alone matches no template (behavior_physio_link needs watch).
    assert results == []
    md = render_markdown(man, results, 0.05, 0.80)
    assert "매칭되는 아이디어가 없습니다" in md


def test_feasible_idea_ranked_above_infeasible():
    # EEG underpowered (n=20) but variable-rich; watch+questionnaire feasible.
    man = _man([
        {"modality": "eeg", "n": 20,
         "variables": [f"v{i}" for i in range(40)]},
        {"modality": "watch", "n": 300, "variables": ["rmssd"]},
        {"modality": "questionnaire", "n": 300, "variables": ["psqi_total"]},
    ])
    results = evaluate(man)
    pos = {r.idea_id: i for i, r in enumerate(results)}
    # Feasible watch×questionnaire idea must outrank the infeasible EEG-only one.
    assert pos["hrv_questionnaire_arousal"] < pos["eeg_spectral_profile"]
    assert next(r for r in results
                if r.idea_id == "hrv_questionnaire_arousal").feasible is True
    assert next(r for r in results
                if r.idea_id == "eeg_spectral_profile").feasible is False


def test_variable_count_does_not_outrank_extra_modality():
    # A 2-modality idea must beat a 1-modality idea even when the single
    # modality is far more variable-rich (capped variable term).
    man = _man([
        {"modality": "eeg", "n": 200,
         "variables": [f"v{i}" for i in range(50)]},
        {"modality": "respiration", "n": 200, "variables": ["resp_rate"]},
    ])
    results = evaluate(man)
    pos = {r.idea_id: i for i, r in enumerate(results)}
    assert pos["eeg_resp_coupling"] < pos["eeg_spectral_profile"]


def test_boolean_n_treated_as_unknown():
    man = M.parse_manifest(
        {"datasets": [{"modality": "eeg", "n": True, "variables": []}]}
    )
    assert man.datasets[0].n is None
    assert any("positive integer" in w for w in man.warnings)


def test_conflicting_modality_n_uses_min_conservatively():
    # Two EEG cohorts (40 and 200); linked N can't exceed the smaller.
    man = _man([
        {"modality": "eeg", "n": 200, "variables": ["a"]},
        {"modality": "eeg", "n": 40, "variables": ["b"]},
        {"modality": "respiration", "n": 200, "variables": ["resp_rate"]},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    assert r.available_n == 40  # min(40, 200), not max
    assert any("서로 다른 n" in w for w in man.warnings)


def test_exploratory_idea_is_its_own_tier():
    man = _man([{"modality": "watch", "n": 5, "variables": ["rmssd"]}])
    r = next(x for x in evaluate(man) if x.idea_id == "hrv_descriptive")
    assert r.exploratory is True
    assert r.required_n is None
    assert r.feasible is None
    assert "탐색적" in r.feasibility_label


def test_example_has_feasible_and_infeasible_ideas():
    # The shipped example must demonstrate the feasibility distinction.
    with open(EXAMPLE, encoding="utf-8") as fh:
        man = M.parse_manifest(json.load(fh))
    results = evaluate(man)
    assert any(r.feasible is True for r in results)
    assert any(r.feasible is False for r in results)
    assert results[0].feasible is True
    # No feasible idea may appear at or after the first infeasible one.
    first_infeasible = next(i for i, r in enumerate(results) if r.feasible is False)
    assert not any(
        results[i].feasible is True for i in range(first_infeasible, len(results))
    )
