"""척도 사전 — 이름 매칭이 헐거우면 GRIM 이 엉뚱한 숫자에 발동한다."""

from __future__ import annotations

import json

import pytest

from numcheck.scales import (
    BUILTIN_SCALES,
    NEEDS_CONFIG,
    ScaleError,
    ScaleRegistry,
    load_scale_config,
    parse_scale_arg,
)


def test_builtin_scales_are_well_formed():
    for scale in BUILTIN_SCALES:
        assert scale.lo < scale.hi
        assert scale.items > 0
        assert scale.unit > 0


def test_exact_name_matches():
    registry = ScaleRegistry()
    assert registry.find("ISI 평균은 14.37")[0].name == "ISI"
    assert registry.find("the Insomnia Severity Index score")[0].name == "ISI"
    assert registry.find("불면증 심각도 지수")[0].name == "ISI"


def test_name_inside_another_word_is_not_a_match():
    registry = ScaleRegistry()
    for text in ("PISI 평균", "ISIS 평균", "ISI2 평균", "aISI"):
        assert registry.find(text) is None


def test_korean_name_needs_hangul_boundaries():
    registry = ScaleRegistry()
    assert registry.find("초불면증 심각도 지수는") is None


def test_longer_alias_wins():
    registry = ScaleRegistry()
    assert registry.find("HADS-A 평균")[0].name == "HADS-A"
    assert registry.find("HADS 평균")[0].name == "HADS"


def test_first_occurrence_wins():
    registry = ScaleRegistry()
    scale, start, _end = registry.find("ISI 와 PSQI 를 함께 보고한다")
    assert scale.name == "ISI"
    assert start == 0


def test_parse_scale_arg():
    scale = parse_scale_arg("ISI=0:28:7")
    assert (scale.lo, scale.hi, scale.items, scale.unit) == (0, 28, 7, 1.0)


def test_parse_scale_arg_percent_of_count():
    scale = parse_scale_arg("단어인지도=0:100:50", percent_of_count=True)
    assert scale.unit == pytest.approx(2.0)
    assert scale.integer_sum is False


@pytest.mark.parametrize("bad", [
    "ISI", "ISI=0:28", "ISI=28:0:7", "ISI=0:28:0", "=0:28:7", "ISI=a:b:c",
])
def test_parse_scale_arg_rejects_bad_input(bad):
    with pytest.raises(ScaleError):
        parse_scale_arg(bad)


def test_user_scale_overrides_builtin():
    registry = ScaleRegistry()
    registry.add(parse_scale_arg("ISI=0:100:25"))
    found = registry.find("ISI 평균")[0]
    assert found.hi == 100
    assert sum(1 for s in registry.scales if s.name.upper() == "ISI") == 1


def test_load_scale_config(tmp_path):
    path = tmp_path / "scales.json"
    path.write_text(json.dumps({
        "ISI": {"min": 0, "max": 28, "items": 7},
        "단어인지도": {"min": 0, "max": 100, "items": 50, "percent_of_count": True,
                   "aliases": ["word recognition score"]},
    }, ensure_ascii=False), encoding="utf-8")
    scales = load_scale_config(path)
    by_name = {s.name: s for s in scales}
    assert by_name["ISI"].unit == 1.0
    assert by_name["단어인지도"].unit == pytest.approx(2.0)
    assert "word recognition score" in by_name["단어인지도"].aliases


def test_load_scale_config_errors(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ScaleError, match="없습니다"):
        load_scale_config(missing)

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScaleError, match="JSON"):
        load_scale_config(bad_json)

    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ScaleError, match="객체"):
        load_scale_config(not_object)

    missing_keys = tmp_path / "keys.json"
    missing_keys.write_text('{"X": {"min": 0}}', encoding="utf-8")
    with pytest.raises(ScaleError, match="min/max/items"):
        load_scale_config(missing_keys)

    bad_range = tmp_path / "range.json"
    bad_range.write_text('{"X": {"min": 10, "max": 0, "items": 3}}', encoding="utf-8")
    with pytest.raises(ScaleError):
        load_scale_config(bad_range)


def test_needs_config_hint_only_for_unconfigured():
    registry = ScaleRegistry()
    hit = registry.find_unconfigured("단어인지도 평균은 62.4%")
    assert hit and hit[0] == "단어인지도"
    registry.add(parse_scale_arg("단어인지도=0:100:50", percent_of_count=True))
    assert registry.find_unconfigured("단어인지도 평균은 62.4%") is None


def test_needs_config_table_is_sane():
    assert "단어인지도" in NEEDS_CONFIG
    assert all(isinstance(v, str) and v for v in NEEDS_CONFIG.values())
