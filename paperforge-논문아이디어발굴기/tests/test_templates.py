"""User-supplied template pack loading, validation and merging."""
import json

import pytest

from paperforge.knowledge import IDEA_TEMPLATES
from paperforge.templates import (
    TemplateError,
    load_template_pack,
    merge_templates,
    parse_template_pack,
    validate_effect,
)


def _tpl(**over):
    base = {
        "id": "custom_one",
        "title": "커스텀 아이디어",
        "required": ["eeg"],
        "optional": ["설문"],
        "hypothesis": "가설",
        "predictors": ["x"],
        "outcomes": ["y"],
        "analysis": "회귀",
        "design": "correlational",
        "effect": {"type": "correlation", "r": 0.3},
        "journal": "저널",
        "novelty": "메모",
    }
    base.update(over)
    return base


def test_valid_pack_parses_and_normalises_modalities():
    [t] = parse_template_pack({"templates": [_tpl()]})
    assert t["id"] == "custom_one"
    assert t["required"] == ["eeg"]
    assert t["optional"] == ["questionnaire"]  # Korean alias canonicalised
    assert t["effect"] == {"type": "correlation", "r": 0.3}
    assert t["caveats"] == []


def test_bare_array_pack_is_accepted():
    assert len(parse_template_pack([_tpl(), _tpl(id="two")])) == 2


@pytest.mark.parametrize("over", [
    {"id": ""},
    {"title": 123},
    {"required": []},
    {"required": "eeg"},          # string, not array
    {"predictors": []},
    {"hypothesis": "   "},
])
def test_malformed_fields_raise(over):
    with pytest.raises(TemplateError):
        parse_template_pack([_tpl(**over)])


def test_unknown_modality_raises_rather_than_silently_never_matching():
    with pytest.raises(TemplateError) as exc:
        parse_template_pack([_tpl(required=["proteomics"])])
    assert "proteomics" in str(exc.value)


def test_modality_in_both_required_and_optional_raises():
    with pytest.raises(TemplateError):
        parse_template_pack([_tpl(required=["eeg"], optional=["뇌파"])])


def test_duplicate_ids_within_a_pack_raise():
    with pytest.raises(TemplateError):
        parse_template_pack([_tpl(), _tpl()])


@pytest.mark.parametrize("effect", [
    {"type": "nope"},
    {"type": "correlation"},                       # missing r
    {"type": "correlation", "r": 1.0},             # r must be < 1
    {"type": "correlation", "r": 0},
    {"type": "correlation", "r": "0.3"},           # string, not number
    {"type": "correlation", "r": True},            # bool is not a number
    {"type": "two_group"},                         # missing d
    {"type": "two_group", "d": 0.5, "allocation": 1.0},
    {"type": "regression", "f2": 0.15, "k": 2.5},  # k must be whole
    {"type": "regression_change", "f2": 0.15, "k_tested": 0, "k_control": 1},
    {"type": "regression_change", "f2": 0.15, "k_tested": 1, "k_control": -1},
    {"type": "regression", "f2": float("inf")},
    "not-an-object",
])
def test_bad_effect_specs_raise(effect):
    with pytest.raises(TemplateError):
        validate_effect(effect, "<t>")


def test_valid_effect_specs_normalise():
    assert validate_effect({"type": "exploratory"}, "<t>") == {"type": "exploratory"}
    assert validate_effect(
        {"type": "regression_change", "f2": 0.15, "k_tested": 2, "k_control": 0},
        "<t>",
    ) == {"type": "regression_change", "f2": 0.15, "k_tested": 2, "k_control": 0}


def test_merge_appends_and_overrides_with_warning():
    custom = parse_template_pack([_tpl(), _tpl(id="eeg_spectral_profile")])
    merged, warns = merge_templates(IDEA_TEMPLATES, [custom])
    assert len(merged) == len(IDEA_TEMPLATES) + 1
    assert any("eeg_spectral_profile" in w for w in warns)
    # Override replaces IN PLACE so ranking order stays stable.
    idx_before = [t["id"] for t in IDEA_TEMPLATES].index("eeg_spectral_profile")
    assert merged[idx_before]["title"] == "커스텀 아이디어"


def test_merge_without_builtin_uses_only_packs():
    custom = parse_template_pack([_tpl()])
    merged, _ = merge_templates(IDEA_TEMPLATES, [custom], include_builtin=False)
    assert [t["id"] for t in merged] == ["custom_one"]


def test_merge_with_nothing_raises():
    with pytest.raises(TemplateError):
        merge_templates(IDEA_TEMPLATES, [], include_builtin=False)


def test_load_pack_from_disk_and_error_paths(tmp_path):
    good = tmp_path / "pack.json"
    good.write_text(json.dumps({"templates": [_tpl()]}), encoding="utf-8")
    assert load_template_pack(str(good))[0]["id"] == "custom_one"

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(TemplateError):
        load_template_pack(str(bad_json))

    no_templates = tmp_path / "empty.json"
    no_templates.write_text(json.dumps({"pack": "x"}), encoding="utf-8")
    with pytest.raises(TemplateError):
        load_template_pack(str(no_templates))

    not_utf8 = tmp_path / "cp949.json"
    not_utf8.write_bytes(json.dumps(
        {"templates": [_tpl(title="한글")]}, ensure_ascii=False
    ).encode("cp949"))
    with pytest.raises(TemplateError):
        load_template_pack(str(not_utf8))


def test_bom_prefixed_pack_loads(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"templates": [_tpl()]}).encode())
    assert load_template_pack(str(p))[0]["id"] == "custom_one"


def test_shipped_example_pack_is_valid():
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pack = load_template_pack(os.path.join(here, "examples", "clinical_pack.json"))
    assert len(pack) >= 3
    merged, warns = merge_templates(IDEA_TEMPLATES, [pack])
    assert warns == []  # example pack must not shadow a built-in id
    assert len(merged) == len(IDEA_TEMPLATES) + len(pack)
