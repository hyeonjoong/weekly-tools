"""CONSORT 숫자·정합성 검사·PP 후보 (중복 제거 포함)·등록 외삽."""

import datetime as dt

from tests.conftest import crit, d, mini_protocol, rec, subj
from visitaudit.consort import build_consort, build_pp, consort_text
from visitaudit.criteria import recheck
from visitaudit.enroll import build_enrollment
from visitaudit.judge import judge
from visitaudit.protocol import PPRules

FAR = d(2026, 12, 31)


def _judged(records, subjects, proto=None, as_of=FAR):
    return judge(records, subjects, proto or mini_protocol(), as_of)


def test_consort_counts_and_checks_pass():
    subjects = [
        subj("S01", arm="A", enroll="2026-03-02"),
        subj("S02", arm="B", enroll="2026-03-09", dropout="2026-03-20", dropout_reason="이상반응"),
        subj("S03", arm="", screenfail="기준미달"),
        subj("S04", arm="", screenfail="동의철회"),
    ]
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30"),
               rec("S02", "Baseline", "2026-03-09")]
    judged = _judged(records, subjects)
    c = build_consort(subjects, judged, mini_protocol(), FAR)
    assert c.n_screened == 4
    assert c.n_excluded == 2
    assert dict(c.excluded_reasons) == {"기준미달": 1, "동의철회": 1}
    assert c.n_randomized == 2 and c.n_itt == 2
    assert c.arm_counts == {"A": 1, "B": 1}
    # S01 은 마지막 방문(V1) 창내 기록 보유 → 완료. S02 는 탈락.
    assert c.arm_completed["A"] == 1 and c.arm_dropout["B"] == 1
    assert c.arm_dropout_reasons["B"] == [("이상반응", 1)]
    assert all(ok for _, ok, _indep in c.checks)
    # 실패할 수 있는 검사는 교차 검증 하나뿐이라는 사실 자체를 고정한다 —
    # 나머지는 같은 목록을 갈라 센 항등식이라 '✓' 가 아무것도 보증하지 않는다.
    assert [desc for desc, _ok, indep in c.checks if indep] == [
        f"ITT({c.n_itt}) = 판정 대상 피험자({c.n_itt})"]


def test_consort_check_fails_on_ambiguous_duplicate():
    # 중복 기재된 행끼리 무작위배정 여부가 **엇갈릴 때만** 판정 대상(universe)에
    # 들어간다. 첫 행은 군 미기재(→ CONSORT 는 스크린실패로 셈), 뒤 행은 군 A
    # (→ judge 는 배정된 것으로 봄) → 두 숫자가 어긋나고 교차 검증이 잡는다.
    s1 = subj("S05", arm="", screenfail="")
    s1.duplicated = True
    s2 = subj("S05", arm="A")
    s2.duplicated = True
    subjects = [subj("S01", arm="A"), s1, s2]
    judged = _judged([rec("S01", "Baseline", "2026-03-02")], subjects)
    c = build_consort(subjects, judged, mini_protocol(), FAR)
    bad = [desc for desc, ok, _indep in c.checks if not ok]
    assert any("ITT" in desc for desc in bad)


def test_consistent_nonrandomized_duplicate_stays_out_of_itt():
    # 반대로 중복된 행이 모두 '군 미기재'로 일치하면 불확실하지 않다 —
    # 스크린 실패자를 ITT 판정 대상에 끌어들여 판정률을 깎으면 안 된다.
    s1 = subj("S05", arm="", screenfail="기준미달")
    s1.duplicated = True
    s2 = subj("S05", arm="", screenfail="기준미달")
    s2.duplicated = True
    subjects = [subj("S01", arm="A"), s1, s2]
    judged = _judged([rec("S01", "Baseline", "2026-03-02")], subjects)
    assert judged.universe == ["S01"]
    assert "S05" not in judged.subject_unjudgeable
    c = build_consort(subjects, judged, mini_protocol(), FAR)
    assert all(ok for _, ok, _indep in c.checks)


