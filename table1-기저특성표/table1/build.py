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

from . import cat_tests, smd, weights as wstats
from .dataio import (
    Frame,
    classify,
    is_missing,
    is_numeric_token,
    numeric_profile,
    parse_float,
)
from .effects import (
    Effect,
    hodges_lehmann,
    mean_ci,
    mean_difference,
    median_ci,
    proportion_ci,
    risk_difference,
)
from .multiplicity import adjust_pvalues, normalize_method
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
        "warn_near_unique": ("변수 '{var}' 는 관측 {total}개 중 고유값이 {n}개로 "
                             "거의 모든 행이 서로 달라 건너뜀(환자ID·이름·생년월일·"
                             "자유텍스트일 가능성). 표에 그대로 실으면 개인이 식별될 "
                             "수 있습니다. 정말 필요하면 '--categorical {var}' 로 "
                             "명시하세요."),
        "warn_vars_duplicate": ("--vars 에 같은 열이 두 번 이상 있어 한 번만 "
                                "요약했습니다: {vars}. (중복을 그대로 두면 "
                                "다중비교 보정의 가족 크기가 부풀려집니다.)"),
        "warn_vars_is_group": ("--vars 의 '{var}' 는 그룹(--group) 열이라 "
                               "제외했습니다. 자기 자신과의 비교는 항상 "
                               "p<0.001·SMD=∞ 가 되어 의미가 없습니다."),
        "warn_forced_cat_identifier": (
            "변수 '{var}' 는 '--categorical' 로 지정하셔서 고유값 {n}개를 모두 "
            "표에 실었습니다. 환자ID·이름 등 식별자라면 이 표를 공유할 때 개인이 "
            "식별될 수 있으니 확인하세요."),
        "warn_type_conflict": ("변수 '{var}' 가 --continuous 와 --categorical 에 "
                               "모두 지정됨 → 연속형으로 처리했습니다."),
        "warn_ref_missing": ("--ref '{var}={level}' 의 수준 '{level}' 이(가) 자료에 "
                             "없어 무시했습니다(관측된 수준: {levels})."),
        "warn_numeric_like_cat": ("변수 '{var}' 는 값이 수치처럼 보이지만(예: {ex}) "
                                  "숫자로 해석되지 않는 셀이 있어 범주형으로 처리했습니다 "
                                  "— 각 값이 별개 수준이 되어 검정이 무의미할 수 있습니다. "
                                  "측정값이라면 부등호·단위·백분율 기호를 없앤 뒤 "
                                  "'--continuous {var}' 를 쓰세요."),
        "warn_mixed_promoted": ("변수 '{var}' 는 값의 {pct:.0f}%가 숫자라 연속형으로 "
                                "처리했습니다. 숫자가 아닌 셀 {n}개는 요약에서 제외했습니다"
                                "(주석 참고). 범주형이라면 '--categorical {var}' 를 쓰세요."),
        "warn_summary_row": ("'{col}' 열에 요약(합계) 행처럼 보이는 값이 있습니다: {ex}. "
                             "합계·평균 행이 자료에 섞여 있으면 통계가 왜곡되므로 "
                             "제거한 뒤 실행하세요."),
        "warn_unknown_cols": ("{flag} 에 지정한 열을 자료에서 찾을 수 없어 "
                              "무시했습니다: {cols}. 열 이름을 확인하세요."),
        "warn_weight_dropped": ("가중치가 결측·비수치이거나 0 이하/무한대인 행 {n}개를 "
                                "제외했습니다(가중 분석에서는 유효한 양(+)의 가중치가 "
                                "필요합니다)."),
        "warn_weighted_no_p": ("가중(IPTW/설문) 분석이라 p값·다중비교 보정을 생략했습니다. "
                               "타당한 가중 p값은 설계기반(robust/Rao-Scott) 분산이 "
                               "필요하며 이 도구의 범위를 벗어납니다. 균형은 가중 SMD로 "
                               "보고하세요(Austin과 Stuart 2015 권고)."),
        "warn_weighted_no_effect": ("가중 분석에서는 군간 차이(95% CI) 열을 생략했습니다"
                                    "(설계기반 분산 필요). 가중 SMD를 사용하세요."),
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
        "warn_near_unique": ("variable '{var}' has {n} distinct values across "
                             "{total} observations — nearly every row differs, "
                             "so it was skipped (likely a patient ID, name, "
                             "date of birth or free text). Printing it would "
                             "risk re-identifying individuals. Use "
                             "'--categorical {var}' if you really need it."),
        "warn_vars_duplicate": ("--vars repeated column(s) {vars}; each was "
                                "summarized once. (Leaving duplicates in would "
                                "inflate the multiplicity family size.)"),
        "warn_vars_is_group": ("--vars listed '{var}', which is the --group "
                               "column, so it was dropped: comparing a column "
                               "against itself always gives p<0.001 and "
                               "SMD=inf."),
        "warn_forced_cat_identifier": (
            "variable '{var}' was forced with '--categorical', so all {n} "
            "distinct values are printed. If this is an identifier (patient "
            "ID, name), sharing this table could re-identify individuals."),
        "warn_type_conflict": ("variable '{var}' was given to both --continuous "
                               "and --categorical → treated as continuous."),
        "warn_ref_missing": ("--ref '{var}={level}': level '{level}' is not "
                             "present in the data and was ignored (observed "
                             "levels: {levels})."),
        "warn_numeric_like_cat": ("variable '{var}' looks numeric (e.g. {ex}) "
                                  "but has cells that are not parseable as "
                                  "numbers, so it was treated as categorical — "
                                  "each value becomes its own level and the "
                                  "test may be meaningless. If it is a "
                                  "measurement, strip comparison signs, units "
                                  "and percent signs and use "
                                  "'--continuous {var}'."),
        "warn_mixed_promoted": ("variable '{var}' is {pct:.0f}% numeric and was "
                                "treated as continuous; {n} non-numeric cell(s) "
                                "were excluded from the summary (see the note). "
                                "If it is categorical, use "
                                "'--categorical {var}'."),
        "warn_summary_row": ("column '{col}' contains what looks like a summary "
                             "(total) row: {ex}. A total/mean row mixed into the "
                             "data distorts every statistic — remove it and "
                             "re-run."),
        "warn_unknown_cols": ("{flag} named column(s) that are not in the data "
                              "and were ignored: {cols}. Check the column "
                              "names."),
        "warn_weight_dropped": ("{n} row(s) were excluded for a missing, "
                                "non-numeric, non-positive or non-finite "
                                "weight (a weighted analysis needs a valid "
                                "positive weight)."),
        "warn_weighted_no_p": ("p-values and multiplicity adjustment are "
                               "omitted for a weighted (IPTW/survey) analysis: "
                               "a valid weighted p-value needs design-based "
                               "(robust/Rao-Scott) variances, which are out of "
                               "scope here. Report balance with the weighted "
                               "SMD (Austin and Stuart 2015)."),
        "warn_weighted_no_effect": ("the between-group difference (95% CI) "
                                    "column is omitted under weighting "
                                    "(design-based variances required); use "
                                    "the weighted SMD."),
    },
}


