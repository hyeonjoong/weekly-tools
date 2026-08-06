"""적대적 검토(2026-08-06)에서 나온 결함들의 회귀 테스트.

여기 있는 테스트는 전부 "실제로 잡혔던 문제"에 1:1로 대응한다. HARDENING.md 참고.
"""

from __future__ import annotations

import csv
import time
import zipfile

import pytest
from conftest import EXAMPLES, analyse, find

from draftcheck.checks import CRITICAL, INFO, WARNING, count_words, josa, sentences
from draftcheck.cli import EXIT_ERROR, EXIT_OK, main
from draftcheck.docio import ManuscriptError, caption_of, heading_key, read_manuscript
from draftcheck.report import (
    ISSUES_CSV,
    REFERENCES_CSV,
    REPORT_MD,
    console_report,
    csv_safe,
    display_width,
    markdown_report,
    sanitize,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write(tmp_path, text, name="m.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def doc(body, refs="1. Kim H. Title. J Test. 2024.\n", extra=""):
    return (
        "Paper title here\n\n## Abstract\nSummary text [1].\n\n"
        "## Introduction\n" + body + "\n\n"
        "## References\n" + refs + extra
    )


# ── 제목에 붙은 메모 때문에 참고문헌 섹션을 통째로 놓치던 문제 ──────────────
# 사용자의 진짜 원고에서 관찰된 형태들. 이걸 놓치면 이 툴의 대표 점검이 죽는다.


@pytest.mark.parametrize(
    "line,expected",
    [
        ("References (Vancouver style; 번호 유지, 최종 정리 필요)", "references"),
        ("Introduction / ref26 to be deleted later", "introduction"),
        ("Results (Primary Endpoints)", "results"),
        ("Methods (Measurements)", "methods"),
        ("Discussion 가설이 많아서 어렵지만 굵직한 것만 적음", "discussion"),
        ("참고문헌 (정리 중)", "references"),
        ("\\begin{thebibliography}{9}", "references"),
        # 문장을 제목으로 오인하면 원고가 엉뚱하게 잘린다
        ("Results (Table 2) are shown below.", None),
        ("Methods (see Appendix) were adapted from Smith and colleagues.", None),
    ],
)
def test_annotated_headings(line, expected):
    assert heading_key(line) == expected


def test_annotated_reference_heading_enables_the_cross_check(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1] and [3].\n\n"
        "## References (Vancouver style; 정리 필요)\n"
        "1. Kim H. First. J Test. 2024.\n"
        "2. Lee S. Never cited. J Test. 2024.\n",
    )
    result = analyse(path)
    assert not result.blockers
    assert find(result, "인용누락", "[3]")
    assert find(result, "미인용문헌")


# ── 구조화 초록이 초록을 0줄로 만들어 조용히 통과하던 문제 ──────────────────


def test_structured_abstract_labels_do_not_swallow_the_abstract(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\n"
        "### Background\nInsomnia is common [1].\n"
        "### Methods\nWe randomized 48 participants.\n"
        "### Results\nSleep improved.\n\n"
        "## Introduction\nProse [1].\n\n"
        "## Methods\nWe analysed 45 participants.\n\n"
        "## Results\nIt worked.\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    result = analyse(path)
    assert result.counts["abstract_words"] > 10
    assert [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]


def test_an_abstract_we_could_not_read_is_reported_not_counted_as_zero(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\n## Introduction\nProse citing [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    result = analyse(path)
    assert any("초록 제목은 찾았지만" in note for note in result.coverage)


# ── 캡션 서식(Springer/Nature)과 본문 문장 구분 ─────────────────────────────


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Table 1 Baseline characteristics", ("table", 1)),
        ("Figure 1 | Flow diagram", ("figure", 1)),
        ("Table 2 | Secondary outcomes", ("table", 2)),
        ("그림 3 야간 심박변이도", ("figure", 3)),
        # 본문 문장은 캡션이 아니다 (소문자로 이어짐)
        ("Table 1 shows the baseline characteristics of the sample.", None),
        ("Figure 2 and Figure 3 show the change scores.", None),
    ],
)
def test_caption_styles(line, expected):
    assert caption_of(line) == expected


# ── 한글 '표'가 들어간 낱말이 표 번호로 잡히던 문제 ─────────────────────────


def test_korean_words_ending_in_pyo_are_not_table_mentions(tmp_path):
    path = write(
        tmp_path,
        "제목\n\n## 서론\n"
        "주요 지표 3개를 사전에 정의하였다. 결과는 학회에서 발표 2회에 걸쳐 보고하였다 [1].\n"
        "목표 5개 중 대표 2개를 제시한다. 표 1에 기저 특성을 정리하였다.\n\n"
        "## 참고문헌\n1. Kim H. T. J Test. 2024.\n\n"
        "## 표\n**표 1.** 기저 특성.\n",
    )
    result = analyse(path)
    assert not find(result, "그림표"), [f.message for f in find(result, "그림표")]


def test_latex_tilde_reference_is_a_mention(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n\\section{Introduction}\n"
        "See Figure~1 and Table~2 \\cite{kim2024}.\n\n"
        "\\section{References}\n\\bibitem{kim2024} Kim H. T. J Test. 2024.\n\n"
        "\\section{Figure legends}\nFigure 1. Flow.\n\nTable 2. Outcomes.\n",
        name="m.tex",
    )
    result = analyse(path)
    assert not [f for f in find(result, "그림표") if "언급되지 않았" in f.message]


def test_roman_numeral_tables_match_their_mentions(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nAs shown in Table I and Table II [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Tables\n**Table I.** First.\n\n**Table II.** Second.\n",
    )
    assert not [f for f in find(analyse(path), "그림표") if "언급되지 않았" in f.message]


def test_front_matter_counts_are_not_mentions(tmp_path):
    """'Figures 5; tables 2' 같은 계수 줄 하나가 경고 5건을 만들던 문제."""
    path = write(
        tmp_path,
        "Title\n\n## Introduction\n"
        "Word count 3500; Figures 5; tables 2; supplementary files 17.\n"
        "See Figure 1 and Table 1 [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Figure legends\n**Figure 1.** Flow.\n\n**Table 1.** Baseline.\n",
    )
    assert not find(analyse(path), "그림표")


def test_plural_mention_with_two_numbers_still_counts(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nSee Figures 1 and 2 [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Figure legends\n**Figure 1.** A.\n\n**Figure 2.** B.\n",
    )
    assert not find(analyse(path), "그림표")


# ── 표본수: 문장 끝의 마침표와 선별 인원 ────────────────────────────────────


def test_sample_size_at_the_end_of_a_sentence(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\nWe randomized 45 participants (N = 45).\n\n"
        "## Methods\nThe number of randomized participants was N = 45.\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    assert not [f for f in find(analyse(path), "숫자불일치") if f.severity == CRITICAL]


def test_screening_counts_in_the_abstract_are_not_sample_sizes(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\nWe screened 60 patients and randomized 45 participants [1].\n\n"
        "## Methods\nAll 45 participants completed the trial.\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    assert not [f for f in find(analyse(path), "숫자불일치") if f.severity == CRITICAL]


def test_decimal_is_not_a_sample_size(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\nMean was N = 45.6 units and we enrolled 45 participants.\n\n"
        "## Methods\nWe analysed 45 participants.\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    assert not [f for f in find(analyse(path), "숫자불일치") if f.severity == CRITICAL]


# ── 통계: 알파 선언 표현과 그림 범례 ────────────────────────────────────────


@pytest.mark.parametrize(
    "sentence",
    [
        "All tests were two-sided, and p < 0.05 was taken to indicate statistical significance.",
        "A p value < 0.05 was regarded as significant.",
        "The threshold for significance was p < 0.05.",
        "양측검정으로 유의수준 p < 0.05를 기준으로 하였다.",
    ],
)
def test_alpha_declarations_are_not_flagged(tmp_path, sentence):
    result = analyse(write(tmp_path, doc(sentence + " Prose [1].")))
    assert not find(result, "통계보고"), [f.message for f in find(result, "통계보고")]


def test_significance_legend_under_a_figure_is_not_a_result_sentence(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nProse [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Figure legends\n**Figure 1.** Change scores.\n\n*p < 0.05, **p < 0.01.\n",
    )
    assert not find(analyse(path), "통계보고")


def test_table_cells_do_not_trigger_reporting_warnings(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nSee Table 1 [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Tables\n**Table 1.** Outcomes.\n\n"
        "| Outcome | p |\n|---|---|\n| ISI | p < 0.05 |\n",
    )
    warnings = [f for f in find(analyse(path), "통계보고") if f.severity == WARNING]
    assert not warnings


def test_p_equals_zero_is_still_caught_inside_a_table(tmp_path):
    """표라고 해서 모두 봐주면 안 된다 — p = 0.000 은 표 안에서도 치명이다."""
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nSee Table 1 [1].\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n\n"
        "## Tables\n**Table 1.** Outcomes.\n\n"
        "| Outcome | p |\n|---|---|\n| ISI | p = 0.000 |\n",
    )
    assert [f for f in find(analyse(path), "통계보고") if f.severity == CRITICAL]


# ── 인용 번호 자릿수: 체계적 문헌고찰 ───────────────────────────────────────


def test_citation_number_above_300_is_still_cross_checked(tmp_path):
    refs = "".join(f"{i}. Author {i}. Title. J Test. 2024.\n" for i in range(1, 61))
    body = " ".join(f"[{i}]" for i in range(1, 61)) + " and a typo [350]."
    path = write(tmp_path, "Title\n\n## Introduction\n" + body + "\n\n## References\n" + refs)
    assert find(analyse(path), "인용누락", "[350]")


def test_large_reference_list_does_not_produce_phantom_uncited_entries(tmp_path):
    refs = "".join(f"{i}. Author {i}. Title. J Test. 2024.\n" for i in range(1, 401))
    body = " ".join(f"[{i}]" for i in range(1, 401))
    path = write(tmp_path, "Title\n\n## Introduction\n" + body + "\n\n## References\n" + refs)
    result = analyse(path)
    assert not find(result, "미인용문헌")
    assert result.counts["ref_entries"] == 400


# ── 참고문헌 목록 파싱 ──────────────────────────────────────────────────────


def test_lowercase_surname_starts_a_new_unnumbered_entry(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1], [2] and [3].\n\n## References\n"
        "Morin CM, Jarrin DC. Epidemiology of insomnia. Sleep Med Clin. 2022.\n"
        "Kim H, Lee S. Second entry. J Test. 2023.\n"
        "van der Berg A, de Vries P. Third entry. J Sleep. 2024.\n",
    )
    result = analyse(path)
    assert result.counts["ref_entries"] == 3
    assert not find(result, "인용누락")


def test_long_author_list_wraps_without_splitting(tmp_path):
    authors = ", ".join(f"Author{i} A{i}" for i in range(1, 15))
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1].\n\n## References\n"
        + authors
        + ",\nand more names. A very long title here. J Test. 2024;12(3):1-9.\n",
    )
    assert analyse(path).counts["ref_entries"] == 1


def test_thebibliography_wrapper_is_not_a_reference(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n\\section{Introduction}\nProse \\cite{a} \\cite{b}.\n\n"
        "\\begin{thebibliography}{9}\n"
        "\\bibitem{a} Kim H. First. J Test. 2024.\n"
        "\\bibitem{b} Lee S. Second. J Test. 2023.\n"
        "\\end{thebibliography}\n",
        name="m.tex",
    )
    result = analyse(path)
    assert result.counts["ref_entries"] == 2
    assert not result.blockers


def test_bracketed_reference_list_is_excluded_from_citation_scanning(tmp_path):
    """참고문헌 줄의 '[1]' 을 본문 인용으로 세면 '미인용 문헌' 점검이 죽는다."""
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1] and [2].\n\n## References\n"
        "[1] Kim H. First. J Test. 2024.\n"
        "[2] Lee S. Second. J Test. 2023.\n"
        "[3] Park J. Never cited anywhere. J Test. 2022.\n",
    )
    result = analyse(path)
    hits = find(result, "미인용문헌")
    assert len(hits) == 1 and "3번" in hits[0].message


# ── 자기 보고는 항상 나온다 ─────────────────────────────────────────────────


def test_coverage_is_reported_even_when_the_cross_check_cannot_run(tmp_path):
    path = write(tmp_path, "Title\n\n## Introduction\nProse citing [1], [2] and [3].\n")
    result = analyse(path)
    assert result.unverifiable
    assert any("인식했고" in note for note in result.coverage)
    assert "인용 표기 3개" in " ".join(result.coverage)


def test_blockers_are_not_double_counted_as_manuscript_defects(tmp_path):
    path = write(tmp_path, "Title\n\n## Introduction\nProse citing [1], [2] and [3].\n")
    text = console_report(analyse(path))
    assert "점검 불가 1건" in text
    assert "치명 0" in text


# ── 출력 안전 ───────────────────────────────────────────────────────────────


def test_control_characters_from_the_manuscript_are_stripped(tmp_path, capsys):
    evil = "\x1b[2J\x1b]0;PWNED\x07"
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1] and [2].\n\n## References\n"
        f"1. {evil} Kim H. Title. J Test. 2024.\n"
        "2. Lee S. Never cited. J Test. 2024.\n",
    )
    out_dir = tmp_path / "o"
    main([str(path), "--out-dir", str(out_dir)])
    printed = capsys.readouterr().out
    assert "\x1b" not in printed and "\x07" not in printed
    for name in (REPORT_MD, ISSUES_CSV, REFERENCES_CSV):
        assert "\x1b" not in (out_dir / name).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("\t=1+1", "'=1+1"),
        ("\r=1+1", "'=1+1"),
        ("-3", "-3"),
        ("=cmd", "'=cmd"),
    ],
)
def test_csv_safe_handles_control_prefixed_formulas(value, expected):
    assert csv_safe(value) == expected


