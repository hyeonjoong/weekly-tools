"""Render evaluated ideas as a Markdown report and/or CSV matrix."""
from __future__ import annotations

import csv
import io

from .engine import IdeaResult, modality_label
from .manifest import Manifest


def _mods(result: IdeaResult) -> str:
    return " × ".join(modality_label(m) for m in result.modalities)


def _req_n(result: IdeaResult) -> str:
    """Recommended-N cell: a number, or '비적용' for exploratory designs."""
    return "비적용" if result.required_n is None else str(result.required_n)


def render_markdown(manifest: Manifest, results: list, alpha: float, power: float) -> str:
    lines: list = []
    lines.append(f"# 논문 아이디어 매트릭스 — {manifest.study}")
    lines.append("")
    avail = ", ".join(sorted(modality_label(m) for m in manifest.modalities())) or "(없음)"
    lines.append(f"- 보유 모달리티: {avail}")
    lines.append(f"- 검정력 기준: alpha={alpha}, power={power} (계획용 근사)")
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
    lines.append("| # | 아이디어 | 모달리티 | 권장 N | 보유 N | 실현가능성 | 적합 저널 |")
    lines.append("|---|----------|----------|-------|-------|-----------|-----------|")
    for i, r in enumerate(results, 1):
        an = r.available_n if r.available_n is not None else "?"
        lines.append(
            f"| {i} | {r.title} | {_mods(r)} | {_req_n(r)} | {an} | "
            f"{r.feasibility_label} | {r.journal} |"
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
        lines.append(
            f"- **실현가능성**: {r.feasibility_label} "
            f"(권장 N={_req_n(r)}, 보유 N="
            f"{r.available_n if r.available_n is not None else '미상'})"
        )
        if r.matched_variables:
            lines.append(f"- **활용 가능한 보유 변수**: {', '.join(r.matched_variables)}")
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
            "predictors", "outcomes", "analysis", "required_n", "available_n",
            "feasibility", "journal", "novelty", "score",
        ]
    )
    for i, r in enumerate(results, 1):
        writer.writerow(
            [
                i, r.idea_id, r.title, "|".join(r.modalities), r.design,
                r.hypothesis, "|".join(r.predictors), "|".join(r.outcomes),
                r.analysis, r.required_n if r.required_n is not None else "",
                r.available_n if r.available_n is not None else "",
                r.feasibility_label, r.journal, r.novelty, r.score,
            ]
        )
    return buf.getvalue()
