"""열 성격 판별."""

from __future__ import annotations

from deidaudit.columns import profile_column, profile_table
from deidaudit.tabular import Table


def _table(columns, rows):
    return Table(file="t.csv", sheet="", columns=columns, rows=rows)


def test_free_text_by_header_keyword():
    table = _table(["비고"], [["짧음"], ["짧음2"], [""]])
    profile = profile_column(table, 0)
    assert profile.is_free_text
    assert "헤더" in profile.free_text_reason


def test_free_text_by_shape():
    rows = [[f"오늘은 컨디션이 괜찮았고 잠도 잘 잤습니다 {i}"] for i in range(10)]
    profile = profile_column(_table(["응답내용상세"], rows), 0)
    assert profile.is_free_text


def test_short_categorical_is_not_free_text():
    rows = [["M"], ["F"], ["M"], ["F"], ["M"]]
    profile = profile_column(_table(["gender_code"], rows), 0)
    assert not profile.is_free_text
    assert "임계 미만" in profile.free_text_reason


def test_numeric_column_is_not_free_text():
    rows = [[str(400 + i)] for i in range(20)]
    profile = profile_column(_table(["TST_min"], rows), 0)
    assert not profile.is_free_text and profile.kind == "숫자"


def test_birth_column_by_header():
    rows = [["1988-04-02"], ["1991-11-27"], ["1979-06-15"]]
    profile = profile_column(_table(["birth"], rows), 0)
    assert profile.is_birth_column and profile.is_date_column


def test_birth_column_detected_without_header_hint():
    rows = [[d] for d in ["1988-04-02", "1991-11-27", "1979-06-15", "1965-01-09", "2001-05-30"]]
    profile = profile_column(_table(["v3"], rows), 0)
    assert profile.is_birth_column


def test_recent_date_column_is_not_birth():
    rows = [[d] for d in ["2026-03-14", "2026-03-21", "2026-03-28"]]
    profile = profile_column(_table(["visit_date"], rows), 0)
    assert profile.is_date_column and not profile.is_birth_column


def test_age_header_excludes_lookalikes():
    assert profile_column(_table(["age"], [["45"]]), 0).is_age_column
    assert profile_column(_table(["나이"], [["45"]]), 0).is_age_column
    assert not profile_column(_table(["page_views"], [["45"]]), 0).is_age_column
    assert not profile_column(_table(["average_hr"], [["72"]]), 0).is_age_column


def test_name_header_excludes_lookalikes():
    assert profile_column(_table(["name"], [["김현중"]]), 0).is_name_column
    assert profile_column(_table(["성명"], [["김현중"]]), 0).is_name_column
    assert not profile_column(_table(["file_name"], [["a.csv"]]), 0).is_name_column
    assert not profile_column(_table(["변수이름"], [["TST"]]), 0).is_name_column


def test_profile_table_returns_one_profile_per_column():
    table = _table(["a", "b", "c"], [["1", "2", "3"]])
    assert [p.index for p in profile_table(table)] == [0, 1, 2]


def test_empty_column_is_reported_as_empty():
    profile = profile_column(_table(["빈열"], [[""], [""]]), 0)
    assert profile.kind == "빈 열" and not profile.is_free_text
