"""`tests/_scipy_expected.py` 를 만든 스크립트 (개발용, 패키지에 포함되지 않음).

테스트 스위트 자체는 **완전 오프라인 · stdlib 전용**이다. scipy 는 여기서
한 번만 쓰여 기대값을 뽑고, 그 결과가 `tests/_scipy_expected.py` 에 float
리터럴로 박힌다. 그래야 "자체 구현 p 값이 scipy 와 ≤1e-9 로 일치한다"는
주장이 scipy 설치 여부와 무관하게 검증된다.

    python3 dev/scipy_기대값생성.py > tests/_scipy_expected.py

시드가 고정돼 있어 다시 돌려도 **같은 파일**이 나온다. numpy 가 스칼라를
`np.float64(...)` 로 repr 하므로 출력 직전에 벗겨 낸다.
"""

import re
import sys

import numpy as np
from scipy import special, stats

RNG_SEED = 20260821


def emit(line=""):
    sys.stdout.write(re.sub(r"np\.float64\(([^)]*)\)", r"\1", str(line)) + "\n")


def block(name, comment, rows):
    emit()
    emit("# " + comment)
    emit("%s = [" % name)
    for row in rows:
        emit("    (" + ", ".join(
            ("%.17g" % v) if isinstance(v, float) else repr(v) for v in row) + "),")
    emit("]")


