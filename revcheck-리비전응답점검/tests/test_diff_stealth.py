"""문단 diff 와 미신고 변경 등급."""

from __future__ import annotations

from revcheck.diffpair import diff_documents
from revcheck.docio import document_from_text
from revcheck.engine import Options, run_check
from revcheck.model import CRITICAL, INFO, WARNING
from revcheck.report import render_text

# 변경 주장 단어("revised")를 일부러 넣는다 — 제출본 오첨부 사고는
# "고쳤다고 했는데 본문이 그대로"일 때만 성립하기 때문이다.
BODY = "We have revised the manuscript accordingly, as described in the paragraph below."


def _resp(*blocks: str) -> str:
    """코멘트 3건짜리 응답서를 만든다(추가 문장은 그대로 이어 붙는다)."""
    base = [
        f"Comment 1-1: First point.\nResponse: {BODY}",
        f"Comment 1-2: Second point.\nResponse: {BODY}",
        f"Comment 1-3: Third point.\nResponse: {BODY}",
    ]
    return "\n".join(list(blocks) + base) + "\n"


def _run(old_text: str, new_text: str, resp_text: str):
    return run_check(
        document_from_text(old_text, "md", "old.md"),
        document_from_text(new_text, "md", "new.md"),
        document_from_text(resp_text, "md", "resp.md", split_lines=True),
        Options(),
    )


OLD = """# T

## Results

The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.

## Discussion

The observed improvement is consistent with earlier reports of paced breathing.
"""


def test_undeclared_number_change_is_critical():
    new = OLD.replace("5.2 points (SD 3.1)", "5.8 points (SD 3.4)")
    result = _run(OLD, new, _resp())
    stealth = [f for f in result.criticals if f.kind == "미신고변경"]
    assert len(stealth) == 1
    assert "숫자가 다른 값으로 바뀌었습니다" in stealth[0].message
    assert "5.2" in " ".join(stealth[0].detail) and "5.8" in " ".join(stealth[0].detail)


def test_undeclared_wording_change_outside_results_is_only_info():
    new = OLD.replace(
        "The observed improvement is consistent with earlier reports of paced breathing.",
        "The observed improvement is broadly consistent with earlier reports of paced breathing.",
    )
    result = _run(OLD, new, _resp())
    kinds = [(f.severity, f.kind) for f in result.findings if f.kind == "미신고변경"]
    assert kinds == [(INFO, "미신고변경")]


def test_undeclared_wording_change_in_results_is_a_warning():
    new = OLD.replace(
        "The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.",
        "The mean ISI fell by 5.2 points (SD 3.1) in the active arm.",
    )
    result = _run(OLD, new, _resp())
    stealth = [f for f in result.findings if f.kind == "미신고변경"]
    assert [f.severity for f in stealth] == [WARNING]


def test_change_declared_by_a_quote_is_not_reported():
    sentence = "The mean ISI decreased by 5.8 points (SD 3.4) in the active arm."
    new = OLD.replace("The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.", sentence)
    result = _run(OLD, new, _resp(f'Comment 2-1: Recheck.\nResponse: Reanalysed: "{sentence}"'))
    assert not [f for f in result.findings if f.kind == "미신고변경"]


def test_change_declared_by_pasted_sentence_without_quotes():
    sentence = "The mean ISI decreased by 5.8 points (SD 3.4) in the active arm."
    new = OLD.replace("The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.", sentence)
    result = _run(OLD, new, _resp(f"Comment 2-1: Recheck.\nResponse: It now reads {sentence}"))
    assert not [f for f in result.findings if f.kind == "미신고변경"]


def test_change_declared_by_a_verified_line_reference():
    """줄 번호가 실재하는 .md 에서는 위치 참조만으로도 신고로 인정한다."""
    new = OLD.replace(
        "The observed improvement is consistent with earlier reports of paced breathing.",
        "The observed improvement is consistent with earlier reports of paced breathing in adults.",
    )
    doc = document_from_text(new, "md", "new.md")
    target = [p for p in doc.paras if "paced breathing" in p.text][0]
    result = _run(
        OLD,
        new,
        _resp(f"Comment 2-1: Please soften this claim.\nResponse: Revised at line {target.line_start}."),
    )
    assert not [f for f in result.findings if f.kind == "미신고변경"]


