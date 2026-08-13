"""비율 재계산 — 잡아야 하는 것과, 절대 잡으면 안 되는 것."""

from __future__ import annotations

from conftest import analyze_text, findings


def crit(text):
    return findings(analyze_text(text), "치명", "비율")


def warn(text):
    return findings(analyze_text(text), "경고", "비율")


def test_explicit_fraction_mismatch_is_critical():
    got = crit("## Results\n반응자는 23/48 (45.2%) 이었다.\n")
    assert len(got) == 1
    assert "47.9" in got[0].recomputed


def test_explicit_fraction_match_is_silent():
    for text in ("23/48 (47.9%)", "23/48 (48%)", "23/48 (47.92%)", "23/48 = 47.9%"):
        assert crit(f"## Results\n{text} 이었다.\n") == []


def test_percent_then_fraction_form():
    assert crit("## Results\n47.9% (23/48) 이었다.\n") == []
    assert len(crit("## Results\n41.0% (23/48) 이었다.\n")) == 1


def test_english_of_form():
    assert crit("## Results\n23 of the 48 patients (47.9%) responded.\n") == []
    assert len(crit("## Results\n23 of the 48 patients (41.0%) responded.\n")) == 1


def test_korean_jung_form():
    assert crit("## Results\n48명 중 23명 (47.9%) 이 반응하였다.\n") == []
    assert len(crit("## Results\n48명 중 23명 (41.0%) 이 반응하였다.\n")) == 1


def test_numerator_larger_than_denominator():
    got = crit("## Results\n60/48 (125.0%) 이었다.\n")
    assert len(got) == 1
    assert "분자" in got[0].message


def test_inferred_denominator_is_only_a_warning():
    report = analyze_text("## Results\n전체 48명 중 반응자는 23명 (41.0%) 이었다.\n")
    levels = {f.level for f in findings(report, item="비율")}
    assert "치명" not in levels


def test_inferred_denominator_detects_the_error():
    text = "## Results\n전체 48명에서 반응자는 23 (41.0%) 이었다.\n"
    got = warn(text)
    assert len(got) == 1
    assert "48" in got[0].message


def test_ambiguous_denominator_is_skipped_not_guessed():
    text = "## Results\n등록 48명, 분석 46명에서 반응자는 23 (41.0%) 이었다.\n"
    assert warn(text) == []
    report = analyze_text(text)
    skipped = [c for c in report.claims if not c.checked and "비율" in c.item]
    assert any("분모 없음" in c.skip_reason for c in skipped)


def test_denominator_never_crosses_a_sentence_boundary():
    """앞 문장의 선별 N 을 뒤 문장의 반응률 분모로 삼으면 정직한 원고가 시끄러워진다."""
    text = ("## Results\n연구 기간 동안 총 120명이 선별되었다. "
            "이 중 능동자극군에서 15명 (65.2%) 이 4주째 반응을 보였다.\n")
    assert warn(text) == []


def test_percent_change_is_not_treated_as_a_proportion():
    """'23 (41.0% 감소)' 의 분모는 전체 N 이 아니다 — 손대면 안 된다."""
    text = "## Results\n총 48명이었다. 점수는 23 (41.0%) 감소하였다.\n"
    assert warn(text) == []


def test_reference_section_numbers_are_never_flagged():
    text = ("## Results\n반응자 23/48 (47.9%).\n\n"
            "## References\n1. Kim S. Trial. J Sleep. 2019;28(3):112-120. 50/10 (99.9%)\n")
    report = analyze_text(text)
    assert findings(report, "치명") == []
    assert any(c.skip_reason == "참고문헌 인용" for c in report.claims)


def test_ci_level_percent_is_not_counted_as_a_claim():
    """'95% CI' 의 95 를 비율 후보로 세면 커버리지 자백이 부풀어 거짓말이 된다."""
    report = analyze_text("## Results\n차이는 -3.5 (95% CI -5.9 to -1.1) 였다.\n")
    assert not any(c.item == "비율" and c.reported == "95%" for c in report.claims)


def test_table_cell_proportions_are_checked():
    text = "## Results\n\n| 군 | 반응자 |\n|---|---|\n| 능동 | 14/23 (41.0%) |\n"
    assert len(crit(text)) == 1


def test_zero_denominator_is_skipped():
    report = analyze_text("## Results\n0/0 (0.0%) 이었다.\n")
    assert findings(report, "치명", "비율") == []


def test_coverage_records_uncheckable_percentages():
    report = analyze_text("## Results\n순응도는 92.3% 였다.\n")
    percent_claims = [c for c in report.claims if c.item == "비율"]
    assert percent_claims and all(not c.checked for c in percent_claims)
    assert percent_claims[0].skip_reason == "분모 없음"
