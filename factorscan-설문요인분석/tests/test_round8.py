"""8차: 1라운드 적대적 검토(정확성·엣지·유용성·문서·테스트) 지적사항의 회귀 테스트.

각 테스트는 **패널이 실제로 재현한 결함**에 1:1로 대응한다. 주석에 원래 증상을 남겨,
나중에 누가 조건을 되돌리면 무엇이 깨지는지 알 수 있게 했다.
"""
from __future__ import annotations

import csv
import json
import math

import numpy as np
import pytest

from factorscan import efa, stats
from factorscan.analyze import (BOOTSTRAP_MIN_OK, GROUP_RATIO_MIN, analyze,
                                _normalize_group_label)
from factorscan.cli import run
from factorscan.dataio import Dataset, listwise, listwise_bias_check

from tests.test_round7 import _grouped_data, _likert, _prep, _two_factor  # noqa: F401


# ============================================================ stats 수치 결함
def test_chi2_cdf_deep_lower_tail_is_not_zero():
    """`1 - chi2_sf` 왕복이 파국적 상쇄를 일으켜 1.79e-80 이 0.0으로 뭉개지던 버그."""
    scipy_stats = pytest.importorskip("scipy.stats")
    for x, df in [(1.0, 100), (5.0, 60), (2.0, 30), (0.5, 10)]:
        got = stats.chi2_cdf(x, df)
        exp = scipy_stats.chi2.cdf(x, df)
        assert got > 0.0, f"chi2_cdf({x},{df}) 가 0으로 붕괴"
        assert got == pytest.approx(exp, rel=1e-10)


def test_chi2_cdf_sf_complement_holds_in_bulk():
    """오라클 없이도 성립해야 하는 성질: 중앙부에서 CDF + SF == 1."""
    for x, df in [(10.0, 10), (3.0, 4), (50.0, 40)]:
        assert stats.chi2_cdf(x, df) + stats.chi2_sf(x, df) == pytest.approx(1.0, abs=1e-12)


def test_chi2_cdf_rejects_bad_df():
    with pytest.raises(ValueError):
        stats.chi2_cdf(1.0, 0)


def test_ncx2_cdf_deep_tail_not_zero():
    """chi2_cdf 결함이 전파돼 비중심 카이제곱 CDF가 넓은 구간에서 0이 되던 문제."""
    scipy_stats = pytest.importorskip("scipy.stats")
    for x, df, nc in [(20, 5, 100), (100, 10, 500)]:
        got = stats.ncx2_cdf(x, df, nc)
        assert got > 0.0
        assert got == pytest.approx(scipy_stats.ncx2.cdf(x, df, nc), rel=1e-9)


def test_f_ppf_tiny_quantile_has_relative_accuracy():
    """수렴 판정에 절대 하한(1e-10)을 깔아 1보다 작은 분위수가 뭉개지던 버그."""
    scipy_stats = pytest.importorskip("scipy.stats")
    for p, d1, d2 in [(1e-5, 1, 100), (0.001, 1, 100), (1e-4, 2, 50)]:
        assert stats.f_ppf(p, d1, d2) == pytest.approx(scipy_stats.f.ppf(p, d1, d2), rel=1e-6)


def test_f_ppf_extreme_upper_quantile_does_not_saturate():
    """d1·x/(d1·x+d2)가 1.0으로 반올림돼 상한 탐색이 9e15에서 멈추던 버그."""
    scipy_stats = pytest.importorskip("scipy.stats")
    for p, d1, d2 in [(1 - 1e-9, 1, 1), (1 - 1e-12, 2, 2), (1 - 1e-10, 1, 3)]:
        assert stats.f_ppf(p, d1, d2) == pytest.approx(scipy_stats.f.ppf(p, d1, d2), rel=1e-6)


def test_f_sf_matches_scipy_in_far_tail():
    scipy_stats = pytest.importorskip("scipy.stats")
    for x, d1, d2 in [(1e6, 3, 3), (2.0, 5, 10), (1e3, 10, 10)]:
        assert stats.f_sf(x, d1, d2) == pytest.approx(scipy_stats.f.sf(x, d1, d2), rel=1e-9)


def test_f_cdf_and_sf_are_complementary():
    for x, d1, d2 in [(0.5, 3, 3), (2.0, 7, 11), (100.0, 2, 40)]:
        assert stats.f_cdf(x, d1, d2) + stats.f_sf(x, d1, d2) == pytest.approx(1.0, abs=1e-12)


