"""위생 점검 — **울어야 할 때만 울고, 울면 안 될 때는 조용하다**.

두 방향 모두 테스트한다. 조용히 통과시키는 체커도 나쁘지만, 매번 우는 체커는
두 번 다시 안 열리기 때문이다. 특히 경계값(`ranges` 의 최소·최대 그 자체)에서
한쪽으로 치우치면 정상값이 경고로 둔갑한다.
"""

from __future__ import annotations

import pytest

from conftest import write_rows
from joinaudit.dataio import load_table
from joinaudit.detect import Detection
from joinaudit.hygiene import (check_columns, check_key_overlap,
                               check_prefix_conflict, check_ranges,
                               check_timezones, check_units, check_yield)
from joinaudit.issues import CRITICAL, INFO, WARNING, IssueLog
from joinaudit.merge import FilePlan, make_prefix
from joinaudit.timeline import plan_date_column


def make_plan(tmp_path, rows, name="a.csv", key="id", date_col=None, index=0):
    frame = load_table(write_rows(str(tmp_path / name), rows))
    plan = FilePlan(index=index, frame=frame, prefix=make_prefix(name),
                    key_det=Detection(role="key", column=key, confidence="명시"))
    plan.value_columns = [c for c in frame.header if c != key]
    if date_col:
        plan.time_kind, plan.time_col = "date", date_col
        plan.date_plan = plan_date_column(frame.column(date_col))
    plan.keys = [v.strip().upper() for v in frame.column(key)]
    return plan


def kinds(log):
    return {i.kind for i in log}


# --------------------------------------------------------------------------
# 완전 결측 열 / 상수 열
# --------------------------------------------------------------------------

def test_all_missing_column_is_reported_as_info(tmp_path):
    plan = make_plan(tmp_path, [["id", "v", "빈열"],
                                ["S01", "1", ""], ["S02", "2", ""],
                                ["S03", "3", ""]])
    log = IssueLog()
    check_columns([plan], log)
    hits = [i for i in log if i.kind == "완전결측열"]
    assert len(hits) == 1 and hits[0].severity == INFO
    assert hits[0].key == "빈열"


def test_constant_column_is_reported_as_info(tmp_path):
    plan = make_plan(tmp_path, [["id", "v", "센터"],
                                ["S01", "1", "서울"], ["S02", "2", "서울"],
                                ["S03", "3", "서울"]])
    log = IssueLog()
    check_columns([plan], log)
    hits = [i for i in log if i.kind == "상수열"]
    assert len(hits) == 1 and hits[0].severity == INFO


def test_a_varying_column_is_not_reported(tmp_path):
    """정상 열에서 울면 안 된다 — 이 방향이 더 중요하다."""
    plan = make_plan(tmp_path, [["id", "v"],
                                ["S01", "1"], ["S02", "2"], ["S03", "3"]])
    log = IssueLog()
    check_columns([plan], log)
    assert list(log) == []


def test_two_row_file_is_not_called_constant(tmp_path):
    """행이 2개뿐이면 값이 같아도 '상수 열'이라 부르지 않는다."""
    plan = make_plan(tmp_path, [["id", "v"], ["S01", "1"], ["S02", "1"]])
    log = IssueLog()
    check_columns([plan], log)
    assert list(log) == []


def test_constant_column_message_is_truncated(tmp_path):
    """자유기재 칸에는 이름·연락처가 들어 있을 수 있다."""
    long = "담당의 박영희 010-3333-4444 세브란스병원 수면센터"
    plan = make_plan(tmp_path, [["id", "메모"], ["S01", long],
                                ["S02", long], ["S03", long]])
    log = IssueLog()
    check_columns([plan], log)
    message = [i.message for i in log if i.kind == "상수열"][0]
    assert "010-3333-4444" not in message
    assert "…" in message


# --------------------------------------------------------------------------
# 범위 이탈 — 경계값이 핵심
# --------------------------------------------------------------------------

def test_values_exactly_on_the_bounds_are_not_flagged(tmp_path):
    """[0, 28] 에서 0과 28은 **정상**이다. `<`/`<=` 를 뒤집으면 여기서 걸린다."""
    plan = make_plan(tmp_path, [["id", "isi_total"],
                                ["S01", "0"], ["S02", "28"], ["S03", "14"]])
    log = IssueLog()
    check_ranges([plan], {"isi_total": (0, 28)}, log)
    assert list(log) == []


