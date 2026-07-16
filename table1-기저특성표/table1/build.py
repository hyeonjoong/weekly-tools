"""Assemble a Table 1 (baseline characteristics) from a Frame.

For each variable the builder auto-classifies continuous vs categorical, picks a
summary + hypothesis test with assumption checks, computes the two-group SMD,
and accounts for missing data. The output is a structured ``Table1`` object that
``render`` turns into Markdown / CSV / TSV / JSON.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import cat_tests, smd
from .dataio import Frame, classify, is_missing, parse_float
from .normality import shapiro_wilk
from .tests_stat import (
    kruskal_wallis,
    levene,
    mann_whitney_u,
    one_way_anova,
    students_t,
    welch_t,
)

__all__ = ["Options", "Table1", "ContinuousRow", "CategoricalRow", "build_table1"]


# --------------------------------------------------------------------------- #
# Bilingual note/warning catalog. Notes and warnings are rendered into the
# output table, so they must follow --lang (a Korean note under an English
# Table 1 was an adoption blocker for journal submission).
# --------------------------------------------------------------------------- #
_MSG = {
    "ko": {
        "shapiro_cap": "표본이 {n}개를 초과해 정규성 검정은 {n}개 부분표본으로 근사했습니다.",
        "normality_untestable": "정규성 검정 불가(각 그룹 n<3 또는 상수) → 평균±표준편차로 표시",
        "skip_lt2": "한 그룹의 관측치가 2개 미만 → 검정 생략",
        "skip_groups_lt2": "분석 가능한 그룹이 2개 미만 → 검정 생략",
        "anova_levene": "등분산 가정 위배(Levene p<{alpha:.2g}) → ANOVA 해석 주의 (Welch/비모수 고려)",
        "test_failed": "검정 계산 불가: {exc}",
        "stat_undefined": "검정 통계량이 정의되지 않음(예: 분산 0) → p값 생략",
        "chi_expected_low": "기대빈도 <5 셀 존재 → χ² 근사 주의(Fisher 권장)",
        "warn_group_missing": "그룹 값이 결측이라 제외한 행: {n}개",
        "warn_var_all_missing": "변수 '{var}' 는 값이 모두 결측이라 건너뜀",
        "warn_int_code": ("변수 '{var}' 는 정수값이 {n}종뿐이라 연속형(평균±SD)으로 "
                          "처리했습니다. 순서형/범주형 코드라면 '--categorical {var}' 를 쓰세요."),
        "warn_too_many_levels": ("변수 '{var}' 는 고유값이 {n}개로 너무 많아 "
                                 "(ID/자유텍스트로 판단) 건너뜀. 표에 넣으려면 "
                                 "'--categorical {var}' 또는 '--max-levels' 를 쓰세요."),
    },
    "en": {
        "shapiro_cap": ("sample exceeded {n}; normality was tested on a "
                        "{n}-point subsample."),
        "normality_untestable": ("normality not testable (each group n<3 or "
                                 "constant) → shown as mean±SD"),
        "skip_lt2": "a group has <2 observations → test skipped",
        "skip_groups_lt2": "fewer than 2 analyzable groups → test skipped",
        "anova_levene": ("equal-variance assumption violated (Levene "
                         "p<{alpha:.2g}) → interpret ANOVA with caution "
                         "(consider Welch/nonparametric)"),
        "test_failed": "test could not be computed: {exc}",
        "stat_undefined": ("test statistic undefined (e.g. zero variance) → "
                           "p-value omitted"),
        "chi_expected_low": ("cells with expected count <5 → chi-square "
                             "approximation unreliable (Fisher recommended)"),
        "warn_group_missing": "rows excluded for a missing group value: {n}",
        "warn_var_all_missing": "variable '{var}' skipped: all values missing",
        "warn_int_code": ("variable '{var}' had only {n} distinct integer "
                          "values and was treated as continuous (mean±SD). If "
                          "it is an ordinal/categorical code, use "
                          "'--categorical {var}'."),
        "warn_too_many_levels": ("variable '{var}' has {n} distinct values "
                                 "(treated as an ID/free-text column) and was "
                                 "skipped. To include it, use "
                                 "'--categorical {var}' or '--max-levels'."),
    },
}


def _msg(lang: str, key: str, **kw) -> str:
    table = _MSG.get(lang or "ko", _MSG["ko"])
    return table[key].format(**kw)


@dataclass
class Options:
    group_col: str
    var_cols: Optional[List[str]] = None       # None -> every non-group column
    continuous: List[str] = field(default_factory=list)   # forced continuous
    categorical: List[str] = field(default_factory=list)  # forced categorical
    cat_max_levels: int = 2
    max_display_levels: int = 20   # skip auto-detected categoricals with more
                                   # distinct levels than this (likely IDs)
    display: str = "auto"          # auto | mean | median | both
    alpha_norm: float = 0.05
    force_fisher: bool = False
    pct: str = "col"               # col | row
    missing_as_level: bool = False
    overall: bool = True
    decimals: int = 1
    show_pvalue: bool = True        # False -> omit the p-value column (CONSORT: RCT
                                    # baseline p-values are discouraged; use SMD)
    show_range: bool = False        # True -> append (min-max) to continuous cells
    lang: str = "ko"               # ko | en  (rendered label language)
    labels: Dict[str, str] = field(default_factory=dict)  # column -> display name


@dataclass
class GroupStat:
    n: int = 0
    n_missing: int = 0
    mean: float = float("nan")
    sd: float = float("nan")
    median: float = float("nan")
    q1: float = float("nan")
    q3: float = float("nan")
    vmin: float = float("nan")
    vmax: float = float("nan")


@dataclass
class ContinuousRow:
    name: str
    per_group: List[GroupStat]
    overall: GroupStat
    display: str                 # mean | median | both
    test_name: str
    pvalue: Optional[float]
    smd: Optional[float]
    n_missing_total: int
    notes: List[str] = field(default_factory=list)
    kind: str = "continuous"


@dataclass
class CatLevel:
    label: str
    counts: List[int]            # per group (aligned to group order)
    overall: int


@dataclass
class CategoricalRow:
    name: str
    levels: List[CatLevel]
    denom_per_group: List[int]   # non-missing n per group (percent basis for pct=col)
    overall_denom: int
    missing_per_group: List[int]
    test_name: str
    pvalue: Optional[float]
    smd: Optional[float]
    n_missing_total: int
    pct: str
    notes: List[str] = field(default_factory=list)
    kind: str = "categorical"


@dataclass
class Table1:
    groups: List[str]
    group_sizes: List[int]
    overall_size: int
    rows: List[object]
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _quantile(sorted_x: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (type 7 / numpy default)."""
    n = len(sorted_x)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_x[0]
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return sorted_x[lo]
    return sorted_x[lo] + (h - lo) * (sorted_x[hi] - sorted_x[lo])


