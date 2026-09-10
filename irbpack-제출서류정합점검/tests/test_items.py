"""항목 추출 12종 — 뽑아야 할 것은 뽑고, **뽑으면 안 되는 것은 뽑지 않는다**.

오탐 억제 테스트가 절반 이상인 이유: 이 툴이 죽는 방식은 '못 찾는 것'보다
'아무거나 찾는 것'이다.
"""

import pytest

from conftest import make_docx, write_md, values_of
from irbpack.docread import read_document
from irbpack.items import ITEMS, extract_all, is_mobile


def extract(tmp_path, lines, name="doc.md"):
    return extract_all(read_document(write_md(tmp_path / name, lines)))


# ---------------------------------------------------------------- 1. 연구제목

def test_title_from_label(tmp_path):
    mentions = extract(tmp_path, ["연구제목: 가상 수면음향 연구"])
    assert values_of(mentions, "title") == ["가상수면음향연구"]


def test_title_from_table_row(tmp_path):
    mentions = extract(tmp_path, ["| 연구제목 | (국문) 가상 수면음향 연구 |"])
    assert values_of(mentions, "title") == ["가상수면음향연구"]


def test_title_strips_korean_english_prefix(tmp_path):
    mentions = extract(tmp_path, ["연구과제명: (영문) Sleep Sound Study"])
    assert values_of(mentions, "title") == ["sleepsoundstudy"]


def test_title_ignores_blank_form(tmp_path):
    mentions = extract(tmp_path, ["연구제목: ____________________"])
    assert values_of(mentions, "title") == []


def test_title_ignores_too_short(tmp_path):
    assert values_of(extract(tmp_path, ["연구명: 가나"]), "title") == []


def test_title_from_ad_label(tmp_path):
    mentions = extract(tmp_path, ["임상시험명: 가상 수면음향 연구"])
    assert values_of(mentions, "title") == ["가상수면음향연구"]


# ---------------------------------------------------------------- 2. 버전·날짜

def test_version_from_body(tmp_path):
    mentions = extract(tmp_path, ["Version No: V1.2"])
    assert "1.2" in values_of(mentions, "version", "버전")


def test_version_from_filename(tmp_path):
    mentions = extract(tmp_path, ["본문"], name="ICF_v1.1_260518.md")
    assert "1.1" in values_of(mentions, "version", "버전(파일명)")


def test_version_filename_underscore_date_is_not_version(tmp_path):
    """'v1.0_260518' 은 버전 1.0 이고, 260518 은 날짜다."""
    mentions = extract(tmp_path, ["본문"], name="ICF_v1.0_260518.md")
    assert values_of(mentions, "version", "버전(파일명)") == ["1.0"]
    assert "2026-05-18" in values_of(mentions, "version", "문서날짜(파일명)")


def test_version_filename_underscore_minor(tmp_path):
    mentions = extract(tmp_path, ["본문"], name="계획서_v1_6_수정.md")
    assert "1.6" in values_of(mentions, "version", "버전(파일명)")


def test_version_ignores_blank_form(tmp_path):
    mentions = extract(tmp_path, ["| 동의서 버전 | v ______ |"], name="a.md")
    assert values_of(mentions, "version", "버전") == []


def test_version_table_row(tmp_path):
    mentions = extract(tmp_path, ["| Version | 1.0 |"])
    assert "1.0" in values_of(mentions, "version", "버전")


def test_document_date_korean_format(tmp_path):
    mentions = extract(tmp_path, ["작성일: 2026년 5월 18일"])
    assert "2026-05-18" in values_of(mentions, "version", "문서날짜")


def test_document_date_needs_context(tmp_path):
    """본문 한복판의 6자리 숫자를 날짜로 읽지 않는다."""
    lines = ["기타"] * 70 + ["일련번호 123456 을 부여한다"]
    mentions = extract(tmp_path, lines)
    assert values_of(mentions, "version", "문서날짜") == []


# ---------------------------------------------------------------- 3. 연구책임자·기관

def test_pi_name_with_title(tmp_path):
    mentions = extract(tmp_path, ["연구책임자: 김하늘 교수 (가상병원 수면의학과)"])
    assert values_of(mentions, "pi", "연구책임자") == ["김하늘"]


def test_pi_name_from_table(tmp_path):
    mentions = extract(tmp_path, ["| 연구책임자 | 수면의학과 김하늘 교수 |"])
    assert values_of(mentions, "pi", "연구책임자") == ["김하늘"]


def test_pi_name_nested_label(tmp_path):
    mentions = extract(tmp_path, ["연구책임자: 연구책임자/담당자: 김하늘, 직위: 교수"])
    assert values_of(mentions, "pi", "연구책임자") == ["김하늘"]