def _msg(lang: str, key: str, **kw) -> str:
    table = _MSG.get(lang or "ko", _MSG["ko"])
    return table[key].format(**kw)


@dataclass
class Options:
    group_col: Optional[str]                    # None -> single-group table
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
    effect: bool = False            # True -> compute a two-group effect + 95% CI
                                    # (mean/HL difference; binary risk difference)
    padjust: str = "none"           # multiplicity correction across variables:
                                    # none | bonferroni | holm | bh | by
    lang: str = "ko"               # ko | en  (rendered label language)
    labels: Dict[str, str] = field(default_factory=dict)  # column -> display name
    nonnormal: List[str] = field(default_factory=list)  # per-variable: force
                                   # median[IQR] + a rank test, regardless of
                                   # what Shapiro-Wilk says (the tableone
                                   # convention: the analyst, not a pre-test,
                                   # decides which variables are skewed)
    weight_col: Optional[str] = None  # IPTW / survey weights column. Turns the
                                   # table into a weighted (pseudo-population)
                                   # Table 1: weighted summaries + weighted SMD,
                                   # with p-values/effects suppressed (they
                                   # would need design-based variances).


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
    # Weighted mode only (NaN otherwise): the summed weight behind this cell
    # and Kish's effective sample size. ``n`` stays the RAW count of
    # contributing observations in both modes, so "how many patients" is never
    # silently replaced by "how much weight".
    wsum: float = float("nan")
    ess: float = float("nan")


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
    effect: Optional[Effect] = None
    p_adjusted: Optional[float] = None


