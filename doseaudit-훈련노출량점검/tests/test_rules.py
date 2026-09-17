"""선언받는 정의들 — 미선언이면 **판정하지 않는다**."""

import datetime

import pytest

from doseaudit.errors import EXIT_REFUSE, RefuseError
from doseaudit.rules import CONTRAST_RULES, ActiveDayRule, Window


class _Row(object):
    def __init__(self, seconds):
        self.seconds = seconds


def bucket(**modules):
    return {name: [_Row(s) for s in seconds] for name, seconds in modules.items()}


@pytest.mark.parametrize("spec,kind,n", [
    ("any-row", "any-row", 0.0), ("min-rows=1", "min-rows", 1.0),
    ("min-rows=5", "min-rows", 5.0), ("min-modules=2", "min-modules", 2.0),
    ("min-seconds=60", "min-seconds", 60.0), ("min-seconds=0.5", "min-seconds", 0.5),
    ("  any-row  ", "any-row", 0.0), ("min-rows=100", "min-rows", 100.0),
])
def test_규칙을_해석한다(spec, kind, n):
    rule = ActiveDayRule.parse(spec)
    assert (rule.kind, rule.n) == (kind, n)


@pytest.mark.parametrize("spec", [
    None, "", "anyrow", "min-rows", "min-rows=", "min-rows=0", "min-rows=-1",
    "min_rows=5", "min-days=5", "any", "min-seconds=0", "min-modules=abc",
    "any-row=1", "ANY-ROW",
])
def test_해석_못_하면_판정하지_않는다(spec):
    with pytest.raises(RefuseError) as info:
        ActiveDayRule.parse(spec)
    assert info.value.exit_code == EXIT_REFUSE


def test_미선언_메시지가_고를_수_있는_규칙을_알려준다():
    with pytest.raises(RefuseError) as info:
        ActiveDayRule.parse(None)
    text = str(info.value)
    for spec in ("any-row", "min-rows=N", "min-modules=K", "min-seconds=S", "--inspect"):
        assert spec in text


def test_any_row():
    rule = ActiveDayRule.parse("any-row")
    assert rule.is_active(bucket(음소쌍=[0])) is True
    assert rule.is_active(bucket(음소쌍=[])) is False
    assert rule.is_active({}) is False


@pytest.mark.parametrize("n_rows,expected", [(0, False), (4, False), (5, True), (9, True)])
def test_min_rows(n_rows, expected):
    rule = ActiveDayRule.parse("min-rows=5")
    assert rule.is_active(bucket(음소쌍=[1] * n_rows)) is expected


@pytest.mark.parametrize("n_modules,expected", [(0, False), (1, False), (2, True), (3, True)])
def test_min_modules(n_modules, expected):
    rule = ActiveDayRule.parse("min-modules=2")
    data = {"모듈%d" % i: [_Row(1)] for i in range(n_modules)}
    assert rule.is_active(data) is expected


def test_min_modules_는_빈_모듈을_세지_않는다():
    rule = ActiveDayRule.parse("min-modules=2")
    assert rule.is_active({"A": [_Row(1)], "B": []}) is False


@pytest.mark.parametrize("seconds,expected", [
    ([], False), ([59], False), ([60], True), ([30, 30], True), ([0, 0, 0], False),
])
def test_min_seconds(seconds, expected):
    rule = ActiveDayRule.parse("min-seconds=60")
    assert rule.is_active(bucket(음소쌍=seconds)) is expected


def test_min_seconds_는_해석못한_값을_0으로_세지_않는다():
    """`None` 을 0 으로 더하면 '짧게 했다'가 되고, 빼면 '모른다'가 된다."""
    rule = ActiveDayRule.parse("min-seconds=60")
    assert rule.is_active(bucket(음소쌍=[None, None, 70])) is True
    assert rule.is_active(bucket(음소쌍=[None, None, 50])) is False


@pytest.mark.parametrize("spec", CONTRAST_RULES)
def test_대조규칙들은_전부_해석된다(spec):
    assert ActiveDayRule.parse(spec).label()


@pytest.mark.parametrize("spec", CONTRAST_RULES)
def test_규칙마다_한국어와_영어_라벨이_있다(spec):
    rule = ActiveDayRule.parse(spec)
    assert rule.label() and rule.label_en()
    assert rule.label() != rule.label_en()


def test_window_미선언은_거절():
    with pytest.raises(RefuseError):
        Window.parse(None, None)


