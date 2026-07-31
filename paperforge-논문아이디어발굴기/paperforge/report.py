"""Render evaluated ideas as a Markdown report and/or CSV matrix."""
from __future__ import annotations

import csv
import io
import json

from .engine import IdeaResult, modality_label
from .manifest import Manifest


# Report-readability caps. The full, uncapped data is always in --csv/--json.
_MAX_WARNINGS = 20
_MAX_VARS = 60


def _mods(result: IdeaResult) -> str:
    return " × ".join(modality_label(m) for m in result.modalities)


def _req_n(result: IdeaResult) -> str:
    """Recommended-N cell: a number, or '비적용' for exploratory designs."""
    return "비적용" if result.required_n is None else str(result.required_n)


def render_markdown(
    manifest: Manifest,
    results: list,
    alpha: float,
    power: float,
    dropout: float = 0.0,
    settings: dict = None,
) -> str:
    settings = settings or {}
    sided = settings.get("sided", 2)
    n_tests = settings.get("n_tests", 1)
    repeats = settings.get("repeats", 1)
    icc = settings.get("icc", 0.0)

    lines: list = []
    lines.append(f"# 논문 아이디어 매트릭스 — {manifest.study}")
    lines.append("")
    avail = ", ".join(sorted(modality_label(m) for m in manifest.modalities())) or "(없음)"
    lines.append(f"- 보유 모달리티: {avail}")
    tail = " · 단측검정" if sided == 1 else ""
    lines.append(f"- 검정력 기준: alpha={alpha}, power={power}{tail} (계획용 근사)")
    if n_tests > 1:
        lines.append(
            f"- 다중비교 보정: 아이디어당 주요 비교 {n_tests}회 → "
            f"적용 alpha = {alpha}/{n_tests} = {alpha / n_tests:.5g} (Bonferroni)"
        )
    if repeats > 1:
        lines.append(
            f"- 반복측정: 피험자당 {repeats}회, ICC={icc:g} → "
            f"설계효과 {1 + (repeats - 1) * icc:.2f} "
            "(관측 단위로 사이징되는 템플릿에만 적용 — 피험자당 값이 하나인 "
            "심리측정·기기 일치도 등은 제외). ICC는 피험자 간(between-subject) "
            "설계효과 기준이라 피험자 내에서 변하는 예측변수에는 보수적입니다."
        )
    if manifest.linked_n:
        pairs = ", ".join(
            "+".join(sorted(modality_label(m) for m in combo)) + f"={n}"
            for combo, n in sorted(
                manifest.linked_n.items(), key=lambda kv: sorted(kv[0])
            )
        )
        lines.append(f"- 선언된 연결 표본수(linked N): {pairs}")
    if dropout > 0.0:
        lines.append(
            f"- 중도탈락 가정: {dropout:.0%} → 권장 모집 N = ⌈권장 N / (1−{dropout:.2f})⌉"
        )
    lines.append(f"- 생성된 아이디어: {len(results)}개 (점수순)")
    if manifest.warnings:
        lines.append("")
        lines.append("> ⚠️ 매니페스트 경고:")
        # A 50k-row inventory with one bad modality emitted 7,148 identical
        # lines; collapse repeats and cap the list so the report stays readable.
        counts: dict = {}
        for w in manifest.warnings:
            counts[w] = counts.get(w, 0) + 1
        for i, (w, count) in enumerate(counts.items()):
            if i >= _MAX_WARNINGS:
                lines.append(f"> - …외 {len(counts) - _MAX_WARNINGS}종의 경고 생략")
                break
            suffix = f" (동일 경고 {count}건)" if count > 1 else ""
            lines.append(f"> - {w}{suffix}")
    lines.append("")

    if not results:
        lines.append("매칭되는 아이디어가 없습니다. 모달리티/변수를 더 채워 보세요.")
        return "\n".join(lines)

    # Summary table.
    lines.append("## 요약 매트릭스")
    lines.append("")
    lines.append(
        "| # | 아이디어 | 모달리티 | 권장 N | 보유 N | 현재 검정력 | 탐지가능 효과 | "
        "실현가능성 | 적합 저널 |"
    )
    lines.append(
        "|---|----------|----------|-------|-------|-----------|-------------|"
        "-----------|-----------|"
    )
    for i, r in enumerate(results, 1):
        an = r.available_n if r.available_n is not None else "?"
        lines.append(
            f"| {i} | {r.title} | {_mods(r)} | {_req_n(r)} | {an} | "
            f"{r.power_label} | {r.detectable_label} | {r.feasibility_label} | "
            f"{r.journal} |"
        )
    lines.append("")
    lines.append(
        "> '현재 검정력' = 보유 N과 템플릿의 가정 효과크기에서 실제로 얻는 검정력"
        "(예: 0.62 = 참 효과가 있어도 38% 확률로 놓침). 상관·평균차는 정규근사라 "
        "N이 작을수록(N≲30) 정확한 t 기반 값보다 최대 ~3%p 높게 나올 수 있습니다. "
        "'탐지가능 효과' = 보유 N으로 alpha/power 기준 검출 가능한 최소 효과크기"
        "(민감도 분석). 예: `r≥0.29`는 상관 0.29 이상이면 검출 가능."
    )
    lines.append("")

    # Detail blocks.
    lines.append("## 상세")
    for i, r in enumerate(results, 1):
        lines.append("")
        lines.append(f"### {i}. {r.title}")
        lines.append(f"- **모달리티 결합**: {_mods(r)}  (설계: {r.design})")
        lines.append(f"- **가설**: {r.hypothesis}")
        lines.append(f"- **예측/독립변수**: {', '.join(r.predictors)}")
        lines.append(f"- **결과/종속변수**: {', '.join(r.outcomes)}")
        lines.append(f"- **권장 분석**: {r.analysis}")
        feas = (
            f"- **실현가능성**: {r.feasibility_label} "
            f"(권장 N={_req_n(r)}, 보유 N="
            f"{r.available_n if r.available_n is not None else '미상'})"
        )
        if r.recruit_n is not None:
            feas += f" · 권장 모집 N(탈락 보정)={r.recruit_n}"
        lines.append(feas)
        if r.attained_power is not None:
            lines.append(
                f"- **현재 표본의 검정력(가정 효과크기 기준)**: {r.power_label} "
                f"— 목표 {power:.2f} 대비."
            )
        if r.required_rows is not None and r.required_rows != r.required_n:
            lines.append(
                f"- **반복측정 환산**: 필요한 분석 행 {r.required_rows}개 → "
                f"피험자 {r.required_n}명 (보유 피험자 "
                f"{r.available_n if r.available_n is not None else '미상'}명 ≈ "
                f"분석 행 {r.analysis_n if r.analysis_n is not None else '미상'}개)"
            )
        if r.detectable is not None:
            lines.append(
                f"- **탐지가능 최소효과(보유 N 기준)**: {r.detectable_label} "
                "— 이보다 작은 실제 효과는 검출되지 않을 수 있음."
            )
        if r.n_sensitivity:
            strip = " / ".join(
                f"{s['label']} N={s['required_n']}(효과 {s['effect_value']})"
                for s in r.n_sensitivity
            )
            lines.append(f"- **표본수 민감도(효과크기 가정별 권장 N)**: {strip}")
        if r.matched_variables:
            shown = r.matched_variables[:_MAX_VARS]
            more = len(r.matched_variables) - len(shown)
            tail = f" … 외 {more}개" if more > 0 else ""
            lines.append(
                f"- **이 조합에서 접근 가능한 열(전체 목록, 가설과 자동매칭 아님)**: "
                f"{', '.join(shown)}{tail}"
            )
        lines.append(f"- **적합 저널 유형**: {r.journal}")
        lines.append(f"- **신규성/중복성 메모**: {r.novelty}")
        for note in r.notes:
            lines.append(f"  - 참고: {note}")
    lines.append("")
    lines.append("---")
    lines.append(
        "_권장 N은 Fisher-z(상관)·정규근사(평균차)·비중심 F(회귀/ΔR²)에 기반한 계획용 "
        "추정치이며, 효과크기는 각 템플릿에 내장된 가정입니다. "
        "최종 검정력은 G*Power 등으로 확정하세요._"
    )
    return "\n".join(lines)


