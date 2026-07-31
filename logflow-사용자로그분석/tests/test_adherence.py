"""프로토콜 준수도(adherence) — 손으로 계산한 값과 대조하는 테스트."""

from datetime import date, datetime

import pytest

from logflow.adherence import MAX_WEEKS_CAP, Adherence, adherence
from logflow.analyze import analyze, to_csv_tables, to_dict
from logflow.dataio import Event


def _ev(user, day, hour=10, name="open", group=None):
    return Event(user=user, name=name, ts=datetime(2026, 1, day, hour), group=group)


def _fixture_events():
    """손계산용 고정 데이터 — 관찰 종료일 2026-01-21.

    A: 첫 활동 1/1 → 완전 관찰 3주. W1(1/1~7) 5일, W2(1/8~14) 2일, W3(1/15~21) 3일
    B: 첫 활동 1/8 → 완전 관찰 2주. W1(1/8~14) 3일, W2(1/15~21) 4일
    C: 첫 활동 1/18 → 완전 관찰 0주 (분모에서 제외)
    """
    evs = []
    for d in (1, 2, 3, 4, 5, 8, 9, 15, 16, 17):
        evs.append(_ev("A", d))
    for d in (8, 9, 10, 15, 16, 17, 18):
        evs.append(_ev("B", d))
    for d in (18, 21):
        evs.append(_ev("C", d))
    return evs


def test_hand_computed_weekly_and_user_level():
    ad = adherence(_fixture_events(), min_days=3)
    assert ad.window_weeks == 3
    assert ad.observed_weeks == 3

    # ── 주차별 (손계산) ────────────────────────────────────────────────
    w1, w2, w3 = ad.weeks
    assert (w1.week, w1.eligible, w1.adherent) == (1, 2, 2)   # A=5일, B=3일
    assert w1.rate == 1.0
    assert w1.median_active_days == 4.0                        # median(5, 3)
    assert (w2.week, w2.eligible, w2.adherent) == (2, 2, 1)   # A=2일(미준수), B=4일
    assert w2.rate == 0.5
    assert w2.median_active_days == 3.0                        # median(2, 4)
    assert (w3.week, w3.eligible, w3.adherent) == (3, 1, 1)   # A만 대상
    assert w3.median_active_days == 3.0

    # ── 참여자별 ──────────────────────────────────────────────────────
    by_user = {u.user: u for u in ad.users}
    assert (by_user["A"].eligible_weeks, by_user["A"].adherent_weeks) == (3, 2)
    assert by_user["A"].rate == pytest.approx(2 / 3)
    assert by_user["A"].longest_streak == 1                    # W1 ✓, W2 ✗, W3 ✓
    assert by_user["A"].active_days_in_window == 10
    assert (by_user["B"].eligible_weeks, by_user["B"].adherent_weeks) == (2, 2)
    assert by_user["B"].longest_streak == 2
    assert by_user["C"].eligible_weeks == 0
    assert by_user["C"].rate is None

    # ── 전체 ─────────────────────────────────────────────────────────
    assert ad.n_users == 2                     # C 는 완전 관찰 주가 없어 제외
    assert ad.n_no_full_week == 1
    assert ad.n_adherent_users == 1            # B 만 rate >= 0.8
    assert ad.median_user_rate == pytest.approx((2 / 3 + 1.0) / 2)
    assert ad.median_streak == 1.5
    assert any("7일보다 짧아" in n for n in ad.notes)
    # 분모가 참여자마다 다르면(A=3주, B=2주) 그 사실을 경고해야 한다.
    assert ad.eligible_weeks_range == (2, 3)
    assert any("관찰된 주 수가 다릅니다" in n for n in ad.notes)


