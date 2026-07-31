"""Independent, exact reference implementations used to check medpath.

These deliberately share **no code** with the package: OLS is solved here in
exact rational arithmetic (``fractions.Fraction``) by Gauss–Jordan elimination
on the normal equations. If medpath's Householder QR and this agree to 1e-10,
the coefficients are right for reasons unrelated to how they were computed.
"""

from __future__ import annotations

from fractions import Fraction as F
from typing import List, Sequence, Tuple


def _to_frac(v) -> F:
    return F(str(v)) if not isinstance(v, F) else v


def exact_ols(X: Sequence[Sequence[float]], y: Sequence[float]) -> List[F]:
    """Exact OLS coefficients for a full-rank design (no intercept added)."""
    n = len(X)
    p = len(X[0])
    Xf = [[_to_frac(v) for v in row] for row in X]
    yf = [_to_frac(v) for v in y]
    # normal equations X'X b = X'y
    a = [[sum((Xf[i][r] * Xf[i][c] for i in range(n)), F(0)) for c in range(p)]
         for r in range(p)]
    b = [sum((Xf[i][r] * yf[i] for i in range(n)), F(0)) for r in range(p)]
    # Gauss-Jordan with partial pivoting on exact rationals.
    for col in range(p):
        piv = max(range(col, p), key=lambda r: abs(a[r][col]))
        if a[piv][col] == 0:
            raise ValueError("singular design in exact_ols")
        a[col], a[piv] = a[piv], a[col]
        b[col], b[piv] = b[piv], b[col]
        inv = F(1) / a[col][col]
        a[col] = [v * inv for v in a[col]]
        b[col] = b[col] * inv
        for r in range(p):
            if r == col or a[r][col] == 0:
                continue
            f = a[r][col]
            a[r] = [a[r][c] - f * a[col][c] for c in range(p)]
            b[r] = b[r] - f * b[col]
    return b


def exact_ols_with_intercept(cols: Sequence[Sequence[float]],
                             y: Sequence[float]) -> List[F]:
    """Exact OLS of ``y`` on ``[1] + cols`` (columns given column-wise)."""
    n = len(y)
    X = [[1.0] + [c[i] for c in cols] for i in range(n)]
    return exact_ols(X, y)


def exact_residual_ss(cols: Sequence[Sequence[float]], y: Sequence[float]) -> F:
    beta = exact_ols_with_intercept(cols, y)
    n = len(y)
    ss = F(0)
    for i in range(n):
        row = [F(1)] + [_to_frac(c[i]) for c in cols]
        fit = sum((b * v for b, v in zip(beta, row)), F(0))
        r = _to_frac(y[i]) - fit
        ss += r * r
    return ss


def exact_se(cols: Sequence[Sequence[float]], y: Sequence[float]) -> List[float]:
    """Classical OLS standard errors, from exact RSS and an exact (X'X)^-1."""
    n = len(y)
    p = len(cols) + 1
    X = [[F(1)] + [_to_frac(c[i]) for c in cols] for i in range(n)]
    xtx = [[sum((X[i][r] * X[i][c] for i in range(n)), F(0)) for c in range(p)]
           for r in range(p)]
    # invert exactly by Gauss-Jordan against the identity
    aug = [row[:] + [F(1) if i == j else F(0) for j in range(p)]
           for i, row in enumerate(xtx)]
    for col in range(p):
        piv = max(range(col, p), key=lambda r: abs(aug[r][col]))
        if aug[piv][col] == 0:
            raise ValueError("singular")
        aug[col], aug[piv] = aug[piv], aug[col]
        inv = F(1) / aug[col][col]
        aug[col] = [v * inv for v in aug[col]]
        for r in range(p):
            if r == col or aug[r][col] == 0:
                continue
            f = aug[r][col]
            aug[r] = [aug[r][c] - f * aug[col][c] for c in range(2 * p)]
    xtx_inv = [row[p:] for row in aug]
    sigma2 = exact_residual_ss(cols, y) / F(n - p)
    return [float(sigma2 * xtx_inv[i][i]) ** 0.5 for i in range(p)]


def simple_slope(x: Sequence[float], y: Sequence[float]) -> float:
    """Textbook simple-regression slope: Sxy / Sxx."""
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    return sxy / sxx


def write_csv(path, header: Sequence[str], rows: Sequence[Sequence]) -> str:
    import csv
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return str(path)
