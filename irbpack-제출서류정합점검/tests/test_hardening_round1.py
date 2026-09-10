"""적대 패널 라운드 1에서 나온 결함을 하나씩 고정한다.

각 테스트는 리뷰어가 실제로 재현한 입력을 그대로 쓴다. 이 파일이 깨지면
"고쳤다고 적어 둔 것 중 하나가 되돌아갔다"는 뜻이다.
"""

import os

import pytest

from conftest import CONSENT_LINES, PROTOCOL_LINES, load_packet, severity_of, write_md
from irbpack.baseline import match_document
from irbpack.cli import EXIT_OK, main
from irbpack.docread import read_document
from irbpack.items import extract_all, touches_topic
from irbpack.safeio import mask_pii, mask_then_truncate


def extract(tmp_path, lines, name="doc.md"):
    return extract_all(read_document(write_md(tmp_path / name, lines)))


def values(mentions, item, facet=None):
    return sorted(set(m.value for m in mentions
                      if m.item == item and (facet is None or m.facet == facet)))


def build(tmp_path, protocol=None, consent=None, crf=None, ad=None, assent=None):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", protocol or PROTOCOL_LINES)
    if consent is not None:
        write_md(directory / "ICF_성인용_v1.2_2026-09-01.md", consent)
    if crf is not None:
        write_md(directory / "CRF_통합_v1.2_2026-09-01.md", crf)
    if ad is not None:
        write_md(directory / "모집공고안_v1.2_2026-09-01.md", ad)
    if assent is not None:
        write_md(directory / "Assent_v1.1_2026-08-01.md", assent)
    return load_packet(directory)


# ================================================================ 정확성 리뷰어

def test_revision_history_does_not_mask_a_stale_version(tmp_path):
    """개정 이력 표가 있으면 프로토콜이 1.0·1.1·1.2 를 전부 '말하게' 되어
    구버전 부속문서를 덮어 주던 사고 (라운드1 #1)."""
    protocol = PROTOCOL_LINES + [
        "## 문서 개정 이력",
        "버전 1.0 (2026-05-18) 최초 작성, 버전 1.1 (2026-08-01) 개정, 버전 1.2 (2026-09-01) 개정",
    ]
    assent = ["연구대상자(아동) 승낙서", "Version No: 1.1", "연구제목: 가상 수면음향 연구",
              "검사하러 3회 방문하게 돼요."]
    _, _, result = build(tmp_path, protocol=protocol, consent=CONSENT_LINES, assent=assent)
    assert severity_of(result, "version") == "치명"


def test_revision_history_block_is_skipped(tmp_path):
    mentions = extract(tmp_path, [
        "Version No: 1.2",
        "문서 개정 이력: 버전 1.0 최초 작성, 버전 1.1 개정",
    ])
    assert values(mentions, "version", "버전") == ["1.2"]


def test_document_version_is_one_value_per_document(tmp_path):
    """문서 하나가 여러 버전을 '말하는' 상태를 만들지 않는다."""
    mentions = extract(tmp_path, ["Version No: 1.2", "이전 문서는 Version 1.0 이었다."])
    assert len(values(mentions, "version", "버전")) == 1


def test_two_ancillary_documents_disagreeing_is_reported(tmp_path):
    """프로토콜이 3년·10년을 둘 다 언급하면, 동의서(3년)와 CRF(10년)가 서로
    다른 말을 해도 조용히 넘어가던 사고 (라운드1 #2).

    라운드2 P2-2 이후 심각도는 **경고**다 — 두 문서가 같은 대상(동의서 원본 vs 전자자료)을
    말하는지 툴은 알 수 없기 때문이다. 다만 조용히 넘어가지는 않는다.
    """
    protocol = PROTOCOL_LINES + [
        "동의서 원본은 연구 종료 후 3년간 보관하고, 전자적 연구자료는 10년간 보관 후 파기한다."]
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |",
           "연구 관련 기록은 10년간 보관 후 파기한다."]
    _, _, result = build(tmp_path, protocol=protocol, consent=CONSENT_LINES, crf=crf)
    finding = [f for f in result.findings if f.item == "retention"]
    assert finding and finding[0].severity == "경고"
    assert "부속문서충돌" in finding[0].kinds


def test_pairwise_conflict_names_both_documents(tmp_path):
    protocol = PROTOCOL_LINES + ["기록은 3년간 보관하고, 전자자료는 10년간 보관 후 파기한다."]
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |", "기록은 10년간 보관 후 파기한다."]
    _, _, result = build(tmp_path, protocol=protocol, consent=CONSENT_LINES, crf=crf)
    finding = [f for f in result.findings if f.item == "retention"][0]
    assert "부속문서끼리 다른 값" in finding.summary
    assert len([row for row in finding.rows if row.status == "다름"]) == 2