def main():
    rng = np.random.default_rng(RNG_SEED)
    emit('"""scipy 1.17.1 로 뽑아 **하드코딩**한 기대값. 테스트는 완전 오프라인이다.')
    emit()
    emit("생성 방법은 `dev/scipy_기대값생성.py` 에 남겨 두었다. 이 파일을 손으로")
    emit("고치지 말 것 — 자체 구현이 scipy 와 어긋났다는 증거가 사라진다.")
    emit('"""')

    rows = []
    for _ in range(14):
        a = np.round(rng.normal(18, 4, int(rng.integers(3, 26))), 3)
        b = np.round(rng.normal(15, 6, int(rng.integers(3, 26))), 3)
        r = stats.ttest_ind(a, b, equal_var=False)
        rows.append((list(a), list(b), float(r.statistic), float(r.pvalue)))
    block("WELCH", "(a, b, t, p) — scipy.stats.ttest_ind(a, b, equal_var=False)", rows)

    rows = []
    for _ in range(10):
        a = np.round(rng.normal(18, 4, int(rng.integers(3, 26))), 3)
        b = np.round(rng.normal(15, 4, int(rng.integers(3, 26))), 3)
        r = stats.ttest_ind(a, b, equal_var=True)
        rows.append((list(a), list(b), float(r.statistic), float(r.pvalue)))
    block("STUDENT", "(a, b, t, p) — scipy.stats.ttest_ind(a, b, equal_var=True)", rows)

    rows = []
    for _ in range(12):
        n = int(rng.integers(3, 30))
        pre = np.round(rng.normal(20, 4, n), 3)
        post = np.round(rng.normal(16, 4, n), 3)
        r = stats.ttest_rel(post, pre)
        rows.append((list(pre), list(post), float(r.statistic), float(r.pvalue)))
    block("PAIRED", "(pre, post, t, p) — scipy.stats.ttest_rel(post, pre)", rows)

    rows = []
    for _ in range(12):
        a = np.round(rng.normal(0, 1, int(rng.integers(2, 13))), 4)
        b = np.round(rng.normal(0.8, 1, int(rng.integers(2, 13))), 4)
        r = stats.mannwhitneyu(a, b, alternative="two-sided", method="exact")
        rows.append((list(a), list(b), float(r.statistic), float(r.pvalue)))
    block("MWU_EXACT",
          "(a, b, U1, p) — mannwhitneyu(a, b, alternative='two-sided', method='exact')",
          rows)

    rows = []
    for _ in range(12):
        a = list(map(float, rng.integers(0, 7, int(rng.integers(8, 40)))))
        b = list(map(float, rng.integers(0, 7, int(rng.integers(8, 40)))))
        r = stats.mannwhitneyu(a, b, alternative="two-sided",
                               method="asymptotic", use_continuity=True)
        rows.append((a, b, float(r.statistic), float(r.pvalue)))
    block("MWU_ASYMPTOTIC",
          "(a, b, U1, p) — mannwhitneyu(..., method='asymptotic', use_continuity=True)",
          rows)

    rows = []
    for _ in range(12):
        n = int(rng.integers(4, 22))
        pre = np.round(rng.normal(20, 4, n), 4)
        post = np.round(rng.normal(16, 4, n), 4)
        r = stats.wilcoxon(np.array(post) - np.array(pre), method="exact")
        rows.append((list(pre), list(post), float(r.statistic), float(r.pvalue)))
    block("WILCOXON_EXACT",
          "(pre, post, stat, p) — wilcoxon(post - pre, method='exact')", rows)

    rows = []
    while len(rows) < 10:
        n = int(rng.integers(12, 40))
        pre = list(map(float, rng.integers(10, 20, n)))
        post = list(map(float, rng.integers(10, 20, n)))
        diff = np.array(post) - np.array(pre)
        if np.all(diff == 0):
            continue
        r = stats.wilcoxon(diff, method="approx", zero_method="wilcox",
                           correction=False)
        rows.append((pre, post, float(r.pvalue)))
    block("WILCOXON_ASYMPTOTIC",
          "(pre, post, p) — wilcoxon(post - pre, method='approx', correction=False)",
          rows)

    rows = []
    for _ in range(12):
        n = int(rng.integers(5, 35))
        x = np.round(rng.normal(0, 1, n), 4)
        y = np.round(0.6 * x + rng.normal(0, 1, n), 4)
        r = stats.pearsonr(x, y)
        rows.append((list(x), list(y), float(r[0]), float(r[1])))
    block("PEARSON", "(x, y, r, p) — scipy.stats.pearsonr", rows)

    rows = []
    for _ in range(12):
        n = int(rng.integers(5, 35))
        x = np.round(rng.normal(0, 1, n), 4)
        y = np.round(0.6 * x + rng.normal(0, 1, n), 4)
        r = stats.spearmanr(x, y)
        rows.append((list(x), list(y), float(r[0]), float(r[1])))
    block("SPEARMAN", "(x, y, rho, p) — scipy.stats.spearmanr", rows)

    rows = []
    for _ in range(10):
        n1, n2 = int(rng.integers(5, 20)), int(rng.integers(5, 20))
        n = n1 + n2
        ca = np.round(rng.normal(20, 4, n1), 3)
        cb = np.round(rng.normal(20, 4, n2), 3)
        ya = np.round(0.6 * ca + rng.normal(12, 3, n1), 3)
        yb = np.round(0.6 * cb + rng.normal(15, 3, n2), 3)
        design = np.column_stack([np.ones(n), np.r_[np.ones(n1), np.zeros(n2)],
                                  np.r_[ca, cb]])
        y = np.r_[ya, yb]
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        resid = y - design @ beta
        df = n - 3
        mse = resid @ resid / df
        cov = mse * np.linalg.inv(design.T @ design)
        t = beta[1] / np.sqrt(cov[1, 1])
        rows.append((list(ya), list(ca), list(yb), list(cb), float(t),
                     float(2 * stats.t.sf(abs(t), df)), float(beta[1])))
    block("ANCOVA",
          "(values_a, cov_a, values_b, cov_b, t, p, beta) — OLS y ~ 1 + group + cov",
          rows)

    block("T_TAIL", "(t, df, two_sided_p) — 2 * scipy.stats.t.sf(|t|, df)",
          [(t, df, float(2 * stats.t.sf(abs(t), df)))
           for t, df in [(0.0, 1), (0.5, 1), (1.0, 2), (2.0, 3), (2.5, 5.5),
                         (3.0, 10), (1.96, 1000), (6.0, 7), (0.1, 120),
                         (12.0, 4), (-2.3, 17), (-0.7, 2.5), (40.0, 3),
                         (1e-8, 9)]])

    block("NORMAL_TAIL", "(z, two_sided_p) — 2 * scipy.stats.norm.sf(|z|)",
          [(z, float(2 * stats.norm.sf(abs(z))))
           for z in [0.0, 0.5, 1.0, 1.6449, 1.96, 2.5758, 3.0, 5.0, 8.0,
                     -1.2, -4.4]])

    block("BETAINC", "(a, b, x, I_x(a,b)) — scipy.special.betainc",
          [(a, b, x, float(special.betainc(a, b, x)))
           for a, b, x in [(0.5, 0.5, 0.3), (1.0, 2.0, 0.5), (2.5, 3.5, 0.25),
                           (10.0, 0.5, 0.9), (0.5, 10.0, 0.05), (50.0, 0.5, 0.99),
                           (3.0, 7.0, 0.4), (0.5, 0.5, 0.999)]])


if __name__ == "__main__":
    main()
