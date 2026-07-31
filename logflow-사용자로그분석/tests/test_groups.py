"""군(arm) 비교 분석 검증 — 배정 · 비율/분포 검정 · 이탈 생존 · 다중비교."""

from datetime import datetime, timedelta

import pytest

from logflow.analyze import analyze, to_csv_tables, to_dict
from logflow.dataio import Event
from logflow.groups import assign_groups, churn_survival, compare_groups
from logflow.stats import fisher_exact_two_sided

BASE = datetime(2026, 1, 1, 9, 0, 0)


def ev(user, name, day, hour=9, minute=0, group=None):
    return Event(user=user, name=name,
                 ts=BASE.replace(hour=hour, minute=minute) + timedelta(days=day),
                 group=group)


def two_arm_events():
    """A군 4명 중 3명이 day-1 재방문, B군 4명 중 1명 — Fisher [[3,1],[1,3]] 이 되도록."""
    events = []
    for i, u in enumerate(["a1", "a2", "a3", "a4"]):
        events.append(ev(u, "open", 0, group="A"))
    for u in ["a1", "a2", "a3"]:
        events.append(ev(u, "open", 1, group="A"))
    for u in ["b1", "b2", "b3", "b4"]:
        events.append(ev(u, "open", 0, group="B"))
    events.append(ev("b1", "open", 1, group="B"))
    # 관찰 지평을 넉넉히 하기 위한 후속 활동 (a1 이 오래 남음)
    events.append(ev("a1", "open", 9, group="A"))
    return sorted(events, key=lambda e: (e.ts, e.user))


# ---------------------------------------------------------------- 군 배정

def test_assign_groups_uses_earliest_label_and_counts_conflicts():
    events = [
        ev("u1", "open", 0, group="A"),
        ev("u1", "open", 1, group="B"),   # 나중에 다른 라벨 → 충돌
        ev("u2", "open", 0, group="B"),
        ev("u3", "open", 0),              # 라벨 없음
    ]
    mapping, ungrouped, conflicting = assign_groups(events)
    assert mapping == {"u1": "A", "u2": "B"}
    assert ungrouped == 1
    assert conflicting == 1


def test_assign_groups_ignores_event_order_in_input():
    """입력이 시간 역순이어도 '가장 이른 이벤트' 라벨을 골라야 한다."""
    events = [ev("u1", "open", 5, group="B"), ev("u1", "open", 0, group="A")]
    mapping, _, conflicting = assign_groups(events)
    assert mapping["u1"] == "A"
    assert conflicting == 1


def test_user_with_partial_labels_is_still_grouped():
    events = [ev("u1", "open", 0, group=None), ev("u1", "open", 1, group="A")]
    mapping, ungrouped, conflicting = assign_groups(events)
    assert mapping == {"u1": "A"}
    assert ungrouped == 0 and conflicting == 0


# ---------------------------------------------------------------- 비율 비교

def test_retention_proportion_test_matches_hand_counts_and_fisher():
    g = compare_groups(two_arm_events(), retention_days=[1], reference="B")
    assert g.groups == ["A", "B"]
    assert g.compare_a == "A" and g.compare_b == "B"
    t = next(t for t in g.proportions if t.label == "day-1 리텐션")
    assert (t.successes_a, t.n_a) == (3, 4)
    assert (t.successes_b, t.n_b) == (1, 4)
    assert t.diff.diff == pytest.approx(0.5)
    assert t.p_value == pytest.approx(fisher_exact_two_sided(3, 1, 1, 3))
    assert t.p_value == pytest.approx(0.4857142857142857)


def test_reference_group_flips_the_sign_of_the_difference():
    a = compare_groups(two_arm_events(), retention_days=[1], reference="B")
    b = compare_groups(two_arm_events(), retention_days=[1], reference="A")
    ta = next(t for t in a.proportions if t.label == "day-1 리텐션")
    tb = next(t for t in b.proportions if t.label == "day-1 리텐션")
    assert ta.diff.diff == pytest.approx(-tb.diff.diff)
    assert ta.group_b == "B" and tb.group_b == "A"


def test_default_reference_is_first_label_alphabetically():
    g = compare_groups(two_arm_events(), retention_days=[1])
    assert g.reference == "A"
    assert g.compare_a == "B" and g.compare_b == "A"


def test_unknown_reference_group_raises():
    with pytest.raises(ValueError, match="기준군"):
        compare_groups(two_arm_events(), reference="없는군")


def test_retention_horizon_is_global_not_per_arm():
    """한 군만 늦게까지 활동해도 상대 군의 eligible 이 줄지 않아야 한다.

    군별 max_day 를 쓰면 A군(마지막 day-9)의 eligible 만 넓어져 비교가 불공정해진다.
    """
    events = two_arm_events()
    g = compare_groups(events, retention_days=[7])
    arm_a = next(a for a in g.arms if a.group == "A")
    arm_b = next(a for a in g.arms if a.group == "B")
    # 전체 max_day = day 9 → 두 군 모두 day-7 관찰 기회가 있다 (첫 활성일 day 0)
    assert arm_a.retention[7][1] == 4
    assert arm_b.retention[7][1] == 4