def test_hoedang_is_not_a_payment_form(tmp_path):
    """'방문 회당 교통비 실비' 의 '회당' 을 정액·사례비로 읽던 사고 (라운드1 #3)."""
    mentions = extract(tmp_path, ["연구 참여 보상으로 방문 회당 교통비 실비 30,000 원이 지급됩니다."])
    assert "정액·사례비" not in values(mentions, "payment", "형태")


def test_payment_form_conflict_still_detected(tmp_path):
    consent = [line.replace("방문당 교통비 실비 30,000 원",
                            "시간당 사례비 30,000 원") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "payment") == "치명"


def test_age_bound_pair_is_one_range(tmp_path):
    """'만 19세 이상 만 65세 이하' 와 '만 19~65세' 는 같은 값이다 (라운드1 #4)."""
    a = values(extract(tmp_path, ["선정기준: 만 19세 이상 만 65세 이하의 성인"], "a.md"), "age")
    b = values(extract(tmp_path, ["선정기준: 만 19~65세 성인"], "b.md"), "age")
    assert a == b == ["19-65세"]


def test_age_bound_pair_with_mimman(tmp_path):
    assert values(extract(tmp_path, ["만 19세 이상 65세 미만"]), "age") == ["19-64세"]


def test_age_range_with_se_before_tilde(tmp_path):
    assert values(extract(tmp_path, ["만 19세 ~ 65세 성인"]), "age") == ["19-65세"]


def test_open_ended_age_still_extracted(tmp_path):
    assert values(extract(tmp_path, ["만 19세 이상 성인"]), "age") == ["19+세"]


def test_retention_ignores_neighbouring_clause(tmp_path):
    """'2년간 진행되며, … 3년간 보관' 에서 2년을 보관기간으로 읽던 사고 (라운드1 #5)."""
    mentions = extract(tmp_path, [
        "본 연구는 2년간 진행되며, 수집된 개인정보는 연구 종료 후 3년간 보관 후 파기합니다."])
    assert values(mentions, "retention") == ["36개월"]


def test_duration_ignores_eligibility_clause(tmp_path):
    """'평균 수면시간이 6시간 미만인 성인' 을 검사 소요시간으로 읽던 사고 (라운드1 #6)."""
    mentions = extract(tmp_path, [
        "참여 대상: 최근 3개월간 평균 수면시간이 6시간 미만인 성인, 검사 소요시간은 90분입니다."])
    assert values(mentions, "duration") == ["90분"]


def test_duration_ignores_threshold_suffix(tmp_path):
    mentions = extract(tmp_path, ["검사 참여 조건: 수면시간 6시간 이상인 자"])
    assert values(mentions, "duration") == []


def test_weekly_frequency_is_not_a_visit_count(tmp_path):
    """'8주 동안 매주 1회 방문' 의 1회는 총 방문 횟수가 아니다 (라운드1 #7)."""
    mentions = extract(tmp_path, ["귀하는 8주 동안 매주 1회 병원에 방문하시게 됩니다."])
    assert values(mentions, "visits", "방문횟수") == []


def test_total_visit_count_still_extracted(tmp_path):
    mentions = extract(tmp_path, ["| 검사/방문일정 | 총 8회 방문 |"])
    assert values(mentions, "visits", "방문횟수") == ["8회"]


def test_staff_headcount_is_not_a_subject_count(tmp_path):
    """'연구책임자 1명과 연구간호사 2명이 참여' 는 대상자 수가 아니다 (라운드1 #8)."""
    mentions = extract(tmp_path, [
        "본 연구에는 연구책임자 1명과 연구간호사 2명이 참여합니다."])
    assert values(mentions, "subjects") == []


def test_subject_count_next_to_staff_clause_survives(tmp_path):
    mentions = extract(tmp_path, [
        "본 연구에는 연구간호사 2명이 참여하며, 대상자는 총 60명입니다."])
    assert values(mentions, "subjects", "총N") == ["60"]


@pytest.mark.parametrize("first,second", [
    ("연구제목: 음향자극–호흡유도 불면증 개선 연구", "연구제목: 음향자극-호흡유도 불면증 개선 연구"),
    ("연구제목: 「수면장애 개선 연구」", "연구제목: 수면장애 개선 연구"),
    ("연구제목: 수면장애 개선 연구.", "연구제목: 수면장애 개선 연구"),
])
def test_title_punctuation_does_not_create_a_conflict(tmp_path, first, second):
    """대시·낫표·마침표 차이로 같은 제목이 달라 보이던 사고 (라운드1 #9)."""
    a = values(extract(tmp_path, [first], "a.md"), "title")
    b = values(extract(tmp_path, [second], "b.md"), "title")
    assert a == b


