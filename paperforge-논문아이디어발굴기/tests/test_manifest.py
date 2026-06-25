"""Manifest parsing & validation tests (fully offline)."""
import json

import pytest

from paperforge import manifest as M


def _ds(**kw):
    base = {"modality": "eeg", "n": 30, "variables": ["alpha_power"]}
    base.update(kw)
    return base


def test_alias_and_korean_modalities():
    data = {"datasets": [_ds(modality="뇌파"), _ds(modality="호흡밴드"),
                          _ds(modality="스마트워치")]}
    man = M.parse_manifest(data)
    assert man.modalities() == {"eeg", "respiration", "watch"}


def test_empty_datasets_rejected():
    with pytest.raises(M.ManifestError):
        M.parse_manifest({"datasets": []})
    with pytest.raises(M.ManifestError):
        M.parse_manifest({})


def test_missing_modality_rejected():
    with pytest.raises(M.ManifestError):
        M.parse_manifest({"datasets": [{"n": 10, "variables": []}]})


def test_bad_n_becomes_unknown_with_warning():
    man = M.parse_manifest({"datasets": [_ds(n=-5), _ds(n="lots")]})
    assert man.datasets[0].n is None
    assert man.datasets[1].n is None
    assert len(man.warnings) >= 2


def test_unknown_modality_warns_but_survives():
    man = M.parse_manifest({"datasets": [_ds(modality="fmri")]})
    assert man.datasets[0].modality == ""
    assert any("unrecognized" in w for w in man.warnings)


def test_variables_must_be_list():
    with pytest.raises(M.ManifestError):
        M.parse_manifest({"datasets": [_ds(variables="alpha")]})


def test_noninteger_float_n_warns_and_drops():
    man = M.parse_manifest({"datasets": [_ds(n=40.7)]})
    assert man.datasets[0].n is None
    assert any("positive integer" in w for w in man.warnings)


def test_integer_valued_float_n_accepted():
    man = M.parse_manifest({"datasets": [_ds(n=40.0)]})
    assert man.datasets[0].n == 40
    assert man.warnings == []


def test_load_from_file(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"study": "X", "datasets": [_ds()]}), encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.study == "X"
    assert man.datasets[0].modality == "eeg"


def test_bad_json_raises_manifest_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(M.ManifestError):
        M.load_manifest(str(p))
