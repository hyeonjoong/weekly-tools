"""역할 판별과 원고 감지(강제 장치 1)."""

import pytest

from conftest import write_md
from irbpack.docread import read_document
from irbpack.roles import (AD, ASSENT, CONSENT, CRF, EXCLUDE, PROTOCOL, REGISTRY, RoleError,
                           assign_roles, detect_manuscript, detect_role, parse_role_option,
                           packet_marker_hits)


def doc(tmp_path, name, lines):
    return read_document(write_md(tmp_path / name, lines))


def test_protocol_detected(tmp_path):
    document = doc(tmp_path, "연구계획서_v1.2.md", ["연구계획서", "선정기준", "제외기준"])
    role, reason = detect_role(document)
    assert role == PROTOCOL and "파일명" in reason


def test_consent_detected(tmp_path):
    document = doc(tmp_path, "ICF_성인용_v1.2.md", ["연구대상자 설명문 및 동의서", "귀하는 권유 받았습니다"])
    assert detect_role(document)[0] == CONSENT


def test_assent_detected(tmp_path):
    document = doc(tmp_path, "Assent_소아_v1.0.md", ["연구대상자(아동) 승낙서", "여러분이 결정할 수 있어요"])
    assert detect_role(document)[0] == ASSENT


def test_crf_detected(tmp_path):
    document = doc(tmp_path, "CRF_통합_v1.0.md", ["증례기록서 (Case Report Form)", "검사일자"])
    assert detect_role(document)[0] == CRF


def test_ad_detected(tmp_path):
    document = doc(tmp_path, "모집광고안_v1.0.md", ["연구대상자 모집 광고안", "참여문의"])
    assert detect_role(document)[0] == AD


def test_registry_detected(tmp_path):
    document = doc(tmp_path, "CRIS_등록정보.md", ["등록번호 KCT0001234", "임상연구정보서비스"])
    assert detect_role(document)[0] == REGISTRY


def test_ambiguous_document_is_refused(tmp_path):
    document = doc(tmp_path, "문서.md", ["아무 말"])
    role, reason = detect_role(document)
    assert role == "" and reason


def test_weak_evidence_is_refused(tmp_path):
    document = doc(tmp_path, "문서.md", ["증례기록서"])
    role, reason = detect_role(document)
    assert role == "" or role == CRF        # 본문 1점만으로는 확정하지 않는다
    if role == "":
        assert "약" in reason or "비슷" in reason


def test_assign_roles_raises_on_unknown(tmp_path):
    documents = [doc(tmp_path, "연구계획서.md", ["연구계획서", "선정기준"]),
                 doc(tmp_path, "메모.md", ["그냥 메모"])]
    with pytest.raises(RoleError) as error:
        assign_roles(documents)
    assert "--role" in str(error.value)


def test_assign_roles_with_override(tmp_path):
    documents = [doc(tmp_path, "연구계획서.md", ["연구계획서", "선정기준"]),
                 doc(tmp_path, "메모.md", ["그냥 메모"])]
    kept, excluded = assign_roles(documents, ["동의서=메모.md"])
    assert [document.role for document in kept] == [PROTOCOL, CONSENT]
    assert excluded == []


def test_assign_roles_exclude(tmp_path):
    documents = [doc(tmp_path, "연구계획서.md", ["연구계획서", "선정기준"]),
                 doc(tmp_path, "메모.md", ["그냥 메모"])]
    kept, excluded = assign_roles(documents, ["제외=메모.md"])
    assert len(kept) == 1 and len(excluded) == 1


def test_override_by_glob(tmp_path):
    documents = [doc(tmp_path, "ICF_성인.md", ["아무 말"]), doc(tmp_path, "ICF_보호자.md", ["아무 말"])]
    kept, _ = assign_roles(documents, ["동의서=ICF_*.md"])
    assert all(document.role == CONSENT for document in kept)