def test_pi_ignores_narrative_sentence(tmp_path):
    """'연구책임자의 책임 하에 보관한다' 에서 이름을 만들어내면 안 된다."""
    mentions = extract(tmp_path, ["자료는 연구책임자의 책임 하에 별도 파일로 보관한다."])
    assert values_of(mentions, "pi", "연구책임자") == []


def test_pi_ignores_institution_token(tmp_path):
    mentions = extract(tmp_path, ["연구책임자: 가상대학교병원 이비인후과 김하늘 교수"])
    assert values_of(mentions, "pi", "연구책임자") == ["김하늘"]


def test_pi_ignores_blank_signature_row(tmp_path):
    mentions = extract(tmp_path, ["| 연구책임자 | 성명: ____________ 서명: ____________ |"])
    assert values_of(mentions, "pi", "연구책임자") == []


def test_site_from_label(tmp_path):
    mentions = extract(tmp_path, ["실시기관 명칭: 가상병원 수면의학과"])
    assert values_of(mentions, "pi", "실시기관") == ["가상병원수면의학과"]


# ---------------------------------------------------------------- 4. 연락처

def test_phone_extracted_with_context(tmp_path):
    mentions = extract(tmp_path, ["연구 문의 전화: 02-1234-5678"])
    assert values_of(mentions, "contact", "전화") == ["0212345678"]


def test_phone_needs_contact_context(tmp_path):
    mentions = extract(tmp_path, ["자극음은 022-050 Hz 로 제시한다"])
    assert values_of(mentions, "contact", "전화") == []


def test_email_extracted(tmp_path):
    mentions = extract(tmp_path, ["문의 이메일: irb@example.org"])
    assert values_of(mentions, "contact", "이메일") == ["irb@example.org"]


def test_mobile_detection():
    assert is_mobile("01012345678")
    assert not is_mobile("0212345678")
    assert not is_mobile("")


def test_phone_ignores_citation_block(tmp_path):
    mentions = extract(tmp_path, ["연락처 관련 선행연구 Kim et al. 전화: 02-1234-5678 [12]"])
    assert values_of(mentions, "contact", "전화") == []


# ---------------------------------------------------------------- 5. 대상자 수

def test_total_n(tmp_path):
    mentions = extract(tmp_path, ["본 연구에는 대상자 총 60명이 참여한다."])
    assert values_of(mentions, "subjects", "총N") == ["60"]


def test_group_n(tmp_path):
    mentions = extract(tmp_path, ["중재군 30명, 대조군 30명의 대상자를 모집한다."])
    assert values_of(mentions, "subjects", "군별N") == ["30"]


def test_total_and_group_split(tmp_path):
    mentions = extract(tmp_path, ["| 연구 대상자 수 | 총 60명 (중재군 30명, 대조군 30명) |"])
    assert values_of(mentions, "subjects", "총N") == ["60"]
    assert values_of(mentions, "subjects", "군별N") == ["30"]


def test_citation_counts_are_ignored(tmp_path):
    """'Gifford 등(2008)은 156명을 대상으로' 는 우리 연구의 대상자 수가 아니다."""
    mentions = extract(tmp_path, ["Gifford 등(2008)은 성인 사용자 156명을 대상으로 보고하였다[5]."])
    assert values_of(mentions, "subjects") == []


def test_population_statistics_are_ignored(tmp_path):
    mentions = extract(tmp_path, ["국내 불면증 환자는 약 30,000명으로 추정되며 대상자 모집에 유리하다."])
    assert "30000" not in values_of(mentions, "subjects", "군별N")


def test_subjects_needs_context(tmp_path):
    mentions = extract(tmp_path, ["문항 수는 20명 아닌 20개이다"])
    assert values_of(mentions, "subjects", "군별N") == []


# ---------------------------------------------------------------- 6. 연령

def test_age_range(tmp_path):
    mentions = extract(tmp_path, ["대상자는 만 20~65세 성인이다."])
    assert values_of(mentions, "age") == ["20-65세"]


def test_age_range_hyphen_equals_tilde(tmp_path):
    a = values_of(extract(tmp_path, ["만 5-12세"], "a.md"), "age")
    b = values_of(extract(tmp_path, ["만 5~12세"], "b.md"), "age")
    assert a == b == ["5-12세"]


def test_age_one_year_difference_is_not_equal(tmp_path):
    a = values_of(extract(tmp_path, ["만 20~65세"], "a.md"), "age")
    b = values_of(extract(tmp_path, ["만 20~64세"], "b.md"), "age")
    assert a != b


def test_age_open_ended(tmp_path):
    assert values_of(extract(tmp_path, ["만 19세 이상 성인"]), "age") == ["19+세"]


