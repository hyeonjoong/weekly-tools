"""예제에 **의도적으로 심은 결함**을 하나씩 검증한다.

`examples/_make_examples.py` 의 DEFECTS 목록과 1:1로 대응한다. 한 결함이 잡히지
않으면 정확히 그 테스트만 실패하므로, 어떤 점검이 무너졌는지 바로 보인다.

그리고 그 반대 방향 — **깨끗한 대조본에서 치명이 0건**인지 — 도 함께 본다.
거짓 양성이 나오는 순간 이 툴은 소음이 되고 두 번 다시 열리지 않기 때문이다.
"""

from __future__ import annotations

from conftest import EXAMPLES, LIMITS, analyse, find
from draftcheck.checks import CRITICAL, WARNING


# ── 거짓 양성 방지: 깨끗한 대조본 ────────────────────────────────────────────


def test_clean_manuscript_has_no_critical(clean):
    assert clean.n_critical == 0, [f.message for f in clean.by_severity(CRITICAL)]


def test_clean_manuscript_has_no_warning(clean):
    assert clean.n_warning == 0, [f.message for f in clean.by_severity(WARNING)]


def test_clean_manuscript_is_not_unverifiable(clean):
    assert clean.blockers == []


def test_clean_docx_has_no_critical_or_warning(clean_docx):
    assert clean_docx.n_critical == 0, [f.message for f in clean_docx.by_severity(CRITICAL)]
    assert clean_docx.n_warning == 0, [f.message for f in clean_docx.by_severity(WARNING)]


def test_clean_manuscript_fits_the_journal_limits():
    result = analyse(EXAMPLES / "manuscript_clean.md", limits=_limits())
    assert [row for row in result.limit_rows if not row[3]] == []


def _limits():
    import json

    return json.loads(LIMITS.read_text(encoding="utf-8"))


# ── D1 인용누락 ──────────────────────────────────────────────────────────────


def test_d1_citation_without_reference(flawed):
    hits = find(flawed, "인용누락", "[27]")
    assert len(hits) == 1
    assert hits[0].severity == CRITICAL
    assert "26" in hits[0].message  # 목록 개수를 함께 알려 준다


# ── D2 미인용문헌 ────────────────────────────────────────────────────────────


def test_d2_reference_never_cited(flawed):
    hits = find(flawed, "미인용문헌", "26번")
    assert len(hits) == 1
    assert hits[0].severity == CRITICAL


def test_d2_only_reference_26_is_reported(flawed):
    assert len(find(flawed, "미인용문헌")) == 1


# ── D3 인용 순서 역전 ────────────────────────────────────────────────────────


def test_d3_citation_order_reversal(flawed):
    hits = find(flawed, "인용순서")
    assert len(hits) == 1, [f.message for f in hits]
    assert "[17]" in hits[0].message and "[18]" in hits[0].message
    assert hits[0].severity == WARNING


# ── D4 그림 미언급 ───────────────────────────────────────────────────────────


def test_d4_figure_never_mentioned(flawed):
    hits = [f for f in find(flawed, "그림표") if "그림 3" in f.target]
    assert len(hits) == 1
    assert hits[0].severity == CRITICAL
    assert "언급" in hits[0].message


def test_d4_figures_1_and_2_are_not_reported(flawed):
    assert not [f for f in find(flawed, "그림표") if f.target in ("그림 1", "그림 2")]


# ── D5 효과크기·CI 없는 p 값 ────────────────────────────────────────────────


def test_d5_p_value_without_effect_size_or_ci(flawed):
    hits = [f for f in find(flawed, "통계보고") if "효과크기" in f.message]
    assert len(hits) == 1
    assert "Sleep efficiency" in hits[0].message
    assert hits[0].severity == WARNING


def test_d5_sentences_with_ci_are_not_reported(flawed):
    """95% CI 와 Hedges g 를 함께 보고한 문장은 걸리면 안 된다."""
    assert not [f for f in find(flawed, "통계보고") if "Hedges" in f.message]


# ── D6 표 번호 건너뜀 ────────────────────────────────────────────────────────


def test_d6_table_number_gap(flawed):
    hits = [f for f in find(flawed, "그림표") if "건너" in f.message]
    assert len(hits) == 1
    assert "2" in hits[0].message
    assert hits[0].severity == WARNING


# ── D7 초록 ↔ 본문 표본수 불일치 ────────────────────────────────────────────


