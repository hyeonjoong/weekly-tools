"""1차 적대적 검토(라운드 1)에서 나온 결함의 회귀 테스트.

각 테스트는 **그 결함이 되살아나면 반드시 실패**해야 한다. 돌연변이 테스트로
"이 단정문이 정말 버그를 잡는가"를 확인한 것만 남겼다 (HARDENING.md 참조).
"""

import json
import math
import os
import subprocess
import sys

import pytest

from powerplan.cli import main
from powerplan.designs import (
    CorrelationTest,
    EquivalenceT,
    NonInferiorityT,
    OneSampleT,
    OneWayAnova,
    PairedT,
    TwoProportions,
    TwoSampleT,
)
from powerplan.distributions import MAX_NCF_TERMS, ncf_sf, nct_cdf, nct_sf, t_cdf, t_ppf
from powerplan.pilot import effect_from_paired, effect_from_two_group, read_paired, read_two_group
from powerplan.precision import icc_ci_width, icc_plan, loa_half_width, loa_plan
from powerplan.report import protocol_sentences, render_json, render_markdown, render_text
from powerplan.solve import Adjustments, make_plan, smallest_unit
from powerplan.special import betainc, log_beta, norm_cdf, norm_ppf
from powerplan.validate import PowerPlanError

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")
SERENE = os.path.join(EXAMPLES, "serene_pilot.csv")
WOWFIT = os.path.join(EXAMPLES, "wowfit_pilot.csv")


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------
# 정확도: 연속성 보정 (양쪽 꼬리 모두 보정이 '빼지는' 방향)
# --------------------------------------------------------------------------
def _yates_power(p1, p2, n, alpha=0.05, sides=2):
    """테스트 안에서 독립적으로 다시 계산한 Yates 보정 검정력."""
    delta = abs(p1 - p2)
    inv = 2.0 / n
    pbar = 0.5 * (p1 + p2)
    se0 = math.sqrt(pbar * (1 - pbar) * inv)
    se1 = math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    bound = 0.5 * inv + norm_ppf(1 - alpha / sides) * se0
    out = norm_cdf((delta - bound) / se1)
    if sides == 2:
        out += norm_cdf((-delta - bound) / se1)
    return out


@pytest.mark.parametrize("p1,p2,n,expected", [
    (0.87, 0.89, 50, 0.029934),      # 라운드1 지적: 예전 코드가 0.049892를 반환
    (0.05, 0.06, 100, 0.029900),     # 라운드1 지적: 예전 코드가 0.0을 반환
    (0.30, 0.50, 103, 0.801042),
])
def test_continuity_correction_matches_yates_definition(p1, p2, n, expected):
    got = TwoProportions(p1, p2, continuity=True).power(n)
    assert got == pytest.approx(expected, abs=1e-6)
    assert got == pytest.approx(_yates_power(p1, p2, n), abs=1e-12)


def test_continuity_correction_never_returns_zero_when_power_exists():
    """보정이 효과를 다 먹어도 우연에 의한 기각 확률은 남는다."""
    power = TwoProportions(0.05, 0.06, continuity=True).power(100)
    assert 0.02 < power < 0.05


def test_continuity_correction_is_symmetric_and_one_sided_works():
    for n in (30, 100):
        assert TwoProportions(0.3, 0.5, continuity=True).power(n) == pytest.approx(
            TwoProportions(0.5, 0.3, continuity=True).power(n), abs=1e-12)
        assert TwoProportions(0.3, 0.5, sides=1, continuity=True).power(n) == pytest.approx(
            _yates_power(0.3, 0.5, n, sides=1), abs=1e-12)


def test_pooled_proportion_is_allocation_weighted():
    """배분비가 1이 아니면 합동비율은 n으로 가중돼야 한다 (돌연변이 킬러)."""
    design = TwoProportions(0.1, 0.4, ratio=0.5)
    n1, n2 = 60.0, 30.0
    pbar = (n1 * 0.1 + n2 * 0.4) / (n1 + n2)
    inv = 1 / n1 + 1 / n2
    se0 = math.sqrt(pbar * (1 - pbar) * inv)
    se1 = math.sqrt(0.1 * 0.9 / n1 + 0.4 * 0.6 / n2)
    bound = norm_ppf(0.975) * se0
    expect = norm_cdf((0.3 - bound) / se1) + norm_cdf((-0.3 - bound) / se1)
    assert design.power(60) == pytest.approx(expect, abs=1e-12)
    # 단순 평균 합동비율(0.25)을 쓰면 눈에 띄게 달라진다
    naive_se0 = math.sqrt(0.25 * 0.75 * inv)
    naive = norm_cdf((0.3 - norm_ppf(0.975) * naive_se0) / se1)
    assert abs(design.power(60) - naive) > 0.02