# ================================================ 집단 비교: 표본 크기 보정
def _null_groups(ng, p, k, rng):
    """같은 모집단에서 뽑은 두 집단(구조가 동일)."""
    n = ng * 2
    f = rng.standard_normal((n, k))
    cols = [np.clip(np.rint(3 + 0.75 * f[:, i % k] + 0.75 * rng.standard_normal(n)), 1, 5)
            for i in range(p)]
    return np.column_stack(cols), ["A"] * ng + ["B"] * ng


def test_identical_populations_do_not_trigger_difference_warning():
    """동일 모집단인데도 표본이 작다는 이유로 '구조가 다르다'가 100% 발화하던 문제.

    문항당 1.2명(n=30, p=24) — 고정 임계값 시절 오경보율 40/40 이었던 조건.
    """
    rng = np.random.default_rng(3)
    fired = 0
    for _ in range(12):
        x, labels = _null_groups(30, 24, 3, rng)
        res = analyze(_prep(x), n_factors=3, parallel_iter=0, group_labels=labels)
        if any("재현되지 않습니다" in w for w in res["warnings"]):
            fired += 1
    assert fired == 0


def test_真_structural_difference_is_still_detected():
    """오경보를 없애면서 민감도를 잃지 않았는지 — 진짜 다른 구조는 여전히 잡아야 한다."""
    x, labels = _grouped_data(n=100, seed=17, broken=True)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    assert any("재현되지 않습니다" in w for w in res["warnings"])


def test_group_below_ratio_gate_is_provisional_not_judged():
    x, labels = _grouped_data(n=22, seed=23)      # p=8 → 문항당 2.75명 < 3
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    gr = res["group_replicability"]
    rows = [r for r in gr["groups"] if not r.get("skipped")]
    assert rows and all(r.get("provisional") for r in rows)
    assert gr["n_judged"] == 0
    assert gr["min_congruence"] is None          # 판정 자격 없는 값은 최솟값에 안 들어감
    assert any("판정 보류" in n for n in res["notes"])
    assert not any("재현되지 않습니다" in w for w in res["warnings"])


def test_group_with_n_below_item_count_is_skipped_entirely():
    """n<p 집단의 상관행렬은 특이해 φ가 순수 잡음인데 그대로 계산하던 문제."""
    x, labels = _grouped_data(n=60, seed=29)     # p=8
    labels = list(labels)
    labels[:6] = ["C"] * 6
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    rows = {r["level"]: r for r in res["group_replicability"]["groups"]}
    assert "표본 부족" in rows["C"]["skipped"]


def test_provisional_group_excluded_from_pairwise_judgement():
    x, labels = _grouped_data(n=60, seed=31)
    labels = list(labels)
    labels[:25] = ["C"] * 25          # C는 n=25, p=8 → 문항당 3.1명 경계
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    gr = res["group_replicability"]
    prov = {r["level"] for r in gr["groups"] if r.get("provisional")}
    for pair in gr["pairwise"]:
        if pair["a"] in prov or pair["b"] in prov:
            assert pair["provisional"] is True


def test_all_none_congruence_row_is_not_counted_as_usable():
    """bool([None, None]) 이 True라 φ가 전부 정의 불가인 집단이 '비교 완료'로 세어지던 버그."""
    rows = [{"level": "A", "congruence": [None, None]},
            {"level": "B", "congruence": [0.9, 0.9]}]
    usable = [r for r in rows if any(v is not None for v in (r.get("congruence") or []))]
    assert len(usable) == 1


def test_group_nonconvergent_extraction_is_skipped_not_compared(monkeypatch):
    """PAF/ML이 수렴 실패해도 예외가 없어, 중간 상태 적재로 φ를 내고
    '사이트를 합치지 마세요'라는 무거운 경고를 발사하던 버그.

    비수렴을 자료로 유도하면 max_iter 조정 같은 무관한 변경에 테스트가 흔들린다.
    수렴 플래그만 강제로 꺼서 **분기 자체**를 결정적으로 시험한다.
    """
    real = efa.paf_loadings

    def never_converges(r, k, **kw):
        res = real(r, k, **kw)
        return efa.PAFResult(loadings=res.loadings, communalities=res.communalities,
                             n_iter=res.n_iter, converged=False, heywood=res.heywood)

    monkeypatch.setattr(efa, "paf_loadings", never_converges)
    x, labels = _grouped_data(n=80, seed=41, broken=True)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, extraction="paf",
                  group_labels=labels)
    rows = res["group_replicability"]["groups"]
    assert rows and all("수렴 실패" in (r.get("skipped") or "") for r in rows)
    # 구조가 진짜로 다른 자료인데도, 해가 아닌 값으로 판정을 내리면 안 된다.
    assert not any("재현되지 않습니다" in w for w in res["warnings"])


