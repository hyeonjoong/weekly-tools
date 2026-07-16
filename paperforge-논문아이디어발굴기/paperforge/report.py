"""Render evaluated ideas as a Markdown report and/or CSV matrix."""
from __future__ import annotations

import csv
import io
import json

from .engine import IdeaResult, modality_label
from .manifest import Manifest


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
) -> str:
    lines: list = []
    lines.append(f"# 논문 아이디어 매트릭스 — {manifest.study}")
    lines.append("")
    avail = ", ".join(sorted(modality_label(m) for m in manifest.modalities())) or "(없음)"
    lines.append(f"- 보유 모달리티: {avail}")
    lines.append(f"- 검정력 기준: alpha={alpha}, power={power} (계획용 근사)")
    if dropout > 0.0:
        lines.append(
            f"- 중도탈락 가정: {dropout:.0%} → 권장 모집 N = ⌈권장 N / (1−{dropout:.2f})⌉"
        )
    lines.append(f"- 생성된 아이디어: {len(results)}개 (점수순)")
    if manifest.warnings:
        lines.append("")
        lines.append("> ⚠️ 매니페스트 경고:")
        for w in manifest.warnings:
            lines.append(f"> - {w}")
    lines.append("")

    if not results:
        lines.append("매칭되는 아이디어가 없습니다. 모달리티/변수를 더 채워 보세요.")
        return "\n".join(lines)

    # Summary table.
    lines.append("## 요약 매트릭스")
    lines.append("")
    lines.append(
        "| # | 아이디어 | 모달리티 | 권장 N | 보유 N | 탐지가능 효과 | 실현가능성 | 적합 저널 |"
    )
    lines.append(
        "|---|----------|----------|-------|-------|-------------|-----------|-----------|"
    )
    for i, r in enumerate(results, 1):
        an = r.available_n if r.available_n is not None else "?"
        lines.append(
            f"| {i} | {r.title} | {_mods(r)} | {_req_n(r)} | {an} | "
            f"{r.detectable_label} | {r.feasibility_label} | {r.journal} |"
        )
    lines.append("")
    lines.append(
        "> '탐지가능 효과' = 보유 N으로 alpha/power 기준 검출 가능한 최소 효과크기"
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
            lines.append(
                f"- **이 조합에서 접근 가능한 열(전체 목록, 가설과 자동매칭 아님)**: "
                f"{', '.join(r.matched_variables)}"
            )
        lines.append(f"- **적합 저널 유형**: {r.journal}")
        lines.append(f"- **신규성/중복성 메모**: {r.novelty}")
        for note in r.notes:
            lines.append(f"  - 참고: {note}")
    lines.append("")
    lines.append("---")
    lines.append(
        "_권장 N은 Fisher-z(상관)·정규근사(평균차/회귀)에 기반한 계획용 추정치이며, "
        "최종 검정력은 G*Power 등으로 확정하세요._"
    )
    return "\n".join(lines)


def render_csv(results: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "rank", "idea_id", "title", "modalities", "design", "hypothesis",
            "predictors", "outcomes", "analysis", "required_n", "recruit_n",
            "available_n", "detectable_metric", "detectable_value",
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
                r.recruit_n if r.recruit_n is not None else "",
                r.available_n if r.available_n is not None else "",
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
) -> str:
    """Machine-readable dump of the full run (stable schema, UTF-8, indent=2).

    Mirrors the report but keeps every field structured so downstream tooling
    doesn't have to scrape Markdown/CSV.
    """
    payload = {
        "study": manifest.study,
        "parameters": {"alpha": alpha, "power": power, "dropout": dropout},
        "modalities_available": sorted(manifest.modalities()),
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
                "recruit_n": r.recruit_n,
                "available_n": r.available_n,
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