def test_no_cell_of_either_csv_can_start_a_formula(tmp_path, capsys):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1] and [2].\n\n## References\n"
        '1. =HYPERLINK("http://evil","x"). Title. J Test. 2024.\n'
        "2. @SUM(A1). Another. J Test. 2024.\n",
    )
    out_dir = tmp_path / "o"
    main([str(path), "--out-dir", str(out_dir)])
    for name in (ISSUES_CSV, REFERENCES_CSV):
        text = (out_dir / name).read_text(encoding="utf-8-sig")
        for row in csv.reader(text.splitlines()):
            for cell in row:
                assert cell[:1] not in ("=", "+", "@", "\t", "\r"), (name, cell)


def test_output_never_overwrites_the_manuscript(tmp_path, capsys):
    path = tmp_path / REPORT_MD
    path.write_text("Title\n\n## Introduction\nProse [1].\n\n## References\n1. K. T. J. 2024.\n",
                    encoding="utf-8")
    before = path.read_bytes()
    assert main([str(path), "--out-dir", str(tmp_path)]) == EXIT_ERROR
    assert path.read_bytes() == before
    assert "원고 파일과 같습니다" in capsys.readouterr().err


def test_output_does_not_follow_a_symlink_out_of_the_out_dir(tmp_path, capsys):
    victim = tmp_path / "victim.md"
    victim.write_text("소중한 원고", encoding="utf-8")
    out_dir = tmp_path / "o"
    out_dir.mkdir()
    (out_dir / REPORT_MD).symlink_to(victim)
    path = write(tmp_path, "Title\n\n## Introduction\nProse [1].\n\n## References\n1. K. T. J. 2024.\n")
    assert main([str(path), "--out-dir", str(out_dir)]) == EXIT_ERROR
    assert victim.read_text(encoding="utf-8") == "소중한 원고"


