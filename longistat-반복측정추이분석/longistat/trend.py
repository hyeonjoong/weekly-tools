"""Trend over time — orthogonal polynomial contrasts and per-subject slopes.

The omnibus "시점(시간)" effect in :mod:`anova` answers *whether* the visit means
differ somewhere.  It does not answer the question a clinical reader actually
asks about a multi-visit outcome: **is the score marching steadily in one
direction, or did it move early and then plateau?**  Two designs with the same
omnibus F can tell completely different stories, and with 3+ visits the omnibus
test also throws away the ordering of the visits entirely — permuting the visit
labels leaves it unchanged.

Two complementary answers are produced here.

1. **Orthogonal polynomial contrasts** (linear, quadratic, cubic), the
   "Tests of Within-Subjects Contrasts" table of SPSS GLM.  Each row tests a
   *single* within-subject score, so no sphericity correction applies (there is
   no covariance structure left to be non-spherical, and ε ≡ 1) — which is *why*
   they are worth reporting when Mauchly rejects.  With arms present, each order
   also gets its group × contrast interaction: "did the two arms differ in their
   *linear* trend?".  That row carries ``g − 1`` numerator df, not 1 — the
   within-subject dimension is still 1, which is what the sphericity argument
   turns on.
   The contrasts are orthonormal and computed on the same complete-case matrix
   as the omnibus, so their sums of squares partition the time effect exactly
   (``Σ_c SS_c = SS_time``); the tests pin that identity on unbalanced arms.
   Only the first three orders are given names — with five or more visits the
   rest are pooled into one explicit ``잔여`` line rather than dropped, because
   a quartic can hold half the time effect and silently vanishing would make
   the printed rows look like the whole story.

2. **Per-subject slopes** (Frison & Pocock's summary-measure approach): fit an
   ordinary least-squares line to each subject's own observed points and treat
   the slopes as the data.  Two things make this worth having next to the
   contrasts — the units are interpretable (points of the outcome per unit of
   time, e.g. ISI points per week), and a subject with a missing middle visit
   still contributes, so it is not restricted to completers the way the
   contrast/ANOVA machinery is.

Visit spacing matters for both.  Real trials visit at week 0, 4, 12, 24, not on
a uniform grid, and fitting a "linear trend" as if 12→24 were one step the same
size as 0→4 is simply the wrong contrast.  ``time_values`` therefore takes the
real numeric schedule; when it is not given the visit labels are parsed for a
number, and only if *that* fails is equal spacing assumed — with a note saying
so, because the assumption changes the answer.  A label holding *two* numbers
(``방문1(0주)``) is treated as a failure, not as a schedule: reading the ordinal
off ``방문1/방문2/방문3`` turned a perfectly linear 0/4/24-week trajectory into a
significant "plateau", and 1, 2, 3 is increasing so nothing would have warned.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .basics import adjust, paired_t, student_t, welch_t
from .dataio import Panel
from .describe import ALL_LABEL
from .special import f_sf, t_ppf

__all__ = [
    "TrendEffect", "SlopeRow", "SlopeContrast", "TrendResult",
    "orthogonal_polynomials", "resolve_time_values", "trend_analysis",
    "trend_shape", "reported_p",
]

MAX_POLY_ORDER = 3

_ORDER_NAME = {1: "선형(linear)", 2: "이차(quadratic)", 3: "삼차(cubic)"}

# Same shape as dataio's label scanner: the first signed number inside a label
# such as "4주", "wk12", "Week -1".
_NUM_IN_LABEL = re.compile(r"[-+]?\d+(?:\.\d+)?")


# --------------------------------------------------------------------------
# contrasts
# --------------------------------------------------------------------------

def orthogonal_polynomials(x: Sequence[float], max_order: int
                           ) -> List[List[float]]:
    """``k × m`` matrix of orthonormal polynomial contrasts over positions *x*.

    Column ``c`` is the degree ``c+1`` polynomial in *x*, made orthogonal to the
    constant vector and to every lower-degree column, then scaled to unit
    length.  Equal spacing reproduces the classical integer contrast weights
    (linear ``-1, 0, +1``; quadratic ``+1, -2, +1``) up to that scaling.

    *x* is centred and rescaled before the powers are formed: a schedule given
    as epoch days or as 0/4/8/12 *weeks squared-cubed* otherwise loses precision
    in the third power long before the statistics do.
    """
    k = len(x)
    if k < 2:
        raise ValueError("시점이 2개 이상이어야 합니다.")
    if len(set(x)) < 2:
        raise ValueError("시점 값이 모두 같아 추세를 계산할 수 없습니다.")
    m = max(1, min(int(max_order), k - 1))

    centre = math.fsum(x) / k
    spread = max(abs(v - centre) for v in x) or 1.0
    z = [(v - centre) / spread for v in x]

    basis: List[List[float]] = [[1.0] * k]          # constant, dropped at the end
    for degree in range(1, m + 1):
        col = [zi ** degree for zi in z]
        # Two projection passes (repeated Gram–Schmidt).  One pass is enough for
        # a normal visit schedule but loses orthogonality to ~1e-5 on a
        # pathological one (x = 0, .001, .002, 1000), and the SS partition this
        # module advertises as exact then holds only to that accuracy.
        for _ in range(2):
            for prev in basis:
                denom = math.fsum(p * p for p in prev)
                if denom <= 0:
                    continue
                coef = math.fsum(c * p for c, p in zip(col, prev)) / denom
                col = [c - coef * p for c, p in zip(col, prev)]
        norm = math.sqrt(math.fsum(c * c for c in col))
        if norm <= 1e-12:
            # Degenerate: fewer distinct visit values than the degree asked for.
            break
        basis.append([c / norm for c in col])
    cols = basis[1:]
    return [[col[j] for col in cols] for j in range(k)]


def resolve_time_values(times: Sequence[str], explicit: Optional[Sequence[float]],
                        notes: List[str]) -> Tuple[List[float], str]:
    """Numeric position of each visit, plus a human-readable provenance string.

    Order of preference: what the user passed, then a number parsed out of every
    visit label, then equal spacing.  A parsed schedule is only accepted when it
    is strictly increasing — ``기저 / 4주 / 8주`` yields no number for the first
    label, and ``V2 / V10`` parses fine but ``2, 10`` is exactly the schedule the
    user meant, so the rule is deliberately literal rather than clever.
    """
    k = len(times)
    if explicit is not None:
        vals = [float(v) for v in explicit]
        if len(vals) != k:
            raise ValueError(
                f"--time-values 는 시점 개수({k})와 같은 개수의 숫자여야 합니다 "
                f"(현재 {len(vals)}개).")
        if any(not math.isfinite(v) for v in vals):
            raise ValueError("--time-values 에 nan/inf 는 쓸 수 없습니다.")
        if any(b <= a for a, b in zip(vals, vals[1:])):
            raise ValueError(
                "--time-values 는 시점 순서대로 증가해야 합니다 "
                "(예: 기저 0, 4주 4, 12주 12).")
        return vals, "지정한 값"

    parsed: List[float] = []
    ambiguous = False
    for label in times:
        hits = _NUM_IN_LABEL.findall(label)
        if len(hits) != 1:
            # "방문1(0주)" holds both an ordinal and the real week.  Taking the
            # first one read 1, 2, 3 off a 0/4/24-week schedule and invented a
            # highly significant quadratic "plateau" — with no warning, because
            # 1, 2, 3 is perfectly increasing.  Refuse to guess.
            ambiguous = ambiguous or len(hits) > 1
            parsed = []
            break
        parsed.append(float(hits[0]))
    if len(parsed) == k and all(b > a for a, b in zip(parsed, parsed[1:])):
        return parsed, "시점 이름에서 읽음"

    if ambiguous:
        notes.append(
            "시점 이름에 숫자가 둘 이상 들어 있어(예: '방문1(0주)') 어느 쪽이 "
            "실제 간격인지 알 수 없습니다 — 등간격으로 가정했습니다. "
            "--time-values 로 실제 방문 간격을 지정하세요.")
    else:
        notes.append(
            "시점 간격을 등간격으로 가정했습니다 — 실제 방문 간격이 불규칙하면 "
            "(예: 0, 4, 12, 24주) --time-values 로 지정하세요. 선형·이차 추세의 "
            "값이 달라집니다.")
    return [float(i + 1) for i in range(k)], "등간격 가정"


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class TrendEffect:
    """One row of the within-subject contrast table."""

    order: int
    order_name: str
    scope: str                       # "시점" or "그룹 × 시점"
    ss: float
    df1: float
    df2: float
    f: float
    p_raw: float
    p_adj: float
    partial_eta2: float
    estimate: float                  # contrast estimate (orthonormal scale)
    se: float
    ci_low: float
    ci_high: float
    residual: bool = False           # pooled "everything above cubic" line


@dataclass
class SlopeRow:
    group: str
    n: int
    mean_slope: float
    sd: float
    ci_low: float
    ci_high: float
    t: float
    p: float
    min_points: int                  # fewest visits any subject contributed


@dataclass
class SlopeContrast:
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    slope_a: float
    slope_b: float
    diff: float
    ci_low: float
    ci_high: float
    p: float
    method: str
    effect: float
    effect_ci: Tuple[float, float]


@dataclass
class TrendResult:
    time_values: List[float]
    time_source: str
    time_unit: str
    n_complete: int
    effects: List[TrendEffect] = field(default_factory=list)
    slopes: List[SlopeRow] = field(default_factory=list)
    slope_contrasts: List[SlopeContrast] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

def _contrast_effects(matrix: Sequence[Sequence[float]],
                      labels: Sequence[str], order: Sequence[str],
                      poly: Sequence[Sequence[float]], alpha: float,
                      correction: str, n_report: Optional[int] = None
                      ) -> List[TrendEffect]:
    """Single-df F tests for each polynomial contrast (and its group interaction).

    Deliberately mirrors :func:`anova.rm_anova`: the time effect is the Type III
    hypothesis about the *unweighted* mean of the arm means, the interaction is
    the between-arm spread of the contrast score, and both share the pooled
    within-arm error of that one contrast.  Because each contrast carries a
    single degree of freedom its error term is its own, so the sphericity
    correction that applies to the omnibus does not apply here.
    """
    n = len(matrix)
    k = len(matrix[0])
    m = len(poly[0])
    top = m if n_report is None else max(1, min(n_report, m))
    idx_by_group = {gl: [i for i, g in enumerate(labels) if g == gl]
                    for gl in order}
    sizes = [len(idx_by_group[gl]) for gl in order]
    g = len(order)
    df_err = float(n - g)
    if df_err <= 0:
        return []
    weight = math.fsum(1.0 / s for s in sizes) / (g * g)

    # Subtracting the grand mean leaves every contrast score unchanged (the
    # columns are orthogonal to 1) but keeps the products in range for data
    # carrying a large offset, as contrast_scores() does for the omnibus.
    n_cells = n * k
    shift = math.fsum(math.fsum(row) for row in matrix) / n_cells
    # Scale for the "is this residual real, or is it rounding noise?" test.  It
    # must be the total SS of the *whole design*, as anova._make_effect uses:
    # comparing a contrast's error SS against that same contrast's own total is
    # self-referential and can never fire, so a perfectly linear panel reported
    # its cubic component as F = 3718, p < .001 off a residue of 1e-30.
    total_ss = math.fsum((v - shift) ** 2 for row in matrix for v in row)

    def _partition(c: int) -> Tuple[float, float, float, float]:
        """``(ss_time, ss_inter, ss_err, mu)`` for polynomial column *c*."""
        scores = [math.fsum((matrix[i][j] - shift) * poly[j][c]
                            for j in range(k)) for i in range(n)]
        grand_w = math.fsum(scores) / n
        ss_err = 0.0
        ss_inter = 0.0
        gmeans: List[float] = []
        for gl, size in zip(order, sizes):
            idx = idx_by_group[gl]
            gm = math.fsum(scores[i] for i in idx) / size
            gmeans.append(gm)
            ss_inter += size * (gm - grand_w) ** 2
            ss_err += math.fsum((scores[i] - gm) ** 2 for i in idx)
        mu = math.fsum(gmeans) / g
        return mu * mu / weight, ss_inter, ss_err, mu

    def _lines(idx: int, name: str, ss_time: float, ss_inter: float,
               ss_err: float, mu: float, scale: float, n_cols: int,
               residual: bool) -> List[TrendEffect]:
        df_e = df_err * n_cols
        ms_err = ss_err / df_e if df_e > 0 else float("nan")
        # Relative to the whole design, not to this contrast (see total_ss).
        usable = (ms_err > 0 and df_e > 0
                  and (ss_err > 1e-12 * abs(scale) if math.isfinite(scale)
                       else False))
        se = math.sqrt(ms_err * weight) if usable and not residual \
            else float("nan")
        crit = t_ppf(1.0 - alpha / 2.0, df_e) if usable else float("nan")

        def _one(scope: str, ss: float, df1: float, est: float,
                 err: float) -> TrendEffect:
            if usable and df1 > 0:
                f = (ss / df1) / ms_err
                p = f_sf(f, df1, df_e)
            else:
                f = p = float("nan")
            # ηp² is meaningless when the error term is rounding noise: a
            # constant-everywhere panel printed "ηp² = 1.000" beside "F = —".
            pe = (ss / (ss + ss_err) if usable and ss + ss_err > 0
                  else float("nan"))
            lo = est - crit * err if math.isfinite(err) else float("nan")
            hi = est + crit * err if math.isfinite(err) else float("nan")
            return TrendEffect(
                order=idx, order_name=name, scope=scope, ss=ss, df1=df1,
                df2=df_e, f=f, p_raw=p, p_adj=float("nan"), partial_eta2=pe,
                estimate=est, se=err, ci_low=lo, ci_high=hi, residual=residual)

        rows = [_one("시점", ss_time, float(n_cols), mu, se)]
        if g > 1:
            rows.append(_one("그룹 × 시점", ss_inter, float((g - 1) * n_cols),
                             float("nan"), float("nan")))
        return rows

    out: List[TrendEffect] = []
    for c in range(top):
        st, si, se_, mu = _partition(c)
        out.extend(_lines(c + 1, _ORDER_NAME.get(c + 1, f"{c + 1}차"),
                          st, si, se_, mu, total_ss, 1, residual=False))

    # Anything above the reported orders is pooled into one explicit residual
    # line.  Dropping it instead left the printed components summing to *less*
    # than the omnibus time effect with no hint that they should not — a 5-visit
    # design can hide 56 % of the time effect in the unreported quartic.
    if top < m:
        rest = [_partition(c) for c in range(top, m)]
        out.extend(_lines(
            0, f"{top + 1}차 이상(잔여)",
            math.fsum(r[0] for r in rest), math.fsum(r[1] for r in rest),
            math.fsum(r[2] for r in rest), float("nan"),
            total_ss, m - top, residual=True))

    # Adjust within each family separately: the time trends are one question
    # ("what shape?") and the interactions another ("does the shape differ?").
    # The residual is not a shape hypothesis, so it stays out of both families.
    for scope in ("시점", "그룹 × 시점"):
        family = [e for e in out if e.scope == scope and not e.residual]
        for eff, padj in zip(family,
                             adjust([e.p_raw for e in family], correction)):
            eff.p_adj = padj
    for eff in out:
        if eff.residual:
            eff.p_adj = eff.p_raw
    return out


def _subject_slopes(panel: Panel, tvals: Sequence[float]
                    ) -> Dict[str, List[Tuple[float, int]]]:
    """OLS slope of each subject's own observed points, grouped by arm label."""
    out: Dict[str, List[Tuple[float, int]]] = {}
    for i in range(panel.n_subjects):
        pts = [(tvals[j], float(v)) for j, v in enumerate(panel.values[i])
               if v is not None]
        if len(pts) < 2:
            continue
        tbar = math.fsum(t for t, _ in pts) / len(pts)
        # Rescale before squaring, then undo it on the slope.  Centring alone is
        # not enough: a schedule in the 1e200s made (t − t̄)² overflow and the
        # OverflowError escaped the CLI as a raw traceback.
        span = max(abs(t - tbar) for t, _ in pts) or 1.0
        sxx = math.fsum(((t - tbar) / span) ** 2 for t, _ in pts)
        if sxx <= 0:
            continue
        ybar = math.fsum(y for _, y in pts) / len(pts)
        sxy = math.fsum(((t - tbar) / span) * (y - ybar) for t, y in pts)
        slope = sxy / sxx / span
        if not math.isfinite(slope):
            continue
        label = panel.groups[i] if panel.groups is not None else ALL_LABEL
        out.setdefault(label, []).append((slope, len(pts)))
    return out


