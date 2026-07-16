"""Match idea templates to a manifest, assess feasibility, and rank."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .knowledge import IDEA_TEMPLATES
from .manifest import MODALITY_LABEL_KO, Manifest
from .power import (
    detectable_effect,
    effect_magnitude,
    required_total_n,
    scale_effect,
)

# How each effect metric renders in the "detectable effect" cell.
_METRIC_LABEL = {"r": "r", "d": "d", "d_z": "d_z", "f2": "f²"}

# Effect-size sensitivity strip: required N is recomputed at these multiples of
# the template's planned effect so the reader sees how the target moves if the
# true effect is smaller (conservative) or larger (optimistic) than assumed.
SENSITIVITY_FACTORS = [("보수적", 2.0 / 3.0), ("계획", 1.0), ("낙관적", 1.5)]


@dataclass
class IdeaResult:
    idea_id: str
    title: str
    modalities: list  # canonical keys used (required ∩ available)
    hypothesis: str
    predictors: list
    outcomes: list
    analysis: str
    design: str
    journal: str
    novelty: str
    required_n: object  # int, or None when the design has no closed-form target
    available_n: object  # int or None
    feasible: object  # True / False / None (unknown)
    matched_variables: list
    score: float
    notes: list = field(default_factory=list)
    exploratory: bool = False  # design with no power target (e.g. clustering)
    detectable: object = None  # {"metric":..., "value":...} or None
    recruit_n: object = None  # required_n inflated for attrition, or None
    n_sensitivity: list = field(default_factory=list)  # required-N vs effect strip

    @property
    def feasibility_label(self) -> str:
        if self.exploratory:
            return "탐색적(표본 판정 비적용)"
        if self.feasible is None:
            return "표본수 미상"
        if self.feasible:
            return "충분 가능"
        return "표본 부족 우려"

    @property
    def detectable_label(self) -> str:
        """Compact minimum-detectable-effect cell, e.g. ``r≥0.29`` or ``—``."""
        if not self.detectable:
            return "—"
        metric = _METRIC_LABEL.get(self.detectable["metric"], self.detectable["metric"])
        return f"{metric}≥{self.detectable['value']:.2f}"


def _modality_index(manifest: Manifest):
    """Group datasets by canonical modality.

    Returns ``(index, conflicts)`` where ``index[mod] = {'n':minN, 'vars':set}``.
    When the same modality appears with different sample sizes (e.g. two EEG
    cohorts) we keep the **minimum** — the number of subjects with *linked*
    multimodal data cannot exceed the smaller set, so min is the conservative
    choice for a feasibility check (taking max would overstate feasibility).
    """
    index: dict = {}
    seen_ns: dict = {}
    for d in manifest.datasets:
        if not d.modality:
            continue
        slot = index.setdefault(d.modality, {"n": None, "vars": set()})
        slot["vars"].update(v.lower() for v in d.variables)
        if d.n is not None:
            seen_ns.setdefault(d.modality, set()).add(d.n)
            slot["n"] = d.n if slot["n"] is None else min(slot["n"], d.n)
    conflicts = {m: sorted(ns) for m, ns in seen_ns.items() if len(ns) > 1}
    return index, conflicts


def evaluate(
    manifest: Manifest,
    alpha: float = 0.05,
    power: float = 0.80,
    templates=IDEA_TEMPLATES,
    dropout: float = 0.0,
    effect_scale: float = 1.0,
) -> list:
    """Return ranked :class:`IdeaResult` list for ideas whose modalities are met.

    ``dropout`` (0 <= p < 1) inflates each idea's recruitment target to
    ``ceil(required_n / (1 - p))`` so a prospective study still finishes with
    the analyzable N; it does not change the feasibility verdict, which compares
    the *analyzable* required N against the N already in hand.

    ``effect_scale`` (>0) multiplies every template's assumed effect magnitude
    before sizing — set it below 1 to plan against a smaller (more conservative)
    true effect. The per-idea sensitivity strip additionally reports required N
    at a spread of effect magnitudes regardless of this global scale.
    """
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout must satisfy 0 <= dropout < 1")
    if effect_scale <= 0.0:
        raise ValueError("effect_scale must be > 0")
    index, conflicts = _modality_index(manifest)
    for mod, ns in conflicts.items():
        msg = (
            f"모달리티 '{modality_label(mod)}'에 서로 다른 n {ns}가 있어 "
            f"연결 가능한 최소값({min(ns)})을 보수적으로 사용합니다."
        )
        if msg not in manifest.warnings:
            manifest.warnings.append(msg)
    available = set(index)
    results: list = []

    for t in templates:
        required = t["required"]
        if not all(m in available for m in required):
            continue

        used = list(required) + [m for m in t.get("optional", []) if m in available]

        # Limiting sample size = smallest available n among required modalities.
        ns = [index[m]["n"] for m in required if index[m]["n"] is not None]
        all_known = len(ns) == len(required)
        available_n = min(ns) if all_known and ns else None

        # Apply the global effect-size scale before sizing / feasibility.
        base_effect = t["effect"]
        planned_effect = scale_effect(base_effect, effect_scale)

        req_n = required_total_n(planned_effect, alpha=alpha, power=power)
        exploratory = req_n is None
        if exploratory or available_n is None:
            feasible = None
        else:
            feasible = available_n >= req_n

        # Recruitment target inflated for expected attrition (planning aid only).
        recruit_n = None
        if req_n is not None and dropout > 0.0:
            recruit_n = math.ceil(req_n / (1.0 - dropout))

        # Sensitivity: smallest effect the *available* N could detect.
        detectable = detectable_effect(
            planned_effect, available_n, alpha=alpha, power=power
        )

        # Required-N-vs-effect strip: how the target moves if the true effect is
        # smaller/larger than the planned prior. Skipped for exploratory designs.
        n_sensitivity = []
        if not exploratory:
            for label, factor in SENSITIVITY_FACTORS:
                eff = scale_effect(planned_effect, factor)
                n_sensitivity.append({
                    "label": label,
                    "factor": factor,
                    "effect_value": round(effect_magnitude(eff), 4),
                    "required_n": required_total_n(eff, alpha=alpha, power=power),
                })

        # Variables available in the modalities this idea draws on. (Template
        # predictors/outcomes are free-text concepts, so this is the pool of
        # usable columns, not a token-by-token match — named accordingly.)
        present_vars = set().union(*(index[m]["vars"] for m in used)) if used else set()
        matched = sorted(present_vars)

        # Scoring within a feasibility tier: prefer multimodal, variable-rich
        # ideas. The variable term is CAPPED so a column-rich modality can't
        # outweigh the multimodal bonus (one extra modality = +4).
        score = 0.0
        score += 4.0 * (len(required) - 1)  # multimodal bonus
        score += 0.4 * min(len(matched), 5)  # capped at +2
        # tie-break: more optional modalities available adds a little.
        score += 0.5 * (len(used) - len(required))

        notes = []
        if len(required) > 1:
            notes.append(
                "동일 피험자에서 모달리티가 연결(linked)돼 있어야 함 — "
                "같은 세션/대상자 매칭 확인 필요."
            )
        if feasible is False:
            notes.append(
                f"가정한 효과크기 기준 권장 N={req_n}, 보유 N={available_n} "
                "→ 작은 효과는 놓칠 수 있으니 표본 확대 또는 효과크기 재검토."
            )
        if exploratory:
            notes.append(
                "탐색적 설계(군집 등)라 단순 표본수 공식이 적용되지 않음 — "
                "실루엣/안정성 등 탐색 지표로 평가하고 예비/파일럿으로 접근."
            )
        elif available_n is None:
            notes.append("매니페스트에 n이 없어 검정력 판단 불가 — n을 채우면 자동 평가됨.")
        # Template-authored honesty caveats (assumptions the sizing makes).
        notes.extend(t.get("caveats", []))

        results.append(
            IdeaResult(
                idea_id=t["id"],
                title=t["title"],
                modalities=used,
                hypothesis=t["hypothesis"],
                predictors=t["predictors"],
                outcomes=t["outcomes"],
                analysis=t["analysis"],
                design=t["design"],
                journal=t["journal"],
                novelty=t["novelty"],
                required_n=req_n,
                available_n=available_n,
                feasible=feasible,
                matched_variables=matched,
                score=round(score, 3),
                notes=notes,
                exploratory=exploratory,
                detectable=detectable,
                recruit_n=recruit_n,
                n_sensitivity=n_sensitivity,
            )
        )

    # Feasibility is the PRIMARY sort key so the triage never puts an
    # underpowered idea above a feasible one: feasible (2) > unknown-N (1) >
    # underpowered (0). Score (multimodal/variable richness) orders within a tier.
    feas_rank = {True: 2, None: 1, False: 0}
    results.sort(
        key=lambda r: (feas_rank[r.feasible], r.score, len(r.modalities)),
        reverse=True,
    )
    return results


def modality_label(key: str) -> str:
    return MODALITY_LABEL_KO.get(key, key)
