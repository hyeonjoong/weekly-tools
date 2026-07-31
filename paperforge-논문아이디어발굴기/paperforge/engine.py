"""Match idea templates to a manifest, assess feasibility, and rank."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .knowledge import IDEA_TEMPLATES
from .manifest import MODALITY_LABEL_KO, Manifest
from .power import (
    attained_power,
    design_effect,
    detectable_effect,
    effect_magnitude,
    observation_level,
    required_total_n,
    rows_to_subjects,
    scale_effect,
    subjects_to_rows,
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
    attained_power: object = None  # power at the N in hand, or None
    required_rows: object = None  # analysis rows needed (= required_n unless clustered)
    analysis_n: object = None  # effective analysis rows the held N supplies
    planned_effect: object = None  # effect spec actually used for sizing
    linked_declared: bool = False  # available_n came from a declared overlap

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

    @property
    def power_label(self) -> str:
        """Attained-power cell, e.g. ``0.83`` / ``>0.99`` / ``—``."""
        if self.attained_power is None:
            return "—"
        if self.attained_power >= 0.995:
            return ">0.99"
        if self.attained_power < 0.005:
            return "<0.01"
        return f"{self.attained_power:.2f}"


def _is_observation_level(template, effect) -> bool:
    """Whether this template's N counts observations rather than subjects.

    ``analysis_unit`` on the template wins when present ("observation" /
    "subject"); otherwise fall back to the effect family. The override matters:
    correlation-family templates whose data are one row per subject (psychometric
    validation, device agreement) must not have their N divided by the number of
    repeated measurements.
    """
    unit = template.get("analysis_unit")
    if unit == "observation":
        return True
    if unit == "subject":
        return False
    return observation_level(effect)


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


def _available_for(required, index, linked_n):
    """Analysable subject count for a modality combination.

    Returns ``(n_or_None, declared, contradictory)``:

    * ``n`` — the smallest per-modality ``n`` among the required modalities,
      capped by any declared subject overlap (``linked_n``) whose modality set
      is a subset of this combination. A declared "only 28 subjects have EEG
      *and* respiration" caps the combination even when each modality reports 90.
    * ``declared`` — whether a linkage covering this combination was declared at
      all. This is deliberately *not* "was it the binding constraint": the report
      must not tell a user who did declare an overlap that they didn't.
    * ``contradictory`` — the declaration claims MORE subjects than the smallest
      contributing modality has, which cannot be true; the smaller value is kept
      and the report says so.

    Without a declared overlap the min-of-n is an *upper bound*: it assumes the
    smaller cohort is fully contained in the larger. That assumption is flagged
    in the report rather than hidden.
    """
    ns = [index[m]["n"] for m in required if index[m]["n"] is not None]
    all_known = len(ns) == len(required)
    base = min(ns) if all_known and ns else None

    req_set = set(required)
    declared = None
    for combo, value in linked_n.items():
        if combo <= req_set:
            declared = value if declared is None else min(declared, value)

    if declared is None:
        return base, False, False
    if base is None:
        return declared, True, False
    return min(base, declared), True, declared > base


def _optional_shortfalls(used, required, index, linked_n, available_n):
    """Optional modalities whose own N falls short of the combination's N.

    An idea is *offered* on the strength of its required modalities, so the
    headline N is computed from those alone. But a template's hypothesis often
    names an optional modality's variables (e.g. "…predicts the questionnaire
    score"), and if only 5 subjects filled in that questionnaire, any analysis
    touching it is capped at 5 — not the advertised 90. Returning those cases
    lets the report say so instead of quietly overstating feasibility.
    """
    if available_n is None:
        return []
    out = []
    for m in used:
        if m in required:
            continue
        cap = index[m]["n"]
        for combo, value in linked_n.items():
            if combo <= set(used) and m in combo:
                cap = value if cap is None else min(cap, value)
        if cap is not None and cap < available_n:
            out.append((m, cap))
    return sorted(out, key=lambda pair: pair[1])


def evaluate(
    manifest: Manifest,
    alpha: float = 0.05,
    power: float = 0.80,
    templates=IDEA_TEMPLATES,
    dropout: float = 0.0,
    effect_scale: float = 1.0,
    sided: int = 2,
    n_tests: int = 1,
    repeats: int = 1,
    icc: float = 0.0,
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

    ``sided`` (1 or 2) selects a directional or two-tailed test for the z-based
    designs (correlation / mean differences); F-based ΔR² sizing is unaffected.

    ``n_tests`` (>=1) applies a Bonferroni correction: every sample-size, MDES
    and attained-power figure is computed at ``alpha / n_tests``, which is what
    you want when an idea's analysis plan carries several primary comparisons.

    ``repeats``/``icc`` describe repeated observations per subject (e.g. 3 nights
    with an intraclass correlation of 0.3). For designs sized in *observations*
    (correlation, regression) the subject requirement becomes
    ``ceil(N_rows * (1 + (m-1)*ICC) / m)`` and the held N is converted to
    effective rows the same way; subject-level designs (paired / two-group) are
    left untouched.
    """
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout must satisfy 0 <= dropout < 1")
    if effect_scale <= 0.0:
        raise ValueError("effect_scale must be > 0")
    if sided not in (1, 2):
        raise ValueError("sided must be 1 or 2")
    n_tests = int(n_tests)
    if n_tests < 1:
        raise ValueError("n_tests must be >= 1")
    try:
        alpha_eff = float(alpha) / n_tests
    except OverflowError:  # n_tests too large to convert to float
        alpha_eff = 0.0
    if not 0.0 < alpha_eff < 1.0:
        raise ValueError(
            f"alpha / n_tests must fall strictly between 0 and 1 "
            f"(alpha={alpha!r}, n_tests={n_tests})"
        )
    de = design_effect(repeats, icc)  # validates repeats/icc
    clustered = int(repeats) > 1
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

        # Limiting sample size: min n across required modalities, capped by any
        # declared subject overlap for this combination.
        available_n, linked_declared, linked_contradictory = _available_for(
            required, index, manifest.linked_n
        )
        shortfalls = _optional_shortfalls(
            used, required, index, manifest.linked_n, available_n
        )

        # Apply the global effect-size scale before sizing / feasibility.
        base_effect = t["effect"]
        planned_effect = scale_effect(base_effect, effect_scale)
        # A template may state its unit of analysis explicitly; otherwise it is
        # inferred from the effect family. The explicit form exists because
        # "correlation" alone does not imply observation-level data — a
        # psychometric validation or a device-agreement study yields exactly one
        # value per subject, so repeated measures must NOT shrink its N.
        obs_level = _is_observation_level(t, planned_effect)
        cluster_applies = clustered and obs_level

        def _subjects(rows):
            """Analysis rows -> subjects needed (identity unless clustered)."""
            if rows is None:
                return None
            return rows_to_subjects(rows, repeats, icc) if cluster_applies else rows

        req_rows = required_total_n(
            planned_effect, alpha=alpha_eff, power=power, sided=sided
        )
        req_n = _subjects(req_rows)
        exploratory = req_n is None

        # Effective rows of analysis the held subjects supply.
        analysis_n = None
        if available_n is not None:
            analysis_n = (
                subjects_to_rows(available_n, repeats, icc)
                if cluster_applies else available_n
            )

        if exploratory or available_n is None:
            feasible = None
        else:
            feasible = available_n >= req_n

        # Recruitment target inflated for expected attrition (planning aid only).
        recruit_n = None
        if req_n is not None and dropout > 0.0:
            recruit_n = math.ceil(req_n / (1.0 - dropout))

        # Sensitivity: smallest effect the *available* N could detect, and the
        # power actually attained against the planned effect at that N.
        detectable = detectable_effect(
            planned_effect, analysis_n, alpha=alpha_eff, power=power, sided=sided
        )
        power_now = attained_power(
            planned_effect, analysis_n, alpha=alpha_eff, sided=sided
        )

        # Required-N-vs-effect strip: how the target moves if the true effect is
        # smaller/larger than the planned prior. Skipped for exploratory designs.
        n_sensitivity = []
        if not exploratory:
            for label, factor in SENSITIVITY_FACTORS:
                eff = scale_effect(planned_effect, factor)
                rows = required_total_n(
                    eff, alpha=alpha_eff, power=power, sided=sided
                )
                n_sensitivity.append({
                    "label": label,
                    "factor": factor,
                    "effect_value": round(effect_magnitude(eff), 4),
                    "required_n": _subjects(rows),
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
        for mod, cap in shortfalls:
            notes.append(
                f"선택 모달리티 '{modality_label(mod)}'의 표본은 {cap}명뿐입니다"
                f"(이 아이디어의 보유 N={available_n}). 가설이 그 변수를 쓰면 "
                f"실제 분석 N은 {cap}으로 줄어듭니다 — 보유 N은 필수 모달리티만으로 "
                "계산합니다."
            )
        if linked_contradictory:
            notes.append(
                "선언된 연결 표본수가 개별 모달리티 n의 최소값보다 커서 "
                "(모순) 최소값을 사용했습니다 — 선언값을 확인하세요."
            )
        if len(required) > 1:
            if linked_declared:
                notes.append(
                    f"연결 표본수(linked N)가 매니페스트에 선언돼 있어 "
                    f"보유 N={available_n}을 사용했습니다."
                )
            else:
                notes.append(
                    "동일 피험자에서 모달리티가 연결(linked)돼 있어야 함 — "
                    "연결 표본수가 선언되지 않아 각 모달리티 n의 최소값을 "
                    "사용했습니다(실제 겹치는 인원은 더 적을 수 있음). "
                    "매니페스트에 linked_n을 넣으면 정확해집니다."
                )
        if cluster_applies:
            notes.append(
                f"반복측정 보정: 피험자당 {repeats}회 관측, ICC={icc:g} → "
                f"설계효과 {de:.2f}. 필요한 분석 행 {req_rows}개 = 피험자 {req_n}명."
            )
        elif clustered:
            notes.append(
                f"이 설계는 표본 단위가 피험자라서 반복측정({repeats}회) 보정을 "
                "적용하지 않았습니다(반복은 측정오차만 줄임)."
            )
        if n_tests > 1:
            notes.append(
                f"다중비교 보정: alpha {alpha:g}/{n_tests} = {alpha_eff:.5g} "
                "기준으로 표본수·검정력을 계산했습니다(Bonferroni)."
            )
        if sided == 1:
            etype = planned_effect.get("type")
            if etype in ("correlation", "two_group", "paired"):
                notes.append("단측검정(one-sided) 기준 — 방향 가설일 때만 사용하세요.")
            elif etype in ("regression", "regression_change"):
                notes.append(
                    "ΔR²/F 검정은 단측 개념이 없어 --one-sided 가 적용되지 않았습니다."
                )
        if feasible is False:
            msg = (
                f"가정한 효과크기 기준 권장 N={req_n}, 보유 N={available_n} "
                "→ 작은 효과는 놓칠 수 있으니 표본 확대 또는 효과크기 재검토."
            )
            if power_now is not None:
                msg += f" (현재 표본의 검정력 ≈ {power_now:.2f})"
            notes.append(msg)
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
                attained_power=power_now,
                required_rows=req_rows,
                analysis_n=analysis_n,
                planned_effect=dict(planned_effect),
                linked_declared=linked_declared,
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