# --------------------------------------------------------------------------
# 정확도: 각 설계의 두 꼬리 / 검정의 크기 (돌연변이 킬러)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("design,alpha", [
    (TwoSampleT(1e-9), 0.05),
    (TwoSampleT(1e-9, alpha=0.01), 0.01),
    (PairedT(1e-9), 0.05),
    (OneSampleT(1e-9), 0.05),
    (CorrelationTest(1e-9), 0.05),
    (TwoProportions(0.4, 0.4 + 1e-9), 0.05),
])
def test_two_sided_test_size_equals_alpha(design, alpha):
    """효과 ≈ 0에서 기각률 = α. 아래쪽 꼬리를 빼먹으면 α/2가 되어 실패한다."""
    assert design.power(600) == pytest.approx(alpha, abs=2e-3)


@pytest.mark.parametrize("design,alpha", [
    (TwoSampleT(1e-9, sides=1), 0.05),
    (PairedT(1e-9, sides=1), 0.05),
    (CorrelationTest(1e-9, sides=1), 0.05),
    (TwoProportions(0.4, 0.4 + 1e-9, sides=1), 0.05),
])
def test_one_sided_test_size_equals_alpha(design, alpha):
    assert design.power(600) == pytest.approx(alpha, abs=2e-3)


def test_one_sided_power_ignores_effect_sign():
    """단측에서도 |d|를 쓴다 — abs()를 빼면 음수 효과의 검정력이 뒤집힌다."""
    for n in (10, 40, 120):
        assert TwoSampleT(-0.5, sides=1).power(n) == pytest.approx(
            TwoSampleT(0.5, sides=1).power(n), abs=1e-14)
        assert PairedT(-0.4, sides=1).power(n) == pytest.approx(
            PairedT(0.4, sides=1).power(n), abs=1e-14)


def test_noninferiority_power_matches_noncentral_t_definition():
    """noninf의 df와 ncp를 정의식으로 다시 계산해 대조 (df 오프바이원 킬러)."""
    margin, sd, n = 3.0, 8.0, 113
    df = 2 * n - 2
    ncp = margin / (sd * math.sqrt(2.0 / n))
    expect = nct_sf(t_ppf(0.975, df), df, ncp)
    assert NonInferiorityT(margin, sd).power(n) == pytest.approx(expect, abs=1e-12)
    # 알려진 기준값 (Julious 2010 정규근사 112 → 정확 t 기준 113)
    assert smallest_unit(NonInferiorityT(3, 8), 0.80) == 113
    assert smallest_unit(NonInferiorityT(3, 8), 0.90) == 151


def test_equivalence_low_power_region_is_not_swallowed():
    """TOST 적분의 hi<=lo 클램프가 사라지면 이 값들이 크게 틀린다."""
    assert EquivalenceT(5, 8, 4.5).power(15) == pytest.approx(0.02589, abs=5e-4)
    assert EquivalenceT(5, 8, 0.0).power(10) == pytest.approx(0.01423, abs=5e-4)
    assert EquivalenceT(4, 6, 3.9).power(8) >= 0.0


def test_tost_power_is_monotone_once_not_negligible():
    """TOST 검정력은 아주 작은 영역에서 비단조일 수 있다 — 실용 영역에선 단조."""
    design = EquivalenceT(5, 8, 0.0)
    powers = [design.power(n) for n in range(8, 200)]
    meaningful = [p for p in powers if p > 1e-4]
    assert all(b >= a - 1e-9 for a, b in zip(meaningful, meaningful[1:]))


