"""점검 규칙 자체의 단위 테스트 — 손으로 센 값, 경계 조건, 다른 인용 스타일."""

from __future__ import annotations

import pytest
from conftest import analyse, find

from draftcheck.checks import (
    CRITICAL,
    INFO,
    WARNING,
    _expand_numbers,
    _first_surname,
    count_words,
    detect_style,
    parse_references,
    sentences,
)
from draftcheck.docio import Line, detect_sections, read_manuscript


def write(tmp_path, text, name="m.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ── 단어 수 세기: 손으로 센 값과 대조 ───────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        # The quick brown non-contact fox jumped over 45 dogs  → [12] 는 세지 않음
        ("The quick brown non-contact fox jumped [12] over 45 dogs.", 9),
        # 하이픈어는 1단어, 숫자도 1단어
        ("non-contact bedside device", 3),
        # 한글은 어절 단위
        ("슬로우 호흡 유도는 서파수면을 늘렸다 (p<0.05).", 6),
        # 마크다운 강조·제목 표시는 글자가 아니다
        ("**Bold** and *italic* text", 4),
        ("# Heading here", 2),
        # 표 구분자는 글자가 아니다
        ("| Age | 48.2 (9.1) |", 3),
        # 인용만 있는 줄은 0단어
        ("[1-3]", 0),
        ("", 0),
        ("   ", 0),
        # 문장부호만 있는 토큰은 세지 않는다
        ("Yes — indeed .", 2),
        # 범위 인용도 제거된다 (Evidence/is/mixed/across/studies)
        ("Evidence is mixed [3,7-9] across studies.", 5),
        # LaTeX 명령은 벗기고 인자만 센다
        ("\\textbf{Slow} breathing \\cite{kim2024}", 2),
    ],
)
def test_count_words_matches_hand_count(text, expected):
    assert count_words(text) == expected


def test_body_word_count_excludes_references_and_captions(tmp_path):
    path = write(
        tmp_path,
        "Title of the paper\n\n"
        "## Introduction\n"
        "One two three [1].\n\n"
        "## References\n"
        "1. Kim H. A very long reference entry that must not be counted. J Test. 2024.\n\n"
        "## Figure legends\n"
        "**Figure 1.** A caption that must not be counted either.\n",
    )
    result = analyse(path)
    # "Introduction"(소제목 1) + "One two three"(3) = 4.
    # 참고문헌 항목과 그림 캡션은 한 단어도 들어가지 않는다.
    assert result.counts["body_words"] == 4


# ── 인용 번호 확장 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "group,expected",
    [
        ("3", [3]),
        ("3,5", [3, 5]),
        ("3-5", [3, 4, 5]),
        ("3–5", [3, 4, 5]),
        ("1, 4-6; 9", [1, 4, 5, 6, 9]),
        ("5-3", []),  # 뒤집힌 범위는 인용이 아니다
        ("1-500", []),  # 지나치게 넓은 범위는 인용이 아니다
        ("0", []),
        ("5000", []),  # 참고문헌 번호로 볼 수 없는 크기
        # 체계적 문헌고찰은 300개를 넘는다 — 세 자리를 넘겨도 인용으로 본다
        ("350", [350]),
        ("1200", [1200]),
        ("", []),
    ],
)
def test_expand_numbers(group, expected):
    assert _expand_numbers(group) == expected


# ── 문장 나누기 ──────────────────────────────────────────────────────────────


def test_sentences_do_not_split_on_et_al():
    text = "Kim et al. reported a gain (p = 0.02). The effect held."
    assert sentences(text) == [
        "Kim et al. reported a gain (p = 0.02).",
        "The effect held.",
    ]


def test_sentences_do_not_split_on_decimals():
    assert sentences("The mean was 3.14 units.") == ["The mean was 3.14 units."]


# ── 저자 성 추출 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "authors,expected",
    [
        ("Morin CM, Jarrin DC", "morin"),
        ("Kim, H., & Lee, S.", "kim"),
        ("H. J. Kim", "kim"),
        ("Ngo HV, Martinetz T, Born J", "ngo"),
        ("", ""),
    ],
)
def test_first_surname(authors, expected):
    assert _first_surname(authors) == expected


# ── p 값 규칙 ────────────────────────────────────────────────────────────────