def test_empty_out_dir_is_an_error_not_a_silent_pass(tmp_path, capsys):
    path = write(tmp_path, "Title\n\n## Introduction\nProse [1].\n\n## References\n1. K. T. J. 2024.\n")
    assert main([str(path), "--out-dir", ""]) == EXIT_ERROR


# ── 망가진 .docx ────────────────────────────────────────────────────────────


def test_deeply_nested_docx_gives_a_clean_error(tmp_path, capsys):
    depth = 400
    body = "<w:tbl>" * depth + "<w:p><w:r><w:t>hi</w:t></w:r></w:p>" + "</w:tbl>" * depth
    path = tmp_path / "deep.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>',
        )
    assert main([str(path)]) == EXIT_ERROR
    assert "중첩" in capsys.readouterr().err


def test_broken_footnotes_do_not_abort_the_whole_check(tmp_path):
    path = tmp_path / "m.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>'
            "<w:p><w:r><w:t>Body text [1].</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr("word/footnotes.xml", "<not well formed")
    ms = read_manuscript(path)
    assert "Body text [1]." in ms.text()
    assert any("footnotes" in note for note in ms.notes)


def test_entity_bomb_behind_a_long_comment_is_still_refused(tmp_path):
    padding = "<!-- " + "x" * 100_000 + " -->"
    evil = (
        '<?xml version="1.0"?>' + padding + '<!DOCTYPE d [<!ENTITY a "boom">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&a;</w:t></w:r>'
        "</w:p></w:body></w:document>"
    )
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", evil)
    with pytest.raises(ManuscriptError, match="DTD/ENTITY"):
        read_manuscript(path)


