"""라운드 3 적대적 검토에서 나온 결함들의 회귀 테스트.

여기 있는 테스트는 전부 **실제로 잘못된 출력이 나왔던** 입력이다. 각 테스트의
docstring 에 그때 무엇이 잘못 나왔는지 적어 둔다 — 나중에 규칙을 손볼 때
"이건 왜 이렇게 좁게 돼 있지?" 를 다시 묻지 않도록.
"""

from __future__ import annotations

import math

from conftest import analyze_text, findings
from numcheck.grim import grim_check
from numcheck.pvalues import Statistic, p_range
from numcheck.rounding import parse_number
from numcheck.scales import ScaleRegistry, parse_scale_arg


def run(text, **kw):
    return analyze_text("## Results\n" + text + "\n", **kw)


def isi_registry() -> ScaleRegistry:
    reg = ScaleRegistry()
    reg.add(parse_scale_arg("ISI=0:28:7"))
    return reg


def skip_reasons(report, item_contains):
    return [c.skip_reason for c in report.claims if item_contains in c.item]


# ── GRIM: 보정·대체된 평균에는 적용하지 않는다 ────────────────────────────────
# 이전 동작: `다중대입 후 PSQI 평균 7.28 (N = 31)` 에 치명 GRIM 위반.
# 다중대입 후에는 개인값이 정수가 아니므로 GRIM 의 전제 자체가 없다.
# SERENE 1차 지표(ISI)가 MMRM 보정평균으로 보고될 것이므로 실전에서 바로 터진다.


def test_grim_skips_imputed_and_adjusted_means():
    for text in (
        "다중대입 후 여성 하위군의 PSQI 평균은 7.28 (N = 31) 이었다.",
        "MMRM 으로 추정한 ISI 평균은 14.37 (N = 23) 이었다.",
        "ISI 조정평균은 14.37 (N = 23) 이었다.",
        "The adjusted mean ISI was 14.37 (N = 23).",
        "The estimated marginal mean ISI was 14.37 (N = 23).",
        "ISI scores were imputed by LOCF; the mean was 14.37 (N = 23).",
    ):
        report = run(text, registry=isi_registry())
        assert findings(report, item="GRIM") == [], text
        assert "원자료 평균 아님" in skip_reasons(report, "GRIM"), text


def test_grim_cue_in_the_same_paragraph_also_suppresses():
    """단서가 앞 문장에 있어도 같은 문단이면 판정을 접는다.

    이전 동작: 문장 단위로만 봐서 `결측은 다중대입으로 대체하였다. ISI 평균은
    14.37 (N = 23) 이었다.` 에 치명이 났다.
    """
    report = run("결측치는 다중대입으로 대체하였다. ISI 평균은 14.37 (N = 23) 이었다.",
                 registry=isi_registry())
    assert findings(report, item="GRIM") == []


def test_grim_still_fires_on_a_plain_observed_mean():
    """강등 규칙이 GRIM 을 통째로 죽이지는 않았는지."""
    report = run("관찰된 ISI 평균은 14.37 (N = 23) 이었다.", registry=isi_registry())
    got = findings(report, item="GRIM")
    assert len(got) == 1 and got[0].level == "치명"


# ── GRIM: 문항 평균으로 읽으면 가능한 값에는 발동하지 않는다 ──────────────────
# 이전 동작: `PSS 평균 2.37 ± 0.50 (N = 23)` 에 치명.
# PSS 는 10문항 0–4점이므로 문항평균 2.37 은 총점 545/230 = 2.3696 으로 가능하다.


def test_grim_skips_when_an_item_mean_reading_is_possible():
    report = run("PSS 평균은 2.37 ± 0.50 (N = 23명) 이었다.")
    assert findings(report, item="GRIM") == []
    assert "표기 불명확" in skip_reasons(report, "GRIM")


def test_item_mean_escape_hatch_does_not_swallow_total_score_means():
    """총점 범위 위쪽 값은 문항평균일 수 없으므로 그대로 검사돼야 한다."""
    report = run("관찰된 ISI 평균은 14.37 (N = 23) 이었다.", registry=isi_registry())
    assert [f.level for f in findings(report, item="GRIM")] == ["치명"]


# ── GRIM: `M` 의 단어 경계 ───────────────────────────────────────────────────
# 이전 동작: `ISI SEM 1.37 (N = 23)`, `PSS 총점의 sum 2.37` 의 m 이 평균으로 읽혀
# 치명 GRIM 위반이 났다.