def test_partial_last_week_excluded_from_denominator():
    """관찰 종료일에 걸친 부분 주는 분모에 넣지 않는다 (미준수로 세면 편향)."""
    evs = [_ev("A", d) for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    ad = adherence(evs, min_days=3)          # 1/1~1/10 → 완전 관찰 1주뿐
    assert ad.window_weeks == 1
    assert [w.eligible for w in ad.weeks] == [1]
    assert ad.users[0].eligible_weeks == 1


def test_target_boundary_is_inclusive():
    """준수 주 비율이 정확히 target 이면 '준수 참여자' 로 센다 (부동소수 여유)."""
    # 5주 관찰: 1~4주차만 준수 → 4/5 = 0.8
    days = []
    for week in range(5):
        n = 3 if week < 4 else 0
        days += [1 + week * 7 + i for i in range(n)]
    days.append(35)   # 5주차 마지막까지 관찰되도록 (1/1 + 34일 = 2/4)
    evs = [Event(user="A", name="open",
                 ts=datetime.fromordinal(date(2026, 1, 1).toordinal() + d - 1)
                 .replace(hour=9))
           for d in days]
    ad = adherence(evs, min_days=3, target=0.8)
    assert ad.users[0].eligible_weeks == 5
    assert ad.users[0].adherent_weeks == 4
    assert ad.n_adherent_users == 1


def test_period_days_generalizes_to_non_weekly():
    """--adherence-period 로 격주/3일 단위 규약도 볼 수 있다."""
    evs = [_ev("A", d) for d in (1, 2, 4, 5, 7, 8, 10, 11)]  # 1/1~1/11
    ad = adherence(evs, min_days=2, period_days=3)
    # 관찰일 11일 → 3일 기간 3개 완전 관찰
    assert ad.period_days == 3
    assert ad.window_weeks == 3
    assert [w.week for w in ad.weeks] == [1, 2, 3]
    # 기간1 = 1/1~1/3 (2일), 기간2 = 1/4~1/6 (2일), 기간3 = 1/7~1/9 (2일)
    assert [w.adherent for w in ad.weeks] == [1, 1, 1]


def test_end_argument_fixes_observation_horizon():
    """관찰 종료일을 밖에서 주면(군 비교용) eligible 판정이 그 날 기준이 된다."""
    evs = [_ev("A", d) for d in (1, 2, 3)]
    assert adherence(evs, min_days=1).window_weeks == 0            # 3일뿐
    ad = adherence(evs, min_days=1, end=date(2026, 1, 14))         # 전체 데이터가 더 길다면
    assert ad.window_weeks == 2
    assert [w.adherent for w in ad.weeks] == [1, 0]


def test_max_weeks_longer_than_data_yields_no_completers():
    """8주 창을 요청했는데 데이터가 2주뿐이면 완주자가 없어 '준수 참여자' 를 내지 않는다."""
    evs = [_ev("A", d) for d in (1, 8, 15)]     # 1/1~1/15 → 완전 관찰 2주
    ad = adherence(evs, min_days=1, max_weeks=8)
    assert ad.window_weeks == 8 and ad.required_weeks == 8
    assert ad.observed_weeks == 2
    assert len(ad.weeks) == 2                   # 표는 관찰된 만큼만 (빈 행을 만들지 않는다)
    assert ad.n_users == 0 and ad.n_adherent_users == 0
    assert ad.n_incomplete == 1
    assert ad.adherent_ci is None and ad.median_user_rate is None
    assert any("8주" in n and "2주" in n for n in ad.notes)
    assert any("끝까지 관찰한 참여자가 없어" in n for n in ad.notes)


def test_max_weeks_truncates_window():
    evs = [_ev("A", d) for d in range(1, 22)]   # 3주 완전 관찰
    ad = adherence(evs, min_days=1, max_weeks=2)
    assert ad.window_weeks == 2
    assert ad.observed_weeks == 3
    assert ad.users[0].eligible_weeks == 2      # 참여자 단위도 같은 창을 쓴다


def test_shorter_than_one_period_gives_note_and_empty_table():
    evs = [_ev("A", 1), _ev("A", 2)]
    ad = adherence(evs, min_days=1)
    assert ad.window_weeks == 0 and ad.weeks == []
    assert ad.n_users == 0 and ad.adherent_ci is None
    assert any("완전히 관찰된 주가 없습니다" in n for n in ad.notes)


def test_empty_events():
    ad = adherence([], min_days=3)
    assert isinstance(ad, Adherence)
    assert ad.n_users == 0 and ad.weeks == [] and ad.users == []


def test_multiple_events_same_day_count_once():
    evs = [_ev("A", 1, hour=h) for h in range(9, 20)] + [_ev("A", 8)]
    ad = adherence(evs, min_days=2)
    assert ad.weeks[0].adherent == 0            # 1주차 활성일은 하루뿐
    assert ad.weeks[0].median_active_days == 1.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_days": 0},
        {"min_days": 8},
        {"min_days": 3, "period_days": 0},
        {"min_days": 3, "target": 0.0},
        {"min_days": 3, "target": 1.5},
        {"min_days": 3, "max_weeks": 0},
    ],
)
def test_invalid_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        adherence([_ev("A", 1)], **kwargs)


