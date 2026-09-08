"""음성 대조 — 일부러 어긋뜨린 패킷을 **정확히 그 항목·그 심각도**로 잡는다. 그리고 맞는 건 조용하다."""
import pytest

from irbpack.model import CRITICAL, MATCH, WARNING
from tests.conftest import (by_sev, full_packet, items_of, md_ad, md_crf, md_icf, md_protocol, run, write_packet)


def _issues(res, sev, item):
    return [i for i in res.issues if i.severity == sev and i.item == item]


def test_consistent_packet_is_quiet(tmp_path):
    res, fatal = run(full_packet(tmp_path))
    assert fatal == []
    assert by_sev(res, CRITICAL) == [] and by_sev(res, WARNING) == []
    matched = {i.item for i in by_sev(res, MATCH)}
    assert {"title", "version", "n", "age", "visits", "duration", "compensation", "retention", "contact"} <= matched
    assert res.exit_code == 0


def test_age_off_by_one_is_critical_age_only(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(age="만 20~45세")))
    assert items_of(by_sev(res, CRITICAL)) == ["age"]
    assert by_sev(res, WARNING) == []


def test_age_alternative_notation_is_not_flagged(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(age="만 19-45세"), ad=md_ad(age="만 １９∼４５세")))
    assert _issues(res, CRITICAL, "age") == [] and _issues(res, MATCH, "age")
    assert res.coverage.norm_equal_pairs >= 1


def test_compensation_amount_differs_is_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(comp="회당 3만원의 사례비를 지급"), icf=md_icf(comp="회당 5만원의 사례비")))
    assert "compensation" in items_of(by_sev(res, CRITICAL))
    assert items_of(by_sev(res, CRITICAL)) == ["compensation"]


def test_compensation_category_differs_is_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(comp="방문별 시간당 10,000원(교통비 및 참여 사례비 포함)")))
    assert items_of(by_sev(res, CRITICAL)) == ["compensation"]


def test_compensation_same_amount_different_notation_quiet(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(comp="회당 1만원 지급"), icf=md_icf(comp="회당 10,000원"), ad=md_ad(comp="회당 10,000 원")))
    assert _issues(res, CRITICAL, "compensation") == []


def test_assent_only_old_version_is_critical_version(tmp_path):
    old = md_icf(kind="소아용", ver="1.1", date="2026-08-11").replace("연구대상자 설명문 및 동의서 (소아용)", "어린이용 승낙서 (Assent)")
    res, _ = run(full_packet(tmp_path, extra_files={"Assent_소아용_v1.1.md": old}))
    crit = by_sev(res, CRITICAL)
    assert items_of(crit) == ["version", "version"]
    assert any("버전" in i.title for i in crit) and any("날짜" in i.title for i in crit)
    stale = [e for i in crit for e in i.evidence if e.differs]
    assert stale and all(e.label == "동의서(소아)" for e in stale)


def test_crf_only_form_is_warning_assessments(tmp_path):
    res, _ = run(full_packet(tmp_path, crf=md_crf(forms=("불면증심각도척도 (ISI)", "수면일지", "삶의질 설문지", "우울척도 (PHQ-9)"))))
    warn = _issues(res, WARNING, "assessments")
    assert len(warn) == 1 and "동의 범위 밖" in warn[0].title and "우울척도" in warn[0].evidence[0].value
    assert by_sev(res, CRITICAL) == []


def test_protocol_only_assessment_is_warning(tmp_path):
    res, _ = run(full_packet(tmp_path, crf=md_crf(forms=("불면증심각도척도 (ISI)", "수면일지"))))
    warn = _issues(res, WARNING, "assessments")
    assert len(warn) == 1 and "전파 누락" in warn[0].title


def test_protocol_only_item_is_uncomparable_not_critical(tmp_path):
    res, _ = run(full_packet(tmp_path))
    names = [n for n, _ in res.coverage.items_uncomparable]
    assert "선정·제외기준" in names
    assert all(i.item != "criteria" for i in by_sev(res, CRITICAL))


def test_visits_three_way_conflict(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(visits="1회 또는 다회기 방문"), crf=md_crf(visits=6)))
    crit = _issues(res, CRITICAL, "visits")
    assert len(crit) == 1
    vals = {e.label: e.value for e in crit[0].evidence}
    assert "1회 또는 다회기" in vals["동의서(성인)"] and "1~6" in vals["CRF"]
    assert [e.differs for e in crit[0].evidence if e.label == "프로토콜"] == [False]


def test_duration_two_icfs_differ(tmp_path):
    guardian = md_icf(kind="보호자용", dur="약 60분, 검사가 길어지면 180분")
    res, _ = run(full_packet(tmp_path, icf=md_icf(dur="약 90분, 검사가 길어지면 120분"), extra_files={"ICF_보호자용_v1.2.md": guardian}))
    crit = _issues(res, CRITICAL, "duration")
    assert len(crit) == 1
    labels = {e.label for e in crit[0].evidence if e.differs}
    assert labels >= {"동의서(보호자)"}


