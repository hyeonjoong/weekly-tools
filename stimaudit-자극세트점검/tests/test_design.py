"""설계 JSON 검증 — 조용히 무시하는 것이 가장 위험합니다."""
from __future__ import annotations

import json
import os

import pytest

from stimaudit import design


def _write(tmp_path, payload, name="d.json"):
    p = os.path.join(str(tmp_path), name)
    if isinstance(payload, str):
        open(p, "w", encoding="utf-8").write(payload)
    else:
        open(p, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
    return p


def test_minimal_design(tmp_path):
    d = design.load(_write(tmp_path, {"conditions": {"a": ["x.wav"], "b": ["y.wav"]}}))
    assert d.n_conditions == 2
    assert d.condition_of("x.wav") == "a"
    assert d.condition_of("zzz.wav") is None


def test_full_design(tmp_path):
    d = design.load(_write(tmp_path, {
        "study": "S", "conditions": {"a": ["x.wav"]}, "contrast": "mod",
        "claims": {"x.wav": {"carrier_hz": 440.0}}, "pairs": {"x.wav": "old.wav"}}))
    assert d.study == "S"
    assert d.contrast == "mod"
    assert d.claims["x.wav"]["carrier_hz"] == 440.0
    assert d.pairs["x.wav"] == "old.wav"


def test_paths_reduced_to_basename(tmp_path):
    """절대경로를 적으면 그 사람 컴퓨터 밖에서 못 씁니다 — basename 으로 대조합니다."""
    d = design.load(_write(tmp_path, {
        "conditions": {"a": ["/Users/x/sounds/x.wav"]},
        "claims": {"/Users/x/sounds/x.wav": {"duration_s": 3}}}))
    assert d.conditions["a"] == ["x.wav"]
    assert "x.wav" in d.claims


def test_missing_file(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(os.path.join(str(tmp_path), "nope.json"))
    assert "찾을 수 없" in str(e.value)


def test_malformed_json(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, "{not json"))
    assert "해석할 수 없" in str(e.value)


def test_non_utf8(tmp_path):
    p = os.path.join(str(tmp_path), "d.json")
    open(p, "wb").write('{"study": "한글"}'.encode("euc-kr"))
    with pytest.raises(design.DesignError) as e:
        design.load(p)
    assert "UTF-8" in str(e.value)


def test_top_level_must_be_object(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, [1, 2, 3]))
    assert "최상위" in str(e.value)


def test_unknown_top_level_key_rejected(tmp_path):
    """오타를 조용히 무시하면 사용자는 검사됐다고 믿습니다."""
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"conditon": {"a": ["x.wav"]}}))
    assert "모르는 항목" in str(e.value)


def test_conditions_must_be_object(tmp_path):
    with pytest.raises(design.DesignError):
        design.load(_write(tmp_path, {"conditions": ["a", "b"]}))


def test_condition_values_must_be_string_list(tmp_path):
    with pytest.raises(design.DesignError):
        design.load(_write(tmp_path, {"conditions": {"a": [1, 2]}}))


def test_empty_condition_rejected(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"conditions": {"a": []}}))
    assert "비어" in str(e.value)


def test_file_in_two_conditions_rejected(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"conditions": {"a": ["x.wav"], "b": ["x.wav"]}}))
    assert "양쪽에" in str(e.value)


def test_unsupported_claim_key_rejected(tmp_path):
    """심리음향량을 claims 에 적으면 거절하고 DEBUSSY 를 가리킵니다."""
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"claims": {"x.wav": {"roughness_asper": 0.3}}}))
    assert "지원하지 않는 주장" in str(e.value)
    assert "DEBUSSY" in str(e.value)


def test_claim_value_must_be_number(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"claims": {"x.wav": {"carrier_hz": "사백사십"}}}))
    assert "숫자가 아닙니다" in str(e.value)


def test_claim_value_must_be_positive(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, {"claims": {"x.wav": {"carrier_hz": -1}}}))
    assert "0보다 커야" in str(e.value)


def test_claim_value_rejects_infinity(tmp_path):
    with pytest.raises(design.DesignError) as e:
        design.load(_write(tmp_path, '{"claims": {"x.wav": {"carrier_hz": Infinity}}}'))
    assert "유한" in str(e.value)


def test_claim_value_rejects_nan(tmp_path):
    with pytest.raises(design.DesignError):
        design.load(_write(tmp_path, '{"claims": {"x.wav": {"carrier_hz": NaN}}}'))


def test_claims_must_be_object(tmp_path):
    with pytest.raises(design.DesignError):
        design.load(_write(tmp_path, {"claims": {"x.wav": [1]}}))


def test_pairs_value_must_be_string(tmp_path):
    with pytest.raises(design.DesignError):
        design.load(_write(tmp_path, {"pairs": {"x.wav": 3}}))


def test_check_against_inputs_passes(tmp_path):
    d = design.load(_write(tmp_path, {"conditions": {"a": ["x.wav"]}}))
    design.check_against_inputs(d, ["x.wav", "y.wav"])


def test_check_against_inputs_rejects_unknown_file(tmp_path):
    """조용히 무시하면 '조건 3개'라고 인쇄하고 실제로는 2개만 비교합니다."""
    d = design.load(_write(tmp_path, {"conditions": {"a": ["ghost.wav"]}}))
    with pytest.raises(design.DesignError) as e:
        design.check_against_inputs(d, ["x.wav"])
    assert "ghost.wav" in str(e.value)


def test_check_against_inputs_rejects_unknown_claim_file(tmp_path):
    d = design.load(_write(tmp_path, {"claims": {"ghost.wav": {"duration_s": 3}}}))
    with pytest.raises(design.DesignError) as e:
        design.check_against_inputs(d, ["x.wav"])
    assert "ghost.wav" in str(e.value)


def test_emit_skeleton_is_loadable(tmp_path):
    """뼈대가 그대로 다시 읽혀야 합니다 — 빈 조건을 넣으면 로드에 실패합니다."""
    text = design.emit_skeleton(["a.wav", "b.wav"], study="T")
    p = os.path.join(str(tmp_path), "skel.json")
    open(p, "w", encoding="utf-8").write(text)
    d = design.load(p)
    assert d.study == "T"
    assert sorted(sum(d.conditions.values(), [])) == ["a.wav", "b.wav"]
    design.check_against_inputs(d, ["a.wav", "b.wav"])


def test_emit_skeleton_does_not_invent_conditions():
    """조건 이름을 자동으로 지어내면 사람이 확인하지 않고 씁니다."""
    text = design.emit_skeleton(["a.wav"])
    assert "바꾸세요" in text
    assert "active" not in text and "control" not in text


def test_emit_skeleton_lists_supported_claims():
    text = design.emit_skeleton(["a.wav"])
    for key in design.SUPPORTED_CLAIMS:
        assert key in text


def test_korean_filenames_survive(tmp_path):
    d = design.load(_write(tmp_path, {"conditions": {"조건가": ["싱잉볼_bi.wav"]}}))
    assert d.condition_of("싱잉볼_bi.wav") == "조건가"