# --------------------------------------------------------------------------
# 정확도: ANCOVA / 변화량 설계배율
# --------------------------------------------------------------------------
def test_ancova_design_factor_and_df():
    """ANCOVA: d' = d/√(1−r²), df = n1+n2−3, 분산 팽창 1+1/(N−3).

    공변량 불균형에 따른 분산 팽창을 빼먹으면 검정력이 0.5~1.5%p 과대평가된다
    (모의실험으로 확인). Borm 등(2007)의 "+1명" 규칙이 바로 이 항이다.
    """
    d, r, n = 0.309, 0.711, 83
    design = TwoSampleT(d, baseline_r=r, analysis="ancova")
    assert design.design_factor == pytest.approx(1 - r * r)
    assert design.effective_d == pytest.approx(d / math.sqrt(1 - r * r))
    df = 2 * n - 3
    ncp = design.effective_d / math.sqrt((2.0 / n) * (1.0 + 1.0 / df))
    tc = t_ppf(0.975, df)
    expect = nct_sf(tc, df, ncp) + nct_cdf(-tc, df, ncp)
    assert design.power(n) == pytest.approx(expect, abs=1e-12)
    # 팽창 항을 빼면 검정력이 위로 치우친다 (이 테스트가 지키려는 것)
    naive = design.effective_d / math.sqrt(2.0 / n)
    assert nct_sf(tc, df, naive) + nct_cdf(-tc, df, naive) > expect


def test_ancova_reduces_sample_size_as_expected():
    raw = smallest_unit(TwoSampleT(0.309), 0.80)
    assert raw == 166
    # Borm(2007) 근사: n_ancova ≈ n_raw·(1−r²) + 1
    for r in (0.3, 0.5, 0.711, 0.9):
        got = smallest_unit(TwoSampleT(0.309, baseline_r=r, analysis="ancova"), 0.80)
        approx = math.ceil(raw * (1 - r * r)) + 1
        assert abs(got - approx) <= 2, (r, got, approx)
    assert smallest_unit(TwoSampleT(0.309, baseline_r=0.711, analysis="ancova"), 0.80) == 83
    # 분산 팽창을 반영해도 ANCOVA는 여전히 추적값만 비교보다 훨씬 작다
    assert smallest_unit(TwoSampleT(0.309, baseline_r=0.711, analysis="ancova"), 0.80) < 90


def test_change_score_factor_and_break_even_at_r_half():
    """변화량 분석의 배율은 2(1−r) — r = 0.5에서 추적값만 비교와 정확히 같다."""
    change = TwoSampleT(0.5, baseline_r=0.5, analysis="change")
    assert change.design_factor == pytest.approx(1.0)
    for n in (20, 64, 200):
        assert change.power(n) == pytest.approx(TwoSampleT(0.5).power(n), abs=1e-14)
    # r < 0.5면 변화량 분석이 더 불리하고, r > 0.5면 유리하다
    assert (smallest_unit(TwoSampleT(0.5, baseline_r=0.3, analysis="change"), 0.8)
            > smallest_unit(TwoSampleT(0.5), 0.8))
    assert (smallest_unit(TwoSampleT(0.5, baseline_r=0.8, analysis="change"), 0.8)
            < smallest_unit(TwoSampleT(0.5), 0.8))
    # 같은 r이면 ANCOVA가 항상 변화량 분석보다 효율적이다
    for r in (0.3, 0.6, 0.9):
        assert (smallest_unit(TwoSampleT(0.4, baseline_r=r, analysis="ancova"), 0.8)
                <= smallest_unit(TwoSampleT(0.4, baseline_r=r, analysis="change"), 0.8))


def test_ancova_requires_baseline_correlation():
    with pytest.raises(PowerPlanError, match="baseline-r"):
        TwoSampleT(0.5, analysis="ancova")
    with pytest.raises(PowerPlanError, match="analysis"):
        TwoSampleT(0.5, baseline_r=0.5, analysis="mmrm")
    with pytest.raises(PowerPlanError, match="baseline-r"):
        TwoSampleT(0.5, baseline_r=1.0, analysis="ancova")
    # 설계명·근거문헌이 분석 방법에 따라 바뀐다
    ancova = TwoSampleT(0.5, baseline_r=0.7, analysis="ancova")
    assert "ANCOVA" in ancova.test_kr and "ANCOVA" in ancova.test_en
    assert any("Frison" in r for r in ancova.references())