def render_csv(results: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "rank", "idea_id", "title", "modalities", "design", "hypothesis",
            "predictors", "outcomes", "analysis", "required_n", "required_rows",
            "recruit_n", "available_n", "analysis_n", "attained_power",
            "detectable_metric", "detectable_value",
            "n_sensitivity", "feasibility", "journal", "novelty", "score",
        ]
    )
    for i, r in enumerate(results, 1):
        det_metric = r.detectable["metric"] if r.detectable else ""
        det_value = round(r.detectable["value"], 4) if r.detectable else ""
        sens = "|".join(
            f"{s['label']}:{s['required_n']}@{s['effect_value']}"
            for s in r.n_sensitivity
        )
        writer.writerow(
            [
                i, r.idea_id, r.title, "|".join(r.modalities), r.design,
                r.hypothesis, "|".join(r.predictors), "|".join(r.outcomes),
                r.analysis, r.required_n if r.required_n is not None else "",
                r.required_rows if r.required_rows is not None else "",
                r.recruit_n if r.recruit_n is not None else "",
                r.available_n if r.available_n is not None else "",
                r.analysis_n if r.analysis_n is not None else "",
                round(r.attained_power, 4) if r.attained_power is not None else "",
                det_metric, det_value, sens,
                r.feasibility_label, r.journal, r.novelty, r.score,
            ]
        )
    return buf.getvalue()


