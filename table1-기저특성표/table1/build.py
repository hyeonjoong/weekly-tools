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
from .dataio import Frame, classify, is_missing, is_numeric_token, parse_float
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
        "normality_untestable": "정규성 검정 불가(각 그룹 n<3 또는 상수)",
        "skip_lt2": "한 그룹의 관측치가 2개 미만 → 검정 생략",
        "skip_groups_lt2": "분석 가능한 그룹이 2개 미만 → 검정 생략",
        "anova_levene": "등분산 가정 위배(Levene p<{alpha:.2g}) → ANOVA 해석 주의 (Welch/비모수 고려)",
        "test_failed": "검정을 계산할 수 없습니다(자료 구조가 검정에 부적합).",
        "unparseable": ("값 {n}개를 숫자로 해석할 수 없어 요약에서 제외했습니다"
                        "(예: {ex}). 부등호·단위·백분율 기호·유럽식 콤마 소수 등을 "
                        "제거하거나 결측으로 두세요 — 이 값들은 평균/중앙값에 "
                        "반영되지 않습니다."),
        "stat_undefined": "검정 통계량이 정의되지 않음(예: 분산 0) → p값 생략",
        "chi_expected_low": "기대빈도 <5 셀 존재 → χ² 근사 주의(Fisher 권장)",
        "warn_group_missing": "그룹 값이 결측이라 제외한 행: {n}개",
        "warn_group_case": ("대소문자만 다른 그룹 라벨이 별개의 군으로 처리됨: {labels}. "
                            "같은 군이면 라벨 표기를 통일하세요."),
        "warn_high_missing": ("변수 '{var}' 는 결측이 {pct:.0f}%로 많습니다 → 요약 통계가 "
                              "소수 관측에 기반하므로 해석에 주의하세요."),
        "warn_var_all_missing": "변수 '{var}' 는 값이 모두 결측이라 건너뜀",
        "warn_int_code": ("변수 '{var}' 는 정수값이 {n}종뿐이라 연속형으로 처리했습니다"
                          "(정규성에 따라 평균±SD 또는 중앙값[IQR]). 순서형/범주형 "
                          "코드라면 '--categorical {var}' 를 쓰세요."),
        "warn_too_many_levels": ("변수 '{var}' 는 고유값이 {n}개로 너무 많아 건너뜀"
                                 "(ID·자유텍스트이거나, 단위·기호가 섞인 수치일 수 있음). "
                                 "수치라면 단위·기호를 제거해 '--continuous {var}', "
                                 "범주라면 '--categorical {var}' 또는 '--max-levels' 를 "
                                 "쓰세요."),
        "warn_type_conflict": ("변수 '{var}' 가 --continuous 와 --categorical 에 "
                               "모두 지정됨 → 연속형으로 처리했습니다."),
        "warn_ref_missing": ("--ref '{var}={level}' 의 수준 '{level}' 이(가) 자료에 "
                             "없어 무시했습니다(관측된 수준: {levels})."),
    },
    "en": {
        "shapiro_cap": ("sample exceeded {n}; normality was tested on a "
                        "{n}-point subsample."),
        "normality_untestable": ("normality not testable (each group n<3 or "
                                 "constant)"),
        "skip_lt2": "a group has <2 observations → test skipped",
        "skip_groups_lt2": "fewer than 2 analyzable groups → test skipped",
        "anova_levene": ("equal-variance assumption violated (Levene "
                         "p<{alpha:.2g}) → interpret ANOVA with caution "
                         "(consider Welch/nonparametric)"),
        "test_failed": ("test could not be computed (data structure unsuitable "
                        "for the test)."),
        "unparseable": ("{n} value(s) could not be parsed as numbers and were "
                        "excluded from the summary (e.g. {ex}). Strip comparison "
                        "signs, units, percent signs, or European decimal "
                        "commas, or leave them blank — these values do not enter "
                        "the mean/median."),
        "stat_undefined": ("test statistic undefined (e.g. zero variance) → "
                           "p-value omitted"),
        "chi_expected_low": ("cells with expected count <5 → chi-square "
                             "approximation unreliable (Fisher recommended)"),
        "warn_group_missing": "rows excluded for a missing group value: {n}",
        "warn_group_case": ("group labels differing only in case are treated as "
                            "separate arms: {labels}. Unify the labels if they "
                            "are the same arm."),
        "warn_high_missing": ("variable '{var}' is {pct:.0f}% missing → the "
                              "summary rests on few observations; interpret with "
                              "caution."),
        "warn_var_all_missing": "variable '{var}' skipped: all values missing",
        "warn_int_code": ("variable '{var}' had only {n} distinct integer "
                          "values and was treated as continuous (mean±SD or "
                          "median[IQR] by normality). If it is an "
                          "ordinal/categorical code, use '--categorical {var}'."),
        "warn_too_many_levels": ("variable '{var}' has {n} distinct values and "
                                 "was skipped (an ID/free-text column, or a "
                                 "numeric column with units/symbols mixed in). "
                                 "If numeric, strip units/symbols and use "
                                 "'--continuous {var}'; if categorical, use "
                                 "'--categorical {var}' or '--max-levels'."),
        "warn_type_conflict": ("variable '{var}' was given to both --continuous "
                               "and --categorical → treated as continuous."),
        "warn_ref_missing": ("--ref '{var}={level}': level '{level}' is not "
                             "present in the data and was ignored (observed "
                             "levels: {levels})."),
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
    test_cont: str = "auto"        # auto | welch | student | nonparam
                                   # controls the continuous TEST (not the summary
                                   # text): 'welch' avoids the Levene pre-test that
                                   # 'auto' uses (Delacre 2017), 'nonparam' forces
                                   # Mann-Whitney/Kruskal regardless of normality.
    alpha_norm: float = 0.05
    force_fisher: bool = False
    pct: str = "col"               # col | row
    pct_decimals: int = 1          # decimals for categorical percentages
    missing_as_level: bool = False
    binary_single: bool = False    # collapse a 2-level categorical to a single row
    ref: Dict[str, str] = field(default_factory=dict)  # column -> reference level
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
    if capped and tested:
        # Only note the subsampling when it actually produced a normality result;
        # if the subsample was constant/untestable the cap note is just noise
        # alongside the "untestable" note.
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
    unparseable = 0            # non-blank cells that are NOT numbers (units/censor)
    unparse_examples: List[str] = []
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
                # Distinguish a genuinely non-numeric token (e.g. ">100", "12 kg",
                # a European "1,5") — silently dropping it would bias the mean —
                # from a non-finite NUMBER (inf/nan), which is legitimately missing.
                if not is_numeric_token(c):
                    unparseable += 1
                    tok = c.strip()[:20]  # cap length; dedup on the stored form
                    if len(unparse_examples) < 3 and tok not in unparse_examples:
                        unparse_examples.append(tok)
            else:
                vals.append(f)
        per_vals.append(vals)
        per_missing.append(miss)

    per_group = [_summ(v, m) for v, m in zip(per_vals, per_missing)]
    all_vals = [v for g in per_vals for v in g]
    overall = _summ(all_vals, sum(per_missing))

    notes: List[str] = []
    if unparseable:
        notes.append(_msg(opt.lang, "unparseable", n=unparseable,
                          ex=", ".join(unparse_examples)))
    nonnormal = _is_nonnormal(per_vals, opt.alpha_norm, notes, opt.lang)

    # The --test-cont override decides parametric vs nonparametric explicitly;
    # otherwise fall back to the normality-gated default. The normality note above
    # is still emitted so the user sees why "auto" would have chosen what it did.
    if opt.test_cont == "nonparam":
        use_nonparam = True
    elif opt.test_cont in ("welch", "student"):
        use_nonparam = False
    else:  # "auto"
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
            elif opt.test_cont == "student":
                res = students_t(per_vals[0], per_vals[1])
                test_name, pvalue = "Student t", res.pvalue
            elif opt.test_cont == "welch":
                res = welch_t(per_vals[0], per_vals[1])
                test_name, pvalue = "Welch t", res.pvalue
            else:  # auto: Levene-gated Student vs Welch
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
                # welch/student only alter the 2-group test; for k>=3 there is no
                # Welch-ANOVA here, so they fall back to one-way ANOVA. The Levene
                # caution note is a pre-test artifact of "auto" mode — suppress it
                # when the user has explicitly fixed the test (it would otherwise
                # recommend the very thing they just chose).
                res = one_way_anova(per_vals)
                test_name, pvalue = "One-way ANOVA", res.pvalue
                if opt.test_cont == "auto" and all(s >= 2 for s in sizes):
                    lev = levene(per_vals)
                    if lev.pvalue < opt.alpha_norm:
                        notes.append(_msg(opt.lang, "anova_levene",
                                          alpha=opt.alpha_norm))
    except (ValueError, ZeroDivisionError):
        # Localized, data-free note (never embed the raw exception text — it may
        # be an untranslated English message and, defensively, must not carry a
        # cell value).
        notes.append(_msg(opt.lang, "test_failed"))
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
    except ValueError:
        notes.append(_msg(opt.lang, "test_failed"))
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

    # Warn on group labels that differ only in case (e.g. "Device" vs "device"):
    # almost always one arm split by a data-entry inconsistency, which would
    # otherwise silently render as extra arms.
    by_lower: Dict[str, List[str]] = {}
    for g in group_order:
        by_lower.setdefault(g.lower(), []).append(g)
    for variants in by_lower.values():
        if len(variants) > 1:
            warnings.append(_msg(opt.lang, "warn_group_case",
                                 labels=", ".join(variants)))

    row_index_by_group: Dict[str, List[int]] = {g: [] for g in group_order}
    for i in keep_idx:
        row_index_by_group[group_raw[i].strip()].append(i)
    group_sizes = [len(row_index_by_group[g]) for g in group_order]
    overall_size = sum(group_sizes)

    forced_cont = set(opt.continuous)
    forced_cat = set(opt.categorical)

    def _warn_high_missing(row_obj) -> None:
        # Flag a variable that is mostly missing so a summary resting on a
        # handful of observations isn't read as if it were complete.
        if overall_size and row_obj.n_missing_total / overall_size > 0.5:
            warnings.append(_msg(opt.lang, "warn_high_missing", var=row_obj.name,
                                 pct=100.0 * row_obj.n_missing_total / overall_size))

    rows: List[object] = []
    for var in var_cols:
        col = frame.column(var)
        group_cells = [[col[i] for i in row_index_by_group[g]]
                       for g in group_order]
        if var in forced_cont:
            kind = "continuous"
            if var in forced_cat:
                warnings.append(_msg(opt.lang, "warn_type_conflict", var=var))
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
            crow = _continuous_row(var, group_cells, opt)
            _warn_high_missing(crow)
            rows.append(crow)
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
            crow = _categorical_row(var, group_cells, opt)
            # If a --ref level was given for this column but never appears in the
            # data, warn instead of silently falling back to the default
            # reference (a typo would otherwise pick the unintended level).
            ref_level = opt.ref.get(var)
            if ref_level is not None:
                observed = [lvl.label for lvl in crow.levels]
                if ref_level not in observed:
                    warnings.append(_msg(opt.lang, "warn_ref_missing", var=var,
                                         level=ref_level,
                                         levels=", ".join(observed)))
            _warn_high_missing(crow)
            rows.append(crow)

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