def _summ(values: Sequence[float], n_missing: int) -> GroupStat:
    n = len(values)
    st = GroupStat(n=n, n_missing=n_missing)
    if n == 0:
        return st
    s = sorted(values)
    m = sum(values) / n
    st.mean = m
    st.median = _quantile(s, 0.5)
    st.q1 = _quantile(s, 0.25)
    st.q3 = _quantile(s, 0.75)
    st.vmin = s[0]
    st.vmax = s[-1]
    st.sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1)) if n >= 2 else 0.0
    return st


_SHAPIRO_MAX_N = 5000  # Royston (1992) AS R94 validity ceiling


def _subsample(values: Sequence[float], cap: int) -> List[float]:
    """Evenly-spaced deterministic subsample preserving distribution shape."""
    n = len(values)
    if n <= cap:
        return list(values)
    s = sorted(values)
    step = n / cap
    return [s[min(n - 1, int(i * step))] for i in range(cap)]


def _is_nonnormal(group_vals: Sequence[Sequence[float]], alpha: float,
                  notes: List[str], lang: str = "ko") -> Optional[bool]:
    """True if any testable group rejects normality; None if none testable."""
    tested = False
    nonnormal = False
    capped = False
    for gi, g in enumerate(group_vals):
        if len(g) < 3:
            continue
        sample = g
        if len(g) > _SHAPIRO_MAX_N:
            # Shapiro-Wilk's p-value is only valid to n=5000; above that the
            # Royston polynomials are extrapolated. Subsample (as SciPy does)
            # rather than silently report an invalid p-value.
            sample = _subsample(g, _SHAPIRO_MAX_N)
            capped = True
        try:
            _w, p = shapiro_wilk(sample)
        except ValueError:
            # constant group: no evidence of non-normality from this group
            continue
        tested = True
        if p < alpha:
            nonnormal = True
    if capped:
        notes.append(_msg(lang, "shapiro_cap", n=_SHAPIRO_MAX_N))
    if not tested:
        notes.append(_msg(lang, "normality_untestable"))
        return None
    return nonnormal


