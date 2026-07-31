"""Mediation (indirect-effect) analysis — parallel and serial multiple mediators.

Model, in the notation used throughout the output:

    parallel (PROCESS model 4)      serial (PROCESS model 6)
      M_j = i + a_j*X + covs          M_j = i + a_j*X + sum_{i<j} d_ji*M_i + covs
      Y   = i + c'*X + sum b_j*M_j + covs
      Y   = i + c*X  + covs                      (total effect)

With OLS and the same rows/covariates in every equation, the decomposition is
exact: ``c = c' + sum(specific indirect effects)``. The test suite asserts
that identity rather than trusting it.

Inference on the products uses **case-resampled bootstrap** intervals; the
Sobel/delta-method z is reported alongside only because reviewers still ask
for it, with an explicit note that it assumes a normal sampling distribution
the product does not have.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

from .bootstrap import (EffectPlan, ci_from_boots, jackknife_acceleration,
                        percentile_ci, run_bootstrap)
from .dataio import Design
from .linalg import GramCache, SingularDesignError
from .model import (Regression, breusch_pagan, fit_ols, influence_summary,
                    mean, sd, vif_table)
from .special import norm_sf

__all__ = ["Effect", "Contrast", "MediationResult", "analyze"]


class Effect:
    """One estimated effect (total, direct, specific indirect, or their sum)."""

    def __init__(self, key: str, label: str, kind: str, estimate: float):
        self.key = key
        self.label = label
        self.kind = kind                     # total | direct | indirect | indirect_total
        self.estimate = estimate
        self.se: float = float("nan")        # bootstrap SD (indirect) or analytic SE
        self.ci_lo: float = float("nan")
        self.ci_hi: float = float("nan")
        self.ci_method: str = ""
        self.p: float = float("nan")         # analytic p (direct/total) — see note
        self.delta_se: float = float("nan")  # Sobel / delta-method SE
        self.delta_z: float = float("nan")
        self.delta_p: float = float("nan")
        self.standardized: float = float("nan")
        self.components: List[Tuple[str, float, float]] = []
        self.warnings: List[str] = []

    @property
    def significant(self) -> bool:
        """Does the bootstrap interval exclude zero? (NaN interval -> False)"""
        if not (math.isfinite(self.ci_lo) and math.isfinite(self.ci_hi)):
            return False
        return (self.ci_lo > 0.0 and self.ci_hi > 0.0) or (self.ci_lo < 0.0 and self.ci_hi < 0.0)

    def to_dict(self) -> dict:
        d = {"key": self.key, "label": self.label, "kind": self.kind,
             "estimate": self.estimate, "se": self.se,
             "ci_lo": self.ci_lo, "ci_hi": self.ci_hi, "ci_method": self.ci_method,
             "excludes_zero": self.significant,
             "standardized": self.standardized}
        if self.components:
            d["components"] = [{"term": n, "estimate": v, "se": s}
                               for n, v, s in self.components]
        if math.isfinite(self.delta_z):
            d["sobel_z"] = self.delta_z
            d["sobel_p"] = self.delta_p
            d["sobel_se"] = self.delta_se
        if math.isfinite(self.p):
            d["p"] = self.p
        if self.warnings:
            d["warnings"] = list(self.warnings)
        return d


class Contrast:
    """Difference between two specific indirect effects."""

    def __init__(self, label: str, estimate: float, ci_lo: float, ci_hi: float):
        self.label = label
        self.estimate = estimate
        self.ci_lo = ci_lo
        self.ci_hi = ci_hi

    @property
    def significant(self) -> bool:
        if not (math.isfinite(self.ci_lo) and math.isfinite(self.ci_hi)):
            return False
        return (self.ci_lo > 0.0 and self.ci_hi > 0.0) or (self.ci_lo < 0.0 and self.ci_hi < 0.0)

    def to_dict(self) -> dict:
        return {"label": self.label, "estimate": self.estimate,
                "ci_lo": self.ci_lo, "ci_hi": self.ci_hi,
                "excludes_zero": self.significant}


class MediationResult:
    def __init__(self) -> None:
        self.design: Optional[Design] = None
        self.serial = False
        self.conf = 0.95
        self.n_boot = 0
        self.seed = 0
        self.ci_method = "percentile"
        self.robust: Optional[str] = None
        self.boot_ok = 0
        self.boot_failed = 0
        self.m_regressions: List[Regression] = []
        self.y_regression: Optional[Regression] = None
        self.total_regression: Optional[Regression] = None
        self.effects: List[Effect] = []
        self.contrasts: List[Contrast] = []
        self.proportion_mediated: float = float("nan")
        self.proportion_note: str = ""
        self.standardized_kind: str = ""
        self.vif: List[Tuple[str, float]] = []
        self.bp_test: Optional[Tuple[float, int, float]] = None
        self.influence: Optional[Tuple[int, float, List[Tuple[int, float]]]] = None
        self.warnings: List[str] = []
        self.notes: List[str] = []

    # convenience accessors -------------------------------------------------
    def effect(self, kind: str) -> Optional[Effect]:
        for e in self.effects:
            if e.kind == kind:
                return e
        return None

    @property
    def indirect_effects(self) -> List[Effect]:
        return [e for e in self.effects if e.kind == "indirect"]

    def to_dict(self) -> dict:
        d = self.design
        out = {
            "model": {
                "type": "serial" if self.serial else "parallel",
                "x": d.x_name if d else None,
                "x_coding": d.x_label if d else None,
                "mediators": [nm for nm, _ in d.mediators] if d else [],
                "y": d.y_name if d else None,
                "covariates": [nm for nm, _ in d.covariates] if d else [],
            },
            "sample": {
                "n_rows_in_file": d.n_total if d else 0,
                "n_analysed": d.n_used if d else 0,
                "missing_by_column": [{"column": c, "n_missing": n}
                                      for c, n in (d.missing_by_column if d else [])],
            },
            "settings": {
                "confidence": self.conf,
                "bootstrap_requested": self.n_boot,
                "bootstrap_used": self.boot_ok,
                "bootstrap_failed": self.boot_failed,
                "ci_method": self.ci_method,
                "seed": self.seed,
                "se_type": self.robust or "classical",
            },
            "regressions": ([r.to_dict() for r in self.m_regressions]
                            + ([self.y_regression.to_dict()] if self.y_regression else [])
                            + ([self.total_regression.to_dict()] if self.total_regression else [])),
            "effects": [e.to_dict() for e in self.effects],
            "contrasts": [c.to_dict() for c in self.contrasts],
            "proportion_mediated": self.proportion_mediated,
            "proportion_mediated_note": self.proportion_note,
            "standardization": self.standardized_kind,
            "diagnostics": {
                "vif": [{"term": t, "vif": v} for t, v in self.vif],
                "breusch_pagan": ({"lm": self.bp_test[0], "df": self.bp_test[1],
                                   "p": self.bp_test[2]} if self.bp_test else None),
                "cooks_d": ({"n_above_cutoff": self.influence[0],
                             "cutoff": self.influence[1],
                             "top": [{"row": r, "d": v} for r, v in self.influence[2]]}
                            if self.influence else None),
            },
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }
        return out


def _paths(k: int, serial: bool) -> List[Tuple[int, ...]]:
    if not serial:
        return [(j,) for j in range(k)]
    out: List[Tuple[int, ...]] = []
    for length in range(1, k + 1):
        for combo in combinations(range(k), length):
            out.append(combo)
    return out


def _path_label(path: Sequence[int], x: str, m_names: Sequence[str], y: str) -> str:
    return " → ".join([x] + [m_names[i] for i in path] + [y])


def _delta_se(values: Sequence[float], ses: Sequence[float]) -> float:
    """First-order delta-method SE of a product of independent estimates.

    For two factors this is exactly Sobel's SE, ``sqrt(b^2 sa^2 + a^2 sb^2)``.
    The factors of any one path always come from *different* regressions
    (a from the mediator equation, b from the outcome equation), which is the
    independence the delta method leans on.
    """
    total = 0.0
    for j in range(len(values)):
        partial = 1.0
        for i in range(len(values)):
            if i != j:
                partial *= values[i]
        total += (partial * ses[j]) ** 2
    return math.sqrt(total) if total >= 0 else float("nan")


def analyze(design: Design,
            serial: bool = False,
            conf: float = 0.95,
            n_boot: int = 5000,
            seed: int = 20260731,
            ci_method: str = "percentile",
            robust: Optional[str] = None,
            jobs: int = 1,
            diagnostics: bool = True) -> MediationResult:
    """Fit the mediation model and return every reported quantity."""
    res = MediationResult()
    res.design = design
    res.serial = serial
    res.conf = conf
    res.n_boot = n_boot
    res.seed = seed
    res.ci_method = ci_method
    res.robust = robust
    res.notes.extend(design.notes)

    x_name = design.x_name
    y_name = design.y_name
    m_names = [nm for nm, _ in design.mediators]
    k = len(m_names)
    xcol = (x_name, design.x)
    mcols = list(design.mediators)
    covs = list(design.covariates)

    if serial and k < 2:
        raise ValueError("직렬(serial) 매개모형은 매개변수가 2개 이상이어야 합니다.")

    # --- reported regressions (stable QR path) ---------------------------
    m_regs: List[Regression] = []
    for j in range(k):
        preds = [xcol] + (mcols[:j] if serial else []) + covs
        m_regs.append(fit_ols(m_names[j], mcols[j][1], preds, conf, robust))
    y_reg = fit_ols(y_name, design.y, [xcol] + mcols + covs, conf, robust)
    total_reg = fit_ols(y_name, design.y, [xcol] + covs, conf, robust)
    res.m_regressions = m_regs
    res.y_regression = y_reg
    res.total_regression = total_reg

    # --- path coefficients ----------------------------------------------
    a = [r.coef(x_name) for r in m_regs]
    b = [y_reg.coef(nm) for nm in m_names]
    c_direct = y_reg.coef(x_name)
    c_total = total_reg.coef(x_name)
    d_coef: Dict[Tuple[int, int], object] = {}
    if serial:
        for j in range(k):
            for i in range(j):
                d_coef[(j, i)] = m_regs[j].coef(m_names[i])

    paths = _paths(k, serial)

    # --- effect point estimates ------------------------------------------
    total_eff = Effect("total", "총효과 c (%s → %s)" % (x_name, y_name), "total",
                       c_total.estimate)
    total_eff.se, total_eff.ci_lo, total_eff.ci_hi = c_total.se, c_total.ci_lo, c_total.ci_hi
    total_eff.p = c_total.p
    total_eff.ci_method = "회귀 t 구간"

    direct_eff = Effect("direct", "직접효과 c' (%s → %s, 매개변수 통제)" % (x_name, y_name),
                        "direct", c_direct.estimate)
    direct_eff.se, direct_eff.ci_lo, direct_eff.ci_hi = c_direct.se, c_direct.ci_lo, c_direct.ci_hi
    direct_eff.p = c_direct.p
    direct_eff.ci_method = "회귀 t 구간"

    indirect_effects: List[Effect] = []
    for pi, path in enumerate(paths):
        vals = [a[path[0]].estimate]
        ses = [a[path[0]].se]
        comps = [("a_%d (%s → %s)" % (path[0] + 1, x_name, m_names[path[0]]),
                  a[path[0]].estimate, a[path[0]].se)]
        for t in range(len(path) - 1):
            cf = d_coef[(path[t + 1], path[t])]
            vals.append(cf.estimate)          # type: ignore[attr-defined]
            ses.append(cf.se)                 # type: ignore[attr-defined]
            comps.append(("d_%d%d (%s → %s)" % (path[t + 1] + 1, path[t] + 1,
                                                m_names[path[t]], m_names[path[t + 1]]),
                          cf.estimate, cf.se))  # type: ignore[attr-defined]
        last = path[-1]
        vals.append(b[last].estimate)
        ses.append(b[last].se)
        comps.append(("b_%d (%s → %s)" % (last + 1, m_names[last], y_name),
                      b[last].estimate, b[last].se))
        est = 1.0
        for v in vals:
            est *= v
        eff = Effect("indirect_%d" % (pi + 1),
                     "간접효과 %s" % _path_label(path, x_name, m_names, y_name),
                     "indirect", est)
        eff.components = comps
        eff.delta_se = _delta_se(vals, ses)
        if math.isfinite(eff.delta_se) and eff.delta_se > 0:
            eff.delta_z = est / eff.delta_se
            eff.delta_p = 2.0 * norm_sf(abs(eff.delta_z))
        indirect_effects.append(eff)

    total_ind = Effect("indirect_total", "총 간접효과 (모든 경로 합)", "indirect_total",
                       math.fsum(e.estimate for e in indirect_effects))

    res.effects = [total_eff, direct_eff] + indirect_effects + (
        [total_ind] if len(indirect_effects) > 1 else [])

    # --- bootstrap --------------------------------------------------------
    cols = [[1.0] * design.n_used, list(design.x)]
    for _, v in mcols:
        cols.append(list(v))
    for _, v in covs:
        cols.append(list(v))
    cols.append(list(design.y))
    y_idx = len(cols) - 1
    cov_idx = list(range(2 + k, 2 + k + len(covs)))

    m_pred, m_out, m_x_pos, m_prior_pos = [], [], [], []
    for j in range(k):
        pred = [0, 1] + ([2 + i for i in range(j)] if serial else []) + cov_idx
        m_pred.append(pred)
        m_out.append(2 + j)
        m_x_pos.append(1)
        m_prior_pos.append({i: 2 + i for i in range(j)} if serial else {})
    plan = EffectPlan(
        k, serial, m_pred, m_out, m_x_pos, m_prior_pos,
        [0, 1] + [2 + i for i in range(k)] + cov_idx, y_idx, 1,
        [2 + i for i in range(k)],
        [0, 1] + cov_idx, y_idx, 1,
        paths,
    )

    observed_vec = [c_total.estimate, c_direct.estimate] + \
                   [e.estimate for e in indirect_effects] + [total_ind.estimate]

    boot = None
    cache: Optional[GramCache] = None
    if n_boot > 0:
        try:
            cache = GramCache(cols)
        except SingularDesignError as exc:
            res.warnings.append("부트스트랩을 준비하지 못했습니다(%s). 구간은 계산되지 않습니다." % exc)
        if cache is not None:
            # Cross-check the fast Cholesky path against the reported QR fit.
            check = plan.compute(cache, cache.full_acc())
            if check is not None:
                scale = max(1.0, max(abs(v) for v in observed_vec))
                worst = max(abs(cv - ov) for cv, ov in zip(check, observed_vec)) / scale
                if worst > 1e-6:
                    res.warnings.append(
                        "수치 점검: 빠른 부트스트랩 경로와 기본 회귀의 추정치가 %.2g 만큼 어긋납니다 "
                        "— 변수 간 공선성이 심할 수 있으니 결과를 신중히 해석하세요." % worst)
            boot = run_bootstrap(cache, plan, n_boot, seed, jobs)
            res.boot_ok = boot.n_ok
            res.boot_failed = boot.failed

    if boot is not None and boot.n_ok > 0:
        eff_list = indirect_effects + ([total_ind] if len(indirect_effects) > 1 else [])
        for offset, eff in enumerate(eff_list):
            stat_index = 2 + offset if offset < len(indirect_effects) else 2 + len(indirect_effects)
            samples = boot.columns[stat_index]
            acc = None
            if ci_method == "bca" and cache is not None:
                acc = jackknife_acceleration(cache, plan, stat_index)
            lo, hi, warns = ci_from_boots(samples, eff.estimate, conf, ci_method, acc)
            eff.ci_lo, eff.ci_hi = lo, hi
            eff.ci_method = {"percentile": "백분위 부트스트랩",
                             "bc": "편향보정(BC) 부트스트랩",
                             "bca": "BCa 부트스트랩"}[ci_method]
            eff.se = sd(samples)
            eff.warnings.extend(warns)
        # contrasts between specific indirect effects
        if len(indirect_effects) >= 2:
            for i, j in combinations(range(len(indirect_effects)), 2):
                diffs = [bi - bj for bi, bj in zip(boot.columns[2 + i], boot.columns[2 + j])]
                est = indirect_effects[i].estimate - indirect_effects[j].estimate
                lo, hi, _ = ci_from_boots(diffs, est, conf, ci_method, None)
                res.contrasts.append(Contrast(
                    "%s − %s" % (indirect_effects[i].label.replace("간접효과 ", ""),
                                 indirect_effects[j].label.replace("간접효과 ", "")),
                    est, lo, hi))
    elif n_boot > 0:
        res.warnings.append(
            "부트스트랩 재표본이 모두 실패해 간접효과 신뢰구간을 계산하지 못했습니다 "
            "(표본이 너무 작거나 변수가 거의 상수일 때 발생합니다).")

    if boot is not None and boot.failed:
        share = boot.failed / max(1, boot.requested)
        msg = ("부트스트랩 재표본 %d/%d개가 특이행렬로 실패해 제외했습니다."
               % (boot.failed, boot.requested))
        if share > 0.05:
            msg += " 실패율이 5%를 넘어 구간이 왜곡됐을 수 있습니다 — 표본 수나 변수 구성을 확인하세요."
        res.warnings.append(msg)

    # --- effect sizes -----------------------------------------------------
    sd_x, sd_y = sd(design.x), sd(design.y)
    if math.isfinite(sd_y) and sd_y > 0:
        # Hayes' rule: standardizing a dichotomous X is not meaningful (its SD
        # depends on the group split), so a 0/1 X gets the *partially*
        # standardized effect — the effect in SDs of Y per group change.
        if design.x_kind in ("dummy", "binary"):
            res.standardized_kind = "부분표준화 (효과 ÷ SD(Y); X가 이분형이라 X는 표준화하지 않음)"
            ratio = 1.0 / sd_y
        elif math.isfinite(sd_x) and sd_x > 0:
            res.standardized_kind = "완전표준화 (효과 × SD(X) ÷ SD(Y))"
            ratio = sd_x / sd_y
        else:
            ratio = float("nan")
        if math.isfinite(ratio):
            for e in res.effects:
                e.standardized = e.estimate * ratio

    ind_total_val = total_ind.estimate
    c_val = c_total.estimate
    if abs(c_val) < 1e-12:
        res.proportion_note = "총효과가 0에 가까워 매개비율은 의미가 없습니다."
    elif ind_total_val * c_val < 0:
        res.proportion_note = ("간접효과와 총효과의 부호가 반대입니다(억제/inconsistent mediation). "
                               "매개비율(%)은 해석 불가라 계산하지 않았습니다.")
    elif abs(ind_total_val) > abs(c_val):
        res.proportion_mediated = ind_total_val / c_val
        res.proportion_note = ("간접효과가 총효과보다 커서 매개비율이 100%를 넘습니다 "
                               "— 직접효과가 반대 부호일 때 생기며 그대로 보고하면 오해를 부릅니다.")
    else:
        res.proportion_mediated = ind_total_val / c_val

    # --- diagnostics ------------------------------------------------------
    if diagnostics:
        preds_y = [xcol] + mcols + covs
        if len(preds_y) >= 2:
            res.vif = vif_table(preds_y)
        res.bp_test = breusch_pagan(y_reg)
        res.influence = influence_summary(y_reg)

    res.warnings.extend(_model_warnings(design, res, k, serial))
    return res


def _model_warnings(design: Design, res: MediationResult, k: int,
                    serial: bool) -> List[str]:
    w: List[str] = []
    n = design.n_used
    if n < 30:
        w.append("표본이 %d명으로 매우 적습니다. 부트스트랩 신뢰구간이 불안정하니 "
                 "탐색적 결과로만 쓰세요(간접효과 검정은 보통 N≥50, 권장 N≥100)." % n)
    elif n < 50:
        w.append("표본이 %d명으로 작습니다(간접효과 부트스트랩은 보통 N≥50 권장)." % n)
    p_y = res.y_regression.p if res.y_regression else 0
    if n < 10 * p_y:
        w.append("추정 계수(%d개) 대비 표본(%d)이 적습니다 — 사례:변수 비가 10:1 미만입니다."
                 % (p_y, n))
    if len({v for v in design.y}) == 2:
        w.append("종속변수 %s 가 2수준(이분형)입니다. 선형회귀 기반 매개분석은 확률 차이를 "
                 "모형화하므로 계수 해석에 주의하고, 로지스틱 기반 매개분석(또는 인과매개분석)을 "
                 "함께 검토하세요." % design.y_name)
    for nm, vals in design.mediators:
        if len({v for v in vals}) == 2:
            w.append("매개변수 %s 가 2수준(이분형)입니다 — a 경로가 확률 차이로 해석됩니다." % nm)
    if res.vif:
        bad = [(t, v) for t, v in res.vif if not math.isfinite(v) or v >= 10.0]
        if bad:
            w.append("다중공선성 경고 — VIF ≥ 10: %s. 겹치는 변수를 정리하면 경로계수가 안정됩니다."
                     % ", ".join("%s=%s" % (t, "∞" if not math.isfinite(v) else "%.1f" % v)
                                 for t, v in bad))
    if res.bp_test and math.isfinite(res.bp_test[2]) and res.bp_test[2] < 0.05:
        w.append("잔차 이분산 가능성(Breusch–Pagan p=%.3f). 부트스트랩 구간은 비교적 견고하지만, "
                 "경로계수의 표준오차는 --robust hc3 로 다시 확인해 보세요." % res.bp_test[2])
    # 4/n flags ~5-10% of rows even in clean data, so it is reported in the
    # diagnostics table but only *warned* about when a point is influential by
    # the stricter, conventional D > 0.5 rule.
    if res.influence and res.influence[2]:
        strong = [(r, d) for r, d in res.influence[2] if d > 0.5]
        if strong:
            w.append("강한 영향점이 있습니다(Cook's D > 0.5): %s. 이 관측치를 빼고도 결론이 "
                     "유지되는지 반드시 확인하세요(행 번호는 결측 제거 후 순번)."
                     % ", ".join("#%d(D=%.2f)" % (r + 1, d) for r, d in strong))
    if res.n_boot and res.n_boot < 1000:
        w.append("부트스트랩 반복이 %d회로 적습니다. 논문 보고용은 5000회 이상을 권합니다."
                 % res.n_boot)
    return w