@dataclass
class CatLevel:
    label: str
    counts: List[int]            # per group (aligned to group order)
    overall: int
    # Weighted mode only (None otherwise): the summed weight per group for this
    # level, and its total. ``counts`` always stays the raw head-count.
    wcounts: Optional[List[float]] = None
    woverall: Optional[float] = None


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
    effect: Optional[Effect] = None
    p_adjusted: Optional[float] = None
    # Weighted mode only (None otherwise): the per-group weight totals that act
    # as the percentage basis, and their sum.
    wdenom_per_group: Optional[List[float]] = None
    woverall_denom: Optional[float] = None


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
# A column whose cells all carry a unit/comparator ("72 kg", ">100") parses as
# zero numbers, so it looks categorical. Warn above this share of number-ish
# cells.
_NUM_ISH_WARN_FRAC = 0.8

# A near-unique categorical (almost one level per patient) is an identifier, not
# a characteristic — printing it would list every patient individually. The
# absolute --max-levels cap misses this in a small cohort, so these thresholds
# add the relative test. Requires a few observations so that a genuine 3-level
# variable measured on 3 patients is not mistaken for an ID.
_NEAR_UNIQUE_FRAC = 0.9
_NEAR_UNIQUE_MIN_OBS = 5

# Upper bound on study arms / cohorts a Table 1 can compare side by side. Well
# above any real design (a 6-arm dose-finding trial is already unusual), low
# enough that pointing --group at an identifier column fails loudly instead of
# emitting one column per patient.
_MAX_GROUPS = 20


def _identifier_skip(var: str, col: Sequence[str], opt: "Options"
                     ) -> Optional[str]:
    """Warning text if this auto-detected categorical looks like an identifier.

    Two criteria, both needed:

    * ABSOLUTE — more distinct levels than ``--max-levels``. Catches an ID or
      free-text column in a normal-sized cohort.
    * RELATIVE — nearly one distinct level per observation. The absolute cap is
      structurally unable to fire in a small cohort (a 12-patient pilot cannot
      have 20 distinct names), which is precisely where every level is one
      identifiable patient. Without this, a name / MRN / date-of-birth column
      renders one row per patient in exactly the studies where that is most
      disclosive.

    Returns None when the column is a legitimate characteristic.
    """
    n_levels = len({c.strip() for c in col if not is_missing(c)})
    if n_levels > opt.max_display_levels:
        return _msg(opt.lang, "warn_too_many_levels", var=var, n=n_levels)
    n_obs = sum(1 for c in col if not is_missing(c))
    if (n_obs >= _NEAR_UNIQUE_MIN_OBS
            and n_levels >= _NEAR_UNIQUE_FRAC * n_obs
            and n_levels > max(1, opt.cat_max_levels)):
        return _msg(opt.lang, "warn_near_unique", var=var, n=n_levels,
                    total=n_obs)
    return None

# Labels that mark a spreadsheet SUMMARY row (a "합계"/Total line pasted under
# the data). Such a row is not a patient: it inflates N and drags every summary
# toward a sum. Deliberately narrow — only tokens that cannot plausibly be a
# real arm/level label ("전체"/"overall" are excluded because they legitimately
# name a group). We warn rather than drop: silently deleting a row the user
# believes is data would be worse than telling them about it.
_SUMMARY_ROW_TOKENS = {
    "합계", "총계", "소계", "누계", "총합", "총계합", "합",
    "total", "totals", "subtotal", "sum", "grand total",
    "평균", "mean", "average",
}


def _summary_row_hits(frame: Frame) -> List[tuple]:
    """[(column, token)] for cells that look like a spreadsheet summary row."""
    hits: List[tuple] = []
    for col in frame.header:
        if not col:
            continue
        for cell in frame.column(col):
            tok = cell.strip()
            if tok and tok.casefold() in _SUMMARY_ROW_TOKENS:
                hits.append((col, tok))
                break   # one report per column is enough
    return hits


def _examples(col: Sequence[str], k: int = 2) -> str:
    """A couple of representative non-numeric cells, for a warning.

    Capped in count and length for the same reason ``_continuous_row`` caps its
    unparseable examples: the value comes from patient data and must not turn a
    warning into a data dump.
    """
    out: List[str] = []
    for c in col:
        if is_missing(c) or parse_float(c) is not None:
            continue
        tok = c.strip()[:20]
        if tok and tok not in out:
            out.append(tok)
        if len(out) >= k:
            break
    return ", ".join(out)


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