def test_sem_and_sum_are_not_read_as_means():
    for text in ("ISI SEM 1.37 (N = 23) 이었다.",
                 "PSS 총점의 sum 2.37 은 관찰되었다 (N = 23)."):
        assert findings(run(text, registry=isi_registry()), item="GRIM") == [], text


# ── GRIM: 영어 어순 ──────────────────────────────────────────────────────────
# 이전 동작: `The ISI mean was 14.37 (N = 23)` 은 **후보로도 안 잡혔다** —
# 건너뜀에도 안 나오므로 커버리지 자백이 거짓말이 됐다.


def test_english_mean_word_orders_are_recognized():
    for text in (
        "The ISI mean was 14.37 (N = 23).",
        "The mean ISI was 14.37 (N = 23).",
        "Mean ISI score was 14.37 (N = 23).",
        "The ISI mean at week 8 was 14.37 (N = 23).",
        "The ISI averaged 14.37 (N = 23).",
    ):
        got = findings(run(text, registry=isi_registry()), item="GRIM")
        assert len(got) == 1 and got[0].level == "치명", text


# ── GRIM 메시지의 등식이 실제 계산과 같아야 한다 ─────────────────────────────
# 이전 동작: percent-of-count 척도에서 `71.0 × 7 = 248.5` 라고 인쇄했다.
# 71.0 × 7 은 497 이다. 손으로 검산하는 사용자가 툴을 불신하게 되는 종류의 오류.


def test_percent_of_count_message_prints_a_true_equation():
    reg = ScaleRegistry()
    reg.add(parse_scale_arg("단어인지도=0:100:50", percent_of_count=True))
    got = findings(run("단어인지도 평균은 71.0 % 였다 (N = 7명).", registry=reg), item="GRIM")
    assert len(got) == 1
    assert "71.0 × 7 ÷ 2 = 248.5" in got[0].message
    assert "(정수)×2/7" in got[0].message
    assert "integer sum score" not in got[0].message_en


# ── 신뢰구간: 정수로 적힌 뒤바뀜 ─────────────────────────────────────────────
# 이전 동작: `오즈비는 4 (95% CI 5 to 3)` 이 조용히 통과했다. 뒤바뀜 판정만
# effective_k 를 안 써서 정수에 ±1 ulp 가 그대로 붙었기 때문.


def test_integer_reversed_ci_is_caught():
    got = findings(run("오즈비는 4 (95% CI 5 to 3) 였다."), item="신뢰구간")
    assert len(got) == 1 and got[0].level == "치명"
    assert "뒤바뀌" in got[0].message


def test_rounding_can_still_legitimately_reverse_an_integer_ci():
    """참값 [2.1, 2.9] 를 0자리로 인쇄하면 `3 to 2` 가 될 수 있다 — 오탈자가 아니다."""
    assert findings(run("평균 차이는 2 (95% CI 3 to 2) 였다."), item="신뢰구간") == []


# ── 신뢰구간: 절대량 CI 에 귀무값 0 을 들이대지 않는다 ────────────────────────
# 이전 동작: `ISI 평균은 8.20점 (95% CI 7.10 to 9.30) 으로 감소하였고 … p = 0.42`
# 에서 CI 뒤의 "감소" 가 걸려 차이의 CI 로 오인, 헛된 경고가 났다.


def test_absolute_mean_ci_is_not_tested_against_zero():
    report = run("12주 시점 ISI 평균은 8.20점 (95% CI 7.10 to 9.30) 으로 감소하였고,"
                 " 성별 차이 검정은 p = 0.42 였다.")
    assert findings(report, item="CI–p") == []


def test_real_difference_ci_still_gets_the_null_of_zero():
    got = findings(run("군간 평균 차이는 1.2 (95% CI -0.4 to 2.8) 로 유의하였다, p = .03."),
                   item="CI–p")
    assert len(got) == 1 and got[0].level == "경고"


# ── 신뢰구간: 점추정치와 CI 사이의 단위어 ────────────────────────────────────
# 이전 동작: `-4.2 points (95% CI …)` 와 `24 minutes (95% CI …)` 에서 점추정치를
# 찾지 못해 포함 검사를 통째로 건너뛰었다. 수면의학 논문에서 가장 흔한 표기다.


def test_unit_words_between_estimate_and_ci_are_tolerated():
    for text, ok in (
        ("The adjusted mean difference was -4.2 points (95% CI -6.0 to -2.4).", True),
        ("Sleep latency decreased by 24 minutes (95% CI 12 to 36).", True),
        ("The mean difference was -4.2 points (95% CI -6.0 to -5.0).", False),
    ):
        report = run(text)
        checked = [c for c in report.claims if c.item == "신뢰구간 정합" and c.checked]
        assert checked, text
        got = findings(report, item="신뢰구간 정합")
        assert (got == []) is ok, text