def test_birthdate_is_not_a_document_date(tmp_path):
    """동의서 서명란의 생년월일 6자리를 문서 날짜로 읽던 사고 (라운드1 #10)."""
    mentions = extract(tmp_path, [
        "동의일자: 2026년 9월 1일  대상자 생년월일(6자리): 890312  서명: ______"])
    assert "1989-03-12" not in values(mentions, "version", "문서날짜")


def test_propagation_gap_works_for_single_facet_items(tmp_path):
    """연령·소요시간·보관기간은 대조단위가 하나뿐이라 전파 누락 규칙이 죽어 있었다 (라운드1 #11)."""
    consent = ["연구대상자 설명문 및 동의서", "Version No: 1.2",
               "귀하는 본 연구에 참여할 것을 권유 받았습니다.",
               "대상자는 만 20~65세 성인 총 60명입니다. 3회 방문하시게 됩니다.",
               "검사 소요시간은 약 60~90분입니다.",
               "연구 참여 보상으로 방문당 교통비 실비 30,000 원이 지급됩니다.",
               "개인정보는 연구 종료 후 폐기됩니다."]      # 기간을 안 적었다
    _, _, result = build(tmp_path, consent=consent)
    finding = [f for f in result.findings if f.item == "retention"]
    assert finding and finding[0].severity == "경고"
    assert "전파누락" in finding[0].kinds


def test_blank_form_column_is_not_topic_engagement(tmp_path):
    """CRF 의 '| 소요시간 (분) |' 은 적을 칸이지 고지가 아니다 — 여기에 경고를 내면 우는 체커가 된다."""
    path = write_md(tmp_path / "CRF_통합_v1.2.md",
                    ["증례기록서 (Case Report Form)", "| 회기 | 소요시간 (분) | 검사자 |"])
    document = read_document(path)
    assert touches_topic(document, "duration") is False


def test_baseline_does_not_pair_adult_with_guardian_consent(tmp_path):
    """짝이 없는 새 문서를 이전 패킷의 다른 동의서와 맞춰 '개정 미반영' 이라 우기던 사고 (라운드1 #12)."""
    old = tmp_path / "old"
    write_md(old / "연구계획서_v1.1.md", PROTOCOL_LINES)
    write_md(old / "ICF_보호자용_v1.1.md", CONSENT_LINES)
    new = tmp_path / "new"
    write_md(new / "연구계획서_v1.2.md", [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES])
    write_md(new / "ICF_성인용_v1.2.md", CONSENT_LINES)
    old_documents, _, _ = load_packet(old)
    new_documents, _, _ = load_packet(new)
    consent = [document for document in new_documents if document.role == "동의서"][0]
    assert match_document(consent, old_documents) is None