def render_json(
    manifest: Manifest,
    results: list,
    alpha: float,
    power: float,
    dropout: float = 0.0,
    settings: dict = None,
) -> str:
    """Machine-readable dump of the full run (stable schema, UTF-8, indent=2).

    Mirrors the report but keeps every field structured so downstream tooling
    doesn't have to scrape Markdown/CSV.
    """
    params = {"alpha": alpha, "power": power, "dropout": dropout}
    params.update(settings or {})
    params["alpha_effective"] = alpha / params.get("n_tests", 1)
    payload = {
        "study": manifest.study,
        "parameters": params,
        "modalities_available": sorted(manifest.modalities()),
        "linked_n": {
            "+".join(sorted(combo)): n
            for combo, n in sorted(
                manifest.linked_n.items(), key=lambda kv: sorted(kv[0])
            )
        },
        "warnings": list(manifest.warnings),
        "ideas": [
            {
                "rank": i,
                "idea_id": r.idea_id,
                "title": r.title,
                "modalities": list(r.modalities),
                "design": r.design,
                "hypothesis": r.hypothesis,
                "predictors": list(r.predictors),
                "outcomes": list(r.outcomes),
                "analysis": r.analysis,
                "required_n": r.required_n,
                "required_rows": r.required_rows,
                "recruit_n": r.recruit_n,
                "available_n": r.available_n,
                "analysis_n": r.analysis_n,
                "attained_power": (
                    round(r.attained_power, 6)
                    if r.attained_power is not None else None
                ),
                "planned_effect": r.planned_effect,
                "linked_declared": r.linked_declared,
                "detectable_effect": r.detectable,
                "n_sensitivity": r.n_sensitivity,
                "feasible": r.feasible,
                "feasibility_label": r.feasibility_label,
                "exploratory": r.exploratory,
                "matched_variables": list(r.matched_variables),
                "journal": r.journal,
                "novelty": r.novelty,
                "score": r.score,
                "notes": list(r.notes),
            }
            for i, r in enumerate(results, 1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
