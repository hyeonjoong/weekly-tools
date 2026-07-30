"""분석 결과 → 사람이 읽는 Markdown 리포트 + 기계용 dict(JSON) + CSV 렌더링."""

from __future__ import annotations

import csv
import io
import math
import urllib.parse
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


_PUBMED_SEARCH = "https://pubmed.ncbi.nlm.nih.gov/?term="
# 주제어에 섞인 제어 문자/따옴표는 검색식 자체를 망가뜨린다(따옴표는 절을 조기 종료해
# 최상위 OR 를 만들어, 검증 결과가 정반대로 나온다).
_CONTROL_RE = __import__("re").compile(r"[\r\n\t\x0b\x0c]+")


def pubmed_pair_url(term_a: str, term_b: str, field: str = "MeSH Terms") -> str:
    """두 주제를 AND 로 묶은 PubMed 검색 URL.

    리포트가 "대표 논문을 직접 확인하라"고 말하면서 확인 수단을 안 주면 그 조언은
    공허하다. 이 URL 을 그대로 클릭하면 그 조합의 실제 문헌이 바로 뜬다.

    `field='MeSH Terms'` 는 색인 기준(= 이 도구가 센 것과 같은 기준),
    `field='Title/Abstract'` 는 **자유어** 기준이다. 둘의 결과 수가 크게 다르면
    (MeSH 0편인데 제목/초록 400편) 그 '공백'은 연구 공백이 아니라 **색인 artifact**
    다 — 착수 전에 반드시 걸러야 하는 가짜 후보를 가장 싸게 판별하는 방법이다.
    """
    a = _CONTROL_RE.sub(" ", str(term_a)).replace('"', "")
    b = _CONTROL_RE.sub(" ", str(term_b)).replace('"', "")
    term = f'"{a}"[{field}] AND "{b}"[{field}]'
    return _PUBMED_SEARCH + urllib.parse.quote_plus(term)


def _gap_dict(gap) -> Dict:
    """GapPair → 리포트용 dict(검증 URL 포함)."""
    d = dict(gap.__dict__)
    d["pubmed_url_mesh"] = pubmed_pair_url(gap.term_a, gap.term_b, "MeSH Terms")
    d["pubmed_url_text"] = pubmed_pair_url(gap.term_a, gap.term_b, "Title/Abstract")
    return d


def _md_cell(text: str) -> str:
    """Markdown 표 칸 안전화 — 파이프는 표 구조를 깨뜨리므로 이스케이프한다.

    실제 입력에서 온다: RIS 의 `KW  - Sleep | Wake` 는 키워드에 '|' 를 그대로 담고,
    그 주제가 공백표에 오르면 이후 모든 열이 한 칸씩 밀려 관측수 자리에 주제명이
    찍힌다(오류 없이 조용히 틀린 표).
    """
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _bar(count: int, max_count: int, width: int = 28) -> str:
    if max_count <= 0:
        return ""
    filled = round(width * count / max_count)
    return "█" * filled + "·" * (width - filled)


# 이 편수 미만이면 공백 통계(초기하검정·FDR)가 매우 불안정하다. 리포트에 경고를 띄운다.
SMALL_SAMPLE_N = 30