def test_subset_is_warning_not_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, ad=md_ad(n="시험군 45명")))
    warn = _issues(res, WARNING, "n")
    assert len(warn) == 1 and "전파 누락" in warn[0].title and "모집공고" in warn[0].title
    assert _issues(res, CRITICAL, "n") == []


def test_absent_value_does_not_warn(tmp_path):
    ad = md_ad().replace("모집 인원: 시험군 45명, 대조군 45명 (총 90명).", "")
    res, _ = run(full_packet(tmp_path, ad=ad))
    assert _issues(res, WARNING, "n") == [] and _issues(res, MATCH, "n")


def test_ad_personal_mobile_is_warning(tmp_path):
    res, _ = run(full_packet(tmp_path, ad=md_ad(contact="010-1234-5678")))
    warn = _issues(res, WARNING, "contact")
    assert len(warn) == 1 and "개인 휴대전화" in warn[0].title
    assert warn[0].evidence[0].value == '"010-****-5678"'


def test_ad_same_office_number_is_quiet(tmp_path):
    res, _ = run(full_packet(tmp_path, ad=md_ad(contact="031-000-0000")))
    assert _issues(res, WARNING, "contact") == [] and _issues(res, MATCH, "contact")


def test_ad_mobile_present_in_icf_is_not_flagged(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(contact="010-1234-5678"), ad=md_ad(contact="010-1234-5678")))
    assert not any("개인 휴대전화" in i.title for i in _issues(res, WARNING, "contact"))


def test_contacts_disjoint_is_warning(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(contact="02-111-1111"), icf=md_icf(contact="02-222-2222"), ad=md_ad(contact="02-333-3333")))
    warn = _issues(res, WARNING, "contact")
    assert len(warn) == 1 and "겹치는 값이 하나도 없음" in warn[0].title


def test_retention_units_equal(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(keep="36개월")))
    assert _issues(res, CRITICAL, "retention") == [] and _issues(res, MATCH, "retention")


def test_retention_differs(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(keep="5년")))
    assert items_of(by_sev(res, CRITICAL)) == ["retention"]


def test_title_differs_is_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(title="성인 불면증 환자 대상 소리 기반 수면 앱의 안전성 연구")))
    assert items_of(by_sev(res, CRITICAL)) == ["title"]


def test_title_punctuation_variant_quiet(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(title="성인 불면증 환자 대상, 소리 기반 수면 앱의 유효성 연구.")))
    assert _issues(res, CRITICAL, "title") == []


def test_pi_differs_is_critical(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(pi="이가상")))
    assert items_of(by_sev(res, CRITICAL)) == ["pi"]


def test_criteria_count_differs_is_warning(tmp_path):
    icf = md_icf(extra="## 선정기준\n1) 불면증 진단\n2) 서면 동의\n3) 만 19세 이상\n## 제외기준\n1) 수면제 복용")
    res, _ = run(full_packet(tmp_path, icf=icf))
    warn = _issues(res, WARNING, "criteria")
    assert len(warn) == 1 and "선정기준 개수" in warn[0].title


def test_period_unit_mismatch_is_uncomparable(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(extra="참여 기간은 약 8주입니다."), icf=md_icf(extra="참여 기간은 약 2개월입니다.")))
    assert any("참여기간" in n and "단위" in w for n, w in res.coverage.sub_gaps)
    assert _issues(res, CRITICAL, "visits") == []


def test_period_same_unit_differs(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(extra="참여 기간은 약 8주입니다."), icf=md_icf(extra="참여 기간은 약 6주입니다.")))
    assert any("참여기간" in i.title for i in _issues(res, CRITICAL, "visits"))


def test_only_two_docs_values_disjoint(tmp_path):
    p = write_packet(tmp_path, {"연구계획서.md": md_protocol(visits="3"), "ICF.md": md_icf(visits="총 2회 방문")})
    res, _ = run(p)
    assert "visits" in items_of(by_sev(res, CRITICAL))


def test_evidence_is_masked(tmp_path):
    res, _ = run(full_packet(tmp_path, ad=md_ad(contact="010-9999-8888")))
    for i in res.issues:
        for e in i.evidence:
            assert "9999" not in e.value and "9999" not in e.sentence


def test_no_verdict_about_which_is_right(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(visits="총 3회 방문")))
    text = " ".join(i.title + " " + i.note for i in res.issues)
    for bad in ("이 맞", "가 맞", "틀렸", "오류입니다", "정정하"):
        assert bad not in text