def trend_analysis(panel: Panel, time_values: Optional[Sequence[float]] = None,
                   alpha: float = 0.05, correction: str = "holm",
                   welch: bool = True, time_unit: str = "",
                   max_order: int = MAX_POLY_ORDER) -> Optional[TrendResult]:
    """Polynomial trend contrasts plus per-subject slopes.

    Returns ``None`` for two-timepoint designs, where the only possible trend is
    the change from baseline that section [5] already reports exactly.
    """
    if panel.n_times < 3:
        return None
    notes: List[str] = []
    tvals, source = resolve_time_values(panel.times, time_values, notes)

    cc = panel.complete_case()
    result = TrendResult(time_values=tvals, time_source=source,
                         time_unit=time_unit, n_complete=cc.n_subjects,
                         notes=notes)

    order = cc.group_labels() if cc.groups is not None else [""]
    labels = list(cc.groups) if cc.groups is not None else [""] * cc.n_subjects
    order = [gl for gl in order if gl in labels] or [""]
    if cc.n_subjects > len(order):
        try:
            # The full basis is always fitted; only the number of *named* rows
            # is capped, and whatever is left over is pooled into an explicit
            # residual line so the table still adds up to the omnibus.
            poly = orthogonal_polynomials(tvals, panel.n_times - 1)
            result.effects = _contrast_effects(
                cc.matrix(), labels, order, poly, alpha, correction,
                n_report=max_order)
            if len(poly[0]) > max_order:
                notes.append(
                    f"{max_order}차까지만 이름을 붙여 보고하고, 그보다 높은 "
                    "차수는 '잔여' 한 줄로 묶었습니다 (임상 결과지표에서 "
                    "해석되는 일이 드뭅니다) — 표의 SS 합은 여전히 위 ANOVA의 "
                    "시점 효과와 일치합니다.")
        except (ValueError, ArithmeticError):
            # Deliberately not interpolating the exception text: this note goes
            # into the text report and the JSON payload, and nothing else in
            # the tool lets an uncontrolled string reach output.
            notes.append(
                "시점 값이 중복되거나 수치가 비정상이어서 다항 추세 대비를 "
                "계산하지 못했습니다 (--time-values 를 확인하세요).")
    else:
        notes.append(
            "완전자료 대상이 부족해 다항 추세 대비를 계산하지 못했습니다 "
            "(개인 기울기는 가용사례로 계산합니다).")

    # ---- per-subject slopes (available cases, not just completers) --------
    by_group = _subject_slopes(panel, tvals)
    named = [g for g in panel.group_labels() if g in by_group] \
        if panel.groups is not None else []
    # Build (display label, entries) pairs rather than writing the pooled row
    # back into by_group: an arm literally named "전체" used to be overwritten by
    # the pooled row, printing a duplicate line with the wrong n and silently
    # dropping that arm from every between-arm slope contrast.
    scopes: List[Tuple[str, List[Tuple[float, int]]]] = []
    if len(named) > 1:
        pooled = [s for g in named for s in by_group[g]]
        if pooled:
            pool_label = ALL_LABEL if ALL_LABEL not in named else "전체(모든 군)"
            scopes.append((pool_label, pooled))
        scopes.extend((g, by_group[g]) for g in named)
    elif named:
        scopes = [(named[0], by_group[named[0]])]
    elif ALL_LABEL in by_group:
        scopes = [(ALL_LABEL, by_group[ALL_LABEL])]

    for label, entries in scopes:
        if len(entries) < 2:
            continue
        vals = [s for s, _ in entries]
        res = paired_t(vals, alpha)          # one-sample t against slope = 0
        result.slopes.append(SlopeRow(
            group=label, n=res.n, mean_slope=res.mean_diff, sd=res.sd_diff,
            ci_low=res.ci_low, ci_high=res.ci_high, t=res.t, p=res.p,
            min_points=min(np for _, np in entries)))

    for a in range(len(named)):
        for b in range(a + 1, len(named)):
            va = [s for s, _ in by_group[named[a]]]
            vb = [s for s, _ in by_group[named[b]]]
            if len(va) < 2 or len(vb) < 2:
                continue
            res = welch_t(va, vb, alpha) if welch else student_t(va, vb, alpha)
            result.slope_contrasts.append(SlopeContrast(
                group_a=named[a], group_b=named[b], n_a=res.n1, n_b=res.n2,
                slope_a=res.mean1, slope_b=res.mean2, diff=res.diff,
                ci_low=res.ci_low, ci_high=res.ci_high, p=res.p,
                method=res.name, effect=res.g, effect_ci=res.g_ci))

    if result.slopes and any(r.min_points < panel.n_times for r in result.slopes):
        notes.append(
            "개인 기울기는 관측된 시점만으로 적합했습니다 — 방문을 적게 마친 "
            "대상의 기울기도 같은 가중치로 평균에 들어갑니다.")
    if not result.effects and not result.slopes:
        return None
    return result