def test_baseline_still_pairs_the_same_document(tmp_path):
    old = tmp_path / "old"
    write_md(old / "연구계획서_v1.1.md", PROTOCOL_LINES)
    write_md(old / "ICF_성인용_v1.1.md", CONSENT_LINES)
    new = tmp_path / "new"
    write_md(new / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(new / "ICF_성인용_v1.2.md", CONSENT_LINES)
    old_documents, _, _ = load_packet(old)
    new_documents, _, _ = load_packet(new)
    consent = [document for document in new_documents if document.role == "동의서"][0]
    matched = match_document(consent, old_documents)
    assert matched is not None and "성인용" in matched.name


def test_same_filename_in_two_folders_stays_two_documents(tmp_path):
    """두 폴더에 같은 파일명이 있으면 한 문서로 뭉쳐 값이 엉뚱하게 귀속되던 사고 (라운드1 #13)."""
    first = tmp_path / "A"
    second = tmp_path / "B"
    write_md(first / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(first / "ICF_성인용_v1.2.md", CONSENT_LINES)
    write_md(second / "ICF_성인용_v1.2.md",
             [line.replace("총 60명", "총 90명") for line in CONSENT_LINES])
    code = main([str(first), str(second), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == 1                                     # 60명 vs 90명 → 치명
    with open(str(tmp_path / "out" / "항목추출표.csv"), encoding="utf-8-sig") as handle:
        text = handle.read()
    assert "[A]" in text and "[B]" in text


def test_uncomparable_rows_are_not_duplicated(clean_packet, tmp_path):
    """고정 안내 두 줄이 compare 와 report 양쪽에서 들어가 중복되던 문제 (라운드1 #14)."""
    main([str(clean_packet), "--out-dir", str(tmp_path / "out"), "--quiet"])
    with open(str(tmp_path / "out" / "대조불가.csv"), encoding="utf-8-sig") as handle:
        text = handle.read()
    assert text.count("항목 목록 전체") == 1
    assert text.count("기준 항목 수") == 1


def test_normalization_counter_is_cross_document_only(tmp_path):
    """한 문서 안의 표기 흔들림을 '문서 간 정규화' 로 세던 문제 (라운드1 #15)."""
    consent = ["연구대상자 설명문 및 동의서", "Version No: 1.2",
               "귀하는 본 연구에 참여할 것을 권유 받았습니다.",
               "대상자는 만 20~65세 성인입니다."]
    _, _, result = build(tmp_path, consent=consent)
    for example in result.normalized_examples:
        assert "버전" not in example or "파일명" in example


def test_per_group_values_are_not_a_conflict(tmp_path):
    """성인용 동의서가 60명·20,000원을, 소아 승낙서가 24명·10,000원을 적는 것은 정상이다.

    라운드2 P2-2: 부속문서끼리 맞대는 규칙이 총N·금액까지 덮어 성인+소아 패킷마다
    오탐 치명을 냈다. 이런 패킷은 조용해야 한다.
    """
    protocol = [
        "연구계획서", "Version No: 1.2",
        "| 연구제목 | (국문) 가상 수면음향 연구 |",
        "| 연구책임자 | 김하늘 교수 (가상병원 수면의학과) |",
        "| 연구 대상자 수 | 총 84명 (성인 총 60명, 청소년 총 24명) |",
        "| 검사/방문일정 | 3회 방문 |",
        "대상자는 만 13~65세이다.",
        "경제적 보상: 성인은 방문당 교통비 실비 20,000 원, 청소년은 10,000 원을 지급한다.",
        "연구 관련 기록은 종료 시점부터 3년간 보관 후 파기한다.",
    ]
    consent = [
        "연구대상자 설명문 및 동의서", "Version No: 1.2",
        "귀하는 본 연구에 참여할 것을 권유 받았습니다. 자발적인 결정입니다.",
        "성인 대상자는 총 60명이 참여합니다. 3회 방문하시게 됩니다.",
        "연구 참여 보상으로 방문당 교통비 실비 20,000 원이 지급됩니다.",
    ]
    assent = [
        "연구대상자(아동) 승낙서", "Version No: 1.2", "연구제목: 가상 수면음향 연구",
        "청소년 참여자는 총 24명이에요. 3회 방문하게 돼요.",
        "올 때마다 교통비 실비 10,000 원을 받아요.",
    ]
    _, _, result = build(tmp_path, protocol=protocol, consent=consent, assent=assent)
    assert severity_of(result, "subjects") is None
    assert severity_of(result, "payment") is None


def test_label_colon_value_is_still_read(tmp_path):
    """'검사 소요시간: 약 60분' 처럼 레이블과 값이 콜론으로 갈린 형태 (라운드2 P2-1)."""
    for line, item, expected in [
        ("검사 소요시간: 약 60분", "duration", ["60분"]),
        ("1회 방문 소요시간: 60분", "duration", ["60분"]),
        ("검사에 소요되는 시간은, 총 90분입니다.", "duration", ["90분"]),
        ("개인정보 보관기간: 3년", "retention", ["36개월"]),
        ("자료 보관: 5년", "retention", ["60개월"]),
    ]:
        assert values(extract(tmp_path, [line], "x.md"), item) == expected, line


def test_carry_does_not_leak_into_the_next_sentence(tmp_path):
    """이미 값을 가진 절은 게이트를 넘기지 않는다 — 옆 문장의 숫자를 끌어오면 안 된다."""
    mentions = extract(tmp_path, ["기록은 3년간 보관 후 파기한다. 연구는 12개월 진행된다."])
    assert values(mentions, "retention") == ["36개월"]


def test_sample_storage_is_not_a_retention_gap(tmp_path):
    """'검체는 냉장고에 보관한다' 는 기록 보관기간 얘기가 아니다 (라운드2 P2-4)."""
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |",
           "채취한 타액 검체는 검사 전까지 4도 냉장고에 보관한다."]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, crf=crf)
    assert severity_of(result, "retention") is None


def test_prose_age_is_confessed(tmp_path):
    """'스무 살에서 예순네 살' 은 못 읽지만, 못 읽었다고 말한다 (라운드2 P2-3)."""
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |",
           "이 연구에는 스무 살에서 예순네 살 사이의 성인이 참여합니다."]
    documents, _, result = build(tmp_path, consent=CONSENT_LINES, crf=crf)
    ages = [entry for entry in result.agreements if entry[0] == "age"]
    assert ages and any("CRF" in name for name in ages[0][4])


# ================================================================ 문서 정직성 리뷰어

def test_agreement_line_names_documents_that_stayed_silent(tmp_path):
    """표현이 달라 값을 못 읽은 문서가 있으면 '일치' 라고 말하지 않는다 (라운드1 S1)."""
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |",
           "대상자 연령은 스무 살에서 예순다섯 살 사이입니다."]      # 정규식이 못 읽는 표기
    documents, mentions, result = build(tmp_path, consent=CONSENT_LINES, crf=crf)
    ages = [entry for entry in result.agreements if entry[0] == "age"]
    assert ages and ages[0][4], "값을 못 찾은 문서가 일치 줄에 드러나야 한다"
    assert any("CRF" in name for name in ages[0][4])


