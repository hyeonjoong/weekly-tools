"""OLS regression with inference and diagnostics — pure standard library."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .linalg import SingularDesignError, qr_lstsq
from .special import chi2_sf, f_sf, t_ppf, t_sf_two_sided

__all__ = ["Coef", "Regression", "fit_ols", "vif_table", "breusch_pagan",
           "influence_summary", "mean", "sd"]


def mean(v: Sequence[float]) -> float:
    return sum(v) / len(v)


def sd(v: Sequence[float]) -> float:
    """Sample standard deviation (n-1 denominator)."""
    n = len(v)
    if n < 2:
        return float("nan")
    m = sum(v) / n
    return math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))


class Coef:
    """One estimated coefficient."""

    __slots__ = ("name", "estimate", "se", "t", "p", "ci_lo", "ci_hi")

    def __init__(self, name: str, estimate: float, se: float, t: float,
                 p: float, ci_lo: float, ci_hi: float):
        self.name = name
        self.estimate = estimate
        self.se = se
        self.t = t
        self.p = p
        self.ci_lo = ci_lo
        self.ci_hi = ci_hi

    def to_dict(self) -> dict:
        return {"term": self.name, "estimate": self.estimate, "se": self.se,
                "t": self.t, "p": self.p, "ci_lo": self.ci_lo, "ci_hi": self.ci_hi}


class Regression:
    """A fitted OLS model, with the pieces the report and diagnostics need."""

    def __init__(self, outcome: str, terms: List[str], coefs: List[Coef],
                 n: int, rss: float, tss: float, residuals: List[float],
                 sigma2: float, rcond: float, robust: Optional[str],
                 design: List[List[float]], xtx_inv: List[List[float]]):
        self.outcome = outcome
        self.terms = terms
        self.coefs = coefs
        self.n = n
        self.rss = rss
        self.tss = tss
        self.residuals = residuals
        self.sigma2 = sigma2
        self.rcond = rcond
        self.robust = robust
        self.design = design
        self.xtx_inv = xtx_inv
        self.p = len(terms)                 # includes the intercept
        self.df_resid = n - self.p
        self.r2 = 1.0 - rss / tss if tss > 0 else float("nan")
        k = self.p - 1                      # predictors excluding intercept
        if tss > 0 and self.df_resid > 0 and k > 0:
            self.adj_r2 = 1.0 - (1.0 - self.r2) * (n - 1) / self.df_resid
            msr = (tss - rss) / k
            mse = rss / self.df_resid
            self.f = msr / mse if mse > 0 else float("inf")
            self.f_p = f_sf(self.f, k, self.df_resid) if math.isfinite(self.f) else 0.0
            self.f_df = (k, self.df_resid)
        else:
            self.adj_r2 = float("nan")
            self.f = float("nan")
            self.f_p = float("nan")
            self.f_df = (k, self.df_resid)

    def coef(self, name: str) -> Coef:
        for c in self.coefs:
            if c.name == name:
                return c
        raise KeyError(name)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "n": self.n,
            "df_resid": self.df_resid,
            "r_squared": self.r2,
            "adj_r_squared": self.adj_r2,
            "f": self.f,
            "f_df": list(self.f_df),
            "f_p": self.f_p,
            "se_type": self.robust or "classical",
            "coefficients": [c.to_dict() for c in self.coefs],
        }


def _leverages(design: Sequence[Sequence[float]],
               xtx_inv: Sequence[Sequence[float]]) -> List[float]:
    p = len(xtx_inv)
    out = []
    for row in design:
        s = 0.0
        for i in range(p):
            ri = row[i]
            if ri == 0.0:
                continue
            inv_i = xtx_inv[i]
            acc = 0.0
            for j in range(p):
                acc += inv_i[j] * row[j]
            s += ri * acc
        # numerical safety: leverage lives in [0, 1]
        out.append(min(1.0, max(0.0, s)))
    return out


def _hc3_cov(design: Sequence[Sequence[float]],
             xtx_inv: Sequence[Sequence[float]],
             residuals: Sequence[float],
             lev: Sequence[float]) -> List[List[float]]:
    """HC3 (MacKinnon–White) heteroscedasticity-consistent covariance."""
    p = len(xtx_inv)
    meat = [[0.0] * p for _ in range(p)]
    for row, e, h in zip(design, residuals, lev):
        denom = (1.0 - h)
        w = (e / denom) ** 2 if denom > 1e-10 else (e / 1e-10) ** 2
        for i in range(p):
            ri = row[i]
            if ri == 0.0:
                continue
            wri = w * ri
            mi = meat[i]
            for j in range(i, p):
                mi[j] += wri * row[j]
    for i in range(p):
        for j in range(i):
            meat[i][j] = meat[j][i]
    tmp = [[sum(xtx_inv[i][k] * meat[k][j] for k in range(p)) for j in range(p)]
           for i in range(p)]
    return [[sum(tmp[i][k] * xtx_inv[k][j] for k in range(p)) for j in range(p)]
            for i in range(p)]


def fit_ols(outcome_name: str,
            y: Sequence[float],
            predictors: Sequence[Tuple[str, Sequence[float]]],
            conf: float = 0.95,
            robust: Optional[str] = None,
            keep_design: bool = True) -> Regression:
    """Fit ``y ~ 1 + predictors`` by least squares.

    ``robust='hc3'`` switches the standard errors to the HC3
    heteroscedasticity-consistent estimator (point estimates are unchanged).
    """
    n = len(y)
    terms = ["(절편)"] + [nm for nm, _ in predictors]
    design = [[1.0] + [col[i] for _, col in predictors] for i in range(n)]
    res = qr_lstsq(design, y, terms)
    beta = res.beta
    p = res.p
    fitted = [sum(b * v for b, v in zip(beta, row)) for row in design]
    residuals = [yi - fi for yi, fi in zip(y, fitted)]
    # RSS from residuals directly (QR's tail-norm agrees to rounding, but this
    # is what the diagnostics below use, so keep one definition).
    rss = sum(e * e for e in residuals)
    ybar = sum(y) / n
    tss = sum((yi - ybar) ** 2 for yi in y)
    df_resid = n - p
    sigma2 = rss / df_resid if df_resid > 0 else float("nan")

    if robust == "hc3":
        if df_resid <= 0:
            raise SingularDesignError("자유도가 0이라 표준오차를 계산할 수 없습니다.")
        lev = _leverages(design, res.xtx_inv)
        cov = _hc3_cov(design, res.xtx_inv, residuals, lev)
        ses = [math.sqrt(max(cov[i][i], 0.0)) for i in range(p)]
    else:
        ses = [math.sqrt(max(sigma2 * res.xtx_inv[i][i], 0.0)) if df_resid > 0
               else float("nan") for i in range(p)]

    tcrit = t_ppf(0.5 + conf / 2.0, df_resid) if df_resid > 0 else float("nan")
    coefs = []
    for i, name in enumerate(terms):
        se = ses[i]
        if se > 0 and math.isfinite(se):
            tval = beta[i] / se
            pval = t_sf_two_sided(tval, df_resid)
            lo, hi = beta[i] - tcrit * se, beta[i] + tcrit * se
        else:
            tval = pval = lo = hi = float("nan")
        coefs.append(Coef(name, beta[i], se, tval, pval, lo, hi))

    return Regression(outcome_name, terms, coefs, n, rss, tss, residuals,
                      sigma2, res.rcond, robust,
                      design if keep_design else [], res.xtx_inv)


def vif_table(predictors: Sequence[Tuple[str, Sequence[float]]]) -> List[Tuple[str, float]]:
    """Variance-inflation factor for each predictor (intercept excluded).

    ``inf`` marks a perfectly collinear predictor. Returns an empty list when
    there is only one predictor (VIF is undefined / always 1).
    """
    k = len(predictors)
    if k < 2:
        return []
    out = []
    for j in range(k):
        name, col = predictors[j]
        others = [predictors[i] for i in range(k) if i != j]
        try:
            reg = fit_ols(name, list(col), others, keep_design=False)
        except SingularDesignError:
            out.append((name, float("inf")))
            continue
        r2 = reg.r2
        if not math.isfinite(r2) or r2 >= 1.0 - 1e-12:
            out.append((name, float("inf")))
        else:
            out.append((name, 1.0 / (1.0 - r2)))
    return out


def breusch_pagan(reg: Regression) -> Optional[Tuple[float, int, float]]:
    """Koenker's studentized Breusch–Pagan test for heteroscedasticity.

    Returns ``(LM statistic, df, p)`` or ``None`` when it cannot be computed.
    The studentized form is used because the original BP test is itself
    sensitive to non-normal residuals, which is common in clinical outcomes.
    """
    if not reg.design or reg.p < 2 or reg.df_resid <= 0:
        return None
    e2 = [e * e for e in reg.residuals]
    preds = [(reg.terms[j], [row[j] for row in reg.design]) for j in range(1, reg.p)]
    if len({round(v, 15) for v in e2}) == 1:
        return None
    try:
        aux = fit_ols("e^2", e2, preds, keep_design=False)
    except SingularDesignError:
        return None
    if not math.isfinite(aux.r2):
        return None
    lm = reg.n * aux.r2
    df = reg.p - 1
    return lm, df, chi2_sf(lm, df)


def influence_summary(reg: Regression, top: int = 3
                      ) -> Optional[Tuple[int, float, List[Tuple[int, float]]]]:
    """Cook's distance summary: (count above 4/n, cutoff, top offenders).

    Offenders are ``(row position, D)`` with 0-based positions into the
    analysed (listwise-complete) sample.
    """
    if not reg.design or reg.df_resid <= 0 or not math.isfinite(reg.sigma2) or reg.sigma2 <= 0:
        return None
    lev = _leverages(reg.design, reg.xtx_inv)
    p = reg.p
    ds = []
    for i, (e, h) in enumerate(zip(reg.residuals, lev)):
        denom = (1.0 - h)
        if denom <= 1e-10:
            ds.append((i, float("inf")))
            continue
        d = (e * e) * h / (p * reg.sigma2 * denom * denom)
        ds.append((i, d))
    cutoff = 4.0 / reg.n
    flagged = [x for x in ds if x[1] > cutoff]
    flagged.sort(key=lambda t: -t[1])
    return len(flagged), cutoff, flagged[:top]
