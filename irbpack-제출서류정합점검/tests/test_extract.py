"""항목 추출 — 여러 표현을 같은 값으로, 함정 문장은 값이 아니다."""
import pytest

from irbpack import extract as X
from irbpack.model import Doc, Para, ROLE_CRF, ROLE_ICF, ROLE_PROTOCOL


def _doc(text, role=ROLE_PROTOCOL, name="연구계획서.md"):
    d = Doc(path=name, name=name, role=role, label=role)
    d.paras = []
    for i, line in enumerate([t for t in text.split("\n") if t.strip()]):
        if "\t" in line:
            d.paras.append(Para(idx=i, text=line, table=1, row=i + 1))
        else:
            d.paras.append(Para(idx=i, text=line))
    return d


def _norms(exts, sub=None):
    return {e.norm for e in exts if sub is None or e.sub == sub}


@pytest.mark.parametrize("line", ["연구제목: 소아 인공와우 음악 재활 연구", "연구 제목\t소아 인공와우 음악 재활 연구",
                                  "과제명 : 소아 인공와우 음악 재활 연구", "Study Title: 소아 인공와우 음악 재활 연구"])
def test_title_forms(line):
    exts = X.extract_title(_doc(line))
    assert len(exts) == 1 and exts[0].norm == "소아인공와우음악재활연구"


def test_title_english_sub():
    exts = X.extract_title(_doc("Title: Music-based auditory rehabilitation for children"))
    assert exts[0].sub == "영문"


def test_title_too_short_ignored():
    assert X.extract_title(_doc("연구제목: 가나")) == []


@pytest.mark.parametrize("line,ver", [("Version No\tV1.0", "1"), ("버전 1.2", "1.2"), ("문서 버전: v2.0", "2"), ("Version 1.0판", "1")])
def test_version_forms(line, ver):
    assert _norms(X.extract_version(_doc(line)), "버전") == {ver}


def test_version_from_filename_when_body_silent():
    exts = X.extract_version(_doc("본문에 버전 없음", name="ICF_v1.3_260518.docx"))
    assert _norms(exts, "버전") == {"1.3"} and _norms(exts, "날짜") == {"2026-05-18"}
    raw = {e.raw for e in exts if e.sub == "버전"}
    assert raw == {"v1.3"}
    assert all(e.where == "파일명" for e in exts)


def test_version_body_beats_filename():
    exts = X.extract_version(_doc("Version No\t1.0", name="ICF_v1.3.docx"))
    assert _norms(exts, "버전") == {"1"}


def test_version_revision_history_row_is_skipped():
    d = _doc("개정 이력\t버전 1.0 (2026.05.18) 최초 작성; 버전 1.2 (2026.08.25) 개정\nVersion No\t1.2\n작성일\t2026-08-25")
    exts = X.extract_version(d)
    assert _norms(exts, "버전") == {"1.2"} and _norms(exts, "날짜") == {"2026-08-25"}


def test_version_sentence_with_two_versions_is_skipped():
    assert _norms(X.extract_version(_doc("본 설명문은 버전 1.0 에서 1.1 로 개정되었습니다.")), "버전") == set()


def test_filename_v2_without_dot():
    assert _norms(X.extract_version(_doc("본문", name="ICF_V2.md")), "버전") == {"2"}


def test_date_with_spaces_on_plain_line():
    assert _norms(X.extract_version(_doc("작성일: 2026. 8. 25.")), "날짜") == {"2026-08-25"}


@pytest.mark.parametrize("line", ["연구책임자: 홍가상", "책임연구자 이름\t홍가상", "연구 책임자 홍가상 교수", "Principal Investigator: 홍가상"])
def test_pi_forms(line):
    assert _norms(X.extract_pi(_doc(line)), "책임자") == {"홍가상"}


def test_pi_label_word_not_a_name():
    assert _norms(X.extract_pi(_doc("연구책임자\t이름\t소속")), "책임자") == set()