@pytest.mark.parametrize("value", ["-0.1", "28.1", "45", "-3"])
def test_values_outside_the_bounds_are_flagged(tmp_path, value):
    plan = make_plan(tmp_path, [["id", "isi_total"],
                                ["S01", value], ["S02", "14"]])
    log = IssueLog()
    check_ranges([plan], {"isi_total": (0, 28)}, log)
    hits = [i for i in log if i.kind == "범위이탈"]
    assert len(hits) == 1 and hits[0].severity == WARNING


def test_no_ranges_means_no_check_at_all(tmp_path):
    """범위를 안 적으면 임의의 정상범위를 지어내지 않는다."""
    plan = make_plan(tmp_path, [["id", "isi_total"], ["S01", "999"]])
    log = IssueLog()
    check_ranges([plan], {}, log)
    assert list(log) == []


def test_range_check_ignores_missing_and_non_numeric(tmp_path):
    plan = make_plan(tmp_path, [["id", "isi_total"],
                                ["S01", ""], ["S02", "NA"], ["S03", "미실시"]])
    log = IssueLog()
    check_ranges([plan], {"isi_total": (0, 28)}, log)
    assert list(log) == []


def test_range_check_matches_column_names_loosely(tmp_path):
    plan = make_plan(tmp_path, [["id", "ISI Total"], ["S01", "45"]])
    log = IssueLog()
    check_ranges([plan], {"isi_total": (0, 28)}, log)
    assert kinds(log) == {"범위이탈"}


def test_range_message_does_not_dump_long_values(tmp_path):
    plan = make_plan(tmp_path, [["id", "isi_total"], ["S01", "9" * 200]])
    log = IssueLog()
    check_ranges([plan], {"isi_total": (0, 28)}, log)
    assert all(len(i.message) < 300 for i in log)


# --------------------------------------------------------------------------
# 단위 의심 — 정보로만, 그리고 진짜 배수일 때만
# --------------------------------------------------------------------------

def test_unit_suspicion_fires_on_a_60x_difference(tmp_path):
    a = make_plan(tmp_path, [["id", "총수면시간_min"]] +
                  [[f"S{i:02d}", str(400 + i)] for i in range(1, 9)],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "총수면시간_시간"]] +
                  [[f"S{i:02d}", str(6.7 + i * 0.1)] for i in range(1, 9)],
                  name="b.csv", index=1)
    log = IssueLog()
    check_units([a, b], log)
    hits = [i for i in log if i.kind == "단위의심"]
    assert len(hits) == 1
    assert hits[0].severity == INFO        # 종료코드에 영향을 주면 안 된다


def test_unit_suspicion_does_not_fire_on_a_plain_difference(tmp_path):
    """2배 차이 같은 평범한 값에서 울면 이 검사는 소음이 된다."""
    a = make_plan(tmp_path, [["id", "rmssd_ms"]] +
                  [[f"S{i:02d}", str(40 + i)] for i in range(1, 9)],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "rmssd_ms"]] +
                  [[f"S{i:02d}", str(80 + i)] for i in range(1, 9)],
                  name="b.csv", index=1)
    log = IssueLog()
    check_units([a, b], log)
    assert list(log) == []


def test_unit_suspicion_needs_at_least_five_values(tmp_path):
    a = make_plan(tmp_path, [["id", "tst_min"], ["S01", "420"], ["S02", "410"]],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "tst_시간"], ["S01", "7"], ["S02", "6.8"]],
                  name="b.csv", index=1)
    log = IssueLog()
    check_units([a, b], log)
    assert list(log) == []


# --------------------------------------------------------------------------
# 타임존
# --------------------------------------------------------------------------

def test_mixed_offsets_within_one_column_block(tmp_path):
    plan = make_plan(tmp_path,
                     [["id", "measured_at"],
                      ["S01", "2026-03-10 23:40+09:00"],
                      ["S02", "2026-03-10 23:40"]],
                     date_col="measured_at")
    log = IssueLog()
    check_timezones([plan], log)
    hits = [i for i in log if i.kind == "타임존혼재"]
    assert len(hits) == 1 and hits[0].blocking