def test_cap_note_when_window_exceeds_default_cap():
    """기본 상한(104주)을 넘는 장기 로그는 잘라내고 그 사실을 알린다."""
    evs = [Event(user="A", name="open", ts=datetime(2026, 1, 1)),
           Event(user="A", name="open", ts=datetime(2030, 1, 1))]
    ad = adherence(evs, min_days=1)
    assert ad.window_weeks == MAX_WEEKS_CAP
    assert ad.observed_weeks > MAX_WEEKS_CAP
    assert any(str(MAX_WEEKS_CAP) in n for n in ad.notes)


def test_fixed_window_restricts_denominator_to_completers():
    """--adherence-weeks 를 주면 창을 끝까지 관찰한 사람만 '준수 참여자' 분모에 넣는다.

    그러지 않으면 1주만 관찰된 사람의 '1/1 = 100%' 가 3주를 채운 사람의 '2/3' 를 이긴다.
    """
    ad = adherence(_fixture_events(), min_days=3, max_weeks=3)
    assert ad.required_weeks == 3
    assert ad.n_users == 1              # A 만 3주 완주 (B=2주, C=0주)
    assert ad.n_incomplete == 1         # B
    assert ad.n_no_full_week == 1       # C
    assert ad.n_adherent_users == 0     # A 의 2/3 는 0.8 미만
    assert ad.eligible_weeks_range == (3, 3)
    assert any("추적 미완료" in n for n in ad.notes)
    # 창을 고정하지 않으면 관찰이 짧은 B 가 100% 로 잡혀 결과가 뒤집힌다.
    assert adherence(_fixture_events(), min_days=3).n_adherent_users == 1


def test_late_enroller_cannot_beat_completer_under_fixed_window():
    """등록이 늦어 1주만 관찰된 사람이 '100% 준수' 로 분류되지 않는다 (교란 방지)."""
    evs = [_ev("early", d) for d in (1, 2, 3, 4, 5)]          # 1주차만 준수, 이후 침묵
    evs += [_ev("early", 21)]                                  # 관찰 종료일 1/21 (3주)
    evs += [_ev("late", d) for d in (15, 16, 17, 18, 19)]      # 1/15 등록 → 1주만 관찰
    loose = adherence(evs, min_days=5)
    assert loose.n_users == 2 and loose.n_adherent_users == 1  # late 가 1/1 = 100%
    fixed = adherence(evs, min_days=5, max_weeks=3)
    assert fixed.n_users == 1 and fixed.n_incomplete == 1      # late 는 완주 못 함
    assert fixed.n_adherent_users == 0


def test_user_rows_are_internally_consistent():
    """부분 주가 참여자 집계로 새면 adherent_weeks > eligible_weeks (rate>1) 가 된다."""
    evs = [_ev("B", d) for d in range(1, 26)]                  # 1/1~1/25 → 3주
    evs += [_ev("A", d) for d in (15, 16, 17, 22, 23, 24)]     # A 첫 활동 1/15 → 1주
    ad = adherence(evs, min_days=3)
    a = next(u for u in ad.users if u.user == "A")
    assert (a.eligible_weeks, a.adherent_weeks, a.active_days_in_window) == (1, 1, 3)
    for u in ad.users:
        assert 0 <= u.adherent_weeks <= u.eligible_weeks
        assert u.longest_streak <= u.eligible_weeks
        assert u.rate is None or 0.0 <= u.rate <= 1.0
    for w in ad.weeks:
        assert 0 <= w.adherent <= w.eligible