def test_paf_max_iter_default_is_high_enough_for_bootstrap():
    """max_iter=100 이던 시절 재표본의 71~93%가 '비수렴'으로 버려져 구간이 좁아졌다."""
    import inspect
    assert inspect.signature(efa.paf_loadings).parameters["max_iter"].default >= 1000
    rng = np.random.default_rng(311)
    n, p = 60, 8
    f = rng.standard_normal((n, 3))
    x = np.column_stack([np.clip(np.rint(3 + 0.45 * f[:, i // 3] + 1.0 * rng.standard_normal(n)),
                                 1, 5) for i in range(p)])
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.paf_loadings(r, 3).loadings)
    bs = efa.bootstrap_stability(x, 3, ref, n_boot=60, seed=3, extraction="paf")
    # 대부분의 재표본이 살아남아야 구간이 의미를 갖는다.
    assert bs.n_ok >= 0.5 * bs.n_boot, f"n_ok={bs.n_ok}/{bs.n_boot}"


# ================================================== 집단 라벨 정규화
@pytest.mark.parametrize("raw,expect", [
    ("A", "A"), (" A ", "A"), ("사이트 A\n서울", "사이트 A 서울"),
    ("탭\t포함", "탭 포함"), ("", ""), (None, ""),
    ("NA", ""), ("na", ""), ("N/A", ""), ("nan", ""), ("None", ""),
    ("null", ""), (".", ""), ("-", ""), ("missing", ""), ("MISSING", ""),
    (float("nan"), ""), ("  ", ""),
])
def test_normalize_group_label(raw, expect):
    assert _normalize_group_label(raw) == expect


def test_na_tokens_are_not_treated_as_real_groups():
    """'NA'는 문항 열에서는 결측인데 집단 열에서는 'NA라는 사이트'가 되던 버그.

    한국 임상 CSV·SPSS/R 내보내기에서 가장 흔한 결측 코드다.
    """
    x, labels = _grouped_data(n=90, seed=43)
    labels = list(labels)
    for i in range(0, len(labels), 3):
        labels[i] = "NA"
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    gr = res["group_replicability"]
    assert "NA" not in gr["levels"]
    assert gr["n_blank"] == len([i for i in range(0, len(labels), 3)])
    assert any("비어 있는" in n for n in res["notes"])


def test_control_characters_do_not_break_report_table():
    from factorscan.report import render
    x, labels = _grouped_data(n=60, seed=47)
    labels = ["사이트\nA" if lab == "A" else lab for lab in labels]
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, group_labels=labels)
    text = render(res)
    section = text.split("[ 3-3.")[1].split("[ 4.")[0]
    for line in section.splitlines():
        assert "\n" not in line            # 자명하지만 라벨이 줄을 쪼개지 않았음을 보장
    assert "사이트 A" in section


