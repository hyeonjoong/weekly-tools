"""ANCOVA — comparing groups after adjusting for baseline and other covariates.

Why this exists
---------------
In almost every randomised trial with a continuous endpoint, the pre-specified
primary analysis is **not** a plain t-test.  It is an analysis of covariance:
the post-treatment outcome regressed on the treatment arm *and* the patient's
own baseline value (plus any randomisation stratification factors such as site).
ICH E9 §5.7 is where pre-specified covariate adjustment is set out (it does not
mandate ANCOVA; it requires the adjustment to be pre-specified and expects the
unadjusted analysis to be shown alongside).  The adjustment is not cosmetic — a
baseline that correlates with the outcome at r removes a fraction r² of the
residual variance.  The residual variance is never *larger*, so with any real
baseline correlation ANCOVA beats both the raw post-treatment comparison and a
t-test on change-from-baseline — though it does spend one extra degree of
freedom, so at r near 0 it is very slightly worse in a small sample.  Unlike the
change score it is also unbiased under baseline imbalance (Lord's paradox).

What is computed
----------------
The model is

    y_i = mu + tau_(group i) + sum_j beta_j * z_ij + sum_f gamma_(level f,i) + e_i

fitted by least squares with the last group as the reference (so every
coefficient and contrast reads as "arm minus control").  From that fit:

* **Adjusted (least-squares) means** per group — the model's prediction with
  every numeric covariate held at its overall mean and every adjustment factor
  weighted equally across its levels (the ``emmeans`` convention), with a
  t-based confidence interval.
* **Adjusted group differences** with confidence intervals and p-values, and
  Holm/BH correction across the pairwise family when there are 3+ arms.
* An **omnibus F-test for the group term**, obtained by refitting without the
  group dummies (a Type II / Type III test — they coincide for a model with no
  interactions), plus partial eta-squared.
* **Covariate slopes** with their own t-tests, so the reader can see whether
  adjusting was worth it.

Assumption checks that ANCOVA specifically needs
------------------------------------------------
* **Homogeneity of regression slopes** — the group x covariate interaction is
  refitted and F-tested.  A significant interaction means the treatment effect
  depends on baseline, and a single adjusted difference no longer describes the
  data.
* **Residual normality** (Shapiro-Wilk on residuals, not on raw groups — that is
  the assumption the model actually makes) and **residual homoscedasticity**
  (Levene on residuals by group).

Honesty
-------
Adjustment is only causal for covariates fixed *before* randomisation.  A
covariate measured after treatment started can be a mediator or a collider, and
adjusting for it biases the treatment effect in a direction nothing in the data
can reveal.  The report says so every time, because the arithmetic cannot tell
the two cases apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import equivalence as equiv_mod
from . import linalg, tests_stat
from .dataio import sanitize_label as _safe_label
from .dataio import summarize_values
from .normality import shapiro_wilk
from .special import f_sf, t_ppf, t_sf_two_sided

__all__ = ["AncovaRecord", "AdjustedMean", "CovariateEffect", "AncovaContrast",
           "SlopeCheck", "AncovaResult", "run_ancova"]


@dataclass
class AncovaRecord:
    """One analysable subject: outcome, arm, covariate values, factor levels."""

    group: str
    y: float
    covariates: Tuple[float, ...] = ()
    factors: Tuple[str, ...] = ()


@dataclass
class AdjustedMean:
    label: str
    n: int
    raw_mean: float
    adjusted: float
    se: float
    ci: Tuple[float, float]
    #: mean of each numeric covariate within this group (baseline balance)
    covariate_means: Tuple[float, ...] = ()


@dataclass
class CovariateEffect:
    name: str
    coef: float
    se: float
    t: float
    pvalue: float
    ci: Tuple[float, float]
    kind: str = "numeric"   # "numeric" | "factor"


@dataclass
class AncovaContrast:
    a: str
    b: str
    diff: float
    se: float
    df: float
    ci: Tuple[float, float]
    pvalue_raw: float
    pvalue_adj: float
    significant: bool
    n_a: int = 0
    n_b: int = 0


@dataclass
class SlopeCheck:
    """Group x covariate interaction test (homogeneity of regression slopes)."""

    statistic: Optional[float]
    df1: Optional[float]
    df2: Optional[float]
    pvalue: Optional[float]
    homogeneous: Optional[bool]
    note: str = ""


@dataclass
class AncovaResult:
    outcome: str
    covariate_names: List[str]
    factor_names: List[str]
    reference: str
    alpha: float
    alpha_norm: float
    n_used: int
    n_dropped: int
    adjusted_means: List[AdjustedMean]
    contrasts: List[AncovaContrast]
    covariate_effects: List[CovariateEffect]
    f_statistic: float
    df1: float
    df2: float
    pvalue: float
    significant: bool
    partial_eta_sq: float
    r_squared: float
    adj_r_squared: float
    sigma: float
    slopes: Optional[SlopeCheck] = None
    resid_normal_p: Optional[float] = None
    resid_levene_p: Optional[float] = None
    equivalence: Optional[equiv_mod.EquivalenceResult] = None
    correction: str = "holm"
    warnings: List[str] = field(default_factory=list)
    #: label -> unusable source rows, for CONSORT-style accounting
    missing: Dict[str, int] = field(default_factory=dict)

    @property
    def group_labels(self) -> List[str]:
        return [a.label for a in self.adjusted_means]


#: Above this many arms an ANCOVA is neither computable in reasonable time nor
#: reportable: the group dummies enter the design matrix itself, so the fit is
#: O(n*k^2), the slope-homogeneity refit doubles k again, and the contrast table
#: grows as k^2 (k=150 already takes ~18 s and prints 11,175 rows). Past this
#: point --group is almost always pointed at the wrong column — a subject id, a
#: date, a free-text field — so a refusal that names the fix beats a hang.
MAX_ANCOVA_GROUPS = 60


_COVARIATE_TIMING_NOTE = (
    "공변량 보정은 **무작위배정 전에 측정된** 변수에 대해서만 인과적으로 "
    "해석할 수 있습니다. 치료 시작 이후에 측정된 값(예: 중간 방문 수치, 순응도, "
    "부작용 발생 여부)을 공변량으로 넣으면 치료효과가 매개변수·충돌변수를 통해 "
    "편향되며, 그 편향은 자료만으로는 확인할 수 없습니다 — 공변량이 기저값인지 "
    "직접 확인하세요.")

_LINEARITY_NOTE = (
    "ANCOVA는 결과와 공변량의 관계가 **직선**이고 그 기울기가 그룹 간 같다고 "
    "가정합니다. 관계가 곡선이면 보정이 불완전해 효과 추정이 치우칠 수 있으니, "
    "산점도로 확인하거나 공변량을 변환(예: log)한 뒤 다시 실행하세요.")

_LSMEAN_NOTE = (
    "보정평균(adjusted/LS mean)은 모든 수치 공변량을 **전체 평균**에 고정하고 "
    "보정인자는 각 수준에 **동일 가중치**를 주어 계산했습니다(emmeans 기본 규약). "
    "따라서 관측된 그룹 평균과 다를 수 있으며, 이것이 정상입니다.")


def _dummy_columns(labels: Sequence[str], levels: Sequence[str]
                   ) -> List[List[float]]:
    """Treatment-coded dummies for ``levels[:-1]`` (last level is the reference)."""
    return [[1.0 if lab == lv else 0.0 for lab in labels]
            for lv in levels[:-1]]


def _factor_columns(values: Sequence[str], levels: Sequence[str]
                    ) -> List[List[float]]:
    """Dummies for a nuisance factor; the **first** level is the reference."""
    return [[1.0 if v == lv else 0.0 for v in values] for lv in levels[1:]]


def _holm(pvals: List[float]) -> List[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adj[idx] = min(1.0, running)
    return adj


def _bh(pvals: List[float]) -> List[float]:
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        running = min(running, pvals[idx] * m / (rank + 1))
        adj[idx] = min(1.0, running)
    return adj


def _correct(pvals: List[float], method: str) -> List[float]:
    if method == "bh":
        return _bh(pvals)
    if method == "holm":
        return _holm(pvals)
    raise ValueError("correction must be 'holm' or 'bh'")


def _nested_f(rss_reduced: float, rss_full: float, q: int, df_resid: int
              ) -> Tuple[float, float]:
    """F and p for dropping ``q`` columns, from the two residual sums of squares."""
    if q <= 0 or df_resid <= 0:
        return float("nan"), float("nan")
    ms_err = rss_full / df_resid
    if ms_err <= 0.0:
        # A perfect fit: the model explains the outcome exactly. Any non-zero
        # improvement is infinitely significant; no improvement is undefined.
        gain = rss_reduced - rss_full
        if gain <= 0.0:
            return float("nan"), float("nan")
        return math.inf, 0.0
    f = ((rss_reduced - rss_full) / q) / ms_err
    if f < 0.0:            # rounding only; the nested model cannot fit better
        f = 0.0
    return f, f_sf(f, float(q), float(df_resid))


def _levels_in_order(values: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


def _check_records(records: Sequence[AncovaRecord], n_cov: int, n_fac: int
                   ) -> None:
    for r in records:
        if len(r.covariates) != n_cov or len(r.factors) != n_fac:
            raise ValueError(
                "관측치마다 공변량/보정인자 개수가 다릅니다 (자료를 확인하세요).")
        for v in (r.y,) + tuple(r.covariates):
            if not math.isfinite(float(v)):
                raise ValueError(
                    "결과값 또는 공변량에 NaN/무한대가 있습니다 — 해당 행을 "
                    "제외한 뒤 다시 실행하세요.")


def _slope_homogeneity(columns: List[List[float]], y: List[float],
                       labels: List[str], levels: List[str],
                       cov_cols: List[List[float]], rss_full: float,
                       df_full: int, alpha_norm: float) -> SlopeCheck:
    """Refit with group x covariate interactions and F-test the whole block."""
    k = len(levels)
    n_cov = len(cov_cols)
    q = (k - 1) * n_cov
    if q == 0:
        return SlopeCheck(None, None, None, None, None,
                          "수치 공변량이 없어 기울기 동질성 검정을 생략했습니다.")
    dummies = _dummy_columns(labels, levels)
    extra = [[a * b for a, b in zip(d, c)] for d in dummies for c in cov_cols]
    try:
        fit = linalg.lstsq_columns(list(columns) + extra, y)
    except ValueError as exc:
        return SlopeCheck(None, None, None, None, None,
                          f"기울기 동질성 검정을 수행할 수 없습니다: {exc}")
    df2 = fit.df_resid
    if df2 <= 0:
        return SlopeCheck(None, None, None, None, None,
                          "자유도가 부족해 기울기 동질성 검정을 생략했습니다.")
    f, p = _nested_f(rss_full, fit.rss, q, df2)
    if p != p:
        return SlopeCheck(None, None, None, None, None,
                          "잔차가 0이라 기울기 동질성 검정이 정의되지 않습니다.")
    return SlopeCheck(f, float(q), float(df2), p, p > alpha_norm)


def run_ancova(records: Sequence[AncovaRecord],
               covariate_names: Sequence[str] = (),
               factor_names: Sequence[str] = (),
               outcome: str = "outcome",
               alpha: float = 0.05, alpha_norm: float = 0.05,
               correction: str = "holm",
               reference: Optional[str] = None,
               equivalence=None,
               missing: Optional[Dict[str, int]] = None,
               n_dropped: int = 0) -> AncovaResult:
    """Fit the ANCOVA model and assemble a reportable result.

    ``records`` must already be complete cases.  The reference group is
    ``reference`` when given, otherwise the **last** group in first-seen order —
    the same convention as the rest of statwise, so ``--reference`` keeps
    meaning "every difference is (other arm − this one)".
    """
    if correction not in ("holm", "bh"):
        raise ValueError("correction must be 'holm' or 'bh'")
    covariate_names = [str(c) for c in covariate_names]
    factor_names = [str(f) for f in factor_names]
    records = list(records)
    if not records:
        raise ValueError("분석할 관측치가 없습니다.")
    _check_records(records, len(covariate_names), len(factor_names))

    # Identity is the RAW group string. Keying on the sanitised label merged two
    # arms whose names agreed in their first 39 characters -- exactly the kind of
    # long protocol-coded arm name a trial uses -- into one, and then reported a
    # single confident adjusted difference between an average of two treatments
    # and the control, with no warning.
    labels = [str(r.group) for r in records]
    levels = _levels_in_order(labels)
    if len(levels) < 2:
        raise ValueError(
            f"공변량 보정 비교에는 그룹이 2개 이상 필요합니다 (지금 "
            f"{len(levels)}개: {summarize_values(levels) if levels else '없음'}).")
    if reference is not None:
        ref = str(reference)
        if ref not in levels:
            raise ValueError(
                f"기준군 '{_safe_label(reference)}' 을(를) 자료에서 찾을 수 "
                f"없습니다 (있는 그룹: {summarize_values(levels)}).")
        levels = [lv for lv in levels if lv != ref] + [ref]
    k = len(levels)
    # Printable names, kept one-to-one with the raw arms: if two raw labels
    # sanitise to the same string we disambiguate and say so, rather than let a
    # display truncation quietly decide which subjects share an arm.
    display: Dict[str, str] = {}
    seen_display: Dict[str, int] = {}
    for lv in levels:
        base = _safe_label(lv) or "(빈 라벨)"
        seen_display[base] = seen_display.get(base, 0) + 1
        # The marker goes in FRONT: the report elides long labels from the
        # right, so a trailing "#2" is the first thing to disappear -- and then
        # two different arms print identically again.
        display[lv] = (base if seen_display[base] == 1
                       else f"[{seen_display[base]}] {base}")
    ref_label = display[levels[-1]]

    collided = sorted({b for b, c in seen_display.items() if c > 1})
    if k > MAX_ANCOVA_GROUPS:
        raise ValueError(
            f"그룹이 {k}개라 공변량 보정 비교를 수행하지 않았습니다 "
            f"(상한 {MAX_ANCOVA_GROUPS}그룹). 이만한 수의 군은 설계행렬과 "
            f"쌍별 비교표({k * (k - 1) // 2:,}개)를 모두 감당할 수 없고 보고할 "
            f"표도 아닙니다 — --group 이 대상 ID·날짜·자유입력 열을 가리키고 "
            f"있지 않은지 확인하고, 관심 있는 군만 남겨 다시 실행하세요.")
    counts: Dict[str, int] = {lv: 0 for lv in levels}
    for lab in labels:
        counts[lab] += 1
    thin = [display[lv] for lv in levels if counts[lv] < 2]
    if thin:
        raise ValueError(
            "공변량 보정 비교에는 그룹마다 관측치가 2개 이상 필요합니다 "
            f"(부족한 그룹: {', '.join(thin)}).")

    y = [float(r.y) for r in records]
    n = len(y)
    if all(v == y[0] for v in y):
        raise ValueError(
            "결과값이 모든 관측치에서 동일합니다(분산 0). 설명할 변동이 없어 "
            "공분산분석을 정의할 수 없습니다 — 이 상태로 계산하면 F와 p값이 "
            "반올림 오차의 비율이 되어 그럴듯한 무의미한 값이 나옵니다. "
            "결과 열을 잘못 지정했는지 확인하세요.")
    cov_cols = [[float(r.covariates[j]) for r in records]
                for j in range(len(covariate_names))]
    fac_levels = [_levels_in_order([r.factors[j] for r in records])
                  for j in range(len(factor_names))]
    for name, lvs in zip(factor_names, fac_levels):
        if len(lvs) < 2:
            raise ValueError(
                f"보정인자 '{name}' 의 수준이 1개뿐이라 보정할 것이 없습니다 — "
                f"해당 인자를 빼고 다시 실행하세요.")

    columns: List[List[float]] = [[1.0] * n]
    columns.extend(_dummy_columns(labels, levels))
    columns.extend(cov_cols)
    fac_col_names: List[str] = []
    for j, (name, lvs) in enumerate(zip(factor_names, fac_levels)):
        vals = [r.factors[j] for r in records]
        columns.extend(_factor_columns(vals, lvs))
        fac_col_names.extend(f"{name}={lv} (vs {lvs[0]})" for lv in lvs[1:])

    p = len(columns)
    if n <= p:
        raise ValueError(
            f"모형의 모수({p}개)가 관측치({n}개) 이상이라 추정할 수 없습니다 — "
            f"공변량·보정인자를 줄이거나 표본을 늘리세요.")
    fit = linalg.lstsq_columns(columns, y)
    df_resid = fit.df_resid
    sigma2 = fit.rss / df_resid if df_resid > 0 else float("nan")
    sigma = math.sqrt(sigma2) if sigma2 == sigma2 and sigma2 >= 0 else float("nan")

    warnings: List[str] = [_COVARIATE_TIMING_NOTE, _LSMEAN_NOTE]
    if collided:
        warnings.append(
            "서로 다른 군인데 표에 찍히는 이름이 같아집니다(이름이 너무 길거나 "
            "제어문자가 섞였습니다): " + ", ".join(collided) +
            ". 분석은 원래 값 그대로 **서로 다른 군**으로 했고, 표에서만 "
            "[2], [3] 을 앞에 붙여 구분했습니다 — CSV의 군 이름을 짧고 "
            "고유하게 만드는 편이 안전합니다.")
    if covariate_names:
        warnings.append(_LINEARITY_NOTE)

    # ---- omnibus test for the group term (refit without the group dummies)
    try:
        reduced = linalg.lstsq_columns([columns[0]] + columns[k:], y)
        f_stat, p_val = _nested_f(reduced.rss, fit.rss, k - 1, df_resid)
        partial_eta = ((reduced.rss - fit.rss) / reduced.rss
                       if reduced.rss > 0 else float("nan"))
        if partial_eta == partial_eta:
            partial_eta = min(1.0, max(0.0, partial_eta))
    except ValueError as exc:  # pragma: no cover - guarded by the checks above
        raise ValueError(f"그룹 효과를 검정할 수 없습니다: {exc}")

    # ---- adjusted (least-squares) means
    by_level: Dict[str, List[int]] = {lv: [] for lv in levels}
    for i, lab in enumerate(labels):
        by_level[lab].append(i)
    cov_means = [tests_stat.mean(c) for c in cov_cols]
    base = [1.0] + [0.0] * (k - 1) + list(cov_means)
    for lvs in fac_levels:
        base.extend([1.0 / len(lvs)] * (len(lvs) - 1))
    tcrit = t_ppf(1.0 - alpha / 2.0, df_resid) if df_resid > 0 else float("nan")
    adjusted: List[AdjustedMean] = []
    for gi, lv in enumerate(levels):
        c = list(base)
        if gi < k - 1:
            c[1 + gi] = 1.0
        est = fit.estimate(c)
        var = sigma2 * fit.quadratic_form(c)
        se = math.sqrt(var) if var == var and var >= 0 else float("nan")
        ci = (est - tcrit * se, est + tcrit * se)
        idxs = by_level[lv]
        raw = [y[i] for i in idxs]
        gcm = tuple(tests_stat.mean([c_[i] for i in idxs]) for c_ in cov_cols)
        adjusted.append(AdjustedMean(display[lv], len(raw),
                                     tests_stat.mean(raw), est, se, ci, gcm))

    # ---- pairwise adjusted differences (covariate terms cancel)
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
    raw_p: List[float] = []
    meta = []
    for i, j in pairs:
        c = [0.0] * p
        if i < k - 1:
            c[1 + i] = 1.0
        if j < k - 1:
            c[1 + j] -= 1.0
        diff = fit.estimate(c)
        var = sigma2 * fit.quadratic_form(c)
        se = math.sqrt(var) if var == var and var >= 0 else float("nan")
        t = diff / se if se and se == se and se > 0 else float("nan")
        pv = t_sf_two_sided(t, df_resid) if t == t else float("nan")
        raw_p.append(pv)
        meta.append((display[levels[i]], display[levels[j]], diff, se, t,
                     counts[levels[i]], counts[levels[j]]))
    adj_p = _correct([q if q == q else 1.0 for q in raw_p], correction)
    contrasts: List[AncovaContrast] = []
    for idx, (la, lb, diff, se, _t, na, nb) in enumerate(meta):
        lo = diff - tcrit * se
        hi = diff + tcrit * se
        contrasts.append(AncovaContrast(
            la, lb, diff, se, float(df_resid), (lo, hi), raw_p[idx],
            adj_p[idx], adj_p[idx] < alpha, na, nb))

    # ---- covariate / factor coefficients
    effects: List[CovariateEffect] = []
    coef_names = ([f"{c}" for c in covariate_names] + fac_col_names)
    kinds = ["numeric"] * len(covariate_names) + ["factor"] * len(fac_col_names)
    for offset, (name, kind) in enumerate(zip(coef_names, kinds)):
        idx = k + offset
        b = fit.beta[idx]
        var = sigma2 * fit.xtx_inv[idx][idx]
        se = math.sqrt(var) if var == var and var >= 0 else float("nan")
        t = b / se if se and se == se and se > 0 else float("nan")
        pv = t_sf_two_sided(t, df_resid) if t == t else float("nan")
        effects.append(CovariateEffect(
            name, b, se, t, pv, (b - tcrit * se, b + tcrit * se), kind))

    # ---- assumption checks on the residuals, which is what the model assumes
    resid = fit.residuals
    resid_p: Optional[float] = None
    if 3 <= n <= 5000:
        try:
            _w, resid_p = shapiro_wilk(resid)
        except ValueError:
            resid_p = None
    if resid_p is not None and resid_p <= alpha_norm:
        warnings.append(
            "잔차의 정규성이 기각되었습니다 (Shapiro-Wilk p={:.4f}). 표본이 크면 "
            "중심극한정리로 t-검정이 견고하지만, 소표본이거나 이상치가 있으면 "
            "결과를 신뢰하기 어렵습니다 — 이상치 확인 또는 변환을 고려하세요."
            .format(resid_p))
    resid_lev: Optional[float] = None
    if k >= 2 and all(counts[lv] >= 2 for lv in levels):
        by_group = [[resid[i] for i in by_level[lv]] for lv in levels]
        try:
            resid_lev = tests_stat.levene(by_group).pvalue
        except ValueError:
            resid_lev = None
    if resid_lev is not None and resid_lev == resid_lev and resid_lev <= alpha_norm:
        warnings.append(
            "그룹별 잔차의 분산이 서로 다릅니다 (Levene p={:.4f}). ANCOVA의 "
            "표준오차는 공통 분산을 가정하므로, 이 경우 p값과 신뢰구간이 "
            "낙관적일 수 있습니다.".format(resid_lev))

    slopes = _slope_homogeneity(columns, y, labels, levels, cov_cols, fit.rss,
                                df_resid, alpha_norm)
    if slopes.homogeneous is False:
        warnings.append(
            "기울기 동질성 가정이 기각되었습니다 (그룹×공변량 상호작용 "
            "F={:.3f}, p={:.4f}). 치료효과가 기저값에 따라 달라진다는 뜻이므로 "
            "'하나의 보정된 차이'로 요약하면 오해를 부릅니다 — 기저값 수준별로 "
            "나누어 보고하거나 상호작용을 포함한 모형을 쓰세요."
            .format(slopes.statistic, slopes.pvalue))
    elif slopes.note:
        warnings.append(slopes.note)

    # Hoist the mean: evaluating it inside the generator made this O(n^2) and
    # turned a 200k-row file into a four-minute apparent hang.
    y_mean = tests_stat.mean(y)
    tss = math.fsum((v - y_mean) ** 2 for v in y)
    r2 = 1.0 - fit.rss / tss if tss > 0 else float("nan")
    # A residual variance of essentially zero is almost never a great model; it
    # is a covariate that contains the outcome (adjusting a change score for the
    # post-treatment value, or a "covariate" copied from the endpoint column).
    # Left alone it produces F ~ 1e31 and p = 0.000, which reads as a triumph.
    if tss > 0 and fit.rss <= 1e-18 * tss:
        warnings.append(
            "잔차가 사실상 0입니다(R²≈1). 결과값이 공변량으로 완전히 설명된다는 "
            "뜻이며, 대개 결과값 자체가 들어간 변수(예: 변화량을 사후값으로 보정, "
            "결과 열을 그대로 복사한 열)를 넣었을 때 생깁니다 — F와 p값이 "
            "천문학적으로 커지므로 그대로 보고하지 마세요.")
    adj_r2 = (1.0 - (1.0 - r2) * (n - 1) / df_resid
              if r2 == r2 and df_resid > 0 else float("nan"))

    # ---- optional equivalence / non-inferiority on the adjusted difference
    eq = None
    if equivalence is not None and getattr(equivalence, "active", False):
        if k != 2:
            warnings.append(
                f"등가(TOST)/비열등성 검정은 두 그룹 비교에서만 정의됩니다 "
                f"(지금 {k}그룹) — 건너뜁니다.")
        else:
            cst = contrasts[0]
            try:
                if equivalence.margin is not None:
                    eq = equiv_mod.tost(cst.diff, cst.se, float(df_resid),
                                        equivalence.margin[0],
                                        equivalence.margin[1], alpha,
                                        model="ancova")
                else:
                    eq = equiv_mod.noninferiority(
                        cst.diff, cst.se, float(df_resid),
                        equivalence.ni_margin, equivalence.ni_direction, alpha,
                        model="ancova")
            except ValueError as exc:
                warnings.append(f"등가/비열등성 검정을 수행할 수 없습니다: {exc}")

    gone = sorted(_safe_label(g) for g in (missing or {})
                  if g and g not in levels)
    if gone:
        warnings.append(
            "완전한 행(결과값·공변량·보정인자가 모두 있는 행)이 하나도 없어 "
            "분석에서 통째로 빠진 군: " + ", ".join(gone) +
            ". 보고할 때 이 군이 사라진 사실을 반드시 밝히세요.")
    unusable = [m.label for m in adjusted if not math.isfinite(m.se)]
    unusable += [f"{c.a}−{c.b}" for c in contrasts if not math.isfinite(c.se)]
    if unusable:
        warnings.append(
            "표준오차가 유한하지 않아(±inf) 다음 추정값의 신뢰구간을 해석할 수 "
            "없습니다: " + ", ".join(unusable[:5]) +
            (" 외" if len(unusable) > 5 else "") +
            ". 공변량의 크기가 극단적이거나(예: 1e-300) 설계가 거의 특이합니다 "
            "— 단위를 바꾸어 다시 실행하세요.")
    if n_dropped:
        warnings.append(
            f"공변량·보정인자·결과값 중 하나라도 결측인 행 {n_dropped}개를 "
            f"제외하고 완전자료(complete-case)로 분석했습니다. 결측이 무작위가 "
            f"아니면 이 방식은 치료효과를 왜곡할 수 있습니다.")

    return AncovaResult(
        outcome=outcome, covariate_names=list(covariate_names),
        factor_names=list(factor_names), reference=ref_label, alpha=alpha,
        alpha_norm=alpha_norm, n_used=n, n_dropped=n_dropped,
        adjusted_means=adjusted, contrasts=contrasts, covariate_effects=effects,
        f_statistic=f_stat, df1=float(k - 1), df2=float(df_resid),
        pvalue=p_val, significant=(p_val == p_val and p_val < alpha),
        partial_eta_sq=partial_eta, r_squared=r2, adj_r_squared=adj_r2,
        sigma=sigma, slopes=slopes, resid_normal_p=resid_p,
        resid_levene_p=resid_lev, equivalence=eq, correction=correction,
        warnings=warnings, missing=dict(missing or {}))