def _stats_doc(sentence):
    return (
        "Paper title here\n\n## Abstract\nSummary text.\n\n"
        "## Results\n" + sentence + "\n\n"
        "## References\n1. Kim H. Title. J Test. 2024.\n"
    )


def test_p_value_above_one_is_critical(tmp_path):
    result = analyse(write(tmp_path, _stats_doc("The test gave p = 1.24 for the difference.")))
    hits = [f for f in find(result, "통계보고") if "1을 넘" in f.message]
    assert len(hits) == 1 and hits[0].severity == CRITICAL


def test_p_equals_zero_variants(tmp_path):
    for text in ("p = 0.000", "p=.000", "P = 0"):
        result = analyse(write(tmp_path, _stats_doc(f"Group means differed ({text}).")))
        assert [f for f in find(result, "통계보고") if "0이 될 수 없" in f.message], text


def test_methods_alpha_sentence_is_not_flagged(tmp_path):
    result = analyse(
        write(tmp_path, _stats_doc("Statistical significance was set at p < 0.05, two-tailed."))
    )
    assert not find(result, "통계보고")


def test_p_with_confidence_interval_is_accepted(tmp_path):
    result = analyse(
        write(
            tmp_path,
            _stats_doc("The difference was 4.0 units (95% CI, 1.0 to 7.0; p = 0.011)."),
        )
    )
    assert not [f for f in find(result, "통계보고") if "효과크기" in f.message]


def test_p_with_odds_ratio_is_accepted(tmp_path):
    result = analyse(write(tmp_path, _stats_doc("Risk was higher (OR = 2.1; p = 0.03).")))
    assert not [f for f in find(result, "통계보고") if "효과크기" in f.message]


def test_bare_p_value_is_flagged(tmp_path):
    result = analyse(write(tmp_path, _stats_doc("The groups differed (p = 0.03).")))
    assert [f for f in find(result, "통계보고") if "효과크기" in f.message]


def test_consistent_p_style_is_not_flagged(tmp_path):
    result = analyse(
        write(
            tmp_path,
            _stats_doc(
                "A rose (95% CI, 1 to 2; p = 0.030) and B rose (95% CI, 2 to 3; p = 0.004)."
            ),
        )
    )
    assert not [f for f in find(result, "통계보고") if "앞자리" in f.message]


# ── 표본수 일관성: 거짓 양성이 나오면 안 되는 상황들 ────────────────────────


def _n_doc(abstract, body):
    return (
        "Title\n\n## Abstract\n" + abstract + "\n\n"
        "## Methods\n" + body + "\n\n"
        "## References\n1. Kim H. Title. J Test. 2024.\n"
    )


def test_matching_sample_sizes_are_not_flagged(tmp_path):
    result = analyse(write(tmp_path, _n_doc("We enrolled 45 participants.", "N = 45 in total.")))
    assert not [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]


def test_per_arm_numbers_in_body_only_are_not_flagged(tmp_path):
    result = analyse(
        write(tmp_path, _n_doc("N = 45 adults took part.", "Arms had n = 23 and n = 22 (N = 45)."))
    )
    assert not [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]


def test_ages_and_durations_are_not_read_as_sample_sizes(tmp_path):
    result = analyse(
        write(
            tmp_path,
            _n_doc(
                "N = 45 adults aged 48 years took part over 12 weeks.",
                "The 45 participants were aged 30 to 65 years.",
            ),
        )
    )
    assert not [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]


def test_mismatched_sample_size_is_flagged(tmp_path):
    result = analyse(write(tmp_path, _n_doc("N = 48 adults.", "We analysed 45 participants.")))
    hits = [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]
    assert len(hits) == 1 and "48" in hits[0].message


def test_korean_sample_size_labels(tmp_path):
    result = analyse(write(tmp_path, _n_doc("참가자 48명이 참여했다.", "대상자 45명을 분석했다.")))
    assert [f for f in find(result, "숫자불일치") if f.severity == CRITICAL]


def test_no_sample_size_anywhere_is_silent(tmp_path):
    result = analyse(write(tmp_path, _n_doc("No numbers here.", "Nor here.")))
    assert not find(result, "숫자불일치")


# ── 참고문헌 목록 파싱 ───────────────────────────────────────────────────────