def _summ(values: Sequence[float], n_missing: int,
          w: Optional[Sequence[float]] = None) -> GroupStat:
    """Summarize one cell, optionally weighted.

    ``w=None`` is the unweighted path and is left byte-for-byte identical to
    what it always computed. With weights, every location/spread statistic is
    the weighted analogue; ``n`` remains the raw count of contributing
    observations and the weight total / Kish ESS are recorded alongside.
    """
    n = len(values)
    st = GroupStat(n=n, n_missing=n_missing)
    if n == 0:
        return st
    if w is not None:
        st.wsum = math.fsum(w)
        st.ess = wstats.kish_ess(w)
        st.mean = wstats.weighted_mean(values, w)
        st.sd = wstats.weighted_sd(values, w) if n >= 2 else 0.0
        st.median = wstats.weighted_quantile(values, w, 0.5)
        st.q1 = wstats.weighted_quantile(values, w, 0.25)
        st.q3 = wstats.weighted_quantile(values, w, 0.75)
        s = sorted(values)
        st.vmin = s[0]
        st.vmax = s[-1]
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
def _continuous_row(name: str, group_cells: List[List[str]], opt: Options,
                    group_weights: Optional[List[List[float]]] = None
                    ) -> ContinuousRow:
    weighted = group_weights is not None
    per_vals: List[List[float]] = []
    per_w: List[List[float]] = []
    per_missing: List[int] = []
    unparseable = 0            # non-blank cells that are NOT numbers (units/censor)
    unparse_examples: List[str] = []
    for gi, cells in enumerate(group_cells):
        vals = []
        wts: List[float] = []
        miss = 0
        # Pair each cell with its weight so a dropped (missing/unparseable)
        # value drops its weight too — misaligning these would attach one
        # patient's weight to another patient's value.
        cell_w = group_weights[gi] if weighted else [1.0] * len(cells)
        for c, wv in zip(cells, cell_w):
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
                wts.append(wv)
        per_vals.append(vals)
        per_w.append(wts)
        per_missing.append(miss)

    if weighted:
        per_group = [_summ(v, m, w)
                     for v, m, w in zip(per_vals, per_missing, per_w)]
        all_vals = [v for g in per_vals for v in g]
        all_w = [x for g in per_w for x in g]
        overall = _summ(all_vals, sum(per_missing), all_w)
    else:
        per_group = [_summ(v, m) for v, m in zip(per_vals, per_missing)]
        all_vals = [v for g in per_vals for v in g]
        overall = _summ(all_vals, sum(per_missing))

    notes: List[str] = []
    if unparseable:
        notes.append(_msg(opt.lang, "unparseable", n=unparseable,
                          ex=", ".join(unparse_examples)))
    # --nonnormal names variables the analyst already knows to be skewed, so
    # the Shapiro-Wilk pre-test is not consulted at all for them (and its
    # "untestable" note would be noise).
    forced_nonnormal = name in set(opt.nonnormal)
    if forced_nonnormal:
        nonnormal: Optional[bool] = True
    else:
        nonnormal = _is_nonnormal(per_vals, opt.alpha_norm, notes, opt.lang)

    # The --test-cont override decides parametric vs nonparametric explicitly;
    # otherwise fall back to the normality-gated default. The normality note above
    # is still emitted so the user sees why "auto" would have chosen what it did.
    if forced_nonnormal:
        # Per-variable --nonnormal is more specific than the global
        # --test-cont, so it wins outright: naming a variable as skewed and
        # then getting a t-test on it would silently ignore the instruction.
        use_nonparam = True
    elif opt.test_cont == "nonparam":
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
        if weighted:
            # An unweighted p-value beside weighted summaries would be
            # incoherent, and a valid weighted one needs design-based
            # variances (out of scope). Balance is reported via the weighted
            # SMD instead; the reason is stated once as a table warning.
            pass
        elif k < 2:
            pass  # single-group descriptive table: no between-group test
        elif k == 2:
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
    effect_val: Optional[Effect] = None
    if k == 2 and weighted:
        # Austin & Stuart (2015): the weighted SMD is the balance metric for an
        # IPTW pseudo-population.
        smd_val = wstats.weighted_continuous_smd(per_vals[0], per_w[0],
                                                 per_vals[1], per_w[1])
    elif k == 2:
        smd_val = smd.continuous_smd(per_vals[0], per_vals[1])
        if opt.effect:
            # Keep the effect coherent with the reported test: a parametric
            # test -> difference in means with the matching (pooled/Welch) CI;
            # a rank test -> Hodges-Lehmann median shift.
            if test_name == "Student t":
                effect_val = mean_difference(per_vals[0], per_vals[1],
                                             kind="student")
            elif test_name == "Welch t":
                effect_val = mean_difference(per_vals[0], per_vals[1],
                                             kind="welch")
            elif test_name == "Mann-Whitney U":
                effect_val = hodges_lehmann(per_vals[0], per_vals[1])

    return ContinuousRow(
        name=name, per_group=per_group, overall=overall, display=display,
        test_name=test_name, pvalue=pvalue, smd=smd_val,
        n_missing_total=sum(per_missing), notes=notes, effect=effect_val)


