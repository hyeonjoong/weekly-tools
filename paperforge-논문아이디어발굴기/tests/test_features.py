"""Integration tests for dropout adjustment, MDES surfacing, and JSON output."""
import json

import pytest

from paperforge import manifest as M
from paperforge.engine import evaluate
from paperforge.report import render_csv, render_json, render_markdown


def _man(datasets, study="t"):
    return M.parse_manifest({"study": study, "datasets": datasets})


def test_detectable_effect_on_result():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    assert r.detectable["metric"] == "r"
    assert 0.28 < r.detectable["value"] < 0.30
    assert r.detectable_label.startswith("r≥")


def test_detectable_none_when_n_unknown():
    man = _man([
        {"modality": "eeg", "variables": []},
        {"modality": "respiration", "n": 50, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    assert r.detectable is None
    assert r.detectable_label == "—"


def test_exploratory_has_no_detectable():
    man = _man([{"modality": "watch", "n": 30, "variables": ["rmssd"]}])
    r = next(x for x in evaluate(man) if x.idea_id == "hrv_descriptive")
    assert r.detectable is None


def test_dropout_inflates_recruit_n():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r0 = next(x for x in evaluate(man, dropout=0.0)
              if x.idea_id == "eeg_resp_coupling")
    r2 = next(x for x in evaluate(man, dropout=0.2)
              if x.idea_id == "eeg_resp_coupling")
    assert r0.recruit_n is None
    # required_n=85 -> ceil(85/0.8)=107
    assert r2.required_n == 85
    assert r2.recruit_n == 107


def test_dropout_does_not_change_feasibility():
    # Feasibility compares available vs analyzable required N, unaffected by dropout.
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r = next(x for x in evaluate(man, dropout=0.5)
             if x.idea_id == "eeg_resp_coupling")
    assert r.feasible is True


def test_dropout_out_of_range_raises():
    man = _man([{"modality": "eeg", "n": 30, "variables": []}])
    with pytest.raises(ValueError):
        evaluate(man, dropout=1.0)
    with pytest.raises(ValueError):
        evaluate(man, dropout=-0.1)


def test_exploratory_never_gets_recruit_n():
    man = _man([{"modality": "watch", "n": 30, "variables": ["rmssd"]}])
    r = next(x for x in evaluate(man, dropout=0.3)
             if x.idea_id == "hrv_descriptive")
    assert r.recruit_n is None


def test_markdown_shows_detectable_column_and_dropout():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    results = evaluate(man, dropout=0.2)
    md = render_markdown(man, results, 0.05, 0.80, dropout=0.2)
    assert "탐지가능 효과" in md
    assert "중도탈락 가정" in md
    assert "권장 모집 N" in md


def test_csv_has_new_columns():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    results = evaluate(man, dropout=0.2)
    csv_text = render_csv(results)
    header = csv_text.splitlines()[0]
    assert "recruit_n" in header
    assert "detectable_metric" in header
    assert "detectable_value" in header


def test_json_output_roundtrips():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": ["alpha"]},
        {"modality": "respiration", "n": 90, "variables": ["resp_rate"]},
    ])
    results = evaluate(man, dropout=0.2)
    payload = json.loads(render_json(man, results, 0.05, 0.80, 0.2))
    assert payload["study"] == "t"
    assert payload["parameters"]["alpha"] == 0.05
    assert payload["parameters"]["power"] == 0.80
    assert payload["parameters"]["dropout"] == 0.2
    # alpha_effective is always present so consumers never have to re-derive the
    # multiplicity correction.
    assert payload["parameters"]["alpha_effective"] == 0.05
    assert len(payload["ideas"]) == len(results)
    idea = payload["ideas"][0]
    assert idea["rank"] == 1
    for key in ("idea_id", "title", "required_n", "available_n",
                "detectable_effect", "feasible", "journal", "recruit_n",
                "attained_power", "analysis_n", "planned_effect",
                "required_rows", "linked_declared"):
        assert key in idea


def test_effect_scale_shifts_feasibility():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r1 = next(x for x in evaluate(man, effect_scale=1.0)
              if x.idea_id == "eeg_resp_coupling")
    r_small = next(x for x in evaluate(man, effect_scale=0.7)
                   if x.idea_id == "eeg_resp_coupling")
    assert r1.feasible is True and r1.required_n == 85
    assert r_small.required_n > r1.required_n  # smaller assumed effect -> bigger N
    assert r_small.feasible is False


def test_effect_scale_invalid_raises():
    man = _man([{"modality": "eeg", "n": 30, "variables": []}])
    with pytest.raises(ValueError):
        evaluate(man, effect_scale=0.0)
    with pytest.raises(ValueError):
        evaluate(man, effect_scale=-1.0)


def test_sensitivity_strip_present_and_ordered():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    labels = [s["label"] for s in r.n_sensitivity]
    assert labels == ["보수적", "계획", "낙관적"]
    ns = [s["required_n"] for s in r.n_sensitivity]
    # Smaller effect (conservative) needs more; larger (optimistic) needs fewer.
    assert ns[0] > ns[1] > ns[2]
    # The "계획" row equals the headline required_n.
    assert r.n_sensitivity[1]["required_n"] == r.required_n


def test_sensitivity_strip_values_match_independent_power():
    # Pin the correlation idea's strip to independent n_for_correlation calls
    # (r=0.30 planned; 2/3 and 3/2 scaling -> r=0.20 and r=0.45).
    from paperforge import power
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "eeg_resp_coupling")
    strip = {s["label"]: s for s in r.n_sensitivity}
    assert strip["계획"]["required_n"] == power.n_for_correlation(0.30) == 85
    assert strip["보수적"]["required_n"] == power.n_for_correlation(0.20) == 194
    assert strip["낙관적"]["required_n"] == power.n_for_correlation(0.45) == 37