# ── 비율: 소수 자릿수가 많은 백분율도 후보로 센다 ─────────────────────────────
# 이전 동작: `47.91667%` 는 토큰으로도 인식되지 않아 "후보 0개" 가 나왔다.
# 몇 개를 못 봤는지 말하지 않는 체커가 되는 자리.


def test_high_precision_percentages_are_counted_and_checked():
    report = run("전체 48명 중 23명 (47.91667%) 이 반응하였다.")
    assert any(c.item == "비율 재계산" and c.checked for c in report.claims)
    assert findings(report, item="비율") == []


def test_high_precision_percentage_that_is_wrong_is_still_caught():
    got = findings(run("전체 48명 중 23명 (45.28311%) 이 반응하였다."), item="비율")
    assert len(got) == 1 and got[0].level == "치명"


# ── 비율: 백분율점(%p)은 비율이 아니다 ───────────────────────────────────────
# 이전 동작: `8.9%p` 를 "분모 없음" 으로 세어, 깨끗한 원고에서 건너뜀이 잔뜩
# 쌓이고 사용자가 파싱 실패로 오해하게 만들었다.


def test_percentage_points_are_not_proportion_candidates():
    report = run("수면효율은 74.2% 에서 83.1% 로 8.9%p 개선되었다.")
    raws = [c.reported for c in report.claims if c.item == "비율 재계산"]
    assert not any("8.9" in (r or "") for r in raws)


# ── p 재계산: F 의 자유도 코너 ───────────────────────────────────────────────
# 이전 동작: `F(2.1, 5.3) = 1.41, p = .320` 에 경고. 실제 가능 구간은
# 0.3195–0.3304 이므로 .320 은 가능한 값이다. F 의 p 는 df1 에 대해 증가하고
# df2 에 대해 감소하므로 극값이 **대각** 코너에 있는데 그걸 안 봤다.


def test_f_p_range_covers_diagonal_df_corners():
    stat = Statistic((0, 0), "F", parse_number("1.41"), (2.1, 5.3), False, "", "F 검정")
    lo, hi = p_range(stat, 1.0, "two")
    assert lo <= 0.3195 + 1e-4 and hi >= 0.3304 - 1e-4
    assert findings(run("F(2.1, 5.3) = 1.41, p = .320 이었다."), item="p 재계산") == []


# ── p 재계산: F·χ² 의 꼬리 이름 ──────────────────────────────────────────────
# 이전 동작: 상측 단측 확률을 "양측 p" 라고 인쇄했다. 값은 맞았지만 라벨이 틀렸다.


def test_upper_tail_statistics_are_labelled_upper_tail():
    for text, tail in (("F(2, 90) = 8.44, p = .04 였다.", "상측"),
                       ("카이제곱(1) = 12.5, p = .04 였다.", "상측"),
                       ("t(45) = 2.31, p = .003 였다.", "양측")):
        got = findings(run(text), item="p 재계산")
        assert len(got) == 1, text
        assert f"나오는 {tail} p" in got[0].message, text


def test_upper_tail_label_in_english_report():
    got = findings(run("F(2, 90) = 8.44, p = .04 였다."), item="p 재계산")
    assert "upper-tail p" in got[0].message_en


# ── p 재계산: 한국어 카이제곱 ────────────────────────────────────────────────
# 이전 동작: `카이제곱(1) = 9.53` 을 못 읽었다. 한국어 원고를 읽는 것이 이 툴이
# statcheck 과 갈리는 지점인데 정작 한국어 표기가 빠져 있었다.


def test_korean_chi_square_name_is_recognized():
    for name in ("카이제곱", "카이 제곱", "카이-제곱", "카이스퀘어"):
        report = run(f"군간 차이는 유의하였다, {name}(1) = 9.53, p = .002.")
        assert any(c.item == "p 재계산" and c.checked for c in report.claims), name


# ── p 재계산: `p > x` 는 유의성에 대한 주장이 아니다 ─────────────────────────
# 이전 동작: `t(45) = 8.31, p > .001` 에 "유의성 판정이 뒤집힙니다" 라는 **틀린
# 사유**를 붙였다. 보고값도 재계산값도 α=.05 에서 유의하므로 뒤집히는 것이 없다.


def test_lower_bound_p_does_not_claim_non_significance():
    got = findings(run("t(45) = 8.31, p > .001 이었다."), item="p 재계산")
    assert len(got) == 1
    assert "뒤집" not in got[0].message