# --------------------------------------------------------------------------- #
# continuous
# --------------------------------------------------------------------------- #
def _continuous_row(name: str, group_cells: List[List[str]], opt: Options
                    ) -> ContinuousRow:
    per_vals: List[List[float]] = []
    per_missing: List[int] = []
    for cells in group_cells:
        vals = []
        miss = 0
        for c in cells:
            if is_missing(c):
                miss += 1
                continue
            f = parse_float(c)
            if f is None:
                miss += 1  # non-numeric in a continuous column counts as missing
            else:
                vals.append(f)
        per_vals.append(vals)
        per_missing.append(miss)

    per_group = [_summ(v, m) for v, m in zip(per_vals, per_missing)]
    all_vals = [v for g in per_vals for v in g]
    overall = _summ(all_vals, sum(per_missing))

    notes: List[str] = []
    nonnormal = _is_nonnormal(per_vals, opt.alpha_norm, notes, opt.lang)
    use_nonparam = bool(nonnormal)  # None -> False (default parametric)

    if opt.display == "auto":
        display = "median" if use_nonparam else "mean"
    else:
        display = opt.display

    test_name = "—"
    pvalue: Optional[float] = None
    k = len(per_vals)
    sizes = [len(v) for v in per_vals]
    try:
        if k == 2:
            if min(sizes) < 2:
                notes.append(_msg(opt.lang, "skip_lt2"))
            elif use_nonparam:
                res = mann_whitney_u(per_vals[0], per_vals[1])
                test_name, pvalue = "Mann-Whitney U", res.pvalue
            else:
                lev = levene(per_vals)
                if lev.pvalue >= opt.alpha_norm:
                    res = students_t(per_vals[0], per_vals[1])
                    test_name = "Student t"
                else:
                    res = welch_t(per_vals[0], per_vals[1])
                    test_name = "Welch t"
                pvalue = res.pvalue
        else:  # k >= 3
            if any(s < 1 for s in sizes) or sum(1 for s in sizes if s >= 1) < 2:
                notes.append(_msg(opt.lang, "skip_groups_lt2"))
            elif use_nonparam:
                res = kruskal_wallis(per_vals)
                test_name, pvalue = "Kruskal-Wallis", res.pvalue
            else:
                res = one_way_anova(per_vals)
                test_name, pvalue = "One-way ANOVA", res.pvalue
                if all(s >= 2 for s in sizes):
                    lev = levene(per_vals)
                    if lev.pvalue < opt.alpha_norm:
                        notes.append(_msg(opt.lang, "anova_levene",
                                          alpha=opt.alpha_norm))
    except (ValueError, ZeroDivisionError) as exc:
        notes.append(_msg(opt.lang, "test_failed", exc=exc))
        test_name, pvalue = "—", None

    if pvalue is not None and (math.isnan(pvalue) or math.isinf(pvalue)):
        notes.append(_msg(opt.lang, "stat_undefined"))
        pvalue = None

    smd_val = None
    if k == 2:
        smd_val = smd.continuous_smd(per_vals[0], per_vals[1])

    return ContinuousRow(
        name=name, per_group=per_group, overall=overall, display=display,
        test_name=test_name, pvalue=pvalue, smd=smd_val,
        n_missing_total=sum(per_missing), notes=notes)