def test_silent_document_is_confessed_in_uncomparable(tmp_path):
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |",
           "대상자 연령은 스무 살에서 예순다섯 살 사이입니다."]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, crf=crf)
    reasons = [entry for entry in result.uncomparable
               if entry.item == "age" and entry.docs]
    assert reasons and "일치'가 아닙니다" in reasons[0].reason


def test_markdown_matrix_cannot_be_column_shifted(tmp_path):
    """값 안의 '|' 가 대조표의 열을 밀어 값이 다른 문서에 귀속되던 사고 (라운드1 S2/A3)."""
    consent = CONSENT_LINES + ["| 버전 | 1.2 |"]
    documents, mentions, result = build(tmp_path, consent=consent)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    markdown = report.markdown(0)
    table = [line for line in markdown.splitlines() if line.startswith("| 문서 버전·날짜")]
    header = [line for line in markdown.splitlines() if line.startswith("| 항목 / 대조단위")][0]
    width = header.count("|")
    assert table and all(row.count("|") == width for row in table)


def test_console_collapses_agreeing_rows(tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |", "대상자는 만 20~65세입니다."]
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
          "모집 대상: 만 20~65세 성인", "참여문의 전화: 02-1234-5678"]
    assent = ["연구대상자(아동) 승낙서", "Version No: 1.2", "연구제목: 가상 수면음향 연구",
              "만 20~65세 어른들도 같이 참여해요."]
    documents, mentions, result = build(tmp_path, consent=consent, crf=crf, ad=ad, assent=assent)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    assert "접었습니다" in report.console()
    report_all = Report("packet", documents, [], result, all_rows=True)
    report_all.set_mentions(mentions)
    assert "접었습니다" not in report_all.console()