def test_funnel_completion_compared_between_arms():
    events = []
    for u in ["a1", "a2"]:                    # A군 2명 모두 완주
        events += [ev(u, "start", 0, hour=9, group="A"),
                   ev(u, "done", 0, hour=10, group="A")]
    for u in ["b1", "b2"]:                    # B군 2명 중 b1 만 완주
        events.append(ev(u, "start", 0, hour=9, group="B"))
    events.append(ev("b1", "done", 0, hour=10, group="B"))
    events.append(ev("a1", "start", 3, group="A"))  # 관찰 기간 확보
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)),
                       retention_days=[1], funnel_steps=["start", "done"],
                       reference="B")
    t = next(t for t in g.proportions if t.label.startswith("퍼널 완주"))
    assert (t.successes_a, t.n_a) == (2, 2)
    assert (t.successes_b, t.n_b) == (1, 2)


# ---------------------------------------------------------------- 분포 비교

def test_distribution_tests_use_user_level_values():
    events = []
    for u in ["a1", "a2", "a3"]:
        for k in range(5):                      # 사용자당 5 이벤트
            events.append(ev(u, "open", k, group="A"))
    for u in ["b1", "b2", "b3"]:
        events.append(ev(u, "open", 0, group="B"))  # 사용자당 1 이벤트
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    t = next(t for t in g.distributions if t.label == "사용자당 이벤트 수")
    assert t.result.n1 == 3 and t.result.n2 == 3      # 이벤트가 아니라 사용자 수
    assert {t.result.median1, t.result.median2} == {5.0, 1.0}
    assert abs(t.result.rank_biserial) == 1.0          # 완전 분리


def test_arm_summary_medians_are_per_user():
    events = []
    for u in ["a1", "a2"]:
        for k in range(4):
            events.append(ev(u, "open", k, group="A"))
    events.append(ev("b1", "open", 0, group="B"))
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    arm_a = next(a for a in g.arms if a.group == "A")
    assert arm_a.n_users == 2 and arm_a.n_events == 8
    assert arm_a.median_events_per_user == 4.0
    assert arm_a.median_active_days == 4.0


# ---------------------------------------------------------------- 이탈 생존

def test_churn_survival_definition():
    """시간은 항상 (마지막 활동 - 첫 활동), 사건/절단만 침묵 길이로 갈린다.

    절단 시간에 (관찰종료 - 첫활동) 을 쓰면 "종료일까지 참여했다"는 관찰되지 않은
    주장을 하게 되므로, 두 경우 모두 L-C 를 쓴다.
    """
    # 관찰 종료 day 10. u1 은 day 2 이후 8일 무활동 → 이탈, 시간 = 2-0 = 2
    # u2 는 day 9 까지 활동(1일 무활동 < 7) → 절단, 시간 = 9-0 = 9 (10 이 아님)
    events = [
        ev("u1", "open", 0), ev("u1", "open", 2),
        ev("u2", "open", 0), ev("u2", "open", 9),
        ev("u3", "open", 10),
    ]
    times, observed = churn_survival(events, churn_days=7)
    assert dict(zip(["u1", "u2", "u3"], zip(times, observed))) == {
        "u1": (2.0, True), "u2": (9.0, False), "u3": (0.0, False),
    }


def test_churn_survival_time_never_exceeds_observed_activity_span():
    """절단된 사용자의 시간이 마지막 활동 이후로 늘어나지 않아야 한다."""
    events = [ev("u1", "open", 0), ev("u1", "open", 5), ev("u2", "open", 30)]
    times, observed = churn_survival(events, churn_days=7)
    assert observed == [True, False]
    assert times == [5.0, 0.0]          # u1 의 활동 구간은 5일, u2 는 0일


def test_churn_boundary_is_inclusive_at_exactly_churn_days():
    """침묵이 정확히 churn_days 면 '이탈로 관찰' (>= 경계)."""
    events = [ev("u1", "open", 0), ev("u1", "open", 3), ev("u2", "open", 10)]
    # end = day 10 → u1 의 침묵 = 7일
    _, observed = churn_survival(events, churn_days=7)
    assert observed[0] is True
    _, observed8 = churn_survival(events, churn_days=8)   # 침묵 7일 < 8
    assert observed8[0] is False


def test_churn_survival_uses_supplied_global_end():
    events = [ev("u1", "open", 0), ev("u1", "open", 1)]
    from datetime import date
    times, observed = churn_survival(events, churn_days=3, end=date(2026, 1, 20))
    assert observed == [True]          # 전체 종료일 기준으로는 이탈
    assert times == [1.0]
    times2, observed2 = churn_survival(events, churn_days=3)
    assert observed2 == [False]        # 자기 군만 보면 아직 절단
    assert times2 == [1.0]             # 시간은 어느 쪽이든 L-C 로 동일


