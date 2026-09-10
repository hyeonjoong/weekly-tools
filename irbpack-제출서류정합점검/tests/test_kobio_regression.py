"""KoBio 실제 패킷 회귀 테스트 — **이 툴의 척추**.

이미 IRB 승인이 난 실제 패킷 6종에 이 툴을 돌려 나온 결과를 고정한다.
저장소에는 원문을 넣지 않고, **추출된 문장만 익명화**해 픽스처로 박았다:

    최병윤 → 홍길동 · 분당서울대학교병원 → 가상대학교병원 ·
    031-787-7400 → 02-000-1111 · 02-2135-2365 → 02-000-2222 ·
    010-9278-6844 → 010-1234-5678 · snubhirb@snubh.org → irb@example.org

값(방문 횟수·소요시간·보상·표본수·연령·보관기간·버전)은 **원문 그대로**다.

고정하는 것은 두 방향이다.
  (A) 실제로 있는 결함 4건을 각각 정확히 그 항목으로 잡는가
  (B) 실제로 일치하는 항목(연령·표본수 90/30/20·보관기간 3년·제목·버전 v1.0)에서
      치명이 **0건**인가  ← 오탐 억제. 이쪽이 더 중요하다.
"""

import pytest

from conftest import load_packet, write_md

# ------------------------------------------------------------------ 픽스처 (익명화된 실제 문장)

PROTOCOL = [
    "연구계획서",
    "Version No: V1.0",
    "| 연구제목 | (국문) 한국어판 소아 문장인지검사 도구 개발 및 타당화 연구 |",
    "| 책임연구자 | 이비인후과 홍길동 교수 |",
    "| 연구 대상자 수 | 총 90명 (정상청력 아동 30명, 정상청력 성인 20명, 성인 CI 사용자 20명, 소아 HA/CI 사용자 20명) |",
    "| 취약한 연구대상자 | 만 5~12세 소아 |",
    "| 검사/방문일정 | 단계별 1~2회 방문 (소아 검사의 경우 다회기 허용) |",
    "| 주요 선정기준 | G1: 만 5~12세, 순음청력역치 20 dB HL 이하 아동 G2: 만 19~45세 정상청력 성인 "
    "G3: 만 19세 이상 CI 착용 후 최소 6개월 경과 G4: 만 5~12세 HA 또는 CI 착용 |",
    "실시기관 명칭: 가상대학교병원 이비인후과 / 청각언어재활과",
    "연구책임자: 홍길동, 직위: 교수 (가상대학교병원 이비인후과)",
    "예상연구기간: IRB 승인일로부터 12개월",
    "분당 지역 외래 환자 중 모집한다. 경제적 보상: 참여 시간에 상응하는 교통비 실비 지급 (해당 기관 기준 적용).",
    "생명윤리법 시행규칙 제15조에 따라 연구 관련 기록을 연구가 종료된 시점부터 3년간 보관할 것이며, "
    "보관기간이 지난 문서는 파기할 것이다.",
    "Gifford 등(2008)은 156명의 성인 인공와우 사용자를 대상으로 천장효과를 보고하였다[5].",
]

ICF_ADULT = [
    "연구대상자 설명문",
    "Version No: 1.0",
    "연구책임자: 홍길동 교수 (가상대학교병원 이비인후과)",
    "귀하는 본 연구에 참여할 것을 권유 받았습니다. 참여 여부는 전적으로 자발적인 결정에 의한 것입니다.",
    "본 연구는 가상대학교병원에서 실시되며, 다음 4개 그룹의 대상자 총 90명이 참여할 예정입니다.",
    "정상청력 아동 30명 (만 5~12세)",
    "정상청력 성인 20명 (만 19~45세)",
    "성인 인공와우 사용자 20명 (만 19세 이상)",
    "소아 보청기/인공와우 사용자 20명 (만 5~12세)",
    "본 연구는 승인일로부터 약 12개월간 진행될 예정이며, 귀하의 연구 참여 기간은 1~2회 방문(총 약 1~3시간)입니다.",
    "순음청력검사 (약 10분)",
    "조용한 공간에서 헤드폰을 통해 한국어 문장을 듣고 따라 말씀해 주시면 됩니다. "
    "총 검사 시간은 약 60~90분이며, 중간에 휴식 시간이 포함됩니다.",
    "본인의 인공와우를 착용하신 상태로 스피커를 통해 문장을 듣고 따라 말씀해 주시면 됩니다. "
    "총 검사 시간은 약 90~120분이며, 중간에 휴식 시간이 포함됩니다.",
    "본 연구 참여에 대한 보상으로 방문별 시간당 10,000 원(교통비 및 참여 사례비 포함, 단위 시간에 따라 차등 지급)이 "
    "지급됩니다.",
    "생명윤리법 시행규칙 제15조에 따라 연구 관련 기록은 연구 종료 시점부터 3년간 보관 후 파기됩니다.",
    "| 연구책임자 | 홍길동 교수 (가상대학교병원 이비인후과) 전화: 02-000-1111 |",
    "| 공동연구자(대표) | 김연구 연구소장 (가상 테라퓨틱스) 전화: 02-000-2222 |",
    "| 연구대상자 권리 관련 문의 | 기관생명윤리심의위원회(IRB) 전화: 02-000-3333 E-mail: irb@example.org |",
]

