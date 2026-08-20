"""숫자 추출과 건너뜀 규칙 — 크라잉울프를 막는 부분이라 가장 촘촘히 봅니다."""

from decimal import Decimal

import pytest

from tracecheck.manuscript import Block
from tracecheck.numbers import (SKIP_ALPHA, SKIP_CITE, SKIP_DATE, SKIP_IDENT,
                                SKIP_ORDER, SKIP_RANGE, SKIP_RATIO, SKIP_SMALL,
                                SKIP_TABREF, SKIP_TIME, SKIP_YEAR,
                                cell_numbers, extract_numbers)


def block(text, kind="para", section="results"):
    return Block(index=0, line=1, section=section, kind=kind, text=text)


def values(text, **kw):
    return [(n.text, n.skip) for n in extract_numbers(block(text, **kw))]


def compared(text, **kw):
    return [n.text for n in extract_numbers(block(text, **kw)) if not n.skip]


def skipped(text, **kw):
    return {n.text: n.skip for n in extract_numbers(block(text, **kw)) if n.skip}


# --------------------------------------------------------------------------- #
# 뽑아야 하는 것
# --------------------------------------------------------------------------- #

def test_basic_extraction():
    assert compared("평균 12.44 (SD 4.08)") == ["12.44", "4.08"]


def test_negative_and_unicode_minus():
    assert compared("평균차 −3.47 이다") == ["-3.47"]


def test_thousands_separator():
    numbers = extract_numbers(block("총 1,204개 셀"))
    assert numbers[0].value == Decimal("1204")


def test_leading_dot_decimal():
    assert compared("p = .002 였다") == ["0.002"]


def test_percent_flag_and_value():
    numbers = extract_numbers(block("순응도는 87.3%였다"))
    assert numbers[0].is_percent and numbers[0].value == Decimal("87.3")


def test_inequality_is_preserved():
    numbers = extract_numbers(block("유의하였다 (p < 0.001)"))
    assert numbers[0].op == "<" and numbers[0].text == "<0.001"


def test_decimals_counted_from_literal():
    numbers = extract_numbers(block("값은 12.40 이다"))
    assert numbers[0].decimals == 2 and numbers[0].value == Decimal("12.40")


# --------------------------------------------------------------------------- #
# 건너뛰어야 하는 것 (8종 + 실무에서 추가된 2종)
# --------------------------------------------------------------------------- #

def test_skip_year():
    assert skipped("2024년 연구에서").get("2024") == SKIP_YEAR
    assert skipped("이 연구는 2019 이후 진행되었다").get("2019") == SKIP_YEAR


def test_skip_citation_bracket_and_author_year():
    assert skipped("보고되었다 [3]").get("3") == SKIP_CITE
    assert skipped("보고되었다 [4, 7]") == {"4": SKIP_CITE, "7": SKIP_CITE}
    assert skipped("(Kim, 2024)에서").get("2024") == SKIP_CITE


def test_skip_table_figure_reference():
    assert skipped("Table 2 에 제시하였다").get("2") == SKIP_TABREF
    assert skipped("그림 3 참조").get("3") == SKIP_TABREF
    assert skipped("Supplementary Table S1 참조") == {"1": SKIP_TABREF}


def test_skip_alpha_and_ci_level():
    assert skipped("유의수준은 p < 0.05 로 하였다").get("<0.05") == SKIP_ALPHA
    assert skipped("α = 0.05 로 설정").get("0.05") == SKIP_ALPHA
    assert skipped("95% CI -5.21 to -1.79").get("95") == SKIP_ALPHA
    # 보고된 p 값은 절대 건너뛰지 않습니다.
    assert "0.0021" in compared("p = 0.0021 이었다")
    assert "<0.001" in compared("p < 0.001 이었다")


def test_skip_scale_range_needs_cue_word():
    assert skipped("ISI 총점(범위 0-28)") == {"0": SKIP_RANGE, "28": SKIP_RANGE}
    # `0-28` 의 28 이 -28(음수)로 읽히면 안 됩니다.
    # 단서가 없으면 범위로 보지 않습니다 — 넓게 건너뛰면 진짜 값이 사라집니다.
    assert "12" in compared("측정값은 12-15 사이로 관찰되었다")