def test_enroll_to_cut_에는_cut_이_필요하다():
    with pytest.raises(RefuseError) as info:
        Window.parse("enroll-to-cut", None)
    assert "--cut" in str(info.value)


@pytest.mark.parametrize("cut", ["2026/06/23", "23-06-2026", "어제", "2026-13-01", "x"])
def test_cut_형식이_틀리면_거절(cut):
    with pytest.raises(RefuseError):
        Window.parse("enroll-to-cut", cut)


@pytest.mark.parametrize("spec", ["enroll-to-last", "fixed-weeks=8", "fixed-weeks=12.0",
                                  "fixed-weeks=1.5" if False else "fixed-weeks=2"])
def test_다른_창들(spec):
    assert Window.parse(spec, None).spec == spec


@pytest.mark.parametrize("spec", ["fixed-weeks=1.5", "fixed-weeks=12.5", "fixed-weeks=0.1"])
def test_하루로_떨어지지_않는_주수는_거절(spec):
    """조용히 잘라서 선언한 것보다 짧은 분모를 쓰느니 받지 않는다."""
    with pytest.raises(RefuseError) as info:
        Window.parse(spec, None)
    assert "하루 단위로 떨어지지 않습니다" in str(info.value)


@pytest.mark.parametrize("spec", ["fixed-weeks=0", "fixed-weeks=-1", "fixed", "weeks=8"])
def test_잘못된_창은_거절(spec):
    with pytest.raises(RefuseError):
        Window.parse(spec, None)


def test_관찰일수는_양_끝을_포함한다():
    """트래커의 `현재까지 일수` 와 같은 관습 — 가입 1/29, 기준 6/21 → 144일."""
    window = Window.parse("enroll-to-cut", "2026-06-21")
    assert window.observed_days(datetime.date(2026, 1, 29), None) == 144.0


def test_고정주수도_시작일을_모르면_분모를_만들지_않는다():
    """주수만으로 분모를 만들면 시작점 없는 비율이 된다 — 그건 셈이 아니라 추론이다."""
    window = Window.parse("fixed-weeks=8", None)
    assert window.observed_days(None, None) is None
    assert window.bounds(None, None) == (None, None)


def test_고정주수는_가입일부터_센다():
    window = Window.parse("fixed-weeks=8", None)
    assert window.observed_days(datetime.date(2026, 3, 1), None) == 56.0
    assert window.bounds(datetime.date(2026, 3, 1), None) == (
        datetime.date(2026, 3, 1), datetime.date(2026, 4, 25))


@pytest.mark.parametrize("spec,cut,enroll,last,expected", [
    ("enroll-to-cut", "2026-03-31", (2026, 3, 1), None, (2026, 3, 1, 2026, 3, 31)),
    ("enroll-to-last", None, (2026, 3, 1), (2026, 3, 10), (2026, 3, 1, 2026, 3, 10)),
    ("fixed-weeks=2", None, (2026, 3, 1), None, (2026, 3, 1, 2026, 3, 14)),
])
def test_창의_양_끝이_분자와_분모에_같이_쓰인다(spec, cut, enroll, last, expected):
    window = Window.parse(spec, cut)
    start, end = window.bounds(datetime.date(*enroll),
                               datetime.date(*last) if last else None)
    assert (start.year, start.month, start.day, end.year, end.month, end.day) == expected
    assert window.observed_days(datetime.date(*enroll),
                                datetime.date(*last) if last else None) == (end - start).days + 1


def test_가입일이_없으면_관찰일수를_세지_않는다():
    window = Window.parse("enroll-to-cut", "2026-06-21")
    assert window.observed_days(None, None) is None


def test_컷이_가입일보다_앞이면_관찰일수_없음():
    window = Window.parse("enroll-to-cut", "2026-01-01")
    assert window.observed_days(datetime.date(2026, 6, 1), None) is None


def test_enroll_to_last_는_마지막_활동일까지():
    window = Window.parse("enroll-to-last", None)
    assert window.observed_days(datetime.date(2026, 3, 1), datetime.date(2026, 3, 10)) == 10.0


@pytest.mark.parametrize("source", ["tracker", "first-activity"])
def test_창_라벨에_시작일_출처가_박힌다(source):
    window = Window.parse("enroll-to-cut", "2026-06-23")
    assert "2026-06-23" in window.label(source)
    assert window.label_en(source)