ICF_GUARDIAN = [
    "연구대상자(소아) 설명문 — 보호자용 —",
    "Version No: 1.0",
    "연구책임자: 홍길동 교수 (가상대학교병원 이비인후과)",
    "귀하의 자녀께서는 본 연구에 참여할 것을 권유 받았습니다. 참여 여부는 보호자의 자발적인 결정입니다.",
    "본 연구는 가상대학교병원에서 실시되며, 다음 4개 그룹의 대상자 총 90명이 참여할 예정입니다.",
    "정상청력 아동 30명 (만 5~12세) — 본 그룹",
    "정상청력 성인 20명 (만 19~45세)",
    "성인 인공와우 사용자 20명 (만 19세 이상)",
    "소아 보청기/인공와우 사용자 20명 (만 5~12세) — 본 그룹",
    "본 연구는 승인일로부터 약 12개월간 진행됩니다. 자녀의 연구 참여 기간은 1회 또는 다회기 방문"
    "(총 약 1~3시간, 소아 집중력에 따라 분할 진행)입니다.",
    "순음청력검사 (약 10분)",
    "조용한 공간에서 헤드폰을 통해 한국어 문장을 듣고 따라 말합니다. "
    "총 검사 시간은 약 30~60분이며, 자녀의 집중력에 따라 휴식이 제공됩니다.",
    "자녀가 본인의 보청기 또는 인공와우를 착용한 상태로 스피커를 통해 문장을 듣고 따라 말합니다. "
    "총 참여 시간은 약 90~180분(여러 회기 분할 가능)입니다.",
    "본 연구 참여에 대한 보상으로 방문별 시간당 10,000 원(교통비 및 참여 사례비 포함, 단위 시간에 따라 차등지급)이 "
    "지급됩니다.",
    "생명윤리법 시행규칙 제15조에 따라 연구 관련 기록은 연구 종료 시점부터 3년간 보관 후 파기됩니다.",
    "| 연구책임자 | 홍길동 교수 (가상대학교병원 이비인후과) 전화: 02-000-1111 |",
    "| 공동연구자(대표) | 김연구 연구소장 (가상 테라퓨틱스) 전화: 02-000-2222 |",
    "| 연구대상자 권리 관련 문의 | 기관생명윤리심의위원회(IRB) 전화: 02-000-3333 E-mail: irb@example.org |",
]

ASSENT = [
    "연구대상자(아동) 승낙서",
    "연구제목: 한국어판 소아 문장인지검사 도구 개발 및 타당화 연구",
    "이 글은 여러분이 참여하게 될 연구에 대해 알려드리는 안내문이에요.",
    "3. 시간은 얼마나 걸려요?",
    "그룹에 따라 다르지만 보통 30분에서 1시간 정도 걸려요. 한 번에 다 하기 힘들면 여러 번에 나눠서 할 수도 있어요.",
    "아니에요. 참여할지 안 할지는 여러분이 직접 결정할 수 있어요.",
]

CRF = [
    "증례기록서 (Case Report Form)",
    "| 연구책임자 | 홍길동 교수 (가상대학교병원 이비인후과) |",
    "| 연구실시기관 | 가상대학교병원 이비인후과 / 청각언어재활과 |",
    "| Version | 1.0 |",
    "| 동의서 취득일 | 년 월 일 | 동의서 버전 | v ______ |",
    "[G1] 정상청력 아동 (만 5~12세)",
    "| 1 | 만 5~12세 아동 | 예 / 아니오 |",
    "[G2] 정상청력 성인 (만 19~45세)",
    "| 1 | 만 19~45세 성인 | 예 / 아니오 |",
    "[G3] 성인 인공와우 사용자 (만 19세 이상)",
    "[G4] 소아 보청기/인공와우 사용자 (만 5~12세)",
    "회기별 방문 기록 (Visit Log)",
    "| 회기 | 방문일자 | 수행 목록 | 소요시간 (분) | 검사자 |",
    "| Visit 1 | | | | |",
    "| Visit 2 | | | | |",
    "| Visit 3 | | | | |",
    "| Visit 4 | | | | |",
    "| Visit 5 | | | | |",
    "| Visit 6 | | | | |",
]