def test_consort_without_subjects_fallback():
    judged = _judged([rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30")], None)
    c = build_consort(None, judged, mini_protocol(), FAR)
    assert c.available is False
    assert c.fallback_subjects == 1
    assert c.fallback_last_visit_done == 1
    assert any("피험자.csv 없음" in n for n in c.notes)


def test_screenfail_reason_missing_label():
    subjects = [subj("S01", arm=""), subj("S02", arm="A")]
    judged = _judged([rec("S02", "Baseline", "2026-03-02")], subjects)
    c = build_consort(subjects, judged, mini_protocol(), FAR)
    assert c.excluded_reasons == [("사유 미기재", 1)]


# ── PP 후보 ─────────────────────────────────────────────────────────
def _pp_setup(records, subjects, pp_rules, incl=None):
    proto = mini_protocol(pp=pp_rules, incl=incl)
    judged = judge(records, subjects, proto, FAR)
    cr = recheck(subjects, proto)
    return judged, build_pp(judged, cr, subjects, proto, FAR)


def test_pp_window_threshold_boundary():
    pp_rules = PPRules(missing_required=False, max_days_out=7, dropout=False,
                       eligibility_violation=False)
    # +7일 이탈 → '7일 초과' 아님 → 후보 유지
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-04-09")]
    _, pp = _pp_setup(records, [subj("S01")], pp_rules)
    assert pp.entries["S01"].status == "후보"
    # +8일 → 제외
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-04-10")]
    _, pp = _pp_setup(records, [subj("S01")], pp_rules)
    assert pp.entries["S01"].status == "제외"
    assert pp.entries["S01"].reasons == ["창이탈 7일초과"]


def test_pp_missing_required_rule():
    pp_rules = PPRules(missing_required=True, dropout=False, eligibility_violation=False)
    records = [rec("S01", "Baseline", "2026-03-02")]     # V1 결측 (창 닫힘)
    _, pp = _pp_setup(records, [subj("S01")], pp_rules)
    assert pp.entries["S01"].reasons == ["필수방문결측"]


def test_pp_dropout_rule():
    pp_rules = PPRules(dropout=True, eligibility_violation=False)
    records = [rec("S01", "Baseline", "2026-03-02")]
    _, pp = _pp_setup(records, [subj("S01", dropout="2026-03-10")], pp_rules)
    assert pp.entries["S01"].reasons == ["탈락"]


def test_pp_eligibility_rule():
    pp_rules = PPRules(eligibility_violation=True, dropout=False)
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30")]
    _, pp = _pp_setup(records, [subj("S01", age=17)], pp_rules,
                      incl=[crit("age", ">=", 19)])
    assert pp.entries["S01"].reasons == ["선정기준위반"]


def test_pp_dedup_two_reasons_one_person():
    """한 사람이 결측 + 탈락… 은 불가능하므로 창이탈초과 + 선정기준위반으로 중복을 만든다."""
    pp_rules = PPRules(missing_required=True, max_days_out=7,
                       eligibility_violation=True, dropout=True)
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-04-15"),  # +13일
               rec("S02", "Baseline", "2026-03-02"), rec("S02", "V1", "2026-03-30")]
    subjects = [subj("S01", age=17), subj("S02", age=30)]
    judged, pp = _pp_setup(records, subjects, pp_rules, incl=[crit("age", ">=", 19)])
    e = pp.entries["S01"]
    assert e.status == "제외"
    assert set(e.reasons) == {"창이탈 7일초과", "선정기준위반"}
    assert pp.n_excluded == 1               # 사유는 2건이지만 사람은 1명
    assert pp.n_dedup_removed == 1          # "중복 1명 제거"
    assert pp.n_candidates == 1             # S02


def test_pp_unjudgeable_subject_not_candidate():
    pp_rules = PPRules()
    records = [rec("S01", "V1", "2026-03-30")]   # 기준방문 없음 → 판정불가
    _, pp = _pp_setup(records, [subj("S01")], pp_rules)
    assert pp.entries["S01"].status == "판정불가"
    assert pp.n_unjudgeable == 1 and pp.n_candidates == 0


def test_pp_skipped_without_rules():
    judged = _judged([rec("S01", "Baseline", "2026-03-02")], [subj("S01")])
    cr = recheck([subj("S01")], mini_protocol())
    pp = build_pp(judged, cr, [subj("S01")], mini_protocol(), FAR)   # pp_rules=None
    assert pp.skipped is not None


def test_consort_text_renders():
    subjects = [subj("S01", arm="A"), subj("S02", arm="B"), subj("S03", arm="", screenfail="기준미달")]
    judged = _judged([rec("S01", "Baseline", "2026-03-02"), rec("S02", "Baseline", "2026-03-02")], subjects)
    proto = mini_protocol(pp=PPRules())
    cr = recheck(subjects, proto)
    pp = build_pp(judged, cr, subjects, proto, FAR)
    text = consort_text(build_consort(subjects, judged, proto, FAR), pp, proto, "2026-08-14")
    assert "스크리닝 3" in text and "무작위배정 2" in text
    assert "2026-08-14" in text
    assert "정합성 검사" in text


# ── 등록 진행 ───────────────────────────────────────────────────────
def test_enrollment_monthly_and_projection():
    # 5~7월 (4, 4, 3)명 → 평균 3.67/월. 목표 120, 등록 20 → 남은 100
    # ceil(100 / 3.6667) = 28개월 → 2026-08 + 28 = 2028-12 (손계산)
    subjects = []
    dates = (["2026-03-0%d" % i for i in (1, 2, 3, 4, 5)]
             + ["2026-04-0%d" % i for i in (1, 2, 3, 4)]
             + ["2026-05-0%d" % i for i in (1, 2, 3, 4)]
             + ["2026-06-0%d" % i for i in (1, 2, 3, 4)]
             + ["2026-07-0%d" % i for i in (1, 2, 3)])
    for i, day in enumerate(dates):
        subjects.append(subj(f"S{i:02d}", enroll=day))
    e = build_enrollment(subjects, 120, d(2026, 8, 14))
    assert e.monthly == [("2026-03", 5), ("2026-04", 4), ("2026-05", 4),
                         ("2026-06", 4), ("2026-07", 3), ("2026-08", 0)]
    assert e.rate_months == ["2026-05", "2026-06", "2026-07"]
    assert abs(e.rate - 11 / 3) < 1e-9
    assert e.remaining == 100
    assert e.projected_month == "2028-12"


def test_enrollment_gap_month_shows_zero():
    subjects = [subj("S01", enroll="2026-03-05"), subj("S02", enroll="2026-05-05")]
    e = build_enrollment(subjects, None, d(2026, 5, 31))
    assert e.monthly == [("2026-03", 1), ("2026-04", 0), ("2026-05", 1)]


def test_enrollment_zero_rate_no_projection():
    subjects = [subj("S01", enroll="2026-01-05")]
    e = build_enrollment(subjects, 100, d(2026, 8, 14))
    assert e.rate == 0.0
    assert e.projected_month is None


def test_enrollment_target_reached():
    subjects = [subj("S01", enroll="2026-03-05"), subj("S02", enroll="2026-03-06")]
    e = build_enrollment(subjects, 2, d(2026, 8, 14))
    assert e.remaining == 0
    assert e.projected_month == "2026-08"


def test_enrollment_skipped_cases():
    assert build_enrollment(None, 100, FAR).skipped == "피험자.csv 없음"
    assert "무작위배정" in build_enrollment([subj("S01", arm="")], 100, FAR).skipped
    assert "등록일" in build_enrollment([subj("S01")], 100, FAR).skipped


def test_enrollment_missing_dates_counted():
    subjects = [subj("S01", enroll="2026-03-05"), subj("S02")]  # S02 등록일 없음
    e = build_enrollment(subjects, None, d(2026, 3, 31))
    assert e.n_missing_dates == 1
    assert e.n_enrolled == 2
