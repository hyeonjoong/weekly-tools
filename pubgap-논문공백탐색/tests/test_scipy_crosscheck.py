"""선택적(scipy 있을 때만) 교차검증 — 우리 순수-표준라이브러리 통계가
scipy 의 구현과 수치적으로 일치함을 확인한다.

scipy 는 이 도구의 의존성이 **아니다**(런타임/기본 테스트는 scipy 없이도 통과).
이 파일은 scipy 가 설치돼 있을 때만 실행되어, README 의 '대조' 주장을 재현한다.
"""

import random

import pytest

scipy_stats = pytest.importorskip("scipy.stats")

from pubgap.analyze import (  # noqa: E402
    benjamini_hochberg,
    clopper_pearson,
    fisher_exact_two_sided,
    hypergeom_lower_tail,
    mann_kendall,
    poisson_count_ci,
    reg_inc_beta,
)


def test_bh_matches_scipy_false_discovery_control():
    fdc = getattr(scipy_stats, "false_discovery_control", None)
    if fdc is None:  # 아주 오래된 scipy
        pytest.skip("scipy.stats.false_discovery_control 없음")
    for seed in range(150):
        random.seed(seed)
        ps = [random.random() for _ in range(random.randint(1, 30))]
        mine = benjamini_hochberg(ps)
        ref = fdc(ps, method="bh")
        assert max(abs(a - b) for a, b in zip(mine, ref)) < 1e-9


def test_mann_kendall_tau_matches_scipy_kendalltau():
    for seed in range(150):
        random.seed(1000 + seed)
        n = random.randint(3, 25)
        vals = [random.randint(0, 6) for _ in range(n)]
        r = mann_kendall(vals)
        tau_ref, _ = scipy_stats.kendalltau(list(range(n)), vals)
        if tau_ref == tau_ref:  # not NaN
            assert abs(r.tau - tau_ref) < 1e-9


def test_hypergeom_matches_scipy_cdf():
    for seed in range(400):
        random.seed(5000 + seed)
        N = random.randint(2, 6000)
        K = random.randint(0, N)
        n = random.randint(0, N)
        k = random.randint(0, min(K, n))
        mine = hypergeom_lower_tail(N, K, n, k)
        ref = scipy_stats.hypergeom.cdf(k, N, K, n)
        assert abs(mine - ref) < 1e-9


def test_fisher_exact_matches_scipy():
    for seed in range(300):
        random.seed(9000 + seed)
        a, b, c, d = (random.randint(0, 40) for _ in range(4))
        mine = fisher_exact_two_sided(a, b, c, d)
        _odds, ref = scipy_stats.fisher_exact([[a, b], [c, d]])
        assert abs(mine - ref) < 1e-9, (a, b, c, d, mine, ref)


def test_clopper_pearson_matches_scipy_beta_quantiles():
    """CP 구간 = Beta 분위수(정의 그대로) — scipy.stats.beta 로 대조."""
    beta = scipy_stats.beta
    for seed in range(300):
        random.seed(20000 + seed)
        n = random.randint(1, 500)
        k = random.randint(0, n)
        lo, hi = clopper_pearson(k, n)
        ref_lo = 0.0 if k == 0 else beta.ppf(0.025, k, n - k + 1)
        ref_hi = 1.0 if k == n else beta.ppf(0.975, k + 1, n - k)
        assert abs(lo - ref_lo) < 1e-9, (k, n, lo, ref_lo)
        assert abs(hi - ref_hi) < 1e-9, (k, n, hi, ref_hi)


def test_poisson_exact_ci_matches_scipy_chi2_form():
    """Garwood 구간 = χ² 분위수 형태 — scipy.stats.chi2 로 대조."""
    chi2 = scipy_stats.chi2
    for k in [0, 1, 2, 3, 7, 15, 40, 137, 1009]:
        lo, hi = poisson_count_ci(k)
        ref_lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2.0
        ref_hi = chi2.ppf(0.975, 2 * k + 2) / 2.0
        assert abs(lo - ref_lo) < 1e-7 * max(1.0, ref_lo), (k, lo, ref_lo)
        assert abs(hi - ref_hi) < 1e-7 * max(1.0, ref_hi), (k, hi, ref_hi)


def test_reg_inc_beta_matches_scipy_betainc():
    special = pytest.importorskip("scipy.special")
    for seed in range(300):
        random.seed(30000 + seed)
        a = random.uniform(0.2, 200)
        b = random.uniform(0.2, 200)
        x = random.random()
        assert abs(reg_inc_beta(a, b, x) - special.betainc(a, b, x)) < 1e-10
