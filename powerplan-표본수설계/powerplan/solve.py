"""표본수 탐색과 현실 보정 — 프로토콜에 실제로 적히는 숫자를 만든다.

계획 표본수는 세 단계를 거친다:

1. **분석 표본수** — 통계적으로 필요한 수 (설계별 검정력 계산)
2. **× 설계효과(design effect)** — 군집(클러스터) 무작위배정일 때 1 + (m−1)·ICC
3. **÷ (1 − 탈락률)** — 중도탈락/결측을 감당할 모집 표본수

세 숫자를 모두 보여주는 것이 중요하다. 프로토콜 심사에서 "분석 대상 n"과
"모집 n"을 구분하지 않아 지적받는 일이 흔하기 때문이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .designs import Design
from .sequential import (
    SPENDING_KINDS,
    check_interim,
    check_timing,
    power_from_fixed,
    sequential_notes,
    sequential_plan,
    sequential_references,
)
from .validate import PowerPlanError, as_float, as_int

__all__ = ["Adjustments", "smallest_unit", "continuous_unit", "make_plan", "MAX_UNIT"]

#: 군당 표본수 탐색 상한 (이보다 크면 설계 자체를 다시 생각해야 한다)
MAX_UNIT = 1_000_000
_SENSITIVITY_POWERS = (0.70, 0.80, 0.85, 0.90, 0.95)
_SENSITIVITY_FACTORS = (0.8, 1.0, 1.2)
_SENSITIVITY_UNIT_FACTORS = (0.5, 0.75, 1.0, 1.5, 2.0)


@dataclass(frozen=True)
class Adjustments:
    """현실 보정 — 탈락, 군집설계, 다중비교 α 보정."""

    dropout: float = 0.0
    cluster_size: int | None = None
    cluster_icc: float | None = None
    comparisons: int = 1
    alpha_method: str = "bonferroni"
    #: 중간분석 횟수 (최종분석 제외). None이면 고정설계.
    interim: int | None = None
    spending: str = "obf"
    timing: tuple[float, ...] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        dropout = as_float("--dropout", self.dropout)
        if not (0.0 <= dropout < 1.0):
            raise PowerPlanError(
                f"--dropout: 0 이상 1 미만의 비율이어야 합니다 (받은 값: {self.dropout!r}). "
                "15%는 0.15로 적습니다"
            )
        object.__setattr__(self, "dropout", dropout)
        if (self.cluster_size is None) != (self.cluster_icc is None):
            raise PowerPlanError(
                "군집설계는 --cluster-size(군집당 인원)와 --cluster-icc(군집내 상관)를 "
                "함께 지정해야 합니다"
            )
        if self.cluster_size is not None:
            m = as_int("--cluster-size", self.cluster_size, minimum=1)
            icc = as_float("--cluster-icc", self.cluster_icc)
            if not (0.0 <= icc < 1.0):
                raise PowerPlanError(
                    f"--cluster-icc: 0 이상 1 미만이어야 합니다 (받은 값: {self.cluster_icc!r})"
                )
            object.__setattr__(self, "cluster_size", m)
            object.__setattr__(self, "cluster_icc", icc)
        comparisons = as_int("--comparisons", self.comparisons, minimum=1)
        object.__setattr__(self, "comparisons", comparisons)
        if self.alpha_method not in ("bonferroni", "sidak", "none"):
            raise PowerPlanError(
                f"--alpha-method: bonferroni, sidak, none 중 하나여야 합니다 "
                f"(받은 값: {self.alpha_method!r})"
            )
        if self.interim is not None:
            interim = check_interim(as_int("--interim", self.interim, minimum=1))
            object.__setattr__(self, "interim", interim)
            if self.spending not in SPENDING_KINDS:
                raise PowerPlanError(
                    f"--spending: {', '.join(SPENDING_KINDS)} 중 하나여야 합니다 "
                    f"(받은 값: {self.spending!r})"
                )
            # 여기서 미리 검증해 두면 설계 계산 전에 잘못된 --timing을 거를 수 있다
            object.__setattr__(self, "timing", check_timing(interim, self.timing))
        elif self.timing is not None:
            raise PowerPlanError("--timing은 --interim(중간분석 횟수)과 함께 써야 합니다")

    @property
    def design_effect(self) -> float:
        """군집 설계효과 DE = 1 + (m − 1)·ICC (군집설계가 아니면 1.0)."""
        if self.cluster_size is None or self.cluster_icc is None:
            return 1.0
        return 1.0 + (self.cluster_size - 1) * self.cluster_icc

    @property
    def inflation(self) -> float:
        """분석 표본수 → 모집 표본수 배율."""
        return self.design_effect / (1.0 - self.dropout)

    def adjusted_alpha(self, alpha: float) -> tuple[float, dict | None]:
        """다중비교 보정된 α와 그 설명."""
        if self.comparisons <= 1 or self.alpha_method == "none":
            return alpha, None
        if self.alpha_method == "bonferroni":
            used = alpha / self.comparisons
            label = f"Bonferroni: α/{self.comparisons}"
        else:
            used = 1.0 - (1.0 - alpha) ** (1.0 / self.comparisons)
            label = f"Šidák: 1 − (1 − α)^(1/{self.comparisons})"
        if used <= 0.0 or used >= 1.0:  # 방어적 (comparisons 검증 이후엔 불가능)
            raise PowerPlanError("다중비교 보정 결과 α가 유효 범위를 벗어났습니다")
        return used, {"method": self.alpha_method, "comparisons": self.comparisons,
                      "label": label, "alpha_used": used}


def _design_cap(design: Design, cap: int) -> int:
    """설계가 스스로 정한 표본수 상한을 존중한다 (정확검정 등 계산량 제한)."""
    return min(cap, int(getattr(design, "max_unit", cap) or cap))


def smallest_unit(design: Design, target_power: float, cap: int = MAX_UNIT) -> int:
    """target_power를 만족하는 최소 unit(군당 n 등)을 정수로 찾는다.

    이분법으로 대략 찾은 뒤 **정수 배분 기준 검정력**으로 위아래를 훑어 보정한다.
    (배분비가 정수가 아니면 ceil 때문에 검정력이 계단식으로 변하므로 필요하다.)
    """
    if not (0.0 < target_power < 1.0):
        raise PowerPlanError(f"--power: 0과 1 사이여야 합니다 (받은 값: {target_power!r})")

    cap = _design_cap(design, cap)

    def alloc_power(unit: int) -> float:
        return design.power_of_allocation(design.allocation(unit))

    lo = design.min_unit
    if alloc_power(lo) >= target_power:
        return lo
    hi = max(lo * 2, 4)
    while hi < cap and design.power(hi) < target_power:
        hi *= 2
    hi = min(hi, cap)
    if alloc_power(hi) < target_power:
        raise PowerPlanError(
            f"군당 {cap:,}명으로도 검정력 {target_power:.0%}에 도달하지 못합니다 "
            f"(상한에서의 검정력 {alloc_power(hi):.3f}). 효과크기 가정이 너무 작거나 "
            "목표 검정력이 너무 높습니다 — 설계를 다시 검토하세요"
        )
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if design.power(mid) >= target_power:
            hi = mid
        else:
            lo = mid
    n = hi
    # 아래로: 정수 배분 덕에 더 작은 n으로도 충분할 수 있다
    while n > design.min_unit and alloc_power(n - 1) >= target_power:
        n -= 1
    # 위로: 연속성 보정 등으로 검정력이 비단조일 때 안전한 n을 고른다
    if not design.monotone:
        guard = 0
        while guard < 500 and n + 3 <= cap:
            if all(alloc_power(n + k) >= target_power for k in range(4)):
                break
            n += 1
            guard += 1
    if alloc_power(n) < target_power:  # 방어적
        raise PowerPlanError("표본수 탐색이 수렴하지 않았습니다 (설계 파라미터를 확인하세요)")
    return n


def continuous_unit(design: Design, target_power: float, cap: int = MAX_UNIT) -> float:
    """검정력이 정확히 target_power가 되는 **연속** unit.

    군차별설계의 팽창계수를 곱할 기준값이다. 정수로 올린 뒤 곱하면 올림이 두 번
    들어가 표본수가 필요 이상으로 커진다.
    """
    if not (0.0 < target_power < 1.0):
        raise PowerPlanError(f"--power: 0과 1 사이여야 합니다 (받은 값: {target_power!r})")
    cap = _design_cap(design, cap)
    lo = float(design.min_unit)
    if design.power(lo) >= target_power:
        return lo
    hi = max(lo * 2.0, 4.0)
    while hi < cap and design.power(hi) < target_power:
        hi *= 2.0
    hi = min(hi, float(cap))
    if design.power(hi) < target_power:
        raise PowerPlanError(
            f"군당 {cap:,}명으로도 검정력 {target_power:.0%}에 도달하지 못합니다 — "
            "효과크기 가정이 너무 작거나 목표 검정력이 너무 높습니다"
        )
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:
            break
        if design.power(mid) >= target_power:
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1e-9 * max(1.0, hi):
            break
    return hi


def _inflate(alloc: dict, factor: float) -> dict:
    """배분 딕셔너리의 사람 수 항목만 배율 적용 후 올림."""
    out: dict = {}
    for key, value in alloc.items():
        if key in ("k",):  # 군 수 등 사람 수가 아닌 항목은 그대로
            out[key] = value
            continue
        out[key] = max(1, math.ceil(value * factor - 1e-9))
    # total은 개별 군의 합으로 다시 맞춘다 (올림 오차 누적 방지)
    parts = [v for k, v in out.items() if k not in ("total", "k")]
    if "total" in out and parts:
        if "n_per_group" in out:
            out["total"] = out["n_per_group"] * out["k"]
        elif "n_per_sequence" in out:
            out["total"] = out["n_per_sequence"] * 2      # AB · BA 두 순서
        else:
            out["total"] = sum(parts)
    return out


def _sensitivity_solve(design: Design, adj: Adjustments) -> dict:
    """목표 검정력 × 효과크기 가정에 따른 필요 표본수 표.

    중간분석(군차별설계)을 켜면 목표 검정력마다 팽창계수가 달라지므로 행마다
    다시 계산한다 — 헤드라인 표본수와 표가 어긋나면 안 되기 때문이다.
    """
    cells = []
    for power in _SENSITIVITY_POWERS:
        row = []
        inflation = 1.0
        if adj.interim is not None:
            inflation = sequential_plan(
                adj.interim, design.alpha, getattr(design, "sides", 1), power,
                adj.spending, adj.timing)["inflation"]
        for factor in _SENSITIVITY_FACTORS:
            try:
                scaled = design.scaled(factor)
                if inflation != 1.0:
                    unit = max(scaled.min_unit, math.ceil(
                        continuous_unit(scaled, power) * inflation - 1e-9))
                else:
                    unit = smallest_unit(scaled, power)
                alloc = scaled.allocation(unit)
                enroll = _inflate(alloc, adj.inflation)
                row.append({"unit": unit, "total": alloc.get("total"),
                            "enroll_total": enroll.get("total"),
                            "single_arm": alloc.get("total") == unit})
            except PowerPlanError:
                row.append(None)
        cells.append(row)
    return {
        "kind": "n_by_power_and_effect",
        "row_label": "목표 검정력",
        "rows": _SENSITIVITY_POWERS,
        "col_label": getattr(design, "sensitivity_kr", "효과크기 가정 배율"),
        "cols": _SENSITIVITY_FACTORS,
        # "효과×0.8"이 실제로 어떤 값인지 열 머리에 함께 보여 준다
        "col_labels": [f"×{f:g} ({design.sensitivity_value(f)})"
                       for f in _SENSITIVITY_FACTORS],
        "cells": cells,
    }


def _sensitivity_power(design: Design, unit: float, adj: Adjustments,
                       seq: dict | None = None) -> dict:
    """확보 가능한 표본수 배율에 따른 검정력 표."""
    cells = []
    for factor in _SENSITIVITY_UNIT_FACTORS:
        u = max(design.min_unit, math.floor(unit * factor))
        alloc = design.allocation(u)
        power = design.power_of_allocation(alloc)
        if seq is not None:
            power = power_from_fixed(seq, power)
        cells.append({
            "factor": factor,
            "unit": u,
            "total": alloc.get("total"),
            "power": power,
        })
    return {"kind": "power_by_n", "rows": cells}


def _shrink_to_minimum(design: Design, seq: dict, unit: int, target_power: float,
                       limit: int = 50) -> int:
    """군차별설계 검정력 기준으로 더 작은 정수 unit이 되는지 아래로 훑는다."""
    def gs_power(u: int) -> float:
        return power_from_fixed(seq, design.power_of_allocation(design.allocation(u)))

    if gs_power(unit) < target_power:      # 방어적: 팽창계수가 모자란 경우 위로
        guard = 0
        while guard < limit and gs_power(unit) < target_power:
            unit += 1
            guard += 1
        return unit
    guard = 0
    while unit > design.min_unit and guard < limit and gs_power(unit - 1) >= target_power:
        unit -= 1
        guard += 1
    return unit


def _sequential_detail(seq: dict, plan: dict, design: Design) -> dict:
    """경계표에 '그 시점까지 몇 명/몇 건인가'를 붙인다 (프로토콜에 그대로 들어간다)."""
    alloc = plan["analysis"]["allocation"]
    total = alloc.get("total")
    info = design.information(alloc)
    info_total = info.get("total")
    looks = []
    for i, t in enumerate(seq["timing"]):
        looks.append({
            "look": i + 1,
            "is_final": i == len(seq["timing"]) - 1,
            "information": t,
            "bound_z": seq["bounds"][i],
            "nominal_p": seq["nominal_p"][i],
            "cumulative_alpha": seq["cumulative_alpha"][i],
            "stop_prob_h1": seq["stop_prob_h1"][i],
            # 정보량이 인원이 아닌 설계(생존분석)에서는 '그 시점의 등록 인원'을
            # 계산할 방법이 없다(달력 시간이 필요하다). 오해를 부르므로 아예 비운다.
            "n_total": (math.ceil(total * t - 1e-9)
                        if total and info.get("label", "누적 N") == "누적 N" else None),
            # 실제로 α를 지배하는 정보량 (생존분석이면 사건 수)
            "information_amount": (math.ceil(info_total * t - 1e-9)
                                   if info_total else None),
        })
    detail = dict(seq)
    detail["looks_detail"] = looks
    detail["information_label"] = info.get("label", "누적 N")
    detail["information_unit"] = info.get("unit", "명")
    detail["information_caveat"] = info.get("caveat")
    detail["information_total"] = info_total
    if total:
        detail["expected_n_h1"] = seq["expected_fraction_h1"] * total
        detail["expected_n_h0"] = seq["expected_fraction_h0"] * total
    return detail


def make_plan(design: Design, *, target_power: float | None = None,
              unit: int | None = None, adjustments: Adjustments | None = None,
              sensitivity: bool = False, alpha_adjustment: dict | None = None) -> dict:
    """설계 + 목표(검정력 또는 표본수) → 프로토콜에 쓸 계획 딕셔너리.

    `target_power`만 주면 표본수를 구하고, `unit`(확보 가능한 군당 n)만 주면
    검정력을 구한다. 둘 다 주면 검정력을 구하되 목표 달성 여부도 함께 알려준다.
    """
    adj = adjustments or Adjustments()
    if target_power is None and unit is None:
        raise PowerPlanError(
            "목표 검정력(--power)이나 확보 가능한 표본수(--n) 중 하나는 지정해야 합니다"
        )

    plan: dict = {
        "design": {
            "key": design.key,
            "name_kr": design.name_kr,
            "name_en": design.name_en,
            "test_kr": design.test_kr,
            "test_en": design.test_en,
            "unit_kr": design.unit_kr,
            "time_unit": getattr(design, "time_unit", None),
            "alpha": design.alpha,
            "sides": getattr(design, "sides", 1),
            "effect": design.effect(),
            "alpha_adjustment": alpha_adjustment,
        },
        "adjustments": {
            "dropout": adj.dropout,
            "design_effect": adj.design_effect,
            "cluster_size": adj.cluster_size,
            "cluster_icc": adj.cluster_icc,
            "inflation": adj.inflation,
        },
        "notes": list(design.notes()),
        "references": list(design.references()),
    }

    if adj.design_effect != 1.0 and not getattr(design, "supports_cluster", True):
        reason = getattr(design, "cluster_reason", "")
        raise PowerPlanError(
            f"--cluster-size/--cluster-icc는 {design.key} 설계에 적용할 수 없습니다."
            + (f" {reason}" if reason else "")
        )
    seq = None
    if adj.interim is not None:
        if not getattr(design, "supports_sequential", True):
            reason = getattr(design, "sequential_reason", "")
            raise PowerPlanError(
                f"--interim(중간분석)은 {design.key} 설계에 적용할 수 없습니다."
                + (f" {reason}" if reason else "")
            )
        seq = sequential_plan(
            adj.interim, design.alpha, getattr(design, "sides", 1),
            float(target_power) if target_power is not None else 0.80,
            adj.spending, adj.timing)

    if unit is None:
        if seq is not None:
            # 고정설계의 (연속) 필요 표본수 × 팽창계수 → 최대 표본수.
            # 올림이 두 번 들어가면 필요 없는 1명이 붙으므로, 군차별설계 기준으로
            # 실제 검정력을 보며 아래로 훑어 최소값을 찾는다.
            fixed_unit = continuous_unit(design, float(target_power))
            unit_found = max(design.min_unit,
                             math.ceil(fixed_unit * seq["inflation"] - 1e-9))
            unit_found = _shrink_to_minimum(design, seq, unit_found,
                                            float(target_power))
            plan["fixed_design"] = {
                "unit": math.ceil(fixed_unit - 1e-9),
                "allocation": design.allocation(smallest_unit(design, float(target_power))),
            }
        else:
            unit_found = smallest_unit(design, float(target_power))
        alloc = design.allocation(unit_found)
        plan["direction"] = "solve_n"
        plan["target_power"] = float(target_power)
        # 개인배정 기준 '유효 표본수' — 검정력 계산이 요구하는 값
        plan["effective"] = {"unit": unit_found, "allocation": alloc,
                             "power": design.power_of_allocation(alloc)}
        plan["achieved_power"] = plan["effective"]["power"]
        if seq is not None:
            # 같은 n에서 고정설계 검정력 → 군차별설계 검정력으로 옮긴다
            plan["fixed_power_at_n"] = plan["achieved_power"]
            plan["achieved_power"] = power_from_fixed(seq, plan["achieved_power"])
            plan["effective"]["power"] = plan["achieved_power"]
        # 군집설계라면 실제로 **분석해야 하는 인원**은 유효 표본수 × 설계효과다
        if adj.design_effect != 1.0:
            analysis_alloc = _inflate(alloc, adj.design_effect)
            plan["analysis"] = {
                # 사람 수이므로 올림한 정수로 (JSON 소비자가 소수를 받지 않게)
                "unit": math.ceil(unit_found * adj.design_effect - 1e-9),
                "unit_exact": unit_found * adj.design_effect,
                "allocation": analysis_alloc,
                "power": plan["achieved_power"]}
        else:
            plan["analysis"] = dict(plan["effective"])
    else:
        unit_given = as_int("--n", unit, minimum=1)
        # 모집 n → 분석 가능한 유효 n (탈락 후, 군집 설계효과로 나눔)
        analysed = unit_given * (1.0 - adj.dropout)
        effective = analysed / adj.design_effect
        if effective < design.min_unit:
            raise PowerPlanError(
                f"--n {unit_given}은 이 설계에 너무 작습니다 "
                f"(탈락·설계효과 반영 후 유효 {effective:.2f}, 최소 {design.min_unit})"
            )
        alloc_given = design.allocation(unit_given)
        plan["direction"] = "compute_power"
        plan["given"] = {"unit": unit_given, "allocation": alloc_given,
                         "effective_unit": effective}
        plan["achieved_power"] = design.power(effective)
        if seq is not None:
            plan["fixed_power_at_n"] = plan["achieved_power"]
            plan["achieved_power"] = power_from_fixed(seq, plan["achieved_power"])
        if target_power is not None:
            plan["target_power"] = float(target_power)
            plan["meets_target"] = plan["achieved_power"] >= float(target_power)
            try:
                if seq is not None:
                    need = max(design.min_unit, math.ceil(
                        continuous_unit(design, float(target_power)) * seq["inflation"]
                        - 1e-9))
                else:
                    need = smallest_unit(design, float(target_power))
                need_alloc = design.allocation(need)
                plan["needed"] = {
                    "unit": need,
                    "allocation": need_alloc,
                    "enrollment": _inflate(need_alloc, adj.inflation),
                }
            except PowerPlanError as exc:
                plan["needed_error"] = str(exc)
        base_alloc = design.allocation(max(effective, design.min_unit))
        plan["analysis"] = {"unit": effective, "allocation": base_alloc,
                            "power": plan["achieved_power"]}

    # 모집 표본수 = 분석 표본수 ÷ (1 − 탈락률), 군집설계면 군집 단위로 올림
    if plan["direction"] == "solve_n":
        enroll = _inflate(plan["analysis"]["allocation"], 1.0 / (1.0 - adj.dropout))
        plan["enrollment"] = {"allocation": enroll}
    else:
        plan["enrollment"] = {"allocation": plan["given"]["allocation"]}

    if adj.cluster_size:
        m = adj.cluster_size
        clusters = {}
        for key, value in plan["enrollment"]["allocation"].items():
            if key in ("total", "k"):
                continue
            clusters[key] = math.ceil(value / m)
        plan["enrollment"]["clusters"] = clusters
        plan["enrollment"]["cluster_size"] = m
        if plan["direction"] == "solve_n":
            # 군집은 쪼갤 수 없으므로 모집 인원 자체를 군집 단위로 맞춘다
            # (예전에는 '모집 162명'과 '군집 17개×10명=170명'이 동시에 표시돼 모순이었다)
            plan["enrollment"]["before_cluster_rounding"] = dict(
                plan["enrollment"]["allocation"])
            rounded = {key: count * m for key, count in clusters.items()}
            if "k" in plan["enrollment"]["allocation"]:
                rounded["k"] = plan["enrollment"]["allocation"]["k"]
                rounded["total"] = rounded.get("n_per_group", 0) * rounded["k"]
            else:
                rounded["total"] = sum(v for k, v in rounded.items() if k != "k")
            plan["enrollment"]["allocation"] = rounded
        plan["notes"].append(
            f"군집 무작위배정: 설계효과 DE = 1 + ({m} − 1)×{adj.cluster_icc:g} "
            f"= {adj.design_effect:.3f}배. 검정력 계산의 유효 표본수에 DE를 곱한 값이 "
            "실제로 **분석해야 하는 인원**이며, 군집은 쪼갤 수 없으므로 모집 인원은 "
            "군집 수 × 군집당 인원으로 올렸습니다."
        )
        plan["notes"].append(
            "군집 수가 적으면(군당 < 10개) DE 보정만으로는 부족합니다 — "
            "혼합효과모형 기반 계산(자유도 2(군집수−1))을 고려하세요."
        )
    if adj.dropout > 0.0:
        if plan["direction"] == "solve_n":
            chain = [f"유효 {plan['effective']['allocation'].get('total')}명"]
            if adj.design_effect != 1.0:
                chain.append(f"×DE {adj.design_effect:.3f} → 분석 "
                             f"{plan['analysis']['allocation'].get('total')}명")
            chain.append(f"÷(1−{adj.dropout:g}) → 모집 "
                         f"{plan['enrollment']['allocation'].get('total')}명")
            plan["notes"].append(
                f"탈락률 {adj.dropout:.0%} 가정. 표본수 계산 흐름(총 N 기준): "
                + " ".join(chain)
            )
        else:
            plan["notes"].append(
                f"탈락률 {adj.dropout:.0%} 가정: 모집 n에서 탈락을 빼고 검정력을 계산했습니다."
            )

    if alpha_adjustment:
        plan["notes"].append(
            f"다중비교 보정({alpha_adjustment['label']}) 적용 → 실제 사용한 α = "
            f"{alpha_adjustment['alpha_used']:.5g}"
        )
        plan["notes"].append(
            "⚠ α를 나누는 보정은 **여러 평가변수 중 하나만 성공하면 되는** 경우에 "
            "맞습니다. (1) **공동 1차 평가변수**(전부 성공해야 함)라면 α가 아니라 "
            "**검정력**을 올려야 합니다 — 두 개면 각각 약 0.9를 잡아야 전체가 0.8이 "
            "됩니다. (2) 계층적/관문(gatekeeping) 검정 순서를 SAP에 정해 두었다면 "
            "보정이 아예 필요 없습니다. 이 옵션을 쓰기 전에 SAP의 다중성 전략을 "
            "먼저 확인하세요.")

    if seq is not None:
        plan["sequential"] = _sequential_detail(seq, plan, design)
        caveat = plan["sequential"].get("information_caveat")
        if caveat:
            plan["notes"].append(caveat)
        plan["notes"].extend(sequential_notes(seq))
        plan["references"].extend(sequential_references())

    plan["design_lines"] = list(design.plan_lines(plan["analysis"]["allocation"]))

    # 검정력이 톱니 모양인 설계(정확 이항검정)에서는 '처음 목표를 넘는 n'과
    # '그 뒤로도 계속 넘는 n'이 다르다. 심사자는 보통 앞의 것을 재현하므로
    # 두 숫자를 함께 밝힌다.
    if (plan["direction"] == "solve_n" and not getattr(design, "monotone", True)
            and hasattr(design, "smallest_n_reaching")):
        chosen = plan["effective"]["unit"]
        first = design.smallest_n_reaching(float(target_power), cap=chosen)
        if first is not None and first < chosen:
            plan["design_lines"].append((
                "처음 도달하는 n",
                f"{first:,}명 (검정력 {design.power(first):.1%}) — 이 툴은 그 뒤로도 "
                f"계속 목표를 넘는 {chosen:,}명을 씁니다. 다른 도구는 앞의 값을 "
                "쓰는 경우가 많으니 어느 규칙인지 프로토콜에 밝히세요"))

    if sensitivity:
        if plan["direction"] == "solve_n":
            plan["sensitivity"] = _sensitivity_solve(design, adj)
        else:
            plan["sensitivity"] = _sensitivity_power(
                design, plan["analysis"]["unit"], adj, seq)
    return plan
