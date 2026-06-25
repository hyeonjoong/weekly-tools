"""Match idea templates to a manifest, assess feasibility, and rank."""
from __future__ import annotations

from dataclasses import dataclass, field

from .knowledge import IDEA_TEMPLATES
from .manifest import MODALITY_LABEL_KO, Manifest
from .power import required_total_n


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

    @property
    def feasibility_label(self) -> str:
        if self.exploratory:
            return "탐색적(표본 판정 비적용)"
        if self.feasible is None:
            return "표본수 미상"
        if self.feasible:
            return "충분 가능"
        return "표본 부족 우려"


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
) -> list:
    """Return ranked :class:`IdeaResult` list for ideas whose modalities are met."""
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

        req_n = required_total_n(t["effect"], alpha=alpha, power=power)
        exploratory = req_n is None
        if exploratory or available_n is None:
            feasible = None
        else:
            feasible = available_n >= req_n

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
