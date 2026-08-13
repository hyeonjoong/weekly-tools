"""절 인식 — 참고문헌을 못 찾으면 리포트가 권·호·페이지 소음으로 뒤덮인다."""

from __future__ import annotations

from numcheck.docio import manuscript_from_text
from numcheck.sections import assign_sections, heading_key


def sections_of(text):
    ms = manuscript_from_text(text)
    assign_sections(ms)
    return {ln.no: ln.section for ln in ms.lines}


def test_markdown_headings():
    assert heading_key("## Results") == "Results"
    assert heading_key("# Abstract") == "Abstract"
    assert heading_key("### 참고문헌") == "References"
    assert heading_key("2. Methods") == "Methods"
    assert heading_key("**Discussion**") == "Discussion"


def test_latex_headings():
    assert heading_key("\\section{Results}") == "Results"
    assert heading_key("\\begin{thebibliography}{99}") == "References"


def test_annotated_headings_still_recognised():
    """실제 작업 원고의 제목에는 메모가 붙어 있다."""
    assert heading_key("References (Vancouver style; 번호 유지)") == "References"
    assert heading_key("Results (Primary Endpoints)") == "Results"
    assert heading_key("Discussion 가설이 많아 어렵지만 일단 씀") == "Discussion"


def test_sentences_are_not_mistaken_for_headings():
    assert heading_key("Results (Table 2) are shown below.") is None
    assert heading_key("이 결과는 방법에서 설명한 대로다.") is None
    assert heading_key("x" * 200) is None


def test_tail_sections_end_the_reference_list():
    text = ("## References\n1. A. 2019;28(3):1-10.\n\n"
            "## Acknowledgements\n감사합니다.\n")
    got = sections_of(text)
    assert got[2] == "References"
    assert got[5] == "Other"


def test_keywords_section_is_not_body():
    text = "## Abstract\n초록 본문.\n\n## Keywords\n불면증, 수면\n\n## Introduction\n서론.\n"
    got = sections_of(text)
    assert got[5] == "Other"
    assert got[8] == "Introduction"


def test_text_before_any_heading_is_title():
    got = sections_of("논문 제목\n\n## Abstract\n초록.\n")
    assert got[1] == "Title"