AD = [
    "임상시험대상자 모집 광고안",
    "임상시험명: 한국어판 소아 문장인지검사 도구 개발 및 타당화 연구",
    "[그룹 1] 정상청력 아동 (만 5~12세, 30명)",
    "방문 1~2회",
    "[그룹 2] 정상청력 성인 (만 19~45세, 20명)",
    "[그룹 3] 성인 인공와우(CI) 사용자 (만 19세 이상, 20명)",
    "[그룹 4] 소아 보청기·인공와우(HA/CI) 사용자 (만 5~12세, 20명)",
    "다회기 방문 허용 (아동의 집중력·피로도 고려)",
    "참여 대상",
    "G1: 만 5~12세, 순음청력역치 20 dB HL 이하 아동",
    "G2: 만 19~45세, 정상청력 성인",
    "G3: 만 19세 이상, CI 착용 후 최소 6개월 경과",
    "G4: 만 5~12세, HA 또는 CI 착용",
    "임상시험 참여자는 참여와 관련한 비용을 부담하지 않으며, 참여 시 소정의 교통비가 지급됩니다.",
    "참여문의: 자세한 내용은 공동연구자(김연구 연구소장(가상 테라퓨틱스)/연락처: 010-1234-5678)에게 문의하시기 바랍니다.",
]


@pytest.fixture(scope="module")
def kobio(tmp_path_factory):
    directory = tmp_path_factory.mktemp("kobio")
    write_md(directory / "연구계획서_kobio_v1.0_260518_Final_clean.md", PROTOCOL)
    write_md(directory / "ICF_성인용_KoBio소아_v1.0_260518_Final_clean.md", ICF_ADULT)
    write_md(directory / "ICF_보호자용_KoBio소아_v1.0_260518_Final_clean.md", ICF_GUARDIAN)
    write_md(directory / "Assent_KoBio소아_v1.0_260518_Final_clean.md", ASSENT)
    write_md(directory / "CRF_KoBio소아_통합_v1.0_260518_Final_clean.md", CRF)
    write_md(directory / "모집 광고안_KoBio_Master_v1.0.md", AD)
    return load_packet(directory)


def finding(result, item):
    matches = [entry for entry in result.findings if entry.item == item]
    return matches[0] if matches else None


# ------------------------------------------------------------------ (A) 잡아야 하는 것

def test_roles_all_detected(kobio):
    documents, _, _ = kobio
    roles = sorted(document.role for document in documents)
    assert roles == sorted(["프로토콜", "동의서", "동의서", "동의서(소아)", "CRF", "모집공고"])


def test_defect_1_visit_count_is_critical(kobio):
    """방문·회기: 계획서 1~2회(다회기 허용) vs CRF Visit 6까지 — 세 문서가 다른 말을 한다."""
    _, _, result = kobio
    entry = finding(result, "visits")
    assert entry is not None and entry.severity == "치명"
    differing = [row.doc for row in entry.rows if row.status == "다름"]
    assert any("CRF" in name for name in differing)


def test_defect_1_visit_rows_show_all_five_documents(kobio):
    """3-way 그림이 보이도록 값을 말한 문서를 전부 인쇄한다."""
    _, _, result = kobio
    entry = finding(result, "visits")
    roles = set(row.role for row in entry.rows)
    assert {"프로토콜", "동의서", "CRF", "모집공고"} <= roles


def test_defect_1_guardian_single_visit_is_shown(kobio):
    _, _, result = kobio
    entry = finding(result, "visits")
    guardian = [row for row in entry.rows if "보호자용" in row.doc]
    assert guardian and any("1회" in value for value in guardian[0].values)


def test_defect_2_duration_is_critical(kobio):
    """같은 연구의 성인용·보호자용 동의서가 서로 다른 검사 시간을 고지한다."""
    _, _, result = kobio
    entry = finding(result, "duration")
    assert entry is not None and entry.severity == "치명"
    docs = set(row.doc for row in entry.rows)
    assert any("성인용" in name for name in docs) and any("보호자용" in name for name in docs)


def test_defect_2_duration_values_are_quoted(kobio):
    _, _, result = kobio
    values = " ".join(value for row in finding(result, "duration").rows for value in row.values)
    assert "60~90분" in values and "90~180분" in values