# ── 제출본 오첨부 사고 ──────────────────────────────────────────────────────


def test_identical_old_and_new_with_change_claims_is_critical():
    result = _run(OLD, OLD, _resp())
    kinds = [f.kind for f in result.criticals]
    assert "원고오첨부" in kinds


def test_identical_old_and_new_without_claims_is_not_critical():
    quiet = "\n".join(
        f"Comment 1-{n}: A point.\nResponse: We respectfully disagree, and explain our reasoning "
        f"in the paragraph below without altering the manuscript."
        for n in (1, 2, 3)
    )
    result = _run(OLD, OLD, quiet + "\n")
    assert not [f for f in result.criticals if f.kind == "원고오첨부"]


# ── 전면 재작성 (변경률 60% 초과) ──────────────────────────────────────────


def _big_pair(n_paras: int = 12, n_changed: int = 10):
    old_paras = [
        f"Paragraph {i} of the discussion states that the observed value was {i}.0 units."
        for i in range(n_paras)
    ]
    new_paras = list(old_paras)
    for i in range(n_changed):
        # 있던 문단의 **숫자만** 조용히 바뀐 경우 — 이 툴이 가장 비싸게 보는 사고.
        new_paras[i] = old_paras[i].replace(f"{i}.0 units", f"{i}.5 units")
    head = "# T\n\n## Discussion\n\n"
    return head + "\n\n".join(old_paras) + "\n", head + "\n\n".join(new_paras) + "\n"


def test_full_rewrite_collapses_the_stealth_list_to_a_summary():
    old, new = _big_pair()
    result = _run(old, new, _resp())
    listed = [f for f in result.findings if f.kind == "미신고변경"]
    summary = [f for f in result.findings if f.kind == "미신고변경요약"]
    assert summary, "요약 한 줄로 강등하고 그 사실을 명시해야 한다"
    assert "전면 재작성" in summary[0].message
    assert len(listed) <= 5, f"{len(listed)}건을 개별 나열했다 — 목록이 리포트를 삼킨다"
    text = render_text(result, result.exit_code())
    assert len(text.splitlines()) < 120, "리포트가 300줄을 쏟으면 아무도 안 읽는다"


def test_moderate_change_rate_still_lists_number_changes_individually():
    old, new = _big_pair(n_paras=12, n_changed=5)  # 약 42% — 요약 모드
    result = _run(old, new, _resp())
    listed = [f for f in result.findings if f.kind == "미신고변경"]
    assert 1 <= len(listed) <= 20
    assert all(f.severity == CRITICAL for f in listed)


# ── diff 자체 ───────────────────────────────────────────────────────────────


def test_appended_sentence_counts_as_one_change_not_two():
    old = "# T\n\n## Methods\n\nWe randomised participants 1:1 using sealed envelopes.\n"
    new = (
        "# T\n\n## Methods\n\nWe randomised participants 1:1 using sealed envelopes. "
        "Allocation was concealed until after baseline assessment was complete.\n"
    )
    diff = diff_documents(
        document_from_text(old, "md", "o.md"), document_from_text(new, "md", "n.md")
    )
    assert [c.kind for c in diff.changes] == ["변경"]


def test_reference_section_is_excluded_from_the_diff():
    old = "# T\n\n## References\n\n1. Kim H. A paper. J Sleep. 2019.\n"
    new = "# T\n\n## References\n\n1. Kim H. A paper. J Sleep. 2019.\n2. Lee S. Another. Sleep. 2021.\n"
    diff = diff_documents(
        document_from_text(old, "md", "o.md"), document_from_text(new, "md", "n.md")
    )
    assert diff.changes == []