def test_conflict_csv_has_status_column(clean_packet, tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    from irbpack.report import Report
    report = Report("packet", [], [], result)
    report.set_mentions([])
    header, rows = report.csv_conflicts()
    assert "상태" in header
    assert any(row[header.index("상태")] == "다름" for row in rows)


def test_extraction_csv_is_deduplicated(tmp_path):
    documents, mentions, result = build(tmp_path, consent=CONSENT_LINES)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    header, rows = report.csv_extractions()
    keys = [(row[1], row[3], row[4], row[5], row[6]) for row in rows]
    assert len(keys) == len(set(keys))
    assert header[0] == "대조결과"


def test_console_has_no_literal_markdown_bold(clean_packet):
    """콘솔에 '**강조**' 마크다운이 그대로 찍히지 않는다 (전화번호 마스킹의 **** 는 별개)."""
    import re as _re
    documents, mentions, result = load_packet(clean_packet)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    assert not _re.search(r"\*\*[^*\s][^*]*\*\*", report.console())


def test_korean_numeral_visits_are_understood(tmp_path):
    """리포트가 인쇄하는 '두 번 → 2회' 규칙을 실제로 적용한다 (라운드1 S11)."""
    mentions = extract(tmp_path, ["귀하는 두 번 방문하시게 됩니다."])
    assert values(mentions, "visits", "방문횟수") == ["2회"]


def test_korean_numeral_frequency_is_still_excluded(tmp_path):
    mentions = extract(tmp_path, ["검사는 한 번에 다 하기 힘들면 방문을 나눌 수 있어요."])
    assert "1회" not in values(mentions, "visits", "방문횟수")


# ================================================================ 안전 리뷰어

def test_masking_happens_before_truncation():
    """24자에서 잘린 번호가 마스킹 패턴에 안 걸려 그대로 남던 유출 (라운드1 A1)."""
    masked = mask_then_truncate("연구책임자 대표 연락처 031-787-1234", 24)
    assert "031-787-1234" not in masked
    assert "**" in masked


@pytest.mark.parametrize("raw,leak", [
    ("연락처 (02)3010-3114 입니다", "3010-3114"),
    ("Tel: 031)787-1234", "787-1234"),
    ("주민등록번호 9001011234567", "9001011234567"),
    ("주민번호 900101-9234567", "900101-9234567"),
    ("담당자 주민등록번호 890312–1234567", "1234567"),
])
def test_masking_covers_korean_formats(raw, leak):
    """괄호형 지역번호·하이픈 없는 주민번호가 통과하던 문제 (라운드1 A2)."""
    assert leak not in mask_pii(raw)


def test_section_label_does_not_leak_a_phone_number(tmp_path):
    path = write_md(tmp_path / "ICF_설명문_v1.2.md",
                    ["연구책임자 대표 연락처 031-787-1234",
                     "귀하는 본 연구에 참여할 것을 권유 받았습니다.",
                     "대상자는 만 20~65세입니다."])
    document = read_document(path)
    for block in document.blocks:
        assert "031-787-1234" not in block.where()


def test_artifacts_contain_no_raw_phone_numbers(tmp_path):
    protocol = PROTOCOL_LINES + ["연구책임자 대표 연락처 031-787-1234 / 담당자 (031) 787-9999"]
    consent = CONSENT_LINES + ["문의 전화 (02)3010-3114 / 이메일 hong.gildong@example.org"]
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", protocol)
    write_md(directory / "ICF_성인용_v1.2.md", consent)
    out_dir = tmp_path / "out"
    main([str(directory), "--out-dir", str(out_dir), "--quiet"])
    for name in os.listdir(str(out_dir)):
        with open(str(out_dir / name), encoding="utf-8-sig") as handle:
            text = handle.read()
        for leak in ("031-787-1234", "787-9999", "3010-3114", "hong.gildong"):
            assert leak not in text, "%s 에 %s 가 남았습니다" % (name, leak)


def test_input_symlink_is_not_read(tmp_path):
    """패킷 폴더에 심어진 심볼릭 링크로 패킷 밖 내용이 리포트에 섞이지 않게 (라운드1 A5)."""
    outside = tmp_path / "secret.md"
    write_md(outside, ["연구대상자 모집 광고안", "임상시험명: 비밀 연구", "참여 대상: 만 20~44세"])
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    os.symlink(str(outside), str(directory / "모집공고안_v1.2.md"))
    documents = [read_document(os.path.join(str(directory), name))
                 for name in sorted(os.listdir(str(directory)))]
    linked = [document for document in documents if "모집공고" in document.name][0]
    assert not linked.read_ok and "심볼릭" in linked.error


def test_out_dir_under_symlinked_parent_is_resolved(tmp_path):
    """/tmp 처럼 상위가 심볼릭인 경로도 쓸 수 있어야 하고, 실제 경로로 해소되어야 한다 (라운드1 A4)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    code = main([str(directory), "--out-dir", str(link / "out"), "--quiet"])
    assert code == EXIT_OK
    assert os.path.exists(str(real / "out" / "정합점검.md"))


def test_pdf_inflation_is_capped():
    from irbpack import pdfread
    assert pdfread.MAX_INFLATED <= 128 * 1024 * 1024


# ================================================================ 견고성 리뷰어

def test_broken_pipe_keeps_the_exit_code(tmp_path):
    """`irbpack 패킷/ | head` 로 읽는 쪽이 먼저 끝나도 종료코드가 뒤집히면 안 된다 (라운드1 R1)."""
    import subprocess
    import sys
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    command = ('%s -m irbpack %s --out-dir %s | head -1 > /dev/null; exit ${PIPESTATUS[0]}'
               % (sys.executable, directory, tmp_path / "out"))
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            timeout=60)
    assert result.returncode == EXIT_OK
    assert "Traceback" not in result.stderr
    assert "BrokenPipe" not in result.stderr


def test_docx_zip_bomb_is_refused_quickly(tmp_path):
    """압축을 풀면 1GB 가 되는 .docx 를 메모리째 읽지 않는다 (라운드1 R2)."""
    import zipfile
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"\x00" * (300 * 1024 * 1024))
    document = read_document(str(path))
    assert not document.read_ok
    assert "압축 폭탄" in document.error or "너무" in document.error


def test_pdf_stream_budget_is_bounded():
    """압축 폭탄 PDF 가 몇 분씩 CPU 를 물고 있지 않게 (라운드1 R3)."""
    from irbpack import pdfread
    assert pdfread.MAX_INFLATED <= 8 * 1024 * 1024
    assert pdfread.MAX_TOTAL_CONTENT <= 32 * 1024 * 1024


def test_unknown_format_is_counted_not_dropped(tmp_path):
    """.odt/.rtf 처럼 모르는 형식도 '읽지 못한 문서'로 세어 자백한다 (라운드1 R4)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    write_md(directory / "동의서_보호자용_v1.2.odt", CONSENT_LINES)
    code = main([str(directory), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == 3
    with open(str(tmp_path / "out" / "대조불가.csv"), encoding="utf-8-sig") as handle:
        assert ".odt" in handle.read()


def test_subfolder_documents_are_confessed(tmp_path):
    """하위 폴더는 보지 않되, 안 봤다고 말한다 (라운드1 R4)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    write_md(directory / "sub" / "CRF_통합_v1.2.md", ["증례기록서 (Case Report Form)", "검사일자"])
    from irbpack.docread import scan
    paths, notes = scan([str(directory)])
    assert len(paths) == 2
    assert notes and "하위 폴더" in notes[0]


def test_empty_out_dir_is_an_input_error(tmp_path, capsys):
    """--out-dir "" 가 현재 폴더에 산출물을 쏟던 문제 (라운드1 R5)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    assert main([str(directory), "--out-dir", ""]) == 2
    assert "--out-dir 가 비어" in capsys.readouterr().err


def test_out_dir_inside_packet_is_idempotent(tmp_path):
    """출력 폴더를 패킷 안에 두어도 두 번째 실행이 자기 리포트를 문서로 읽지 않는다 (라운드1 R6)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    first = main([str(directory), "--out-dir", str(directory / "결과"), "--quiet"])
    second = main([str(directory), "--out-dir", str(directory / "결과"), "--quiet"])
    assert first == second == EXIT_OK


def test_artifact_left_in_packet_folder_is_skipped(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    write_md(directory / "정합점검.md", ["# 이전 리포트", "치명 3건"])
    from irbpack.docread import scan
    paths, _ = scan([str(directory)])
    assert not any(path.endswith("정합점검.md") for path in paths)


def test_missing_input_path_says_so(tmp_path, capsys):
    """'문서가 1개뿐입니다' 라고 엉뚱하게 말하던 문제 (라운드1 R7)."""
    assert main([str(tmp_path / "없는폴더"), "--out-dir", str(tmp_path / "out")]) == 2
    assert "입력 경로를 찾지 못했습니다" in capsys.readouterr().err


def test_binary_garbage_is_not_a_readable_document(tmp_path):
    """무작위 바이트가 동의서로 통과하던 문제 (라운드1 R8)."""
    path = tmp_path / "ICF_binary.md"
    path.write_bytes(os.urandom(50000))
    document = read_document(str(path))
    assert not document.read_ok


def test_fifo_does_not_hang(tmp_path):
    """FIFO 를 인자로 주면 영원히 멈춰 있던 문제 (라운드1 R9)."""
    path = tmp_path / "ICF_fifo.md"
    os.mkfifo(str(path))
    document = read_document(str(path))
    assert not document.read_ok and "일반 파일이 아닙니다" in document.error


def test_encrypt_word_in_prose_is_not_an_encrypted_pdf(tmp_path):
    """본문에 '/Encrypt' 라는 글자가 있다고 암호화 PDF 로 보지 않는다 (라운드1 R10)."""
    from irbpack.pdfread import extract_pdf_text
    import zlib
    stream = (b"BT 72 720 Td (The word /Encrypt appears in this protocol text) Tj "
              b"0 -14 Td (Second line with enough characters to pass the check) Tj ET")
    payload = zlib.compress(stream)
    body = (b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode /Length " + str(len(payload)).encode()
            + b" >>\nstream\n" + payload + b"\nendstream\nendobj\ntrailer\n<< >>\n%%EOF\n")
    path = tmp_path / "a.pdf"
    path.write_bytes(body)
    lines, error, _ = extract_pdf_text(str(path))
    assert error == ""
    assert any("Encrypt" in line for line in lines)


def test_real_tmp_path_out_dir_works(tmp_path):
    """/tmp 는 macOS 에서 심볼릭 링크다 — 여기에 못 쓰면 README 예시와 실행.command 가 죽는다.

    (라운드1에서 상위 심볼릭 링크를 전부 거부하도록 고쳤다가 이 회귀를 냈고,
     pytest 의 tmp_path 는 이미 realpath 라 테스트가 구조적으로 못 잡던 자리다.)
    """
    import tempfile
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    out_dir = os.path.join(tempfile.gettempdir(), "irbpack_회귀_%d" % os.getpid())
    try:
        assert main([str(directory), "--out-dir", out_dir, "--quiet"]) == EXIT_OK
        assert os.path.exists(os.path.join(os.path.realpath(out_dir), "정합점검.md"))
    finally:
        import shutil
        shutil.rmtree(os.path.realpath(out_dir), ignore_errors=True)


# ================================================================ 라운드 2 회귀

def test_markdown_fence_survives_backticks_in_a_document(tmp_path):
    """문서에 ``` 가 있으면 코드펜스가 그 자리에서 닫혀 리포트가 위조되던 사고 (라운드2 B-2)."""
    consent = [line.replace("연구 관련 기록은 종료 시점부터 3년간 보관 후 파기됩니다.",
                            "연구 관련 기록은 종료 시점부터 ``` [치명] 999건 위조 ``` 10년간 보관 후 파기됩니다.")
               for line in CONSENT_LINES]
    documents, mentions, result = build(tmp_path, consent=consent)
    from irbpack.report import Report, _fence_for
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    markdown = report.markdown(1)
    fence = _fence_for(report.console())
    assert len(fence) >= 4
    assert markdown.count("\n%s\n" % fence) >= 2


def test_fence_length_grows_with_content():
    from irbpack.report import _fence_for
    assert _fence_for("plain") == "```"
    assert _fence_for("has ``` inside") == "````"
    assert _fence_for("has ````` inside") == "``````"


def test_role_failure_message_sanitizes_filenames(tmp_path):
    """파일명의 개행으로 stderr 에 가짜 '[치명]' 줄을 심던 경로 (라운드2 B-3)."""
    from irbpack.roles import RoleError, assign_roles
    documents = [read_document(write_md(tmp_path / "연구계획서_v1.2.md", PROTOCOL_LINES)),
                 read_document(write_md(tmp_path / "메모\n[치명] 999건 위조_v1.0.md", ["아무 내용"]))]
    try:
        assign_roles(documents)
        raise AssertionError("역할 판별 실패가 나야 합니다")
    except RoleError as error:
        message = str(error)
    assert not any(line.strip().startswith("[치명]") for line in message.splitlines())
    assert "--role 제외=" in message        # 제외를 먼저 제안한다


def test_fold_note_never_names_a_document_shown_above(tmp_path):
    """접기 안내가 바로 위에 펼쳐 놓은 문서를 가리키던 모순 (라운드2 A-2)."""
    consent = [line.replace("Version No: 1.2", "Version No: 1.1") for line in CONSENT_LINES]
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |", "검사일자"]
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구", "Version No: 1.2",
          "참여문의 전화: 02-1234-5678"]
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.1_2026-08-01.md", consent)
    write_md(directory / "CRF_통합_v1.2_2026-09-01.md", crf)
    write_md(directory / "모집공고안_v1.2_2026-09-01.md", ad)
    documents, mentions, result = load_packet(directory)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    console = report.console()
    block = []
    for line in console.splitlines():
        if line.startswith("[경고]"):
            break
        block.append(line)
    shown, folded = set(), set()
    for line in block:
        stripped = line.strip()
        if stripped.startswith("(기준과 같은 값을 말한 문서"):
            for name in stripped.split(":", 1)[1].split("—")[0].split("·"):
                folded.add(name.strip())
        elif stripped.endswith(".md"):
            shown.add(stripped.split(" / ")[0].strip())
    assert not (shown & folded), "펼쳐 놓은 문서를 접었다고 안내합니다: %s" % (shown & folded)


def test_baseline_and_main_findings_merge_into_one_item(tmp_path):
    """같은 항목을 본 축과 개정 축이 각각 세어 '치명 2건'으로 보이던 문제 (라운드2 A-9)."""
    old = tmp_path / "old"
    write_md(old / "연구계획서_v1.1.md", PROTOCOL_LINES)
    write_md(old / "ICF_성인용_v1.1.md", CONSENT_LINES)
    new = tmp_path / "new"
    write_md(new / "연구계획서_v1.2.md", [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES])
    write_md(new / "ICF_성인용_v1.2.md", CONSENT_LINES)
    code = main([str(new), "--baseline", str(old), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == 1
    with open(str(tmp_path / "out" / "정합점검.md"), encoding="utf-8") as handle:
        text = handle.read()
    assert "치명 1건" in text


def test_all_findings_is_stable_across_calls(clean_packet, tmp_path):
    """all_findings 는 프로퍼티다 — 호출할 때마다 블록이 불어나면 안 된다."""
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    documents, mentions, result = build(tmp_path, consent=consent)
    from irbpack.report import Report
    report = Report("packet", documents, [], result)
    report.set_mentions(mentions)
    first = len(report.all_findings[0].blocks)
    report.console()
    report.markdown(1)
    assert len(report.all_findings[0].blocks) == first


def test_service_number_is_masked():
    """15xx·16xx·18xx 대표번호도 가린다 (라운드2 B-1 계열)."""
    assert mask_pii("문의 1588-1234") == "문의 1588-****"
