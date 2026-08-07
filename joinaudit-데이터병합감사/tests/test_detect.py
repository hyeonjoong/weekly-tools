"""열 자동 탐지 — **확신이 없으면 고르지 않는다**를 지키는 테스트.

이 툴이 가장 크게 실패하는 방식은 크래시가 아니라 틀린 표를 자신 있게 내놓는
것이고, 그 실패는 전부 여기서 시작된다. 후보가 둘이면 하나를 고르는 대신
멈춰야 한다.
"""

from __future__ import annotations

import pytest

from conftest import write_rows
from joinaudit.dataio import load_table
from joinaudit.detect import (BY_CONTENT, BY_NAME, EXPLICIT, detect_date,
                              detect_key, detect_visit, norm_name)
from joinaudit.timeline import VisitNormalizer


def frame(tmp_path, rows, name="a.csv"):
    return load_table(write_rows(str(tmp_path / name), rows))


# --------------------------------------------------------------------------
# 이름 정규화
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Subject ID", "subjectid"), ("subject_id", "subjectid"),
    ("subject-id", "subjectid"), ("피험자 번호", "피험자번호"),
    ("ＩＤ", "id"), ("v (ms)", "vms"),
])
def test_norm_name(raw, expected):
    assert norm_name(raw) == expected


# --------------------------------------------------------------------------
# 피험자 키
# --------------------------------------------------------------------------

def test_key_is_found_by_name(tmp_path):
    f = frame(tmp_path, [["subject_id", "v"], ["S01", "1"], ["S02", "2"]])
    det = detect_key(f)
    assert det.ok and det.column == "subject_id" and det.confidence == BY_NAME


def test_two_first_class_key_names_are_ambiguous(tmp_path):
    """`subject_id` 와 `연구번호` 는 둘 다 1급 이름이다 — 골라선 안 된다."""
    f = frame(tmp_path, [["subject_id", "연구번호", "v"], ["S01", "R01", "1"]])
    det = detect_key(f)
    assert not det.ok and det.ambiguous
    assert "--key" in det.reason


def test_a_stronger_name_beats_a_weaker_one(tmp_path):
    """등급이 다르면 모호가 아니다 — `subject_id` 가 `no` 를 이긴다."""
    f = frame(tmp_path, [["subject_id", "no", "v"],
                         ["S01", "1", "1"], ["S02", "2", "2"]])
    det = detect_key(f)
    assert det.ok and det.column == "subject_id"


def test_explicit_key_wins_and_is_marked_as_such(tmp_path):
    f = frame(tmp_path, [["subject_id", "연구번호", "v"], ["S01", "R01", "1"]])
    det = detect_key(f, explicit="연구번호")
    assert det.ok and det.column == "연구번호" and det.confidence == EXPLICIT


def test_explicit_key_that_is_absent_reports_the_available_columns(tmp_path):
    f = frame(tmp_path, [["subject_id", "v"], ["S01", "1"]])
    det = detect_key(f, explicit="없는열")
    assert not det.ok and "subject_id" in det.reason


def test_a_date_like_column_is_not_taken_as_the_key(tmp_path):
    f = frame(tmp_path, [["id", "v"],
                         ["2026-03-01", "1"], ["2026-03-02", "2"]])
    assert not detect_key(f).ok


def test_a_measurement_column_is_not_taken_as_the_key(tmp_path):
    f = frame(tmp_path, [["id", "v"], ["1.5", "1"], ["2.5", "2"]])
    assert not detect_key(f).ok


def test_a_mostly_empty_column_is_not_taken_as_the_key(tmp_path):
    rows = [["id", "v"]] + [["", str(i)] for i in range(9)] + [["S01", "9"]]
    assert not detect_key(frame(tmp_path, rows)).ok


def test_no_key_column_at_all(tmp_path):
    f = frame(tmp_path, [["온도", "습도"], ["21", "40"]])
    det = detect_key(f)
    assert not det.ok and not det.ambiguous
    assert "--key" in det.reason


# --------------------------------------------------------------------------
# 날짜
# --------------------------------------------------------------------------

