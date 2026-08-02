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
from .covariates import (Covariate, complete_subjects,
                         encode_covariates, independent_of,
                         orthonormal_basis)
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

    # Equilibrate before eliminating: divide row and column j of XᵀX by
    # sqrt(XᵀX[j][j]) so every diagonal entry becomes 1.  Without this the
    # singularity test was an *absolute* 1e-12 on entries whose size depends on
    # the covariate's units — the same variable in mol/L rather than nmol/L
    # turned a perfectly estimable design into "singular" and deleted the whole
    # section.  On the scaled matrix the threshold is finally unit-free.
    d = [math.sqrt(xtx[j][j]) if xtx[j][j] > 0 else 0.0 for j in range(p)]
    if any(dj == 0.0 for dj in d):
        raise ValueError(
            "공변량 행렬이 특이(singular)합니다 — 값이 모두 0인 열이 있습니다.")
    scaled = [[xtx[i][j] / (d[i] * d[j]) for j in range(p)] for i in range(p)]

    # Gauss–Jordan on [XtX | I | Xty] gives beta and (XtX)^-1 in one pass.
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(p)] + [xty[i] / d[i]]
           for i, row in enumerate(scaled)]
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
    # Undo the scaling: beta_j = beta_scaled_j / d_j, (XᵀX)⁻¹ likewise.
    beta = [aug[i][2 * p] / d[i] for i in range(p)]
    xtx_inv = [[aug[i][p + j] / (d[i] * d[j]) for j in range(p)]
               for i in range(p)]
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
    # Extra subject-level covariates that entered the model, as encoded column
    # names (``나이``, ``기관=B`` …).  Empty for the baseline-only model.
    covariates: List[str] = field(default_factory=list)