@pytest.mark.parametrize("line", ["궁금한 점이 있으면 연구책임자에게 문의하십시오.", "연구책임자로부터 설명을 들었습니다.", "연구책임자 또는 담당자", "연구책임자께서 설명합니다", "연구책임자이며 교수이다"])
def test_pi_particles_are_not_names(line):
    assert _norms(X.extract_pi(_doc(line)), "책임자") == set()


def test_org():
    assert _norms(X.extract_pi(_doc("연구기관\t가상대학교병원")), "기관") == {"가상대학교병원"}


def test_contact_phone_and_email():
    exts = X.extract_contact(_doc("문의 02-2135-2300 또는 irb@example.org"))
    assert _norms(exts, "전화") == {"0221352300"} and _norms(exts, "이메일") == {"irb@example.org"}


def test_contact_date_not_phone():
    assert X.extract_contact(_doc("작성일 2026-05-18")) == []


@pytest.mark.parametrize("line,expect", [
    ("연구 대상자는 총 90명이며, 소아군 30명, 보호자군 20명으로 구성한다.", {"90", "30", "20"}),
    ("총 1,200명을 모집한다.", {"1200"}),
    ("연구자 3명이 평가한다.", set()),
    ("연구간호사 2명과 대상자 40명", {"40"}),
    ("대상자 1명당 약 90분이 소요됩니다.", set()),
    ("선행 연구에서는 120명의 환자를 분석하였다.", set()),
    ("1명의 연구자가 30명의 참여자를 면담한다.", {"30"}),
    ("환자 1명당 보호자 1명이 참여한다.", set()),
    ("보호자 1명이 동반한다.", set()),
])
def test_n_forms(line, expect):
    assert _norms(X.extract_n(_doc(line))) == expect


def test_age_forms():
    assert _norms(X.extract_age(_doc("소아군의 연령은 만 5~12세, 성인은 만 19세 이상 45세 이하이다."))) == {"5~12", "19~45"}


@pytest.mark.parametrize("line,expect", [
    ("본 연구는 총 2회 방문으로 진행한다.", {"2"}),
    ("두 번 방문하게 됩니다.", {"2"}),
    ("방문 횟수는 총 3회이다.", {"3"}),
    ("1회 또는 다회기 방문을 하게 됩니다.", {"1회 또는 다회기"}),
    ("1회 방문 시 약 90분이 소요됩니다.", set()),
    ("매 방문마다 1회씩 검사", set()),
    ("총 4회기로 진행한다.", {"4"}),
    ("주 1회 방문하여 총 8회 방문한다.", {"8"}),
    ("제2회 방문 시 혈액검사를 시행한다.", set()),
    ("3회차 방문에서 종료한다.", set()),
    ("회기 수: 6회", {"6"}),
])
def test_visits_forms(line, expect):
    assert _norms(X.extract_visits(_doc(line)), "방문횟수") == expect


def test_visit_enumeration_in_crf():
    text = "회기\t일자\nVisit 1\t__\nVisit 2\t__\nVisit 3\t__"
    exts = X.extract_visits(_doc(text, role=ROLE_CRF, name="CRF.md"))
    assert _norms(exts, "방문횟수") == {"3"}


def test_visit_enumeration_needs_two():
    assert _norms(X.extract_visits(_doc("Visit 1 에서 검사를 받습니다.")), "방문횟수") == set()


def test_period():
    assert _norms(X.extract_visits(_doc("참여 기간은 약 8주입니다.")), "참여기간") == {"8주"}


def test_study_period_is_not_participation_period():
    assert _norms(X.extract_visits(_doc("총 연구 기간은 IRB 승인 후 2년이다.")), "참여기간") == set()


@pytest.mark.parametrize("line,expect", [
    ("1회 방문 시 약 90분이 소요됩니다.", {"90"}),
    ("검사는 1시간 30분 정도 걸립니다.", {"90"}),
    ("약 60분이 소요되며 길어지면 120분까지 걸릴 수 있습니다.", {"60", "120"}),
    ("검사는 30분 정도 걸리고, 길어지면 1시간 정도 걸려요.", {"30", "60"}),
    ("자료는 24시간 이내에 보관한다.", set()),
    ("매일 8시간 수면을 취한다.", set()),
    ("총 90분", set()),
    ("훈련은 매일 30분씩 진행되며 검사에는 약 60분이 소요됩니다.", {"60"}),
])
def test_duration_forms(line, expect):
    assert _norms(X.extract_duration(_doc(line))) == expect


