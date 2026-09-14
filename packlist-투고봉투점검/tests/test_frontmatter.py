"""표제 3종 — 제목 · 저자순서 · 원고번호. 정확히 셋, 넓히지 않는다."""

import pytest

from packlist.docxpkg import read_docx
from packlist.frontmatter import (FRONTMATTER_FIELDS, MATCH, MISMATCH, UNKNOWN,
                                  compare_frontmatter, read_frontmatter)
from conftest import AUTHORS, TITLE


def test_exactly_three_comparison_fields():
    """항목이 늘어나면 irbpack 재탕이 된다 — 상수로 못 박는다."""
    assert len(FRONTMATTER_FIELDS) == 3
    assert FRONTMATTER_FIELDS == ("제목", "저자순서", "원고번호")


def _pair(envelope, builder, other_paragraphs, ms_paragraphs=None):
    ms = ms_paragraphs or [TITLE, AUTHORS, "Body."]
    builder(envelope / "원고.docx", paragraphs=ms)
    builder(envelope / "기타.docx", paragraphs=other_paragraphs)
    return (read_docx(envelope / "원고.docx", "원고.docx"),
            [read_docx(envelope / "기타.docx", "기타.docx")])


def verdicts(result, field):
    return [c.verdict for checks in result.checks.values() for c in checks if c.field == field]


def test_title_match(envelope, builder):
    manuscript, others = _pair(envelope, builder,
                               ["Dear Editor,", f"our manuscript “{TITLE}” is enclosed."])
    assert verdicts(compare_frontmatter(manuscript, others), "제목") == [MATCH]


def test_title_mismatch_when_tail_differs(envelope, builder):
    """NBR 실사례: 원고에서 뺀 말이 커버레터에 남아 있다."""
    stale = TITLE + " and Norms"
    manuscript, others = _pair(envelope, builder,
                               ["Dear Editor,", f"our manuscript “{stale}” is enclosed."])
    result = compare_frontmatter(manuscript, others)
    assert verdicts(result, "제목") == [MISMATCH]
    assert "Norms" in result.mismatches()[0].found


def test_title_unknown_when_absent(envelope, builder):
    manuscript, others = _pair(envelope, builder, ["Highlights", "bullet one", "bullet two"])
    assert verdicts(compare_frontmatter(manuscript, others), "제목") == [UNKNOWN]


def test_author_order_match(envelope, builder):
    manuscript, others = _pair(envelope, builder, ["Title page", AUTHORS])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [MATCH]


def test_author_order_mismatch(envelope, builder):
    reordered = "Tuomas Eerola, Jiyeon Ha, Hyeon-Joong Kim"
    manuscript, others = _pair(envelope, builder, ["Title page", reordered])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [MISMATCH]


def test_author_order_unknown_when_names_absent(envelope, builder):
    manuscript, others = _pair(envelope, builder, ["Highlights", "no names here at all"])
    assert verdicts(compare_frontmatter(manuscript, others), "저자순서") == [UNKNOWN]


def test_ms_number_match(envelope, builder):
    manuscript, others = _pair(
        envelope, builder,
        ["Cover", "Manuscript ID: NPJDM-2026-0912 enclosed."],
        [TITLE, AUTHORS, "Manuscript ID: NPJDM-2026-0912"])
    assert verdicts(compare_frontmatter(manuscript, others), "원고번호") == [MATCH]


def test_ms_number_mismatch(envelope, builder):
    manuscript, others = _pair(
        envelope, builder,
        ["Cover", "Manuscript ID: NPJDM-2026-0001 enclosed."],
        [TITLE, AUTHORS, "Manuscript ID: NPJDM-2026-0912"])
    assert verdicts(compare_frontmatter(manuscript, others), "원고번호") == [MISMATCH]


def test_ms_number_unknown_when_absent(envelope, builder):
    manuscript, others = _pair(envelope, builder, ["Cover", "no identifier here"])
    assert verdicts(compare_frontmatter(manuscript, others), "원고번호") == [UNKNOWN]


def test_author_affiliation_letters_are_stripped(envelope, builder):
    builder(envelope / "원고.docx",
            paragraphs=[TITLE, "Hyeon-Joong Kim a, Jiyeon Ha a, Tuomas Eerola b", "Body."])
    info = read_docx(envelope / "원고.docx", "원고.docx")
    assert read_frontmatter(info).authors == ["Hyeon-Joong Kim", "Jiyeon Ha", "Tuomas Eerola"]


def test_title_prefers_core_properties(envelope, builder):
    builder(envelope / "원고.docx", paragraphs=["일부러 다른 첫 문단입니다 길게 씁니다", "Body."],
            title=TITLE)
    assert read_frontmatter(read_docx(envelope / "원고.docx", "원고.docx")).title == TITLE


def test_no_other_documents_yields_no_checks(envelope, builder):
    builder(envelope / "원고.docx", paragraphs=[TITLE, AUTHORS, "Body."])
    result = compare_frontmatter(read_docx(envelope / "원고.docx", "원고.docx"), [])
    assert result.checks == {} and result.mismatches() == []
