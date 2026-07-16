"""짝지은 코호트 통계 — Wilcoxon 부호순위, paired_summary, --paired 모드."""

import math
import os
import random

import pytest

from hrvkit import analyze_rr, cli
from hrvkit.stats import (hodges_lehmann, normal_cdf, paired_summary,
                          walsh_averages, wilcoxon_signed_rank)
from hrvkit.report import paired_group, render_paired_group

try:
    import scipy.stats as _sps
    HAVE_SCIPY = True
except Exception:  # pragma: no cover
    HAVE_SCIPY = False


def test_normal_cdf_known_values():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_wilcoxon_all_positive_exact_pvalue():
    # 모두 같은 방향(양수), 동점 없음, n=8 ≤ 25 → auto가 정확 분포 선택.
    # 정확 p = 2·P(W+ = 36) = 2/2^8 = 0.0078125 (손으로 검산 가능).
    w = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7, 8])
    assert w["n_pairs"] == 8
    assert w["method"] == "exact"
    assert w["p_value"] == pytest.approx(2.0 / 2 ** 8, rel=1e-12)


def test_wilcoxon_all_positive_approx_pvalue():
    # 같은 데이터를 정규 근사로 강제하면 정확 p보다 1.8배 보수적.
    w = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7, 8], method="approx")
    assert w["method"] == "approx"
    assert w["p_value"] == pytest.approx(0.0143, abs=0.002)


def test_wilcoxon_zeros_excluded():
    w = wilcoxon_signed_rank([0, 0, 1, 2, 3])
    assert w["n_pairs"] == 3


def test_wilcoxon_all_zero_p_one():
    w = wilcoxon_signed_rank([0, 0, 0])
    assert w["p_value"] == 1.0
    assert w["z"] == 0.0


