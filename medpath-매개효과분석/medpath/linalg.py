"""Least-squares machinery — pure standard library.

Two solvers, deliberately:

* :func:`qr_lstsq` — Householder QR on the (column-scaled) design matrix.
  Numerically stable, returns residuals and ``(X'X)^-1`` for standard errors.
  Used for every *reported* regression.
* :class:`GramCache` — precomputes, once, the per-row cross-products of the
  *whole* variable set ``[1, X, M1..Mk, covariates, Y]``. Every equation of a
  mediation model is then a submatrix of one accumulated Gram matrix, so a
  bootstrap replicate costs a single weighted sum plus a few tiny Cholesky
  solves instead of k+2 full regressions. Cholesky squares the condition
  number, so this path is never used for reported standard errors — only for
  bootstrap/jackknife point estimates, where it is the difference between
  seconds and minutes in pure Python.

Both work on plain lists of floats; ``X`` is row-major (``X[i][j]``).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

__all__ = ["SingularDesignError", "qr_lstsq", "GramCache", "LstsqResult"]


class SingularDesignError(ValueError):
    """Raised when the design matrix is rank-deficient (collinear columns)."""


class LstsqResult:
    """Coefficients plus everything needed for inference."""

    __slots__ = ("beta", "rss", "xtx_inv", "n", "p", "rcond")

    def __init__(self, beta: List[float], rss: float,
                 xtx_inv: List[List[float]], n: int, p: int, rcond: float):
        self.beta = beta
        self.rss = rss
        self.xtx_inv = xtx_inv
        self.n = n
        self.p = p
        self.rcond = rcond


def _column_scales(X: Sequence[Sequence[float]], p: int) -> List[float]:
    scales = []
    for j in range(p):
        s = math.sqrt(sum(row[j] * row[j] for row in X))
        scales.append(s if s > 0.0 else 0.0)
    return scales


def qr_lstsq(X: Sequence[Sequence[float]], y: Sequence[float],
             names: Optional[Sequence[str]] = None) -> LstsqResult:
    """Solve min ||X b - y||^2 by Householder QR.

    Columns are scaled to unit norm first (and unscaled afterwards), which
    keeps the triangular factor well conditioned even when predictors live on
    wildly different scales (age in years next to an EEG power in uV^2).

    Raises :class:`SingularDesignError` naming the offending column when the
    design is rank deficient.
    """
    n = len(X)
    if n == 0:
        raise SingularDesignError("분석에 쓸 수 있는 행이 없습니다 (N=0).")
    p = len(X[0])
    if p == 0:
        raise SingularDesignError("설명변수가 하나도 없습니다.")
    if n < p:
        raise SingularDesignError(
            "표본 수(N=%d)가 추정할 계수 수(%d)보다 적어 회귀를 적합할 수 없습니다." % (n, p))

    scales = _column_scales(X, p)
    for j, s in enumerate(scales):
        if s == 0.0:
            raise SingularDesignError(
                "'%s' 열이 전부 0이라 회귀에 쓸 수 없습니다."
                % (names[j] if names else "col%d" % j))

    a = [[row[j] / scales[j] for j in range(p)] for row in X]
    b = list(y)

    diag = [0.0] * p
    for k in range(p):
        normx = math.sqrt(sum(a[i][k] * a[i][k] for i in range(k, n)))
        if normx == 0.0:
            raise SingularDesignError(
                "'%s' 열이 다른 열들과 완전히 겹칩니다(공선성). 변수 하나를 빼고 다시 실행하세요."
                % (names[k] if names else "col%d" % k))
        alpha = -normx if a[k][k] > 0 else normx
        v = [0.0] * n
        v[k] = a[k][k] - alpha
        for i in range(k + 1, n):
            v[i] = a[i][k]
        vnorm2 = sum(v[i] * v[i] for i in range(k, n))
        if vnorm2 > 0.0:
            for j in range(k, p):
                s = sum(v[i] * a[i][j] for i in range(k, n))
                f = 2.0 * s / vnorm2
                if f != 0.0:
                    for i in range(k, n):
                        a[i][j] -= f * v[i]
            s = sum(v[i] * b[i] for i in range(k, n))
            f = 2.0 * s / vnorm2
            if f != 0.0:
                for i in range(k, n):
                    b[i] -= f * v[i]
        diag[k] = a[k][k]

    dmax = max(abs(d) for d in diag)
    dmin = min(abs(d) for d in diag)
    tol = max(n, p) * 2.220446049250313e-16 * dmax
    for k in range(p):
        if abs(diag[k]) <= tol:
            raise SingularDesignError(
                "'%s' 열이 다른 열들의 조합으로 완전히 설명됩니다(완전 공선성). "
                "중복되거나 상수인 변수를 빼고 다시 실행하세요."
                % (names[k] if names else "col%d" % k))

    # Back-substitute R beta_scaled = Q'y.
    beta_s = [0.0] * p
    for k in range(p - 1, -1, -1):
        s = b[k] - sum(a[k][j] * beta_s[j] for j in range(k + 1, p))
        beta_s[k] = s / a[k][k]

    rss = sum(b[i] * b[i] for i in range(p, n))
    if rss < 0.0:
        rss = 0.0

    # R^-1 by back-substitution on the identity, then (X'X)^-1 = R^-1 R^-T.
    rinv = [[0.0] * p for _ in range(p)]
    for col in range(p):
        for k in range(col, -1, -1):
            s = (1.0 if k == col else 0.0)
            s -= sum(a[k][j] * rinv[j][col] for j in range(k + 1, col + 1))
            rinv[k][col] = s / a[k][k]
    xtx_inv = [[0.0] * p for _ in range(p)]
    for i in range(p):
        for j in range(i, p):
            s = sum(rinv[i][k] * rinv[j][k] for k in range(max(i, j), p))
            s /= scales[i] * scales[j]
            xtx_inv[i][j] = s
            xtx_inv[j][i] = s

    beta = [beta_s[j] / scales[j] for j in range(p)]
    rcond = dmin / dmax if dmax > 0 else 0.0
    return LstsqResult(beta, rss, xtx_inv, n, p, rcond)


class GramCache:
    """One cached cross-product table serving every equation of a model.

    Build it from the full variable matrix ``Z = [1, X, M1..Mk, covariates, Y]``.
    Each row contributes the flattened upper triangle of ``z_i z_i'``; the
    accumulated triangle for any row subset contains ``X'X`` and ``X'y`` for
    *every* sub-model as a submatrix, so all k+2 regressions of a bootstrap
    replicate come out of a single pass.

    Columns are scaled to unit norm (using the full sample) before the
    products are formed, keeping the Cholesky well conditioned; coefficients
    are unscaled back to original units on the way out.
    """

    __slots__ = ("q", "n", "scales", "rows", "m", "_pairs", "_pos")

    def __init__(self, columns: Sequence[Sequence[float]]):
        q = len(columns)
        if q == 0:
            raise SingularDesignError("GramCache: 변수가 없습니다.")
        n = len(columns[0])
        if n == 0:
            raise SingularDesignError("GramCache: 빈 데이터")
        self.q = q
        self.n = n
        scales = []
        for col in columns:
            s = math.sqrt(sum(v * v for v in col))
            if s == 0.0:
                raise SingularDesignError("GramCache: 값이 전부 0인 열이 있습니다.")
            scales.append(s)
        self.scales = scales
        self._pairs = [(i, j) for i in range(q) for j in range(i, q)]
        self._pos = {}
        for k, (i, j) in enumerate(self._pairs):
            self._pos[(i, j)] = k
            self._pos[(j, i)] = k
        self.m = len(self._pairs)
        scaled = [[v / scales[c] for v in columns[c]] for c in range(q)]
        rows = []
        for r in range(n):
            zr = [scaled[c][r] for c in range(q)]
            rows.append([zr[i] * zr[j] for (i, j) in self._pairs])
        self.rows = rows

    def full_acc(self) -> List[float]:
        """Accumulated triangle over all rows (weight 1 each)."""
        acc = [0.0] * self.m
        for row in self.rows:
            acc = [a + r for a, r in zip(acc, row)]
        return acc

    def weighted_acc(self, weights: Sequence[Tuple[int, float]]) -> List[float]:
        """Accumulated triangle for ``(row_index, weight)`` pairs."""
        acc = [0.0] * self.m
        for idx, w in weights:
            row = self.rows[idx]
            acc = [a + w * r for a, r in zip(acc, row)]
        return acc

    def acc_minus_row(self, acc: Sequence[float], idx: int) -> List[float]:
        """Leave-one-out accumulator (used by the BCa jackknife)."""
        return [a - r for a, r in zip(acc, self.rows[idx])]

    def solve(self, acc: Sequence[float], predictors: Sequence[int],
              outcome: int) -> Optional[List[float]]:
        """OLS coefficients of ``outcome`` on ``predictors`` for one accumulator.

        Returns ``None`` when the sub-design is not positive definite (e.g. a
        bootstrap resample containing only one level of a binary predictor);
        callers count and report those instead of crashing.
        """
        p = len(predictors)
        pos = self._pos
        g = [[acc[pos[(predictors[i], predictors[j])]] for j in range(p)]
             for i in range(p)]
        rhs = [acc[pos[(predictors[i], outcome)]] for i in range(p)]
        scale_ref = max(g[i][i] for i in range(p))
        if scale_ref <= 0.0:
            return None
        tol = 1e-11 * scale_ref
        L = [[0.0] * p for _ in range(p)]
        for i in range(p):
            Li = L[i]
            for j in range(i + 1):
                s = g[i][j] - sum(Li[k] * L[j][k] for k in range(j))
                if i == j:
                    if s <= tol:
                        return None
                    Li[i] = math.sqrt(s)
                else:
                    Li[j] = s / L[j][j]
        z = [0.0] * p
        for i in range(p):
            z[i] = (rhs[i] - sum(L[i][k] * z[k] for k in range(i))) / L[i][i]
        beta_s = [0.0] * p
        for i in range(p - 1, -1, -1):
            beta_s[i] = (z[i] - sum(L[k][i] * beta_s[k] for k in range(i + 1, p))) / L[i][i]
        s_out = self.scales[outcome]
        return [beta_s[j] * s_out / self.scales[predictors[j]] for j in range(p)]
