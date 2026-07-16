"""분석 결과 → 사람이 읽는 Markdown 리포트 + 기계용 dict(JSON) + CSV 렌더링."""

from __future__ import annotations

import csv
import io
import math
from typing import Dict, List, Optional, Sequence

from . import analyze
from .records import Article


def json_safe(obj):
    """JSON 표준에 없는 값(Infinity/NaN)을 None 으로 바꾼 사본을 만든다.

    `growth.ratio` 는 초기 구간이 0편이면 inf 가 된다. json.dumps 는 기본적으로
    이를 `Infinity` 로 출력하는데, 이는 표준 JSON 이 아니어서 Node/브라우저의
    JSON.parse 등이 거부한다. 기계 소비용 JSON 이 항상 유효하도록 경계에서 정화한다.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


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
    gap_max_q: Optional[float] = None,
    drop_check_tags: bool = True,
    bridge_top_n: int = 3,
) -> Dict:
    """모든 분석을 한 번에 수행해 dict 로 반환(리포트/JSON/CSV 공용).

    drop_check_tags: PubMed 체크 태그(Humans/Male/Female/Adult…)를 주제 분석에서 제외.
    bridge_top_n: 각 공백쌍에 대해 제안할 Swanson ABC 가교 주제 수(0 이면 생략).
    """
    if drop_check_tags:
        articles = analyze.strip_check_tags(articles)
    counts = analyze.yearly_counts(articles)
    span = analyze.year_span(articles)
    split = analyze.split_point(articles)
    trends = analyze.term_trends(articles)
    gaps = analyze.gap_pairs(
        articles, top_k=gap_top_k, min_expected=gap_min_expected, max_lift=gap_max_lift,
        bridge_top_n=bridge_top_n,
    )
    if gap_max_q is not None:
        gaps = [g for g in gaps if g.q_value <= gap_max_q]
    n_with_mesh = sum(1 for a in articles if a.mesh)

    # 초기/최근 중 한쪽이 비면(예: 전부 같은 연도) 비중 변화가 무의미하므로 생략.
    dated = [a for a in articles if a.year is not None]
    n_early = sum(1 for a in dated if split is not None and a.year < split)
    n_recent = sum(1 for a in dated if split is not None and a.year >= split)
    trend_ok = n_early > 0 and n_recent > 0

    mk = analyze.trend_test(counts)

    # 부상/쇠퇴는 표시할 소수 행에만 Fisher 정확검정 p 를 채운다(전량 계산 회피).
    em = analyze.emerging(trends) if trend_ok else []
    dec = analyze.declining(trends) if trend_ok else []
    for t in list(em) + list(dec):
        t.p_value = analyze.fisher_exact_two_sided(
            t.recent_count, n_recent - t.recent_count,
            t.early_count, n_early - t.early_count,
        )

    return {
        "query": query,
        "n_articles": len(articles),
        "n_with_mesh": n_with_mesh,
        "year_span": list(span) if span else None,
        "split_year": split,
        "trend_reliable": trend_ok,
        "yearly_counts": counts,
        "growth": analyze.growth_summary(counts, split=split),
        "mann_kendall": mk.__dict__,
        "top_journals": analyze.top_journals(articles, top_journals_n),
        "top_mesh": analyze.top_mesh(articles, top_mesh_n),
        "emerging": [t.__dict__ for t in em],
        "declining": [t.__dict__ for t in dec],
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
        line = (
            f"- 발행량: **{window}**가 전체의 **{g['recent_share']*100:.0f}%** "
            f"(그 이전 대비 {ratio_txt})"
        )
        cagr = g.get("cagr")
        # 연도가 3개 미만이면(추세 검정도 'insufficient') CAGR 은 양끝 두 점의 잡음이라
        # 오해를 부르므로 표시하지 않는다.
        mk_n = (rep.get("mann_kendall") or {}).get("n", 0)
        if cagr is not None and mk_n >= 3:
            line += f" · 연평균 {cagr*100:+.0f}%"
        L.append(line)

    mk = rep.get("mann_kendall")
    if mk and mk.get("direction") not in (None, "insufficient"):
        label = {"increasing": "유의한 증가 추세 ↗︎", "decreasing": "유의한 감소 추세 ↘︎",
                 "flat": "뚜렷한 추세 없음"}.get(mk["direction"], mk["direction"])
        L.append(
            f"- 추세 검정(Mann–Kendall): **{label}** "
            f"(τ={mk['tau']:+.2f}, p={mk['p_value']:.3f}, n={mk['n']}년)"
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
        L.append("| 주제 | 초기 | 최근 | 비중변화 | p |")
        L.append("|---|---:|---:|---:|---:|")
        for t in rep["emerging"]:
            p = t.get("p_value")
            ptxt = f"{p:.3f}" if p is not None else "–"
            L.append(
                f"| {t['term']} | {t['early_count']} | {t['recent_count']} "
                f"| +{t['delta']*100:.0f}%p | {ptxt} |"
            )
        L.append("")
        L.append("_p = Fisher 정확검정(최근 vs 초기 등장) 양측값. 편수가 적으면(예: 0→2편) "
                 "비중 변화가 커도 p 는 유의하지 않을 수 있으니 초기/최근 실제 편수를 함께 보세요._")
        L.append("")

    # 쇠퇴 주제
    if rep["declining"]:
        L.append("## ↘︎ 관심 감소 주제 (비중 하락)")
        L.append("")
        L.append("| 주제 | 초기 | 최근 | 비중변화 | p |")
        L.append("|---|---:|---:|---:|---:|")
        for t in rep["declining"]:
            p = t.get("p_value")
            ptxt = f"{p:.3f}" if p is not None else "–"
            L.append(
                f"| {t['term']} | {t['early_count']} | {t['recent_count']} "
                f"| {t['delta']*100:.0f}%p | {ptxt} |"
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
        L.append("| 주제 A | 주제 B | 함께(관측) | 기대 | lift | p | q(FDR) |")
        L.append("|---|---|---:|---:|---:|---:|---:|")
        for gp in rep["gaps"]:
            L.append(
                f"| {gp['term_a']} | {gp['term_b']} | {gp['observed']} "
                f"| {gp['expected']:.1f} | {gp['lift']:.2f} | {gp['p_value']:.3f} "
                f"| {gp.get('q_value', 1.0):.3f} |"
            )
        L.append("")
        L.append(
            "_정렬은 lift(미개척 정도) 오름차순입니다. p = 초기하검정 하단꼬리"
            "(우연히 이만큼 덜 엮일 확률), q = 검정한 모든 쌍에 BH-FDR 를 적용한 보정값입니다._ "
            "_여러 쌍을 동시에 보므로 **q ≤ 0.05** 를 유의 기준으로 쓰는 것이 정직합니다._"
        )
        L.append("")
        top = rep["gaps"][0]
        L.append(
            f"> 제안: **{top['term_a']} × {top['term_b']}** 를 결합한 분석/논문을 검토하세요. "
            f"관련 논문 각각 {top['count_a']}·{top['count_b']}편이 있으나 둘을 함께 다룬 논문은 "
            f"{top['observed']}편뿐입니다(기대 {top['expected']:.1f}편, p={top['p_value']:.3f}, "
            f"q={top.get('q_value', 1.0):.3f})."
        )
        # Swanson ABC 가교 주제 — '왜 이 주제인가'의 기전 서사.
        bridges = top.get("bridges") or []
        if bridges:
            btxt = ", ".join(f"**{c}**(A&C {ac}·C&B {cb})" for c, ac, cb in bridges)
            L.append("")
            L.append(
                f"> 가교(Swanson ABC): {top['term_a']} 와 {top['term_b']} 를 잇는 제3 주제 → {btxt}. "
                f"두 주제가 각각 C 와는 자주 엮이므로, C 를 매개로 한 연결 가설을 세울 수 있습니다."
            )
        # 대표 PMID — Limitations 가 요구하는 '대표 논문 직접 확인'을 바로 할 수 있게.
        pa = top.get("pmids_a") or []
        pb = top.get("pmids_b") or []
        both = top.get("pmids_both") or []
        if pa or pb or both:
            L.append("")
            parts = []
            if pa:
                parts.append(f"{top['term_a']}: {', '.join(pa)}")
            if pb:
                parts.append(f"{top['term_b']}: {', '.join(pb)}")
            if both:
                parts.append(f"함께: {', '.join(both)}")
            L.append("> 대표 PMID(확인용) — " + " · ".join(parts))
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


_CSV_HEADER = [
    "term_a", "term_b", "observed", "expected", "lift",
    "count_a", "count_b", "p_value", "q_value",
    "pmids_a", "pmids_b", "pmids_both", "bridges",
]


def render_csv(rep: Dict) -> str:
    """공백 후보(gaps)를 CSV 로 렌더링(스프레드시트/파이프라인용).

    엑셀 한글 깨짐을 막기 위해 UTF-8 BOM 을 붙인다. 공백이 없으면 헤더만 출력.
    PMID 목록은 세미콜론, 가교는 'C(ac/cb)' 형태로 담는다(csv 모듈이 인용 처리).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    for gp in rep.get("gaps", []):
        bridges = "; ".join(f"{c}({ac}/{cb})" for c, ac, cb in (gp.get("bridges") or []))
        writer.writerow([
            gp["term_a"],
            gp["term_b"],
            gp["observed"],
            f"{gp['expected']:.4f}",
            f"{gp['lift']:.4f}",
            gp["count_a"],
            gp["count_b"],
            f"{gp['p_value']:.6f}",
            f"{gp.get('q_value', 1.0):.6f}",
            "; ".join(gp.get("pmids_a") or []),
            "; ".join(gp.get("pmids_b") or []),
            "; ".join(gp.get("pmids_both") or []),
            bridges,
        ])
    return "﻿" + buf.getvalue().rstrip("\n")
