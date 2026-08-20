"""선정/제외기준 재점검 — 없는 열은 위반이 아니라 판정불가."""

from tests.conftest import crit, mini_protocol, subj
from visitaudit.criteria import recheck


def _proto(incl=None, excl=None):
    return mini_protocol(incl=incl, excl=excl)


def test_numeric_ge_violation():
    proto = _proto(incl=[crit("ISI_baseline", ">=", 15)])
    res = recheck([subj("S01", ISI_baseline=13)], proto)
    assert len(res.violations) == 1
    assert res.violations[0].subject == "S01"
    assert "13" in res.violations[0].detail


def test_numeric_ge_pass():
    proto = _proto(incl=[crit("ISI_baseline", ">=", 15)])
    res = recheck([subj("S01", ISI_baseline=15)], proto)   # 경계값 포함
    assert res.violations == []
    assert res.n_checked == 1


def test_numeric_le_and_lt():
    proto = _proto(incl=[crit("age", "<=", 65), crit("age", "<", 66)])
    res = recheck([subj("S01", age=66)], proto)
    assert len(res.violations) == 2


def test_string_eq_exclusion_met_is_violation():
    proto = _proto(excl=[crit("OSA진단", "==", "Y", kind="제외")])
    res = recheck([subj("S01", OSA진단="Y")], proto)
    assert len(res.violations) == 1


def test_string_eq_exclusion_not_met():
    proto = _proto(excl=[crit("OSA진단", "==", "Y", kind="제외")])
    res = recheck([subj("S01", OSA진단="N")], proto)
    assert res.violations == []


def test_inclusion_ne():
    proto = _proto(incl=[crit("임신여부", "!=", "Y")])
    res = recheck([subj("S01", 임신여부="Y")], proto)
    assert len(res.violations) == 1


def test_missing_column_is_not_violation():
    proto = _proto(incl=[crit("없는항목", ">=", 1)])
    res = recheck([subj("S01", age=30)], proto)
    assert res.violations == []
    assert res.missing_columns == ["없는항목"]


def test_empty_value_unjudgeable():
    proto = _proto(incl=[crit("ISI_baseline", ">=", 15)])
    res = recheck([subj("S01", ISI_baseline="")], proto)
    assert res.violations == []
    assert len(res.unjudgeable) == 1
    assert "값 없음" in res.unjudgeable[0].detail


def test_unparseable_numeric_unjudgeable():
    proto = _proto(incl=[crit("ISI_baseline", ">=", 15)])
    res = recheck([subj("S01", ISI_baseline="중간쯤")], proto)
    assert res.violations == []
    assert "해석 불가" in res.unjudgeable[0].detail


def test_string_with_order_op_unjudgeable():
    # 문자열 값에 >= 는 판정하지 않는다 (사전순 비교는 함정)
    proto = _proto(incl=[crit("등급", ">=", "B")])
    res = recheck([subj("S01", 등급="A")], proto)
    assert res.violations == []
    assert len(res.unjudgeable) == 1


def test_only_randomized_checked():
    proto = _proto(incl=[crit("age", ">=", 19)])
    res = recheck([subj("S01", age=17, arm=""),      # 스크린 실패 — 점검 대상 아님
                   subj("S02", age=17)], proto)      # 무작위배정 — 위반
    assert [f.subject for f in res.violations] == ["S02"]


def test_skipped_without_subjects():
    proto = _proto(incl=[crit("age", ">=", 19)])
    res = recheck(None, proto)
    assert res.skipped == "피험자.csv 없음"


def test_skipped_without_criteria():
    res = recheck([subj("S01")], _proto())
    assert "기준이 없음" in res.skipped


def test_violators_dedup():
    proto = _proto(incl=[crit("age", ">=", 19), crit("ISI_baseline", ">=", 15)])
    res = recheck([subj("S01", age=17, ISI_baseline=10)], proto)
    assert len(res.violations) == 2
    assert res.violators() == ["S01"]      # 사람 단위로는 1명
