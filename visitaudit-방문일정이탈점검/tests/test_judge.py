"""판정 엔진 — 손으로 계산한 기대값과 대조.

가장 먼저 지키는 성질: 미도래·탈락후를 이탈로 세지 않는다 (크라잉울프 방지).
"""

import datetime as dt

from tests.conftest import d, mini_protocol, rec, subj
from visitaudit.judge import (E_DUP, E_FUTURE, E_PARSE, E_PREENROLL, E_SUBJECT,
                              V_IN, V_MISSING, V_NA_DROPOUT, V_NA_OPTIONAL,
                              V_OUT, V_PENDING, V_UNJUDGEABLE, judge)
from visitaudit.protocol import PPRules, Protocol, VisitDef

FAR = d(2026, 12, 31)  # 모든 창이 닫힌 뒤


def slot_of(res, sid, visit):
    for s in res.slots:
        if s.subject == sid and s.visit.name == visit:
            return s
    raise AssertionError(f"slot 없음: {sid} {visit}")


def base_recs(sid="S01", anchor="2026-03-02"):
    return [rec(sid, "Baseline", anchor)]


# ── 창 경계 포함 규칙: 창 [-3,3] 이면 -3일과 +3일은 창 안 ────────────
def test_window_boundary_inclusive_early():
    # 기준 2026-03-02, V1 예정 03-30, 창 03-27~04-02 (손계산)
    res = judge(base_recs() + [rec("S01", "V1", "2026-03-27")], None, mini_protocol(), FAR)
    s = slot_of(res, "S01", "V1")
    assert s.win_start == d(2026, 3, 27) and s.win_end == d(2026, 4, 2)
    assert s.verdict == V_IN and res.deviations == []


