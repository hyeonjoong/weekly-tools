"""분석 결과 → 사람이 읽는 Markdown 리포트 + 기계용 dict(JSON) 렌더링."""

from __future__ import annotations

from typing import Dict, List, Sequence

from . import analyze
from .records import Article


def _bar(count: int, max_count: int, width: int = 28) -> str:
    if max_count <= 0:
        return ""
    filled = round(width * count / max_count)
    return "█" * filled + "·" * (width - filled)


def build_report(
    articles: Sequence[Article],
    query: str,
    *,
    top_mesh_n: int = 15,
    top_journals_n: int = 8,
    gap_top_k: int = 12,
    gap_min_expected: float = 2.0,
    gap_max_lift: float = 0.5,
) -> Dict:
    """모든 분석을 한 번에 수행해 dict 로 반환(리포트/JSON 공용)."""
    counts = analyze.yearly_counts(articles)
    span = analyze.year_span(articles)
    split = analyze.split_point(articles)
    trends = analyze.term_trends(articles)
    gaps = analyze.gap_pairs(
        articles, top_k=gap_top_k, min_expected=gap_min_expected, max_lift=gap_max_lift
    )
    n_with_mesh = sum(1 for a in articles if a.mesh)

    # 초기/최근 중 한쪽이 비면(예: 전부 같은 연도) 비중 변화가 무의미하므로 생략.
    dated = [a for a in articles if a.year is not None]
    n_early = sum(1 for a in dated if split is not None and a.year < split)
    n_recent = sum(1 for a in dated if split is not None and a.year >= split)
    trend_ok = n_early > 0 and n_recent > 0

    return {
        "query": query,
        "n_articles": len(articles),
        "n_with_mesh": n_with_mesh,
        "year_span": list(span) if span else None,
        "split_year": split,
        "trend_reliable": trend_ok,
        "yearly_counts": counts,
        "growth": analyze.growth_summary(counts, split=split),
        "top_journals": analyze.top_journals(articles, top_journals_n),
        "top_mesh": analyze.top_mesh(articles, top_mesh_n),
        "emerging": [t.__dict__ for t in analyze.emerging(trends)] if trend_ok else [],
        "declining": [t.__dict__ for t in analyze.declining(trends)] if trend_ok else [],
        "gaps": [g.__dict__ for g in gaps],
    }


def render_markdown(rep: Dict) -> str:
    L: List[str] = []
    q = rep["query"]
    L.append(f"# 연구 동향·공백 리포트 — `{q}`")
    L.append("")
    span = rep["year_span"]
    span_txt = f"{span[0]}–{span[1]}" if span else "연도 미상"
    L.append(
        f"- 분석 논문: **{rep['n_articles']}편** "
        f"(MeSH 주제어 보유 {rep['n_with_mesh']}편) · 발행연도 {span_txt}"
    )
    g = rep["growth"]
    if g.get("total"):
        ratio = g["ratio"]
        ratio_txt = "∞" if ratio == float("inf") else f"{ratio:.2f}배"
        split_yr = g.get("split")
        window = f"{split_yr}년 이후" if split_yr else "최근 구간"
        L.append(
            f"- 발행량: **{window}**가 전체의 **{g['recent_share']*100:.0f}%** "
            f"(그 이전 대비 {ratio_txt})"
        )
    L.append("")

    # 연도별 발행량
    counts = rep["yearly_counts"]
    if counts:
        L.append("## 연도별 발행량")
        L.append("")
        mx = max(counts.values())
        for y in sorted(counts):
            L.append(f"`{y}` {counts[y]:>3}  {_bar(counts[y], mx)}")
        L.append("")

    # 주요 저널
    if rep["top_journals"]:
        L.append("## 주요 저널 (게재 편수)")
        L.append("")
        for j, c in rep["top_journals"]:
            L.append(f"- {j} — {c}")
        L.append("")

    # 주요 주제
    if rep["top_mesh"]:
        L.append("## 주요 주제 (MeSH, 논문 수)")
        L.append("")
        for t, c in rep["top_mesh"]:
            L.append(f"- {t} — {c}")
        L.append("")

    # 부상 주제
    if rep["emerging"]:
        L.append("## ↗︎ 최근 부상 주제 (비중 상승)")
        L.append("")
        L.append("| 주제 | 초기 | 최근 | 비중변화 |")
        L.append("|---|---:|---:|---:|")
        for t in rep["emerging"]:
            L.append(
                f"| {t['term']} | {t['early_count']} | {t['recent_count']} "
                f"| +{t['delta']*100:.0f}%p |"
            )
        L.append("")

    # 쇠퇴 주제
    if rep["declining"]:
        L.append("## ↘︎ 관심 감소 주제 (비중 하락)")
        L.append("")
        L.append("| 주제 | 초기 | 최근 | 비중변화 |")
        L.append("|---|---:|---:|---:|")
        for t in rep["declining"]:
            L.append(
                f"| {t['term']} | {t['early_count']} | {t['recent_count']} "
                f"| {t['delta']*100:.0f}%p |"
            )
        L.append("")

    # 연구공백
    L.append("## 🔍 덜 연구된 각도 (저조 조합 = 연구공백 후보)")
    L.append("")
    if rep["gaps"]:
        L.append(
            "각각 개별적으로는 자주 다뤄지지만 **함께는 기대보다 훨씬 드물게** "
            "연구된 주제쌍입니다. lift(관측/기대)가 낮을수록 미개척 조합입니다."
        )
        L.append("")
        L.append("| 주제 A | 주제 B | 함께(관측) | 기대 | lift | p |")
        L.append("|---|---|---:|---:|---:|---:|")
        for gp in rep["gaps"]:
            L.append(
                f"| {gp['term_a']} | {gp['term_b']} | {gp['observed']} "
                f"| {gp['expected']:.1f} | {gp['lift']:.2f} | {gp['p_value']:.3f} |"
            )
        L.append("")
        L.append(
            "_정렬은 lift(미개척 정도) 오름차순입니다. p = 초기하검정 하단꼬리"
            "(우연히 이만큼 덜 엮일 확률)로, 작을수록 통계적으로 유의한 공백입니다._"
        )
        L.append("")
        top = rep["gaps"][0]
        L.append(
            f"> 제안: **{top['term_a']} × {top['term_b']}** 를 결합한 분석/논문을 검토하세요. "
            f"관련 논문 각각 {top['count_a']}·{top['count_b']}편이 있으나 둘을 함께 다룬 논문은 "
            f"{top['observed']}편뿐입니다(기대 {top['expected']:.1f}편, p={top['p_value']:.3f})."
        )
    else:
        L.append(
            "_설정한 임계값에서 뚜렷한 저조 조합을 찾지 못했습니다. 좁은 주제라면_ "
            "`--gap-min-expected` _를 낮추거나(예: 1.0)_ `--gap-max-lift` _를 높여 보세요._"
        )
    L.append("")

    L.append("---")
    L.append(
        "_주의: 이 리포트는 MeSH 주제어 공동출현 기반 휴리스틱입니다. "
        "'공백'은 문헌 부재의 신호일 뿐 인과/타당성을 보장하지 않으며, "
        "실제 착수 전 대표 논문을 직접 확인하세요._"
    )
    return "\n".join(L)
