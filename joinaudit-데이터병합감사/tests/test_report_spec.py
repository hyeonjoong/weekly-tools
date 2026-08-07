"""스펙 파일과 산출물 모듈 — 리포트의 숫자는 계측값이어야 한다."""

from __future__ import annotations

import os

import pytest

from conftest import write_rows, write_text
from joinaudit.report import (OUTPUT_NAMES, OutputError, prepare_out_dir,
                              verify_downstream_schema)
from joinaudit.spec import Spec, SpecError, load_spec


# --------------------------------------------------------------------------
# spec.json
# --------------------------------------------------------------------------

def test_full_spec_round_trip(tmp_path):
    path = write_text(str(tmp_path / "spec.json"), """{
      "id_prefixes": ["BELL-001-"],
      "visit_aliases": {"baseline": ["BL", "기저"], "week4": ["W4"]},
      "ranges": {"isi_total": [0, 28]}
    }""")
    spec = load_spec(path)
    assert spec.id_prefixes == ["BELL-001-"]
    assert spec.visit_aliases["baseline"] == ["BL", "기저"]
    assert spec.ranges["isi_total"] == (0.0, 28.0)
    assert spec.empty is False
    assert any("ISI" in line or "isi" in line for line in spec.describe())


def test_empty_spec_adds_no_checks(tmp_path):
    path = write_text(str(tmp_path / "spec.json"), "{}")
    spec = load_spec(path)
    assert spec.empty is True
    assert spec.describe() == []


def test_unknown_top_level_key_is_warned_not_silently_ignored(tmp_path):
    """오타 난 설정이 조용히 무시되는 것은 설정이 없는 것보다 나쁘다."""
    path = write_text(str(tmp_path / "spec.json"), '{"id_prefix": ["X-"]}')
    spec = load_spec(path)
    assert spec.warnings and "id_prefix" in spec.warnings[0]


@pytest.mark.parametrize("body, fragment", [
    ("{ not json", "JSON"),
    ("[1,2,3]", "객체"),
    ('{"ranges": {"isi": [5, 1]}}', "큽니다"),
    ('{"ranges": {"isi": [1]}}', "두 개"),
    ('{"ranges": {"isi": ["a", "b"]}}', "숫자가 아닙니다"),
    ('{"ranges": []}', "객체여야"),
    ('{"visit_aliases": []}', "객체여야"),
    ('{"id_prefixes": [3]}', "문자열"),
])
def test_bad_specs_get_actionable_messages(tmp_path, body, fragment):
    path = write_text(str(tmp_path / "spec.json"), body)
    with pytest.raises(SpecError) as exc:
        load_spec(path)
    assert fragment in str(exc.value)


def test_missing_spec_file(tmp_path):
    with pytest.raises(SpecError):
        load_spec(str(tmp_path / "nope.json"))


def test_spec_accepts_utf8_bom(tmp_path):
    path = write_text(str(tmp_path / "spec.json"), '{"id_prefixes": ["A-"]}',
                      encoding="utf-8-sig")
    assert load_spec(path).id_prefixes == ["A-"]


# --------------------------------------------------------------------------
# 출력 폴더 안전장치
# --------------------------------------------------------------------------

def test_prepare_out_dir_creates_and_returns_realpath(tmp_path):
    out = prepare_out_dir(str(tmp_path / "결과"), [])
    assert os.path.isdir(out) and os.path.isabs(out)


@pytest.mark.parametrize("name", OUTPUT_NAMES)
def test_prepare_out_dir_refuses_to_overwrite_any_input(tmp_path, name):
    out = tmp_path / "o"
    out.mkdir()
    victim = write_rows(str(out / name), [["id"], ["S01"]])
    with pytest.raises(OutputError) as exc:
        prepare_out_dir(str(out), [victim])
    assert "덮어쓰지" in str(exc.value)


def test_prepare_out_dir_follows_symlinks_when_checking(tmp_path):
    """심볼릭 링크로 우회해 입력을 덮어쓰는 것도 막아야 한다."""
    real = tmp_path / "real"
    real.mkdir()
    victim = write_rows(str(real / "merged.csv"), [["id"], ["S01"]])
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    with pytest.raises(OutputError):
        prepare_out_dir(str(link), [victim])


def test_prepare_out_dir_rejects_a_file_as_out_dir(tmp_path):
    path = write_rows(str(tmp_path / "a.csv"), [["id"], ["S01"]])
    with pytest.raises(OutputError):
        prepare_out_dir(path, [])


# --------------------------------------------------------------------------
# 하류 스키마 자체 검증
# --------------------------------------------------------------------------

def test_schema_check_passes_on_a_well_formed_table(tmp_path):
    path = write_rows(str(tmp_path / "merged.csv"),
                      [["subject_id", "timepoint", "v"],
                       ["S01", "2026-03-01", "1"],
                       ["S02", "2026-03-01", ""]])
    assert verify_downstream_schema(path) == []


def test_schema_check_catches_duplicate_column_names(tmp_path):
    path = write_rows(str(tmp_path / "merged.csv"),
                      [["subject_id", "v", "v"], ["S01", "1", "2"]])
    problems = verify_downstream_schema(path)
    assert any("중복" in p for p in problems)


def test_schema_check_catches_ragged_rows(tmp_path):
    write_text(str(tmp_path / "merged.csv"),
               "subject_id,v\nS01,1\nS02,1,2\n")
    problems = verify_downstream_schema(str(tmp_path / "merged.csv"))
    assert any("열 수" in p for p in problems)


def test_schema_check_catches_missing_tokens(tmp_path):
    """`NA` 는 pandas 에서 문자열이 되어 열 전체를 문자형으로 만든다."""
    path = write_rows(str(tmp_path / "merged.csv"),
                      [["subject_id", "v"], ["S01", "NA"]])
    problems = verify_downstream_schema(path)
    assert any("빈 칸이 아니라" in p for p in problems)


def test_schema_check_on_a_missing_file(tmp_path):
    problems = verify_downstream_schema(str(tmp_path / "nope.csv"))
    assert problems and "읽을 수 없습니다" in problems[0]