def _refs(tmp_path, ref_block, body="Body cites [1] and [2].\n"):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\n" + body + "\n## References\n" + ref_block,
    )
    ms = read_manuscript(path)
    return parse_references(detect_sections(ms))


def test_wrapped_reference_entries_are_joined(tmp_path):
    entries, _ = _refs(
        tmp_path,
        "1. Morin CM, Jarrin DC. Epidemiology of insomnia.\n"
        "   Sleep Med Clin. 2022;17(2):173-91.\n"
        "2. Kim H. Second entry. J Test. 2024.\n",
    )
    assert len(entries) == 2
    assert "Sleep Med Clin" in entries[0].raw


def test_unnumbered_reference_list_gets_sequential_numbers(tmp_path):
    entries, notes = _refs(
        tmp_path,
        "Morin CM, Jarrin DC. Epidemiology of insomnia. Sleep Med Clin. 2022.\n"
        "Kim H. Second entry. J Test. 2024.\n",
    )
    assert [e.number for e in entries] == [1, 2]
    assert any("등장 순서" in n for n in notes)


def test_reference_fields_are_extracted(tmp_path):
    entries, _ = _refs(
        tmp_path,
        "1. Kim HJ, Park JW. Non-contact respiration sensing. Sensors. 2024;24(4):1122. "
        "doi:10.3390/s24041122 PMID: 38400123\n",
    )
    entry = entries[0]
    assert entry.authors == "Kim HJ, Park JW"
    assert entry.year == "2024"
    assert entry.title == "Non-contact respiration sensing"
    assert entry.journal == "Sensors"
    assert entry.doi == "10.3390/s24041122"
    assert entry.pmid == "38400123"
    assert entry.parse_ok


def test_apa_style_reference_fields(tmp_path):
    entries, _ = _refs(
        tmp_path,
        "Kim, H., & Lee, S. (2024). Slow breathing and sleep. Journal of Sleep, 30(4), 1-12.\n",
    )
    entry = entries[0]
    assert entry.year == "2024"
    assert entry.authors.startswith("Kim, H.")
    assert entry.title == "Slow breathing and sleep"


def test_unparseable_reference_is_marked(tmp_path):
    entries, _ = _refs(tmp_path, "1. ???\n")
    assert entries[0].parse_ok is False


def test_duplicate_reference_numbers_are_reported(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nBody cites [1] and [2].\n\n## References\n"
            "1. Kim H. First. J Test. 2024.\n"
            "1. Lee S. Duplicate number. J Test. 2024.\n"
            "2. Park J. Third. J Test. 2024.\n",
        )
    )
    assert [f for f in find(result, "목록번호") if "두 번" in f.message]


def test_reference_number_gap_is_reported(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nBody cites [1] and [3].\n\n## References\n"
            "1. Kim H. First. J Test. 2024.\n"
            "3. Park J. Third. J Test. 2024.\n",
        )
    )
    assert [f for f in find(result, "목록번호") if "건너" in f.message]


# ── 인용 스타일 판별 ─────────────────────────────────────────────────────────


def test_style_detection_numeric():
    lines = [Line(1, "A [1] B [2] C [3] D")]
    assert detect_style(lines)[0] == "numeric"


def test_style_detection_author_year():
    lines = [Line(1, "As shown (Kim et al., 2024; Lee & Park, 2023) and by Choi (2022).")]
    assert detect_style(lines)[0] == "author-year"


def test_style_detection_cite_key():
    lines = [Line(1, "As shown \\cite{kim2024,lee2023}.")]
    assert detect_style(lines)[0] == "cite-key"


def test_style_detection_gives_up_on_a_manuscript_with_no_citations():
    lines = [Line(1, "Plain prose with no citations at all.")]
    assert detect_style(lines)[0] == "판별불가"


def test_author_year_cross_check(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\n"
            "Slow breathing helps (Morin & Jarrin, 2022). Ghost work (Nobody, 1999) too.\n\n"
            "## References\n"
            "Morin, C. M., & Jarrin, D. C. (2022). Epidemiology of insomnia. Sleep Med Clin.\n"
            "Kim, H. (2024). Never cited here. Journal of Sleep.\n",
        )
    )
    assert result.style == "author-year"
    assert [f for f in find(result, "인용누락") if "Nobody" in f.target]
    assert [f for f in find(result, "미인용문헌") if "Kim" in f.target]
    assert any("2급 지원" in note for note in result.coverage)


