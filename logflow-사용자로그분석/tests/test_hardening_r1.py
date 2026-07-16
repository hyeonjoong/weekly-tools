"""R1 적대적 리뷰(정확성·엣지·문서·테스트/보안 4패널)에서 나온 회귀·불변식 테스트."""

from datetime import datetime, timedelta, timezone

import pytest

from logflow.analyze import analyze, to_dict
from logflow.dataio import Event, parse_timestamp
from logflow.metrics import (
    active_users,
    activity_by_hour,
    activity_by_weekday,
    funnel,
    retention,
    stickiness,
)
from logflow.report import build_report, render_text
from logflow.sessionize import sessionize
from logflow.stats import describe, wilson_interval


def _ev(u, n, day=0, hour=0):
    # 2026-01-05 = 월요일
    return Event(u, n, datetime(2026, 1, 5) + timedelta(days=day, hours=hour))


SAMPLE = [
    _ev(f"u{u}", n, day=d, hour=h)
    for u in range(4)
    for d in range(6)
    for h, n in [(9, "open"), (10, "start"), (11, "buy")]
]


# ---- 오버플로/극단 입력 (correctness+edge 패널) ----

def test_retention_huge_n_no_overflow():
    evs = [_ev("u1", "a", 0), _ev("u1", "a", 1)]
    # 예전에는 date 연산 오버플로로 크래시. 이제는 조용히 eligible=0.
    r = {x.n: x for x in retention(evs, days=[9999999999])}[9999999999]
    assert r.eligible == 0 and r.rate is None and r.ci is None


def test_retention_still_rejects_non_positive():
    evs = [_ev("u1", "a", 0), _ev("u1", "a", 1)]
    with pytest.raises(ValueError):
        retention(evs, days=[0])


def test_negative_epoch_rejected_not_silently_1938():
    # 부호 붙은 값은 epoch 로 보지 않고 ISO 파싱 실패 → 명확한 오류
    with pytest.raises(ValueError):
        parse_timestamp("-1000000000")
    with pytest.raises(ValueError):
        parse_timestamp("+1000000000")


def test_epoch_second_millisecond_boundary():
    # 초/밀리초 경계 (1e11) 근처 의미 고정
    assert parse_timestamp("1609459200") == datetime(2021, 1, 1)
    assert parse_timestamp("1609459200000") == datetime(2021, 1, 1)  # ms 경로
    # 경계 1e11: 미만은 '초', 이상은 '밀리초'로 해석 (회귀 방지)
    def _utc(sec):
        return datetime.fromtimestamp(sec, tz=timezone.utc).replace(tzinfo=None)
    assert parse_timestamp("99999999999") == _utc(99999999999)        # 초
    assert parse_timestamp("100000000000") == _utc(100000000000 / 1000)  # ms


# ---- analyze / to_dict 직접 커버리지 (test 패널) ----

def test_analyze_empty_raises_clean():
    with pytest.raises(ValueError):
        analyze([])


def test_build_report_equals_render_text():
    # build_report 는 analyze+render_text 와 동일해야 한다 (단일 진실 원천)
    r1 = build_report(SAMPLE, funnel_steps=["open", "start", "buy"])
    r2 = render_text(analyze(SAMPLE, funnel_steps=["open", "start", "buy"]))
    assert r1 == r2


def test_to_dict_overview_consistency():
    d = to_dict(analyze(SAMPLE, funnel_steps=["open", "start", "buy"]))
    assert d["overview"]["total_events"] == len(SAMPLE)
    assert d["overview"]["unique_users"] == len(d["users"])
    assert sum(d["activity"]["by_hour"]) == len(SAMPLE)
    assert sum(d["activity"]["by_weekday"]) == len(SAMPLE)
    # JSON 직렬화 가능해야 한다 (raw 값 남지 않도록)
    import json
    json.dumps(d, ensure_ascii=False)


# ---- 불변식/속성 테스트 (test 패널) ----

def test_hour_and_weekday_histograms_sum_to_event_count():
    assert sum(activity_by_hour(SAMPLE)) == len(SAMPLE)
    assert sum(activity_by_weekday(SAMPLE)) == len(SAMPLE)


def test_sessionize_conserves_events_and_orders_times():
    ss = sessionize(SAMPLE)
    assert sum(s.event_count for s in ss) == len(SAMPLE)
    assert all(s.end >= s.start for s in ss)


def test_dau_le_wau_le_mau_every_day():
    for a in active_users(SAMPLE):
        assert a.dau <= a.wau <= a.mau


def test_retention_counts_and_rates_bounded():
    for r in retention(SAMPLE, days=[1, 3, 5]):
        assert r.retained <= r.eligible
        if r.rate is not None:
            assert 0.0 <= r.rate <= 1.0
            lo, hi = r.ci
            assert lo <= r.rate <= hi


def test_funnel_reached_monotonic_and_conversions_in_unit():
    steps = funnel(SAMPLE, ["open", "start", "buy"])
    reached = [s.reached for s in steps]
    assert reached == sorted(reached, reverse=True)  # 비증가
    for s in steps:
        for c in (s.step_conversion, s.overall_conversion):
            if c is not None:
                assert 0.0 <= c <= 1.0


def test_describe_quartiles_monotone_and_mean_bounded():
    d = describe([5, 1, 9, 3, 7, 2])
    assert d["min"] <= d["p25"] <= d["median"] <= d["p75"] <= d["p90"] <= d["max"]
    assert d["min"] <= d["mean"] <= d["max"]


@pytest.mark.parametrize("k,n", [(0, 5), (5, 5), (1, 3), (37, 40), (0, 1), (1, 1)])
def test_wilson_always_brackets_and_in_unit(k, n):
    lo, hi = wilson_interval(k, n)
    assert 0.0 <= lo <= k / n <= hi <= 1.0


def test_stickiness_empty_and_zero_mau_none():
    assert stickiness([]) is None
