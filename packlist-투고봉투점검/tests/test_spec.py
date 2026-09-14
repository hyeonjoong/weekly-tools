"""--limits / --expect 는 **입력**이다. 저널 규정을 코드에 박지 않는다."""

import json

import pytest

from packlist.safeio import UsageError
from packlist.spec import load_expectations, load_limits


def write(tmp_path, name, payload, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode(encoding))
    return str(path)


def test_expect_english_keys(tmp_path):
    expect = load_expectations(write(tmp_path, "e.json", {"required": ["S1 Table"]}))
    assert expect.required == ["S1 Table"]


def test_expect_korean_keys(tmp_path):
    expect = load_expectations(write(tmp_path, "e.json", {"필수": ["S1 표"]}))
    assert expect.required == ["S1 표"]


def test_expect_optional_only_is_allowed(tmp_path):
    expect = load_expectations(write(tmp_path, "e.json", {"optional": ["Cover Letter"]}))
    assert expect.optional == ["Cover Letter"]


def test_expect_empty_is_refused(tmp_path):
    with pytest.raises(UsageError):
        load_expectations(write(tmp_path, "e.json", {}))


def test_expect_wrong_type_is_refused(tmp_path):
    with pytest.raises(UsageError):
        load_expectations(write(tmp_path, "e.json", {"required": [1, 2]}))


def test_expect_non_object_is_refused(tmp_path):
    path = tmp_path / "e.json"
    path.write_text("[1,2,3]")
    with pytest.raises(UsageError):
        load_expectations(str(path))


def test_expect_invalid_json_is_refused(tmp_path):
    path = tmp_path / "e.json"
    path.write_text("{not json")
    with pytest.raises(UsageError) as exc:
        load_expectations(str(path))
    assert "JSON" in str(exc.value)


def test_expect_cp949_file(tmp_path):
    assert load_expectations(write(tmp_path, "e.json", {"필수": ["보충자료"]}, "cp949"))


def test_limits_values(tmp_path):
    limits = load_limits(write(tmp_path, "l.json",
                               {"max_total_mb": 30, "max_file_mb": 10, "max_files": 12}))
    assert (limits.max_total_mb, limits.max_file_mb, limits.max_files) == (30, 10, 12)


def test_limits_korean_keys(tmp_path):
    limits = load_limits(write(tmp_path, "l.json", {"봉투_최대MB": 5}))
    assert limits.max_total_mb == 5


@pytest.mark.parametrize("payload", [{"max_total_mb": 0}, {"max_total_mb": -1}])
def test_limits_non_positive_refused(tmp_path, payload):
    with pytest.raises(UsageError):
        load_limits(write(tmp_path, "l.json", payload))


def test_limits_boolean_refused(tmp_path):
    with pytest.raises(UsageError):
        load_limits(write(tmp_path, "l.json", {"max_total_mb": True}))


def test_limits_string_refused(tmp_path):
    with pytest.raises(UsageError):
        load_limits(write(tmp_path, "l.json", {"max_total_mb": "30"}))


def test_limits_empty_refused(tmp_path):
    with pytest.raises(UsageError):
        load_limits(write(tmp_path, "l.json", {"journal": "anything"}))