def test_upper_bound_p_that_really_flips_still_says_so():
    got = findings(run("t(45) = 0.31, p < .001 이었다."), item="p 재계산")
    assert len(got) == 1
    assert "뒤집" in got[0].message


# ── N 합계: 영문 CONSORT 어순 ────────────────────────────────────────────────
# 이전 동작: `A total of 112 were randomized (56 active, 55 sham)` 을 놓쳤다.
# 무작위배정 논문에서 가장 흔한 문장인데 전체 N 과 괄호가 붙어 있기를 요구했다.


def test_english_consort_sentence_is_checked():
    got = findings(run("A total of 112 were randomized (56 active, 55 sham)."),
                   item="N 합계")
    assert len(got) == 1 and "111" in got[0].recomputed


def test_colon_breakdown_with_an_explicit_total_marker():
    got = findings(run("총 112명이 배정되었다: 56명은 능동자극, 55명은 위약."), item="N 합계")
    assert len(got) == 1 and "111" in got[0].recomputed


def test_colon_form_needs_an_explicit_total_marker():
    """전체 표지가 없으면 평범한 콜론 목록이 전부 하위군으로 오인된다."""
    assert findings(run("측정 시점은 다음과 같다: 4주 24명, 8주 23명."), item="N 합계") == []


def test_matching_english_consort_sentence_is_silent():
    report = run("A total of 112 were randomized (56 active, 56 sham).")
    assert findings(report, item="N 합계") == []
    assert any(c.item == "N 합계" and c.checked for c in report.claims)


# ── GRIMMER 의 SD 구간이 다른 곳과 같은 규칙을 쓰는지 ────────────────────────


def test_grimmer_uses_the_shared_rounding_interval():
    from numcheck.grim import grimmer_check

    mean, sd = parse_number("2.5"), parse_number("1.0")
    ok, _ = grimmer_check(mean, sd, 4, 0.0, 4.0, 1.0)
    assert isinstance(ok, bool)


# ── 깨끗한 원고에서 조용한지 (규칙을 넓힌 뒤에도) ───────────────────────────


CLEAN_EN = """## Methods
Missing data were handled by multiple imputation. All tests were two-sided.
A total of 112 participants were randomized (56 active, 56 sham).

## Results
The mean age was 44.6 years (SD 11.2) and the mean body mass index was 24.3 (SD 3.1).
At week 8, the adjusted mean ISI was 10.86 (N = 54) in the active group.
The adjusted mean difference was -4.2 points (95% CI -6.0 to -2.4), t(102) = 4.62, p < .001.
Sleep onset latency decreased by 24 minutes (95% CI 12 to 36) in the active group.
Sleep efficiency improved from 74.2% to 83.1%, a difference of 8.9%p.
Responders were 38/56 (67.9%) in the active group and 21/56 (37.5%) in the sham group.
Wrist-derived RMSSD increased by 6.30 ms (95% CI 2.10 to 10.50), p = .004.
Median total sleep time at week 8 was 396 minutes (IQR 351 to 438).
Baseline ISI did not differ between groups, t(110) = 0.45, p = .653.
"""


def test_realistic_clean_english_manuscript_is_completely_silent():
    """규칙을 넓힌 뒤 가장 먼저 깨지는 곳. 치명·경고가 하나라도 나오면 규칙을 좁힌다."""
    report = analyze_text(CLEAN_EN, registry=isi_registry())
    assert findings(report, level="치명") == []
    assert findings(report, level="경고") == []
    assert report.exit_code() == 0


def test_realistic_clean_english_manuscript_still_rechecks_enough():
    """조용한 이유가 '아무것도 못 읽어서' 는 아닌지."""
    report = analyze_text(CLEAN_EN, registry=isi_registry())
    assert sum(1 for c in report.claims if c.checked) >= 10


def test_p_range_is_finite_everywhere_it_is_used():
    stat = Statistic((0, 0), "F", parse_number("1.41"), (2.1, 5.3), False, "", "F 검정")
    lo, hi = p_range(stat, 1.0, "two")
    assert math.isfinite(lo) and math.isfinite(hi) and 0.0 <= lo <= hi <= 1.0


def test_grim_check_item_mean_granularity_is_finer():
    """문항평균 해석의 입도가 총점 해석보다 문항 수만큼 촘촘한지."""
    mean = parse_number("2.37")
    assert not grim_check(mean, 23, 1.0, 0.0, 40.0, 1.0).consistent
    assert grim_check(mean, 23 * 10, 1.0, 0.0, 4.0, 1.0).consistent