def test_text_box_paragraphs_are_separated(tmp_path):
    path = tmp_path / "m.docx"
    inner = (
        "<w:p><w:r><w:t>ends with sleep</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Figure 2 shows more</w:t></w:r></w:p>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>'
            f"<w:p><w:r><w:txbxContent>{inner}</w:txbxContent></w:r></w:p>"
            "</w:body></w:document>",
        )
    assert "sleepFigure" not in read_manuscript(path).text()


# ── 한도 파일 ───────────────────────────────────────────────────────────────


def test_infinity_in_limits_is_refused(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"references_max": Infinity}', encoding="utf-8")
    assert main([str(EXAMPLES / "manuscript_clean.md"), "--limits", str(bad)]) == EXIT_ERROR


def test_cp949_limits_file_is_read(tmp_path, capsys):
    limits = tmp_path / "k.json"
    limits.write_bytes('{"journal": "대한수면의학회지", "references_max": 40}'.encode("cp949"))
    assert main([str(EXAMPLES / "manuscript_clean.md"), "--limits", str(limits)]) == EXIT_OK
    assert "대한수면의학회지" in capsys.readouterr().out


# ── 성능 (병적인 입력에서 멈추지 않는다) ────────────────────────────────────


def test_sentence_splitting_is_linear():
    text = "Group A vs. " * 20000
    start = time.monotonic()
    sentences(text)
    assert time.monotonic() - start < 2.0


