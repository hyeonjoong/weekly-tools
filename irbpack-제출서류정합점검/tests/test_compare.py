"""대조 규칙 — 값 충돌 · 전파 누락 · 동의 범위 밖 · 공고 연락처.

핵심은 두 가지다: **부분 기재는 조용히 넘어가고**, **기준에 없는 값만 잡는다**.
"""

import pytest

from conftest import (CONSENT_LINES, PROTOCOL_LINES, load_packet, severity_of, write_md)
from irbpack.compare import _covered, compare, severity_counts


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


def test_clean_packet_has_no_findings(clean_packet):
    _, _, result = load_packet(clean_packet)
    assert result.findings == []


def test_clean_packet_reports_agreements(clean_packet):
    _, _, result = load_packet(clean_packet)
    items = set(entry[0] for entry in result.agreements)
    assert {"age", "subjects", "visits", "duration", "payment", "retention"} <= items


def test_age_conflict_is_critical(tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "age") == "치명"


def test_payment_amount_conflict_is_critical(tmp_path):
    consent = [line.replace("30,000 원", "50,000 원") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "payment") == "치명"


def test_visit_conflict_is_critical(tmp_path):
    consent = [line.replace("3회 방문", "5회 방문") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "visits") == "치명"


def test_contact_conflict_is_warning(tmp_path):
    consent = [line.replace("02-1234-5678", "02-9999-0000") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "contact") == "경고"


def test_partial_statement_is_not_a_conflict(tmp_path):
    """부속문서가 프로토콜 값의 *일부만* 적는 것은 정상이다."""
    consent = ["연구대상자 설명문 및 동의서", "Version No: 1.2",
               "귀하는 본 연구에 참여할 것을 권유 받았습니다.",
               "대상자는 만 20~65세 성인입니다."]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "age") is None


def test_extra_value_is_a_conflict(tmp_path):
    consent = CONSENT_LINES + ["추가로 만 70~80세 고령군도 포함합니다."]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "age") == "치명"


def test_propagation_gap_is_warning(tmp_path):
    """공고가 군별 수만 적고 총 대상자 수를 빼먹으면 경고."""
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
          "모집 대상: 만 20~65세 성인 (중재군 30명, 대조군 30명)",
          "참여 방법: 3회 방문", "보상: 방문당 교통비 실비 30,000 원",
          "참여문의 전화: 02-1234-5678"]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, ad=ad)
    finding = [f for f in result.findings if f.item == "subjects"]
    assert finding and finding[0].severity == "경고"
    assert "전파누락" in finding[0].kinds


def test_propagation_gap_needs_engagement(tmp_path):
    """그 항목을 아예 다루지 않는 문서에는 전파 누락을 묻지 않는다."""
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구", "참여문의 전화: 02-1234-5678"]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, ad=ad)
    assert severity_of(result, "subjects") is None


def test_crf_only_form_is_warning(tmp_path):
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |", "검사일자",
           "우울증선별검사", "| 항목 | 점수 |"]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, crf=crf)
    finding = [f for f in result.findings if f.item == "assessments"]
    assert finding and finding[0].severity == "경고"
    assert "동의범위밖" in finding[0].kinds


def test_crf_form_mentioned_elsewhere_is_not_flagged(tmp_path):
    crf = ["증례기록서 (Case Report Form)", "| Version | 1.2 |", "검사일자", "수면다원검사"]
    protocol = PROTOCOL_LINES + ["평가 항목: 수면다원검사를 시행한다."]
    _, _, result = build(tmp_path, protocol=protocol, consent=CONSENT_LINES, crf=crf)
    assert severity_of(result, "assessments") is None


def test_ad_mobile_phone_note(tmp_path):
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
          "참여문의: 연구원 / 연락처: 010-1234-5678"]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, ad=ad)
    finding = [f for f in result.findings if f.item == "contact"][0]
    assert any("개인 휴대전화" in note for note in finding.notes)