def test_end_before_first_activity_clamps_to_zero_weeks():
    """관찰 종료일이 첫 활동보다 이르면 음수 주차가 나오지 않는다."""
    ad = adherence([_ev("A", 20)], min_days=1, end=date(2026, 1, 1))
    assert ad.users[0].eligible_weeks == 0
    assert ad.n_users == 0


def test_small_cell_weeks_get_reidentification_note():
    ad = adherence(_fixture_events(), min_days=3)   # 3주차 대상 1명
    assert ad.weeks[2].eligible == 1
    assert any("대상이 5명 미만" in n for n in ad.notes)


def test_user_row_order_is_stable():
    ad = adherence(_fixture_events(), min_days=3)
    assert [u.user for u in ad.users] == ["A", "B", "C"]


def test_confidence_level_reaches_weekly_and_overall_cis():
    ad95 = adherence(_fixture_events(), min_days=3, confidence=0.95)
    ad80 = adherence(_fixture_events(), min_days=3, confidence=0.80)
    assert ad80.adherent_ci[0] > ad95.adherent_ci[0]
    assert ad80.adherent_ci[1] < ad95.adherent_ci[1]
    assert ad80.weeks[1].ci[0] > ad95.weeks[1].ci[0]
    # Wilson 1/2 @95% 를 직접 대조 (adherent 1 / eligible 2)
    assert ad95.adherent_ci == pytest.approx((0.0946, 0.9054), abs=1e-3)


# ---------------------------------------------------------------- 통합

def test_analysis_json_and_csv_include_adherence():
    a = analyze(_fixture_events(), adherence_min_days=3)
    d = to_dict(a)["adherence"]
    assert d["min_days"] == 3 and d["period_days"] == 7
    assert d["n_users"] == 2 and d["n_adherent_users"] == 1
    assert len(d["weeks"]) == 3 and len(d["users"]) == 3
    assert d["adherent_rate"] == pytest.approx(0.5)

    tables = to_csv_tables(a)
    assert "adherence_weekly" in tables and "adherence_users" in tables
    assert tables["adherence_weekly"].splitlines()[0].startswith("week,eligible,adherent")
    assert "A,3,2," in tables["adherence_users"]


def test_adherence_csv_rows_match_header_order():
    """열 순서가 뒤바뀌어도 헤더만 보면 알 수 없으므로 값까지 고정한다."""
    a = analyze(_fixture_events(), adherence_min_days=3)
    weekly = to_csv_tables(a)["adherence_weekly"].splitlines()
    # week,eligible,adherent,rate,ci_low,ci_high,median_active_days,min_days,period_days
    assert weekly[1].startswith("1,2,2,1.0,")
    lo, hi = weekly[1].split(",")[4:6]
    assert float(lo) < float(hi)
    assert weekly[1].endswith(",4.0,3,7")
    assert weekly[2].startswith("2,2,1,0.5,")
    assert weekly[3].startswith("3,1,1,1.0,")

    users = to_csv_tables(a)["adherence_users"].splitlines()
    assert users[1] == "A,3,2,0.6667,1,10"
    assert users[2] == "B,2,2,1.0,2,7"
    assert users[3] == "C,0,0,,0,0"


def test_csv_cells_are_not_spreadsheet_formulas():
    """엑셀용 표에 `=cmd|...` 같은 값이 수식으로 실려 나가지 않게 한다."""
    evil = "=cmd|' /C calc'!A0"
    evs = [Event(user=evil, name="+SUM(A1)", ts=datetime(2026, 1, d, 9), group="-x")
           for d in (1, 2, 3, 8, 9, 15)]
    tables = to_csv_tables(analyze(evs, group_col="arm", adherence_min_days=1))
    for name, text in tables.items():
        for line in text.splitlines()[1:]:
            for cell in line.split(","):
                bare = cell.strip('"')
                assert not bare.startswith(("=", "+", "@")) or bare.startswith("'"), (name, line)
    # 값 자체는 보존된다 (앞에 작은따옴표만 붙음).
    assert "'" + evil in tables["adherence_users"]


def test_adherence_absent_unless_requested():
    a = analyze(_fixture_events())
    assert a.adherence is None
    assert to_dict(a)["adherence"] is None
    assert "adherence_weekly" not in to_csv_tables(a)
