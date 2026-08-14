"""인용 문구 실존 검증 — 이 툴의 존재 이유 ②.

이 파일의 첫 두 테스트가 revcheck 가 존재하는 이유 그 자체다:
응답서가 "이렇게 고쳤습니다"라며 인용한 문구가 개정본에 없으면 **치명**으로 잡고,
가장 가까운 문장을 일치율과 함께 나란히 보여 준다.
"""

from __future__ import annotations

import pytest

from docx_fixture import p, simple_docx, write_docx
from revcheck.comments import scan_comments
from revcheck.docio import document_from_text, read_document
from revcheck.engine import Options, run_check
from revcheck.model import CRITICAL, WARNING
from revcheck.normalize import norm_compare
from revcheck.quotes import extract_quotes, verify_quotes
from revcheck.textutil import build_candidates

REVISED = (
    "Assuming a between-group difference of 3.0 points on the ISI with an SD of 4.5, "
    "42 participants per arm provide 80% power at a two-sided alpha of 0.05."
)


def _verify(quote_text: str, new_text: str, old_text: str = "irrelevant old text"):
    resp = document_from_text(
        f"Comment 1-1: Q.\nResponse: We revised the Methods: \"{quote_text}\"\n"
        f"Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        f"Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    new = document_from_text(f"## Methods\n\n{new_text}\n", "md", "new.md")
    old = document_from_text(f"## Methods\n\n{old_text}\n", "md", "old.md")
    scan = scan_comments(resp.paras)
    quotes = extract_quotes(scan.comments)
    candidates = build_candidates(new.paras)
    old_norms = [norm_compare(para.text) for para in old.paras]
    return verify_quotes(quotes, new.paras, candidates, old_norms)


# ── 존재 이유 ───────────────────────────────────────────────────────────────


def test_missing_quote_is_critical_with_closest_sentence_and_ratio(trio, run_cli, tmp_path):
    """인용 문구가 개정본에 없으면 치명 + 가장 가까운 문장 + 일치율."""
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    resp = tmp_path / "resp.md"
    old.write_text("## Methods\n\nWe recruited adults with insomnia.\n", encoding="utf-8")
    new.write_text(
        "## Methods\n\nWe recruited adults with insomnia. "
        + REVISED.replace("42 participants", "45 participants")
        + "\n",
        encoding="utf-8",
    )
    resp.write_text(
        f"Comment 1-1: Justify the sample size.\nResponse: We added the calculation: \"{REVISED}\"\n"
        "Comment 1-2: Second point.\nResponse: We have clarified this in the Discussion section.\n"
        "Comment 1-3: Third point.\nResponse: We have clarified this in the Methods section too.\n",
        encoding="utf-8",
    )
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 1
    assert "인용 문구가 개정본의 해당 문장과" in out and "숫자가 다릅니다" in out
    assert "가장 가까운 문장" in out
    assert "45 participants" in out  # 실제 개정본 문구를 나란히 보여 준다
    assert "%" in out  # 일치율


def test_quote_absent_entirely_is_critical():
    scan = _verify(REVISED, "Something completely unrelated about respiration bands.")
    assert [v.status for v in scan.verdicts] == ["없음"]


def test_exact_quote_passes():
    scan = _verify(REVISED, f"Before. {REVISED} After.")
    assert [v.status for v in scan.verdicts] == ["일치"]


def test_wording_only_difference_is_a_warning_not_critical():
    changed = REVISED.replace("provide 80% power", "yield 80% power")
    scan = _verify(REVISED, changed)
    assert scan.verdicts[0].status == "표현불일치"
    assert scan.verdicts[0].ratio >= 0.8


def test_number_difference_is_critical_even_at_high_similarity():
    changed = REVISED.replace("42 participants", "45 participants")
    scan = _verify(REVISED, changed)
    assert scan.verdicts[0].status == "숫자불일치"
    assert scan.verdicts[0].ratio > 0.95  # 표현은 거의 같지만 숫자가 다르다


# ── 정규화 차이만 있는 인용은 '일치' ────────────────────────────────────────


@pytest.mark.parametrize(
    "quoted",
    [
        REVISED,
        REVISED.replace("-", "–"),  # en-dash
        REVISED.replace(" ", " ", 3),  # non-breaking space
        REVISED.replace("Assuming", "assuming"),  # 대소문자
        REVISED.replace(", 42", ",  42"),  # 연속 공백
    ],
)
def test_formatting_only_differences_still_match(quoted):
    scan = _verify(quoted, f"Methods text. {REVISED}")
    assert scan.verdicts[0].status == "일치", quoted


def test_curly_quotes_in_response_are_handled(tmp_path):
    resp = document_from_text(
        "Comment 1-1: Q.\nResponse: We revised it: “" + REVISED + "”\n"
        "Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        "Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    scan = scan_comments(resp.paras)
    quotes = extract_quotes(scan.comments)
    assert any(REVISED[:30].casefold() in q.norm for q in quotes)


def test_docx_run_splitting_does_not_break_quote_matching(tmp_path):
    """워드가 문장을 <w:r> 여러 개로 쪼개 저장해도 인용은 일치해야 한다."""
    old = simple_docx(tmp_path / "old.docx", ["## Methods", "We recruited adults."])
    new = write_docx(
        tmp_path / "new.docx",
        [p("Methods", style="Heading1"), p(f"We recruited adults. {REVISED}", split=11)],
    )
    resp = simple_docx(
        tmp_path / "resp.docx",
        [
            "Comment 1-1: Justify the sample size.",
            f'Response: We added the following sentence: "{REVISED}"',
            "Comment 1-2: Second point.",
            "Response: We have clarified this in the Discussion section as requested.",
            "Comment 1-3: Third point.",
            "Response: We have clarified this in the Methods section as well, thank you.",
        ],
    )
    result = run_check(
        read_document(old, "제출본"),
        read_document(new, "개정본"),
        read_document(resp, "응답서"),
        Options(),
    )
    assert not result.criticals, [f.message for f in result.criticals]


def test_hard_wrapped_quote_across_lines_is_still_found(tmp_path):
    """.md 응답서는 인용문이 여러 줄로 접혀 있다 — 줄 단위로 찾으면 놓친다."""
    wrapped = REVISED.replace("with an SD of 4.5,", "with an SD of 4.5,\n")
    resp = document_from_text(
        f"Comment 1-1: Q.\nResponse: We revised it: \"{wrapped}\"\n"
        "Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        "Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    scan = scan_comments(resp.paras)
    quotes = [q for q in extract_quotes(scan.comments) if not q.skipped]
    assert quotes and quotes[0].norm == norm_compare(REVISED)


# ── 검사에서 빼는 것과 그 자백 ──────────────────────────────────────────────


def test_short_quotes_are_skipped_with_a_reason():
    scan = _verify("Table 2", "The dropout rate is shown in Table 2.")
    assert scan.checked == 0
    assert "15자 미만" in scan.skipped


def test_abridged_quotes_are_skipped():
    scan = _verify(
        "Assuming a between-group difference [...] at a two-sided alpha of 0.05.",
        REVISED,
    )
    assert scan.checked == 0
    assert "생략부호 포함" in scan.skipped


def test_quote_that_only_exists_in_the_old_manuscript_is_a_warning_not_critical():
    """개정 전 문장을 인용한 경우 — 치명이 아니라 경고로 남기고, 숨기지도 않는다."""
    original = "The device is safe and effective for all patients with insomnia."
    scan = _verify(original, "The device was well tolerated in this trial.", original)
    assert [v.status for v in scan.verdicts] == ["제출본문구"]
    assert scan.verdicts[0].in_old is True


def test_reviewer_voice_quote_is_skipped():
    """'The reviewer asks for "..."' 는 개정 후 문구가 아니다."""
    from revcheck.comments import scan_comments
    from revcheck.quotes import extract_quotes

    resp = document_from_text(
        'Comment 1-1: Q.\nResponse: The reviewer asks for "the number of completers by arm '
        'rather than the pooled figure"; we have added these counts to Results.\n'
        "Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        "Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    quotes = extract_quotes(scan_comments(resp.paras).comments)
    assert [q.skipped for q in quotes] == ["리뷰어 말 인용"]


def test_quote_inside_one_table_cell_is_still_checked():
    """응답서를 두 칸짜리 표로 쓰는 저널이 많다 — 셀 안의 인용은 대조해야 한다."""
    from revcheck.comments import scan_comments
    from revcheck.quotes import extract_quotes

    rows = "\n".join(
        f'| Comment 1-{n}: A point. | Response: We revised it to read: "{REVISED}" |'
        for n in (1, 2, 3)
    )
    resp = document_from_text(rows + "\n", "md", "resp.md")
    quotes = [q for q in extract_quotes(scan_comments(resp.paras).comments) if not q.skipped]
    assert quotes, "표 셀 안의 인용을 통째로 버리면 이 툴의 핵심 검사가 죽는다"


def test_revised_text_lead_without_quotes_is_captured():
    scan = _verify_lead()
    assert scan.verdicts and scan.verdicts[0].status == "일치"


def _verify_lead():
    resp = document_from_text(
        f"Comment 1-1: Q.\nRevised text: {REVISED}\n"
        "Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        "Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    new = document_from_text(f"## Methods\n\n{REVISED}\n", "md", "new.md")
    old = document_from_text("## Methods\n\nOld text.\n", "md", "old.md")
    scan = scan_comments(resp.paras)
    quotes = extract_quotes(scan.comments)
    return verify_quotes(
        quotes, new.paras, build_candidates(new.paras),
        [norm_compare(para.text) for para in old.paras],
    )


def test_abridged_quote_is_a_warning_not_a_critical():
    """저자가 (SD …) 를 빼고 인용하는 것은 흔하다 — 치명으로 잡으면 소음이다."""
    full = (
        "The mean ISI decreased by 5.2 points (SD 3.1) in the active arm and by "
        "2.1 points (SD 2.8) in the sham arm, giving a between-arm difference of 3.1 points."
    )
    abridged = (
        "The mean ISI decreased by 5.2 points in the active arm and by 2.1 points "
        "in the sham arm"
    )
    scan = _verify(abridged, full)
    assert scan.verdicts[0].status == "축약인용"


def test_abridged_quote_with_a_conflicting_number_is_still_critical():
    full = (
        "The mean ISI decreased by 5.2 points (SD 3.1) in the active arm and by "
        "2.1 points (SD 2.8) in the sham arm, giving a between-arm difference of 3.1 points."
    )
    wrong = (
        "The mean ISI decreased by 5.9 points in the active arm and by 2.1 points "
        "in the sham arm"
    )
    scan = _verify(wrong, full)
    assert scan.verdicts[0].status == "숫자불일치"


def test_reviewer_voice_followed_by_a_revision_lead_in_is_still_checked():
    """'As the reviewer suggested, the sentence now reads: "…"' 는 개정 후 문구다."""
    from revcheck.quotes import extract_quotes

    resp = document_from_text(
        f'Comment 1-1: Q.\nResponse: As the reviewer suggested, the sentence now reads: "{REVISED}"\n'
        "Comment 1-2: Q.\nResponse: A sufficiently long second response body here.\n"
        "Comment 1-3: Q.\nResponse: A sufficiently long third response body here.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    quotes = [q for q in extract_quotes(scan_comments(resp.paras).comments) if not q.skipped]
    assert quotes and quotes[0].norm == norm_compare(REVISED)