def test_ad_office_phone_no_note(tmp_path):
    ad = ["연구대상자 모집 광고안", "임상시험명: 가상 수면음향 연구",
          "참여문의: 연구원 / 연락처: 02-1234-5678"]
    _, _, result = build(tmp_path, consent=CONSENT_LINES, ad=ad)
    assert severity_of(result, "contact") is None


def test_single_document_value_is_uncomparable(tmp_path):
    consent = ["연구대상자 설명문 및 동의서", "Version No: 1.2", "귀하는 권유 받았습니다."]
    _, _, result = build(tmp_path, consent=consent)
    reasons = [entry.reason for entry in result.uncomparable if entry.item == "retention"]
    assert reasons and "한 문서에만" in reasons[0]


def test_uncomparable_always_lists_assessment_and_criteria(clean_packet):
    _, _, result = load_packet(clean_packet)
    pairs = set((entry.item, entry.facet) for entry in result.uncomparable)
    assert ("assessments", "항목 목록 전체") in pairs
    assert ("criteria", "기준 항목 수") in pairs


def test_findings_merge_per_item(tmp_path):
    """한 항목에서 여러 단위가 어긋나도 결론은 항목당 하나다."""
    consent = [line.replace("Version No: 1.2", "Version No: 1.1") for line in CONSENT_LINES]
    directory = tmp_path / "p"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.1_2026-08-01.md", consent)
    _, _, result = load_packet(directory)
    version_findings = [f for f in result.findings if f.item == "version"]
    assert len(version_findings) == 1
    assert "버전" in version_findings[0].summary and "문서날짜" in version_findings[0].summary


def test_findings_sorted_by_severity(tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세").replace("02-1234-5678", "02-9999-0000")
               for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    severities = [finding.severity for finding in result.findings]
    assert severities == sorted(severities, key=lambda value: {"치명": 0, "경고": 1}[value])


def test_severity_counts(tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    counts = severity_counts(result.findings)
    assert counts["치명"] == 1


def test_normalization_counter(tmp_path):
    consent = [line.replace("만 20~65세", "만 20-65세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert result.normalized_pairs >= 1
    assert any("연령" in example for example in result.normalized_examples)


def test_normalization_does_not_equate_different_values(tmp_path):
    consent = [line.replace("만 20~65세", "만 21~65세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    assert severity_of(result, "age") == "치명"


def test_compared_items_counted(clean_packet):
    _, _, result = load_packet(clean_packet)
    assert len(result.compared_items) >= 4


def test_anchor_prefers_protocol(tmp_path):
    consent = CONSENT_LINES + ["추가 문구 만 70~80세"]
    _, _, result = build(tmp_path, consent=consent)
    rows = [f for f in result.findings if f.item == "age"][0].rows
    anchor_rows = [row for row in rows if row.status == "기준"]
    assert anchor_rows and "연구계획서" in anchor_rows[0].doc


def test_evidence_rows_carry_quotes(tmp_path):
    consent = [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES]
    _, _, result = build(tmp_path, consent=consent)
    rows = [f for f in result.findings if f.item == "age"][0].rows
    assert all(row.quote or row.status == "없음" for row in rows)


# ------------------------------------------------------------------ 범위 포괄 규칙

@pytest.mark.parametrize("value,anchor,expected", [
    ("2회", {"1-2회"}, True),
    ("1회", {"1-2회"}, True),
    ("3회", {"1-2회"}, False),
    ("1-2회", {"1-3회"}, True),
    ("1-4회", {"1-3회"}, False),
    ("6회", {"1-2회", "다회기"}, False),
])
def test_visit_range_subsumption(value, anchor, expected):
    assert _covered(value, anchor, "visits") is expected


def test_age_never_subsumed():
    assert _covered("20-64세", {"20-65세"}, "age") is False


def test_duration_never_subsumed():
    """'총 약 1~3시간' 이 '60~90분 vs 90~180분' 불일치를 삼키면 안 된다."""
    assert _covered("60-90분", {"60-180분"}, "duration") is False


def test_exact_value_always_covered():
    assert _covered("3년", {"3년"}, "retention") is True


def test_empty_packet_is_safe():
    result = compare([], [])
    assert result.findings == []
    assert result.compared_items == set()