def ancova_analysis(panel: Panel, baseline: int = 0, alpha: float = 0.05,
                    correction: str = "holm",
                    primary_time: Optional[str] = None,
                    covariates: Optional[Sequence[Covariate]] = None
                    ) -> Optional[AncovaResult]:
    """Baseline-adjusted arm comparison at each post-baseline visit.

    Returns ``None`` when the panel has fewer than two groups (there is nothing
    to contrast).  Only subjects with both the baseline and the visit observed
    contribute, exactly as for the change-score analysis.

    *covariates* are additional subject-level variables (age, site, stratum);
    the default (``None``) takes whatever the panel was loaded with, and an
    explicit empty sequence forces the baseline-only model.
    They are mean-centred over the subjects in each visit's fit, so the reported
    quantity stays the LS-mean difference at the common baseline **and** the
    mean covariate value — the same thing SAS ``LSMEANS`` prints.  A subject
    with any covariate missing is out of that fit; the count is noted.
    """
    if panel.groups is None or len(panel.group_labels()) < 2:
        return None
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    labels = panel.group_labels()
    covs = list(panel.covariates if covariates is None else covariates)
    res = AncovaResult(baseline=panel.times[baseline])
    cov_names: List[str] = []
    dropped_notes: List[str] = []
    n_missing_cov = 0

    for j, tname in enumerate(panel.times):
        if j == baseline:
            continue
        rows: List[Tuple[str, float, float]] = []
        who: List[int] = []
        for i in range(panel.n_subjects):
            base = panel.values[i][baseline]
            post = panel.values[i][j]
            if base is None or post is None:
                continue
            who.append(i)
            rows.append(((panel.groups or [])[i], float(base), float(post)))
        if covs:
            keep, n_drop = complete_subjects(covs, who)
            if n_drop:
                n_missing_cov = max(n_missing_cov, n_drop)
                pos = {s: k for k, s in enumerate(who)}
                rows = [rows[pos[s]] for s in keep]
                who = keep
        present = [g for g in labels if any(r[0] == g for r in rows)]
        design_cov: List[List[float]] = [[] for _ in rows]
        cov_names_here: List[str] = []
        if covs and rows:
            try:
                block = encode_covariates(covs, who)
            except ValueError as exc:
                res.notes.append(f"{tname}: {exc}")
                continue
            design_cov = block.columns
            cov_names_here = list(block.names)
            for name in block.names:
                if name not in cov_names:
                    cov_names.append(name)
            for msg in block.dropped:
                text = f"{tname}: 공변량 {msg}"
                if text not in dropped_notes:
                    dropped_notes.append(text)
        n_arm = max(len(present) - 1, 0)
        if design_cov and design_cov[0] and len(present) >= 2:
            # Rank-check each covariate column against *this visit's* own
            # columns — intercept, arm codes, baseline — not just against the
            # other covariates.  A site factor nested in the treatment arm is
            # perfectly aliased with the arm codes, and solving anyway raised a
            # "singular" error that deleted every contrast at that visit.  MMRM
            # drops the column and carries on; so does this, for the same data.
            fixed = [[1.0] * len(rows)]
            for c in range(n_arm):
                fixed.append([
                    -1.0 if lab == present[-1] else (1.0 if present.index(lab) == c
                                                     else 0.0)
                    for lab, _b, _p in rows])
            fixed.append([base for _lab, base, _p in rows])
            basis = orthonormal_basis(fixed)
            keep_cols: List[int] = []
            for c, name in enumerate(cov_names_here):
                col = [r[c] for r in design_cov]
                if independent_of(col, basis):
                    keep_cols.append(c)
                    basis = orthonormal_basis([list(b) for b in basis] + [col])
                else:
                    text = (f"{tname}: 공변량 '{name}' 이(가) 군 구분 또는 "
                            "기저값과 완전히 겹쳐 이 시점에서는 빼고 "
                            "적합했습니다.")
                    if text not in dropped_notes:
                        dropped_notes.append(text)
            if len(keep_cols) != len(cov_names_here):
                design_cov = [[r[c] for c in keep_cols] for r in design_cov]
        n_cov = len(design_cov[0]) if design_cov else 0
        if len(present) < 2 or len(rows) < len(present) + 2 + n_cov:
            continue

        # Sum-to-zero coding: columns are
        # [intercept, g-1 arm contrasts, baseline, covariate block].
        design: List[List[float]] = []
        y: List[float] = []
        for (lab, base, post), extra in zip(rows, design_cov):
            code = [0.0] * n_arm
            if lab == present[-1]:
                code = [-1.0] * n_arm
            else:
                code[present.index(lab)] = 1.0
            design.append([1.0] + code + [base] + list(extra))
            y.append(post)
        try:
            beta, xtx_inv, sigma2, df = solve_ols(design, y)
        except ValueError as exc:
            res.notes.append(f"{tname}: {exc}")
            continue
        # The baseline coefficient sits right after the arm codes — *not* last,
        # once a covariate block follows it.
        slope = beta[1 + n_arm]
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

    # The primary visit is its own family, not an exemption: with three arms
    # there are three pairwise contrasts at that visit and reporting all of them
    # unadjusted inflates the error rate at exactly the timepoint the protocol
    # cares about.  A family of one is unchanged by Holm/BH.
    for family in ([c for c in res.contrasts if c.primary],
                   [c for c in res.contrasts if not c.primary]):
        for row, padj in zip(family,
                             adjust([r.p_raw for r in family], correction)):
            row.p_adj = padj
    res.covariates = cov_names
    res.notes.extend(dropped_notes)
    if cov_names:
        res.notes.append("보정 공변량: 기저값 + " + ", ".join(cov_names)
                         + " (모두 평균 중심화 — 조정평균차는 평균 공변량 값에서의"
                           " 군간 차이입니다).")
    if n_missing_cov:
        res.notes.append(f"공변량 값이 없는 대상 최대 {n_missing_cov}명은 이 "
                         "분석에서 제외되었습니다.")
    # Same over-adjustment check the MMRM does: the model is still solvable at
    # df = 3, and its confidence intervals are still worthless.
    worst = min((c.df for c in res.contrasts if math.isfinite(c.df)),
                default=float("inf"))
    if worst < 5:
        res.notes.append(
            f"잔차 자유도가 {worst:.0f} 뿐입니다 — 대상 수에 비해 공변량이 "
            "많습니다. 신뢰구간을 그대로 인용하지 마세요.")
    if not res.contrasts and not res.notes:
        return None
    # A result with no contrasts but with notes is *not* nothing: those notes
    # say why every visit failed (covariate aliased with arm, too many columns,
    # too few subjects).  Returning None here threw that explanation away and
    # the [5b] section simply vanished from the report.
    return res