def test_many_duplicate_reference_numbers_are_fast(tmp_path):
    refs = "".join("1. Author. Title. J Test. 2024.\n" for _ in range(3000))
    path = write(tmp_path, "Title\n\n## Introduction\nBody [1].\n\n## References\n" + refs)
    start = time.monotonic()
    analyse(path)
    assert time.monotonic() - start < 5.0


# ── 표시 폭 / 한국어 조사 ───────────────────────────────────────────────────


def test_display_width_counts_hangul_as_two_columns():
    assert display_width("표") == 2
    assert display_width("ab") == 2
    assert display_width("표 2") == 4


def test_unverifiable_box_edges_line_up(tmp_path):
    path = write(tmp_path, "Title\n\nProse with no citations and no reference list.\n")
    lines = [ln for ln in console_report(analyse(path), width=78).splitlines() if ln.startswith("┃")]
    assert lines
    assert {display_width(ln) for ln in lines} == {78}


def test_findings_keep_their_indentation(flawed):
    text = console_report(flawed, width=78)
    assert any(ln.startswith("    L") or ln.startswith("   L") for ln in text.splitlines())


@pytest.mark.parametrize(
    "word,expected",
    [
        ("표 2", "표 2를"),
        ("표 3", "표 3을"),
        ("그림 1", "그림 1을"),
        ("(그림 2)", "(그림 2)를"),
    ],
)
def test_josa_picks_the_right_particle(word, expected):
    assert josa(word, "을", "를") == expected


def test_report_uses_correct_korean_particles(flawed):
    text = console_report(flawed) + markdown_report(flawed)
    assert "이(가)" not in text and "을(를)" not in text
    assert "48가" not in text


# ── 여전히 지켜야 하는 것들 (변이 테스트에서 살아남았던 구멍) ───────────────


def test_keyword_line_is_excluded_from_the_abstract_count(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Abstract\nOne two three [1].\n\n"
        "**Keywords.** insomnia; sleep; breathing; entrainment; trial\n\n"
        "## Introduction\nProse.\n\n## References\n1. K. T. J. 2024.\n",
    )
    assert analyse(path).counts["abstract_words"] == 3


def test_truncation_summary_appears_when_a_check_overflows(tmp_path):
    refs = "".join(f"{i}. Author {i}. Title. J Test. 2024.\n" for i in range(1, 41))
    path = write(tmp_path, "Title\n\n## Introduction\nOnly [1] is cited.\n\n## References\n" + refs)
    result = analyse(path)
    hits = find(result, "미인용문헌")
    assert len(hits) == 13  # 12건 + 요약 1건
    assert any("39건 중 12건만" in f.message for f in hits)


