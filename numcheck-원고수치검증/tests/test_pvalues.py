"""p 재계산 — 반올림 구간, 자동 강등, 짝짓기."""

from __future__ import annotations

import pytest

from conftest import analyze_text, findings
from numcheck.pvalues import find_pvalues, find_statistics, p_range


def check(text, level=None):
    return findings(analyze_text("## Results\n" + text + "\n"), level, "p 재계산")


# ── 파싱 ─────────────────────────────────────────────────────────────────────


def test_find_pvalues_operators():
    ps = find_pvalues("p = .03, P < .001, p > .05, p ≤ .01, p-value = 0.20")
    assert [p.op for p in ps] == ["=", "<", ">", "<=", "="]
    assert ps[1].interval() == (0.0, 0.001)
    assert ps[2].interval() == (0.05, 1.0)


def test_find_pvalues_ignores_non_probabilities():
    assert find_pvalues("p = 1.7") == []


def test_find_statistics_shapes():
    stats = find_statistics("t(45) = 2.31; F(2, 88) = 4.12; χ²(1) = 6.44; "
                            "r(38) = .41; z = 2.05")
    assert [s.kind for s in stats] == ["t", "F", "chi2", "r", "z"]
    assert stats[1].df == (2.0, 88.0)


def test_chi_square_with_sample_size_in_parentheses():
    stats = find_statistics("χ²(1, N = 48) = 6.44")
    assert stats and stats[0].df == (1.0,)


def test_statistic_letters_inside_words_are_ignored():
    assert find_statistics("Pt(45) = 2.31") == []
    assert find_statistics("var(45) = 2.31") == []


def test_p_range_widens_with_rounded_statistic():
    stat = find_statistics("t(45) = 2.31")[0]
    lo, hi = p_range(stat)
    assert lo < 0.02553 < hi
    assert hi - lo > 0


# ── 판정 ─────────────────────────────────────────────────────────────────────


def test_clear_mismatch_is_critical():
    got = check("차이는 컸다, t(45) = 2.31, p = .003.", "치명")
    assert len(got) == 1
    assert "0.0255" in got[0].recomputed


def test_correct_p_is_silent():
    assert check("t(45) = 2.31, p = .026.") == []
    assert check("t(45) = 2.31, p = .03.") == []
    assert check("F(2, 88) = 4.12, p = .019.") == []
    assert check("χ²(1) = 6.44, p = .011.") == []
    assert check("r(38) = .41, p = .009.") == []
    assert check("z = 2.05, p = .040.") == []


def test_inequality_forms_are_honoured():
    assert check("t(45) = 2.31, p < .05.") == []
    assert len(check("t(45) = 2.31, p < .001.", "치명")) == 1
    assert len(check("t(45) = 2.31, p > .05.", "치명")) == 1


def test_one_tailed_keyword_downgrades_to_warning():
    got = check("단측검정에서 t(40) = 1.75, p = .044.", "경고")
    assert len(got) == 1
    assert got[0].downgraded


def test_one_tailed_fit_downgrades_even_without_keyword():
    got = check("총수면시간이 늘었다, t(40) = 1.75, p = .044.", "경고")
    assert len(got) == 1
    assert "단측" in got[0].downgraded


def test_correction_keyword_downgrades_to_warning():
    got = check("Greenhouse-Geisser 보정 후 F(2, 88) = 4.12, p = .04.", "경고")
    assert len(got) == 1
    assert "greenhouse" in got[0].downgraded.lower()


def test_small_difference_without_decision_flip_is_a_warning():
    """0.0255 대 0.02 — 1.3배 차이이고 유의성 판정도 그대로 → 경고."""
    got = check("t(45) = 2.31, p = .0199.")
    assert got and got[0].level == "경고"


def test_decision_flip_is_critical():
    got = check("t(45) = 1.20, p = .012.", "치명")
    assert len(got) == 1
    assert "뒤집" in got[0].message


def test_statistic_without_p_is_recorded_as_skipped():
    report = analyze_text("## Results\n효과가 있었다, t(45) = 2.31.\n")
    claims = [c for c in report.claims if c.item == "p 재계산"]
    assert claims and not claims[0].checked
    assert claims[0].skip_reason == "p 미보고"


def test_p_without_statistic_is_recorded_as_skipped():
    report = analyze_text("## Results\n효과가 있었다 (p = .03).\n")
    claims = [c for c in report.claims if c.item == "p 재계산"]
    assert claims and claims[0].skip_reason == "검정통계량 없음"


def test_statistics_are_paired_within_the_sentence_only():
    """앞 문장의 p 를 뒤 문장의 통계량에 붙이면 헛된 지적이 나온다."""
    report = analyze_text(
        "## Results\n첫 비교는 유의하였다, p = .003. 둘째 비교는 t(45) = 2.31 이었다.\n")
    got = findings(report, item="p 재계산")
    assert got == []


def test_two_statistics_in_one_sentence_pair_with_their_own_p():
    got = check("t(45) = 2.31, p = .026 이고 χ²(1) = 6.44, p = .011 이었다.")
    assert got == []


def test_negative_t_is_handled():
    assert check("t(45) = -2.31, p = .026.") == []


def test_noninteger_df_widens_the_range():
    """Welch 의 소수 자유도는 반올림 구간을 넓혀 오탐을 줄인다."""
    assert check("Welch t(43.7) = 2.31, p = .026.") == []


@pytest.mark.parametrize("text", [
    "r(40) = 1.80, p = .03.",
    "t(0) = 2.31, p = .03.",
    "F(0, 88) = 4.12, p = .04.",
    "χ²(0) = 6.44, p = .01.",
    "chi2(3) = -5, p = .02.",
])
def test_arithmetically_impossible_statistics_are_reported_not_dropped(text):
    """산술적으로 존재할 수 없는 통계량은 **지적한다.**

    라운드 3 이전에는 이런 보고를 조용히 버렸다. 그러면 짝이 없어진 p 가
    '검정통계량 없음' 으로 기록되는데, 같은 줄에 통계량이 버젓이 인쇄돼 있으므로
    그 사유는 원고에 대한 거짓 진술이었다. 산술 오류를 잡는 툴이 산술적으로
    불가능한 값을 못 본 척하는 것은 이 툴이 존재하는 이유와 정면으로 어긋난다.
    """
    report = analyze_text("## Results\n" + text + "\n")
    got = findings(report, item="검정통계량")
    assert len(got) == 1 and got[0].level == "치명", text
    # 짝이 되는 p 가 '검정통계량 없음' 으로 이중 계상되지 않는다.
    assert not any(c.skip_reason == "검정통계량 없음" for c in report.claims), text


def test_impossible_statistic_does_not_also_produce_a_p_recompute():
    report = analyze_text("## Results\nr(40) = 1.80, p = .03.\n")
    assert findings(report, item="p 재계산") == []


def test_a_valid_statistic_is_not_flagged_as_impossible():
    report = analyze_text("## Results\nr(40) = 0.80, p = .03.\n")
    assert findings(report, item="검정통계량") == []