@pytest.mark.parametrize("line,expect", [
    ("대상자에게는 교통비 실비 지급 (해당 기관 기준 적용)을 한다.", {"실비"}),
    ("방문별 시간당 10,000 원(교통비 및 참여 사례비 포함)이 지급됩니다.", {"10000원/시간당"}),
    ("연구 참여에 대한 보상으로 1만원 상당의 기념품이 제공됩니다.", {"10000원"}),
    ("금전적 보상은 제공되지 않습니다.", {"없음"}),
    ("회당 3만원의 사례비를 지급한다.", {"30000원/회당"}),
    ("총 50,000원을 일괄 지급한다.", {"50000원/총액"}),
    ("연구 관련 손상 시 보상을 위해 임상시험보험에 가입하였으며 보상 한도는 1인당 100,000,000원입니다.", set()),
    ("연구비 예산은 총 5,000,000원이며 보상에 쓰인다.", set()),
    ("금전적 보상은 없으며 교통비만 지급됩니다.", {"없음"}),
    ("사례비: 없음", {"없음"}),
    ("회당 5천원의 사례비를 지급한다.", {"5000원/회당"}),
])
def test_compensation_forms(line, expect):
    assert _norms(X.extract_compensation(_doc(line))) == expect


@pytest.mark.parametrize("line,expect", [
    ("개인정보는 연구 종료 후 3년간 보관한 뒤 폐기한다.", {"36개월"}),
    ("기록은 36개월 보관 후 파기합니다.", {"36개월"}),
    ("자료는 삼년 동안 보존한다.", {"36개월"}),
    ("3년 전 진단받은 자", set()),
    ("자료는 1년 6개월간 보관한다.", {"18개월"}),
])
def test_retention_forms(line, expect):
    assert _norms(X.extract_retention(_doc(line))) == expect


def test_criteria_counts_and_items():
    text = "선정기준\n1) 인공와우 1년 이상\n2) 보호자 동의\n3) 한국어 사용\n제외기준\n1) 중복 장애\n연구 방법\n본문"
    exts = X.extract_criteria(_doc(text))
    assert _norms(exts, "선정기준 개수") == {"3"} and _norms(exts, "제외기준 개수") == {"1"}
    assert len([e for e in exts if e.sub == "선정항목"]) == 3


def test_criteria_narrative_yields_nothing():
    assert X.extract_criteria(_doc("선정기준은 인공와우를 1년 이상 사용한 소아입니다.")) == []


def test_assessments_protocol_list():
    exts = X.extract_assessments(_doc("평가 항목은 다음과 같다: 음악지각검사, 어음인지검사, 삶의질 설문지(QoL)."))
    assert _norms(exts) == {"음악지각검사", "어음인지검사", "삶의질설문지"}


def test_assessments_common_list_sentence():
    exts = X.extract_assessments(_doc("1차 방문 시 불면증 심각도 척도(ISI), 피츠버그 수면의 질 지수(PSQI), 병원 불안-우울 척도(HADS)를 평가한다."))
    assert {"불면증심각도척도", "피츠버그수면의질지수", "병원불안우울척도"} <= _norms(exts)


def test_assessments_visit_ordinal_stripped():
    exts = X.extract_assessments(_doc("제2회 방문 시 혈액검사를 시행한다."))
    assert _norms(exts) == {"혈액검사"}


def test_assessments_crf_form_lines():
    d = _doc("평가 폼\n음악지각검사\n수면일지 (Sleep Diary)\n본 검사\n검사", role=ROLE_CRF, name="CRF.md")
    assert _norms(X.extract_assessments(d)) == {"음악지각검사", "수면일지"}


def test_extract_all_unreadable_doc_gives_nothing():
    d = Doc(path="x", name="x", readable=False)
    assert X.extract_all(d) == []


def test_extract_all_covers_all_items():
    assert set(X.EXTRACTORS) == {"title", "version", "pi", "contact", "n", "age", "visits", "duration", "compensation", "retention", "criteria", "assessments"}