def test_utf16_manuscript_is_read(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes(
        "Title\n\n## Introduction\nProse citing [1].\n\n## References\n"
        "1. Kim H. T. J Test. 2024.\n".encode("utf-16")
    )
    result = analyse(path)
    assert not result.blockers


def test_markdown_table_cells_escape_pipes(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1].\n\n## References\n"
        "1. Kim H. Cited. J Test. 2024.\n"
        "2. Lee S. Never cited | with a pipe. J Test. 2024.\n",
    )
    text = markdown_report(analyse(path))
    assert "\\| with a pipe" in text
    assert " | with a pipe" not in text


# ── 깨끗한 대조본: 포맷·스타일별 거짓 양성 방지 ─────────────────────────────


def test_clean_plain_text_manuscript(tmp_path):
    body = (EXAMPLES / "manuscript_clean.md").read_text(encoding="utf-8")
    result = analyse(write(tmp_path, body, name="m.txt"))
    assert result.n_critical == 0 and result.n_warning == 0


def test_clean_author_year_manuscript(tmp_path):
    path = write(
        tmp_path,
        "Title of the study\n\n## Abstract\nWe enrolled 45 participants.\n\n"
        "## Introduction\n"
        "Insomnia is common (Morin & Jarrin, 2022). Arousal matters (Bonnet, 2020).\n\n"
        "## Methods\nWe analysed 45 participants (N = 45).\n\n"
        "## References\n"
        "Morin, C. M., & Jarrin, D. C. (2022). Epidemiology of insomnia. Sleep Med Clin.\n"
        "Bonnet, M. H. (2020). Hyperarousal and insomnia. Sleep Med Rev.\n",
    )
    result = analyse(path)
    assert result.style == "author-year"
    assert result.n_critical == 0 and result.n_warning == 0


def test_clean_cite_key_manuscript(tmp_path):
    path = write(
        tmp_path,
        "Title of the study\n\n\\section{Abstract}\nWe enrolled 45 participants.\n\n"
        "\\section{Introduction}\nInsomnia is common \\cite{morin2022,bonnet2020}.\n\n"
        "\\section{Methods}\nWe analysed 45 participants (N = 45).\n\n"
        "\\begin{thebibliography}{9}\n"
        "\\bibitem{morin2022} Morin CM. Epidemiology. Sleep Med Clin. 2022.\n"
        "\\bibitem{bonnet2020} Bonnet MH. Hyperarousal. Sleep Med Rev. 2020.\n"
        "\\end{thebibliography}\n",
        name="m.tex",
    )
    result = analyse(path)
    assert result.style == "cite-key"
    assert result.n_critical == 0 and result.n_warning == 0


def test_many_order_reversals_collapse_into_one_warning(tmp_path):
    """원인 하나(번호 재정렬 안 함)를 열두 번 반복하면 진짜 경고가 묻힌다."""
    refs = "".join(f"{i}. Author {i}. Title. J Test. 2024.\n" for i in range(1, 21))
    body = " ".join(f"[{i}]" for i in range(20, 0, -1))  # 완전히 역순으로 인용
    path = write(tmp_path, "Title\n\n## Introduction\n" + body + "\n\n## References\n" + refs)
    hits = find(analyse(path), "인용순서")
    assert len(hits) == 1
    assert "19곳" in hits[0].message


def test_a_couple_of_reversals_are_still_listed_individually(tmp_path):
    refs = "".join(f"{i}. Author {i}. Title. J Test. 2024.\n" for i in range(1, 5))
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nA [1] B [3] C [2] D [4].\n\n## References\n" + refs,
    )
    hits = find(analyse(path), "인용순서")
    assert len(hits) == 1 and "[2]" in hits[0].message


def test_bibitem_list_does_not_claim_missing_numbers(tmp_path):
    """\\bibitem 목록은 키로 대조한다 — '번호가 없다'는 메모는 혼란만 준다."""
    path = write(
        tmp_path,
        "Title\n\n\\section{Introduction}\nProse \\cite{a}.\n\n"
        "\\begin{thebibliography}{9}\n\\bibitem{a} Kim H. T. J Test. 2024.\n"
        "\\end{thebibliography}\n",
        name="m.tex",
    )
    result = analyse(path)
    assert not any("번호 텍스트가 없어" in note for note in result.coverage)