# --------------------------------------------------------------------------- #
# categorical
# --------------------------------------------------------------------------- #
def _order_levels(labels: Sequence[str]) -> List[str]:
    uniq = list(dict.fromkeys(labels))
    parsed = [parse_float(x) for x in uniq]
    if all(p is not None for p in parsed):
        return [x for _, x in sorted(zip(parsed, uniq), key=lambda t: t[0])]
    return sorted(uniq)


def _categorical_row(name: str, group_cells: List[List[str]], opt: Options
                     ) -> CategoricalRow:
    k = len(group_cells)
    missing_per_group = [0] * k
    # collect observed level labels
    seen: List[str] = []
    for cells in group_cells:
        for c in cells:
            if is_missing(c):
                continue
            seen.append(c.strip())
    real_levels = _order_levels(seen)

    # counts for the real (observed) levels only
    counts = [[0] * k for _ in real_levels]
    level_pos = {lab: i for i, lab in enumerate(real_levels)}
    for gi, cells in enumerate(group_cells):
        for c in cells:
            if is_missing(c):
                missing_per_group[gi] += 1
                continue
            counts[level_pos[c.strip()]][gi] += 1

    # Optional synthetic "missing" level, tracked SEPARATELY so a real category
    # literally named "(결측)" cannot merge with the sentinel (the old code keyed
    # it by string and silently folded real values into the missing row).
    n_real = len(real_levels)
    missing_label: Optional[str] = None
    if opt.missing_as_level and any(missing_per_group):
        lbl = "(결측)"
        while lbl in level_pos:  # disambiguate against an identically-named level
            lbl += " "
        missing_label = lbl
        counts.append(list(missing_per_group))
    levels = list(real_levels) + ([missing_label] if missing_label else [])

    denom_per_group = [sum(counts[li][gi] for li in range(len(levels)))
                       for gi in range(k)]
    overall_denom = sum(denom_per_group)
    cat_levels = [
        CatLevel(label=levels[li],
                 counts=[counts[li][gi] for gi in range(k)],
                 overall=sum(counts[li][gi] for gi in range(k)))
        for li in range(len(levels))
    ]

    notes: List[str] = []
    test_name = "—"
    pvalue: Optional[float] = None

    # Contingency table for the test: only the real (observed) levels, never the
    # synthetic missing row, so the test reflects association among observed
    # categories.
    test_levels = list(range(n_real))
    table = [[counts[li][gi] for gi in range(k)] for li in test_levels]
    try:
        is_2x2 = len(table) == 2 and k == 2
        if is_2x2 and (opt.force_fisher or cat_tests.min_expected(table) < 5):
            res = cat_tests.fisher_exact_2x2(table)
            test_name, pvalue = "Fisher exact", res.pvalue
        else:
            res = cat_tests.chi_square(table)
            test_name, pvalue = "Pearson χ²", res.pvalue
            if res.min_expected < 5:
                notes.append(_msg(opt.lang, "chi_expected_low"))
    except ValueError as exc:
        notes.append(_msg(opt.lang, "test_failed", exc=exc))
        test_name, pvalue = "—", None

    if pvalue is not None and (math.isnan(pvalue) or math.isinf(pvalue)):
        pvalue = None

    smd_val = None
    if k == 2:
        c1 = [counts[li][0] for li in test_levels]
        c2 = [counts[li][1] for li in test_levels]
        smd_val = smd.categorical_smd(c1, c2)

    return CategoricalRow(
        name=name, levels=cat_levels, denom_per_group=denom_per_group,
        overall_denom=overall_denom, missing_per_group=missing_per_group,
        test_name=test_name, pvalue=pvalue, smd=smd_val,
        n_missing_total=sum(missing_per_group), pct=opt.pct, notes=notes)


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def build_table1(frame: Frame, opt: Options) -> Table1:
    if not frame.has(opt.group_col):
        raise ValueError(
            f"그룹 열 '{opt.group_col}' 을(를) 찾을 수 없습니다. "
            f"헤더: {frame.header}")

    warnings: List[str] = []

    # Determine variable columns.
    if opt.var_cols is not None:
        var_cols = list(opt.var_cols)
        for v in var_cols:
            if not frame.has(v):
                raise ValueError(f"변수 열 '{v}' 을(를) 찾을 수 없습니다. "
                                 f"헤더: {frame.header}")
    else:
        var_cols = [h for h in frame.header if h != opt.group_col]
    if not var_cols:
        raise ValueError("분석할 변수 열이 없습니다.")

    # Retain rows with a non-missing group; record group order (first-seen).
    group_raw = frame.column(opt.group_col)
    keep_idx: List[int] = []
    dropped_group = 0
    group_order: List[str] = []
    for i, g in enumerate(group_raw):
        if is_missing(g):
            dropped_group += 1
            continue
        lab = g.strip()
        if lab not in group_order:
            group_order.append(lab)
        keep_idx.append(i)
    if dropped_group:
        warnings.append(_msg(opt.lang, "warn_group_missing", n=dropped_group))
    if len(group_order) < 2:
        raise ValueError(
            f"그룹이 2개 미만입니다(발견: {group_order or '없음'}). "
            "'--group' 열에 2개 이상의 그룹 라벨이 필요합니다.")

    row_index_by_group: Dict[str, List[int]] = {g: [] for g in group_order}
    for i in keep_idx:
        row_index_by_group[group_raw[i].strip()].append(i)
    group_sizes = [len(row_index_by_group[g]) for g in group_order]
    overall_size = sum(group_sizes)

    forced_cont = set(opt.continuous)
    forced_cat = set(opt.categorical)

    rows: List[object] = []
    for var in var_cols:
        col = frame.column(var)
        group_cells = [[col[i] for i in row_index_by_group[g]]
                       for g in group_order]
        if var in forced_cont:
            kind = "continuous"
        elif var in forced_cat:
            kind = "categorical"
        else:
            kind = classify(col, opt.cat_max_levels)
        if kind == "empty":
            warnings.append(_msg(opt.lang, "warn_var_all_missing", var=var))
            continue
        if kind == "continuous":
            # Foot-gun guard: a numeric code column (e.g. NYHA 1-4, a Likert /
            # ISI severity band) that slipped past --cat-max-levels is summarized
            # as mean(SD) with a t-test, which is usually wrong. Warn if it looks
            # like a small-support integer code and the user did not force it.
            if var not in forced_cont:
                nums = [parse_float(c) for c in col if not is_missing(c)]
                nums = [x for x in nums if x is not None]
                distinct = sorted(set(nums))
                if nums and len(distinct) <= 10 and all(
                        float(x).is_integer() for x in distinct):
                    warnings.append(_msg(opt.lang, "warn_int_code",
                                         var=var, n=len(distinct)))
            rows.append(_continuous_row(var, group_cells, opt))
        else:
            # Guard against ID-like columns: an auto-detected categorical with a
            # huge number of levels is almost always an identifier or free text.
            # (A user who really wants it can name it in --vars/--categorical.)
            n_levels = len({c.strip() for c in col if not is_missing(c)})
            auto = var not in forced_cat
            if auto and n_levels > opt.max_display_levels:
                warnings.append(_msg(opt.lang, "warn_too_many_levels",
                                     var=var, n=n_levels))
                continue
            rows.append(_categorical_row(var, group_cells, opt))

    if not rows:
        raise ValueError("요약할 수 있는 변수가 없습니다(모두 결측/제외).")

    meta = {
        "group_col": opt.group_col,
        "alpha_norm": opt.alpha_norm,
        "pct": opt.pct,
        "two_group": len(group_order) == 2,
    }
    return Table1(groups=group_order, group_sizes=group_sizes,
                  overall_size=overall_size, rows=rows,
                  warnings=warnings, meta=meta)