# --------------------------------------------------------------------------
# 정밀도 설계: 경계와 검증
# --------------------------------------------------------------------------
def test_loa_plan_minimal_n_at_the_boundary():
    """lo=2가 이미 충분한 경우에도 최소값을 지켜야 한다 (오프바이원)."""
    assert loa_half_width(2, 1.0) <= 20.0
    assert loa_plan(1.0, 20.0)["n"] == 2
    assert loa_plan(1.0, 100.0)["n"] == 2


def test_icc_ci_width_validates_raters():
    with pytest.raises(PowerPlanError, match="raters"):
        icc_ci_width(50, 0.8, 1)
    with pytest.raises(PowerPlanError, match="icc"):
        icc_ci_width(50, 1.5, 2)
    with pytest.raises(PowerPlanError):
        icc_ci_width(1, 0.8, 2)


def test_precision_sentences_follow_alpha():
    """--alpha를 바꾸면 프로토콜 문장의 신뢰수준도 따라가야 한다."""
    for alpha, level in ((0.05, "95%"), (0.10, "90%"), (0.01, "99%")):
        icc = protocol_sentences(icc_plan(0.8, 0.15, 2, alpha))
        loa = protocol_sentences(loa_plan(2.0, 0.5, alpha))
        for sentences in (icc, loa):
            assert level in sentences["kr"], (alpha, sentences["kr"])
            assert level in sentences["en"], (alpha, sentences["en"])
        if alpha != 0.05:
            assert "95%" not in icc["kr"] and "95%" not in icc["en"]
            assert "95%" not in loa["kr"] and "95%" not in loa["en"]


# --------------------------------------------------------------------------
# 군집설계 3단 구조 (유효 → 분석 → 모집)
# --------------------------------------------------------------------------
def test_cluster_analysis_population_includes_design_effect():
    plan = make_plan(TwoSampleT(0.4), target_power=0.80,
                     adjustments=Adjustments(cluster_size=10, cluster_icc=0.05,
                                             dropout=0.1))
    assert plan["effective"]["allocation"]["n1"] == 100
    assert plan["analysis"]["allocation"]["n1"] == 145      # 100 × 1.45
    assert plan["enrollment"]["allocation"]["n1"] == 170    # 17 군집 × 10명
    text = render_text(plan)
    assert "유효 표본수(개인배정 기준)" in text
    assert "군당 145명" in text and "군당 170명" in text
    # 프로토콜 문장의 '분석 대상'도 DE가 반영된 값이어야 한다
    assert "145" in protocol_sentences(plan)["kr"]
    # 모집 인원과 군집 기준 인원이 모순되지 않는다
    assert "162" not in text


def test_cluster_round_trip_power():
    """모집 n을 그대로 --n으로 넣으면 목표 검정력을 만족해야 한다."""
    adj = Adjustments(cluster_size=10, cluster_icc=0.05, dropout=0.1)
    plan = make_plan(TwoSampleT(0.4), target_power=0.80, adjustments=adj)
    enrolled = plan["enrollment"]["allocation"]["n1"]
    back = make_plan(TwoSampleT(0.4), unit=enrolled, adjustments=adj)
    assert back["achieved_power"] >= 0.80


# --------------------------------------------------------------------------
# 입력 검증: 예전에 트레이스백으로 죽던 조합
# --------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "2", "--comparisons", "2",
     "--alpha-method", "sidak"],
    ["ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "1.5", "--comparisons", "3"],
    ["ttest2", "--d", "0.5", "--power", "0.8", "--alpha", "0.99", "--comparisons", "100"],
    ["ttest2", "--d", "0.5", "--n", "30", "--power", "-5"],
    ["ttest2", "--d", "0.5", "--n", "30", "--power", "nan"],
    ["ttest2", "--d", "0.5", "--n", "30", "--power", "1e999"],
    ["ttest2", "--d", "0.5", "--n", "10000000000000000"],
    ["ttest2", "--d", "0.5", "--n", "99999999999999999999"],
    ["anova", "--k", "3", "--f", "1e20", "--power", "0.8"],
    ["anova", "--k", "3", "--f", "1e300", "--power", "0.8"],
    ["anova", "--k", "1000", "--f", "50", "--n", "100000"],
    ["icc", "--icc", "0.8", "--width", "0.15", "--alpha", "0.9"],
    ["pilot", SERENE, "--value", "isi_week8", "--group", "arm", "--conf", "1"],
    ["pilot", SERENE, "--value", "isi_week8", "--group", "arm", "--conf", "0"],
    ["pilot", SERENE, "--value", "isi_week8", "--group", "arm", "--conf", "1.5"],
])
def test_no_traceback_for_bad_input(argv, capsys):
    code, out, err = run(argv, capsys)
    assert code == 2, (argv, out[:200])
    assert err.startswith("오류"), (argv, err)
    assert "Traceback" not in err