def test_skip_timepoint():
    assert skipped("8주 시점 평균").get("8") == SKIP_TIME
    assert skipped("at week 8 the mean").get("8") == SKIP_TIME
    assert skipped("12개월 추적").get("12") == SKIP_TIME
    # 소수는 시점이 아니라 결과값입니다.
    assert "9.82" in compared("9.82분 증가하였다")


def test_skip_small_integers_in_body_but_not_in_table_cells():
    assert skipped("2명이 탈락하였다").get("2") == SKIP_SMALL
    assert compared("2", kind="table") == ["2"]


def test_skip_date_and_time():
    assert skipped("2026-08-18 기준").get("2026") == SKIP_DATE
    assert skipped("23:40 에 측정").get("23") == SKIP_DATE


def test_skip_identifier():
    assert skipped("등록번호 NCT01234567").get("1234567") == SKIP_IDENT
    assert skipped("IRB 2026-041 승인").get("2026") in (SKIP_IDENT, SKIP_DATE)


def test_skip_allocation_ratio():
    assert skipped("1:1 로 배정") == {"1": SKIP_RATIO}


def test_every_skip_reason_is_in_display_order():
    """새 사유를 추가하고 리포트 목록에 안 넣는 실수를 막습니다."""
    from tracecheck import numbers as mod
    declared = {getattr(mod, name) for name in dir(mod)
                if name.startswith("SKIP_") and name != "SKIP_ORDER"
                and isinstance(getattr(mod, name), str)}
    assert declared == set(SKIP_ORDER)


def test_skip_reason_priority_is_deterministic():
    """겹치는 구간은 SKIP_ORDER 앞쪽 사유로 셉니다(집계가 흔들리지 않게)."""
    reasons = skipped("Table 2 는 2026-08-18 자료다")
    assert reasons["2"] == SKIP_TABREF


# --------------------------------------------------------------------------- #
# 문맥·원문 보존
# --------------------------------------------------------------------------- #

def test_context_is_the_sentence_not_whole_paragraph():
    text = ("첫 문장은 관계없다. 평균은 12.44 였다. 세 번째 문장도 관계없다.")
    numbers = [n for n in extract_numbers(block(text)) if not n.skip]
    assert numbers[0].context == "평균은 12.44 였다."


def test_raw_keeps_original_fullwidth_characters():
    numbers = extract_numbers(block("평균 １２.４ 이다"))
    assert numbers[0].value == Decimal("12.4")
    assert "１" in numbers[0].raw


def test_absurdly_long_digit_runs_are_ignored():
    assert compared("코드 123456789012345678901234567890") == []


# --------------------------------------------------------------------------- #
# 번들 셀 토크나이저
# --------------------------------------------------------------------------- #

def test_cell_numbers_multiple_values_per_cell():
    got = [(v, d) for v, d, _raw, _pct, _lab in cell_numbers("12.44 (4.08)")]
    assert got == [(Decimal("12.44"), 2), (Decimal("4.08"), 2)]


def test_cell_numbers_float_artifact_is_truncated():
    values_out = [v for v, _d, _raw, _p, _lab in cell_numbers("0.30000000000000004")]
    assert values_out == [Decimal("0.30000000")]


def test_cell_numbers_marks_digits_glued_to_letters():
    """`phq9_change` 의 9 는 값이 아니라 이름의 일부입니다."""
    assert [lab for *_rest, lab in cell_numbers("phq9_change")] == [True]
    assert [lab for *_rest, lab in cell_numbers("week8")] == [True]
    assert [lab for *_rest, lab in cell_numbers("12.44")] == [False]
    assert [lab for *_rest, lab in cell_numbers("12.4mmHg")] == [False]


def test_cell_numbers_reads_scientific_notation():
    """R 의 write.csv 는 작은 p 값을 1.5e-05 로 씁니다."""
    got = [(v, d) for v, d, _r, _p, _l in cell_numbers("1.5e-05")]
    assert got == [(Decimal("0.000015"), 6)]


@pytest.mark.parametrize("text", ["", "   ", "N/A", "결측"])
def test_cell_numbers_empty(text):
    assert cell_numbers(text) == []