@pytest.mark.parametrize("raw,masked", [
    ("+82-10-1234-5678", "010-****-5678"), ("+82 10 1234 5678", "010-****-5678"), ("(02)123-4567", "02-***-4567"),
    ("(02) 1234-5678", "02-****-5678"), ("010 - 1234 - 5678", "010-****-5678"), ("1588-1234", "1588-****"),
    ("900101-5234567", "******-*******"), ("9001011234567", "******-*******"), ("홍길동@example.com", "홍**@example.com"),
    ("０１０－１２３４－５６７８", "010-****-5678"), ("010.1234.5678", "010-****-5678"),
])
def test_mask_forms(raw, masked):
    from irbpack import mask
    assert mask.mask("연락처 " + raw + " 끝") == "연락처 " + masked + " 끝"


def test_mask_linear_on_long_ascii():
    import time
    from irbpack import mask
    t = time.time()
    mask.mask("a" * 200_000)
    assert time.time() - t < 2.0


def test_contact_plus82_normalised():
    exts = X.extract_contact(_doc("문의 +82-10-1234-5678"))
    assert _norms(exts, "전화") == {"01012345678"}


# ------------------------------------------------------------ 라운드 2

def test_crf_checklist_header_row_not_counted():
    d = _doc("선정기준\n기준\t예\t아니오\n① 만 19세 이상\t□\t□\n② 서면 동의\t□\t□\n제외기준\n기준\t예\t아니오\n① 임신부\t□\t□", role=ROLE_CRF, name="CRF.md")
    exts = X.extract_criteria(d)
    assert _norms(exts, "선정기준 개수") == {"2"} and _norms(exts, "제외기준 개수") == {"1"}


def test_two_level_heading_is_not_assessment():
    d = Doc(path="p", name="p", role=ROLE_PROTOCOL, label="프로토콜")
    d.paras = [Para(idx=0, text="4.3 평가 항목", section="4.3 평가 항목"), Para(idx=1, text="4.3.1 주요 평가 변수", section="4.3.1 주요 평가 변수"),
               Para(idx=2, text="평가 항목은 다음과 같다: 불면증심각도척도(ISI).", section="4.3 평가 항목")]
    assert _norms(X.extract_assessments(d)) == {"불면증심각도척도"}


@pytest.mark.parametrize("line,expect", [
    ("탈락 및 불성실 응답을 고려하여 최대 220명까지 모집할 수 있다.", set()),
    ("표본이 작거나(대부분 100명 미만) 단일 대학에 국한되어 있었다.", set()),
    ("성인을 대상으로 한 연구는 대개 50명 이하였다.", set()),
    ("총 200명을 모집한다.", {"200"}),
])
def test_n_caps_and_bounds(line, expect):
    assert _norms(X.extract_n(_doc(line))) == expect


def test_assessment_honorific_and_leadin():
    d = _doc("귀하는 지각된 스트레스 척도(PSS)를 작성하시게 됩니다.\n측정 도구: 다음 설문을 실시한다.\n스마트폰 중독 자가진단 척도(S-Scale), 우울 척도(PHQ-9).")
    got = _norms(X.extract_assessments(d))
    assert {"지각된스트레스척도", "스마트폰중독자가진단척도", "우울척도"} <= got


def test_assessment_suffix_chain_not_truncated():
    assert _norms(X.extract_assessments(_doc("통증숫자평가척도(NRS)를 평가한다."))) == {"통증숫자평가척도"}


def test_pi_cover_cell_and_paren_org():
    exts = X.extract_pi(_doc("연구책임자 소속/직위/성명\t가상대학교병원 재활의학과 / 교수 / 김가상"))
    assert _norms(exts, "책임자") == {"김가상"} and _norms(exts, "기관") == {"가상대학교병원"}
    exts = X.extract_pi(_doc("연구책임자\t김가상 (가상대학교병원 재활의학과 교수)"))
    assert _norms(exts, "책임자") == {"김가상"} and _norms(exts, "기관") == {"가상대학교병원"}