def test_added_paragraph_with_numbers_is_not_a_fabrication_accusation():
    """리뷰어가 요청한 한계 문단을 새로 쓰면 그 안에 숫자가 있는 게 당연하다.

    여기에 치명 + '데이터 조작 의심' 을 붙이면 정상 리비전마다 치명이 뜨고,
    이 툴은 두 번 다시 열리지 않는다.
    """
    new = OLD.replace(
        "The observed improvement is consistent with earlier reports of paced breathing.",
        "The observed improvement is consistent with earlier reports of paced breathing.\n\n"
        "This study has limitations. Follow-up was limited to 8 weeks and the 84 participants "
        "were recruited from two urban clinics.",
    )
    result = _run(OLD, new, _resp())
    assert not [f for f in result.criticals if f.kind == "미신고변경"]
    stealth = [f for f in result.findings if f.kind == "미신고변경"]
    assert stealth and stealth[0].severity in (INFO, WARNING)


def test_citation_renumbering_is_not_a_number_change():
    """참고문헌을 한 편 넣으면 [5] 이후가 전부 밀린다 — 정상 리비전이다."""
    old = "# T\n\n## Discussion\n\nSleep disorders are common in adults [5].\n"
    new = "# T\n\n## Discussion\n\nSleep disorders are common in adults [6].\n"
    result = _run(old, new, _resp())
    assert not result.criticals, [f.message for f in result.criticals]


def test_table_label_change_is_not_a_number_change():
    old = "# T\n\n## Results\n\nOutcomes are summarised in Table 2.\n"
    new = "# T\n\n## Results\n\nOutcomes are summarised in Table 3.\n"
    result = _run(old, new, _resp())
    assert not result.criticals, [f.message for f in result.criticals]


def test_silently_changed_number_in_an_existing_table_row_stays_critical():
    """표 번호를 언급했다는 이유로 표 안 숫자 변경을 면제하면 안 된다."""
    old = (
        "# T\n\n## Results\n\nCompleters are shown in Table 2.\n\n"
        "| Arm | Completed |\n| Active | 40 |\n| Sham | 38 |\n"
    )
    new = old.replace("| Sham | 38 |", "| Sham | 31 |")
    resp = _resp("Comment 2-1: Report completers.\nResponse: We added Table 2 with completers by arm.")
    result = _run(old, new, resp)
    assert [f.kind for f in result.criticals] == ["미신고변경"]


def test_deletion_is_not_absorbed_across_sections():
    """초록에서 조용히 지운 숫자가, 결과의 신고된 변경에 먹혀 사라지면 안 된다."""
    old = (
        "# T\n\n## Abstract\n\nWe enrolled 84 participants. "
        "Adverse events occurred in 6 participants (7.1%).\n\n"
        "## Results\n\nAdverse events occurred in 6 participants (7.1%).\n"
    )
    new = (
        "# T\n\n## Abstract\n\nWe enrolled 84 participants.\n\n"
        "## Results\n\nAdverse events occurred in 5 participants (6.0%), all resolving "
        "without treatment.\n"
    )
    resp = _resp(
        "Comment 2-1: Recheck adverse events.\nResponse: Corrected: "
        '"Adverse events occurred in 5 participants (6.0%), all resolving without treatment."'
    )
    result = _run(old, new, resp)
    abstract = [f for f in result.findings if "Abstract" in f.target]
    assert abstract, "초록의 미신고 변경이 사라졌다"


def test_line_reference_pointing_at_an_unchanged_line_is_a_warning():
    """위치 참조 검증이 늘 '일치'라고 하면 이 검사는 없는 것과 같다."""
    new = OLD.replace(
        "The observed improvement is consistent with earlier reports of paced breathing.",
        "The observed improvement is consistent with earlier reports of paced breathing in adults.",
    )
    result = _run(
        OLD,
        new,
        _resp("Comment 2-1: Please soften this claim.\nResponse: Revised at line 3."),
    )
    warns = [f for f in result.warnings if f.kind == "위치참조오류"]
    assert warns and "바뀌지 않았습니다" in warns[0].message


