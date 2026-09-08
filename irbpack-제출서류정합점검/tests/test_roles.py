"""역할 자동판별 — 파일명 + 본문 키워드, 애매하면 추측하지 않는다."""
import pytest

from irbpack import roles
from irbpack.model import Doc, Para, ROLE_AD, ROLE_ASSENT, ROLE_CRF, ROLE_ICF, ROLE_PROTOCOL, ROLE_REG


def _doc(name, text=""):
    d = Doc(path=name, name=name)
    d.paras = [Para(idx=i, text=t) for i, t in enumerate(text.split("\n")) if t]
    return d


@pytest.mark.parametrize("name,text,role", [
    ("연구계획서_v1.2.docx", "연구 배경\n연구 방법", ROLE_PROTOCOL),
    ("Protocol_v1.0.docx", "연구 설계", ROLE_PROTOCOL),
    ("ICF_성인용_v1.2.docx", "연구대상자 설명문\n동의합니다", ROLE_ICF),
    ("Assent_소아용.docx", "어린이 여러분", ROLE_ASSENT),
    ("승낙서.docx", "친구들", ROLE_ASSENT),
    ("CRF_통합.docx", "Visit 1\nVisit 2", ROLE_CRF),
    ("증례기록서.docx", "대상자 번호", ROLE_CRF),
    ("모집공고안.docx", "참여자를 모집합니다", ROLE_AD),
    ("연구대상자 모집 광고안_Master V1.0.docx", "", ROLE_AD),
    ("등록정보.docx", "CRIS 등록번호 KCT0001234", ROLE_REG),
])
def test_detect(name, text, role):
    got, why = roles.detect(_doc(name, text))
    assert got == role, why
    assert why


def test_ambiguous_returns_none():
    got, why = roles.detect(_doc("문서.docx", "아무 내용도 없는 문서"))
    assert got is None and "충분하지" in why


def test_close_scores_are_ambiguous():
    got, why = roles.detect(_doc("동의서_계획서.docx", ""))
    assert got is None and "애매" in why


def test_body_keyword_reason_is_printed():
    got, why = roles.detect(_doc("x.docx", "연구계획서\n연구 방법\n연구 설계\n연구 배경"))
    assert got == ROLE_PROTOCOL and "본문" in why


def test_assent_overrides_icf_when_child_marker():
    got, _ = roles.detect(_doc("동의서_소아용.docx", "연구대상자 설명문\n동의합니다\n어린이 여러분"))
    assert got == ROLE_ASSENT


def test_forced_role_pattern_and_exact():
    d1, d2 = _doc("a.docx", ""), _doc("b.docx", "")
    errs = roles.assign([d1, d2], [(ROLE_PROTOCOL, "a.docx"), (ROLE_ICF, "b*")])
    assert errs == [] and d1.role == ROLE_PROTOCOL and d2.role == ROLE_ICF and d1.role_forced


def test_forced_role_unmatched_reports_error():
    d = _doc("연구계획서.docx", "")
    errs = roles.assign([d], [(ROLE_CRF, "없는파일.docx")])
    assert any("맞는 파일이 없습니다" in e for e in errs)


def test_assign_reports_ambiguous():
    d = _doc("문서.docx", "")
    errs = roles.assign([d], [])
    assert errs and "--role" in errs[0]


@pytest.mark.parametrize("arg", ["프로토콜", "프로토콜=", "=a.docx", "외계인=a.docx"])
def test_parse_role_args_rejects(arg):
    with pytest.raises(ValueError):
        roles.parse_role_args([arg])


@pytest.mark.parametrize("alias,role", [("protocol", ROLE_PROTOCOL), ("ICF", ROLE_ICF), ("assent", ROLE_ASSENT), ("crf", ROLE_CRF), ("모집", ROLE_AD)])
def test_parse_role_aliases(alias, role):
    assert roles.parse_role_args(["{}=x.docx".format(alias)]) == [(role, "x.docx")]


def test_sublabels_and_duplicate_numbering():
    a = _doc("ICF_성인용.docx", "연구대상자 설명문\n동의합니다")
    b = _doc("ICF_보호자용.docx", "연구대상자 설명문\n동의합니다")
    c = _doc("ICF_v2.docx", "연구대상자 설명문\n동의합니다")
    d = _doc("ICF_v3.docx", "연구대상자 설명문\n동의합니다")
    assert roles.assign([a, b, c, d], []) == []
    assert a.label == "동의서(성인)" and b.label == "동의서(보호자)"
    assert {c.label, d.label} == {"동의서#1", "동의서#2"}