def test_exploratory_has_no_sensitivity_strip():
    man = _man([{"modality": "watch", "n": 30, "variables": ["rmssd"]}])
    r = next(x for x in evaluate(man) if x.idea_id == "hrv_descriptive")
    assert r.n_sensitivity == []


def test_template_caveats_surface_in_notes():
    # The MoA idea carries balanced-split + logistic caveats.
    man = _man([
        {"modality": "moa", "n": 200, "variables": ["responder_flag"]},
        {"modality": "eeg", "n": 200, "variables": ["alpha_power"]},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "moa_responder_profiling")
    assert any("50:50" in n and "30:70" in n for n in r.notes)  # balanced-split caveat
    assert any("로지스틱" in n for n in r.notes)  # test-vs-analysis caveat


def test_composite_idea_is_incremental_r2():
    # multimodal_arousal_index now sizes an incremental-R^2 test (N=68), and
    # carries the ΔR² caveat.
    man = _man([
        {"modality": "eeg", "n": 200, "variables": []},
        {"modality": "watch", "n": 200, "variables": []},
        {"modality": "respiration", "n": 200, "variables": []},
    ])
    r = next(x for x in evaluate(man) if x.idea_id == "multimodal_arousal_index")
    assert r.required_n == 68
    assert any("증분설명력" in n or "ΔR²" in n for n in r.notes)


def test_sensitivity_in_csv_and_json():
    man = _man([
        {"modality": "eeg", "n": 90, "variables": []},
        {"modality": "respiration", "n": 90, "variables": []},
    ])
    results = evaluate(man)
    csv_text = render_csv(results)
    assert "n_sensitivity" in csv_text.splitlines()[0]
    assert "보수적:" in csv_text
    payload = json.loads(render_json(man, results, 0.05, 0.80))
    idea = next(i for i in payload["ideas"] if i["idea_id"] == "eeg_resp_coupling")
    assert len(idea["n_sensitivity"]) == 3
    assert idea["n_sensitivity"][0]["label"] == "보수적"


def test_variable_column_relabeled_honestly():
    # The report must not imply variables were matched to the hypothesis.
    man = _man([
        {"modality": "watch", "n": 100, "variables": ["RMSSD"]},
        {"modality": "questionnaire", "n": 100, "variables": ["psqi_total"]},
    ])
    results = evaluate(man)
    md = render_markdown(man, results, 0.05, 0.80)
    assert "자동매칭 아님" in md


def test_json_is_valid_and_utf8_readable():
    # ensure_ascii=False must keep Korean legible (not \uXXXX-escaped) in the
    # file, and the payload must round-trip. Use a Hangul study name to prove it.
    man = _man([{"modality": "eeg", "n": 90, "variables": []}], study="수면연구")
    results = evaluate(man)
    text = render_json(man, results, 0.05, 0.80)
    assert "수면연구" in text  # literal Hangul present, not escaped
    assert "\\uc218" not in text  # not ascii-escaped
    assert json.loads(text)["study"] == "수면연구"