def test_wilcoxon_symmetric_sign():
    # 부호를 뒤집어도 양측 p는 동일
    d = [3.0, -1.0, 2.0, 5.0, -2.0, 4.0, 1.5, -0.5, 6.0, 2.5]
    a = wilcoxon_signed_rank(d)["p_value"]
    b = wilcoxon_signed_rank([-x for x in d])["p_value"]
    assert a == pytest.approx(b, rel=1e-12)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_wilcoxon_matches_scipy_normal_approx():
    rng = random.Random(3)
    for _ in range(6):
        d = [round(rng.gauss(1.5, 4), 2) for _ in range(25)]
        mine = wilcoxon_signed_rank(d, method="approx")
        ref = _sps.wilcoxon(d, correction=True, method="approx",
                            zero_method="wilcox")
        assert mine["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-6)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_wilcoxon_exact_matches_scipy_exact():
    # 동점 없는 표본에서 auto는 정확 분포를 쓰고, scipy의 정확 검정과 일치해야 함.
    rng = random.Random(3)
    for _ in range(6):
        # |차이| 동점이 없어야 정확 분포가 정의됨 → 동점 없는 표본만 뽑는다.
        d = []
        seen = set()
        while len(d) < 20:
            x = round(rng.gauss(1.5, 4), 3)
            if x != 0.0 and abs(x) not in seen:
                seen.add(abs(x))
                d.append(x)
        mine = wilcoxon_signed_rank(d)
        assert mine["method"] == "exact"
        ref = _sps.wilcoxon(d, method="exact", zero_method="wilcox")
        assert mine["p_value"] == pytest.approx(float(ref.pvalue), rel=1e-12)


@pytest.mark.skipif(not HAVE_SCIPY, reason="scipy 없음")
def test_wilcoxon_matches_scipy_with_ties():
    d = [0, 0, 1, 1, 1, -1, -2, 3, 3, -3, 5, 2, 2, -2]
    mine = wilcoxon_signed_rank(d)
    ref = _sps.wilcoxon(d, correction=True, method="approx",
                        zero_method="wilcox")
    assert mine["p_value"] == pytest.approx(float(ref.pvalue), abs=1e-6)


def test_paired_summary_basic():
    base = [10, 12, 11, 13, 9]
    interv = [14, 15, 13, 18, 12]
    s = paired_summary(base, interv)
    assert s["n"] == 5
    assert s["mean_diff"] == pytest.approx(3.4)
    assert s["n_increased"] == 5
    assert s["cohens_dz"] == pytest.approx(s["mean_diff"] / s["sd_diff"])


def test_paired_summary_ignores_nonfinite():
    s = paired_summary([1, float("nan"), 3], [2, 5, 4])
    assert s["n"] == 2   # NaN 짝 제외


def test_paired_summary_empty():
    assert paired_summary([], [])["n"] == 0


def _mk_pair(seed):
    rng = random.Random(seed)
    base = [800 + rng.gauss(0, 15) for _ in range(200)]
    interv = [880 + rng.gauss(0, 35) for _ in range(200)]
    return (analyze_rr(base, source=f"b{seed}.csv"),
            analyze_rr(interv, source=f"i{seed}.csv"))


def test_paired_group_and_render():
    pairs = [_mk_pair(s) for s in range(6)]
    g = paired_group(pairs)
    assert g["_meta"]["n_subjects"] == 6
    assert g["rmssd"]["n"] == 6
    assert g["rmssd"]["mean_diff"] > 0     # 개입에서 RMSSD 증가
    out = render_paired_group(pairs)
    assert "짝지은 코호트" in out
    assert "Wilcoxon" in out
    assert "RMSSD" in out


def test_cli_paired_mode(capsys, tmp_path):
    rng = random.Random(11)
    rows = ["baseline,intervention,subject"]
    for s in range(6):
        base = [800 + rng.gauss(0, 15) for _ in range(200)]
        interv = [880 + rng.gauss(0, 35) for _ in range(200)]
        bp = tmp_path / f"b{s}.csv"
        ip = tmp_path / f"i{s}.csv"
        bp.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in base) + "\n",
                      encoding="utf-8")
        ip.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in interv) + "\n",
                      encoding="utf-8")
        rows.append(f"b{s}.csv,i{s}.csv,S{s}")
    man = tmp_path / "manifest.csv"
    man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = cli.main(["--paired", str(man)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "코호트" in out and "RMSSD" in out


def test_cli_paired_json(capsys, tmp_path):
    rng = random.Random(5)
    rows = ["baseline,intervention"]
    for s in range(5):
        for tag, mean in (("b", 800), ("i", 880)):
            p = tmp_path / f"{tag}{s}.csv"
            vals = [mean + rng.gauss(0, 20) for _ in range(200)]
            p.write_text("rr_ms\n" + "\n".join(f"{x:.1f}" for x in vals) + "\n",
                         encoding="utf-8")
        rows.append(f"b{s}.csv,i{s}.csv")
    man = tmp_path / "m.csv"
    man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    import json
    rc = cli.main(["--paired", str(man), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["mode"] == "paired"
    assert data["_meta"]["n_subjects"] == 5


def test_cli_no_input_errors(capsys):
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "CSV" in err or "paired" in err


def test_cli_paired_bad_manifest(capsys, tmp_path):
    man = tmp_path / "bad.csv"
    man.write_text("baseline,intervention\n", encoding="utf-8")  # 데이터 없음
    rc = cli.main(["--paired", str(man)])
    assert rc == 2


# --------------------------------------------------------------------------- #
# Hodges–Lehmann CI ↔ 정확검정 쌍대성(duality) — 모듈이 내세우는 가장 강한 주장
# --------------------------------------------------------------------------- #
def test_hl_ci_is_dual_to_exact_test():
    """"CI가 μ0를 배제" ⇔ "정확검정이 H0: pseudomedian=μ0 를 α에서 기각".

    격자 탐색으로 확인합니다. 이 성질이 깨지면 CI와 p값이 서로 다른 결론을 내게 되어
    (예: p<0.05 인데 CI가 0을 포함) 리포트가 자기모순이 됩니다.
    """
    from hrvkit.stats import wilcoxon_ci
    rng = random.Random(20)
    alpha = 0.05
    for trial in range(25):
        n = rng.randint(6, 14)
        # 동점 없는 표본(정확 분포가 정의되는 조건)
        d, seen = [], set()
        while len(d) < n:
            x = round(rng.gauss(1.0, 3.0), 4)
            if x != 0.0 and abs(x) not in seen:
                seen.add(abs(x))
                d.append(x)
        ci = wilcoxon_ci(d, alpha=alpha)
        assert ci["ci_method"] == "exact"
        lo, hi = ci["ci_low"], ci["ci_high"]
        # Walsh 평균 사이사이를 포함하는 촘촘한 격자에서 두 판정을 대조.
        walsh = sorted(walsh_averages(d))
        grid = set()
        for w in walsh:
            grid.update((w - 1e-6, w + 1e-6))
        grid.update((lo - 0.5, hi + 0.5, 0.0))
        for mu0 in sorted(grid):
            shifted = [x - mu0 for x in d]
            if any(s == 0.0 for s in shifted):
                continue          # 영차이는 Wilcoxon이 제외 → 쌍대성 가정 밖
            rejects = wilcoxon_signed_rank(shifted)["p_value"] < alpha
            excluded = not (lo <= mu0 <= hi)
            assert rejects == excluded, (
                f"trial={trial} n={n} mu0={mu0} p="
                f"{wilcoxon_signed_rank(shifted)['p_value']} CI=[{lo},{hi}]")


def test_hl_ci_alpha_widens_interval():
    from hrvkit.stats import wilcoxon_ci
    d = [1.2, -0.4, 2.3, 3.1, 0.7, -1.5, 2.8, 0.9, 1.1, -0.2]
    narrow = wilcoxon_ci(d, alpha=0.20)
    wide = wilcoxon_ci(d, alpha=0.01)
    assert wide["ci_low"] <= narrow["ci_low"]
    assert wide["ci_high"] >= narrow["ci_high"]


def test_hodges_lehmann_matches_definition():
    import statistics as _st
    d = [3.0, -1.0, 2.0, 5.0, -2.0]
    walsh = [(d[i] + d[j]) / 2.0 for i in range(len(d)) for j in range(i, len(d))]
    assert hodges_lehmann(d) == pytest.approx(_st.median(walsh))
    assert len(walsh_averages(d)) == 5 * 6 // 2


def test_paired_summary_reports_hl_and_ci():
    base = [10, 12, 11, 13, 9, 14, 10, 12]
    interv = [14, 15, 13, 18, 12, 19, 13, 16]
    s = paired_summary(base, interv)
    assert s["hl_shift"] > 0
    assert s["ci_low"] <= s["hl_shift"] <= s["ci_high"]
    assert s["ci_low"] > 0                     # 효과가 뚜렷 → CI가 0을 배제
    assert s["wilcoxon_p"] < 0.05              # 쌍대성과 일치
    assert s["ci_alpha"] == 0.05


@pytest.mark.parametrize("n", [6, 7, 8, 10, 12, 15, 20, 25])
def test_hl_ci_coverage_meets_nominal_level(n):
    """정확 CI의 **해석적 피복률**이 명목 수준 이상이어야 한다.

    피복률 = 1 - 2·P(W+ ≤ k). 과거엔 k가 1 커서 n=4~20 전 구간에서 명목 미달이었다
    (n=8: 0.9453 < 0.95). Monte Carlo 없이 정확 영분포로 직접 검산한다.
    """
    from hrvkit.stats import signed_rank_null_counts, wilcoxon_ci
    alpha = 0.05
    d = [i + 1.7 for i in range(n)]          # 동점 없는 표본
    ci = wilcoxon_ci(d, alpha=alpha)
    assert ci["ci_method"] == "exact"
    counts = signed_rank_null_counts(n)
    k = int(ci["ci_k"])
    coverage = 1.0 - 2.0 * sum(counts[:k + 1]) / sum(counts)
    assert coverage >= 1.0 - alpha
    # 그리고 한 칸 더 잘라내면(k+1) 명목 미달이어야 한다 = k가 최대(가장 짧은 구간).
    tighter = 1.0 - 2.0 * sum(counts[:k + 2]) / sum(counts)
    assert tighter < 1.0 - alpha


def test_exact_and_approx_branches_agree_at_boundary():
    """n=25(정확)↔n=26(근사) 경계에서 피복률이 튀지 않아야 한다.

    과거엔 정확 분기만 k가 1 커서 0.9484 → 0.9507 로 불연속이었다.
    """
    from hrvkit.stats import signed_rank_null_counts, wilcoxon_ci
    covs = []
    for n in (24, 25):
        d = [i + 1.7 for i in range(n)]
        k = int(wilcoxon_ci(d, 0.05)["ci_k"])
        counts = signed_rank_null_counts(n)
        covs.append(1.0 - 2.0 * sum(counts[:k + 1]) / sum(counts))
    for c in covs:
        assert 0.95 <= c < 0.96          # 명목 이상이되 과도하게 보수적이지 않음


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_no_alpha_level_ci_exists_for_tiny_n(n):
    """n이 너무 작으면 어떤 유한 구간도 95%를 담보할 수 없다 → (-∞, ∞) + 명시.

    n=5 의 정확검정 최소 p = 2/2^5 = 0.0625 > 0.05 이므로 절대 기각할 수 없다.
    전체 범위를 "95% 구간"이라 부르면 거짓 — 참인 답은 (-∞, ∞).
    """
    from hrvkit.stats import wilcoxon_ci
    d = [i + 1.7 for i in range(n)]
    ci = wilcoxon_ci(d, alpha=0.05)
    assert ci["ci_method"] == "insufficient-n"
    assert ci["ci_low"] == float("-inf")
    assert ci["ci_high"] == float("inf")
    # 쌍대성: 검정도 절대 기각하지 못한다.
    assert wilcoxon_signed_rank(d)["p_value"] > 0.05


def test_hl_shift_always_inside_its_own_ci_with_zero_diffs():
    """영차이가 섞여도 점추정이 자기 CI 밖으로 나가면 안 된다.

    과거: 전역 hodges_lehmann(모든 차이) 2.5 vs CI(0 제외) [5,6] → 모순.
    """
    s = paired_summary([0, 0, 0, 0, 0, 0, 0, 0],
                       [0, 0, 0, 5, 6, 7, 8, 9])
    assert s["ci_low"] <= s["hl_shift"] <= s["ci_high"]


def test_global_hodges_lehmann_keeps_textbook_definition():
    # 전역 함수는 교과서대로 '모든' 차이를 쓴다(0 포함).
    assert hodges_lehmann([0.0, 0.0, 0.0, 5.0, 6.0]) == pytest.approx(2.5)
