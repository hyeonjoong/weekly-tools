"""KoBio 실제 패킷 회귀 — 이 툴의 척추. 정답: 치명 3 · 경고 2 · 일치 4 (항목 기준).

픽스처는 실제 승인 패킷에서 추출된 문장만 익명화해 옮긴 것입니다 (tests/fixtures/kobio/):
이름·기관·전화·제목은 전부 합성이고, 실측표에 없던 '성인 대조군 40명'은 30+20+40=90 을 맞추기 위해 넣었습니다.
"""
import os

import pytest

from irbpack.model import CRITICAL, MATCH, WARNING
from tests.conftest import by_sev, run

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "kobio")


@pytest.fixture(scope="module")
def kobio():
    res, fatal = run(FIX)
    assert fatal == [], fatal
    return res


def test_exit_is_1(kobio):
    assert kobio.exit_code == 1


def test_roles_auto_detected(kobio):
    labels = {d.label for d in kobio.docs}
    assert labels == {"프로토콜", "동의서(성인)", "동의서(보호자)", "동의서(소아)", "CRF", "모집공고"}
    assert kobio.coverage.forced_roles == 0


def test_critical_items_exactly(kobio):
    assert sorted(i.item for i in by_sev(kobio, CRITICAL)) == ["compensation", "duration", "visits"]


def test_visits_three_way(kobio):
    [c] = [i for i in by_sev(kobio, CRITICAL) if i.item == "visits"]
    vals = {e.label: (e.value, e.differs) for e in c.evidence}
    assert "2회" in vals["프로토콜"][0] and vals["프로토콜"][1] is False
    assert "1회 또는 다회기" in vals["동의서(보호자)"][0] and vals["동의서(보호자)"][1]
    assert "1~6" in vals["CRF"][0] and vals["CRF"][1]
    assert "2회" in vals["동의서(성인)"][0] and vals["동의서(성인)"][1] is False


def test_duration_two_icfs(kobio):
    [c] = [i for i in by_sev(kobio, CRITICAL) if i.item == "duration"]
    vals = {e.label: e.value for e in c.evidence}
    assert '"90분", "120분"' == vals["동의서(성인)"]
    assert '"60분", "180분"' == vals["동의서(보호자)"]
    assert '"30분", "1시간"' == vals["동의서(소아)"]


def test_compensation_category(kobio):
    [c] = [i for i in by_sev(kobio, CRITICAL) if i.item == "compensation"]
    vals = {e.label: e.value for e in c.evidence}
    assert vals["프로토콜"] == '"실비"' and "10,000" in vals["동의서(성인)"] and "시간당" in vals["동의서(성인)"]


def test_warning_items_exactly(kobio):
    assert sorted(i.item for i in by_sev(kobio, WARNING)) == ["contact", "n"]


def test_ad_mobile_masked(kobio):
    [w] = [i for i in by_sev(kobio, WARNING) if i.item == "contact"]
    assert "개인 휴대전화" in w.title and w.evidence[0].value == '"010-****-5678"'
    assert "1234" not in w.evidence[0].value


def test_total_n_missing_in_ad(kobio):
    [w] = [i for i in by_sev(kobio, WARNING) if i.item == "n"]
    assert "전파 누락" in w.title and "모집공고" in w.title and "90" in w.title


@pytest.mark.parametrize("item", ["title", "age", "retention", "version"])
def test_matched_items_zero_findings(kobio, item):
    assert [i for i in kobio.issues if i.item == item and i.severity in (CRITICAL, WARNING)] == []
    assert [i for i in kobio.issues if i.item == item and i.severity == MATCH]


def test_age_values(kobio):
    ages = [i for i in kobio.issues if i.item == "age" and i.severity == MATCH]
    assert ages and all('"만 5~12세", "만 19~45세"' == e.value for e in ages[0].evidence if e.label != "모집공고")


def test_coverage_counts(kobio):
    cov = kobio.coverage
    assert cov.n_docs == 6 and cov.n_read == 6 and cov.unread == []
    assert len(cov.items_compared) >= 9
    names = [n for n, _ in cov.items_uncomparable]
    assert "선정·제외기준" in names


def test_no_more_findings_than_truth(kobio):
    """규칙을 넓히다가 늘어나면 실패해야 한다 — 늘리지 말고 좁힐 것."""
    assert len(by_sev(kobio, CRITICAL)) == 3 and len(by_sev(kobio, WARNING)) == 2