def test_compare_groups_passes_global_end_to_survival():
    """군별로 종료일을 따로 쓰면 늦게까지 활동한 군만 절단이 많아져 왜곡된다.

    A군은 day 0~1 만 활동하고 B군이 day 20 까지 활동하면, 전체 종료일(=20) 기준으로
    A군은 전원 이탈로 관찰돼야 한다 (A군만 보면 day1 이 종료일이라 절단됐을 것).
    """
    events = [
        ev("a1", "open", 0, group="A"), ev("a1", "open", 1, group="A"),
        ev("a2", "open", 0, group="A"), ev("a2", "open", 1, group="A"),
        ev("b1", "open", 0, group="B"), ev("b1", "open", 20, group="B"),
        ev("b2", "open", 0, group="B"), ev("b2", "open", 20, group="B"),
    ]
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)),
                       retention_days=[1], churn_days=7)
    assert g.survival.n_churned["A"] == 2      # 전체 종료일 기준: 19일 침묵
    assert g.survival.n_churned["B"] == 0      # 아직 침묵 0일


def test_churn_days_must_be_positive():
    with pytest.raises(ValueError):
        churn_survival([ev("u1", "open", 0)], churn_days=0)


def test_survival_comparison_present_with_two_arms():
    g = compare_groups(two_arm_events(), retention_days=[1], churn_days=3)
    assert g.survival is not None
    assert set(g.survival.curves) <= {"A", "B"}
    assert sum(g.survival.n_churned.values()) > 0


# ---------------------------------------------------------------- 다중비교·경계

def test_holm_adjustment_applied_to_every_test():
    g = compare_groups(two_arm_events(), retention_days=[1, 2], churn_days=3)
    ps = [t.p_adjusted for t in g.proportions] + [t.p_adjusted for t in g.distributions]
    assert all(p is not None for p in ps)
    assert all(t.p_adjusted >= t.p_value - 1e-12 for t in g.proportions)
    assert all(t.p_adjusted >= t.result.p - 1e-12 for t in g.distributions)
    expected = len(g.proportions) + len(g.distributions) + (
        1 if g.survival and g.survival.logrank else 0
    )
    assert g.n_tests == expected


def test_three_arms_gives_descriptives_only_with_note():
    events = [ev(f"u{i}", "open", 0, group=g) for i, g in enumerate("AABBCC")]
    events += [ev(f"u{i}", "open", 1, group=g) for i, g in enumerate("AABBCC")]
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    assert len(g.arms) == 3
    assert g.proportions == [] and g.distributions == [] and g.survival is None
    assert g.n_tests == 0
    assert any("검정은 생략" in n for n in g.notes)


def test_single_arm_and_no_arm_notes():
    one = compare_groups([ev("u1", "open", 0, group="A"), ev("u1", "open", 1, group="A")],
                         retention_days=[1])
    assert any("1개뿐" in n for n in one.notes)
    none = compare_groups([ev("u1", "open", 0), ev("u1", "open", 1)], retention_days=[1])
    assert none.groups == []
    assert any("군 라벨이 있는 사용자가 없습니다" in n for n in none.notes)


def test_ungrouped_users_excluded_from_arms_and_reported():
    events = two_arm_events() + [ev("x1", "open", 0), ev("x1", "open", 1)]
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    assert g.ungrouped_users == 1
    assert sum(a.n_users for a in g.arms) == 8
    assert any("제외" in n for n in g.notes)


def test_empty_events_raises():
    with pytest.raises(ValueError):
        compare_groups([])


# ---------------------------------------------------------------- 통합(analyze)

def test_analyze_includes_groups_only_when_requested():
    events = two_arm_events()
    assert analyze(events, retention_days=[1]).groups is None
    a = analyze(events, retention_days=[1], group_col="arm", reference_group="B")
    assert a.groups is not None and a.groups.group_col == "arm"


def test_to_dict_group_payload_is_json_serializable():
    import json
    a = analyze(two_arm_events(), retention_days=[1], funnel_steps=["open"],
                group_col="arm", churn_days=3)
    payload = json.loads(json.dumps(to_dict(a), ensure_ascii=False))
    g = payload["groups"]
    assert g["groups"] == ["A", "B"]
    assert len(g["arms"]) == 2
    assert g["proportion_tests"][0]["p_holm"] is not None
    assert g["survival"]["curves"]


def test_csv_tables_include_group_tables():
    import csv
    import io
    a = analyze(two_arm_events(), retention_days=[1], group_col="arm", churn_days=3)
    tables = to_csv_tables(a)
    assert {"group_summary", "group_tests", "group_survival_km"} <= set(tables)
    rows = list(csv.reader(io.StringIO(tables["group_tests"])))
    assert rows[0][0] == "kind"
    assert any(r[0] == "proportion" for r in rows[1:])
    assert all(len(r) == len(rows[0]) for r in rows)


def test_csv_tables_have_no_group_tables_without_group_col():
    a = analyze(two_arm_events(), retention_days=[1])
    assert "group_summary" not in to_csv_tables(a)
