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
    hypergeom_lower_tail,
    mann_kendall,
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