def test_json_is_always_valid_even_with_extreme_inputs(capsys):
    for argv in (["prop2", "--p1", "1e-320", "--p2", "0.5", "--power", "0.8"],
                 ["equiv", "--margin", "1e300", "--sd", "1e-300", "--power", "0.8"],
                 ["noninf", "--margin", "1e300", "--sd", "1e-300", "--power", "0.8"]):
        code, out, err = run(argv + ["--format", "json"], capsys)
        assert code == 0, err
        payload = json.loads(out)          # NaN/Infinity가 있으면 여기서 실패
        assert "NaN" not in out and "Infinity" not in out
        assert payload["design"]["key"]


def test_extreme_pilot_values_do_not_produce_nan_json(tmp_path, capsys):
    path = tmp_path / "wide.csv"
    path.write_text("pre,post\n1e308,1e308\n-1e308,-1e308\n1,2\n2,4\n3,5\n", encoding="utf-8")
    code, out, err = run(["pilot", str(path), "--pre", "pre", "--post", "post",
                          "--power", "0.8", "--format", "json"], capsys)
    if code == 0:
        json.loads(out)
        assert "NaN" not in out and "Infinity" not in out
    else:
        assert err.startswith("오류")


# --------------------------------------------------------------------------
# 수치 안전장치
# --------------------------------------------------------------------------
def test_betainc_does_not_saturate_when_x_is_tiny():
    """t_cdf가 |t| > 1e8에서 조용히 0/1로 포화되던 문제."""
    assert t_cdf(-1e8, 1) == pytest.approx(3.183098861e-9, rel=1e-6)
    assert t_cdf(-1e12, 1) == pytest.approx(3.183098861e-13, rel=1e-6)
    assert t_ppf(1 - 1e-9, 1) > 3e8
    # betainc 자체도 x가 1e-18이어도 값을 준다
    assert 0.0 < betainc(0.5, 0.5, 1e-18) < 1e-8


def test_huge_df_is_rejected_not_overflowed():
    from powerplan.distributions import MAX_DF
    with pytest.raises(ValueError, match="너무 큽니다"):
        nct_cdf(1.0, MAX_DF * 10, 2.0)
    # 상한 안쪽은 정상 동작
    assert 0.0 <= nct_cdf(1.0, 1e11, 2.0) <= 1.0


def test_ncf_series_length_is_bounded():
    with pytest.raises(ValueError, match="λ"):
        ncf_sf(1.0, 3, 100, 1e12)
    assert MAX_NCF_TERMS <= 500_000
    # 현실적인 λ는 계속 잘 계산된다
    assert ncf_sf(3.0537, 2, 156, 9.9375) == pytest.approx(0.804922, abs=1e-5)


def test_log_beta_is_symmetric_including_large_arguments():
    for a, b in ((500000.0, 0.5), (1e6, 0.5), (1000.0, 3.0), (2.0, 7.0)):
        assert log_beta(a, b) == pytest.approx(log_beta(b, a), rel=1e-13)


# --------------------------------------------------------------------------
# CSV 처리: 라벨 병합, 스트리밍, 깨진 파일, 장치 파일
# --------------------------------------------------------------------------
def test_long_group_labels_are_not_merged(tmp_path):
    """표시용으로 자른 라벨을 집계 키로 쓰면 서로 다른 군이 합쳐졌다."""
    a, b = "X" * 59 + "AAA", "X" * 59 + "BBB"
    path = tmp_path / "long.csv"
    path.write_text("val,arm\n" + "\n".join(
        [f"{v},{a}" for v in (10, 12, 14)] + [f"{v},{b}" for v in (30, 32, 34)]) + "\n",
        encoding="utf-8")
    data = read_two_group(str(path), "val", "arm")
    assert data["group1"]["n"] == 3 and data["group2"]["n"] == 3
    assert data["group1"]["label"] != data["group2"]["label"]
    assert {data["group1"]["mean"], data["group2"]["mean"]} == {12.0, 32.0}


