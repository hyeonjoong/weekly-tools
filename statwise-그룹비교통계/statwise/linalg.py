"""Minimal dense least-squares solver — pure standard library.

Only what an ANCOVA needs: solve ``X b ~= y`` for a tall, full-rank design
matrix and hand back the pieces every linear-model inference is built from:

    beta        the coefficients
    rss         residual sum of squares
    xtx_inv     (X'X)^-1, which scales every standard error and contrast

The solve is a **Householder QR**, not the textbook normal equations.  Forming
``X'X`` squares the condition number, and an ANCOVA design routinely contains a
baseline column around 1e2 next to 0/1 dummies — on real clinical data the
normal-equations route loses several digits of the residual and can report a
negative RSS.  QR keeps the conditioning of ``X`` itself.

Each column is additionally rescaled to unit norm before the factorisation and
the result is unscaled afterwards, so a covariate measured in ng (1e-9) beside
one measured in counts (1e6) does not by itself make the design look singular.

Rank deficiency is detected (a covariate that is constant, duplicated, or
collinear with the group dummies) and raised as a ``ValueError``, never silently
"solved" — a rank-deficient ANCOVA produces coefficients that look ordinary and
mean nothing.
"""

from __future__ import annotations

import math
from operator import mul
from typing import List, Sequence, Tuple

__all__ = ["lstsq", "lstsq_columns", "LstsqResult", "RankDeficientError"]


class RankDeficientError(ValueError):
    """The design matrix has linearly dependent columns."""

    def __init__(self, message: str, column: int = -1) -> None:
        super().__init__(message)
        #: index of the first column that added no new information
        self.column = column


class LstsqResult:
    """Coefficients plus everything needed for standard errors and contrasts."""

    __slots__ = ("beta", "rss", "xtx_inv", "n", "p", "fitted", "residuals")

    def __init__(self, beta: List[float], rss: float,
                 xtx_inv: List[List[float]], n: int, p: int,
                 fitted: List[float], residuals: List[float]) -> None:
        self.beta = beta
        self.rss = rss
        self.xtx_inv = xtx_inv
        self.n = n
        self.p = p
        self.fitted = fitted
        self.residuals = residuals

    @property
    def df_resid(self) -> int:
        return self.n - self.p

    def quadratic_form(self, c: Sequence[float]) -> float:
        """``c' (X'X)^-1 c`` — the variance multiplier of the contrast ``c'b``.

        Clamped at 0: the form is mathematically non-negative, but on a badly
        scaled design the accumulated rounding can land at ``-1e-19``, and a
        negative variance turns into a NaN standard error two lines later.
        """
        p = self.p
        total = math.fsum(
            c[i] * self.xtx_inv[i][j] * c[j]
            for i in range(p) for j in range(p) if c[i] != 0.0 and c[j] != 0.0)
        return total if total > 0.0 else 0.0

    def estimate(self, c: Sequence[float]) -> float:
        """The contrast value ``c'b``."""
        return math.fsum(ci * bi for ci, bi in zip(c, self.beta))


#: Columns whose QR pivot has shrunk below this fraction of the largest pivot
#: are treated as linearly dependent.  1e-10 on unit-normalised columns is far
#: above double-precision noise (~1e-16) and far below any real design.
_RANK_TOL = 1e-10


def _column_scales_cm(columns: Sequence[Sequence[float]], n: int, p: int
                      ) -> List[float]:
    scales = []
    for j in range(p):
        col = columns[j]
        # Factor out the largest magnitude first. The direct sqrt(sum(x*x))
        # squares every element, so a column around 1e-160 -- a real unit choice
        # for a plasma concentration -- underflowed the whole sum to 0 and the
        # column was then reported as "constant or collinear", the one diagnosis
        # this rescaling exists to prevent. Above 1e154 it overflowed to inf.
        m = max((abs(v) for v in col), default=0.0)
        if not math.isfinite(m):
            raise ValueError(
                f"설계행렬 {j + 1}번째 열에 유한하지 않은 값(inf/NaN)이 있습니다.")
        if m == 0.0:
            scales.append(1.0)
            continue
        s = m * math.sqrt(math.fsum((v / m) ** 2 for v in col))
        if not math.isfinite(s) or s == 0.0:
            raise ValueError(
                f"설계행렬 {j + 1}번째 열의 값이 배정밀도 범위를 넘어갑니다 "
                f"(약 1e154 이상). 단위를 바꾼 뒤 다시 실행하세요.")
        scales.append(s)
    return scales


def lstsq(x: Sequence[Sequence[float]], y: Sequence[float]) -> LstsqResult:
    """Least-squares fit of ``y`` on the columns of ``x`` (no implicit intercept).

    ``x`` is a list of **rows**.  Raises ``ValueError`` when there are fewer
    observations than columns, and ``RankDeficientError`` when the columns are
    linearly dependent.
    """
    n = len(x)
    if n == 0:
        raise ValueError("관측치가 없습니다.")
    p = len(x[0])
    if p == 0:
        raise ValueError("설명변수가 없습니다.")
    if any(len(row) != p for row in x):
        raise ValueError("설계행렬의 행 길이가 일정하지 않습니다.")
    return lstsq_columns([[row[j] for row in x] for j in range(p)], y)