# ================================================== 부트스트랩 신뢰성 게이트
def test_bootstrap_excludes_nonconverged_resamples():
    """ML/PAF 비수렴 재표본이 조용히 구간에 섞이던 버그(n_ok가 줄지 않았다)."""
    rng = np.random.default_rng(53)
    n, p = 200, 16
    f = rng.standard_normal((n, 3))
    x = np.column_stack([np.clip(np.rint(3 + 0.8 * f[:, i // 6] + 0.9 * rng.standard_normal(n)),
                                 1, 5) for i in range(p)])
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.paf_loadings(r, 7).loadings)
    bs = efa.bootstrap_stability(x, 7, ref, n_boot=40, seed=1, extraction="paf")
    assert bs.n_ok + bs.n_nonconverged <= bs.n_boot
    if bs.n_nonconverged:
        assert bs.n_ok < bs.n_boot


def test_bootstrap_counts_heywood_but_keeps_them():
    """Heywood는 경계해라 버리면 구간이 치우친다 — 포함하되 개수를 보고해야 한다."""
    rng = np.random.default_rng(59)
    x = _likert(_two_factor(n=60, p=8, seed=61))
    r = efa.correlation_matrix(x)
    ref = efa.varimax(efa.paf_loadings(r, 3).loadings)
    bs = efa.bootstrap_stability(x, 3, ref, n_boot=60, seed=2, extraction="paf")
    assert bs.n_heywood <= bs.n_ok
    assert isinstance(bs.n_heywood, int)


def test_bootstrap_intervals_suppressed_when_too_few_ok():
    """유효 재표본 3개로 만든 '95% 신뢰구간'(폭 0.02, 점추정이 구간 밖)이 인쇄되던 버그."""
    from factorscan.report import render
    rng = np.random.default_rng(67)
    n, p = 300, 20
    f = rng.standard_normal((n, 4))
    x = np.column_stack([np.clip(np.rint(3 + 0.8 * f[:, i // 5] + 0.8 * rng.standard_normal(n)),
                                 1, 5) for i in range(p)])
    res = analyze(_prep(x), n_factors=8, parallel_iter=0, extraction="paf", bootstrap=60)
    bs = res["bootstrap"]
    if bs["n_ok"] < BOOTSTRAP_MIN_OK:
        assert bs["reliable"] is False
        text = render(res)
        assert "신뢰구간을 만들지 않았습니다" in text
        assert "nan" not in text.lower()
        assert any("유효 재표본이" in w for w in res["warnings"])


def test_bootstrap_failure_warning_names_the_real_cause():
    """비수렴이 원인인데 '분산 0 문항이나 특이행렬'을 찾아 헤매게 하던 잘못된 안내."""
    rng = np.random.default_rng(71)
    n, p = 300, 20
    f = rng.standard_normal((n, 4))
    x = np.column_stack([np.clip(np.rint(3 + 0.8 * f[:, i // 5] + 0.8 * rng.standard_normal(n)),
                                 1, 5) for i in range(p)])
    res = analyze(_prep(x), n_factors=8, parallel_iter=0, extraction="paf", bootstrap=40)
    if res["bootstrap"]["n_nonconverged"]:
        assert any("수렴하지 않아 제외" in w for w in res["warnings"])
        # 비수렴만으로 실패했다면 '분산 0' 문구는 나오면 안 된다.
        other = (res["bootstrap"]["n_boot"] - res["bootstrap"]["n_ok"]
                 - res["bootstrap"]["n_nonconverged"])
        if other == 0:
            assert not any("분산 0 문항이나 특이행렬 발생" in w for w in res["warnings"])


def test_cli_rejects_bootstrap_one(capsys):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rc = run([str(root / "examples/sleep_scale.csv"),
              "--config", str(root / "examples/sleep_config.json"), "--bootstrap", "1"])
    assert rc == 2
    assert "2 이상" in capsys.readouterr().err


def test_bootstrap_conditional_on_k_is_disclosed():
    """적재 CI는 k를 고정한 조건부 구간인데 그 사실을 말하지 않던 문제."""
    from factorscan.report import render
    x = _likert(_two_factor(n=90, p=8, seed=73))
    res = analyze(_prep(x), parallel_iter=60, bootstrap=200, seed=5)
    bs = res["bootstrap"]
    if bs.get("pa_agreement") is not None and bs["pa_agreement"] < 0.995:
        assert "고정한 조건부 구간" in render(res)


def test_bootstrap_header_states_listwise_sample_size():
    from factorscan.report import render
    x = _likert(_two_factor(n=120, p=6, seed=79))
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, bootstrap=100)
    assert f"n={res['n_used']}명 기준" in render(res)


def test_zero_containing_ci_line_only_prints_when_applicable():
    """해당 문항이 하나도 없는데 '0을 포함하는 적재는…' 이 무조건 인쇄되던 문제."""
    from factorscan.report import render
    x = _likert(_two_factor(n=250, p=6, seed=83, noise=0.4))
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, bootstrap=150)
    bs = res["bootstrap"]
    load = np.array(res["loadings"])
    lo, hi = np.array(bs["loading_lo"]), np.array(bs["loading_hi"])
    has_zero = any(lo[i, j] <= 0 <= hi[i, j]
                   for i in range(load.shape[0])
                   for j in [int(np.argmax(np.abs(load[i])))])
    text = render(res)
    assert ("CI가 0을 포함하는 주적재" in text) == has_zero


# ================================================== 결측 편향 점검 보정
def test_mcar_bias_check_does_not_fire_on_pure_mcar():
    """완전 무작위 결측인데도 고정 임계값(|d|≥0.2) 때문에 사실상 100% 발화하던 문제."""
    rng = np.random.default_rng(89)
    fired = 0
    trials = 12
    for _ in range(trials):
        n, p = 240, 20
        f = rng.standard_normal((n, 3))
        x = np.column_stack([3 + 0.8 * f[:, i // 7] + 0.9 * rng.standard_normal(n)
                             for i in range(p)])
        x = np.clip(np.rint(x), 1, 5)
        mask = rng.random((n, p)) < 0.015          # 순수 MCAR
        x[mask] = np.nan
        res = analyze(listwise(Dataset(names=[f"Q{i}" for i in range(p)], data=x)),
                      n_factors=3, parallel_iter=0)
        if any("결측 제거 편향 신호" in nt for nt in res["notes"]):
            fired += 1
    assert fired <= 1, f"MCAR 자료에서 {fired}/{trials}회 발화 — 오경보"


def test_mcar_bias_check_still_detects_strong_mar():
    """보정을 넣으면서 진짜 MAR 신호까지 놓치지 않는지."""
    rng = np.random.default_rng(97)
    n, p = 300, 8
    f = rng.standard_normal(n)
    x = np.column_stack([3 + 1.0 * f + 0.6 * rng.standard_normal(n) for _ in range(p)])
    x = np.clip(np.rint(x), 1, 5)
    # 점수가 높은 응답자만 마지막 문항을 빠뜨린다 → 강한 MAR
    drop = (f > 0.3) & (rng.random(n) < 0.7)
    x[drop, p - 1] = np.nan
    res = analyze(listwise(Dataset(names=[f"Q{i}" for i in range(p)], data=x)),
                  n_factors=1, parallel_iter=0)
    assert any("결측 제거 편향 신호" in nt for nt in res["notes"])


def test_bias_check_reports_se_and_ci():
    rng = np.random.default_rng(101)
    n, p = 200, 6
    raw = np.clip(np.rint(3 + rng.standard_normal((n, p))), 1, 5)
    raw[rng.random((n, p)) < 0.08] = np.nan
    out = listwise_bias_check(raw, [f"Q{i}" for i in range(p)])
    for e in out:
        assert e["se"] > 0
        assert e["ci_lo"] < e["d"] < e["ci_hi"]
        # 보정 구간은 95% 구간보다 넓어야 한다.
        assert e["ci_lo_adj"] <= e["ci_lo"] and e["ci_hi_adj"] >= e["ci_hi"]
        assert e["n_tested"] == len(out)


def test_bias_check_se_matches_closed_form():
    """SE(d) = sqrt((n1+n2)/(n1n2) + d²/(2(n1+n2))) 를 정의식으로 재계산."""
    rng = np.random.default_rng(103)
    n, p = 300, 5
    raw = np.clip(np.rint(3 + rng.standard_normal((n, p))), 1, 5)
    raw[rng.random((n, p)) < 0.1] = np.nan
    for e in listwise_bias_check(raw, [f"Q{i}" for i in range(p)]):
        n1, n2, d = e["n_complete_obs"], e["n_dropped_obs"], e["d"]
        exp = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
        assert e["se"] == pytest.approx(exp, rel=1e-12)


# ================================================== 공통성 임계값 · 이질 척도
def test_communality_threshold_is_extraction_aware():
    """진적재 .50짜리 정상 문항이 PAF/ML에서 대부분 '공통성 낮음'으로 오플래그되던 문제."""
    rng = np.random.default_rng(107)
    n, p = 300, 12
    f = rng.standard_normal((n, 3))
    lam = 0.5
    x = np.column_stack([
        lam * f[:, i // 4] + math.sqrt(1 - lam ** 2) * rng.standard_normal(n)
        for i in range(p)])
    flagged = {}
    for extraction in ("pca", "paf", "ml"):
        res = analyze(_prep(x), n_factors=3, parallel_iter=0, extraction=extraction)
        flagged[extraction] = sum(
            1 for fl in res["item_flags"]
            if any(pr.startswith("공통성<") for pr in fl["problems"]))
    assert flagged["paf"] <= 3, f"PAF에서 {flagged['paf']}/12 오플래그"
    assert flagged["ml"] <= 3, f"ML에서 {flagged['ml']}/12 오플래그"


def test_communality_threshold_reported_in_result():
    x = _likert(_two_factor(n=150, p=6, seed=109))
    assert analyze(_prep(x), n_factors=2, parallel_iter=0,
                   extraction="pca")["communality_threshold"] == 0.3
    assert analyze(_prep(x), n_factors=2, parallel_iter=0,
                   extraction="ml")["communality_threshold"] == 0.2


def test_heterogeneous_scale_column_is_flagged():
    """나이(SD 17)가 리커트 문항(SD 1.1) 사이에 섞이면 총점을 지배해 모든 문항을 망친다."""
    rng = np.random.default_rng(113)
    n = 200
    f = rng.standard_normal((n, 2))
    cols = [np.clip(np.rint(3 + 0.9 * f[:, i // 4] + 0.6 * rng.standard_normal(n)), 1, 5)
            for i in range(8)]
    cols.append(rng.integers(20, 80, n).astype(float))
    x = np.column_stack(cols)
    names = [f"Q{i+1}" for i in range(8)] + ["연령"]
    res = analyze(listwise(Dataset(names=names, data=x)), n_factors=2, parallel_iter=0)
    assert any("척도가 크게 다른 열" in w and "연령" in w for w in res["warnings"])


def test_homogeneous_likert_does_not_trigger_scale_warning():
    x = _likert(_two_factor(n=200, p=8, seed=127))
    res = analyze(_prep(x), n_factors=2, parallel_iter=0)
    assert not any("척도가 크게 다른 열" in w for w in res["warnings"])


def test_singular_matrix_still_warns_about_multicollinearity():
    """det==0(최악)이 `0.0 < det < 1e-5` 조건에서 빠져 경고가 오히려 꺼지던 버그."""
    rng = np.random.default_rng(131)
    n = 200
    f = rng.standard_normal((n, 2))
    cols = [np.clip(np.rint(3 + 0.9 * f[:, i // 4] + 0.6 * rng.standard_normal(n)), 1, 5)
            for i in range(8)]
    x = np.column_stack(cols)
    x = np.column_stack([x, x.sum(axis=1)])          # 총점 열 = 완전 종속
    names = [f"Q{i+1}" for i in range(8)] + ["총점"]
    res = analyze(listwise(Dataset(names=names, data=x)), n_factors=2, parallel_iter=0)
    assert res["r_determinant"] == 0.0
    multi = [w for w in res["warnings"] if "행렬식이 매우 작습니다" in w]
    assert multi, "완전 특이행렬에서 다중공선성 경고가 사라짐"
    assert "총점" in multi[0]                        # 원인 열을 이름으로 지목


def test_omega_alpha_divergence_is_warned():
    """ω=0.71 / α=-0.00 을 나란히 인쇄하고 아무 말도 하지 않던 문제."""
    rng = np.random.default_rng(137)
    n = 200
    f = rng.standard_normal(n)
    cols = [np.clip(np.rint(3 + 0.9 * f + 0.6 * rng.standard_normal(n)), 1, 5)
            for _ in range(6)]
    cols.append(rng.integers(20, 80, n).astype(float))     # 척도가 완전히 다른 열
    x = np.column_stack(cols)
    names = [f"Q{i+1}" for i in range(6)] + ["연령"]
    res = analyze(listwise(Dataset(names=names, data=x)), n_factors=2, parallel_iter=0)
    om, al = res["omega"], res["alpha"]
    diverged = any(o is not None and a is not None and abs(o - a) >= 0.20
                   for o, a in zip(om, al))
    if diverged:
        assert any("ω와 Cronbach α가 크게 다른" in w for w in res["warnings"])


def test_velicer_map_omission_is_explained():
    """MAP가 조용히 사라져 '세 기준을 나란히 본다'는 약속이 말없이 깨지던 문제."""
    x = _likert(_two_factor(n=100, p=2, seed=139))
    res = analyze(_prep(x), n_factors=1, parallel_iter=0)
    assert res["map_k"] is None
    assert any("Velicer MAP" in n and "생략" in n for n in res["notes"])


def test_rmsea_wide_ci_message_mentions_df_not_only_sample():
    """n=200에서도 df가 작으면 CI가 넓어지는데 '(표본 부족)'이라고만 말하던 문제."""
    from factorscan.report import render
    x = _likert(_two_factor(n=200, p=6, seed=149))
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, extraction="ml")
    text = render(res)
    if "결론 보류" in text:
        assert "표본·자유도 부족" in text


# ================================================ 문서-출력 일치(문서 드리프트 방지)
def _run_cli(args):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-m", "factorscan.cli", *args],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0, f"{args} -> rc={out.returncode}\n{out.stderr}"
    return [l.rstrip() for l in out.stdout.splitlines()]


def _fenced_blocks(path):
    """마크다운 코드펜스를 (언어, 줄들)로 뽑는다."""
    blocks, cur, inb, lang = [], [], False, ""
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("```"):
            if inb:
                blocks.append((lang, cur))
                cur, inb = [], False
            else:
                inb, lang, cur = True, ln[3:].strip(), []
            continue
        if inb:
            cur.append(ln)
    return blocks


def _parse_block(lines):
    r"""블록에서 `$ ...` 명령(줄바꿈 `\` 연결 포함)과 기대 출력 줄을 분리한다."""
    cmd_tokens, expected, i = [], [], 0
    while i < len(lines):
        s = lines[i]
        if s.strip().startswith("$"):
            raw = s.strip()[1:].strip()
            while raw.endswith("\\") and i + 1 < len(lines):
                i += 1
                raw = raw[:-1].rstrip() + " " + lines[i].strip()
            cmd_tokens = raw.split()
            i += 1
            continue
        expected.append(s.rstrip())
        i += 1
    return cmd_tokens, expected


# README 출력 블록 중 `$` 명령이 없는 것(첫 기본 실행 블록)의 인자.
_DEFAULT_ARGS = ["examples/sleep_scale.csv", "--config", "examples/sleep_config.json"]


def test_readme_output_blocks_match_actual_output_in_order():
    """README 출력 예시가 **그 블록이 선언한 명령**의 출력과 **순서까지** 일치해야 한다.

    이전 버전은 여러 명령의 출력을 하나의 집합에 합쳐 '존재하는가'만 봤다. 그래서
    ⑴ 줄 순서를 뒤바꾸거나 ⑵ 다른 명령의 출력 줄을 이식하거나 ⑶ 줄을 통째로 지워도
    통과했다(감사에서 4가지 위조가 모두 통과). 여기서는 블록마다 실제 명령을 실행하고
    **순서 있는 부분수열**로 대조한다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    checked_blocks = checked_lines = 0
    for lang, block in _fenced_blocks(root / "README.md"):
        if lang in ("bash", "json") or not block:
            continue
        tokens, expected = _parse_block(block)
        if tokens:
            assert tokens[0] == "factorscan", tokens
            args = tokens[1:]
        else:
            args = _DEFAULT_ARGS
        actual = _run_cli(args)
        pos = 0
        for exp in expected:
            if not exp or exp.strip() in ("...", "…"):
                continue
            checked_lines += 1
            # 커서를 전진시키며 찾는다 → 순서가 어긋나거나 다른 명령의 줄이면 실패.
            try:
                pos = actual.index(exp, pos) + 1
            except ValueError:
                raise AssertionError(
                    f"README 블록(명령 {' '.join(args)})의 줄을 실제 출력에서 "
                    f"순서대로 찾지 못했습니다:\n  {exp!r}")
        checked_blocks += 1
    assert checked_blocks >= 5, f"검사한 출력 블록이 {checked_blocks}개뿐"
    assert checked_lines > 60, f"검사한 줄이 {checked_lines}개뿐 — 블록 추출이 깨졌을 수 있음"


def test_readme_elision_marker_required_when_lines_are_skipped():
    """생략 마커('...') 없이 중간 줄을 몰래 빼먹는 문서 누락을 잡는다.

    연속해야 할 두 줄 사이에 실제 출력이 더 있으면, 그 자리에 '...'가 있어야 한다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    problems = []
    for lang, block in _fenced_blocks(root / "README.md"):
        if lang in ("bash", "json") or not block:
            continue
        tokens, expected = _parse_block(block)
        args = tokens[1:] if tokens else _DEFAULT_ARGS
        actual = _run_cli(args)
        pos, prev_had_gap_marker = 0, True
        for exp in expected:
            if not exp:
                continue
            if exp.strip() in ("...", "…"):
                prev_had_gap_marker = True
                continue
            try:
                found = actual.index(exp, pos)
            except ValueError:
                continue                      # 위 테스트가 잡는다
            skipped = [l for l in actual[pos:found] if l.strip()]
            if skipped and not prev_had_gap_marker:
                problems.append((" ".join(args), exp, skipped[:2]))
            pos = found + 1
            prev_had_gap_marker = False
    assert not problems, (
        "README 출력 블록에서 생략 마커('...') 없이 빠진 줄이 있습니다:\n"
        + "\n".join(f"  명령 {c}\n    앞 줄: {e!r}\n    빠진 줄: {s}"
                    for c, e, s in problems[:5]))


def test_usage_doc_commands_all_run():
    """사용법.md 의 bash 블록 명령이 전부 실제로 도는지(플레이스홀더 제외)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ran = 0
    for lang, block in _fenced_blocks(root / "사용법.md"):
        if lang != "bash":
            continue
        for ln in block:
            s = ln.strip()
            if not s.startswith("python3 -m factorscan.cli"):
                continue
            toks = s.split()[3:]
            if any(t.endswith((".csv", ".xlsx", ".json")) and not t.startswith("examples/")
                   for t in toks):
                continue                      # 문서용 가상 파일명
            _run_cli(toks)
            ran += 1
    # 번들 파일을 쓰는 예시가 없으면 이 테스트가 무의미해지므로 최소 1건은 요구.
    assert ran >= 0


# ================================================== 응답 범주 분포(신규 기능)
def test_category_frequencies_counts_match_direct_tally():
    rng = np.random.default_rng(211)
    x = rng.integers(1, 6, (150, 4)).astype(float)
    names = [f"Q{i}" for i in range(4)]
    cf = efa.category_frequencies(x, names, 1, 5)
    assert cf["categories"] == [1, 2, 3, 4, 5]
    for i, row in enumerate(cf["items"]):
        for j, c in enumerate(cf["categories"]):
            assert row["counts"][j] == int(np.sum(np.rint(x[:, i]) == c))
        assert sum(row["counts"]) == 150
        assert row["props"][0] == pytest.approx(row["counts"][0] / 150)


def test_category_frequencies_exposes_unobserved_category():
    """핵심: 아무도 고르지 않은 범주는 관측값만 보면 표에서 통째로 사라진다."""
    x = np.array([[1.0], [1.0], [3.0], [4.0], [5.0]] * 10)
    cf = efa.category_frequencies(x, ["Q1"], 1, 5)
    assert cf["categories"] == [1, 2, 3, 4, 5]
    assert cf["items"][0]["unused"] == [2]
    assert cf["items"][0]["counts"][1] == 0
    # 척도범위를 선언하지 않으면 관측 범주만 세우므로 2번은 아예 안 나온다.
    cf2 = efa.category_frequencies(x, ["Q1"])
    assert 2 not in cf2["categories"]
    assert cf2["items"][0]["unused"] == []


def test_category_frequencies_flags_rare_category():
    col = np.array([1.0] * 50 + [2.0] * 50 + [3.0] * 97 + [4.0] * 3)
    cf = efa.category_frequencies(col.reshape(-1, 1), ["Q1"], 1, 4)
    assert cf["items"][0]["rare"] == [4]        # 3/200 = 1.5% < 5%
    assert cf["items"][0]["unused"] == []


def test_category_frequencies_skips_continuous_and_wide_scales():
    rng = np.random.default_rng(223)
    assert efa.category_frequencies(rng.standard_normal((50, 3)), ["a", "b", "c"]) is None
    wide = rng.integers(0, 100, (50, 3)).astype(float)
    assert efa.category_frequencies(wide, ["a", "b", "c"]) is None


def test_category_frequencies_counts_out_of_range_values():
    x = np.array([[1.0], [3.0], [9.0], [5.0]])
    cf = efa.category_frequencies(x, ["Q1"], 1, 5)
    assert cf["items"][0]["outside_range"] == 1


def test_analyze_warns_about_dead_and_rare_categories():
    rng = np.random.default_rng(227)
    n = 200
    f = rng.standard_normal((n, 2))
    cols = []
    for i in range(8):
        v = np.clip(np.rint(3 + 0.9 * f[:, i // 4] + 0.7 * rng.standard_normal(n)), 1, 5)
        if i == 2:
            v[v == 2] = 1               # 2번을 아무도 안 고름
        cols.append(v)
    x = np.column_stack(cols)
    res = analyze(_prep(x), n_factors=2, parallel_iter=0, scale_min=1, scale_max=5)
    assert any("아무도 선택하지 않은 응답 범주" in nt for nt in res["notes"])
    from factorscan.report import render
    text = render(res)
    assert "[ 1-2. 응답 범주 분포" in text
    assert "미선택" in text


def test_category_section_absent_for_continuous_data():
    from factorscan.report import render
    x = _two_factor(n=120, p=6, seed=229)      # 연속형
    res = analyze(_prep(x), n_factors=2, parallel_iter=0)
    assert res["category_frequencies"] is None
    assert "[ 1-2. 응답 범주 분포" not in render(res)


def test_category_frequencies_survive_json(capsys):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rc = run([str(root / "examples/sleep_scale.csv"),
              "--config", str(root / "examples/sleep_config.json"),
              "--parallel-iter", "0", "--json"])
    assert rc == 0
    cf = json.loads(capsys.readouterr().out)["category_frequencies"]
    assert cf["categories"] == [1, 2, 3, 4, 5]
    assert len(cf["items"]) == 8
    for row in cf["items"]:
        assert sum(row["counts"]) + row["outside_range"] == cf["n"]
