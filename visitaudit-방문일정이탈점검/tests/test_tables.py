"""입력 표 읽기 — 인코딩·별칭·wide·오류 경로."""

import pytest

from tests.conftest import write_csv
from visitaudit.tables import (InputError, load_subjects, load_visits_long,
                               load_visits_wide)


def test_korean_headers(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문명,방문일,상태\nS01,Baseline,2026-03-02,완료\n")
    records, enc, _, _ = load_visits_long(p)
    assert len(records) == 1
    r = records[0]
    assert (r.subject, r.visit, r.status_kind) == ("S01", "Baseline", "record")
    assert r.date is not None


def test_joinaudit_style_headers(tmp_path):
    # joinaudit merged.csv 스키마 호환: subject_id / timepoint (+ 날짜 열)
    p = write_csv(tmp_path, "merged.csv",
                  "subject_id,timepoint,visit_date\nS01,Baseline,2026-03-02\n")
    records, _, _, _ = load_visits_long(p)
    assert records[0].subject == "S01" and records[0].visit == "Baseline"


def test_cp949_encoding(tmp_path):
    text = "피험자ID,방문명,방문일\nS01,기저,2026-03-02\n"
    path = tmp_path / "cp949.csv"
    path.write_bytes(text.encode("cp949"))
    records, enc, _, _ = load_visits_long(str(path))
    assert enc == "cp949"
    assert records[0].visit == "기저"


def test_utf8_bom(tmp_path):
    text = "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n"
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    records, enc, _, _ = load_visits_long(str(path))
    assert records[0].subject == "S01"


def test_column_override(tmp_path):
    p = write_csv(tmp_path, "v.csv", "환자,회차,일자\nS01,V1,2026-03-02\n")
    records, _, _, _ = load_visits_long(p, id_col="환자", visit_col="회차", date_col="일자")
    assert records[0].subject == "S01"


def test_override_missing_column_fails(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문명,방문일\nS01,V1,2026-03-02\n")
    with pytest.raises(InputError, match="없습니다"):
        load_visits_long(p, date_col="없는열")


def test_ambiguous_id_columns_fail(tmp_path):
    # 별칭 두 개가 동시에 존재 → 고르지 않고 멈춘다
    p = write_csv(tmp_path, "v.csv", "피험자ID,subject_id,방문명,방문일\nS01,S01,V1,2026-03-02\n")
    with pytest.raises(InputError, match="여러 개"):
        load_visits_long(p)


def test_missing_date_column_fails(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문명\nS01,V1\n")
    with pytest.raises(InputError, match="방문일"):
        load_visits_long(p)


def test_duplicate_header_fails(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문일,방문일,방문명\nS01,a,b,V1\n")
    with pytest.raises(InputError, match="중복"):
        load_visits_long(p)


def test_empty_file_fails(tmp_path):
    p = write_csv(tmp_path, "v.csv", "")
    with pytest.raises(InputError, match="빈 파일"):
        load_visits_long(p)


def test_missing_file_fails():
    with pytest.raises(InputError, match="파일이 없습니다"):
        load_visits_long("/없는/파일.csv")


def test_blank_id_fails(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문명,방문일\n,V1,2026-03-02\n")
    with pytest.raises(InputError, match="피험자ID"):
        load_visits_long(p)


def test_fully_blank_rows_skipped(tmp_path):
    p = write_csv(tmp_path, "v.csv", "피험자ID,방문명,방문일\nS01,V1,2026-03-02\n,,\n\n")
    records, _, _, _ = load_visits_long(p)
    assert len(records) == 1


def test_status_kinds(tmp_path):
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일,상태\n"
                  "S01,V1,2026-03-02,완료\n"
                  "S01,V2,2026-04-02,예정\n"
                  "S01,V3,2026-04-09,취소\n"
                  "S01,V4,2026-04-16,\n")
    records, _, _, _ = load_visits_long(p)
    assert [r.status_kind for r in records] == ["record", "planned", "notdone", "record"]


def test_wide_format(tmp_path):
    p = write_csv(tmp_path, "w.csv",
                  "피험자ID,Baseline,V1,메모\nS01,2026-03-02,2026-03-30,잘함\nS02,2026-03-09,,\n")
    records, _, ignored = load_visits_wide(p, ["Baseline", "V1"])
    assert len(records) == 3          # S02 의 빈 V1 은 기록 없음
    assert ignored == ["메모"]
    assert {(r.subject, r.visit) for r in records} == {("S01", "Baseline"), ("S01", "V1"), ("S02", "Baseline")}


def test_wide_no_matching_columns_fails(tmp_path):
    p = write_csv(tmp_path, "w.csv", "피험자ID,W1,W2\nS01,2026-03-02,2026-03-30\n")
    with pytest.raises(InputError, match="일치하는 열이 하나도"):
        load_visits_wide(p, ["Baseline", "V1"])


def test_subjects_load(tmp_path):
    p = write_csv(tmp_path, "s.csv",
                  "피험자ID,군,등록일,탈락일,탈락사유,제외사유,age\n"
                  "S01,중재군,2026-03-02,,,,34\n"
                  "S02,,,,,기준미달,70\n")
    subjects, warnings, _ = load_subjects(p)
    assert subjects[0].randomized is True
    assert subjects[0].extras == {"age": "34"}
    assert subjects[1].randomized is False
    assert subjects[1].screenfail_reason == "기준미달"
    assert warnings == []


def test_subjects_duplicate_id_warned(tmp_path):
    p = write_csv(tmp_path, "s.csv", "피험자ID,군\nS01,A\nS01,B\n")
    subjects, warnings, _ = load_subjects(p)
    assert all(s.duplicated for s in subjects)
    assert any("중복" in w for w in warnings)


def test_subjects_bad_dropout_date_kept_as_error(tmp_path):
    p = write_csv(tmp_path, "s.csv", "피험자ID,군,탈락일\nS01,A,언젠가\n")
    subjects, _, _ = load_subjects(p)
    assert subjects[0].dropout is None
    assert subjects[0].dropout_error is not None