def lstsq_columns(columns: Sequence[Sequence[float]],
                  y: Sequence[float]) -> LstsqResult:
    """Same fit, taking the design **column-major**.

    Column-major is not a stylistic choice.  The Householder sweep touches every
    element of every column p times, and with a row-major ``aug[i][j]`` that is
    two Python index operations per element: on a 200k-row trial file the
    row-major version of this function took ~2 minutes, which reads as a hang.
    Working on whole columns lets each reflection be a ``zip`` over two flat
    lists, which is where the ~50x comes from.
    """
    p = len(columns)
    if p == 0:
        raise ValueError("설명변수가 없습니다.")
    n = len(columns[0])
    if n == 0:
        raise ValueError("관측치가 없습니다.")
    if any(len(c) != n for c in columns):
        raise ValueError("설계행렬의 열 길이가 일정하지 않습니다.")
    if len(y) != n:
        raise ValueError("설계행렬과 반응변수의 길이가 다릅니다.")
    if n <= p:
        raise ValueError(
            f"모형의 모수({p}개)가 관측치({n}개)보다 많거나 같아 잔차 자유도가 "
            f"없습니다 — 공변량을 줄이거나 표본을 늘리세요.")

    scales = _column_scales_cm(columns, n, p)
    # Working copies; the reflections applied to y give Q'y for free.
    work = [[v / s for v in col] for col, s in zip(columns, scales)]
    ycol = [float(v) for v in y]

    for k in range(p):
        col = work[k]
        tail = col[k:]
        norm = math.sqrt(math.fsum(t * t for t in tail))
        if norm == 0.0:
            raise RankDeficientError(
                f"설계행렬 {k + 1}번째 열이 다른 열들로 완전히 설명됩니다 "
                f"(선형종속). 상수 공변량이거나 그룹과 겹치는 변수입니다.", k)
        alpha = -math.copysign(norm, tail[0] if tail[0] != 0.0 else 1.0)
        v = list(tail)
        v[0] -= alpha
        vnorm2 = math.fsum(t * t for t in v)
        if vnorm2 > 0.0:
            two_over = 2.0 / vnorm2
            for target in work[k + 1:] + [ycol]:
                seg = target[k:]
                f = two_over * sum(map(mul, v, seg))
                target[k:] = [b - f * a for a, b in zip(v, seg)]
        # column k becomes (0,...,0, alpha, 0,...,0) by construction
        col[k:] = [alpha] + [0.0] * (n - k - 1)

    diag = [abs(work[k][k]) for k in range(p)]
    biggest = max(diag)
    for k, d in enumerate(diag):
        if biggest == 0.0 or d / biggest < _RANK_TOL:
            raise RankDeficientError(
                f"설계행렬 {k + 1}번째 열이 다른 열들과 (거의) 선형종속입니다. "
                f"같은 정보를 담은 공변량이 중복되었거나, 공변량이 그룹 안에서 "
                f"상수입니다 — 해당 변수를 빼고 다시 실행하세요.", k)

    # R[i][j] == work[j][i] for i <= j.  Back-substitution: R b_scaled = Q'y
    beta_s = [0.0] * p
    for i in range(p - 1, -1, -1):
        acc = ycol[i] - math.fsum(work[j][i] * beta_s[j]
                                  for j in range(i + 1, p))
        beta_s[i] = acc / work[i][i]
    beta = [beta_s[j] / scales[j] for j in range(p)]

    # Invert the upper-triangular R, then (X'X)^-1 = R^-1 R^-T (scaled units).
    rinv = [[0.0] * p for _ in range(p)]
    for i in range(p - 1, -1, -1):
        rinv[i][i] = 1.0 / work[i][i]
        for j in range(i + 1, p):
            rinv[i][j] = -rinv[i][i] * math.fsum(
                work[k][i] * rinv[k][j] for k in range(i + 1, j + 1))
    xtx_inv = [[0.0] * p for _ in range(p)]
    for i in range(p):
        for j in range(i, p):
            val = math.fsum(rinv[i][k] * rinv[j][k] for k in range(max(i, j), p))
            # Divide by the two scales separately: their product underflows to
            # zero for a covariate around 1e-300 and raised ZeroDivisionError
            # straight out of the CLI.
            val = val / scales[i] / scales[j]
            xtx_inv[i][j] = val
            xtx_inv[j][i] = val

    # Residuals column-wise (p passes over n) rather than a dot product per row.
    residuals = [float(v) for v in y]
    for col, b in zip(columns, beta):
        if b != 0.0:
            residuals = [r - b * c for r, c in zip(residuals, col)]
    fitted = [float(v) - r for v, r in zip(y, residuals)]
    # The tail of Q'y is the numerically clean RSS; fall back to the residuals
    # only if it comes out non-finite or negative.
    rss = math.fsum(t * t for t in ycol[p:])
    if not math.isfinite(rss) or rss < 0.0:
        rss = math.fsum(r * r for r in residuals)
    return LstsqResult(beta, rss, xtx_inv, n, p, fitted, residuals)


