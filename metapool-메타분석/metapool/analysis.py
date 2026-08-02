"""전체 분석 파이프라인 — 입력 레코드 → 합성·이질성·하위군·민감도·편향 결과 묶음."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .diagnostics import EggerResult, LeaveOneOut, egger_test, leave_one_out
from .effects import LOG_MEASURES, Study, build_studies, measure_label
from .meta import (
    Heterogeneity,
    Pooled,
    SubgroupResult,
    fixed_effect,
    heterogeneity,
    prediction_interval,
    random_effects,
)

__all__ = ["Analysis", "run_analysis"]

#: leave-one-out 은 O(k^2) 이므로 연구가 아주 많으면 자동으로 건너뛴다.
LOO_MAX_K = 300


def _json_safe(value):
    """inf/nan 은 JSON 표준에 없으므로 null(None)로 바꾼다 (재귀)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class Analysis:
    measure: str
    conf: float
    studies: List[Study]
    fixed: Pooled
    random: Pooled
    het: Heterogeneity
    pred: Optional[tuple]
    subgroups: List[SubgroupResult] = field(default_factory=list)
    subgroup_test: Optional[Dict[str, Any]] = None
    loo: List[LeaveOneOut] = field(default_factory=list)
    egger: Optional[EggerResult] = None
    warnings: List[str] = field(default_factory=list)
    source: str = ""
    primary_model: str = "random"
    #: 입력이 비(ratio)값이어서 로그로 변환해 합성한 경우(--log-input)
    log_input: bool = False
    outcome: Optional[str] = None

    @property
    def is_log(self) -> bool:
        return self.measure in LOG_MEASURES or self.log_input

    @property
    def primary(self) -> Pooled:
        return self.random if self.primary_model == "random" else self.fixed

    @property
    def total_n(self) -> Optional[float]:
        values = [s.n_total for s in self.studies if s.n_total is not None]
        if len(values) != len(self.studies) or not values:
            return None
        return math.fsum(values)

    def back(self, value: float) -> float:
        """분석 척도 값을 보고 척도로 되돌린다(OR/RR이면 지수변환).

        극단적인 입력에서 exp가 넘칠 수 있으므로 무한대로 포화시킨다
        (리포트에서는 '—'로 표시된다). 예외를 던져 실행을 끊지 않는다.
        """
        if not self.is_log:
            return value
        try:
            return math.exp(value)
        except OverflowError:
            return math.inf if value > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        def pooled_dict(p: Pooled) -> Dict[str, Any]:
            d = {
                "model": p.model,
                "estimate": p.estimate,
                "se": p.se,
                "se_model": p.se_model,
                "ci_low": p.ci_low,
                "ci_high": p.ci_high,
                "statistic": p.stat,
                "statistic_type": "t" if p.ci_method == "HK" else "z",
                "p_value": p.p,
                "k": p.k,
                "tau2": p.tau2,
                "tau2_method": p.tau2_method,
                "ci_method": p.ci_method,
                "df": p.df,
            }
            if self.is_log:
                d["estimate_exp"] = math.exp(p.estimate)
                d["ci_low_exp"] = math.exp(p.ci_low)
                d["ci_high_exp"] = math.exp(p.ci_high)
            return d

        out: Dict[str, Any] = {
            "source": self.source,
            "measure": self.measure,
            "measure_label": measure_label(self.measure),
            "scale": "log" if self.is_log else "raw",
            "conf_level": self.conf,
            "k": len(self.studies),
            "total_n": self.total_n,
            "studies": [
                {
                    "label": s.label,
                    "subgroup": s.subgroup,
                    "effect": s.yi,
                    "se": s.sei,
                    "variance": s.vi,
                    "ci_low": s.ci(self._z)[0],
                    "ci_high": s.ci(self._z)[1],
                    "weight_fixed_pct": wf,
                    "weight_random_pct": wr,
                    "n_total": s.n_total,
                }
                for s, wf, wr in zip(
                    self.studies, self.fixed.weight_percent, self.random.weight_percent
                )
            ],
            "fixed_effect": pooled_dict(self.fixed),
            "random_effects": pooled_dict(self.random),
            "heterogeneity": {
                "Q": self.het.q,
                "df": self.het.df,
                "p_value": self.het.p,
                "I2_percent": self.het.i2,
                "H2": self.het.h2,
                "tau2": self.het.tau2,
                "tau": self.het.tau,
                "tau2_method": self.het.tau2_method,
            },
            "prediction_interval": (
                {"low": self.pred[0], "high": self.pred[1]} if self.pred else None
            ),
            "warnings": list(self.warnings),
        }
        if self.pred and self.is_log:
            out["prediction_interval"].update(
                {"low_exp": math.exp(self.pred[0]), "high_exp": math.exp(self.pred[1])}
            )
        if self.subgroups:
            out["subgroups"] = [
                {
                    "name": r.name,
                    "k": r.k,
                    "pooled": pooled_dict(r.pooled),
                    "I2_percent": r.het.i2 if r.het else None,
                    "tau2": r.het.tau2 if r.het else None,
                }
                for r in self.subgroups
            ]
            out["subgroup_test"] = self.subgroup_test
        if self.loo:
            out["leave_one_out"] = [
                {
                    "omitted": r.omitted,
                    "estimate": r.estimate,
                    "ci_low": r.ci_low,
                    "ci_high": r.ci_high,
                    "tau2": r.tau2,
                    "p_value": r.p,
                }
                for r in self.loo
            ]
        if self.egger:
            out["egger_test"] = {
                "intercept": self.egger.intercept,
                "se": self.egger.se,
                "t": self.egger.t,
                "df": self.egger.df,
                "p_value": self.egger.p,
                "k": self.egger.k,
                "note": "연구 10편 미만에서는 검정력이 낮아 해석에 주의가 필요합니다."
                if self.egger.k < 10
                else "",
            }
        return _json_safe(out)

    @property
    def _z(self) -> float:
        from .distributions import normal_ppf

        return normal_ppf(0.5 + self.conf / 2.0)