def build_report(
    articles: Sequence[Article],
    query: str,
    *,
    top_mesh_n: int = 15,
    top_journals_n: int = 5,
    gap_top_k: int = 12,
    gap_min_expected: float = 2.0,
    gap_max_lift: float = 0.5,
    gap_max_q: Optional[float] = None,
    gap_sort: str = "deficit",
    drop_check_tags: bool = True,
    exclude_terms: Sequence[str] = (),
    bridge_top_n: int = 3,
    evidence: bool = True,
    top_evidence_n: int = 12,
    meta: Optional[Dict] = None,
    topic_source: str = "mesh",
    total_available: Optional[int] = None,
    n_fetched: Optional[int] = None,
) -> Dict:
    """모든 분석을 한 번에 수행해 dict 로 반환(리포트/JSON/CSV 공용).

    drop_check_tags: PubMed 체크 태그(Humans/Male/Female/Adult…)를 주제 분석에서 제외.
    exclude_terms: 추가로 제외할 주제어(대소문자 무시) — 보통 검색어 자체.
    bridge_top_n: 각 공백쌍에 대해 제안할 Swanson ABC 가교 주제 수(0 이면 생략).
    evidence: PublicationType 기반 '근거 공백'(연구 설계 축) 분석 포함 여부.
    meta: 재현용 실행 정보(도구 버전·파라미터·입력 해시 등). 주면 리포트에 실린다.
    topic_source: 'mesh' | 'keywords' | 'mesh+keywords' — 주제가 어디서 왔는지.
        리포트가 "MeSH 기반"이라고 거짓말하지 않도록 렌더러가 이 값을 읽는다.
    total_available: PubMed 가 보고한 전체 검색결과 편수(esearch Count). 분석 편수보다
        크면 **표본이 잘렸다**는 뜻이므로 추세 관련 출력을 억제한다.
    """
    # 연구 설계 신호는 체크 태그를 떼기 **전** 의 MeSH 로 읽어야 한다
    # (NLM 은 코호트·후향 연구를 MeSH 로 색인하는데, 그 태그들이 곧 제거 대상이다).
    tiers = [analyze.article_tier(a) for a in articles] if evidence else None

    if drop_check_tags:
        articles = analyze.strip_check_tags(articles)
    if exclude_terms:
        articles = analyze.drop_terms(articles, exclude_terms)
    counts = analyze.yearly_counts(articles)
    span = analyze.year_span(articles)
    split = analyze.split_point(articles)
    trends = analyze.term_trends(articles)
    gaps = analyze.gap_pairs(
        articles, top_k=gap_top_k, min_expected=gap_min_expected, max_lift=gap_max_lift,
        bridge_top_n=bridge_top_n, sort=gap_sort,
    )
    if gap_max_q is not None:
        gaps = [g for g in gaps if g.q_value <= gap_max_q]
    n_with_mesh = sum(1 for a in articles if a.mesh)

    # 표본이 잘렸는지는 **PubMed 가 돌려준 편수(n_fetched)** 와 전체 편수를 비교해
    # 판정해야 한다. 분석 시점의 논문 수(len(articles))로 비교하면, 사용자가
    # `--min-year` 로 기간을 좁힌 **완전한** 코퍼스까지 '잘렸다'고 잘못 표시하고
    # (그리고 "기간을 좁히세요"라고, 방금 한 일을 다시 권한다) 추세를 지워 버린다.
    fetched = n_fetched if n_fetched is not None else len(articles)
    truncated = bool(total_available is not None and total_available > fetched)

    # 초기/최근 중 한쪽이 비면(예: 전부 같은 연도) 비중 변화가 무의미하므로 생략.
    dated = [a for a in articles if a.year is not None]
    n_early = sum(1 for a in dated if split is not None and a.year < split)
    n_recent = sum(1 for a in dated if split is not None and a.year >= split)
    trend_ok = n_early > 0 and n_recent > 0 and not truncated

    mk = analyze.trend_test(counts)

    # 부상/쇠퇴는 표시할 소수 행에만 Fisher 정확검정 p 를 채운다(전량 계산 회피).
    em = analyze.emerging(trends) if trend_ok else []
    dec = analyze.declining(trends) if trend_ok else []
    shown = list(em) + list(dec)
    for t in shown:
        t.p_value = analyze.fisher_exact_two_sided(
            t.recent_count, n_recent - t.recent_count,
            t.early_count, n_early - t.early_count,
        )
    # 표시하는 행들도 여러 개를 동시에 보는 것이므로 BH-FDR 를 붙인다.
    # (주의: 행은 delta 로 *고른 뒤* 검정하므로 선택편향이 남는다 — 리포트가 밝힌다.)
    for t, q in zip(shown, analyze.benjamini_hochberg([t.p_value for t in shown])):
        t.q_value = q

    rep: Dict = {
        "query": query,
        "n_articles": len(articles),
        "n_with_mesh": n_with_mesh,
        "topic_source": topic_source,
        "total_available": total_available,
        "truncated": truncated,
        "year_span": list(span) if span else None,
        "split_year": split,
        "trend_reliable": trend_ok,
        "small_sample": len(articles) < SMALL_SAMPLE_N,
        "gap_sort": gap_sort,
        "gap_terms": analyze.gap_candidate_terms(articles, gap_top_k),
        "gap_n_tested": analyze.count_gap_tests(articles, gap_top_k, gap_min_expected),
        "yearly_counts": counts,
        "growth": analyze.growth_summary(counts, split=split),
        "mann_kendall": mk.__dict__,
        "top_journals": analyze.top_journals(articles, top_journals_n),
        "top_mesh": analyze.top_mesh(articles, top_mesh_n),
        "emerging": [t.__dict__ for t in em],
        "declining": [t.__dict__ for t in dec],
        "gaps": [_gap_dict(g) for g in gaps],
    }
    if evidence:
        rep["evidence"] = analyze.evidence_profile(articles, tiers=tiers)
        rep["topic_evidence"] = [
            t.__dict__
            for t in analyze.topic_evidence(articles, top_k=top_evidence_n, tiers=tiers)
        ]
    if meta:
        rep["meta"] = dict(meta)
    return rep


_TREND_MARK = {
    "empty": "⬜ 완전공백",
    "closing": "↗ 메워짐",
    "widening": "↘ 벌어짐",
    "stable": "→ 유지",
    "unknown": "–",
}
# 전수 검증 결과 표시. VERIFY_ARTIFACT_LIFT 는 cli 에서 판정할 때 쓰는 임계와 같다.
VERIFY_ARTIFACT_LIFT = 0.5
_VERDICT_MARK = {
    "confirmed_empty": "⬜ 전수 0편",
    "confirmed": "✅ 진짜 공백",
    "artifact": "❌ 색인 artifact",
    "unknown": "–",
}
_TOPIC_SOURCE_LABEL = {
    "mesh": "MeSH 주제어",
    "keywords": "저자 키워드",
    "mesh+keywords": "MeSH + 저자 키워드",
}
_SORT_LABEL = {
    "lift": "lift 오름차순(미개척 정도)",
    "deficit": "부족 편수 내림차순(기대−관측)",
    "q": "q(FDR) 오름차순(통계적 견고성)",
    "expected": "기대 동시등장 내림차순(분야 규모)",
    "npmi": "nPMI 오름차순(배타성)",
}


