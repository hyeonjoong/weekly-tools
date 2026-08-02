"""Subject-level covariates — encoding, centring, and rank repair.

Why this module exists
----------------------
Randomisation balances covariates *in expectation*, and every regulator-facing
analysis plan says the same thing anyway: adjust the primary comparison for the
stratification factors used at randomisation (site, severity stratum) and for
prognostic baseline variables (age, disease duration).  ICH E9 and the EMA
covariate guideline both treat an unadjusted analysis of a stratified trial as
the *less* correct one, because ignoring a stratification factor leaves the
standard error inflated even when the point estimate is fine.

longistat already conditions on the baseline score (see :mod:`ancova` and the
``adjusted`` path in :mod:`mmrm`).  This module supplies the *other* covariates,
in the one form both of those models can consume: a dense, mean-centred,
full-rank block of columns over a stated set of subjects.

Design decisions worth knowing
------------------------------
* **Centring is not cosmetic.**  Both consumers read a model coefficient
  directly as an LS-mean (MMRM cell coefficients) or take differences of arm
  coefficients (ANCOVA).  Centring every covariate column on the mean of the
  subjects actually in the fit makes those coefficients LS-means *at the mean
  covariate value*.  Without it the "LS-mean" would be the value at
  covariate = 0 — meaningless for age.  For a *continuous* covariate that is
  what SAS ``LSMEANS`` prints; for a categorical one it is not, because
  centring a dummy weights the levels by their **observed proportions** while
  SAS's default weights them equally (1/k) — the centred version is SAS
  ``LSMEANS / OM``.  The between-arm *difference*, which is the number this
  tool actually reports, is identical under either weighting.
* **Categoricals get reference coding**, with the first level in data order as
  the reference, so ``k`` levels cost ``k − 1`` columns.
* **Rank repair is done here, once.**  Two site columns that happen to be
  identical, a covariate that is constant among the subjects who survived to
  this visit, a dummy for a level nobody in the fit belongs to — all of these
  make XᵀX singular.  Detecting them where the columns are built (and saying
  which column was dropped and why) beats a linear-algebra error surfacing from
  three call sites.
* **A subject with any missing covariate is not in the fit.**  Silently
  imputing a stratification factor would be worse than dropping the subject;
  the count of dropped subjects is returned so the caller can report it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

__all__ = ["Covariate", "CovariateDesign", "encode_covariates",
           "complete_subjects", "orthonormal_basis", "independent_of",
           "MAX_LEVELS", "MAX_COLUMNS"]

# A categorical covariate with more levels than this is almost always a mistake
# (a subject ID, a free-text site name, a date).  Fitting it would eat the
# degrees of freedom the trial exists to spend.
MAX_LEVELS = 12
# Total encoded columns.  Twenty covariate columns on a clinical panel of a few
# dozen subjects is already past the point of usefulness.
MAX_COLUMNS = 20


@dataclass
class Covariate:
    """One subject-level variable, one raw string per subject (None = missing)."""

    name: str
    values: List[Optional[str]]
    categorical: bool = False
    numeric: List[Optional[float]] = field(default_factory=list)

    def level_labels(self) -> List[str]:
        """Distinct non-missing levels in order of first appearance."""
        out: List[str] = []
        for v in self.values:
            if v is not None and v not in out:
                out.append(v)
        return out

    def observed(self, i: int) -> bool:
        if self.categorical:
            return self.values[i] is not None
        return i < len(self.numeric) and self.numeric[i] is not None


@dataclass
class CovariateDesign:
    """A full-rank, mean-centred covariate block over a fixed subject list."""

    rows: List[int]                       # subject indices, in fit order
    names: List[str] = field(default_factory=list)
    columns: List[List[float]] = field(default_factory=list)   # [row][column]
    dropped: List[str] = field(default_factory=list)           # human-readable
    n_missing: int = 0                    # subjects excluded for missing values

    @property
    def n_columns(self) -> int:
        return len(self.names)


def orthonormal_basis(columns: Sequence[Sequence[float]]) -> List[List[float]]:
    """Modified Gram–Schmidt over *columns*, dropping the dependent ones."""
    basis: List[List[float]] = []
    for col in columns:
        work = list(col)
        for b in basis:
            dot = math.fsum(w * bv for w, bv in zip(work, b))
            work = [w - dot * bv for w, bv in zip(work, b)]
        norm = math.sqrt(math.fsum(v * v for v in work))
        if norm > 0.0:
            basis.append([v / norm for v in work])
    return basis


def independent_of(col: Sequence[float], basis: Sequence[Sequence[float]]) -> bool:
    """Does *col* add a direction the *basis* does not already span?

    Relative to the column's own norm, so the answer does not depend on whether
    a covariate is recorded in years or in seconds.
    """
    norm0 = math.sqrt(math.fsum(v * v for v in col))
    if norm0 <= 0.0:
        return False
    work = list(col)
    for b in basis:
        dot = math.fsum(w * bv for w, bv in zip(work, b))
        work = [w - dot * bv for w, bv in zip(work, b)]
    return math.sqrt(math.fsum(v * v for v in work)) > norm0 * 1e-8


def _short(level: str, limit: int = 32) -> str:
    """Bound a level label before it becomes a printed coefficient name.

    A category level is a *data value*: if someone points ``--covariate`` at a
    free-text field it can be a patient name, and nothing else bounds its
    length (one cell may be 131,072 characters).  The label has to identify the
    coefficient, not reproduce the record — so collapse whitespace and cut.
    """
    flat = " ".join(level.split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit - 8]}…({len(flat)}자)"


def complete_subjects(covariates: Sequence[Covariate],
                      candidates: Sequence[int]) -> Tuple[List[int], int]:
    """Split *candidates* into those with every covariate observed, and a count.

    Order is preserved; the second element is how many were dropped.
    """
    keep = [i for i in candidates
            if all(c.observed(i) for c in covariates)]
    return keep, len(candidates) - len(keep)


def _raw_columns(covariates: Sequence[Covariate], rows: Sequence[int]
                 ) -> Tuple[List[str], List[List[float]], List[str]]:
    """Reference-code categoricals, pass numerics through.  No centring yet."""
    names: List[str] = []
    cols: List[List[float]] = []
    dropped: List[str] = []
    for cov in covariates:
        if cov.categorical:
            # Levels present *among the subjects in this fit* — a level that
            # dropped out entirely would contribute an all-zero column.
            #
            # The order comes from the whole covariate, not from this subset:
            # ANCOVA refits at every visit, and taking "first row wins" per fit
            # let the reference level flip between visits as soon as the first
            # subject was missing at one of them.  A reference that changes
            # halfway down the table makes the covariate coefficients
            # incomparable (the arm contrast is unaffected either way).
            here = {cov.values[i] for i in rows if cov.values[i] is not None}
            present: List[str] = [lev for lev in cov.level_labels()
                                  if lev in here]
            if len(present) < 2:
                only = _short(present[0]) if present else "관측 없음"
                dropped.append(f"{cov.name}: 분석에 들어간 대상이 모두 "
                               f"'{only}' 한 수준뿐이라 제외")
                continue
            ref = present[0]
            for lev in present[1:]:
                names.append(f"{cov.name}={_short(lev)}")
                cols.append([1.0 if cov.values[i] == lev else 0.0 for i in rows])
        else:
            vals = [cov.numeric[i] for i in rows]
            names.append(cov.name)
            cols.append([float(v) if v is not None else 0.0 for v in vals])
    return names, cols, dropped


def encode_covariates(covariates: Sequence[Covariate], rows: Sequence[int]
                      ) -> CovariateDesign:
    """Build a centred, full-rank covariate block for the subjects in *rows*.

    *rows* must already be restricted to subjects with every covariate observed
    (use :func:`complete_subjects`).  Columns that are constant, or linearly
    dependent on earlier columns, are dropped with a note — that is what a site
    factor confounded with arm, or a stratum nobody reached, looks like.
    """
    design = CovariateDesign(rows=list(rows))
    if not covariates or not rows:
        return design
    names, cols, dropped = _raw_columns(covariates, list(rows))
    design.dropped.extend(dropped)

    n = len(rows)
    # Centre first: a constant column becomes the zero vector, which the
    # orthogonalisation below then rejects on its own.
    centred: List[List[float]] = []
    for col in cols:
        m = math.fsum(col) / n
        centred.append([v - m for v in col])

    # Modified Gram–Schmidt.  Keep a column only if what is left of it after
    # projecting out the accepted ones is a real direction, not rounding noise.
    basis: List[List[float]] = []
    kept_idx: List[int] = []
    for pos, (name, col) in enumerate(zip(names, centred)):
        norm0 = math.sqrt(math.fsum(v * v for v in col))
        work = list(col)
        for b in basis:
            dot = math.fsum(w * bv for w, bv in zip(work, b))
            work = [w - dot * bv for w, bv in zip(work, b)]
        norm = math.sqrt(math.fsum(v * v for v in work))
        # Relative to the column's own norm, never to an absolute floor.  With
        # `max(norm0, 1.0)` the test became an absolute 1e-8 cut-off for any
        # small-magnitude column, so the *units* of a covariate decided whether
        # it entered the model: the same concentration in mol/L was silently
        # dropped as "collinear" while nmol/L was kept, moving the adjusted
        # difference by 72%.  A rank test has to be scale-invariant.
        if norm0 <= 0.0 or norm <= norm0 * 1e-8:
            # With nothing accepted yet there is nothing to be collinear
            # *with*, so blaming another covariate would be a lie.
            reason = ("값이 모두 같아" if norm0 <= 0.0
                      else "앞서 채택된 공변량과 완전히 겹쳐(공선성)" if basis
                      else "변동이 사실상 없어")
            design.dropped.append(f"{name}: {reason} 제외")
            continue
        basis.append([v / norm for v in work])
        design.names.append(name)
        kept_idx.append(pos)
        if len(design.names) > MAX_COLUMNS:
            raise ValueError(
                f"공변량 열이 {MAX_COLUMNS}개를 넘습니다 — 범주 수준이 너무 "
                "많은 변수를 넣지 않았는지 확인하세요.")

    # Re-emit the accepted columns in their original (centred) form: the
    # orthogonalised basis was only ever a rank test.
    design.columns = [[centred[k][r] for k in kept_idx] for r in range(n)]
    return design