def test_window_boundary_inclusive_late():
    res = judge(base_recs() + [rec("S01", "V1", "2026-04-02")], None, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_IN
    assert res.deviations == []


def test_one_day_before_window_is_out_minus1():
    res = judge(base_recs() + [rec("S01", "V1", "2026-03-26")], None, mini_protocol(), FAR)
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_OUT and s.days_out == -1
    assert len(res.deviations) == 1 and res.deviations[0].kind == "창이탈"
    assert res.deviations[0].days_out == -1


def test_one_day_after_window_is_out_plus1():
    res = judge(base_recs() + [rec("S01", "V1", "2026-04-03")], None, mini_protocol(), FAR)
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_OUT and s.days_out == +1


def test_scheduled_day_in_window():
    res = judge(base_recs() + [rec("S01", "V1", "2026-03-30")], None, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_IN


# ── 미도래: 절대 이탈로 세지 않는다 (이 툴의 존재 조건) ──────────────
def test_pending_visits_are_never_deviations():
    """미래 방문 3건(기록 없음, 창 미마감) → 이탈 0건."""
    proto = Protocol(study="T", anchor="Baseline", target_n=None, visits=[
        VisitDef("Baseline", 0, 0, 0, True),
        VisitDef("V1", 28, -3, 3, True),
        VisitDef("V2", 56, -5, 5, True),
        VisitDef("V3", 84, -7, 7, True),
    ])
    as_of = d(2026, 8, 14)
    res = judge([rec("S01", "Baseline", "2026-08-01")], None, proto, as_of)
    assert len(res.deviations) == 0            # ← 무너지면 툴은 한 번 열리고 끝
    assert res.count(V_PENDING) == 3
    assert res.count(V_MISSING) == 0


def test_planned_future_rows_are_not_deviations():
    """'예정' 상태로 심은 미래 방문 3건 → 이탈 0건, 자백만 남는다."""
    proto = Protocol(study="T", anchor="Baseline", target_n=None, visits=[
        VisitDef("Baseline", 0, 0, 0, True),
        VisitDef("V1", 28, -3, 3, True),
        VisitDef("V2", 56, -5, 5, True),
        VisitDef("V3", 84, -7, 7, True),
    ])
    as_of = d(2026, 8, 14)
    records = [rec("S01", "Baseline", "2026-08-01"),
               rec("S01", "V1", "2026-08-29", status="예정"),
               rec("S01", "V2", "2026-09-26", status="예정"),
               rec("S01", "V3", "2026-10-24", status="scheduled")]
    res = judge(records, None, proto, as_of)
    assert len(res.deviations) == 0
    assert res.count(V_PENDING) == 3
    assert res.n_planned_rows == 3
    assert any("예정" in n for n in res.notes)


def test_window_end_equals_asof_is_pending():
    # 창 종료일 == as-of → 당일 방문이 아직 가능 → 미도래
    res = judge(base_recs(), None, mini_protocol(), d(2026, 4, 2))
    assert slot_of(res, "S01", "V1").verdict == V_PENDING
    assert res.deviations == []


def test_window_end_one_day_past_asof_is_missing():
    # 창 종료일(04-02) < as-of(04-03) → 닫힘 → 필수방문 결측
    res = judge(base_recs(), None, mini_protocol(), d(2026, 4, 3))
    assert slot_of(res, "S01", "V1").verdict == V_MISSING
    assert len(res.deviations) == 1
    assert res.deviations[0].kind == "필수방문결측"


# ── 탈락 처리: 결측이 아니라 해당없음 ───────────────────────────────
def test_dropout_before_window_end_is_na():
    subjects = [subj("S01", enroll="2026-03-02", dropout="2026-03-20")]
    res = judge(base_recs(), subjects, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_NA_DROPOUT
    assert res.deviations == []


def test_dropout_during_window_is_still_na():
    # 창(03-27~04-02) 도중 탈락(03-30) — 창 마감 전 탈락이므로 해당없음
    subjects = [subj("S01", dropout="2026-03-30")]
    res = judge(base_recs(), subjects, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_NA_DROPOUT


def test_dropout_after_window_end_missing_still_counts():
    # 창이 닫힐 때(04-02)까지 재적 중이었는데 기록 없음 → 결측 맞음
    subjects = [subj("S01", dropout="2026-05-01")]
    res = judge(base_recs(), subjects, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_MISSING


def test_record_after_dropout_is_na_not_deviation():
    subjects = [subj("S01", dropout="2026-03-20")]
    res = judge(base_recs() + [rec("S01", "V1", "2026-03-30")], subjects, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_NA_DROPOUT
    assert res.deviations == []


# ── 기준방문 문제 → 피험자 통째로 판정불가 (추정 금지) ───────────────
def test_anchor_missing_subject_unjudgeable():
    res = judge([rec("S01", "V1", "2026-03-30")], None, mini_protocol(), FAR)
    assert "S01" in res.subject_unjudgeable
    assert "기록 없음" in res.subject_unjudgeable["S01"]
    for s in res.slots:
        assert s.verdict == V_UNJUDGEABLE and s.error_kind == E_SUBJECT
    assert res.deviations == []          # 판정불가는 이탈이 아니다


def test_anchor_duplicated_subject_unjudgeable():
    records = [rec("S01", "Baseline", "2026-03-02", row=2),
               rec("S01", "Baseline", "2026-03-09", row=3),
               rec("S01", "V1", "2026-03-30")]
    res = judge(records, None, mini_protocol(), FAR)
    assert "중복" in res.subject_unjudgeable["S01"]


def test_anchor_unparseable_subject_unjudgeable():
    res = judge([rec("S01", "Baseline", "03/02/2026")], None, mini_protocol(), FAR)
    assert "해석 불가" in res.subject_unjudgeable["S01"]


def test_anchor_after_asof_is_not_yet_enrolled_not_unjudgeable():
    """기준방문일이 as-of 이후 = 그 시점엔 아직 시험에 안 들어온 사람.

    데이터가 나쁜 게 아니므로 '판정불가'(= 판정률을 깎는 쪽)가 아니라 판정 대상
    제외다. 과거 기준일로 되돌려 그때 보고한 숫자를 재현하는 것이 이 툴의 용도인데,
    미등록자를 판정불가로 세면 판정률이 무너져 애먼 exit 3 이 뜬다.
    """
    res = judge([rec("S01", "Baseline", "2026-09-01")], None, mini_protocol(), d(2026, 8, 14))
    assert "S01" not in res.subject_unjudgeable
    assert res.universe == []
    assert res.not_yet_enrolled == ["S01"]
    assert any("미등록" in n for n in res.notes)
    assert res.coverage_rate is None      # 판정 대상이 0건 — 임계 검사 생략


# ── 데이터 오류: 이탈로 세지 않고 크게 보고 ─────────────────────────
def test_duplicate_rows_unjudgeable_not_deviation():
    records = base_recs() + [rec("S01", "V1", "2026-03-30", row=3),
                             rec("S01", "V1", "2026-03-31", row=4)]
    res = judge(records, None, mini_protocol(), FAR)
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_UNJUDGEABLE and s.error_kind == E_DUP
    assert res.deviations == []
    assert len(res.data_errors) == 1 and res.data_errors[0].kind == E_DUP
    assert "3행" in res.data_errors[0].detail and "4행" in res.data_errors[0].detail


def test_identical_duplicate_rows_still_unjudgeable():
    records = base_recs() + [rec("S01", "V1", "2026-03-30", row=3),
                             rec("S01", "V1", "2026-03-30", row=4)]
    res = judge(records, None, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").error_kind == E_DUP


def test_parse_failure_unjudgeable():
    res = judge(base_recs() + [rec("S01", "V1", "이번주쯤")], None, mini_protocol(), FAR)
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_UNJUDGEABLE and s.error_kind == E_PARSE
    assert res.deviations == []


def test_future_dated_record_before_window_closes_is_pending():
    """창이 아직 안 닫혔는데 날짜만 미래 = 코디네이터가 적어 둔 다음 예약일.
    데이터 오류가 아니라 미도래다 — 멀쩡한 예약을 '원본 확인 필요'로 띄우면
    상태 열 없는 트래커에서 잡음만 수십 건 생긴다."""
    # Baseline 03-02 → V1 예정 03-30, 창 03-27~04-02. as-of 03-15 → 창 미마감.
    res = judge(base_recs() + [rec("S01", "V1", "2026-03-30")], None, mini_protocol(),
                d(2026, 3, 15))
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_PENDING
    assert res.deviations == [] and res.data_errors == []
    assert res.n_future_booked == 1


def test_future_dated_record_after_window_closed_is_error():
    """반대로 창이 이미 닫혔는데 날짜가 미래면 진짜 모순 — 데이터 오류로 남긴다."""
    # as-of 04-10 이면 V1 창(03-27~04-02)은 이미 마감. 그런데 기록은 04-20.
    res = judge(base_recs() + [rec("S01", "V1", "2026-04-20")], None, mini_protocol(),
                d(2026, 4, 10))
    s = slot_of(res, "S01", "V1")
    assert s.verdict == V_UNJUDGEABLE and s.error_kind == E_FUTURE
    assert res.n_future_booked == 0


def test_pre_enrollment_visit_is_error_for_postbaseline():
    subjects = [subj("S01", enroll="2026-03-02")]
    res = judge(base_recs() + [rec("S01", "V1", "2026-02-20")], subjects, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").error_kind == E_PREENROLL


def test_pre_enrollment_ok_for_screening_negative_offset():
    proto = Protocol(study="T", anchor="Baseline", target_n=None, visits=[
        VisitDef("Screening", -14, -14, 13, True),
        VisitDef("Baseline", 0, 0, 0, True),
    ])
    subjects = [subj("S01", enroll="2026-03-02")]
    records = [rec("S01", "Screening", "2026-02-16"), rec("S01", "Baseline", "2026-03-02")]
    res = judge(records, subjects, proto, FAR)
    # 스크리닝은 등록일 이전이 정상 — 오류가 아니어야 한다
    assert slot_of(res, "S01", "Screening").verdict == V_IN
    assert res.data_errors == []


# ── 순서 위반 ───────────────────────────────────────────────────────
def _two_visit_proto():
    return Protocol(study="T", anchor="Baseline", target_n=None, visits=[
        VisitDef("Baseline", 0, 0, 0, True),
        VisitDef("V1", 28, -3, 3, True),
        VisitDef("V2", 56, -5, 5, True),
    ])


def test_order_violation_detected():
    records = [rec("S01", "Baseline", "2026-03-02"),
               rec("S01", "V1", "2026-04-29"),      # 창이탈 +27 (창 03-27~04-02)
               rec("S01", "V2", "2026-04-27")]      # 창내 (04-22~05-02), V1 보다 앞섬
    res = judge(records, None, _two_visit_proto(), FAR)
    orders = res.deviations_of("순서위반")
    assert len(orders) == 1
    assert "V2(2026-04-27)" in orders[0].evidence and "V1(2026-04-29)" in orders[0].evidence
    # 창이탈도 별도로 잡힌다 (한 사건이 두 결함일 수 있다 — 문서화된 규칙)
    assert len(res.deviations_of("창이탈")) == 1


def test_same_day_visits_not_order_violation():
    records = [rec("S01", "Baseline", "2026-03-02"),
               rec("S01", "V1", "2026-03-30"),
               rec("S01", "V2", "2026-03-30")]      # V2 는 창이탈이지만 순서 위반은 아님
    res = judge(records, None, _two_visit_proto(), FAR)
    assert res.deviations_of("순서위반") == []


def test_unjudgeable_slots_skip_order_check():
    records = [rec("S01", "Baseline", "2026-03-02"),
               rec("S01", "V1", "깨진날짜"),
               rec("S01", "V2", "2026-04-27")]
    res = judge(records, None, _two_visit_proto(), FAR)
    assert res.deviations_of("순서위반") == []


# ── 필수 아님 / 프로토콜 외 방문 ────────────────────────────────────
def test_optional_visit_missing_is_not_deviation():
    proto = Protocol(study="T", anchor="Baseline", target_n=None, visits=[
        VisitDef("Baseline", 0, 0, 0, True),
        VisitDef("V1", 28, -3, 3, False),      # 필수 아님
    ])
    res = judge(base_recs(), None, proto, FAR)
    assert slot_of(res, "S01", "V1").verdict == V_NA_OPTIONAL
    assert res.deviations == []


def test_extra_protocol_visit_confessed_and_ignored():
    res = judge(base_recs() + [rec("S01", "UnscheduledX", "2026-03-15")],
                None, mini_protocol(), FAR)
    assert res.n_extra_visit_rows == 1
    assert any("프로토콜에 없는 방문명" in n for n in res.notes)
    assert len(res.slots) == 2  # Baseline, V1 만


# ── 달력 산술: 윤년·월말 (손계산 값) ────────────────────────────────
def test_leap_year_scheduled_date():
    # 2028-02-01 + 28일 = 2028-02-29 (윤년에 실제로 존재)
    res = judge([rec("S01", "Baseline", "2028-02-01"),
                 rec("S01", "V1", "2028-02-29")], None, mini_protocol(), d(2028, 12, 31))
    s = slot_of(res, "S01", "V1")
    assert s.scheduled == d(2028, 2, 29)
    assert s.verdict == V_IN and s.actual == d(2028, 2, 29)


def test_leap_day_record_in_window():
    # 기준 2028-01-31 → V1 예정 02-28, 창 02-25~03-02. 02-29 기록은 창내.
    res = judge([rec("S01", "Baseline", "2028-01-31"),
                 rec("S01", "V1", "2028-02-29")], None, mini_protocol(), d(2028, 12, 31))
    s = slot_of(res, "S01", "V1")
    assert s.scheduled == d(2028, 2, 28)
    assert s.win_start == d(2028, 2, 25) and s.win_end == d(2028, 3, 2)
    assert s.verdict == V_IN


def test_month_end_offset_nonleap():
    # 2026-01-31 + 28일 = 2026-02-28 (2026 은 평년 — 손계산)
    res = judge([rec("S01", "Baseline", "2026-01-31")], None, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").scheduled == d(2026, 2, 28)


# ── 커버리지 산술 ───────────────────────────────────────────────────
def test_coverage_rate_excludes_pending_and_na():
    # S01: V1 창내(판정완료). S02: V1 미도래. S03: 탈락 → V1 해당없음.
    # S04: V1 날짜 깨짐(판정불가).
    as_of = d(2026, 4, 10)
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30"),
               rec("S02", "Baseline", "2026-04-01"),
               rec("S03", "Baseline", "2026-03-02"),
               rec("S04", "Baseline", "2026-03-02"), rec("S04", "V1", "몰라요")]
    subjects = [subj("S01"), subj("S02"), subj("S03", dropout="2026-03-10"), subj("S04")]
    res = judge(records, subjects, mini_protocol(), as_of)
    # 판정완료: S01 2 + S02 1(Baseline) + S03 1(Baseline) + S04 1(Baseline) = 5
    assert res.n_completed == 5
    assert res.count(V_PENDING) == 1
    assert res.count(V_NA_DROPOUT) == 1
    assert res.n_unjudgeable == 1
    # 판정률 = 5 / (5 + 1) = 83.333...
    assert abs(res.coverage_rate - 100.0 * 5 / 6) < 1e-9


def test_coverage_rate_none_when_no_judgeable():
    # 갓 시작한 시험: 기준방문만 있고 나머지 전부 미도래 → 분모가 0이 아님(기준방문은 판정됨)
    # 진짜 분모 0: 피험자 자체가 없다
    res = judge([], None, mini_protocol(), FAR)
    assert res.coverage_rate is None
    assert res.n_slots == 0


def test_universe_from_subjects_randomized_only():
    records = [rec("S01", "Baseline", "2026-03-02"),
               rec("S99", "Baseline", "2026-03-02")]  # 피험자 CSV에 없음
    subjects = [subj("S01"), subj("S02", arm="")]      # S02 는 미배정(스크린 실패)
    res = judge(records, subjects, mini_protocol(), FAR)
    assert res.universe == ["S01"]
    assert any("피험자.csv 에 없는" in w for w in res.warnings)
    assert res.n_nonuniverse_rows == 1


def test_randomized_subject_with_no_rows_is_unjudgeable():
    subjects = [subj("S01")]
    res = judge([], subjects, mini_protocol(), FAR)
    assert res.subject_unjudgeable["S01"].startswith("기준방문")
    assert res.n_slots == 2


def test_duplicated_subject_row_unjudgeable():
    s1 = subj("S01")
    s1.duplicated = True
    res = judge([rec("S01", "Baseline", "2026-03-02")], [s1], mini_protocol(), FAR)
    assert "중복" in res.subject_unjudgeable["S01"]


def test_dropout_date_unparseable_unjudgeable():
    s1 = subj("S01", dropout="언젠가")
    res = judge([rec("S01", "Baseline", "2026-03-02")], [s1], mini_protocol(), FAR)
    assert "탈락일 해석 불가" in res.subject_unjudgeable["S01"]


def test_notdone_row_closed_window_is_missing():
    records = base_recs() + [rec("S01", "V1", "2026-03-30", status="취소")]
    res = judge(records, None, mini_protocol(), FAR)
    assert slot_of(res, "S01", "V1").verdict == V_MISSING
    assert res.n_notdone_rows == 1


def test_notdone_row_open_window_is_pending():
    records = base_recs() + [rec("S01", "V1", "2026-03-30", status="취소")]
    res = judge(records, None, mini_protocol(), d(2026, 3, 31))
    assert slot_of(res, "S01", "V1").verdict == V_PENDING
    assert res.deviations == []


def test_time_rows_counted():
    records = base_recs() + [rec("S01", "V1", "2026-03-30 14:00")]
    res = judge(records, None, mini_protocol(), FAR)
    assert res.n_time_rows == 1
    assert slot_of(res, "S01", "V1").verdict == V_IN
    assert any("시각 정보" in n for n in res.notes)


def test_severity_thresholds():
    pp = PPRules(missing_required=True, max_days_out=7)
    proto = mini_protocol(pp=pp)
    # +7 은 경미(임계 이하), +8 은 중대
    res7 = judge(base_recs() + [rec("S01", "V1", "2026-04-09")], None, proto, FAR)
    res8 = judge(base_recs() + [rec("S01", "V1", "2026-04-10")], None, proto, FAR)
    assert res7.deviations[0].days_out == 7 and res7.deviations[0].severity == "경미"
    assert res8.deviations[0].days_out == 8 and res8.deviations[0].severity == "중대"