def run_analysis(
    records,
    measure: str,
    conf: float = 0.95,
    tau2_method: str = "DL",
    knapp_hartung: bool = True,
    do_subgroup: bool = True,
    do_loo: bool = True,
    do_egger: bool = True,
    cc: float = 0.5,
    log_input: bool = False,
    input_conf: float = 0.95,
    source: str = "",
    primary_model: str = "random",
    sort: str = "none",
    outcome: Optional[str] = None,
    loo_max_k: int = LOO_MAX_K,
) -> Analysis:
    """레코드 목록으로 전체 메타분석을 수행한다."""
    from .meta import MetaError, subgroup_analysis

    studies, warnings = build_studies(
        records, measure, conf=conf, cc=cc, log_input=log_input, input_conf=input_conf
    )
    if not studies:
        raise MetaError(
            "유효한 연구가 한 편도 없습니다. 아래 경고를 확인하세요:\n  - "
            + "\n  - ".join(warnings or ["(경고 없음)"])
        )

    if sort == "effect":
        studies.sort(key=lambda s: s.yi)
    elif sort == "label":
        studies.sort(key=lambda s: s.label)
    elif sort == "weight":
        studies.sort(key=lambda s: -1.0 / s.vi)

    fixed = fixed_effect(studies, conf=conf)
    rand = random_effects(
        studies, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung and len(studies) >= 2
    )
    het = heterogeneity(studies, tau2_method=tau2_method)
    pred = prediction_interval(rand, conf=conf)

    subgroups: List[SubgroupResult] = []
    sub_test = None
    if do_subgroup and any(s.subgroup for s in studies):
        subgroups, sub_test = subgroup_analysis(
            studies, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung
        )

    loo = []
    if do_loo:
        if len(studies) > loo_max_k:
            warnings.append(
                "연구가 %d편이라 하나씩 제외(민감도) 분석을 건너뛰었습니다 — 계산량이 연구 수의 "
                "제곱에 비례합니다. 필요하면 --sensitivity-max 로 상한을 올리세요."
                % len(studies)
            )
        else:
            loo = leave_one_out(
                studies, conf=conf, tau2_method=tau2_method, knapp_hartung=knapp_hartung
            )
    egger = egger_test(studies) if do_egger else None
    if do_egger and egger is None and len(studies) < 3:
        warnings.append("연구가 3편 미만이라 Egger 비대칭 검정을 생략했습니다.")
    if len(studies) < 3:
        warnings.append(
            "유효한 연구가 %d편뿐이라 예측구간·민감도 분석을 계산하지 않았고, 통합 추정치의 불확실성도 큽니다."
            % len(studies)
        )

    if rand.hk_degenerate:
        warnings.append(
            "모든 연구의 효과크기가 사실상 동일해 Hartung–Knapp 보정 분산이 0이 되었습니다 — "
            "모형기반 z 신뢰구간으로 대체했습니다."
        )
    elif rand.ci_method == "HK" and rand.se < rand.se_model:
        warnings.append(
            "이질성이 거의 없어 Hartung–Knapp 신뢰구간이 모형기반 구간보다 좁아졌습니다 "
            "(ad hoc 절단 미적용). --no-hksj 결과와 함께 확인하세요."
        )

    return Analysis(
        measure=measure,
        conf=conf,
        studies=studies,
        fixed=fixed,
        random=rand,
        het=het,
        pred=pred,
        subgroups=subgroups,
        subgroup_test=sub_test,
        loo=loo,
        egger=egger,
        warnings=warnings,
        source=source,
        primary_model=primary_model,
        log_input=log_input,
        outcome=outcome,
    )