def test_d7_abstract_sample_size_mismatch(flawed):
    hits = find(flawed, "숫자불일치")
    assert len(hits) == 1
    assert hits[0].severity == CRITICAL
    assert "48" in hits[0].message and "45" in hits[0].message


def test_d7_per_arm_sample_sizes_are_not_false_positives(clean):
    """n = 23 / n = 22 는 총 45의 부분집합 — 절대 불일치로 잡히면 안 된다."""
    assert not [f for f in clean.findings if f.kind == "숫자불일치" and f.severity == CRITICAL]


# ── D8 p = 0.000 ─────────────────────────────────────────────────────────────


def test_d8_p_equals_zero(flawed):
    hits = [f for f in find(flawed, "통계보고") if "0이 될 수 없" in f.message]
    assert len(hits) == 1
    assert hits[0].severity == CRITICAL
    assert "p < 0.001" in hits[0].advice


# ── D9 p 표기 혼재 ───────────────────────────────────────────────────────────


def test_d9_leading_zero_style_mixed(flawed):
    hits = [f for f in find(flawed, "통계보고") if "앞자리 0" in f.message]
    assert len(hits) == 1
    assert hits[0].severity == WARNING


def test_d9_clean_manuscript_has_consistent_p_style(clean):
    assert not [f for f in clean.findings if "앞자리 0" in f.message]


# ── D10 임계값만 보고 ────────────────────────────────────────────────────────


def test_d10_threshold_only_p_value(flawed):
    hits = [f for f in find(flawed, "통계보고") if "임계값" in f.message]
    assert len(hits) == 1
    assert "p < 0.05" in hits[0].message


def test_d10_methods_significance_sentence_is_exempt(clean):
    """'Two-sided p values below 0.05 were considered significant' 는 방법 기술이다."""
    assert not [f for f in clean.findings if "임계값" in f.message]


# ── D11 약어 정의 전 사용 ────────────────────────────────────────────────────


def test_d11_abbreviation_used_before_definition(flawed):
    hits = [f for f in find(flawed, "약어") if f.target == "ISI"]
    assert len(hits) == 1
    assert hits[0].severity == WARNING
    assert "먼저 사용" in hits[0].message


# ── D12 초록 단어수 초과 ─────────────────────────────────────────────────────


def test_d12_abstract_word_limit_exceeded():
    result = analyse(EXAMPLES / "manuscript_flawed.md", limits=_limits())
    hits = [f for f in result.findings if f.kind == "분량" and "초록" in f.target]
    assert len(hits) == 1
    assert hits[0].severity == WARNING
    assert result.counts["abstract_words"] > 250


def test_length_counts_are_reported_even_without_limits(flawed):
    labels = {row[0] for row in flawed.limit_rows}
    assert labels == {"제목 문자수", "초록 단어수", "본문 단어수", "참고문헌 개수", "그림+표 개수"}
    assert all(row[2] is None for row in flawed.limit_rows)


# ── .docx 판이 .md 판과 같은 결론을 내는가 ──────────────────────────────────


def test_docx_finds_the_same_defects_as_markdown(flawed, flawed_docx):
    def signature(result):
        return sorted((f.severity, f.kind, f.target) for f in result.findings)

    assert signature(flawed_docx) == signature(flawed)


def test_docx_ignores_tracked_deletions(flawed_docx):
    """추적 변경으로 지워진 문장의 인용 [99] 와 'Figure 9' 는 존재하지 않는 글자다."""
    blob = " ".join(f"{f.target} {f.message}" for f in flawed_docx.findings)
    assert "99" not in blob
    assert "그림 9" not in blob
    assert any("추적 변경" in note for note in flawed_docx.ms.notes)


def test_docx_ignores_endnote_field_codes(flawed_docx):
    """필드 코드 안의 rec-number 77 등이 인용으로 새어 나오면 안 된다."""
    numbers = {f.target for f in flawed_docx.findings}
    assert "[77]" not in numbers
    assert "[17]" in " ".join(numbers)  # 진짜 인용은 여전히 보인다
    assert any("EndNote" in note for note in flawed_docx.ms.notes)


def test_docx_reads_real_word_tables(flawed_docx):
    table_lines = [ln for ln in flawed_docx.ms.lines if ln.kind == "table"]
    assert len(table_lines) >= 20  # 표 셀 하나가 문단 하나
    assert any("48.2" in ln.text for ln in table_lines)