def test_broken_quotes_give_korean_error(tmp_path):
    """따옴표가 안 닫히면 한 칸이 무한정 길어진다 — 상한을 넘으면 그 사실을 말해야 한다."""
    path = tmp_path / "quote.csv"
    path.write_text('val,arm\n1,"unclosed\n' + "x" * 2_000_000 + "\n", encoding="utf-8")
    with pytest.raises(PowerPlanError, match="따옴표"):
        read_two_group(str(path), "val", "arm")


def test_long_field_within_limit_is_not_blamed_on_quotes(tmp_path):
    """자유기술 항목이 128KB를 넘어도(엑셀 메모 등) 정상 처리되어야 한다."""
    long_note = "메" * 200_000
    path = tmp_path / "longfield.csv"
    path.write_text(
        "val,arm,note\n1,a,\"%s\"\n2,a,x\n3,b,y\n4,b,z\n" % long_note,
        encoding="utf-8")
    data = read_two_group(str(path), "val", "arm")
    assert data["group1"]["n"] == 2 and data["group2"]["n"] == 2


def test_non_regular_files_are_rejected(tmp_path):
    fifo = tmp_path / "pipe.fifo"
    os.mkfifo(str(fifo))
    with pytest.raises(PowerPlanError, match="일반 파일"):
        read_two_group(str(fifo), "v", "g")
    if os.path.exists("/dev/zero"):
        with pytest.raises(PowerPlanError, match="일반 파일"):
            read_two_group("/dev/zero", "v", "g")


def test_csv_reading_is_streaming(tmp_path):
    """파일 크기의 몇 배씩 메모리를 쓰지 않는다 (resource로 최대 RSS 확인)."""
    import resource
    path = tmp_path / "big.csv"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("v,g\n")
        for i in range(500_000):
            handle.write(f"{i % 100},a\n{(i % 100) + 5},b\n")
    size_mb = os.path.getsize(path) / 1024 ** 2
    assert size_mb > 4          # 파일이 충분히 큰지 확인
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    data = read_two_group(str(path), "v", "g")
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    grew_mb = (after - before) / (1024 ** 2 if sys.platform == "darwin" else 1024)
    assert data["group1"]["n"] == 500_000
    assert data["group1"]["mean"] == pytest.approx(49.5, rel=1e-12)
    assert grew_mb < size_mb, f"메모리 증가 {grew_mb:.1f}MB ≥ 파일 크기 {size_mb:.1f}MB"


def test_labels_cannot_inject_terminal_or_spreadsheet_payloads(tmp_path):
    path = tmp_path / "inject.csv"
    path.write_text(
        "val,arm\n1,\x1b]0;PWNED\x07evil\n2,\x1b]0;PWNED\x07evil\n"
        "3,=cmd|' /C calc'!A1\n4,=cmd|' /C calc'!A1\n", encoding="utf-8")
    data = read_two_group(str(path), "val", "arm")
    labels = [data["group1"]["label"], data["group2"]["label"]]
    assert all("\x1b" not in label and "\x07" not in label for label in labels)
    assert any(label.startswith("'=") for label in labels)


def test_bidi_and_zero_width_are_stripped(tmp_path):
    path = tmp_path / "bidi.csv"
    path.write_text("val,arm\n1,sham‮gnahs‬\n2,sham‮gnahs‬\n"
                    "3,ok​\n4,ok​\n", encoding="utf-8")
    data = read_two_group(str(path), "val", "arm")
    joined = data["group1"]["label"] + data["group2"]["label"]
    for ch in ("‮", "‬", "​", " ", "﻿"):
        assert ch not in joined


def test_markdown_labels_cannot_break_the_table(tmp_path, capsys):
    path = tmp_path / "pipe.csv"
    path.write_text("val,arm\n1,a|b`code`\n2,a|b`code`\n3,ok\n4,ok\n", encoding="utf-8")
    code, out, err = run(["pilot", str(path), "--value", "val", "--group", "arm",
                          "--power", "0.8", "--format", "md"], capsys)
    assert code == 0, err
    for line in out.splitlines():
        if line.startswith("|") and "---" not in line:
            # 이스케이프되지 않은 파이프가 칸을 늘리지 않았는지
            assert line.count("|") - line.count("\\|") == 3, line