def test_date_is_found_by_name_and_content(tmp_path):
    f = frame(tmp_path, [["subject_id", "measured_at", "v"],
                         ["S01", "2026-03-01", "1"]])
    det = detect_date(f)
    assert det.ok and det.column == "measured_at" and det.plan is not None


def test_two_equally_strong_date_names_are_ambiguous(tmp_path):
    f = frame(tmp_path, [["subject_id", "date", "날짜", "v"],
                         ["S01", "2026-03-01", "2026-03-02", "1"]])
    det = detect_date(f)
    assert not det.ok and det.ambiguous
    assert "--date" in det.reason


def test_a_date_shaped_column_without_a_date_name_is_accepted_alone(tmp_path):
    f = frame(tmp_path, [["subject_id", "언제", "v"],
                         ["S01", "2026-03-01", "1"]])
    det = detect_date(f)
    assert det.ok and det.confidence == BY_CONTENT


def test_two_date_shaped_columns_without_names_are_ambiguous(tmp_path):
    f = frame(tmp_path, [["subject_id", "가", "나", "v"],
                         ["S01", "2026-03-01", "2026-03-02", "1"]])
    det = detect_date(f)
    assert not det.ok and det.ambiguous


def test_excel_serials_are_only_accepted_with_a_date_name(tmp_path):
    """`steps` 열의 20000~65000 이 날짜로 둔갑하면 표가 통째로 틀린다."""
    rows = [["subject_id", "steps", "v"]] + [
        [f"S{i:02d}", str(30000 + i * 7), str(i)] for i in range(1, 9)]
    assert not detect_date(frame(tmp_path, rows)).ok

    named = [["subject_id", "측정일", "v"]] + [
        [f"S{i:02d}", str(46000 + i), str(i)] for i in range(1, 9)]
    det = detect_date(frame(tmp_path, named, "b.csv"))
    assert det.ok and det.plan is not None and det.plan.excel_serial


def test_a_column_that_mostly_fails_to_parse_is_not_a_date(tmp_path):
    rows = [["subject_id", "date", "v"]] + \
        [[f"S{i:02d}", "몰라요", str(i)] for i in range(1, 9)]
    rows.append(["S09", "2026-03-01", "9"])
    assert not detect_date(frame(tmp_path, rows)).ok


def test_explicit_date_column_that_is_absent(tmp_path):
    f = frame(tmp_path, [["subject_id", "v"], ["S01", "1"]])
    det = detect_date(f, explicit="없는열")
    assert not det.ok and "없는열" in det.reason


# --------------------------------------------------------------------------
# 방문 / 시점
# --------------------------------------------------------------------------

def test_visit_is_found_by_name(tmp_path):
    f = frame(tmp_path, [["subject_id", "visit", "v"],
                         ["S01", "baseline", "1"], ["S01", "week4", "2"]])
    det = detect_visit(f, normalizer=VisitNormalizer())
    assert det.ok and det.column == "visit"


def test_a_weakly_named_visit_column_with_strange_labels_is_rejected(tmp_path):
    """이름 근거가 약하면(rank>0) 값도 알아볼 수 있어야 시점 열로 본다."""
    rows = [["subject_id", "측정시점구분", "v"]] + \
        [[f"S{i:02d}", f"메모{i}", str(i)] for i in range(1, 9)]
    det = detect_visit(frame(tmp_path, rows), normalizer=VisitNormalizer())
    assert not det.ok


def test_a_strongly_named_visit_column_survives_unknown_labels(tmp_path):
    """이름이 명확하면 낯선 라벨이어도 시점 열로 본다(대신 나중에 보고된다)."""
    rows = [["subject_id", "visit", "v"]] + \
        [[f"S{i:02d}", f"낯선라벨{i}", str(i)] for i in range(1, 9)]
    det = detect_visit(frame(tmp_path, rows), normalizer=VisitNormalizer())
    assert det.ok and det.column == "visit"


def test_two_visit_candidates_are_ambiguous(tmp_path):
    f = frame(tmp_path, [["subject_id", "visit", "시점", "v"],
                         ["S01", "baseline", "week4", "1"]])
    det = detect_visit(f, normalizer=VisitNormalizer())
    assert not det.ok and det.ambiguous
