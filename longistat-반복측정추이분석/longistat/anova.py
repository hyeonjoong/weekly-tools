"""Repeated-measures and mixed (split-plot) ANOVA — pure standard library.

Everything is computed from **orthonormal within-subject contrast scores**
rather than from the textbook sums-of-squares recipe.  Writing

    u_i  = (1/√k) Σ_j x_ij                (the subject's "level")
    y_ic = Σ_j C_jc x_ij                  (k−1 Helmert contrasts, C ⟂ 1)

turns the design into an ordinary *between-subjects* problem on ``u`` (group
main effect) and on ``y`` (time, group × time), because ``[1/√k , C]`` is an
orthonormal basis of R^k.  Three things fall out of that:

* for one group it reproduces the classical one-way RM-ANOVA identities
  exactly (SS_time = n Σ_j (m_j − G)², SS_error = Σ_i Σ_j (x_ij − m_j − x̄_i + G)²);
* with **unequal group sizes** the group and group × time effects stay
  unambiguous (a one-way between-subjects contrast has one SS, Type I = III),
  and the time main effect is computed as **Type III** — the hypothesis about
  the *unweighted* mean of the group means, which is what SPSS GLM reports;
* Mauchly's W and Greenhouse–Geisser ε read straight off ``T = CᵀSC``, with
  ε = (tr T)² / (d · tr T²) — no eigen-decomposition needed.

Complete cases only: a subject missing any timepoint cannot contribute a
contrast score, so :func:`rm_anova` expects an already complete-case matrix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .special import chi2_sf, f_sf

__all__ = [
    "Effect",
    "Sphericity",
    "RMAnovaResult",
    "helmert",
    "contrast_scores",
    "rm_anova",
]


# --------------------------------------------------------------------------
# contrasts
# --------------------------------------------------------------------------

def helmert(k: int) -> List[List[float]]:
    """Normalised Helmert contrasts: a ``k × (k−1)`` orthonormal matrix ⟂ **1**.

    Column ``c`` (1-based) contrasts the mean of the first ``c`` timepoints with
    timepoint ``c``, scaled to unit length.
    """
    if k < 2:
        raise ValueError("시점이 2개 이상이어야 합니다.")
    cols: List[List[float]] = []
    for c in range(1, k):
        scale = 1.0 / math.sqrt(c * (c + 1.0))
        col = [scale] * c + [-c * scale] + [0.0] * (k - c - 1)
        cols.append(col)
    # transpose to k × (k-1)
    return [[cols[c][j] for c in range(k - 1)] for j in range(k)]


def contrast_scores(matrix: Sequence[Sequence[float]]
                    ) -> Tuple[List[float], List[List[float]]]:
    """Return ``(u, y)`` — subject levels and ``k−1`` contrast scores per subject.

    The matrix is shifted by its grand mean first.  Every sum of squares is
    invariant to a constant offset, but the *products* ``x_ij · C_jc`` are not:
    on data whose offset dwarfs its signal (epoch timestamps ~1.7e9, KRW costs)
    they round at the offset's magnitude and the F ratio lost 8 significant
    digits at 1e9 and 2 % at 1e15.  One subtraction removes the whole problem.
    """
    k = len(matrix[0])
    if any(len(row) != k for row in matrix):
        raise ValueError("모든 대상의 시점 개수가 같아야 합니다.")
    cmat = helmert(k)
    inv_sqrt_k = 1.0 / math.sqrt(k)
    n_cells = len(matrix) * k
    shift = math.fsum(math.fsum(row) for row in matrix) / n_cells if n_cells else 0.0
    u: List[float] = []
    y: List[List[float]] = []
    for row in matrix:
        centred = [v - shift for v in row]
        u.append(inv_sqrt_k * math.fsum(centred))
        y.append([math.fsum(centred[j] * cmat[j][c] for j in range(k))
                  for c in range(k - 1)])
    return u, y


# --------------------------------------------------------------------------
# small linear algebra
# --------------------------------------------------------------------------

def _log_abs_det(mat: Sequence[Sequence[float]]) -> Optional[float]:
    """``log|det|`` by Gaussian elimination, or ``None`` if singular/negative.

    Mauchly's W is a ratio of two quantities that each scale as ``s^(2d)``, so
    forming them separately overflowed for a 31-day diary of KRW costs
    (``OverflowError`` escaping as a raw traceback) and underflowed to a false
    "singular covariance" message for micro-scale units.  Accumulating logs
    keeps W in range at every scale.
    """
    n = len(mat)
    a = [list(map(float, row)) for row in mat]
    log_det = 0.0
    negative = False
    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[pivot][i]) < 1e-300:
            return None
        if pivot != i:
            a[i], a[pivot] = a[pivot], a[i]
            negative = not negative
        piv = a[i][i]
        if piv < 0:
            negative = not negative
        log_det += math.log(abs(piv))
        inv = 1.0 / piv
        for r in range(i + 1, n):
            factor = a[r][i] * inv
            if factor:
                for c in range(i, n):
                    a[r][c] -= factor * a[i][c]
    # T is a covariance matrix; a negative determinant means it is numerically
    # indefinite, and W would not be a probability.
    return None if negative else log_det


def _pooled_cov(matrix: Sequence[Sequence[float]],
                groups: Optional[Sequence[str]]) -> Tuple[List[List[float]], int]:
    """Within-group pooled covariance matrix and its degrees of freedom."""
    n, k = len(matrix), len(matrix[0])
    labels = list(groups) if groups is not None else [""] * n
    distinct = list(dict.fromkeys(labels))
    cov = [[0.0] * k for _ in range(k)]
    df = n - len(distinct)
    for lab in distinct:
        idx = [i for i, g in enumerate(labels) if g == lab]
        means = [math.fsum(matrix[i][j] for i in idx) / len(idx) for j in range(k)]
        for i in idx:
            dev = [matrix[i][j] - means[j] for j in range(k)]
            for a in range(k):
                da = dev[a]
                if da:
                    for b in range(a, k):
                        cov[a][b] += da * dev[b]
    for a in range(k):
        for b in range(a, k):
            v = cov[a][b] / df if df > 0 else float("nan")
            cov[a][b] = v
            cov[b][a] = v
    return cov, df


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class Effect:
    """One ANOVA line."""

    name: str
    ss: float
    df1: float
    df2: float
    ms: float
    f: float
    p: float
    partial_eta2: float
    generalized_eta2: float
    within: bool                    # subject to the sphericity correction?
    p_gg: Optional[float] = None
    p_hf: Optional[float] = None
    df1_gg: Optional[float] = None
    df2_gg: Optional[float] = None
    df1_hf: Optional[float] = None
    df2_hf: Optional[float] = None

    def p_reported(self, correction: str) -> float:
        """p-value under the requested sphericity correction."""
        if not self.within or correction == "none":
            return self.p
        if correction == "gg" and self.p_gg is not None:
            return self.p_gg
        if correction == "hf" and self.p_hf is not None:
            return self.p_hf
        return self.p

    def df_reported(self, correction: str) -> Tuple[float, float]:
        """The degrees of freedom that go with :meth:`p_reported`.

        Reporting an ε-corrected p next to uncorrected df makes the F triple
        irreproducible for a reader, which is exactly what a methods reviewer
        recomputes.
        """
        if self.within and correction == "gg" and self.df1_gg is not None:
            return self.df1_gg, self.df2_gg          # type: ignore[return-value]
        if self.within and correction == "hf" and self.df1_hf is not None:
            return self.df1_hf, self.df2_hf          # type: ignore[return-value]
        return self.df1, self.df2


@dataclass
class Sphericity:
    """Mauchly's test and the two ε corrections — availability tracked separately.

    ``epsilon_ok`` and ``mauchly_ok`` are *not* the same question.  When the
    contrast covariance is singular, Mauchly's W does not exist but ε̂ does —
    and it sits at its lower bound 1/d, i.e. the most extreme violation
    possible.  Conflating the two used to drop the correction entirely in
    exactly that case, so the worst-behaved data got the most liberal p-value.
    """

    epsilon_ok: bool
    mauchly_ok: bool = False
    reason: str = ""
    w: Optional[float] = None
    chi2: Optional[float] = None
    df: Optional[float] = None
    p: Optional[float] = None
    eps_gg: Optional[float] = None
    eps_hf: Optional[float] = None
    eps_lb: Optional[float] = None
    unreliable: bool = False        # χ² approximation stretched too thin?

    def violated(self, alpha: float = 0.05) -> bool:
        return bool(self.mauchly_ok and self.p is not None and self.p < alpha)

    def recommended(self, alpha: float = 0.05) -> str:
        """Which correction to report: ``none``, ``gg`` or ``hf``.

        Follows the usual convention (Girden 1992): sphericity not rejected →
        uncorrected; ε̂_GG ≥ 0.75 → Huynh–Feldt (less conservative); otherwise
        Greenhouse–Geisser.  When ε̂ exists but Mauchly's test does not, correct
        anyway — an unestimable W means the covariance is degenerate, not
        spherical.
        """
        if not self.epsilon_ok or self.eps_gg is None:
            return "none"
        if self.mauchly_ok and self.p is not None and self.p >= alpha:
            return "none"
        return "hf" if self.eps_gg >= 0.75 else "gg"


@dataclass
class RMAnovaResult:
    n_subjects: int
    n_times: int
    times: List[str]
    groups: List[str]
    group_sizes: List[int]
    effects: List[Effect]
    sphericity: Sphericity
    ss_error_between: float
    df_error_between: float
    ss_error_within: float
    df_error_within: float
    ss_total: float
    notes: List[str] = field(default_factory=list)

    def effect(self, name: str) -> Optional[Effect]:
        for e in self.effects:
            if e.name == name:
                return e
        return None


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

def rm_anova(matrix: Sequence[Sequence[float]],
             times: Sequence[str],
             groups: Optional[Sequence[str]] = None,
             group_order: Optional[Sequence[str]] = None) -> RMAnovaResult:
    """One-way repeated-measures ANOVA, or a mixed ANOVA when *groups* is given.

    ``matrix`` must be complete (subjects × timepoints).  Returns every effect
    with uncorrected, Greenhouse–Geisser and Huynh–Feldt p-values plus partial
    and generalized η².
    """
    n = len(matrix)
    if n == 0:
        raise ValueError("완전한 자료를 가진 대상이 없습니다.")
    k = len(matrix[0])
    if k < 2:
        raise ValueError("시점이 2개 이상이어야 합니다.")
    if len(times) != k:
        raise ValueError("시점 이름 개수가 자료의 열 개수와 다릅니다.")
    d = k - 1

    labels = list(groups) if groups is not None else [""] * n
    if len(labels) != n:
        raise ValueError("그룹 라벨 개수가 대상 수와 다릅니다.")
    order = list(group_order) if group_order else list(dict.fromkeys(labels))
    idx_by_group = {gl: [i for i, g in enumerate(labels) if g == gl] for gl in order}
    sizes = [len(idx_by_group[gl]) for gl in order]
    if any(s == 0 for s in sizes):
        raise ValueError("표본이 없는 그룹이 있습니다.")
    g = len(order)
    notes: List[str] = []
    if n - g < 1:
        raise ValueError(
            f"그룹당 최소 2명이 필요합니다 (현재 N={n}, 그룹 {g}개).")

    u, ys = contrast_scores(matrix)

    # ---- between-subject partition (on u) -------------------------------
    u_grand = math.fsum(u) / n
    ss_between_total = math.fsum((v - u_grand) ** 2 for v in u)
    ss_group = 0.0
    ss_err_b = 0.0
    for gl, size in zip(order, sizes):
        idx = idx_by_group[gl]
        gm = math.fsum(u[i] for i in idx) / size
        ss_group += size * (gm - u_grand) ** 2
        ss_err_b += math.fsum((u[i] - gm) ** 2 for i in idx)
    if g == 1:
        ss_group = 0.0
        ss_err_b = ss_between_total
    df_err_b = float(n - g)

    # ---- within-subject partition (on contrast scores) ------------------
    ss_time = 0.0
    ss_gt = 0.0
    ss_err_w = 0.0
    weight = math.fsum(1.0 / s for s in sizes) / (g * g)   # Var(μ̂)/σ²
    for c in range(d):
        col = [row[c] for row in ys]
        grand_w = math.fsum(col) / n                       # size-weighted mean
        gmeans = []
        for gl, size in zip(order, sizes):
            idx = idx_by_group[gl]
            gm = math.fsum(col[i] for i in idx) / size
            gmeans.append(gm)
            ss_gt += size * (gm - grand_w) ** 2
            ss_err_w += math.fsum((col[i] - gm) ** 2 for i in idx)
        mu_unweighted = math.fsum(gmeans) / g              # Type III estimate
        ss_time += mu_unweighted * mu_unweighted / weight
    if g == 1:
        ss_gt = 0.0
    df_err_w = float((n - g) * d)

    # In the orthonormal basis [1/√k, C] the total SS about the grand mean is
    # Σ_i (u_i − ū)² + Σ_i Σ_c y_ic² — the contrast scores enter as raw squares
    # because their own mean *is* the time effect.
    ss_total = ss_between_total + math.fsum(
        math.fsum(v * v for v in row) for row in ys)

    # ---- sphericity ------------------------------------------------------
    sph = _sphericity(matrix, labels, order, n, g, d)

    # ---- assemble --------------------------------------------------------
    ms_err_b = ss_err_b / df_err_b if df_err_b > 0 else float("nan")
    ms_err_w = ss_err_w / df_err_w if df_err_w > 0 else float("nan")
    denom_g = ss_err_b + ss_err_w                  # generalized η² denominator

    effects: List[Effect] = []
    if g > 1:
        effects.append(_make_effect(
            "그룹(집단)", ss_group, float(g - 1), df_err_b, ms_err_b,
            denom_g, within=False, sph=sph, scale=ss_total))
    effects.append(_make_effect(
        "시점(시간)", ss_time, float(d), df_err_w, ms_err_w,
        denom_g, within=True, sph=sph, scale=ss_total))
    if g > 1:
        effects.append(_make_effect(
            "그룹 × 시점", ss_gt, float((g - 1) * d), df_err_w, ms_err_w,
            denom_g, within=True, sph=sph, scale=ss_total))

    if df_err_w <= 0:
        notes.append("오차 자유도가 0이어서 검정할 수 없습니다 (대상 수 부족).")
    if sph.unreliable:
        notes.append(
            f"Mauchly 검정의 χ² 근사는 오차 자유도({n - g})가 추정 모수 "
            f"{int(d * (d + 1) / 2)}개에 비해 넉넉할 때만 믿을 수 있습니다 — "
            "이 자료에서는 구형성 기각을 과신하지 마세요.")

    return RMAnovaResult(
        n_subjects=n, n_times=k, times=list(times), groups=order,
        group_sizes=sizes, effects=effects, sphericity=sph,
        ss_error_between=ss_err_b, df_error_between=df_err_b,
        ss_error_within=ss_err_w, df_error_within=df_err_w,
        ss_total=ss_total, notes=notes)


def _make_effect(name: str, ss: float, df1: float, df2: float, ms_err: float,
                 denom_g: float, within: bool, sph: Sphericity,
                 scale: float) -> Effect:
    """Assemble one ANOVA line.

    ``scale`` is the total sum of squares: the residual mean square is only
    treated as real if it is above rounding noise *relative* to it.  An absolute
    ``ms_err > 0`` test let a residue of 1e-30 through on perfectly additive
    data and produced ``F = 3.0e32`` inside a paste-ready APA sentence.
    """
    ms = ss / df1 if df1 > 0 else float("nan")
    ss_err = ms_err * df2 if df2 > 0 else float("nan")
    negligible = not (ss_err > 1e-12 * abs(scale)) if math.isfinite(scale) else True
    if not (ms_err > 0) or not (df2 > 0) or negligible:
        f = p = float("nan")
    else:
        f = ms / ms_err
        p = f_sf(f, df1, df2)
    pe = ss / (ss + ss_err) if ss + ss_err > 0 else float("nan")
    ge = ss / (ss + denom_g) if ss + denom_g > 0 else float("nan")
    eff = Effect(name=name, ss=ss, df1=df1, df2=df2, ms=ms, f=f, p=p,
                 partial_eta2=pe, generalized_eta2=ge, within=within)
    if within and sph.epsilon_ok and math.isfinite(f):
        if sph.eps_gg is not None:
            eff.df1_gg = df1 * sph.eps_gg
            eff.df2_gg = df2 * sph.eps_gg
            eff.p_gg = f_sf(f, eff.df1_gg, eff.df2_gg)
        if sph.eps_hf is not None:
            eff.df1_hf = df1 * sph.eps_hf
            eff.df2_hf = df2 * sph.eps_hf
            eff.p_hf = f_sf(f, eff.df1_hf, eff.df2_hf)
    return eff


def _sphericity(matrix: Sequence[Sequence[float]], labels: Sequence[str],
                order: Sequence[str], n: int, g: int, d: int) -> Sphericity:
    """Greenhouse–Geisser / Huynh–Feldt ε and Mauchly's W from ``T = CᵀSC``."""
    if d < 2:
        return Sphericity(epsilon_ok=False, mauchly_ok=False,
                          reason="시점이 2개면 구형성 가정이 자동으로 성립합니다.")
    nu = n - g
    if nu < d:
        return Sphericity(
            epsilon_ok=False, mauchly_ok=False,
            reason=f"오차 자유도({nu})가 시점−1({d})보다 작아 구형성을 "
                   "추정할 수 없습니다.")
    k = len(matrix[0])
    cov, _ = _pooled_cov(matrix, list(labels))
    cmat = helmert(k)
    # T = Cᵀ S C  (d × d)
    sc = [[math.fsum(cov[a][j] * cmat[j][c] for j in range(k)) for c in range(d)]
          for a in range(k)]
    t = [[math.fsum(cmat[j][r] * sc[j][c] for j in range(k)) for c in range(d)]
         for r in range(d)]

    tr = math.fsum(t[i][i] for i in range(d))
    tr2 = math.fsum(t[a][b] * t[a][b] for a in range(d) for b in range(d))
    if not (tr > 0) or not (tr2 > 0) or not math.isfinite(tr2):
        return Sphericity(epsilon_ok=False, mauchly_ok=False,
                          reason="시점 간 분산이 0이어서 구형성을 계산할 수 없습니다.")

    eps_lb = 1.0 / d
    eps_gg = min(1.0, max(eps_lb, (tr * tr) / (d * tr2)))
    hf_den = d * (n - g - d * eps_gg)
    if hf_den > 0:
        eps_hf = min(1.0, max(eps_lb, (n * d * eps_gg - 2.0) / hf_den))
    else:
        eps_hf = 1.0

    # Mauchly's χ² approximation needs ν ≫ d(d+1)/2; at ν ≈ that many free
    # parameters it rejects sphericity on i.i.d. normal data almost surely.
    unreliable = nu < 3 * (d * (d + 1) / 2.0)

    log_det = _log_abs_det(t)
    if log_det is None:
        return Sphericity(
            epsilon_ok=True, mauchly_ok=False,
            reason="공분산행렬이 특이(singular)하여 Mauchly 검정은 계산할 수 "
                   "없습니다 — ε 보정은 그대로 적용합니다.",
            eps_gg=eps_gg, eps_hf=eps_hf, eps_lb=eps_lb, unreliable=unreliable)

    # log W = log|det T| − d·log(tr T / d), formed in logs so that neither
    # factor overflows for large-variance data (k ≥ ~24 with SD ~1e5).
    log_w = log_det - d * math.log(tr / d)
    log_w = min(0.0, log_w)                      # W ≤ 1 by AM–GM
    w = math.exp(log_w)
    f_corr = 1.0 - (2.0 * d * d + d + 2.0) / (6.0 * d * nu)
    chi2 = -nu * f_corr * log_w
    df_m = d * (d + 1) / 2.0 - 1.0
    # Two-term asymptotic expansion, as in R's stats::mauchly.test.  The
    # first-order form alone is systematically anti-conservative for d >= 3 and
    # small n — exactly the pilot-study regime this tool targets.
    p1 = chi2_sf(chi2, df_m) if chi2 > 0 else 1.0
    if d >= 3 and chi2 > 0:
        p2 = chi2_sf(chi2, df_m + 4.0)
        omega2 = ((d + 2) * (d - 1) * (d - 2) * (2 * d ** 3 + 6 * d * d + 3 * d + 2)
                  / (288.0 * (nu * d * f_corr) ** 2))
        p = p1 + omega2 * (p2 - p1)
    else:
        p = p1
    p = min(1.0, max(0.0, p))
    return Sphericity(epsilon_ok=True, mauchly_ok=True, w=w, chi2=chi2, df=df_m,
                      p=p, eps_gg=eps_gg, eps_hf=eps_hf, eps_lb=eps_lb,
                      unreliable=unreliable)