def test_reference_only_revision_is_not_a_wrong_file_accusation():
    """참고문헌만 손본 리비전에 '제출본을 첨부했다'고 하면 오탐이다."""
    old = "# T\n\n## Discussion\n\nThe effect is modest.\n\n## References\n\n1. Kim H. A paper. J Sleep. 2019.\n"
    new = old + "2. Lee S. Another paper. Sleep. 2021.\n"
    result = _run(old, new, _resp())
    assert not [f for f in result.criticals if f.kind == "원고오첨부"]


# ── 라운드 2 회귀: 무엇이 치명이고 무엇이 아닌가 ──────────────────────────


def test_number_that_only_gets_added_is_not_a_critical():
    """리뷰어 요청으로 한계 문장을 덧붙이면 그 안에 숫자가 있는 게 당연하다."""
    new = OLD.replace(
        "The observed improvement is consistent with earlier reports of paced breathing.",
        "The observed improvement is consistent with earlier reports of paced breathing. "
        "Follow-up was limited to 8 weeks and the 84 participants came from two urban clinics.",
    )
    result = _run(OLD, new, _resp())
    assert not result.criticals, [f.message for f in result.criticals]


def test_replaced_value_is_still_critical_even_when_the_paragraph_is_mentioned():
    """'문장을 다듬었습니다(줄 N)' 한 줄로 5.2 → 5.9 를 덮을 수 없어야 한다."""
    new = OLD.replace("5.2 points (SD 3.1)", "5.9 points (SD 3.6)")
    doc = document_from_text(new, "md", "new.md")
    target = [p for p in doc.paras if "mean ISI" in p.text][0]
    result = _run(
        OLD,
        new,
        _resp(
            f"Comment 2-1: Please polish the wording.\nResponse: We edited the wording of the "
            f"primary outcome paragraph (lines {target.line_start}-{target.line_start}) for readability."
        ),
    )
    assert [f.kind for f in result.criticals] == ["미신고변경"]


def test_deleted_results_paragraph_with_numbers_is_critical():
    new = OLD.replace(
        "The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.\n\n", ""
    )
    result = _run(OLD, new, _resp())
    assert [f.kind for f in result.criticals] == ["미신고변경"]


def test_author_year_citation_added_is_not_a_number_change():
    old = "# T\n\n## Introduction\n\nSleep disturbance is common (Smith & Jones, 2018).\n"
    new = "# T\n\n## Introduction\n\nSleep disturbance is common (Smith & Jones, 2018; Cho et al., 2022).\n"
    result = _run(old, new, _resp())
    assert not result.criticals, [f.message for f in result.criticals]


def test_median_iqr_change_is_still_a_number_change():
    """``22 [14-31]`` 의 대괄호는 인용 번호가 아니라 사분위범위다."""
    old = "# T\n\n## Results\n\nMedian sleep-onset latency was 22 [14-31] minutes.\n"
    new = "# T\n\n## Results\n\nMedian sleep-onset latency was 22 [5-60] minutes.\n"
    result = _run(old, new, _resp())
    assert [f.kind for f in result.criticals] == ["미신고변경"]


def test_reference_only_revision_in_tex_is_not_a_wrong_file_accusation():
    """.tex 참고문헌 목록이 문단 하나로 뭉치면 두 검사가 동시에 눈이 먼다."""
    old = (
        "\\section{Discussion}\nThe effect is modest.\n\n\\section{References}\n"
        "1. Kim H. A paper. J Sleep. 2019;28:11-19.\n2. Lee S. Another. Sleep. 2021;44:e1.\n"
    )
    new = old + "3. Cho Y. New one. Diabetologia. 2022;65:77-88.\n"
    result = run_check(
        document_from_text(old, "tex", "old.tex"),
        document_from_text(new, "tex", "new.tex"),
        document_from_text(_resp(), "md", "resp.md", split_lines=True),
        Options(),
    )
    assert not [f for f in result.criticals if f.kind == "원고오첨부"]