def test_a_single_uniform_offset_is_fine(tmp_path):
    plan = make_plan(tmp_path,
                     [["id", "measured_at"],
                      ["S01", "2026-03-10 23:40+09:00"],
                      ["S02", "2026-03-11 23:40+09:00"]],
                     date_col="measured_at")
    log = IssueLog()
    check_timezones([plan], log)
    assert list(log) == []


def test_different_offsets_between_files_block(tmp_path):
    a = make_plan(tmp_path, [["id", "measured_at"],
                             ["S01", "2026-03-10 23:40+09:00"]],
                  name="a.csv", date_col="measured_at", index=0)
    b = make_plan(tmp_path, [["id", "measured_at"],
                             ["S01", "2026-03-10 14:40+00:00"]],
                  name="b.csv", date_col="measured_at", index=1)
    log = IssueLog()
    check_timezones([a, b], log)
    hits = [i for i in log if i.kind == "파일간타임존불일치"]
    assert len(hits) == 1 and hits[0].blocking


# --------------------------------------------------------------------------
# 접두어 충돌 / 결과 수확량
# --------------------------------------------------------------------------

def test_prefix_conflict_cancels_auto_removal():
    log = IssueLog()
    ok = check_prefix_conflict({"a.csv": "BELL-001-", "b.csv": "BELL-002-"}, log)
    assert ok is False
    assert kinds(log) == {"접두어불일치"}


def test_one_prefix_or_none_is_allowed():
    log = IssueLog()
    assert check_prefix_conflict({"a.csv": "BELL-001-", "b.csv": ""}, log) is True
    assert check_prefix_conflict({"a.csv": "", "b.csv": ""}, log) is True
    assert list(log) == []


def test_empty_result_is_critical():
    log = IssueLog()
    check_yield([], log, final_rows=0, unmatched=10, total_rows=10)
    hits = [i for i in log if i.kind == "결과없음"]
    assert len(hits) == 1 and hits[0].severity == CRITICAL


def test_mostly_unmatched_is_a_warning():
    log = IssueLog()
    check_yield([], log, final_rows=1, unmatched=9, total_rows=10)
    assert kinds(log) == {"미매칭과다"}


def test_a_healthy_merge_is_silent():
    log = IssueLog()
    check_yield([], log, final_rows=10, unmatched=1, total_rows=20)
    assert list(log) == []


# --------------------------------------------------------------------------
# 키 겹침
# --------------------------------------------------------------------------

def test_full_overlap_is_silent(tmp_path):
    a = make_plan(tmp_path, [["id", "v"], ["S01", "1"], ["S02", "2"]],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "w"], ["S01", "9"], ["S02", "8"]],
                  name="b.csv", index=1)
    log = IssueLog()
    check_key_overlap([a, b], log)
    assert list(log) == []


def test_zero_overlap_is_critical(tmp_path):
    a = make_plan(tmp_path, [["id", "v"], ["S01", "1"], ["S02", "2"]],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "w"], ["X01", "9"], ["X02", "8"]],
                  name="b.csv", index=1)
    log = IssueLog()
    check_key_overlap([a, b], log)
    hits = [i for i in log if i.kind == "키겹침없음"]
    assert len(hits) == 1 and hits[0].severity == CRITICAL


def test_low_overlap_is_a_warning(tmp_path):
    a = make_plan(tmp_path, [["id", "v"]] +
                  [[f"S{i:02d}", str(i)] for i in range(1, 11)],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "w"]] +
                  [[f"S{i:02d}", str(i)] for i in (1, 2, 30, 31, 32)],
                  name="b.csv", index=1)
    log = IssueLog()
    check_key_overlap([a, b], log)
    hits = [i for i in log if i.kind == "키겹침낮음"]
    assert len(hits) == 1 and hits[0].severity == WARNING


def test_zero_overlap_names_the_flag_that_would_fix_it(tmp_path):
    a = make_plan(tmp_path, [["id", "v"], ["S1", "1"], ["S2", "2"]],
                  name="a.csv", index=0)
    b = make_plan(tmp_path, [["id", "w"], ["1", "9"], ["2", "8"]],
                  name="b.csv", index=1)
    log = IssueLog()
    check_key_overlap([a, b], log)
    advice = [i.advice for i in log if i.kind == "키겹침없음"][0]
    assert "--unify-id-heads" in advice and "2명" in advice