def reported_p(eff: TrendEffect) -> float:
    """The p-value the table prints — adjusted, falling back to raw.

    The verdict sentence used to read ``p_raw`` while the column beside it
    printed ``p_adj``, so a contrast at raw .022 / Holm .066 was shown without a
    star and then declared significant on the next line.
    """
    return eff.p_adj if math.isfinite(eff.p_adj) else eff.p_raw


def trend_shape(effects: Sequence[TrendEffect], alpha: float = 0.05) -> str:
    """One-line plain-language reading of the time trend, or '' when unclear."""
    time_eff = {e.order: e for e in effects
                if e.scope == "시점" and not e.residual}
    lin = time_eff.get(1)
    quad = time_eff.get(2)
    if lin is None or not math.isfinite(reported_p(lin)):
        return ""
    lin_sig = reported_p(lin) < alpha
    quad_sig = quad is not None and math.isfinite(reported_p(quad)) \
        and reported_p(quad) < alpha
    if lin_sig and quad_sig:
        return ("선형 + 이차 성분이 모두 유의합니다 — 한 방향으로 변하되 "
                "속도가 달라집니다(초기 변화 후 정체 등).")
    if lin_sig:
        return "선형 성분만 유의합니다 — 시점에 따라 일정한 방향으로 변합니다."
    if quad_sig:
        return ("이차 성분만 유의합니다 — 단조 변화가 아니라 꺾이는 형태입니다"
                "(반등·U자 등).")
    return "선형·이차 성분 모두 유의하지 않습니다 — 뚜렷한 추세 근거가 없습니다."