def test_cite_key_cross_check(tmp_path):
    path = write(
        tmp_path,
        "Title\n\n\\section{Introduction}\n"
        "Slow breathing \\cite{morin2022} and \\cite{ghost2020}.\n\n"
        "\\section{References}\n"
        "\\bibitem{morin2022} Morin CM. Epidemiology. Sleep Med Clin. 2022.\n"
        "\\bibitem{unused2019} Lee S. Unused. J Test. 2019.\n",
        name="m.tex",
    )
    result = analyse(path)
    assert result.style == "cite-key"
    assert [f for f in find(result, "인용누락") if f.target == "ghost2020"]
    assert [f for f in find(result, "미인용문헌") if f.target == "unused2019"]


def test_forcing_a_style_reports_the_disagreement(tmp_path):
    path = write(tmp_path, "Title\n\n## Introduction\nA [1] B [2] C [3].\n\n## References\n1. K. T. J. 2024.\n")
    result = analyse(path, style="author-year")
    assert result.style_source == "사용자 지정"
    assert any("자동 판별은 numeric" in note for note in result.coverage)


# ── 항목 7: 조용히 통과하지 않는다 ───────────────────────────────────────────


def test_no_reference_section_is_unverifiable(tmp_path):
    result = analyse(
        write(tmp_path, "Title\n\n## Introduction\nProse citing [1] and [2] and [3].\n")
    )
    assert result.unverifiable
    assert any("참고문헌" in reason for reason in result.blockers)
    assert [f for f in result.findings if f.kind == "점검불가" and f.severity == CRITICAL]


def test_no_citations_found_is_unverifiable(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nProse with no citation markers at all.\n\n"
            "## References\n1. Kim H. Title. J Test. 2024.\n",
        )
    )
    assert result.unverifiable
    assert any("인용 표기를 하나도" in reason for reason in result.blockers)


def test_empty_manuscript_is_unverifiable(tmp_path):
    result = analyse(write(tmp_path, "\n\n\n"))
    assert result.unverifiable
    assert len(result.blockers) == 2  # 참고문헌 없음 + 인용 없음


def test_a_complete_manuscript_is_not_unverifiable(clean):
    assert not clean.unverifiable


# ── 그림/표 ──────────────────────────────────────────────────────────────────


def test_mentioned_figure_without_caption(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nSee Figure 1 and Figure 4 [1].\n\n"
            "## References\n1. Kim H. T. J. 2024.\n\n"
            "## Figure legends\n**Figure 1.** Only one caption.\n",
        )
    )
    hits = [f for f in find(result, "그림표") if f.target == "그림 4"]
    assert len(hits) == 1 and hits[0].severity == CRITICAL


def test_no_captions_at_all_downgrades_to_info(tmp_path):
    """그림을 별도 파일로 내는 저널이 흔하다 — 이때 치명을 쏟아내면 안 된다."""
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nSee Figure 1, Figure 2 and Figure 3 [1].\n\n"
            "## References\n1. Kim H. T. J. 2024.\n",
        )
    )
    hits = find(result, "그림표")
    assert hits and all(f.severity == INFO for f in hits)


def test_supplementary_figures_are_out_of_scope(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nSee Figure 1 and Supplementary Figure 2 [1].\n\n"
            "## References\n1. Kim H. T. J. 2024.\n\n"
            "## Figure legends\n**Figure 1.** Caption.\n",
        )
    )
    assert not find(result, "그림표")


def test_figure_mention_order_reversal(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nFirst see Figure 2 [1].\nThen see Figure 1.\n\n"
            "## References\n1. Kim H. T. J. 2024.\n\n"
            "## Figure legends\n**Figure 1.** A.\n\n**Figure 2.** B.\n",
        )
    )
    assert [f for f in find(result, "그림표") if "순서" in f.message or "뒤에 처음" in f.message]


def test_duplicate_caption_number(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nSee Table 1 [1].\n\n"
            "## References\n1. Kim H. T. J. 2024.\n\n"
            "## Tables\n**Table 1.** A.\n\n**Table 1.** B again.\n",
        )
    )
    assert [f for f in find(result, "그림표") if "두 번" in f.message]


# ── 약어 ─────────────────────────────────────────────────────────────────────