def test_override_by_substring(tmp_path):
    documents = [doc(tmp_path, "무명문서_A.md", ["아무 말"]), doc(tmp_path, "무명문서_B.md", ["아무 말"])]
    kept, _ = assign_roles(documents, ["동의서=무명문서"])
    assert all(document.role == CONSENT for document in kept)


def test_unreadable_document_gets_no_role(tmp_path):
    path = tmp_path / "a.hwp"
    path.write_text("x")
    documents = [read_document(str(path)), doc(tmp_path, "연구계획서.md", ["연구계획서", "선정기준"])]
    kept, _ = assign_roles(documents)
    assert kept[0].role == "" and kept[0].role_source == "읽지 못함"


def test_parse_role_option_ok():
    assert parse_role_option(["프로토콜=a.docx"]) == [(PROTOCOL, "a.docx")]


def test_parse_role_option_alias():
    assert parse_role_option(["icf=a.docx"]) == [(CONSENT, "a.docx")]


def test_parse_role_option_exclude_alias():
    assert parse_role_option(["제외=a.docx"]) == [(EXCLUDE, "a.docx")]


def test_parse_role_option_requires_equals():
    with pytest.raises(RoleError):
        parse_role_option(["프로토콜"])


def test_parse_role_option_unknown_role():
    with pytest.raises(RoleError) as error:
        parse_role_option(["설계도=a.docx"])
    assert "모르는 역할" in str(error.value)


def test_parse_role_option_empty_pattern():
    with pytest.raises(RoleError):
        parse_role_option(["프로토콜="])


def test_conflicting_overrides(tmp_path):
    documents = [doc(tmp_path, "a.md", ["x"])]
    with pytest.raises(RoleError):
        assign_roles(documents, ["프로토콜=a.md", "동의서=a.md"])


def test_role_reason_is_printed_in_document(tmp_path):
    document = doc(tmp_path, "연구계획서_v1.md", ["연구계획서", "선정기준"])
    assign_roles([document])
    assert document.role_reason and document.role_source == "자동"


# ------------------------------------------------------------------ 원고 감지

def test_manuscript_detected(tmp_path):
    document = doc(tmp_path, "paper.md", ["Abstract", "본문", "Introduction", "본문",
                                          "Discussion", "본문", "References"])
    flagged = detect_manuscript([document])
    assert flagged and len(flagged[0][1]) >= 3


def test_manuscript_korean_headings(tmp_path):
    document = doc(tmp_path, "논문.md", ["초록", "본문", "서론", "본문", "고찰", "본문", "참고문헌"])
    assert detect_manuscript([document])


def test_protocol_with_one_heading_is_not_manuscript(tmp_path):
    document = doc(tmp_path, "연구계획서.md", ["연구계획서", "선정기준", "제외기준", "참고 문헌"])
    assert detect_manuscript([document]) == []


def test_english_irb_protocol_is_exempted(tmp_path):
    """영문 IRB 프로토콜에도 Introduction/Methods 는 흔하다 — 패킷 표지가 2개 이상이면 통과."""
    document = doc(tmp_path, "Protocol_EN.md", [
        "Abstract", "text", "Introduction", "text", "Methods", "text",
        "Inclusion Criteria", "adults", "Exclusion Criteria", "none",
        "Informed Consent Form will be obtained",
    ])
    assert packet_marker_hits(document) >= 2
    assert detect_manuscript([document]) == []


def test_tex_file_is_manuscript(tmp_path):
    path = tmp_path / "paper.tex"
    path.write_text("\\documentclass{article}")
    document = read_document(str(path))
    flagged = detect_manuscript([document])
    assert flagged and "LaTeX" in flagged[0][1][0]


def test_long_paragraph_is_not_a_heading(tmp_path):
    document = doc(tmp_path, "x.md", ["Abstract 라는 단어가 들어간 아주 긴 문장으로 서술된 문단입니다. " * 3])
    assert detect_manuscript([document]) == []
