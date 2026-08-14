"""코멘트 번호 전수 점검 — 이 툴의 존재 이유 ①."""

from __future__ import annotations

import pytest

from revcheck.comments import (
    MIN_COMMENTS,
    parse_comment_ids,
    scan_comments,
)
from revcheck.docio import document_from_text

RESPONSE_BODY = (
    "We thank the reviewer for this helpful comment and have revised the manuscript "
    "accordingly in the Methods section."
)


def scan(text: str, ids=None):
    doc = document_from_text(text, "md", "resp.md", split_lines=True)
    return scan_comments(doc.paras, ids)


# ── 번호 체계 5종 이상 ──────────────────────────────────────────────────────

SCHEMES = {
    "Reviewer N, Comment M": "\n".join(
        f"Reviewer 1, Comment {n}: A question.\nResponse: {RESPONSE_BODY}"
        for n in (1, 2, 3)
    ),
    "RN-M": "\n".join(
        f"R1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)
    ),
    "Comment N-M": "\n".join(
        f"Comment 1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)
    ),
    "N-M:": "\n".join(
        f"1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)
    ),
    "Comment N:": "Reviewer 1\n"
    + "\n".join(
        f"Comment {n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)
    ),
    "N)": "Reviewer 2\n"
    + "\n".join(f"{n}) A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)),
    "심사위원 N - M": "\n".join(
        f"심사위원 1 - {n}: 표본수 산출 근거가 불분명합니다.\n답변: 방법 절에 검정력 계산을 추가했습니다. "
        f"자세한 내용은 통계 분석 항목을 참고해 주십시오."
        for n in (1, 2, 3)
    ),
}


@pytest.mark.parametrize("name,text", sorted(SCHEMES.items()))
def test_five_plus_numbering_schemes_are_parsed(name, text):
    result = scan(text)
    assert result.ok, result.undecidable
    assert len(result.comments) == 3, f"{name}: {[c.label for c in result.comments]}"
    assert not result.missing


def test_editor_block_is_recognised():
    text = (
        "Editor\n"
        "Comment 1: Please shorten the abstract.\n"
        f"Response: {RESPONSE_BODY}\n"
        "Comment 2: Please add a data availability statement.\n"
        f"Response: {RESPONSE_BODY}\n"
        "Reviewer 1\n"
        "Comment 1: Clarify the sample size.\n"
        f"Response: {RESPONSE_BODY}\n"
    )
    result = scan(text)
    assert result.ok
    assert {c.label for c in result.comments} == {"Editor-1", "Editor-2", "1-1"}


# ── 번호 건너뜀 · 중복 · 빈 응답 ───────────────────────────────────────────


def test_missing_comment_number_is_caught():
    text = "\n".join(
        f"Comment 2-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (3, 4, 6)
    )
    result = scan(text)
    assert result.ok
    gaps = {(rev, num) for rev, num, _why in result.missing}
    # 가운데 구멍(2-5)과, 1 부터 시작하지 않아 앞이 빈 2-1·2-2 를 모두 잡는다.
    assert gaps == {("R2", 1), ("R2", 2), ("R2", 5)}
    why = [w for rev, num, w in result.missing if num == 5][0]
    assert "2-4" in why and "2-6" in why


def test_leading_comment_number_is_not_invented_when_numbering_starts_high():
    """번호가 30 부터 시작하는 통합 번호 체계에서 1~29 를 지어내면 안 된다."""
    text = "\n".join(
        f"Comment 1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (30, 31, 32)
    )
    result = scan(text)
    assert result.missing == []


def test_typo_number_does_not_invent_hundreds_of_missing_comments():
    """``Comment 1-999`` 오타 하나로 없는 코멘트 996건을 지어내면 리포트가 죽는다."""
    text = "\n".join(
        f"Comment 1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 999)
    )
    result = scan(text)
    assert result.missing == []
    assert result.wild_gaps and result.wild_gaps[0][0] == "R1"


def test_block_header_without_any_numbered_comment_is_confessed():
    """번호 없이 산문으로 쓴 Editor 지시문을 조용히 버리면 안 된다."""
    text = (
        "Editor\n"
        "Please shorten the abstract to 250 words and add a data availability statement.\n"
        "Reviewer 1\n"
        + "\n".join(
            f"Comment {n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3)
        )
    )
    result = scan(text)
    assert result.ok
    assert result.silent_blocks == ["Editor"]


def test_multiple_gaps_across_reviewers():
    text = "\n".join(
        f"Comment {rev}-{n}: A question.\nResponse: {RESPONSE_BODY}"
        for rev, n in [(1, 1), (1, 3), (2, 1), (2, 2), (2, 5)]
    )
    result = scan(text)
    gaps = {(rev, num) for rev, num, _why in result.missing}
    assert gaps == {("R1", 2), ("R2", 3), ("R2", 4)}


def test_duplicate_number_is_reported():
    text = "\n".join(
        f"Comment 1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 2, 3)
    )
    result = scan(text)
    assert result.duplicates == [("R1", 2)]


def test_thin_response_body_is_flagged():
    text = (
        f"Comment 1-1: A question.\nResponse: {RESPONSE_BODY}\n"
        "Comment 1-2: Another question.\nResponse: Done.\n"
        f"Comment 1-3: A third question.\nResponse: {RESPONSE_BODY}\n"
    )
    result = scan(text)
    assert [c.label for c in result.thin] == ["1-2"]


def test_body_excludes_the_reviewer_comment_text():
    text = (
        "Comment 1-1: The sentence 'the device is safe' is unsupported.\n"
        f"Response: {RESPONSE_BODY}\n"
        f"Comment 1-2: Second.\nResponse: {RESPONSE_BODY}\n"
        f"Comment 1-3: Third.\nResponse: {RESPONSE_BODY}\n"
    )
    result = scan(text)
    first = result.comments[0]
    assert "unsupported" not in first.body
    assert first.body.startswith("We thank")


# ── 판정불가 ────────────────────────────────────────────────────────────────


def test_unparsable_response_stops_with_undecidable():
    text = (
        "Dear Editor,\n\nWe have addressed all reviewer concerns in the revised "
        "manuscript. Thank you for your consideration.\n"
    )
    result = scan(text)
    assert not result.ok
    assert "--comments" in result.undecidable


def test_fewer_than_minimum_comments_is_undecidable():
    text = "\n".join(
        f"Comment 1-{n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2)
    )
    result = scan(text)
    assert not result.ok
    assert str(MIN_COMMENTS) in result.undecidable


def test_enumeration_inside_a_response_is_not_mistaken_for_comments():
    """``1) 2) 5) 9)`` 처럼 리뷰어 번호로 볼 수 없는 목록은 채택하지 않는다."""
    text = (
        "Reviewer 1\n"
        "We restructured the Methods as follows:\n"
        "3) first item of a list\n"
        "7) second item of a list\n"
        "9) third item of a list\n"
    )
    result = scan(text)
    assert not result.ok


# ── 직접 지정 모드 ──────────────────────────────────────────────────────────


def test_parse_comment_ids():
    assert parse_comment_ids("1-1, 1-2 ,2-3,E-1") == [
        ("R1", 1), ("R1", 2), ("R2", 3), ("Editor", 1)
    ]


def test_parse_comment_ids_rejects_garbage():
    with pytest.raises(ValueError):
        parse_comment_ids("첫번째")


def test_manual_ids_find_missing_comment():
    text = (
        f"Comment 1-1: A question.\nResponse: {RESPONSE_BODY}\n"
        f"Comment 1-3: A question.\nResponse: {RESPONSE_BODY}\n"
    )
    result = scan(text, ids=parse_comment_ids("1-1,1-2,1-3"))
    assert result.ok
    assert result.not_found == ["1-2"]
    assert len(result.comments) == 2


def test_numbering_continued_across_reviewers_does_not_invent_missing_comments():
    """저널에 따라 리뷰어를 가로질러 번호를 이어 매긴다(R1 1~3, R2 4~6)."""
    text = (
        "Reviewer 1\n"
        + "\n".join(f"Comment {n}: A question.\nResponse: {RESPONSE_BODY}" for n in (1, 2, 3))
        + "\nReviewer 2\n"
        + "\n".join(f"Comment {n}: A question.\nResponse: {RESPONSE_BODY}" for n in (4, 5, 6))
    )
    result = scan(text)
    assert result.ok
    assert result.missing == []


def test_prose_line_mentioning_a_reviewer_is_not_a_block_header():
    """'Reviewer 2 raised the same point, …' 를 머리로 오인하면 뒤 인용이 잘려 나간다."""
    text = (
        f"Comment 1-1: A question.\nResponse: {RESPONSE_BODY}\n"
        "Comment 1-2: Another question.\n"
        "Response: Reviewer 2 raised the same point, so we answer both here. "
        'The Results now read: "Of the 84 randomised participants, 71 completed the assessment."\n'
        f"Comment 1-3: A third question.\nResponse: {RESPONSE_BODY}\n"
    )
    result = scan(text)
    assert result.ok
    second = [c for c in result.comments if c.label == "1-2"][0]
    assert "71 completed" in second.body