def test_filters_select_rows_and_report_exclusions():
    """중재군과 대조군을 섞어 전후 비교하던 문제 — --filter로 한쪽만."""
    both = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도")
    treated = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도",
                          filters=[("군", "중재")])
    control = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도",
                          filters=[("군", "대조")])
    assert treated["diff"]["n"] == 11 and control["diff"]["n"] == 11
    assert treated["filtered_out"] == 11
    assert both["diff"]["n"] == treated["diff"]["n"] + control["diff"]["n"]
    dz_treated = effect_from_paired(treated)["dz"]
    dz_control = effect_from_paired(control)["dz"]
    dz_pooled = effect_from_paired(both)["dz"]
    assert dz_treated > dz_pooled > dz_control      # 섞으면 희석된다
    with pytest.raises(PowerPlanError, match="조건에 맞는 행이 없"):
        read_two_group(WOWFIT, "훈련후_단어인지도", "군", filters=[("군", "없는군")])


def test_baseline_correlation_is_estimated_from_csv():
    data = read_two_group(SERENE, "isi_week8", "arm", baseline_col="isi_baseline")
    assert data["baseline_r"] == pytest.approx(0.7106, abs=5e-4)
    # 그 r로 ANCOVA 계획을 세우면 표본수가 절반 수준으로 줄어든다
    d = effect_from_two_group(data)["d"]
    raw = smallest_unit(TwoSampleT(d), 0.80)
    ancova = smallest_unit(
        TwoSampleT(d, baseline_r=data["baseline_r"], analysis="ancova"), 0.80)
    assert ancova < raw / 1.8


def test_paired_hedges_g_is_omitted_when_undefined(tmp_path):
    """쌍 2개(df=1)에서는 Hedges 보정계수가 정의되지 않는다."""
    path = tmp_path / "two.csv"
    path.write_text("pre,post\n1,3\n2,5\n", encoding="utf-8")
    data = read_paired(str(path), "pre", "post")
    effect = effect_from_paired(data)
    assert effect["hedges_g"] is None
    assert effect["dz"] != 0
    # df > 1이면 보정값이 있고, 항상 |g| < |dz|
    data2 = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도")
    effect2 = effect_from_paired(data2)
    assert 0 < effect2["hedges_g"] < effect2["dz"]


def test_conservative_d_is_the_bound_closest_to_zero(tmp_path):
    path = tmp_path / "clear.csv"
    rows = ["val,arm"] + [f"{v},a" for v in (10, 11, 12, 13, 14)] \
        + [f"{v},b" for v in (20, 21, 22, 23, 24)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    effect = effect_from_two_group(read_two_group(str(path), "val", "arm"))
    lo, hi = effect["ci"]["low"], effect["ci"]["high"]
    assert lo < 0 and hi < 0                       # 구간이 0을 포함하지 않음
    assert effect["conservative_d"] == pytest.approx(min(abs(lo), abs(hi)))
    assert effect["conservative_d"] < abs(effect["d"])   # 보수적 = 더 작은 효과


def test_invalid_examples_do_not_leak_values_into_saved_output(tmp_path, capsys):
    path = tmp_path / "messy.csv"
    path.write_text("v,g\n1,a\n환자거부,a\n3,a\n5,b\n7,b\n9,b\n", encoding="utf-8")
    code, out, err = run(["pilot", str(path), "--value", "v", "--group", "g",
                          "--power", "0.8", "--skip-invalid", "--format", "json"], capsys)
    assert code == 0, err
    payload = json.loads(out)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "환자거부" not in dumped        # 원본 값은 저장물에 남기지 않는다
    assert payload["pilot"]["data"]["invalid_examples"]
    assert "3행" in payload["pilot"]["data"]["invalid_examples"][0]


def test_pilot_notes_use_basename_not_full_path(capsys):
    code, out, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                          "--power", "0.8"], capsys)
    assert code == 0, err
    assert "serene_pilot.csv" in out
    assert os.path.dirname(SERENE) not in out