# --------------------------------------------------------------------------- #
# categorical
# --------------------------------------------------------------------------- #
def _order_levels(labels: Sequence[str]) -> List[str]:
    uniq = list(dict.fromkeys(labels))
    parsed = [parse_float(x) for x in uniq]
    if all(p is not None for p in parsed):
        return [x for _, x in sorted(zip(parsed, uniq), key=lambda t: t[0])]
    return sorted(uniq)


def _categorical_row(name: str, group_cells: List[List[str]], opt: Options,
                     group_weights: Optional[List[List[float]]] = None
                     ) -> CategoricalRow:
    weighted = group_weights is not None
    k = len(group_cells)
    missing_per_group = [0] * k
    wmissing_per_group = [0.0] * k
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
    wcounts = [[0.0] * k for _ in real_levels]
    level_pos = {lab: i for i, lab in enumerate(real_levels)}
    for gi, cells in enumerate(group_cells):
        cell_w = group_weights[gi] if weighted else [1.0] * len(cells)
        for c, wv in zip(cells, cell_w):
            if is_missing(c):
                missing_per_group[gi] += 1
                wmissing_per_group[gi] += wv
                continue
            counts[level_pos[c.strip()]][gi] += 1
            wcounts[level_pos[c.strip()]][gi] += wv

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
        wcounts.append(list(wmissing_per_group))
    levels = list(real_levels) + ([missing_label] if missing_label else [])

    denom_per_group = [sum(counts[li][gi] for li in range(len(levels)))
                       for gi in range(k)]
    overall_denom = sum(denom_per_group)
    wdenom_per_group = None
    woverall_denom = None
    if weighted:
        wdenom_per_group = [math.fsum(wcounts[li][gi]
                                      for li in range(len(levels)))
                            for gi in range(k)]
        woverall_denom = math.fsum(wdenom_per_group)
    cat_levels = [
        CatLevel(label=levels[li],
                 counts=[counts[li][gi] for gi in range(k)],
                 overall=sum(counts[li][gi] for gi in range(k)),
                 wcounts=([wcounts[li][gi] for gi in range(k)]
                          if weighted else None),
                 woverall=(math.fsum(wcounts[li][gi] for gi in range(k))
                           if weighted else None))
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
    if weighted:
        # See _continuous_row: no unweighted p-value beside weighted summaries.
        pass
    elif k >= 2:  # a single-group descriptive table carries no association test
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
    effect_val: Optional[Effect] = None
    if k == 2 and weighted:
        smd_val = wstats.weighted_categorical_smd(
            [wcounts[li][0] for li in test_levels],
            [wcounts[li][1] for li in test_levels])
    elif k == 2:
        c1 = [counts[li][0] for li in test_levels]
        c2 = [counts[li][1] for li in test_levels]
        smd_val = smd.categorical_smd(c1, c2)
        # A scalar effect is only defined for a binary (2-level) categorical:
        # the risk (proportion) difference of the index level. The index level
        # is the non-reference one (mirroring --binary-single's default of
        # showing the second level; --ref COL=level flips it).
        if opt.effect and n_real == 2:
            index = 1
            ref_level = opt.ref.get(name)
            if ref_level is not None and ref_level == real_levels[1]:
                index = 0
            n1 = sum(counts[li][0] for li in test_levels)  # non-missing group 0
            n2 = sum(counts[li][1] for li in test_levels)  # non-missing group 1
            effect_val = risk_difference(counts[index][0], n1,
                                         counts[index][1], n2)
            if effect_val is not None:
                # Record which level the risk difference is FOR, so the table
                # is not ambiguous about a binary variable's two levels.
                effect_val.index_level = real_levels[index]
                effect_val.reference_level = real_levels[1 - index]

    return CategoricalRow(
        name=name, levels=cat_levels, denom_per_group=denom_per_group,
        overall_denom=overall_denom, missing_per_group=missing_per_group,
        test_name=test_name, pvalue=pvalue, smd=smd_val,
        n_missing_total=sum(missing_per_group), pct=opt.pct, notes=notes,
        effect=effect_val, wdenom_per_group=wdenom_per_group,
        woverall_denom=woverall_denom)


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def build_table1(frame: Frame, opt: Options) -> Table1:
    # A missing group_col (== None) is a whole-cohort DESCRIPTIVE table: one
    # "Overall" column, no tests / SMD / effect / adjusted p.
    single_group = opt.group_col is None
    if not single_group and not frame.has(opt.group_col):
        raise ValueError(
            f"그룹 열 '{opt.group_col}' 을(를) 찾을 수 없습니다. "
            f"헤더: {frame.header}")

    warnings: List[str] = []

    # A "합계"/Total line pasted under the data is a spreadsheet artifact, not a
    # patient. With a group column it usually drops out anyway (its group cell
    # is blank), but a whole-cohort table would silently count it — inflating N
    # and dragging every summary toward a sum.
    for _col, _tok in _summary_row_hits(frame):
        warnings.append(_msg(opt.lang, "warn_summary_row", col=_col, ex=_tok))

    # ---------------------------------------------------------------- weights
    # A weighted (IPTW / survey) table. Parse the weights up front so rows with
    # an unusable weight are excluded from EVERY variable consistently, rather
    # than per-variable (which would silently vary the denominator by row).
    weighted = opt.weight_col is not None
    weight_by_row: Dict[int, float] = {}
    if weighted:
        if not frame.has(opt.weight_col):
            raise ValueError(
                f"가중치 열 '{opt.weight_col}' 을(를) 찾을 수 없습니다. "
                f"헤더: {frame.header}")
        if opt.weight_col == opt.group_col:
            raise ValueError(
                "'--weights' 와 '--group' 에 같은 열을 지정할 수 없습니다.")
        wraw = frame.column(opt.weight_col)
        dropped_w = 0
        for i, cell in enumerate(wraw):
            v = None if is_missing(cell) else parse_float(cell)
            # parse_float already maps inf/nan to None; a zero or negative
            # weight carries no information and is not a valid IPTW weight.
            if v is None or v <= 0.0:
                dropped_w += 1
                continue
            weight_by_row[i] = v
        if not weight_by_row:
            raise ValueError(
                f"가중치 열 '{opt.weight_col}' 에 유효한 양(+)의 가중치가 "
                "하나도 없습니다.")
        if dropped_w:
            warnings.append(_msg(opt.lang, "warn_weight_dropped", n=dropped_w))

    # Determine variable columns.
    if opt.var_cols is not None:
        var_cols = list(opt.var_cols)
        for v in var_cols:
            if not frame.has(v):
                raise ValueError(f"변수 열 '{v}' 을(를) 찾을 수 없습니다. "
                                 f"헤더: {frame.header}")
        # A repeated column would render an identical row twice AND count twice
        # toward the multiplicity family size, silently changing every adjusted
        # p-value in the table. De-duplicate (first mention wins) and say so.
        seen_vars: set = set()
        deduped = []
        dupe_vars = []
        for v in var_cols:
            if v in seen_vars:
                if v not in dupe_vars:
                    dupe_vars.append(v)
                continue
            seen_vars.add(v)
            deduped.append(v)
        if dupe_vars:
            warnings.append(_msg(opt.lang, "warn_vars_duplicate",
                                 vars=", ".join(dupe_vars)))
        # Summarizing the grouping column against itself is a degenerate
        # comparison: it yields p<0.001 and SMD=inf by construction, and it
        # joins the multiplicity family, distorting every other adjusted p.
        if opt.group_col is not None and opt.group_col in seen_vars:
            deduped = [v for v in deduped if v != opt.group_col]
            warnings.append(_msg(opt.lang, "warn_vars_is_group",
                                 var=opt.group_col))
        var_cols = deduped
    else:
        # The weight column is a design variable, not a baseline characteristic:
        # keep it out of the auto-detected variable list (an explicit --vars
        # naming it is still honoured).
        var_cols = [h for h in frame.header
                    if h != opt.group_col and h != opt.weight_col]
    if not var_cols:
        raise ValueError("분석할 변수 열이 없습니다.")

    if single_group:
        # Every row belongs to one synthetic "Overall" group.
        overall_label = "Overall" if (opt.lang or "ko") == "en" else "전체"
        keep_idx = [i for i in range(frame.nrows)
                    if not weighted or i in weight_by_row]
        group_order = [overall_label]
        row_index_by_group = {overall_label: list(keep_idx)}
    else:
        # Retain rows with a non-missing group; record group order (first-seen).
        group_raw = frame.column(opt.group_col)
        keep_idx = []
        dropped_group = 0
        group_order = []
        for i, g in enumerate(group_raw):
            if weighted and i not in weight_by_row:
                continue  # already counted in warn_weight_dropped
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
                "'--group' 열에 2개 이상의 그룹 라벨이 필요합니다. "
                "(전체 코호트를 요약하려면 '--group' 없이 실행하세요.)")
        # A grouping column with a huge number of levels is an identifier
        # (--group mrn), not a study arm. Unchecked it renders ONE COLUMN PER
        # PATIENT, turning Table 1 into a re-identifiable line listing of every
        # other variable — the group column is excluded from var_cols, so it is
        # the one column no other guard can see. Report the COUNT only; echoing
        # the labels would print the very identifiers we are refusing to show.
        # Two criteria, mirroring _identifier_skip: the absolute cap catches an
        # ID column in a normal cohort, the relative one catches it in a small
        # cohort where the absolute cap structurally cannot fire.
        near_unique_groups = (
            len(keep_idx) >= _NEAR_UNIQUE_MIN_OBS
            and len(group_order) >= _NEAR_UNIQUE_FRAC * len(keep_idx))
        if len(group_order) > _MAX_GROUPS or near_unique_groups:
            raise ValueError(
                f"'--group {opt.group_col}' 의 서로 다른 값이 "
                f"{len(group_order)}개({len(keep_idx)}행)로 너무 많습니다"
                f"(최대 {_MAX_GROUPS}개, 그리고 행 수보다 충분히 적어야 함). "
                "환자ID처럼 행마다 다른 열을 그룹으로 지정하면 표에 환자별 열이 "
                "생겨 개인이 식별될 수 있습니다. 시험군·코호트처럼 값이 몇 개뿐인 "
                "열을 지정하세요.")

        # Warn on group labels that differ only in case (e.g. "Device" vs
        # "device"): almost always one arm split by a data-entry inconsistency,
        # which would otherwise silently render as extra arms.
        by_lower: Dict[str, List[str]] = {}
        for g in group_order:
            by_lower.setdefault(g.lower(), []).append(g)
        for variants in by_lower.values():
            if len(variants) > 1:
                warnings.append(_msg(opt.lang, "warn_group_case",
                                     labels=", ".join(variants)))

        row_index_by_group = {g: [] for g in group_order}
        for i in keep_idx:
            row_index_by_group[group_raw[i].strip()].append(i)
    group_sizes = [len(row_index_by_group[g]) for g in group_order]
    overall_size = sum(group_sizes)

    forced_cont = set(opt.continuous)
    forced_cat = set(opt.categorical)

    # A typo in a column-name flag would otherwise be silently ignored, quietly
    # changing which statistic gets reported (e.g. '--nonnormal ahii' leaves ahi
    # summarized as mean(SD) with a t-test). --vars/--group/--weights already
    # hard-error on an unknown name; these flags are advisory, so warn instead
    # of failing a table that is otherwise fine.
    for flag, names in (("--continuous", opt.continuous),
                        ("--categorical", opt.categorical),
                        ("--nonnormal", opt.nonnormal),
                        ("--labels", list(opt.labels)),
                        ("--ref", list(opt.ref))):
        unknown = [n for n in names if not frame.has(n)]
        if unknown:
            warnings.append(_msg(opt.lang, "warn_unknown_cols", flag=flag,
                                 cols=", ".join(sorted(unknown))))

    def _warn_high_missing(row_obj) -> None:
        # Flag a variable that is mostly missing so a summary resting on a
        # handful of observations isn't read as if it were complete.
        if overall_size and row_obj.n_missing_total / overall_size > 0.5:
            warnings.append(_msg(opt.lang, "warn_high_missing", var=row_obj.name,
                                 pct=100.0 * row_obj.n_missing_total / overall_size))

    # Per-group weight vectors, aligned cell-for-cell with group_cells below.
    group_weights: Optional[List[List[float]]] = None
    if weighted:
        group_weights = [[weight_by_row[i] for i in row_index_by_group[g]]
                         for g in group_order]

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

        # Decide identifier-like SKIPS before any warning that quotes example
        # cells: a date-of-birth / name column would otherwise be skipped for
        # privacy while a preceding warning printed two of its raw values into
        # the same output file.
        if kind == "categorical":
            skip = _identifier_skip(var, col, opt)
            if skip is not None:
                if var not in forced_cat:
                    warnings.append(skip)
                    continue
                # --categorical is a deliberate override and stays honoured,
                # but silently printing one row per patient is not something a
                # researcher should discover only after sharing the table.
                warnings.append(_msg(opt.lang, "warn_forced_cat_identifier",
                                     var=var,
                                     n=len({c.strip() for c in col
                                            if not is_missing(c)})))

        # A column that is mostly numbers but carries a few censored / unit-
        # bearing cells (">100", "12 kg") is the classic silent-corruption
        # shape: treated as a category it renders one level per patient and
        # attaches a meaningless chi-square to a continuous endpoint. classify()
        # promotes the clear-cut cases; say so either way so the researcher is
        # never left guessing which reading they got.
        if var not in forced_cont and var not in forced_cat:
            prof = numeric_profile(col)
            if prof.n_nonnumeric and prof.n_numeric:
                if kind == "continuous":
                    warnings.append(_msg(opt.lang, "warn_mixed_promoted",
                                         var=var,
                                         pct=100.0 * prof.numeric_fraction,
                                         n=prof.n_nonnumeric))
                elif prof.num_ish_fraction >= 0.5:
                    warnings.append(_msg(opt.lang, "warn_numeric_like_cat",
                                         var=var, ex=_examples(col)))
            elif (kind == "categorical" and not prof.n_numeric
                    and prof.num_ish_fraction >= _NUM_ISH_WARN_FRAC
                    and prof.n_nonmissing >= 3):
                # Every cell carries a unit ("72 kg", "45%"): nothing parses, so
                # the column looks categorical, but it is plainly a measurement.
                warnings.append(_msg(opt.lang, "warn_numeric_like_cat",
                                     var=var, ex=_examples(col)))

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
            crow = _continuous_row(var, group_cells, opt, group_weights)
            _warn_high_missing(crow)
            rows.append(crow)
        else:
            # ID-like columns are already filtered out above (_identifier_skip),
            # before any warning that could quote their raw cells.
            crow = _categorical_row(var, group_cells, opt, group_weights)
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

    # Multiple-comparison adjustment across the per-variable primary p-values
    # (one per row, never per level). Untestable variables (pvalue None) pass
    # through and do not count toward the family size.
    padjust = normalize_method(opt.padjust)
    if weighted:
        # No p-values exist under weighting, so there is nothing to adjust and
        # no p-column to render. Say so once, at table level, rather than
        # leaving the user to wonder why the column vanished.
        padjust = "none"
        if opt.show_pvalue:
            warnings.append(_msg(opt.lang, "warn_weighted_no_p"))
        if opt.effect:
            warnings.append(_msg(opt.lang, "warn_weighted_no_effect"))
    if padjust != "none":
        adj = adjust_pvalues([getattr(r, "pvalue", None) for r in rows], padjust)
        for r, a in zip(rows, adj):
            r.p_adjusted = a

    # Per-group weight totals and Kish effective sample sizes for the header.
    wsums: Optional[List[float]] = None
    esss: Optional[List[float]] = None
    if weighted:
        wsums = [math.fsum(w) for w in (group_weights or [])]
        esss = [wstats.kish_ess(w) for w in (group_weights or [])]

    meta = {
        "group_col": opt.group_col,
        "alpha_norm": opt.alpha_norm,
        "pct": opt.pct,
        "single_group": single_group,
        "two_group": len(group_order) == 2,
        "padjust": padjust,
        # An effect/CI column is only actually present for a two-group table
        # (between-group difference) or, in descriptive mode, a one-sample CI —
        # and never under weighting, which has no design-based variance here.
        "effect": (bool(opt.effect) and not weighted
                   and (len(group_order) == 2 or single_group)),
        "weighted": weighted,
        "weight_col": opt.weight_col,
        "weight_sums": wsums,
        "ess": esss,
        # Pooled ESS for the Overall column, computed here rather than exposing
        # every row's weight in meta: meta is serialized into the JSON output,
        # and a per-row weight vector is row-level data that has no business in
        # a table-level summary (and would bloat the file).
        "ess_overall": (wstats.kish_ess([x for g in (group_weights or [])
                                         for x in g]) if weighted else None),
        # Under weighting the p-value column is suppressed regardless of
        # --no-pvalue; render reads this rather than re-deriving the rule.
        "show_pvalue": bool(opt.show_pvalue) and not weighted,
    }
    return Table1(groups=group_order, group_sizes=group_sizes,
                  overall_size=overall_size, rows=rows,
                  warnings=warnings, meta=meta)