def _render_evidence(rep: Dict) -> List[str]:
    """근거 지형(연구 설계 구성) + 근거 공백(개입연구가 비어 있는 주제) 섹션.

    임상·제약 연구자에게 "논문은 많은데 RCT·임상시험은 없는 주제"는 곧 *시험을 설계할
    자리*다. 주제쌍 공백(가로축)과 함께 이 세로축을 봐야 실제 연구 계획이 나온다.
    """
    ev = rep.get("evidence")
    if not ev:
        return []
    L: List[str] = ["## 🧪 근거 지형 (연구 설계 구성)", ""]
    if not ev.get("n_typed"):
        L.append(
            "_이 입력에는 연구 설계 정보(PublicationType)가 없어 근거 지형을 낼 수 없습니다. "
            "PubMed efetch XML 또는 NBIB 로 받으면 자동으로 채워집니다._"
        )
        L.append("")
        return L

    cov = ev["coverage"]
    L.append(
        f"- 연구 설계가 확인된 논문 **{ev['n_typed']}편** "
        f"(전체 {ev['n_articles']}편의 {cov*100:.0f}%) 기준입니다. "
        f"설계를 알 수 없는 {ev.get('n_unknown', 0)}편은 분모에서 제외했습니다 — "
        "'색인이 안 됨'과 '시험이 없음'을 섞지 않기 위해서입니다."
    )
    L.append("")
    L.append("| 근거 수준 | 편수 | 비중 |")
    L.append("|---|---:|---:|")
    for t in ev["tiers"]:
        if t["count"]:
            L.append(f"| {t['label']} | {t['count']} | {t['share']*100:.0f}% |")
    L.append("")
    L.append(
        f"- **개입연구(RCT·임상시험) {ev['n_interventional']}편 "
        f"= 설계 확인된 논문의 {ev['interventional_share']*100:.0f}%**"
    )
    if cov < 0.5:
        L.append(
            f"- ⚠️ 설계 정보 커버리지가 {cov*100:.0f}% 로 낮습니다 — 아래 비율은 "
            "색인된 일부만 반영하므로 과대·과소 해석에 주의하세요."
        )
    L.append("")

    te = rep.get("topic_evidence") or []
    # 헤딩이 '비어 있는 주제'라고 말하면 그 표에는 실제로 비어 있는 주제만 있어야 한다.
    # (예전에는 상위 K 주제를 전부 넣어, 개입연구가 *과밀한* 주제가 q=0.000 으로
    #  가장 눈에 띄는 자리에 실렸다 — 독자가 정반대로 읽는다.)
    gaps_rows = [t for t in te if t["share"] < t["rest_share"]]
    rich_rows = [t for t in te if t["share"] > t["rest_share"]]

    def _rows(rows: List[Dict]) -> None:
        L.append("| 주제 | 논문 | 개입연구 | 개입비율 | 그 외 논문 | p | q(FDR) |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        for t in rows:
            L.append(
                f"| {_md_cell(t['term'])} | {t['n_articles']} | {t['n_interventional']} "
                f"| {t['share']*100:.0f}% | {t['rest_share']*100:.0f}% "
                f"| {t['p_value']:.3f} | {t['q_value']:.3f} |"
            )
        L.append("")

    if gaps_rows:
        L.append("### 개입연구가 비어 있는 주제 (= 시험을 설계할 자리)")
        L.append("")
        _rows(gaps_rows)
        L.append(
            "_개입비율 = 그 주제 논문 중 RCT·임상시험 비중. `그 외 논문` 은 그 주제를 "
            "**달지 않은** 나머지 논문의 개입비율입니다(코퍼스 전체 평균이 아니라 비교군). "
            "p/q 는 두 비율이 다른지에 대한 Fisher 정확검정(양측)과 BH-FDR 보정값이며, "
            "**q ≤ 0.05** 인 줄이 가장 뚜렷한 근거 공백입니다._"
        )
        L.append("")
    elif te:
        L.append("### 개입연구가 비어 있는 주제")
        L.append("")
        L.append("_상위 주제 중 나머지 논문보다 개입연구 비율이 낮은 주제가 없습니다._")
        L.append("")
    if rich_rows:
        names = ", ".join(_md_cell(t["term"]) for t in rich_rows[:5])
        more = f" 외 {len(rich_rows) - 5}개" if len(rich_rows) > 5 else ""
        L.append(
            f"_참고: {names}{more} 는 오히려 개입연구가 **많은** 주제라 공백이 아닙니다 "
            "(전체 표는 `--format json` 또는 `--csv-section topic-evidence`)._"
        )
        L.append("")
    return L


def render_markdown(rep: Dict) -> str:
    L: List[str] = []
    q = rep["query"]
    L.append(f"# 연구 동향·공백 리포트 — `{q}`")
    L.append("")
    span = rep["year_span"]
    span_txt = f"{span[0]}–{span[1]}" if span else "연도 미상"
    src = rep.get("topic_source", "mesh")
    src_label = _TOPIC_SOURCE_LABEL.get(src, "주제어")
    L.append(
        f"- 분석 논문: **{rep['n_articles']}편** "
        f"({src_label} 보유 {rep['n_with_mesh']}편) · 발행연도 {span_txt}"
    )
    if src == "keywords":
        L.append(
            "- ⚠️ 이 입력에는 MeSH 색인이 없어 **저자 키워드**를 주제로 사용했습니다. "
            "저자 키워드는 MeSH 처럼 표준화돼 있지 않아 같은 개념이 여러 표기로 흩어질 수 "
            "있습니다(공백이 실제보다 부풀려질 수 있음)."
        )
    elif src == "mesh+keywords":
        L.append(
            "- ⚠️ `--include-keywords` 로 **MeSH 에 저자 키워드를 합쳐** 주제로 썼습니다. "
            "MeSH 미부여 최신 논문을 살리는 대신, 표준화되지 않은 키워드가 섞여 같은 개념이 "
            "여러 표기로 흩어질 수 있습니다."
        )

    truncated = rep.get("truncated")
    total_avail = rep.get("total_available")
    if total_avail is not None:
        line = f"- PubMed 검색 결과 **{total_avail:,}편** 중 **{rep['n_articles']}편** 분석"
        if truncated:
            line += " (표본 추출)"
        L.append(line)
    if truncated:
        L.append(
            "- ⚠️ **표본입니다 → 추세 관련 출력을 생략합니다.** 검색 결과가 "
            "`--max-records` 보다 많아 일부만 받았으므로, 이 표본의 연도 분포를 분야의 "
            "추세로 읽으면 안 됩니다. `--count-only` 로 전체 편수를 확인한 뒤 "
            "`--max-records` 를 그 수 이상으로 두면 전수 분석이 됩니다. "
            "**공백 분석은 이 표본에서도 유효**하지만, 표본인 만큼 드문 조합은 "
            "실제보다 비어 보일 수 있습니다(전수 검증 열을 함께 보세요)."
        )

    g = rep["growth"]
    # 초기 구간이 비어 있으면(예: 전부 같은 해) 비교 대상이 없다. 그런데도 배수를 찍으면
    # '연 0.0편 대비 ∞배' 라는 헛된 성장 주장이 나온다 — truncated 가드가 막으려던 것과
    # 같은 종류의 오류이므로 여기서도 막는다.
    if g.get("total") and not truncated and g.get("early_years"):
        ratio = g["ratio_per_year"]
        ratio_txt = "∞" if ratio == float("inf") else f"{ratio:.2f}배"
        split_yr = g.get("split")
        window = f"{split_yr}년 이후" if split_yr else "최근 구간"
        line = (
            f"- 발행량: **{window}** 연 {g['recent_per_year']:.1f}편 "
            f"(그 이전 연 {g['early_per_year']:.1f}편 대비 {ratio_txt})"
        )
        # 연도가 3개 미만이면 기울기 추정이 무의미하므로 표시하지 않는다.
        mk_n = (rep.get("mann_kendall") or {}).get("n", 0)
        ts = g.get("theil_sen")
        if ts is not None and mk_n >= 3:
            line += f" · 추세 기울기 {ts:+.1f}편/년(Theil–Sen)"
        L.append(line)

    mk = rep.get("mann_kendall")
    if mk and mk.get("direction") not in (None, "insufficient") and not truncated:
        label = {"increasing": "유의한 증가 추세 ↗︎", "decreasing": "유의한 감소 추세 ↘︎",
                 "flat": "뚜렷한 추세 없음"}.get(mk["direction"], mk["direction"])
        L.append(
            f"- 추세 검정(Mann–Kendall): **{label}** "
            f"(τ={mk['tau']:+.2f}, p={mk['p_value']:.3f}, n={mk['n']}년)"
        )
    if rep.get("small_sample"):
        L.append(
            f"- ⚠️ **표본 주의**: 분석 논문이 {rep['n_articles']}편으로 적습니다"
            f"(권장 ≥{SMALL_SAMPLE_N}편). 아래 공백 통계(기대·lift·p·q)는 한두 편의"
            " 색인 차이로 크게 흔들립니다 — `--max-records` 를 늘리거나 검색어를 넓혀 보세요."
        )
    L.append("")

    # 연도별 발행량
    counts = rep["yearly_counts"]
    if counts:
        title = "연도별 발행량" if not truncated else "연도별 발행량 (⚠️ 표본 — 추세 아님)"
        L.append(f"## {title}")
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
            L.append(f"- {_md_cell(j)} — {c}")
        L.append("")

    # 주요 주제
    if rep["top_mesh"]:
        L.append(f"## 주요 주제 ({src_label}, 논문 수)")
        L.append("")
        gap_terms = set(rep.get("gap_terms") or [])
        for t, c in rep["top_mesh"]:
            # 공백 탐색은 --gap-top-k 개만 쓴다. 어떤 주제가 후보였는지 밝히지 않으면
            # 목록에 보이는 주제가 왜 공백표에 안 나오는지 알 수 없다.
            mark = "" if not gap_terms or t in gap_terms else "  ·(공백 탐색 제외)"
            L.append(f"- {_md_cell(t)} — {c}{mark}")
        L.append("")

    # 부상 / 쇠퇴 — 쇠퇴는 한 줄 요약(행동으로 이어지지 않는 절이라 지면을 아낀다).
    for key, title, sign in (("emerging", "## ↗︎ 최근 부상 주제 (비중 상승)", "+"),):
        rows = rep.get(key)
        if not rows:
            continue
        L.append(title)
        L.append("")
        L.append("| 주제 | 초기 | 최근 | 비중변화 | p | q(FDR) |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for t in rows:
            p, qv = t.get("p_value"), t.get("q_value")
            ptxt = f"{p:.3f}" if p is not None else "–"
            qtxt = f"{qv:.3f}" if qv is not None else "–"
            L.append(
                f"| {_md_cell(t['term'])} | {t['early_count']} | {t['recent_count']} "
                f"| {sign}{t['delta']*100:.0f}%p | {ptxt} | {qtxt} |"
            )
        L.append("")
    if rep.get("declining"):
        names = ", ".join(
            f"{_md_cell(t['term'])}({t['delta']*100:.0f}%p)" for t in rep["declining"][:5]
        )
        L.append(f"_↘︎ 비중이 줄어든 주제: {names} (전체는 `--csv-section declining`)._")
        L.append("")
    if rep.get("emerging") or rep.get("declining"):
        L.append(
            "_순위는 비중 변화(Δ)로 매기고, **표시된 행에만** Fisher 정확검정(최근 vs 초기 "
            "등장, 양측)과 BH-FDR q 를 붙였습니다. 행을 Δ 로 고른 뒤 검정하므로 **선택편향이 "
            "남아** p·q 는 참고값입니다 — 편수가 적으면(예: 0→2편) 비중 변화가 커도 유의하지 "
            "않으니 초기/최근 실제 편수를 함께 보세요._"
        )
        L.append("")

    # 근거 지형 / 근거 공백 (연구 설계 축)
    L.extend(_render_evidence(rep))

    # 연구공백
    L.append("## 🔍 덜 연구된 각도 (저조 조합 = 연구공백 후보)")
    L.append("")
    if rep["gaps"]:
        L.append(
            "각각 개별적으로는 자주 다뤄지지만 **함께는 기대보다 훨씬 드물게** "
            "연구된 주제쌍입니다. lift(관측/기대)가 낮을수록 미개척 조합입니다."
        )
        L.append("")
        verified = any("verdict" in gp for gp in rep["gaps"])
        # 판정 가능한 행이 하나도 없으면 '추이' 열은 전부 '–' 라 지면만 차지한다.
        show_trend = any(
            gp.get("gap_trend") not in (None, "unknown") for gp in rep["gaps"]
        )
        head = "| 주제 A | 주제 B | 함께(관측) | 기대 | 부족 | lift | p | q(FDR) |"
        sep = "|---|---|---:|---:|---:|---:|---:|---:|"
        if show_trend:
            head += " 추이 |"
            sep += ":--:|"
        if verified:
            head += " PubMed 전수 | 판정 |"
            sep += "---:|:--|"
        L.append(head)
        L.append(sep)
        for gp in rep["gaps"]:
            row = (
                f"| {_md_cell(gp['term_a'])} | {_md_cell(gp['term_b'])} | {gp['observed']} "
                f"| {gp['expected']:.1f} | {gp.get('deficit', 0.0):+.1f} "
                f"| {gp['lift']:.2f} | {gp['p_value']:.3f} "
                f"| {gp.get('q_value', 1.0):.3f} |"
            )
            if show_trend:
                row += f" {_TREND_MARK.get(gp.get('gap_trend'), '–')} |"
            if verified:
                cnt = gp.get("pubmed_observed")
                lift = gp.get("pubmed_lift")
                cnt_txt = "–" if cnt is None else f"{cnt:,}"
                if lift is not None:
                    cnt_txt += f" (lift {lift:.2f})"
                row += f" {cnt_txt} | {_VERDICT_MARK.get(gp.get('verdict'), '–')} |"
            L.append(row)
        L.append("")
        if verified:
            L.append(
                f"_`PubMed 전수` = 같은 검색 제한 안에서 **전체 {rep.get('verify_total', 0):,}편**을 "
                "대상으로 두 주제의 실제 동시색인 편수를 다시 조회한 값(괄호는 그 전수 기준 lift)입니다. "
                "표본은 최대 `--max-records` 편이고 PubMed 는 MeSH 상하위어를 자동 확장하므로, "
                "**표본에서 0편이어도 실제 문헌에는 수백 편이 있을 수 있습니다.** "
                f"판정: ✅ 진짜 공백(전수 lift ≤ {VERIFY_ARTIFACT_LIFT}) · "
                "⬜ 전수에서도 0편 · ❌ 색인/표본 artifact(전수에서는 충분히 엮여 있음)._"
            )
            L.append("")
        L.append(
            f"_정렬: **{_SORT_LABEL.get(rep.get('gap_sort', 'deficit'), rep.get('gap_sort'))}** "
            "(`--gap-sort`). `부족`=기대−관측(있었어야 하는데 없는 편수) · "
            "`lift`=관측/기대 · `p`=초기하 하단꼬리 · `q`=BH-FDR 보정 · "
            "`추이`: ⬜완전공백(양쪽 구간 모두 0편) / ↗메워짐 / ↘벌어짐 / –판단불가. "
            "자세한 읽는 법은 사용법.md 참고._"
        )
        # 다중검정 예산을 솔직히 밝힌다. 검정 수가 많을수록 q 는 나빠지므로, 사용자가
        # 'q≤0.05 인 줄이 왜 없는지' 를 알 수 있어야 한다.
        #
        # 주의: `q ≥ p×m` 같은 하한은 **성립하지 않는다**. BH 는 순위 i 에서
        # q_(i) = min_{j≥i} (m·p_(j)/j) 이므로 q 는 오히려 p×m 보다 **작을 수 있다**
        # (번들 예시에서 p=0.008·m=9 인 쌍의 q 가 0.034 로, p×m=0.069 보다 작다).
        # 그래서 '필요한 p' 를 역산해 제시하지 않고, 실제로 **달성한 최소 q** 만 보고한다.
        m = rep.get("gap_n_tested") or 0
        best_q = min((gp.get("q_value", 1.0) for gp in rep["gaps"]), default=1.0)
        if m:
            note = f"_검정한 주제쌍 m={m}개 · 달성한 최소 q={best_q:.3f}"
            if best_q <= 0.05:
                note += " (q≤0.05 를 만족하는 후보가 있습니다)._"
            else:
                note += (
                    " — **q≤0.05 인 후보가 없습니다.** 검정 수가 많을수록 q 는 나빠지므로, "
                    "`--gap-top-k` 를 낮춰 검정 수를 줄이거나 `--max-records` 를 올려 "
                    "표본을 키우세요. (`--gap-min-expected` 를 낮추면 검정 수가 오히려 "
                    "늘어 q 는 더 나빠집니다.)_"
                )
            L.append(note)
        L.append("")
        # 전수 검증을 했다면 **검증을 통과한** 첫 후보를 추천한다. 검증 없이 1행을
        # 그대로 추천하면, 상하위어 같은 색인 artifact 를 최상위로 권하게 된다
        # (실측: 실제 쿼리 3건 모두 1순위가 artifact 였다).
        confirmed = [
            gp for gp in rep["gaps"]
            if gp.get("verdict") in ("confirmed", "confirmed_empty")
        ]
        all_artifact = any("verdict" in gp for gp in rep["gaps"]) and not confirmed
        if all_artifact:
            L.append(
                "> ⚠️ 전수 검증 결과 **표에 남은 후보가 모두 색인/표본 artifact** 입니다 "
                "— 실제 문헌에서는 이미 충분히 함께 다뤄지고 있습니다. 추천할 후보가 "
                "없으므로 제안을 생략합니다. `--gap-top-k` 를 넓히거나 검색어를 바꿔 보세요."
            )
            L.append("")
        if not all_artifact:
            top = confirmed[0] if confirmed else rep["gaps"][0]
            L.append(
                f"> 제안: **{_md_cell(top['term_a'])} × {_md_cell(top['term_b'])}** 를 "
                "결합한 분석/논문을 검토하세요. "
                f"관련 논문 각각 {top['count_a']}·{top['count_b']}편이 있으나 둘을 함께 다룬 논문은 "
                f"{top['observed']}편뿐입니다(기대 {top['expected']:.1f}편, p={top['p_value']:.3f}, "
                f"q={top.get('q_value', 1.0):.3f})."
            )
            # 표에서 q≤0.05 를 권해 놓고 q=1.000 짜리를 굵게 추천하면 안 된다.
            top_q = top.get("q_value", 1.0)
            if top_q > 0.05:
                L.append("")
                L.append(
                    f"> ⚠️ 이 후보의 q={top_q:.3f} 는 다중검정 보정 기준(0.05)을 넘습니다 — "
                    "**탐색적 후보**로만 쓰고, 아래 검증 링크로 실제 문헌을 확인하세요."
                )
            # 검증 링크 — '대표 논문을 직접 확인하라'는 조언을 실행 가능하게 만든다.
            if top.get("pubmed_url_mesh"):
                L.append("")
                L.append(
                    f"> 검증: [MeSH 색인 기준으로 이 조합 검색]({top['pubmed_url_mesh']}) · "
                    f"[제목/초록(자유어) 기준]({top['pubmed_url_text']}) — "
                    "자유어 검색에서는 논문이 많이 나온다면, 이 '공백'은 연구 공백이 아니라 "
                    "**색인 방식의 차이(artifact)** 일 가능성이 큽니다."
                )
            # Swanson ABC 가교 주제 — '왜 이 주제인가'의 기전 서사.
            bridges = top.get("bridges") or []
            if bridges:
                btxt = ", ".join(
                    f"**{_md_cell(c)}**(A&C {ac}·C&B {cb})" for c, ac, cb in bridges
                )
                L.append("")
                L.append(
                    f"> 가교(Swanson ABC): {_md_cell(top['term_a'])} 와 "
                    f"{_md_cell(top['term_b'])} 를 잇는 제3 주제 → {btxt}. "
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
                    parts.append(f"{_md_cell(top['term_a'])}: {', '.join(pa)}")
                if pb:
                    parts.append(f"{_md_cell(top['term_b'])}: {', '.join(pb)}")
                if both:
                    parts.append(f"함께: {', '.join(both)}")
                # RIS/CSV 입력은 PMID 가 없어 DOI 나 '?' 가 들어올 수 있으므로 라벨을 정확히.
                L.append("> 대표 논문 ID(확인용, PMID 또는 DOI) — " + " · ".join(parts))
    else:
        L.append(
            "_설정한 임계값에서 뚜렷한 저조 조합을 찾지 못했습니다._ "
            "`--gap-max-lift` _를 높이면 더 느슨한 조합까지 봅니다._ "
            "_후보 자체가 적다면_ `--gap-top-k` _를 올려 더 많은 주제를 보되, "
            "검정 수가 늘어 q 는 나빠진다는 점을 감안하세요._"
        )
    L.append("")

    L.append("---")
    L.append(
        f"_주의: 이 리포트는 {src_label} 공동출현 기반 휴리스틱입니다. "
        "'공백'은 문헌 부재의 신호일 뿐 인과/타당성을 보장하지 않으며, "
        "실제 착수 전 위 검증 링크와 대표 논문을 직접 확인하세요._"
    )
    L.extend(_render_meta(rep.get("meta")))
    return "\n".join(L)


# 실행정보에서 '기본값과 다른 옵션'만 강조하기 위한 기준값(build_parser 기본값과 일치).
_DEFAULT_PARAMS = {
    "gap_top_k": 12, "gap_min_expected": 2.0, "gap_max_lift": 0.5, "gap_max_q": None,
    "gap_sort": "deficit", "bridges": True, "evidence": True, "top_evidence": 12,
    "top_mesh": 15, "top_journals": 8, "major_topics_only": False,
    "include_keywords": False, "include_check_tags": False, "min_year": None,
    "max_year": None, "exclude_terms": None, "sample": "stratified",
    "verify_gaps": True,
}


def _render_meta(meta: Optional[Dict]) -> List[str]:
    """재현용 실행 정보 — 논문 Methods 에 그대로 옮겨 적을 수 있도록.

    같은 파일·같은 옵션이면 같은 결과가 나오는 도구이므로, 무엇으로 돌렸는지만
    남아 있으면 리포트가 재현 가능해진다.
    """
    if not meta:
        return []
    L = ["", "### 실행 정보(재현용)", ""]
    L.append(f"- 도구: `{meta.get('tool', 'pubgap')}` v{meta.get('version', '?')}")
    if meta.get("generated_at"):
        L.append(f"- 생성 시각(UTC): {meta['generated_at']}")
    src = meta.get("input") or {}
    if src.get("path"):
        line = f"- 입력: `{src['path']}`"
        if src.get("bytes") is not None:
            line += f" ({src['bytes']:,} bytes"
            if src.get("sha256"):
                line += f", sha256 `{src['sha256'][:16]}…`"
            line += ")"
        L.append(line)
    if src.get("format"):
        L.append(f"- 입력 형식(자동판별): `{src['format']}`")
    window = []
    if src.get("years") is not None:
        window.append(f"최근 {src['years']}년")
    if src.get("max_records") is not None:
        window.append(f"최대 {src['max_records']:,}편 요청")
    if src.get("total_available") is not None:
        window.append(f"검색결과 {src['total_available']:,}편")
    if window:
        L.append("- 조회 범위: " + " · ".join(window))
    params = meta.get("params") or {}
    if params:
        defaults = dict(_DEFAULT_PARAMS)
        defaults.update(meta.get("defaults") or {})   # 실행 경로별 기본값
        changed = {k: v for k, v in params.items() if defaults.get(k, object()) != v}
        if changed:
            L.append("- 기본값과 다른 옵션: "
                     + ", ".join(f"`{k}={v}`" for k, v in sorted(changed.items())))
        else:
            L.append("- 옵션: 전부 기본값")
        L.append("  (나머지는 기본값 — 전체 목록은 `--format json` 의 `meta.params`)")
    return L


_CSV_HEADER = [
    "term_a", "term_b", "observed", "expected", "deficit", "lift",
    "jaccard", "cosine", "npmi", "count_a", "count_b", "p_value", "q_value",
    "observed_early", "observed_recent", "gap_trend",
    "pmids_a", "pmids_b", "pmids_both", "bridges",
    "pubmed_url_mesh", "pubmed_url_text",
    "verdict", "pubmed_observed", "pubmed_lift",
]

# 엑셀/구글시트는 이 문자로 시작하는 셀을 **수식**으로 해석한다. 서지 데이터에
# '=cmd|...' 같은 문자열이 섞여 들어오면 스프레드시트에서 실행 위험이 되므로,
# 값을 바꾸지 않는 선에서 앞에 작은따옴표를 붙여 텍스트로 강제한다(CSV 주입 방어).
_CSV_INJECTION_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _is_numeric(text: str) -> bool:
    """엑셀이 수식이 아니라 **숫자**로 읽는 값인가(음수·지수표기 포함).

    `inf`/`nan` 류는 float() 가 받아들이지만 엑셀에서는 숫자가 아니라 `-`/`+` 로
    시작하는 문자열이라 수식으로 해석된다(`#NAME?`). 유한한 값만 면제한다.
    """
    try:
        value = float(text)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value)


def _csv_safe(value):
    """CSV 수식 주입 방어 — 단, 숫자는 건드리지 않는다.

    접두 목록에 '-' 가 있어서, 음수인 지표(gaps 의 `npmi`, declining 의 `delta`,
    때로는 `deficit`)가 **모든 행에서** "'-1.0000" 으로 나갔다. 그러면 pandas 는 그
    열을 object 로 읽고 float() 는 예외를 던진다 — 스프레드시트/파이프라인용 출력이
    쓸 수 없게 된다. 숫자는 수식이 될 수 없으므로 그대로 둔다.

    앞 공백은 **무시하고** 판정한다. 스프레드시트는 가져오기 때 선행 공백을 지우므로
    '" =1+1"' 같은 값은 그대로 수식이 된다(\t·\r 를 막는 것과 같은 이유).
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip("\t\r\n\v\f \u00a0\ufeff")
    if stripped.startswith(_CSV_INJECTION_PREFIX) and not _is_numeric(stripped):
        return "'" + value
    return value

# --csv-section 으로 고를 수 있는 표. 공백 표 말고도 스프레드시트로 바로 옮기고 싶은
# 집계(연도별 편수·저널·주제·부상/쇠퇴·근거 지형)를 모두 내보낼 수 있게 한다.
CSV_SECTIONS: tuple = (
    "gaps", "yearly", "journals", "mesh", "emerging", "declining",
    "evidence", "topic-evidence",
)


def render_csv(rep: Dict, section: str = "gaps") -> str:
    """리포트의 한 표를 CSV 로 렌더링(스프레드시트/파이프라인용).

    엑셀 한글 깨짐을 막기 위해 UTF-8 BOM 을 붙인다. 해당 표가 비어 있으면 헤더만 출력.
    PMID 목록은 세미콜론, 가교는 'C(ac/cb)' 형태로 담는다(csv 모듈이 인용 처리).
    """
    if section not in CSV_SECTIONS:
        raise ValueError(
            f"알 수 없는 CSV 섹션: {section!r} (가능: {', '.join(CSV_SECTIONS)})"
        )
    buf = io.StringIO()
    writer = _SafeWriter(csv.writer(buf, lineterminator="\n"))
    _CSV_RENDERERS[section](writer, rep)
    return "﻿" + buf.getvalue().rstrip("\n")


class _SafeWriter:
    """csv.writer 래퍼 — 모든 셀에 수식 주입 방어를 적용한다."""

    def __init__(self, writer):
        self._writer = writer

    def writerow(self, row) -> None:
        self._writer.writerow([_csv_safe(v) for v in row])


def _csv_gaps(writer, rep: Dict) -> None:
    writer.writerow(_CSV_HEADER)
    for gp in rep.get("gaps", []):
        bridges = "; ".join(f"{c}({ac}/{cb})" for c, ac, cb in (gp.get("bridges") or []))
        writer.writerow([
            gp["term_a"],
            gp["term_b"],
            gp["observed"],
            f"{gp['expected']:.4f}",
            f"{gp.get('deficit', 0.0):.4f}",
            f"{gp['lift']:.4f}",
            f"{gp.get('jaccard', 0.0):.4f}",
            f"{gp.get('cosine', 0.0):.4f}",
            f"{gp.get('npmi', 0.0):.4f}",
            gp["count_a"],
            gp["count_b"],
            f"{gp['p_value']:.6f}",
            f"{gp.get('q_value', 1.0):.6f}",
            gp.get("observed_early", 0),
            gp.get("observed_recent", 0),
            gp.get("gap_trend", "unknown"),
            "; ".join(gp.get("pmids_a") or []),
            "; ".join(gp.get("pmids_b") or []),
            "; ".join(gp.get("pmids_both") or []),
            bridges,
            gp.get("pubmed_url_mesh", ""),
            gp.get("pubmed_url_text", ""),
            gp.get("verdict", ""),
            "" if gp.get("pubmed_observed") is None else gp["pubmed_observed"],
            "" if gp.get("pubmed_lift") is None else f"{gp['pubmed_lift']:.4f}",
        ])


def _csv_yearly(writer, rep: Dict) -> None:
    writer.writerow(["year", "n_articles"])
    for y in sorted(rep.get("yearly_counts") or {}):
        writer.writerow([y, rep["yearly_counts"][y]])


def _csv_journals(writer, rep: Dict) -> None:
    writer.writerow(["journal", "n_articles"])
    for j, c in rep.get("top_journals") or []:
        writer.writerow([j, c])


def _csv_mesh(writer, rep: Dict) -> None:
    writer.writerow(["term", "n_articles"])
    for t, c in rep.get("top_mesh") or []:
        writer.writerow([t, c])


def _csv_trend_rows(writer, rows) -> None:
    writer.writerow([
        "term", "early_count", "recent_count", "early_share", "recent_share",
        "delta", "p_value", "q_value",
    ])
    for t in rows or []:
        p, q = t.get("p_value"), t.get("q_value")
        writer.writerow([
            t["term"], t["early_count"], t["recent_count"],
            f"{t['early_share']:.6f}", f"{t['recent_share']:.6f}", f"{t['delta']:.6f}",
            "" if p is None else f"{p:.6f}",
            "" if q is None else f"{q:.6f}",
        ])


def _csv_evidence(writer, rep: Dict) -> None:
    ev = rep.get("evidence") or {}
    writer.writerow(["tier", "label", "count", "share"])
    for t in ev.get("tiers") or []:
        writer.writerow([t["tier"], t["label"], t["count"], f"{t['share']:.6f}"])


def _csv_topic_evidence(writer, rep: Dict) -> None:
    writer.writerow([
        "term", "n_articles", "n_interventional", "share",
        "rest_n", "rest_interventional", "rest_share", "p_value", "q_value",
    ])
    for t in rep.get("topic_evidence") or []:
        writer.writerow([
            t["term"], t["n_articles"], t["n_interventional"], f"{t['share']:.6f}",
            t["rest_n"], t["rest_interventional"], f"{t['rest_share']:.6f}",
            f"{t['p_value']:.6f}", f"{t['q_value']:.6f}",
        ])


_CSV_RENDERERS = {
    "gaps": _csv_gaps,
    "yearly": _csv_yearly,
    "journals": _csv_journals,
    "mesh": _csv_mesh,
    "emerging": lambda w, r: _csv_trend_rows(w, r.get("emerging")),
    "declining": lambda w, r: _csv_trend_rows(w, r.get("declining")),
    "evidence": _csv_evidence,
    "topic-evidence": _csv_topic_evidence,
}
