"""MMRM — mixed model for repeated measures, REML with an unstructured covariance.

Why this module exists
----------------------
Every other omnibus in this package is a **complete-case** analysis: the
repeated-measures ANOVA in :mod:`anova` throws away any subject who missed a
single visit, and LOCF/BOCF in :mod:`sensitivity` fills the hole with a value
nobody measured.  In a real trial that is the difference between analysing 120
patients and analysing 78 of them, and the 42 who dropped out are exactly the
ones whose outcome you care about.

MMRM is the analysis regulators actually expect for a continuous longitudinal
endpoint (EMA CPMP/EWP/1776/99 on missing data; the FDA-era "MMRM as primary"
convention after Mallinckrodt et al. 2008).  It keeps every subject who has at
least one observation, models the within-subject covariance as **unstructured**
(no sphericity assumption at all — that is why no GG/HF correction appears
here), and is valid under MAR rather than the far stronger MCAR that
complete-case analysis needs.

What is fitted
--------------
Two shapes, chosen automatically:

* **grouped** (``adjusted=True``) — response is the change from baseline at each
  post-baseline visit; fixed effects are a saturated ``visit × arm`` cell mean
  plus a baseline covariate **interacted with visit** (SAS's
  ``trt vis trt*vis base base*vis``).  The baseline column is mean-centred, so
  each cell coefficient *is* the LS-mean change at the average baseline value.
  Contrasts are the between-arm differences in adjusted mean change per visit —
  the row a submission table shows.
* **ungrouped** (``adjusted=False``) — response is the raw value at every visit,
  fixed effects are a saturated visit mean.  Contrasts are each visit against
  the baseline visit, i.e. the mean change from baseline using *all* observed
  data instead of completers only.

Estimation is REML by EM.  The E-step completes each subject's residual vector
with its conditional expectation given the observed visits, and adds back both
the conditional covariance of the missing part and the ``X A X'`` term that
carries the uncertainty in β̂ — that last term is what makes it REML rather than
ML, i.e. what stops the variances being biased low at clinical sample sizes.
The restricted log-likelihood is recomputed every sweep and is asserted
non-decreasing by the test-suite.

Two exact identities pin the implementation down, and both are tested:

* with complete data and two or more arms, the per-visit contrast, its standard
  error and its degrees of freedom reproduce the per-visit **ANCOVA** of
  :mod:`ancova` exactly (Zellner: SUR with identical regressors in every
  equation collapses to equation-by-equation OLS);
* with complete data and one arm, the visit-vs-baseline contrast reproduces the
  **paired t-test** of :mod:`basics` exactly.

Degrees of freedom
------------------
``df = (subjects contributing at that visit) − (mean parameters at that visit)``.
On complete data that is the exact residual df (see the identities above).  With
missing data it is an approximation and is *not* Kenward–Roger; it is reported
as such in the output rather than dressed up.  For a small trial (say n < 30 per
arm) with heavy dropout, a Kenward–Roger fit in R (``mmrm::mmrm``) or SAS
(``PROC MIXED ... ddfm=kr``) will give a slightly wider interval than this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import adjust
from .dataio import Panel
from .special import t_ppf, t_sf_two_sided

__all__ = ["MMRMContrast", "MMRMLsMean", "MMRMResult", "mmrm_analysis"]

MAX_TIMES = 12        # unstructured Σ has T(T+1)/2 parameters — stop somewhere
# Runtime guard.  The EM is pure Python and its cost grows with the number of
# subjects times the *square* of the number of visits (T×T solves per subject
# per sweep), and more visits also need more sweeps: 2 000 × 6 takes ~4 s,
# 5 000 × 8 ~28 s, but 5 000 × 12 takes ~140 s — a plain subjects × visits
# ceiling would wave that last one straight through.  Budgeting n·T² keeps the
# worst case near a minute instead of turning a report into a coffee break.
MAX_WORK = 320_000


# ---------------------------------------------------------------- linear algebra
def _cholesky(a: Sequence[Sequence[float]]) -> List[List[float]]:
    """Lower-triangular ``L`` with ``a = L Lᵀ``; raises on non-positive-definite."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - math.fsum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                if not (s > 0.0) or not math.isfinite(s):
                    raise ArithmeticError(
                        "공분산 행렬이 양정치(positive definite)가 아닙니다.")
                low[i][i] = math.sqrt(s)
            else:
                low[i][j] = s / low[j][j]
    return low


