"""대상자별 / 시기별 집계.

수면일기 분석의 함정은 **분석 단위**다. 한 사람이 14박을 적으면 14행이지만
통계의 n은 14가 아니라 1이다. 이 모듈은 항상 두 단계로 집계한다.

    1) 밤(night) → 대상자(subject) [× 시기(period)]  : 사람마다 평균/중앙값
    2) 대상자 → 집단(group)                          : 사람들 사이의 평균/SD

시기 비교도 **대상자 평균의 차이**에 대해 대응표본으로 수행한다
(같은 사람이 두 시기 모두 기록한 경우만 짝으로 쓴다).

시각형 지표(취침·기상·중앙수면시각)는 자정을 넘나들므로 산술평균이 아니라
원형(circular) 평균/차이를 쓴다 — 23:50과 00:10의 평균은 12:00이 아니다.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .stats import (
    PairedResult,
    WilcoxonResult,
    circular_diff,
    circular_mean,
    circular_sd,
    mean,
    mean_ci,
    paired_ttest,
    summarize,
    wilcoxon_signed_rank,
)

# (키, 표시이름, 단위) — 선형 지표
LINEAR_METRICS: tuple[tuple[str, str, str], ...] = (
    ("tst_min", "총수면시간 TST", "min"),
    ("se_pct", "수면효율 SE", "%"),
    ("sol_min", "입면잠복기 SOL", "min"),
    ("waso_min", "중도각성 WASO", "min"),
    ("tib_min", "침대에머문시간 TIB", "min"),
    ("spt_min", "수면기간 SPT", "min"),
    ("twak_min", "기상후침대체류 TWAK", "min"),
    ("awakenings", "각성횟수", "회"),
)

# 시계 위의 값 — 원형 통계로 다룬다
CIRCULAR_METRICS: tuple[tuple[str, str], ...] = (
    ("lights_off_min", "소등시각"),
    ("onset_min", "입면시각"),
    ("final_awake_min", "최종기상시각"),
    ("midsleep_min", "수면중앙시각"),
)

# 시각형 지표의 변화가 이보다 크면 ±720분 감김 때문에 부호를 신뢰할 수 없다
# (예: "6시간 앞당김"과 "18시간 늦춤"이 수치상 구분되지 않는다).
WRAP_UNSTABLE_MIN = 360.0

LINEAR_KEYS = tuple(k for k, _, _ in LINEAR_METRICS)
CIRCULAR_KEYS = tuple(k for k, _ in CIRCULAR_METRICS)
ALL_KEYS = LINEAR_KEYS + CIRCULAR_KEYS

METRIC_LABEL = {k: label for k, label, _ in LINEAR_METRICS}
METRIC_LABEL.update({k: label for k, label in CIRCULAR_METRICS})
METRIC_UNIT = {k: unit for k, _, unit in LINEAR_METRICS}
METRIC_UNIT.update({k: "clock" for k, _ in CIRCULAR_METRICS})


class SubjectSummary:
    """한 대상자(× 한 시기)의 여러 밤을 하나로 요약한 것."""

    __slots__ = ("subject", "period", "n_nights", "n_excluded", "values",
                 "metrics", "regularity", "date_span", "n_warned", "n_imputed")

    def __init__(self, subject: str, period: Optional[str]):
        self.subject = subject
        self.period = period
        self.n_nights = 0            # 집계에 실제로 쓴 밤 수
        self.n_excluded = 0          # 오류로 제외된 밤 수
        self.n_warned = 0            # 경고가 붙은 채 포함된 밤 수
        # SOL/WASO를 0으로 채운 밤 수 (그 지표의 요약에서는 빠진 밤들)
        self.n_imputed: dict[str, int] = {"sol": 0, "waso": 0}
        self.values: dict[str, list[float]] = {k: [] for k in ALL_KEYS}
        self.metrics: dict[str, dict] = {}
        self.regularity: Optional[float] = None   # 수면중앙시각의 원형 SD(분)
        self.date_span: Optional[tuple] = None

    # -- 대표값 -----------------------------------------------------------
    def value(self, key: str) -> Optional[float]:
        """이 대상자의 그 지표 대표값 (선형=산술평균, 시각=원형평균)."""
        entry = self.metrics.get(key)
        return entry.get("mean") if entry else None

    def as_dict(self) -> dict:
        out = {
            "subject": self.subject,
            "period": self.period,
            "n_nights": self.n_nights,
            "n_excluded": self.n_excluded,
            "n_warned": self.n_warned,
            "regularity_min": self.regularity,
            "sol_imputed_nights": self.n_imputed["sol"],
            "waso_imputed_nights": self.n_imputed["waso"],
        }
        if self.date_span:
            out["date_first"], out["date_last"] = self.date_span
        for key in ALL_KEYS:
            entry = self.metrics.get(key)
            out[key] = entry.get("mean") if entry else None
            out[key + "_n"] = entry.get("n", 0) if entry else 0
        return out


def _finalize(summary: SubjectSummary) -> None:
    """모아둔 밤별 값에서 대상자 요약통계를 계산한다."""
    for key in LINEAR_KEYS:
        vals = summary.values[key]
        summary.metrics[key] = summarize(vals)
    for key in CIRCULAR_KEYS:
        vals = summary.values[key]
        if not vals:
            summary.metrics[key] = {"n": 0, "mean": None, "sd": None}
            continue
        try:
            cm = circular_mean(vals)
        except ValueError:
            # 값들이 시계 위에서 정확히 상쇄된 희귀한 경우 — 평균 방향 미정의
            cm = None
        summary.metrics[key] = {"n": len(vals), "mean": cm, "sd": circular_sd(vals)}
    summary.regularity = summary.metrics.get("midsleep_min", {}).get("sd")


def summarize_by_subject(nights: Sequence, *, by_period: bool = True) -> list[SubjectSummary]:
    """`Night` 목록 → 대상자(× 시기)별 요약 목록.

    오류가 있는 밤(`night.valid`가 False)은 집계에서 빼되 `n_excluded`로 센다.
    경고만 있는 밤은 포함한다 (이상치를 조용히 지우지 않기 위해서).
    """
    buckets: dict[tuple, SubjectSummary] = {}
    order: list[tuple] = []

    for night in nights:
        period = night.period if by_period else None
        key = (night.subject, period)
        if key not in buckets:
            buckets[key] = SubjectSummary(night.subject, period)
            order.append(key)
        summary = buckets[key]

        if not night.valid:
            summary.n_excluded += 1
            continue

        summary.n_nights += 1
        if night.warnings:
            summary.n_warned += 1
        # 0으로 채운 SOL/WASO는 TST 계산에는 쓰지만 **그 지표 자체의 요약에는
        # 넣지 않는다**. 넣으면 "입면잠복기 평균 0분, 95% CI [0, 0]" 같은,
        # 측정한 적 없는 값에 대한 정밀한 주장이 논문 문장까지 흘러간다.
        imputed_fields = {name.split("(")[0] for name in night.imputed}
        for attr, key_name in (("tst", "tst_min"), ("se", "se_pct"), ("sol", "sol_min"),
                               ("waso", "waso_min"), ("tib", "tib_min"), ("spt", "spt_min"),
                               ("twak", "twak_min"), ("awakenings", "awakenings"),
                               ("lights_off", "lights_off_min"), ("onset", "onset_min"),
                               ("final_awake", "final_awake_min"),
                               ("midsleep", "midsleep_min")):
            if attr in ("sol", "waso") and attr in imputed_fields:
                summary.n_imputed[attr] += 1
                continue
            value = getattr(night, attr, None)
            if value is not None:
                summary.values[key_name].append(float(value))

        if night.date is not None:
            first, last = summary.date_span or (night.date, night.date)
            summary.date_span = (min(first, night.date), max(last, night.date))

    result = [buckets[key] for key in order]
    for summary in result:
        _finalize(summary)
    return result


class GroupSummary:
    """대상자 요약들을 다시 사람 사이에서 요약한 것 (분석단위 = 사람)."""

    __slots__ = ("period", "n_subjects", "n_nights", "n_excluded", "metrics", "regularity")

    def __init__(self, period: Optional[str]):
        self.period = period
        self.n_subjects = 0
        self.n_nights = 0
        self.n_excluded = 0
        self.metrics: dict[str, dict] = {}
        self.regularity: dict = {}

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "n_subjects": self.n_subjects,
            "n_nights": self.n_nights,
            "n_excluded": self.n_excluded,
            "regularity_min": dict(self.regularity),
            "metrics": {k: dict(v) for k, v in self.metrics.items()},
        }


def summarize_group(summaries: Sequence[SubjectSummary],
                    period: Optional[str] = None,
                    conf: float = 0.95) -> GroupSummary:
    """대상자 요약 목록 → 집단 요약. n은 **사람 수**다."""
    group = GroupSummary(period)
    group.n_subjects = len(summaries)
    group.n_nights = sum(s.n_nights for s in summaries)
    group.n_excluded = sum(s.n_excluded for s in summaries)

    for key in LINEAR_KEYS:
        per_subject = [s.value(key) for s in summaries]
        vals = [v for v in per_subject if v is not None]
        entry = summarize(vals)
        low, high = mean_ci(vals, conf) if len(vals) >= 2 else (None, None)
        entry["ci_low"], entry["ci_high"] = low, high
        group.metrics[key] = entry

    for key in CIRCULAR_KEYS:
        per_subject = [s.value(key) for s in summaries]
        vals = [v for v in per_subject if v is not None]
        if vals:
            try:
                cm = circular_mean(vals)
            except ValueError:
                cm = None
            group.metrics[key] = {"n": len(vals), "mean": cm, "sd": circular_sd(vals),
                                  "ci_low": None, "ci_high": None}
        else:
            group.metrics[key] = {"n": 0, "mean": None, "sd": None,
                                  "ci_low": None, "ci_high": None}

    reg = [s.regularity for s in summaries if s.regularity is not None]
    group.regularity = summarize(reg)
    return group


class PeriodComparison:
    """두 시기 사이 대응표본 비교 (같은 사람이 양쪽 모두 기록한 경우만)."""

    __slots__ = ("metric", "period_a", "period_b", "n_pairs", "subjects",
                 "mean_a", "mean_b", "diffs", "ttest", "wilcoxon", "circular",
                 "wrap_unstable")

    def __init__(self, metric, period_a, period_b):
        self.metric = metric
        self.period_a = period_a
        self.period_b = period_b
        self.n_pairs = 0
        self.subjects: list[str] = []
        self.mean_a: Optional[float] = None
        self.mean_b: Optional[float] = None
        self.diffs: list[float] = []
        self.ttest: Optional[PairedResult] = None
        self.wilcoxon: Optional[WilcoxonResult] = None
        self.circular = False
        self.wrap_unstable = False

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "label": METRIC_LABEL.get(self.metric, self.metric),
            "period_a": self.period_a,
            "period_b": self.period_b,
            "n_pairs": self.n_pairs,
            "mean_a": self.mean_a,
            "mean_b": self.mean_b,
            "circular": self.circular,
            "wrap_unstable": self.wrap_unstable,
            "ttest": self.ttest.as_dict() if self.ttest else None,
            "wilcoxon": self.wilcoxon.as_dict() if self.wilcoxon else None,
        }


def compare_periods(summaries: Sequence[SubjectSummary],
                    period_a: str, period_b: str,
                    metrics: Sequence[str] = ALL_KEYS,
                    conf: float = 0.95) -> list[PeriodComparison]:
    """시기 A → B 변화를 지표별로 대응표본 검정한다 (차이 = B − A).

    양쪽 시기에 모두 나타난 대상자만 짝으로 쓴다. 시각형 지표의 차이는
    원형 최단차(−720..720분)로 계산한다 — 23:30 → 00:10 은 −1400분이
    아니라 +40분이다.
    """
    by_subject: dict[str, dict[Optional[str], SubjectSummary]] = {}
    for s in summaries:
        by_subject.setdefault(s.subject, {})[s.period] = s

    paired_subjects = [name for name, per in by_subject.items()
                       if period_a in per and period_b in per]

    results = []
    for metric in metrics:
        comp = PeriodComparison(metric, period_a, period_b)
        comp.circular = metric in CIRCULAR_KEYS
        a_vals, b_vals, subjects = [], [], []
        for name in paired_subjects:
            va = by_subject[name][period_a].value(metric)
            vb = by_subject[name][period_b].value(metric)
            if va is None or vb is None:
                continue
            a_vals.append(va)
            b_vals.append(vb)
            subjects.append(name)

        comp.subjects = subjects
        comp.n_pairs = len(subjects)
        if not subjects:
            results.append(comp)
            continue

        if comp.circular:
            comp.diffs = [circular_diff(b, a) for a, b in zip(a_vals, b_vals)]
            try:
                comp.mean_a = circular_mean(a_vals)
                comp.mean_b = circular_mean(b_vals)
            except ValueError:
                comp.mean_a = comp.mean_b = None
        else:
            comp.diffs = [b - a for a, b in zip(a_vals, b_vals)]
            comp.mean_a = mean(a_vals)
            comp.mean_b = mean(b_vals)

        # 시각형 지표의 차이는 ±720분에서 감긴다. 어떤 대상자의 변화가 그
        # 경계에 가까우면 +700분과 -740분이 같은 이동을 뜻하게 되어, 선형
        # 검정에 그대로 넣으면 서로 상쇄돼 "변화 없음, p=1.0"이 나온다.
        # 그런 자료에서는 수치를 지어내지 않고 검정을 생략한다.
        if comp.circular and comp.diffs and max(abs(d) for d in comp.diffs) > WRAP_UNSTABLE_MIN:
            comp.wrap_unstable = True
            results.append(comp)
            continue

        if comp.n_pairs >= 2:
            comp.ttest = paired_ttest(comp.diffs, conf)
            comp.wilcoxon = wilcoxon_signed_rank(comp.diffs)
        results.append(comp)
    return results


def period_levels(nights: Sequence) -> list[str]:
    """등장 순서대로 시기 값 목록 (빈 값 제외)."""
    seen: list[str] = []
    for night in nights:
        p = night.period
        if p and p not in seen:
            seen.append(p)
    return seen
