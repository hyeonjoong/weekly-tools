"""전체 분석 파이프라인 — 입력 레코드 → 합성·이질성·하위군·민감도·편향 결과 묶음."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .clinical import AbsoluteEffect, NNT_MEASURES, absolute_effect, pooled_control_risk
from .diagnostics import (
    BeggResult,
    EggerResult,
    LeaveOneOut,
    TrimFillResult,
    begg_test,
    egger_test,
    leave_one_out,
    trim_and_fill,
)
from .effects import LOG_MEASURES, MEASURE_SCALE, Study, back_transform, build_studies, measure_label
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
    begg: Optional[BeggResult] = None
    trimfill: Optional[TrimFillResult] = None
    absolute: Optional[AbsoluteEffect] = None
    warnings: List[str] = field(default_factory=list)
    source: str = ""
    primary_model: str = "random"
    #: 입력이 비(ratio)값이어서 로그로 변환해 합성한 경우(--log-input)
    log_input: bool = False
    outcome: Optional[str] = None

    @property
    def scale(self) -> str:
        """분석 척도: 'raw' | 'log' | 'fisherz' | 'logit'."""
        # --log-input 은 generic(이미 계산된 효과크기)에서 비(ratio) 값을 읽을 때만
        # 뜻이 있다. 다른 지표에 붙으면 척도만 log 로 바뀌어 값이 통째로
        # 지수변환되므로(위험차가 부호까지 뒤집힌다) 무시한다 — CLI 가 앞단에서
        # 막지만, 라이브러리로 직접 호출하는 경로까지 안전하게 둔다.
        if self.log_input and self.measure == "generic":
            return "log"
        return MEASURE_SCALE.get(self.measure, "raw")

    @property
    def is_log(self) -> bool:
        return self.scale == "log"

    @property
    def has_null_line(self) -> bool:
        """무효과선(효과 0)이 의미가 있는 지표인가.

        단일군 비율에는 "효과 없음"이 없다 — logit 0 은 50%%일 뿐이라
        숲그림에 세로선을 그으면 오해를 부른다.
        """
        return self.measure != "prop"

    @property
    def is_transformed(self) -> bool:
        """보고할 때 되돌려야 하는 척도인가."""
        return self.scale != "raw"

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
        """분석 척도 값을 보고 척도로 되돌린다.

        OR/RR은 지수변환, 상관계수는 tanh, 비율은 로지스틱. 극단적인 입력에서
        넘칠 수 있으므로 경계값으로 포화시킨다 — 예외를 던져 실행을 끊지 않는다.
        """
        return back_transform(value, self.scale)

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
            if self.is_transformed:
                d["estimate_back"] = self.back(p.estimate)
                d["ci_low_back"] = self.back(p.ci_low)
                d["ci_high_back"] = self.back(p.ci_high)
            if self.is_log:
                # 하위호환: 로그 지표에서는 예전 이름도 함께 남긴다.
                d["estimate_exp"] = d["estimate_back"]
                d["ci_low_exp"] = d["ci_low_back"]
                d["ci_high_exp"] = d["ci_high_back"]
            return d

        out: Dict[str, Any] = {
            "source": self.source,
            "measure": self.measure,
            "measure_label": measure_label(self.measure),
            "scale": self.scale,
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
                "tau2_ci_low": self.het.tau2_ci[0] if self.het.tau2_ci else None,
                "tau2_ci_high": self.het.tau2_ci[1] if self.het.tau2_ci else None,
                "I2_ci_low": self.het.i2_ci[0] if self.het.i2_ci else None,
                "I2_ci_high": self.het.i2_ci[1] if self.het.i2_ci else None,
                "ci_method": "Q-profile",
            },
            "prediction_interval": (
                {"low": self.pred[0], "high": self.pred[1]} if self.pred else None
            ),
            "warnings": list(self.warnings),
        }
        if self.pred and self.is_transformed:
            out["prediction_interval"].update(
                {"low_back": self.back(self.pred[0]), "high_back": self.back(self.pred[1])}
            )
            if self.is_log:
                out["prediction_interval"].update(
                    {"low_exp": self.back(self.pred[0]), "high_exp": self.back(self.pred[1])}
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
                    "I2_percent": r.i2,
                    "std_residual": r.std_resid,
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
                "note": " ".join(
                    n for n in (
                        "연구 10편 미만에서는 검정력이 낮아 해석에 주의가 필요합니다."
                        if self.egger.k < 10 else "",
                        "단일군 비율은 분산이 효과크기의 함수라 깔때기그림이 구조적으로 "
                        "비대칭해집니다 — 출판편향의 근거로 쓰지 마세요."
                        if self.measure == "prop" else "",
                        "이분형 지표에서는 효과크기와 표준오차의 구조적 상관 때문에 "
                        "Egger 위양성이 잦습니다."
                        if self.measure in ("or", "rr") else "",
                    ) if n
                ),
            }
        if self.begg:
            out["begg_test"] = {
                "kendall_tau": self.begg.tau,
                "kendall_S": self.begg.score,
                "z": self.begg.z,
                "p_value": self.begg.p,
                "p_method": self.begg.method,
                "k": self.begg.k,
            }
        if self.trimfill:
            tf = self.trimfill
            out["trim_and_fill"] = {
                "k0": tf.k0,
                "side": tf.side,
                "estimator": tf.estimator,
                "converged": tf.converged,
                "adjusted_estimate": tf.adjusted.estimate,
                "adjusted_ci_low": tf.adjusted.ci_low,
                "adjusted_ci_high": tf.adjusted.ci_high,
                "adjusted_p_value": tf.adjusted.p,
                "adjusted_estimate_back": self.back(tf.adjusted.estimate)
                if self.is_transformed else None,
                "note": "출판편향이 있었다면 어느 정도일지 보는 민감도 분석이며, "
                        "이질성이 큰 자료에서는 k0을 과대추정합니다.",
            }
        if self.absolute:
            ab = self.absolute
            out["absolute_effect"] = {
                "baseline_risk": ab.baseline_risk,
                "baseline_source": ab.baseline_source,
                "experimental_risk": ab.exp_risk,
                "risk_difference": ab.risk_diff,
                "risk_difference_ci_low": ab.risk_diff_low,
                "risk_difference_ci_high": ab.risk_diff_high,
                "per_1000": ab.per_1000,
                "nnt": ab.nnt,
                "nnt_ci_low": ab.nnt_low,
                "nnt_ci_high": ab.nnt_high,
                "is_harm": ab.is_harm,
                "ci_spans_null": ab.spans_null,
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
    do_trimfill: bool = True,
    trimfill_estimator: str = "L0",
    baseline_risk: Optional[float] = None,
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
    het = heterogeneity(studies, tau2_method=tau2_method, conf=conf)
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
    if tau2_method.upper() == "REML":
        from .meta import tau2_reml_converged

        if not tau2_reml_converged(studies)[1]:
            warnings.append(
                "REML tau² 반복이 수렴하지 않아 마지막 값을 사용했습니다 — "
                "--tau2 PM 또는 --tau2 DL 결과와 반드시 비교하세요."
            )

    egger = egger_test(studies) if do_egger else None
    begg = begg_test(studies) if do_egger else None

    if do_egger and egger is None and len(studies) < 3:
        warnings.append("연구가 3편 미만이라 Egger·Begg 비대칭 검정을 생략했습니다.")

    trimfill = None
    if do_trimfill and len(studies) >= 3:
        trimfill = trim_and_fill(
            studies, conf=conf, tau2_method=tau2_method,
            knapp_hartung=knapp_hartung, estimator=trimfill_estimator,
        )
        if trimfill is not None and not trimfill.converged:
            warnings.append(
                "trim-and-fill 반복이 수렴하지 않아 마지막 k0 값을 사용했습니다 — 결과를 신뢰하지 마세요."
            )

    absolute = None
    if measure in NNT_MEASURES:
        source_tag = "user"
        risk = baseline_risk
        if risk is None:
            risk = pooled_control_risk(studies)
            source_tag = "data"
        if risk is not None:
            absolute = absolute_effect(
                measure,
                (rand if primary_model == "random" else fixed).estimate,
                (rand if primary_model == "random" else fixed).ci_low,
                (rand if primary_model == "random" else fixed).ci_high,
                risk,
                baseline_source=source_tag,
            )
            if absolute is None:
                warnings.append(
                    "가정 대조군 위험(%g)으로는 절대효과·NNT를 계산할 수 없습니다 — 0 초과 1 미만이어야 "
                    "합니다.%s" % (
                        risk,
                        "" if baseline_risk is not None else
                        " 포함 연구의 대조군에서 사건이 전혀(또는 전원) 발생해 자동 추정이 불가능하니 "
                        "--baseline-risk 로 직접 지정하세요.",
                    )
                )
        elif measure in NNT_MEASURES and baseline_risk is None:
            warnings.append(
                "대조군 사건수/표본수를 알 수 없어 절대효과·NNT를 계산하지 못했습니다 — "
                "--baseline-risk 로 가정 대조군 위험을 지정하면 계산합니다."
            )
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
        # 표준오차는 작아졌지만 t 임계값이 z 보다 커서 구간 자체는 넓어질 수 있다.
        # (연구 2~3편에서 흔하다.) 실제 폭을 비교해 사실대로 적는다.
        from .distributions import normal_ppf, t_ppf

        z_half = normal_ppf(0.5 + conf / 2.0) * rand.se_model
        hk_half = 0.5 * (rand.ci_high - rand.ci_low)
        narrower = hk_half < z_half
        warnings.append(
            "Hartung–Knapp 표준오차가 모형기반 표준오차보다 작아졌습니다 "
            "(ad hoc 절단 미적용) — 실제 신뢰구간은 z 구간보다 %s. "
            "--no-hksj 결과와 함께 확인하세요."
            % ("좁습니다" if narrower else "넓습니다(자유도가 작아 t 임계값이 크기 때문)")
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
        begg=begg,
        trimfill=trimfill,
        absolute=absolute,
        warnings=warnings,
        source=source,
        primary_model=primary_model,
        log_input=log_input,
        outcome=outcome,
    )
