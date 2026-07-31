"""Baseline-adjusted between-group comparison (ANCOVA) — pure standard library.

Why this exists as well as the change-score contrast in :mod:`posthoc`:

A change-score analysis (``post − baseline``, compared between arms) is unbiased
only when the arms start equal.  Randomisation makes that true *in expectation*,
not in any one trial — the bundled ISI example starts 1.6 points apart — and the
change score is also vulnerable to regression to the mean.  Fitting

    y_post = β0 + β1·arm + β2·y_baseline

instead conditions on where each patient actually started.  It is unbiased under
baseline imbalance, and strictly more powerful than the change score whenever the
baseline–follow-up correlation exceeds 0.5, which it essentially always does for
a clinical rating scale.  EMA's guideline on baseline covariates and current
CONSORT-era practice both treat it as the default primary analysis, so a reviewer
will ask for it.

Implementation notes: ordinary least squares with sum-to-zero coding for the arm
factor, solved by Gaussian elimination on the normal equations (the design has at
most ``g + 1`` columns, so conditioning is a non-issue at clinical sample sizes).
The reported quantity is the **adjusted mean difference** between two arms at the
common baseline value — the LSMEAN difference SPSS/SAS print — with a t-based
confidence interval on ``N − g − 1`` degrees of freedom.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import adjust, mean
from .dataio import Panel
from .special import t_ppf, t_sf_two_sided

__all__ = ["AncovaContrast", "AncovaResult", "ancova_analysis", "solve_ols"]


def solve_ols(x: Sequence[Sequence[float]], y: Sequence[float]
              ) -> Tuple[List[float], List[List[float]], float, int]:
    """Least-squares fit of ``y ~ x`` (design already includes the intercept).

    Returns ``(beta, xtx_inv, sigma2, df_resid)``.  Raises ``ValueError`` when
    the design is rank-deficient, which is what a constant baseline column or a
    fully confounded arm looks like.
    """
    n = len(y)
    p = len(x[0]) if n else 0
    if n <= p:
        raise ValueError("공변량 보정에 필요한 자유도가 부족합니다.")
    xtx = [[math.fsum(x[i][a] * x[i][b] for i in range(n)) for b in range(p)]
           for a in range(p)]
    xty = [math.fsum(x[i][a] * y[i] for i in range(n)) for a in range(p)]

    # Gauss–Jordan on [XtX | I | Xty] gives beta and (XtX)^-1 in one pass.
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(p)] + [xty[i]]
           for i, row in enumerate(xtx)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError(
                "공변량 행렬이 특이(singular)합니다 — 기준시점 값이 모두 "
                "같거나 그룹과 완전히 겹칩니다.")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        inv = 1.0 / aug[col][col]
        aug[col] = [v * inv for v in aug[col]]
        for r in range(p):
            if r != col and aug[r][col]:
                factor = aug[r][col]
                aug[r] = [v - factor * w for v, w in zip(aug[r], aug[col])]
    beta = [aug[i][2 * p] for i in range(p)]
    xtx_inv = [[aug[i][p + j] for j in range(p)] for i in range(p)]
    resid = [y[i] - math.fsum(x[i][a] * beta[a] for a in range(p))
             for i in range(n)]
    df = n - p
    sigma2 = math.fsum(r * r for r in resid) / df
    return beta, xtx_inv, sigma2, df


@dataclass
class AncovaContrast:
    time: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    adjusted_diff: float             # a − b at the common baseline
    ci_low: float
    ci_high: float
    t: float
    df: float
    p_raw: float
    p_adj: float
    slope: float                     # coefficient on the baseline covariate
    unadjusted_diff: float           # the change-score answer, for comparison
    primary: bool = False


@dataclass
class AncovaResult:
    baseline: str
    contrasts: List[AncovaContrast] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def ancova_analysis(panel: Panel, baseline: int = 0, alpha: float = 0.05,
                    correction: str = "holm",
                    primary_time: Optional[str] = None) -> Optional[AncovaResult]:
    """Baseline-adjusted arm comparison at each post-baseline visit.

    Returns ``None`` when the panel has fewer than two groups (there is nothing
    to contrast).  Only subjects with both the baseline and the visit observed
    contribute, exactly as for the change-score analysis.
    """
    if panel.groups is None or len(panel.group_labels()) < 2:
        return None
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    labels = panel.group_labels()
    res = AncovaResult(baseline=panel.times[baseline])

    for j, tname in enumerate(panel.times):
        if j == baseline:
            continue
        rows: List[Tuple[str, float, float]] = []
        for i in range(panel.n_subjects):
            base = panel.values[i][baseline]
            post = panel.values[i][j]
            if base is None or post is None:
                continue
            rows.append(((panel.groups or [])[i], float(base), float(post)))
        present = [g for g in labels if any(r[0] == g for r in rows)]
        if len(present) < 2 or len(rows) < len(present) + 2:
            continue

        # Sum-to-zero coding: columns are [intercept, g-1 arm contrasts, baseline].
        n_arm = len(present) - 1
        design: List[List[float]] = []
        y: List[float] = []
        for lab, base, post in rows:
            code = [0.0] * n_arm
            if lab == present[-1]:
                code = [-1.0] * n_arm
            else:
                code[present.index(lab)] = 1.0
            design.append([1.0] + code + [base])
            y.append(post)
        try:
            beta, xtx_inv, sigma2, df = solve_ols(design, y)
        except ValueError as exc:
            res.notes.append(f"{tname}: {exc}")
            continue
        slope = beta[-1]
        crit = t_ppf(1.0 - alpha / 2.0, df)

        pairs: List[AncovaContrast] = []
        for a in range(len(present)):
            for b in range(a + 1, len(present)):
                # Contrast vector on the coefficient scale: the difference of
                # the two arms' effect codes leaves intercept and covariate out.
                cvec = [0.0] * len(beta)
                for arm, sign in ((a, 1.0), (b, -1.0)):
                    if arm == len(present) - 1:
                        for c in range(n_arm):
                            cvec[1 + c] -= sign
                    else:
                        cvec[1 + arm] += sign
                est = math.fsum(cvec[i] * beta[i] for i in range(len(beta)))
                var = math.fsum(
                    cvec[u] * xtx_inv[u][v] * cvec[v]
                    for u in range(len(beta)) for v in range(len(beta))) * sigma2
                se = math.sqrt(var) if var > 0 else float("nan")
                if se and math.isfinite(se):
                    t = est / se
                    p = t_sf_two_sided(t, df)
                    lo, hi = est - crit * se, est + crit * se
                else:
                    t = p = lo = hi = float("nan")
                ca = [r for r in rows if r[0] == present[a]]
                cb = [r for r in rows if r[0] == present[b]]
                unadj = (mean([r[2] - r[1] for r in ca])
                         - mean([r[2] - r[1] for r in cb]))
                pairs.append(AncovaContrast(
                    time=tname, group_a=present[a], group_b=present[b],
                    n_a=len(ca), n_b=len(cb), adjusted_diff=est, ci_low=lo,
                    ci_high=hi, t=t, df=float(df), p_raw=p, p_adj=float("nan"),
                    slope=slope, unadjusted_diff=unadj,
                    primary=(tname == primary_time)))
        res.contrasts.extend(pairs)

    secondary = [c for c in res.contrasts if not c.primary]
    for row, padj in zip(secondary,
                         adjust([r.p_raw for r in secondary], correction)):
        row.p_adj = padj
    for row in res.contrasts:
        if row.primary:
            row.p_adj = row.p_raw
    if not res.contrasts:
        return None
    return res