def _chol_solve(low: Sequence[Sequence[float]],
                b: Sequence[Sequence[float]]) -> List[List[float]]:
    """Solve ``A X = B`` for the ``A = L Lᵀ`` already factorised, B given n×m."""
    n = len(low)
    m = len(b[0]) if b else 0
    y = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for k in range(m):
            y[i][k] = (b[i][k]
                       - math.fsum(low[i][j] * y[j][k] for j in range(i))
                       ) / low[i][i]
    x = [[0.0] * m for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for k in range(m):
            x[i][k] = (y[i][k]
                       - math.fsum(low[j][i] * x[j][k] for j in range(i + 1, n))
                       ) / low[i][i]
    return x


def _inverse_spd(a: Sequence[Sequence[float]]) -> Tuple[List[List[float]], float]:
    """Inverse and log-determinant of a symmetric positive-definite matrix."""
    low = _cholesky(a)
    n = len(a)
    eye = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    inv = _chol_solve(low, eye)
    # symmetrise: round-off otherwise leaks into the EM update and can nudge Σ
    # off the PSD cone after a few hundred sweeps.
    for i in range(n):
        for j in range(i + 1, n):
            v = 0.5 * (inv[i][j] + inv[j][i])
            inv[i][j] = inv[j][i] = v
    logdet = 2.0 * math.fsum(math.log(low[i][i]) for i in range(n))
    return inv, logdet


# ------------------------------------------------------------------ data model
@dataclass
class _Subject:
    obs: Tuple[int, ...]                       # modelled visit indices observed
    y: List[float]                             # response at those visits
    rows: List[List[Tuple[int, float]]]        # sparse design row per observation
    group: str


@dataclass
class MMRMLsMean:
    group: str
    time: str
    n: int
    estimate: float
    se: float
    df: float
    ci_low: float
    ci_high: float


@dataclass
class MMRMContrast:
    time: str
    label: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    estimate: float
    se: float
    df: float
    t: float
    p_raw: float
    p_adj: float
    ci_low: float
    ci_high: float
    primary: bool = False


@dataclass
class MMRMResult:
    response: str                  # what the model was fitted to (Korean label)
    baseline: str
    times: List[str]               # visits carried in the model
    grouped: bool
    adjusted: bool                 # baseline used as a covariate
    n_subjects: int
    n_obs: int
    n_dropped: int                 # subjects the model could not use
    converged: bool
    iterations: int
    loglik: float                  # restricted log-likelihood
    n_cov_params: int
    sds: List[float]
    corr: List[List[float]]
    cov: List[List[float]]
    lsmeans: List[MMRMLsMean] = field(default_factory=list)
    contrasts: List[MMRMContrast] = field(default_factory=list)
    df_method: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def aic(self) -> float:
        """REML AIC — comparable only across models with identical fixed effects."""
        return -2.0 * self.loglik + 2.0 * self.n_cov_params


# ----------------------------------------------------------------------- fitting
def _fit_reml(subjects: Sequence[_Subject], n_cols: int, n_times: int,
              max_iter: int, tol: float
              ) -> Tuple[List[float], List[List[float]], List[List[float]],
                         float, int, bool, List[float]]:
    """REML fit by EM.

    Returns ``(beta, cov_beta, sigma, loglik, iterations, converged, history)``
    where *history* is the restricted log-likelihood of every sweep (the tests
    use it to check monotonicity).
    """
    n_sub = len(subjects)

    # Start from the per-visit residual variance with zero correlation.  A
    # correlation guess only changes how many sweeps EM needs, never the answer.
    sums: List[float] = [0.0] * n_times
    sqs: List[float] = [0.0] * n_times
    cnt: List[int] = [0] * n_times
    for s in subjects:
        for pos, j in enumerate(s.obs):
            sums[j] += s.y[pos]
            sqs[j] += s.y[pos] ** 2
            cnt[j] += 1
    sigma = [[0.0] * n_times for _ in range(n_times)]
    scale = 0.0
    for j in range(n_times):
        # cnt[j] >= 2 always holds here: _identifiable() has already required
        # two subjects at every *pair* of modelled visits.
        var = (max(sqs[j] / cnt[j] - (sums[j] / cnt[j]) ** 2, 0.0)
               if cnt[j] >= 2 else 0.0)                  # pragma: no branch
        scale = max(scale, var)
    if scale <= 0.0:
        raise ArithmeticError("모든 시점의 분산이 0입니다 — MMRM을 적합할 수 없습니다.")
    for j in range(n_times):
        var = (max(sqs[j] / cnt[j] - (sums[j] / cnt[j]) ** 2, 0.0)
               if cnt[j] >= 2 else 0.0)
        sigma[j][j] = var if var > scale * 1e-8 else scale * 1e-3

    beta = [0.0] * n_cols
    cov_beta: List[List[float]] = [[0.0] * n_cols for _ in range(n_cols)]
    history: List[float] = []
    converged = False
    it = 0

    def _sweep(current: List[List[float]]):
        """Pattern caches, the GLS solve and the restricted log-likelihood at Σ.

        Kept as one function so that β, cov(β) and the log-likelihood are always
        read off the *same* Σ — reporting a covariance matrix from one EM sweep
        next to standard errors from another would make the printed numbers
        mutually irreproducible, which matters most in exactly the case where the
        iteration limit was hit and the user is being asked to check them.
        """
        # Every subject with the same observed visits shares Σ_oo⁻¹, its
        # log-determinant and the conditional-covariance block.
        cache: Dict[Tuple[int, ...], Tuple[List[List[float]], float,
                                           List[List[float]],
                                           List[List[float]]]] = {}
        for s in subjects:
            if s.obs in cache:
                continue
            obs = s.obs
            soo = [[current[a][b] for b in obs] for a in obs]
            inv, logdet = _inverse_spd(soo)
            miss = [j for j in range(n_times) if j not in obs]
            # B = Σ_mo Σ_oo⁻¹ maps the observed residual onto the missing part.
            bmat = [[math.fsum(current[m][obs[k]] * inv[k][c]
                               for k in range(len(obs)))
                     for c in range(len(obs))] for m in miss]
            rblock = [[current[m1][m2]
                       - math.fsum(bmat[r1][c] * current[obs[c]][m2]
                                   for c in range(len(obs)))
                       for m2 in miss] for r1, m1 in enumerate(miss)]
            cache[obs] = (inv, logdet, bmat, rblock)

        xtx = [[0.0] * n_cols for _ in range(n_cols)]
        xty = [0.0] * n_cols
        for s in subjects:
            inv = cache[s.obs][0]
            k = len(s.obs)
            for r in range(k):
                row_r = s.rows[r]
                tv = math.fsum(inv[r][c] * s.y[c] for c in range(k))
                for col_a, val_a in row_r:
                    xty[col_a] += val_a * tv
                for c in range(k):
                    w = inv[r][c]
                    if w == 0.0:
                        continue
                    for col_a, val_a in row_r:
                        wa = w * val_a
                        for col_b, val_b in s.rows[c]:
                            xtx[col_a][col_b] += wa * val_b
        cov, logdet_xtx = _inverse_spd(xtx)
        b = [math.fsum(cov[a][c] * xty[c] for c in range(n_cols))
             for a in range(n_cols)]

        quad = 0.0
        logdet_v = 0.0
        n_obs_total = 0
        for s in subjects:
            inv, logdet, _, _ = cache[s.obs]
            k = len(s.obs)
            resid = [s.y[r] - math.fsum(v * b[c] for c, v in s.rows[r])
                     for r in range(k)]
            quad += math.fsum(resid[a] * inv[a][b2] * resid[b2]
                              for a in range(k) for b2 in range(k))
            logdet_v += logdet
            n_obs_total += k
        ll = -0.5 * (logdet_v + quad + logdet_xtx
                     + (n_obs_total - n_cols) * math.log(2.0 * math.pi))
        return cache, b, cov, ll

    for it in range(1, max_iter + 1):
        cache, beta, cov_beta, loglik = _sweep(sigma)
        history.append(loglik)

        # -- E-step: expected complete-data residual cross-product ---------
        acc = [[0.0] * n_times for _ in range(n_times)]
        for s in subjects:
            inv, _, bmat, rblock = cache[s.obs]
            obs = s.obs
            k = len(obs)
            miss = [j for j in range(n_times) if j not in obs]
            resid = [s.y[r] - math.fsum(v * beta[c] for c, v in s.rows[r])
                     for r in range(k)]
            # W = ê êᵀ + X A Xᵀ  (the second term is the REML part)
            wmat = [[resid[a] * resid[b] for b in range(k)] for a in range(k)]
            for a in range(k):
                for b in range(a, k):
                    extra = math.fsum(
                        va * vb * cov_beta[ca][cb]
                        for ca, va in s.rows[a] for cb, vb in s.rows[b])
                    wmat[a][b] += extra
                    if b != a:
                        wmat[b][a] += extra
            # Expand W to the full T×T grid through M = [I ; B].
            mrow: List[List[float]] = [[0.0] * k for _ in range(n_times)]
            for pos, j in enumerate(obs):
                mrow[j][pos] = 1.0
            for pos, j in enumerate(miss):
                mrow[j] = list(bmat[pos])
            tmp = [[math.fsum(mrow[i][a] * wmat[a][b] for a in range(k))
                    for b in range(k)] for i in range(n_times)]
            for i in range(n_times):
                for j in range(i, n_times):
                    v = math.fsum(tmp[i][b] * mrow[j][b] for b in range(k))
                    acc[i][j] += v
                    if j != i:
                        acc[j][i] += v
            for a, m1 in enumerate(miss):
                for b, m2 in enumerate(miss):
                    acc[m1][m2] += rblock[a][b]

        new_sigma = [[acc[i][j] / n_sub for j in range(n_times)]
                     for i in range(n_times)]
        for i in range(n_times):
            for j in range(i + 1, n_times):
                v = 0.5 * (new_sigma[i][j] + new_sigma[j][i])
                new_sigma[i][j] = new_sigma[j][i] = v
        delta = max(abs(new_sigma[i][j] - sigma[i][j])
                    for i in range(n_times) for j in range(n_times))
        sigma = new_sigma
        # Relative to the data's own variance, never absolute.  A `max(scale, 1)`
        # floor here silently turns the test absolute for small-magnitude
        # outcomes — proportions, absorbances, mmol/L — and EM then "converges"
        # after one sweep with Σ still at its diagonal starting value.
        if delta <= tol * scale:
            converged = True
            break

    # One last sweep so β, cov(β) and the log-likelihood all belong to the Σ
    # that is actually returned — on convergence this is a no-op to within tol,
    # and when the iteration limit was hit it is the difference between a
    # reportable fit and three half-steps stitched together.
    _cache, beta, cov_beta, loglik = _sweep(sigma)
    history.append(loglik)
    return beta, cov_beta, sigma, loglik, it, converged, history


# ------------------------------------------------------------------ orchestration
def _build(panel: Panel, baseline: int, adjusted: bool
           ) -> Tuple[List[_Subject], List[int], List[str], Dict[Tuple[int, str], int],
                      Dict[int, int], int, int]:
    """Assemble the sparse design.

    Returns ``(subjects, visit_indices, groups, cell_col, base_col, n_cols,
    n_dropped)`` where *visit_indices* are positions in ``panel.times``.
    """
    labels = panel.group_labels() or [""]
    if adjusted:
        visits = [j for j in range(panel.n_times) if j != baseline]
    else:
        visits = list(range(panel.n_times))

    # Which (visit, arm) cells actually have data?  A cell with nobody in it is
    # an all-zero column and would make XᵀV⁻¹X singular.
    present: Dict[Tuple[int, str], int] = {}
    for i in range(panel.n_subjects):
        lab = (panel.groups or [""] * panel.n_subjects)[i]
        if adjusted and panel.values[i][baseline] is None:
            continue
        for pos, j in enumerate(visits):
            if panel.values[i][j] is not None:
                present[(pos, lab)] = present.get((pos, lab), 0) + 1

    cell_col: Dict[Tuple[int, str], int] = {}
    n_cols = 0
    for pos in range(len(visits)):
        for lab in labels:
            if present.get((pos, lab), 0) > 0:
                cell_col[(pos, lab)] = n_cols
                n_cols += 1

    base_col: Dict[int, int] = {}
    base_mean = 0.0
    if adjusted:
        bvals = [float(panel.values[i][baseline])
                 for i in range(panel.n_subjects)
                 if panel.values[i][baseline] is not None
                 and any(panel.values[i][j] is not None for j in visits)]
        base_mean = math.fsum(bvals) / len(bvals) if bvals else 0.0
        for pos, j in enumerate(visits):
            seen = {float(panel.values[i][baseline])
                    for i in range(panel.n_subjects)
                    if panel.values[i][baseline] is not None
                    and panel.values[i][j] is not None}
            # A baseline column needs at least two distinct values at that visit,
            # otherwise it is collinear with the cell indicators.
            if len(seen) >= 2:
                base_col[pos] = n_cols
                n_cols += 1

    subjects: List[_Subject] = []
    dropped = [0, 0]          # [no baseline value, no usable visit]
    for i in range(panel.n_subjects):
        lab = (panel.groups or [""] * panel.n_subjects)[i]
        base = panel.values[i][baseline]
        if adjusted and base is None:
            dropped[0] += 1
            continue
        obs: List[int] = []
        y: List[float] = []
        rows: List[List[Tuple[int, float]]] = []
        for pos, j in enumerate(visits):
            v = panel.values[i][j]
            if v is None:
                continue
            col = cell_col.get((pos, lab))
            if col is None:                       # pragma: no cover - defensive
                continue
            entries = [(col, 1.0)]
            if adjusted:
                y.append(float(v) - float(base))
                if pos in base_col:
                    entries.append((base_col[pos], float(base) - base_mean))
            else:
                y.append(float(v))
            obs.append(pos)
            rows.append(entries)
        if not obs:
            dropped[1] += 1
            continue
        subjects.append(_Subject(tuple(obs), y, rows, lab))
    return subjects, visits, labels, cell_col, base_col, n_cols, tuple(dropped)


def _identifiable(subjects: Sequence[_Subject],
                  names: Sequence[str]) -> Optional[str]:
    """Every off-diagonal of an unstructured Σ needs subjects seen at both visits."""
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            both = sum(1 for s in subjects if a in s.obs and b in s.obs)
            if both < 2:
                return (f"'{names[a]}'와 '{names[b]}'를 모두 관측한 대상이 "
                        f"{both}명뿐이라 비구조화 공분산을 추정할 수 없습니다.")
    return None


def mmrm_analysis(panel: Panel, baseline: int = 0, alpha: float = 0.05,
                  correction: str = "holm",
                  primary_time: Optional[str] = None,
                  max_iter: int = 400, tol: float = 1e-9,
                  skipped: Optional[List[str]] = None
                  ) -> Optional[MMRMResult]:
    """Fit an MMRM to *panel* and return LS-means plus per-visit contrasts.

    Returns ``None`` when the design is too thin to support an unstructured
    covariance — the reason is appended to *skipped* so the caller can tell the
    user *why* rather than silently omitting a section.  Raises
    :class:`ArithmeticError` when the fit itself fails.
    """
    def _skip(reason: str) -> None:
        if skipped is not None:
            skipped.append(reason)

    if panel.n_times < 2:
        _skip("시점이 2개 미만입니다.")
        return None
    if not 0 <= baseline < panel.n_times:
        raise ValueError("기준 시점 색인이 범위를 벗어났습니다.")
    grouped = panel.groups is not None and len(panel.group_labels()) > 1
    adjusted = grouped
    notes: List[str] = []

    if panel.n_times > MAX_TIMES:
        _skip(f"시점이 {panel.n_times}개로 {MAX_TIMES}개를 넘어 비구조화 공분산 "
              f"({panel.n_times * (panel.n_times + 1) // 2}개 모수)을 추정하지 "
              "않습니다.")
        return None
    work = panel.n_subjects * panel.n_times ** 2
    if work > MAX_WORK:
        _skip(f"대상 {panel.n_subjects}명 × 시점 {panel.n_times}개는 순수 "
              f"파이썬 EM으로는 너무 큽니다 (계산량 {work:,} > 한도 "
              f"{MAX_WORK:,} = 대상 수 × 시점 수²). 이 규모라면 "
              "R mmrm·SAS PROC MIXED 를 쓰세요.")
        return None

    subjects, visits, labels, cell_col, base_col, n_cols, dropped = _build(
        panel, baseline, adjusted)
    n_model_times = len(visits)
    if n_model_times < 1 or n_cols == 0 or not subjects:
        _skip("모형에 넣을 수 있는 관측이 없습니다.")
        return None
    n_obs = sum(len(s.obs) for s in subjects)
    if n_obs <= n_cols:
        _skip(f"관측 수({n_obs})가 평균 모수 수({n_cols})보다 많지 않습니다.")
        return None
    # Σ has T(T+1)/2 free parameters.  With fewer subjects than that the fit is
    # not merely imprecise, it is under-identified: EM wanders for 400 sweeps or
    # the Cholesky fails outright and the user gets a linear-algebra message
    # instead of an explanation.  Refuse, and say the two numbers involved.
    n_cov_params = n_model_times * (n_model_times + 1) // 2
    if len(subjects) < max(n_cov_params, n_model_times + 2):
        _skip(f"모형에 쓸 대상이 {len(subjects)}명인데 "
              f"{n_model_times}×{n_model_times} 비구조화 공분산은 모수가 "
              f"{n_cov_params}개입니다 — 대상 수가 모수 수보다 적어 추정할 수 "
              "없습니다 (시점을 줄이거나 --no-mmrm).")
        return None
    unident = _identifiable(subjects, [panel.times[j] for j in visits])
    if unident:
        _skip(unident)
        return None

    try:
        beta, cov_beta, sigma, loglik, iterations, converged, _hist = _fit_reml(
            subjects, n_cols, n_model_times, max_iter, tol)
    except ArithmeticError as exc:
        raise ArithmeticError(
            f"{exc} — 대상 {len(subjects)}명으로 비구조화 공분산 모수 "
            f"{n_cov_params}개를 추정하는 중 발생했습니다. 시점을 줄이거나 "
            "--no-mmrm 으로 이 구획을 끄세요.") from None
    if not converged:
        notes.append(
            f"EM 반복 {iterations}회 안에 수렴하지 않았습니다 — 추정치를 "
            "그대로 신뢰하지 마세요 (결측이 매우 많거나 시점 수가 많을 때 발생).")

    # A visit whose residual variance has collapsed (constant outcome, or a
    # baseline covariate that predicts it exactly) survives the Cholesky but
    # yields SE 0.00 and a zero-width "95% CI".  Mark it and blank those rows
    # rather than printing false precision.
    var_scale = max(sigma[j][j] for j in range(n_model_times))
    # 1e-8 of the largest visit variance: a real ceiling effect is nowhere near
    # this ratio, so anything below it is arithmetic, not measurement.
    degenerate = {j for j in range(n_model_times)
                  if not sigma[j][j] > var_scale * 1e-8}
    if degenerate:
        notes.append(
            "잔차분산이 사실상 0인 시점이 있습니다 ("
            + ", ".join(panel.times[visits[j]] for j in sorted(degenerate))
            + ") — 그 시점의 값이 모두 같거나 기저값으로 완전히 설명됩니다. "
            "표준오차·신뢰구간을 표시하지 않습니다.")
    sds = [math.sqrt(sigma[j][j]) if sigma[j][j] > 0 else float("nan")
           for j in range(n_model_times)]
    corr = [[(sigma[i][j] / (sds[i] * sds[j])
              if sds[i] > 0 and sds[j] > 0 else float("nan"))
             for j in range(n_model_times)] for i in range(n_model_times)]

    # counts per (visit, arm) among the subjects the model actually used
    counts: Dict[Tuple[int, str], int] = {}
    for s in subjects:
        for pos in s.obs:
            counts[(pos, s.group)] = counts.get((pos, s.group), 0) + 1

    def _df(pos: int, arms: Sequence[str]) -> float:
        n_here = sum(counts.get((pos, lab), 0) for lab in arms)
        q = sum(1 for lab in arms if (pos, lab) in cell_col)
        q += 1 if pos in base_col else 0
        return float(max(n_here - q, 1))

    lsmeans: List[MMRMLsMean] = []
    for pos, j in enumerate(visits):
        # Σ is pooled across arms, so an LS-mean's t-quantile rides on the same
        # residual df as the between-arm contrast at that visit.  Using only the
        # arm's own n here made every within-arm interval visibly too wide.
        arms_here = [lab for lab in labels if (pos, lab) in cell_col]
        for lab in labels:
            col = cell_col.get((pos, lab))
            if col is None:
                continue
            est = beta[col]
            var = cov_beta[col][col]
            se = (math.sqrt(var) if var > 0 and pos not in degenerate
                  else float("nan"))
            df = _df(pos, arms_here)
            if math.isfinite(se):
                crit = t_ppf(1.0 - alpha / 2.0, df)
                lo, hi = est - crit * se, est + crit * se
            else:
                lo = hi = float("nan")
            lsmeans.append(MMRMLsMean(
                group=lab, time=panel.times[j], n=counts.get((pos, lab), 0),
                estimate=est, se=se, df=df, ci_low=lo, ci_high=hi))

    contrasts: List[MMRMContrast] = []
    if grouped:
        for pos, j in enumerate(visits):
            arms = [lab for lab in labels if (pos, lab) in cell_col]
            for a in range(len(arms)):
                for b in range(a + 1, len(arms)):
                    ca, cb = cell_col[(pos, arms[a])], cell_col[(pos, arms[b])]
                    est = beta[ca] - beta[cb]
                    var = (float("nan") if pos in degenerate else
                           cov_beta[ca][ca] + cov_beta[cb][cb]
                           - 2 * cov_beta[ca][cb])
                    contrasts.append(_contrast(
                        panel.times[j], f"{arms[a]} − {arms[b]}", arms[a], arms[b],
                        counts.get((pos, arms[a]), 0), counts.get((pos, arms[b]), 0),
                        est, var, _df(pos, arms), alpha,
                        primary=(panel.times[j] == primary_time)))
    else:
        lab = labels[0]
        base_pos = visits.index(baseline)
        cbase = cell_col.get((base_pos, lab))
        for pos, j in enumerate(visits):
            if pos == base_pos or cbase is None:
                continue
            col = cell_col.get((pos, lab))
            if col is None:
                continue
            est = beta[col] - beta[cbase]
            var = (float("nan")
                   if pos in degenerate or base_pos in degenerate else
                   cov_beta[col][col] + cov_beta[cbase][cbase]
                   - 2 * cov_beta[col][cbase])
            # Both visits must be observed for a subject to inform this
            # within-subject contrast, so that — not the visit's own headcount —
            # is the n that belongs next to the df.
            n_here = sum(1 for s in subjects if pos in s.obs and base_pos in s.obs)
            contrasts.append(_contrast(
                panel.times[j], f"{panel.times[j]} − {panel.times[baseline]}",
                lab, lab, n_here, n_here, est, var,
                float(max(n_here - 1, 1)), alpha,
                primary=(panel.times[j] == primary_time)))

    # Two families, each adjusted on its own.  Exempting the primary visit
    # entirely would hand every pairwise contrast at that visit an unadjusted p
    # as soon as there are three arms — with one contrast (the usual two-arm
    # case) Holm/BH on a family of one is the identity, so nothing changes.
    for family in ([c for c in contrasts if c.primary],
                   [c for c in contrasts if not c.primary]):
        for row, padj in zip(family,
                             adjust([c.p_raw for c in family], correction)):
            row.p_adj = padj

    if dropped[0]:
        notes.append(f"{dropped[0]}명은 기준시점({panel.times[baseline]}) 값이 없어 "
                     "기저 보정 모형에 들어가지 못했습니다.")
    if dropped[1]:
        notes.append(f"{dropped[1]}명은 모형에 쓸 관측 시점이 하나도 없어 "
                     "제외되었습니다"
                     + (" (기준시점만 측정하고 탈락)." if adjusted else "."))
    used = len(subjects)
    complete = len(panel.complete_rows())
    if used > complete:
        notes.append(
            f"완전사례 분석([4]·[5])이 쓰는 {complete}명보다 {used - complete}명 "
            "많은 자료를 사용했습니다 — 이것이 MMRM을 쓰는 이유입니다.")

    return MMRMResult(
        response=("기저 대비 변화량 (기저값 공변량 보정)" if adjusted
                  else "관측값 (시점별 평균)"),
        baseline=panel.times[baseline],
        times=[panel.times[j] for j in visits],
        grouped=grouped, adjusted=adjusted, n_subjects=used, n_obs=n_obs,
        n_dropped=dropped[0] + dropped[1], converged=converged,
        iterations=iterations,
        loglik=loglik, n_cov_params=n_cov_params, sds=sds, corr=corr,
        cov=[list(row) for row in sigma], lsmeans=lsmeans, contrasts=contrasts,
        df_method=(
            "시점별 관측 수 − 그 시점의 평균 모수 수" if grouped else
            "LS 평균은 그 시점 관측 수 − 1, 대비는 두 시점을 모두 관측한 "
            "대상 수 − 1") + " (완전자료에서는 정확, 결측이 있으면 근사 · "
            "Kenward–Roger 아님)",
        notes=notes)


def _contrast(time: str, label: str, ga: str, gb: str, na: int, nb: int,
              est: float, var: float, df: float, alpha: float,
              primary: bool) -> MMRMContrast:
    se = math.sqrt(var) if var > 0 and math.isfinite(var) else float("nan")
    if math.isfinite(se) and se > 0:
        t = est / se
        p = t_sf_two_sided(t, df)
        crit = t_ppf(1.0 - alpha / 2.0, df)
        lo, hi = est - crit * se, est + crit * se
    else:
        t = p = lo = hi = float("nan")
    return MMRMContrast(
        time=time, label=label, group_a=ga, group_b=gb, n_a=na, n_b=nb,
        estimate=est, se=se, df=df, t=t, p_raw=p, p_adj=float("nan"),
        ci_low=lo, ci_high=hi, primary=primary)