def test_age_ignores_blank_form(tmp_path):
    assert values_of(extract(tmp_path, ["나이 (만) ______ 세"]), "age") == []


def test_age_rejects_reversed_range(tmp_path):
    assert values_of(extract(tmp_path, ["만 65~20세"]), "age") == []


# ---------------------------------------------------------------- 7. 방문·회기

def test_visit_count_single(tmp_path):
    assert values_of(extract(tmp_path, ["총 3회 방문한다."]), "visits", "방문횟수") == ["3회"]


def test_visit_count_range(tmp_path):
    assert values_of(extract(tmp_path, ["단계별 1~2회 방문"]), "visits", "방문횟수") == ["1-2회"]


def test_visit_multi_session(tmp_path):
    assert "다회기" in values_of(extract(tmp_path, ["소아는 다회기 방문을 허용한다."]), "visits", "방문횟수")


def test_visit_forms_counted_as_sessions(tmp_path):
    lines = ["회기별 방문 기록", "| 회기 | 날짜 |", "| Visit 1 | |", "| Visit 2 | |", "| Visit 3 | |"]
    assert "3회" in values_of(extract(tmp_path, lines), "visits", "방문횟수")


def test_single_visit_form_is_not_a_count(tmp_path):
    lines = ["회기 기록", "| Visit 1 | |"]
    assert values_of(extract(tmp_path, lines), "visits", "방문횟수") == []


def test_per_visit_duration_is_not_a_visit_count(tmp_path):
    """'1회 방문 소요시간 90분' 의 1회는 횟수가 아니다."""
    mentions = extract(tmp_path, ["1회 방문 소요시간은 약 90분입니다."])
    assert values_of(mentions, "visits", "방문횟수") == []


def test_visit_needs_visit_context(tmp_path):
    mentions = extract(tmp_path, ["검사는 3회 반복 측정하여 평균한다."])
    assert values_of(mentions, "visits", "방문횟수") == []


def test_study_period_months(tmp_path):
    mentions = extract(tmp_path, ["본 연구는 승인일로부터 약 12개월간 진행됩니다."])
    assert values_of(mentions, "visits", "참여기간") == ["12개월"]


def test_study_period_not_taken_from_year_digits(tmp_path):
    """'2026년' 의 '026년' 을 참여기간으로 읽던 사고."""
    mentions = extract(tmp_path, ["연구기간: 2026년부터 수행한다."])
    assert "26개월" not in values_of(mentions, "visits", "참여기간")


# ---------------------------------------------------------------- 8. 소요시간

def test_duration_range_minutes(tmp_path):
    assert values_of(extract(tmp_path, ["검사 소요시간은 약 60~90분"]), "duration") == ["60-90분"]


def test_duration_hours_converted(tmp_path):
    assert values_of(extract(tmp_path, ["총 참여 시간은 약 1~3시간"]), "duration") == ["60-180분"]


def test_duration_mixed_units(tmp_path):
    assert values_of(extract(tmp_path, ["검사는 보통 30분에서 1시간 걸려요"]), "duration") == ["30-60분"]


def test_duration_single_value(tmp_path):
    assert "10분" in values_of(extract(tmp_path, ["순음청력검사 (약 10분) 소요"]), "duration")


def test_duration_ignores_bundang_hospital(tmp_path):
    """'분당서울대병원' 의 '분당' 을 소요시간으로 읽으면 안 된다."""
    mentions = extract(tmp_path, ["검사는 분당서울대학교병원에서 시행한다."])
    assert values_of(mentions, "duration") == []


def test_duration_ignores_rate_per_minute(tmp_path):
    mentions = extract(tmp_path, ["발화 속도는 분당 약 120~140어절로 설정하여 검사한다."])
    assert values_of(mentions, "duration") == []


def test_duration_needs_gate_word(tmp_path):
    mentions = extract(tmp_path, ["소음은 30분위수로 보정한다"])
    assert values_of(mentions, "duration") == []


# ---------------------------------------------------------------- 9. 보상

def test_payment_amount(tmp_path):
    mentions = extract(tmp_path, ["보상으로 방문당 30,000 원을 지급합니다."])
    assert values_of(mentions, "payment", "금액") == ["30000원"]


def test_payment_amount_man_unit_equals_commas(tmp_path):
    a = values_of(extract(tmp_path, ["보상으로 3만 원 지급"], "a.md"), "payment", "금액")
    b = values_of(extract(tmp_path, ["보상으로 30,000 원 지급"], "b.md"), "payment", "금액")
    assert a == b == ["30000원"]


def test_payment_form_actual_cost(tmp_path):
    assert "실비" in values_of(extract(tmp_path, ["교통비 실비 지급"]), "payment", "형태")