def _abbrev_doc(intro):
    return (
        "Title\n\n## Abstract\nSummary [1].\n\n## Introduction\n"
        + intro
        + "\n\n## References\n1. Kim H. T. J. 2024.\n"
    )


def test_undefined_abbreviation_is_reported_as_info(tmp_path):
    """등급은 '정보'다 — 학회·기관 약어(PROSPERO, AASM…)를 구별할 방법이 없어서,
    경고로 두면 진짜 경고가 이 소음에 묻힌다(실제 원고에서 경고 34건 중 27건)."""
    result = analyse(
        write(tmp_path, _abbrev_doc("We measured XYZ. The XYZ rose. Later XYZ fell [1]."))
    )
    hits = [f for f in find(result, "약어") if f.target == "XYZ" and "정의가 없" in f.message]
    assert len(hits) == 1
    assert hits[0].severity == INFO


def test_undefined_abbreviation_below_the_use_threshold_is_ignored(tmp_path):
    for text in ("We measured XYZ once only [1].", "We measured XYZ. Later XYZ rose [1]."):
        result = analyse(write(tmp_path, _abbrev_doc(text)))
        assert not [f for f in find(result, "약어") if f.target == "XYZ"], text


def test_hyphenated_abbreviation_is_one_token(tmp_path):
    """'CBT-I'를 'CBT'로 잘라 보고하면 --abbrev-ok 로도 끌 수 없다."""
    result = analyse(
        write(
            tmp_path,
            _abbrev_doc(
                "Cognitive behavioural therapy for insomnia (CBT-I) helps. "
                "CBT-I was offered. CBT-I was declined by two. CBT-I ended [1]."
            ),
        )
    )
    assert not find(result, "약어")


def test_product_code_is_not_mangled(tmp_path):
    result = analyse(
        write(tmp_path, _abbrev_doc("The BELL-001 device. BELL-001 again. BELL-001 thrice [1].")),
        extra_known={"BELL-001"},
    )
    assert not find(result, "약어")


def test_well_known_abbreviations_need_no_definition(tmp_path):
    result = analyse(write(tmp_path, _abbrev_doc("We used MRI. The MRI showed DNA and DNA [1].")))
    assert not find(result, "약어")


def test_extra_known_abbreviations_can_be_supplied(tmp_path):
    path = write(tmp_path, _abbrev_doc("We measured XYZ. Later the XYZ rose [1]."))
    result = analyse(path, extra_known={"XYZ"})
    assert not [f for f in find(result, "약어") if f.target == "XYZ"]


def test_defined_abbreviation_is_accepted(tmp_path):
    result = analyse(
        write(tmp_path, _abbrev_doc("The extra sleep score (XYZ) rose. The XYZ rose again [1]."))
    )
    assert not [f for f in find(result, "약어") if "정의가 없" in f.message]


def test_redefinition_inside_the_same_section(tmp_path):
    result = analyse(
        write(
            tmp_path,
            _abbrev_doc(
                "The extra sleep score (XYZ) rose. The extra sleep score (XYZ) rose again [1]."
            ),
        )
    )
    assert [f for f in find(result, "약어") if "번 정의" in f.message]


def test_abstract_and_body_definitions_are_separate_scopes(tmp_path):
    """대부분의 저널이 초록과 본문에서 각각 정의하라고 한다 — 재정의가 아니다."""
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Abstract\nThe extra sleep score (XYZ) rose and the XYZ held [1].\n\n"
            "## Introduction\nThe extra sleep score (XYZ) rose and the XYZ held again.\n\n"
            "## References\n1. Kim H. T. J. 2024.\n",
        )
    )
    assert not [f for f in find(result, "약어") if f.severity == WARNING]


def test_table_notation_is_not_a_definition(tmp_path):
    """표의 mean (SD) / n (%) 는 약어 정의가 아니다."""
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nSee Table 1 [1].\n\n"
            "## References\n1. Kim H. T. J. 2024.\n\n"
            "## Tables\n**Table 1.** Baseline.\n\n"
            "| Age, mean (SD) | 48.2 (9.1) |\n|---|---|\n| Female, n (%) | 14 (60.9) |\n",
        )
    )
    assert not find(result, "약어")


def test_section_headings_are_not_abbreviations(tmp_path):
    result = analyse(write(tmp_path, _abbrev_doc("Plain prose [1].")))
    assert not find(result, "약어")
