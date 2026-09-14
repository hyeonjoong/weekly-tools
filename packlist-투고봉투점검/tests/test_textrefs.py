"""본문에서 그림·표·보충자료 약속을 뽑는 규칙."""

import pytest

from packlist.docxpkg import Paragraph
from packlist.textrefs import (extract_refs, looks_standard_part, normalize_filename,
                               token_matches_file)


def refs(*texts):
    return extract_refs([Paragraph(i + 1, t, False) for i, t in enumerate(texts)])


# ---------------------------------------------------------------- 인용 표기
@pytest.mark.parametrize("text,number", [
    ("see Fig. 4 for details", 4),
    ("see Fig 4 for details", 4),
    ("see Figure 4 for details", 4),
    ("see Figs. 4 and 5", 4),
    ("see fig. 4", 4),
    ("(Fig. 12)", 12),
    ("as in Fig.4 without space", 4),
    ("as shown in Figure  7", 7),
])
def test_figure_citation_forms(text, number):
    assert refs("Intro paragraph.", text).fig_citations[number] >= 1


def test_double_digit_figure_numbers():
    result = refs("compare Fig. 10 with Fig. 11")
    assert result.fig_citations[10] == 1 and result.fig_citations[11] == 1


def test_caption_label_is_not_counted_as_citation():
    """캡션 머리 라벨은 '본문이 그림을 불렀다'가 아니다."""
    result = refs("Fig. 3. A caption sentence.")
    assert result.fig_citations.get(3, 0) == 0
    assert 3 in result.fig_captions


def test_citation_inside_caption_body_counts():
    result = refs("Fig. 3. Panel layout mirrors Fig. 2 exactly.")
    assert result.fig_citations[2] == 1


def test_repeated_caption_is_flagged_once():
    result = refs("Fig. 2. cap", "body", "Fig. 2. cap")
    assert result.fig_caption_dups == [2]
    assert len(result.fig_captions) == 1


def test_first_order_tracks_body_order():
    result = refs("first Fig. 3", "then Fig. 1")
    assert result.fig_first_order == [3, 1]


# ---------------------------------------------------------------- 보충자료
@pytest.mark.parametrize("text,token", [
    ("see S1 Table", "S1 Table"),
    ("see S12 Table", "S12 Table"),
    ("see Table S1", "S1 Table"),
    ("see Supplementary Table S1", "S1 Table"),
    ("see S5 Fig", "S5 Fig"),
    ("see Fig S5", "S5 Fig"),
    ("see Figure S5", "S5 Fig"),
    ("see Supplementary Fig. S5", "S5 Fig"),
    ("see S2 Data", "S2 Data"),
    ("see S3 Movie", "S3 Movie"),
    ("see Appendix A", "Appendix A"),
    ("see Appendix 2", "Appendix 2"),
    ("see Supplementary Material", "Supplementary Material"),
    ("see Supplementary Methods", "Supplementary Methods"),
])
def test_supplementary_token_forms(text, token):
    assert token in refs("body", text).supp


def test_supplementary_token_counts_mentions():
    result = refs("S1 Table here", "and S1 Table again", "Table S1 once more")
    assert result.supp["S1 Table"].count == 3


def test_supplementary_fig_is_not_a_body_figure():
    """`Fig S3` 은 본문 그림 3번이 아니다 — 여기서 헷갈리면 전부 무너진다."""
    result = refs("body mentions Fig S3 only")
    assert result.fig_citations.get(3, 0) == 0
    assert "S3 Fig" in result.supp


def test_leading_zero_supplementary_number_normalised():
    assert "S3 Table" in refs("body", "see S03 Table").supp


# ---------------------------------------------------------------- 패널
def test_caption_panel_range_expanded():
    result = refs("Fig. 2. Results. (a–c) three panels.")
    assert result.caption_panels[2] == ["a", "b", "c"]


def test_caption_panel_list_collected():
    result = refs("Fig. 2. Results. (a) one. (b) two. (d) four.")
    assert result.caption_panels[2] == ["a", "b", "d"]


def test_body_panel_reference_collected():
    result = refs("see Fig. 2a and Fig. 2c")
    assert sorted(result.body_panels[2]) == ["a", "c"]


# ---------------------------------------------------------------- 표
def test_table_citation_and_caption():
    result = refs("Table 1 lists the sample.", "Table 1. Baseline characteristics.")
    assert result.table_citations[1] == 1
    assert 1 in result.table_captions


def test_table_s_token_not_counted_as_body_table():
    result = refs("see Table S2")
    assert result.table_citations.get(2, 0) == 0


# ---------------------------------------------------------------- 파일명 대조
@pytest.mark.parametrize("token,filename", [
    ("S1 Table", "S1_Table.pdf"),
    ("S1 Table", "s1table.docx"),
    ("S1 Table", "Table S1.xlsx"),
    ("S1 Table", "supp-table-s1.pdf"),
    ("S12 Fig", "S12_Fig.png"),
    ("S3 Fig", "figure_S3.tif"),
    ("Appendix A", "Appendix-A.pdf"),
    ("Supplementary Material", "Supplementary_Material.docx"),
])
def test_token_matches_expected_filename(token, filename):
    assert token_matches_file(token, filename)


@pytest.mark.parametrize("token,filename", [
    ("S1 Table", "S2_Table.pdf"),
    ("S1 Table", "S1_Fig.pdf"),
    ("S1 Fig", "S11_Fig.pdf"),
    ("S3 Fig", "manuscript.docx"),
    ("Appendix A", "Appendix-B.pdf"),
])
def test_token_does_not_match_wrong_filename(token, filename):
    assert not token_matches_file(token, filename)


def test_normalize_filename_strips_separators():
    assert normalize_filename("S1 Table (final)-v2.pdf") == "s1tablefinalv2pdf"


@pytest.mark.parametrize("name", [
    "Cover_Letter.docx", "커버레터.docx", "Highlights.docx",
    "Suggested_Reviewers.docx", "Title_Page.docx", "Response_to_Reviewers.docx",
    "Graphical_Abstract.png", "CONSORT_checklist.pdf", "보충자료.docx",
])
def test_standard_parts_are_recognised(name):
    assert looks_standard_part(name)


@pytest.mark.parametrize("name", [
    "SubjectInfoAdditionalAnaly.pptx", "복사본 (1).xlsx", "old_draft_backup.zip",
])
def test_non_standard_parts_are_not_recognised(name):
    assert not looks_standard_part(name)


def test_empty_paragraphs_are_skipped():
    assert refs("", "   ", "").fig_citation_total == 0


def test_figure_number_zero_and_huge_are_ignored_gracefully():
    result = refs("Fig. 0 and Fig. 999")
    assert result.fig_citations[0] == 1 and result.fig_citations[999] == 1