# --------------------------------------------------------------------------
# 사전연구 계획 기준 (관측값 vs 신뢰구간 하한)
# --------------------------------------------------------------------------
def test_pilot_plans_on_conservative_bound_by_default(capsys):
    code, out, err = run(["pilot", WOWFIT, "--pre", "훈련전_단어인지도",
                          "--post", "훈련후_단어인지도", "--power", "0.8"], capsys)
    assert code == 0, err
    # 헤드라인·프로토콜 문장이 모두 보수적 기준(35명)을 써야 한다
    assert "▶ 필요한 분석 표본수 : 35명" in out
    assert "35명" in out.split("[KR]")[1].split("[EN]")[0]
    assert "계획 기준: **신뢰구간 하한**" in out


def test_pilot_plan_on_observed_is_opt_in(capsys):
    code, out, err = run(["pilot", WOWFIT, "--pre", "훈련전_단어인지도",
                          "--post", "훈련후_단어인지도", "--power", "0.8",
                          "--plan-on", "observed"], capsys)
    assert code == 0, err
    assert "▶ 필요한 분석 표본수 : 10명" in out
    assert "--plan-on observed" in out


def test_pilot_warns_loudly_when_ci_crosses_zero(capsys):
    code, out, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                          "--power", "0.8"], capsys)
    assert code == 0, err
    assert "⚠ 신뢰구간이 0을 포함" in out
    assert "MCID" in out
    # md 출력에도 같은 경고가 남아야 한다 (예전에는 텍스트에만 있었다)
    code, md, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                         "--power", "0.8", "--format", "md"], capsys)
    assert code == 0, err
    assert "신뢰구간이 0을 포함" in md


def test_pilot_reports_observed_dropout_and_baseline_r(capsys):
    code, out, err = run(["pilot", SERENE, "--value", "isi_week8", "--group", "arm",
                          "--baseline", "isi_baseline", "--power", "0.8"], capsys)
    assert code == 0, err
    assert "--dropout 참고값" in out
    assert "--analysis ancova --baseline-r 0.711" in out


# --------------------------------------------------------------------------
# 출력 파일 안전성
# --------------------------------------------------------------------------
def test_output_file_is_not_silently_overwritten(tmp_path, capsys):
    target = tmp_path / "protocol.md"
    target.write_text("소중한 원고\n", encoding="utf-8")
    code, out, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                          "-o", str(target)], capsys)
    assert code == 2 and "--force" in err
    assert target.read_text(encoding="utf-8") == "소중한 원고\n"
    code, out, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                          "-o", str(target), "--force"], capsys)
    assert code == 0, err
    assert "군당 64명" in target.read_text(encoding="utf-8")


def test_output_file_permissions_are_owner_only(tmp_path, capsys):
    target = tmp_path / "out.txt"
    code, _, err = run(["ttest2", "--d", "0.5", "--power", "0.8", "-o", str(target)], capsys)
    assert code == 0, err
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_output_does_not_follow_symlinks(tmp_path, capsys):
    secret = tmp_path / "secret.txt"
    secret.write_text("비밀\n", encoding="utf-8")
    link = tmp_path / "link.md"
    os.symlink(str(secret), str(link))
    code, _, err = run(["ttest2", "--d", "0.5", "--power", "0.8",
                        "-o", str(link), "--force"], capsys)
    assert code == 2, err
    assert secret.read_text(encoding="utf-8") == "비밀\n"


# --------------------------------------------------------------------------
# 출력 인코딩 / 파이프
# --------------------------------------------------------------------------
def test_survives_ascii_only_stdout():
    """PYTHONIOENCODING=ascii 에서도 트레이스백 없이 종료해야 한다."""
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    root = os.path.dirname(HERE)
    proc = subprocess.run([sys.executable, "-m", "powerplan.cli", "ttest2",
                           "--d", "0.5", "--power", "0.8"],
                          capture_output=True, env=env, cwd=root)
    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert b"Traceback" not in proc.stderr


def test_markdown_render_covers_every_direction():
    """--format md 가 모든 방향/종류에서 깨지지 않는지 (예전엔 미검증 경로)."""
    plans = [
        make_plan(TwoSampleT(0.5), target_power=0.8),
        make_plan(TwoSampleT(0.5), unit=30, target_power=0.8),
        make_plan(OneWayAnova(0.25, 3), unit=40),
        icc_plan(0.8, 0.2), loa_plan(2.0, 0.5),
    ]
    for plan in plans:
        md = render_markdown(plan)
        assert md.startswith("| 항목 |")
        assert json.loads(render_json(plan))