@pytest.mark.parametrize("kw,ad_kw,rule", [
    ({"age": "만 19-45세"}, {"age": "만 １９∼４５세"}, "age"),
    ({"date": "260825"}, {}, "date"),
    ({"ver": "V1.2"}, {}, "version"),
])
def test_norm_equal_pairs_counted_per_rule(tmp_path, kw, ad_kw, rule):
    """정규화 덕에 같다고 본 쌍이 연령·날짜·버전 각각에서 실제로 세어진다 (표시용 raw 가 아니라 원문 토큰으로)."""
    res, _ = run(full_packet(tmp_path, icf=md_icf(**kw), ad=md_ad(**ad_kw)))
    assert res.coverage.norm_equal_pairs >= 1
    assert _issues(res, CRITICAL, rule if rule != "date" else "version") == []


def test_norm_equal_pairs_counts_only_real_notation_differences(tmp_path):
    """기본 패킷에서 표기가 다른데 같다고 본 쌍은 CRF 의 'Visit 1~2 칸' 대 '총 2회 방문' 3쌍뿐이어야 한다.
    'Version' / '버전' / 'Version No' 같은 **라벨** 차이는 정규화 쌍으로 세지 않는다."""
    res, _ = run(full_packet(tmp_path))
    assert res.coverage.norm_equal_pairs == 3


def test_assessments_without_crf_is_confessed(tmp_path):
    res, _ = run(full_packet(tmp_path, drop=("CRF_v1.2.md",)))
    assert any(n == "평가·검사 항목" and "CRF 문서 없음" in w for n, w in res.coverage.items_uncomparable)
    assert "평가·검사 항목" not in res.coverage.items_compared


def test_two_docs_with_different_subsets_of_protocol_is_not_conflict(tmp_path):
    """프로토콜 {90,45} · 동의서 {90} · 공고 {45} — 충돌이 아니라 전파 누락."""
    res, _ = run(full_packet(tmp_path, icf=md_icf(n="총 90명"), ad=md_ad(n="각 군 45명")))
    assert _issues(res, CRITICAL, "n") == []
    assert len(_issues(res, WARNING, "n")) == 1


def test_email_disjoint_is_not_warning(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(extra="연락처 pi@example.org"), icf=md_icf(extra="IRB 사무국 irb@example.org")))
    assert not any("이메일" in i.title for i in _issues(res, WARNING, "contact"))


def test_insurance_limit_does_not_become_compensation(tmp_path):
    icf = md_icf(extra="연구 관련 손상 시 보상을 위해 임상시험보험에 가입하였으며 보상 한도는 1인당 100,000,000원입니다.")
    res, _ = run(full_packet(tmp_path, icf=icf))
    assert [i for i in res.issues if i.item == "compensation" and i.severity != MATCH] == []


def test_pi_mention_with_particle_does_not_warn(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(extra="궁금한 점이 있으면 연구책임자에게 문의하십시오.")))
    assert [i for i in res.issues if i.item == "pi" and i.severity != MATCH] == []


def test_revision_history_row_does_not_conflict(tmp_path):
    icf = md_icf().replace("| 버전 | 1.2 |", "| 개정 이력 | 버전 1.0 (2026.05.18) 최초 작성; 버전 1.2 (2026.08.25) 개정 |\n| 버전 | 1.2 |")
    res, _ = run(full_packet(tmp_path, icf=icf))
    assert _issues(res, CRITICAL, "version") == []


def test_study_period_vs_participation_period_not_conflict(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(extra="총 연구 기간은 IRB 승인 후 2년이다."), icf=md_icf(extra="귀하의 참여 기간은 약 1년입니다.")))
    assert _issues(res, CRITICAL, "visits") == []


def test_compensation_none_vs_amount_is_conflict(tmp_path):
    res, _ = run(full_packet(tmp_path, protocol=md_protocol(comp="금전적 보상은 없으며 교통비만 지급"), icf=md_icf(comp="회당 3만원의 사례비"), ad=md_ad(comp="회당 3만원의 사례비")))
    assert _issues(res, CRITICAL, "compensation")


def test_age_exclusive_upper_bound_differs(tmp_path):
    res, _ = run(full_packet(tmp_path, icf=md_icf(age="만 19세 이상 45세 미만")))
    assert items_of(by_sev(res, CRITICAL)) == ["age"]


def test_same_basename_in_two_folders_are_separate_docs(tmp_path):
    a = tmp_path / "v1"
    b = tmp_path / "v2"
    a.mkdir(); b.mkdir()
    (a / "동의서.md").write_text(md_icf(visits="총 2회 방문"), encoding="utf-8")
    (b / "동의서.md").write_text(md_icf(visits="총 3회 방문"), encoding="utf-8")
    (b / "연구계획서.md").write_text(md_protocol(), encoding="utf-8")
    from irbpack import cli, safeio
    files = [str(a / "동의서.md"), str(b / "동의서.md"), str(b / "연구계획서.md")]
    safeio.clear_protected(); safeio.protect_inputs(files)
    res, fatal = cli.run_packet(files, [], None)
    assert fatal == [] and "visits" in items_of(by_sev(res, CRITICAL))
    assert len({d.name for d in res.docs}) == 3