def test_payment_form_fixed_fee(tmp_path):
    values = values_of(extract(tmp_path, ["방문별 시간당 10,000 원(참여 사례비 포함) 지급"]), "payment", "형태")
    assert "정액·사례비" in values


def test_payment_form_actual_cost_differs_from_fixed(tmp_path):
    actual = values_of(extract(tmp_path, ["교통비 실비 지급"], "a.md"), "payment", "형태")
    fixed = values_of(extract(tmp_path, ["시간당 사례비 지급"], "b.md"), "payment", "형태")
    assert set(actual) != set(fixed)


def test_payment_needs_gate(tmp_path):
    mentions = extract(tmp_path, ["장비 단가는 30,000 원이었다는 배경 설명"])
    assert values_of(mentions, "payment", "금액") == []


# ---------------------------------------------------------------- 10. 보관기간

def test_retention_years_to_months(tmp_path):
    assert values_of(extract(tmp_path, ["기록은 3년간 보관 후 파기한다."]), "retention") == ["36개월"]


def test_retention_months_equal_years(tmp_path):
    a = values_of(extract(tmp_path, ["기록은 3년간 보관한다"], "a.md"), "retention")
    b = values_of(extract(tmp_path, ["기록은 36개월 보관한다"], "b.md"), "retention")
    assert a == b


def test_retention_needs_gate(tmp_path):
    assert values_of(extract(tmp_path, ["연구는 3년 동안 지원받는다"]), "retention") == []


# ---------------------------------------------------------------- 11. 선정기준(군 구성)

def test_group_labels(tmp_path):
    values = values_of(extract(tmp_path, ["G1: 정상군, G2: 환자군"]), "criteria")
    assert values == ["G1", "G2"]


def test_group_labels_korean_form(tmp_path):
    assert "G1" in values_of(extract(tmp_path, ["[그룹 1] 정상청력 아동"]), "criteria")


def test_group_label_not_from_units(tmp_path):
    assert values_of(extract(tmp_path, ["자극은 60 dB SPL, G12 조건"]), "criteria") == []


# ---------------------------------------------------------------- 12. 평가·검사 항목

def test_assessment_tokens(tmp_path):
    values = values_of(extract(tmp_path, ["평가 항목: 순음청력검사, 문장인지검사를 시행한다."]), "assessments")
    assert "순음청력검사" in values and "문장인지검사" in values


def test_assessment_stopwords(tmp_path):
    assert "검사일자" not in values_of(extract(tmp_path, ["| 검사일자 | 년 월 일 |"]), "assessments")


def test_assessment_form_title_facet(tmp_path):
    mentions = extract(tmp_path, ["우울증선별검사"])
    assert any(m.facet == "폼제목" for m in mentions if m.item == "assessments")


def test_assessment_mention_facet_for_long_block(tmp_path):
    mentions = extract(tmp_path, ["본 연구에서는 우울증선별검사를 포함한 여러 검사를 시행하며 결과를 기록한다."])
    facets = set(m.facet for m in mentions if m.item == "assessments")
    assert facets == {"언급"}


# ---------------------------------------------------------------- 공통

def test_items_are_exactly_twelve():
    assert len(ITEMS) == 12


def test_unreadable_document_yields_no_mentions(tmp_path):
    path = tmp_path / "x.hwp"
    path.write_text("x")
    assert extract_all(read_document(str(path))) == []


def test_mentions_carry_evidence(tmp_path):
    mentions = extract(tmp_path, ["대상자는 만 20~65세 성인이다."])
    mention = mentions[0]
    assert mention.quote and mention.where and mention.doc == "doc.md"


def test_quote_is_truncated(tmp_path):
    mentions = extract(tmp_path, ["대상자는 만 20~65세 성인이다. " + "설명 " * 200])
    assert len(mentions[0].quote) <= 161


def test_docx_table_values_are_found(tmp_path):
    path = make_docx(tmp_path / "a.docx", [("t", [["연구 대상자 수", "총 60명"]])])
    mentions = extract_all(read_document(path))
    assert values_of(mentions, "subjects", "총N") == ["60"]


@pytest.mark.parametrize("line", ["   ", "___________", "N/A", "해당 없음", "· · · ·"])
def test_placeholder_lines_produce_no_values(tmp_path, line):
    """빈 서식·해당없음 줄에서는 값이 나오면 안 된다 (죽지 않는 것만으로는 부족하다)."""
    assert extract(tmp_path, [line]) == []


def test_empty_document_is_not_readable(tmp_path):
    from irbpack.docread import read_document
    path = tmp_path / "empty.md"
    path.write_text("")
    assert extract_all(read_document(str(path))) == []