def test_defect_3_payment_category_is_critical(kobio):
    """계획서는 '실비', 동의서는 '시간당 정액 + 사례비' — IRB 가 다르게 보는 범주."""
    _, _, result = kobio
    entry = finding(result, "payment")
    assert entry is not None and entry.severity == "치명"
    values = " ".join(value for row in entry.rows for value in row.values)
    assert "실비" in values and "정액·사례비" in values


def test_defect_4_ad_personal_mobile_is_warned(kobio):
    """모집공고(공개 게시물)에 동의서 대표 연락처와 다른 개인 휴대전화."""
    _, _, result = kobio
    entry = finding(result, "contact")
    assert entry is not None and entry.severity == "경고"
    assert any("개인 휴대전화" in note for note in entry.notes)


def test_defect_5_total_n_missing_in_ad_is_warning(kobio):
    """공고에 군별(30명/20명)만 있고 총 90명이 없다 → 전파 누락 경고."""
    _, _, result = kobio
    entry = finding(result, "subjects")
    assert entry is not None and entry.severity == "경고"
    assert "전파누락" in entry.kinds


def test_exact_counts(kobio):
    """치명 3건 · 경고 2건 — 개발 시점에 고정한 실측값."""
    _, _, result = kobio
    critical = [entry for entry in result.findings if entry.severity == "치명"]
    warnings = [entry for entry in result.findings if entry.severity == "경고"]
    assert sorted(entry.item for entry in critical) == ["duration", "payment", "visits"]
    assert sorted(entry.item for entry in warnings) == ["contact", "subjects"]


# ------------------------------------------------------------------ (B) 잡으면 안 되는 것

@pytest.mark.parametrize("item", ["age", "retention", "title", "version", "pi", "criteria"])
def test_no_false_critical_on_matching_items(kobio, item):
    """연령·보관기간·제목·버전·연구책임자·군구성은 실제로 일치한다 — 치명이 나오면 오탐."""
    _, _, result = kobio
    entry = finding(result, item)
    assert entry is None or entry.severity != "치명", "%s 에서 오탐" % item


def test_age_is_reported_as_agreement(kobio):
    _, _, result = kobio
    ages = [entry for entry in result.agreements if entry[0] == "age"]
    assert ages and "만 5~12세" in ages[0][2]


def test_sample_size_values_agree(kobio):
    """표본수 90/30/20 은 값 자체로는 일치한다(빠진 곳만 경고)."""
    _, _, result = kobio
    entry = finding(result, "subjects")
    assert "값충돌" not in entry.kinds


def test_retention_three_years_agrees(kobio):
    _, _, result = kobio
    retention = [entry for entry in result.agreements if entry[0] == "retention"]
    assert retention and "3년" in retention[0][2]


def test_version_v1_0_agrees_across_documents(kobio):
    """버전은 파일명 축과 본문 축을 따로 본다 — 둘 다 v1.0 으로 일치해야 한다."""
    _, _, result = kobio
    by_facet = dict((entry[1], entry) for entry in result.agreements if entry[0] == "version")
    assert "버전(파일명)" in by_facet and by_facet["버전(파일명)"][3] >= 5
    assert "버전" in by_facet and by_facet["버전"][3] >= 3


def test_citation_count_156_is_not_treated_as_sample_size(kobio):
    """'Gifford 등(2008) … 156명' 을 대상자 수로 세면 안 된다."""
    _, mentions, _ = kobio
    values = [mention.value for mention in mentions if mention.item == "subjects"]
    assert "156" not in values


def test_bundang_is_not_read_as_minutes(kobio):
    """'분당 지역' 의 '분당' 을 소요시간으로 읽으면 안 된다."""
    _, mentions, _ = kobio
    protocol_durations = [mention.value for mention in mentions
                          if mention.item == "duration" and "연구계획서" in mention.doc]
    assert protocol_durations == []


def test_blank_consent_version_field_is_not_a_value(kobio):
    """CRF 의 '동의서 버전 | v ______' 는 값이 아니다."""
    _, mentions, _ = kobio
    crf_versions = set(mention.value for mention in mentions
                       if mention.item == "version" and mention.facet == "버전" and "CRF" in mention.doc)
    assert crf_versions == {"1.0"}


def test_coverage_is_confessed(kobio):
    _, _, result = kobio
    assert len(result.compared_items) >= 8
    assert result.uncomparable


def test_every_finding_carries_evidence(kobio):
    _, _, result = kobio
    for entry in result.findings:
        for row in entry.rows:
            assert row.status in ("없음", "값못찾음") or (row.where and row.quote)
