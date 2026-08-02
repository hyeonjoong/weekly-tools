"""Render evaluated ideas as a Markdown report, HTML page, CSV or JSON."""
from __future__ import annotations

import csv
import html
import io
import json

from .engine import IdeaResult, modality_label
from .manifest import Manifest


# Report-readability caps. The full, uncapped data is always in --csv/--json.
_MAX_WARNINGS = 20
_MAX_VARS = 60


def _mods(result: IdeaResult) -> str:
    return " × ".join(modality_label(m) for m in result.modalities)


def _collapse_warnings(warnings) -> list:
    """De-duplicate warnings, keeping order and noting repeat counts."""
    counts: dict = {}
    for w in warnings:
        counts[w] = counts.get(w, 0) + 1
    return [w if c == 1 else f"{w} (동일 경고 {c}건)" for w, c in counts.items()]


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
    max_n = settings.get("max_n")
    if max_n:
        lines.append(f"- 모집 상한 가정: 최대 {max_n}명 (초과 아이디어는 상세에 표시)")
    filtered = " · '충분 가능'만 표시(--feasible-only)" if settings.get(
        "feasible_only") else ""
    lines.append(f"- 생성된 아이디어: {len(results)}개 (점수순){filtered}")
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
    legend = (
        "> '현재 검정력' = 보유 N과 템플릿의 가정 효과크기에서 실제로 얻는 검정력"
        "(예: 0.62 = 참 효과가 있어도 38% 확률로 놓침). 상관·평균차는 정규근사라 "
        "N이 작을수록(N≲30) 정확한 t 기반 값보다 최대 ~3%p 높게 나올 수 있습니다. "
        "'탐지가능 효과' = 보유 N으로 alpha/power 기준 검출 가능한 최소 효과크기"
        "(민감도 분석). 예: `r≥0.29`는 상관 0.29 이상이면 검출 가능."
    )
    # The clinical clauses used to be printed on every report with invented
    # numbers that matched no row on the page (and contradicted the README).
    # Explain each notation only when a row actually uses it.
    metrics = {r.detectable["metric"] for r in results if r.detectable}
    if "delta_p" in metrics:
        legend += (
            " 이분 종점의 `Δp≥…`는 대조군 비율을 고정하고 구한 위험차이며, "
            "괄호의 `30%→48%`는 그때의 대조군→시험군 비율입니다."
        )
    if "hr" in metrics:
        legend += (
            " 시간-사건 종점의 `HR≥…`는 위험비가 로그척도에서 대칭이므로 "
            "괄호에 반대 방향(보호 효과) 값을 함께 적습니다."
        )
    if "f" in metrics:
        legend += (
            " 다군 설계의 `f≥…`는 Cohen's f(군간 표준편차/군내 표준편차)이며, "
            "0.10/0.25/0.40이 각각 작음/중간/큼의 관례적 기준입니다 — 어느 "
            "두 군이 다른지가 아니라 '군간 차이가 존재하는가'에 대한 값입니다."
        )
    lines.append(legend)
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
        if r.required_events is not None:
            got = (
                f" · 보유 N 기준 예상 사건 {r.expected_events}건"
                if r.expected_events is not None else ""
            )
            lines.append(
                f"- **필요 사건 수(시간-사건 설계)**: {r.required_events}건{got} "
                "— 검정력은 등록 인원이 아니라 사건 수로 결정됩니다."
            )
        if r.detectable is not None:
            lines.append(
                f"- **탐지가능 최소효과(보유 N 기준)**: {r.detectable_label} "
                "— 이보다 작은 실제 효과는 검출되지 않을 수 있음."
            )
        if r.within_max_n is False:
            lines.append(
                "- **모집 상한 초과**: 설정한 --max-n 로는 권장 N에 도달할 수 "
                "없습니다(위 참고 항목 확인)."
            )
        if r.justification:
            lines.append(
                f"- **표본수 산출 근거(프로토콜·IRB 문장)**: {r.justification}"
            )
        if r.n_sensitivity:
            # Name the metric: "효과 0.79" beside the 보수적 label reads as a
            # LARGER effect when the metric is a hazard ratio.
            strip = " / ".join(
                f"{s['label']} N={s['required_n']}"
                f"({s.get('metric', '효과')} {s['effect_value']})"
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
        "_권장 N은 Fisher-z(상관)·정규근사(평균차·두 비율·ANCOVA)·비중심 F"
        "(회귀/ΔR²·일원배치 ANOVA)·Schoenfeld(로그순위)에 기반한 계획용 "
        "추정치이며, 효과크기는 각 템플릿에 내장된 가정입니다. 최종 검정력은 "
        "G*Power 등으로 확정하세요._"
    )
    return "\n".join(lines)


_HTML_CSS = """\
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic",
       sans-serif; line-height: 1.6; max-width: 1100px; margin: 2rem auto;
       padding: 0 1.2rem; }
h1 { font-size: 1.6rem; border-bottom: 2px solid currentColor; padding-bottom: .3rem; }
h2 { font-size: 1.25rem; margin-top: 2rem; }
h3 { font-size: 1.05rem; margin-top: 1.6rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { border: 1px solid #9993; padding: .4rem .55rem; text-align: left;
         vertical-align: top; }
th { background: #8881; }
tr.ok td:nth-child(8) { font-weight: 700; }
tr.short td:nth-child(8) { font-weight: 700; opacity: .85; }
.meta { font-size: .9rem; }
.warn { border-left: 4px solid #c93; background: rgba(204,153,51,.10);
        padding: .6rem .9rem; margin: 1rem 0; font-size: .9rem; }
.note { font-size: .87rem; opacity: .85; margin: .15rem 0 .15rem 1.1rem; }
.legend, footer { font-size: .85rem; opacity: .85; margin-top: 1.2rem; }
dl { margin: .4rem 0; }
dt { font-weight: 700; margin-top: .5rem; }
dd { margin: 0 0 0 1rem; }
@media print { body { max-width: none; margin: 0; } h2 { page-break-before: auto; } }
"""


def _esc(value) -> str:
    """HTML-escape any value (template packs are untrusted user content)."""
    return html.escape("" if value is None else str(value), quote=True)


def render_html(
    manifest: Manifest,
    results: list,
    alpha: float,
    power: float,
    dropout: float = 0.0,
    settings: dict = None,
) -> str:
    """A self-contained HTML report of the same run the Markdown shows.

    Markdown is fine in a terminal but is not what gets circulated: a PI wants
    something to open, print to PDF and paste into a protocol draft. This is one
    file, no assets, no scripts — and everything user-supplied (study name,
    template titles, hypotheses, variable names) is escaped, because a template
    pack is arbitrary third-party JSON and a manifest column can contain
    anything a spreadsheet allowed.
    """
    settings = settings or {}
    sided = settings.get("sided", 2)
    n_tests = settings.get("n_tests", 1)
    repeats = settings.get("repeats", 1)
    icc = settings.get("icc", 0.0)

    out: list = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="ko"><head><meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    out.append(f"<title>논문 아이디어 매트릭스 — {_esc(manifest.study)}</title>")
    out.append(f"<style>{_HTML_CSS}</style></head><body>")
    out.append(f"<h1>논문 아이디어 매트릭스 — {_esc(manifest.study)}</h1>")

    avail = ", ".join(sorted(modality_label(m) for m in manifest.modalities())) or "(없음)"
    meta = [f"보유 모달리티: {_esc(avail)}"]
    tail = " · 단측검정" if sided == 1 else ""
    meta.append(f"검정력 기준: alpha={_esc(alpha)}, power={_esc(power)}{tail} (계획용 근사)")
    if n_tests > 1:
        meta.append(
            f"다중비교 보정: 아이디어당 주요 비교 {n_tests}회 → 적용 alpha = "
            f"{_esc(alpha)}/{n_tests} = {alpha / n_tests:.5g} (Bonferroni)"
        )
    if repeats > 1:
        meta.append(
            f"반복측정: 피험자당 {repeats}회, ICC={icc:g} → 설계효과 "
            f"{1 + (repeats - 1) * icc:.2f} (관측 단위로 사이징되는 템플릿에만 적용)"
        )
    if manifest.linked_n:
        pairs = ", ".join(
            "+".join(sorted(modality_label(m) for m in combo)) + f"={n}"
            for combo, n in sorted(
                manifest.linked_n.items(), key=lambda kv: sorted(kv[0])
            )
        )
        meta.append(f"선언된 연결 표본수(linked N): {_esc(pairs)}")
    if dropout > 0.0:
        meta.append(f"중도탈락 가정: {dropout:.0%} → 권장 모집 N = ⌈권장 N / (1−{dropout:.2f})⌉")
    if settings.get("max_n"):
        meta.append(f"모집 상한 가정: 최대 {settings['max_n']}명")
    filtered = " · '충분 가능'만 표시(--feasible-only)" if settings.get(
        "feasible_only") else ""
    meta.append(f"생성된 아이디어: {len(results)}개 (점수순){filtered}")
    out.append('<ul class="meta">')
    out.extend(f"<li>{m}</li>" for m in meta)
    out.append("</ul>")

    if manifest.warnings:
        collapsed = _collapse_warnings(manifest.warnings)
        shown = collapsed[:_MAX_WARNINGS]
        out.append('<div class="warn"><strong>⚠️ 매니페스트 경고</strong><ul>')
        out.extend(f"<li>{_esc(w)}</li>" for w in shown)
        if len(collapsed) > len(shown):
            out.append(f"<li>…외 {len(collapsed) - len(shown)}종의 경고 생략</li>")
        out.append("</ul></div>")

    if not results:
        out.append("<p>매칭되는 아이디어가 없습니다. 모달리티/변수를 더 채워 보세요.</p>")
        out.append("</body></html>")
        return "\n".join(out)

    out.append("<h2>요약 매트릭스</h2>")
    out.append("<table><thead><tr>")
    for head in ("#", "아이디어", "모달리티", "권장 N", "보유 N", "현재 검정력",
                 "탐지가능 효과", "실현가능성", "적합 저널"):
        out.append(f"<th>{head}</th>")
    out.append("</tr></thead><tbody>")
    for i, r in enumerate(results, 1):
        an = r.available_n if r.available_n is not None else "?"
        cls = "ok" if r.feasible is True else ("short" if r.feasible is False else "")
        out.append(f'<tr class="{cls}">')
        for cell in (i, r.title, _mods(r), _req_n(r), an, r.power_label,
                     r.detectable_label, r.feasibility_label, r.journal):
            out.append(f"<td>{_esc(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    metrics = {r.detectable["metric"] for r in results if r.detectable}
    legend = (
        "'현재 검정력' = 보유 N과 가정 효과크기에서 실제로 얻는 검정력. "
        "'탐지가능 효과' = 보유 N으로 검출 가능한 최소 효과크기(민감도 분석)."
    )
    if "f" in metrics:
        legend += " 다군 설계의 f는 Cohen's f(0.10/0.25/0.40 = 작음/중간/큼)입니다."
    out.append(f'<p class="legend">{_esc(legend)}</p>')

    out.append("<h2>상세</h2>")
    for i, r in enumerate(results, 1):
        out.append(f"<h3>{i}. {_esc(r.title)}</h3>")
        rows = [
            ("모달리티 결합", f"{_mods(r)}  (설계: {r.design})"),
            ("가설", r.hypothesis),
            ("예측/독립변수", ", ".join(r.predictors)),
            ("결과/종속변수", ", ".join(r.outcomes)),
            ("권장 분석", r.analysis),
        ]
        feas = (
            f"{r.feasibility_label} (권장 N={_req_n(r)}, 보유 N="
            f"{r.available_n if r.available_n is not None else '미상'})"
        )
        if r.recruit_n is not None:
            feas += f" · 권장 모집 N(탈락 보정)={r.recruit_n}"
        rows.append(("실현가능성", feas))
        if r.attained_power is not None:
            rows.append(("현재 표본의 검정력",
                         f"{r.power_label} — 목표 {power:.2f} 대비."))
        if r.required_rows is not None and r.required_rows != r.required_n:
            rows.append((
                "반복측정 환산",
                f"필요한 분석 행 {r.required_rows}개 → 피험자 {r.required_n}명",
            ))
        if r.required_events is not None:
            got = (f" · 보유 N 기준 예상 사건 {r.expected_events}건"
                   if r.expected_events is not None else "")
            rows.append(("필요 사건 수(시간-사건 설계)",
                         f"{r.required_events}건{got}"))
        if r.detectable is not None:
            rows.append(("탐지가능 최소효과(보유 N 기준)", r.detectable_label))
        if r.within_max_n is False:
            rows.append(("모집 상한 초과",
                         "설정한 --max-n 로는 권장 N에 도달할 수 없습니다."))
        if r.justification:
            rows.append(("표본수 산출 근거(프로토콜·IRB 문장)", r.justification))
        if r.n_sensitivity:
            rows.append((
                "표본수 민감도(효과크기 가정별 권장 N)",
                " / ".join(
                    f"{s['label']} N={s['required_n']}"
                    f"({s.get('metric', '효과')} {s['effect_value']})"
                    for s in r.n_sensitivity
                ),
            ))
        if r.matched_variables:
            shown = r.matched_variables[:_MAX_VARS]
            more = len(r.matched_variables) - len(shown)
            rows.append((
                "이 조합에서 접근 가능한 열(가설과 자동매칭 아님)",
                ", ".join(shown) + (f" … 외 {more}개" if more > 0 else ""),
            ))
        rows.append(("적합 저널 유형", r.journal))
        rows.append(("신규성/중복성 메모", r.novelty))
        out.append("<dl>")
        for key, value in rows:
            out.append(f"<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>")
        out.append("</dl>")
        for note in r.notes:
            out.append(f'<p class="note">참고: {_esc(note)}</p>')

    out.append(
        "<footer><hr>권장 N은 Fisher-z(상관)·정규근사(평균차·두 비율·ANCOVA)·"
        "비중심 F(회귀/ΔR²·일원배치 ANOVA)·Schoenfeld(로그순위)에 기반한 "
        "계획용 추정치입니다. 최종 검정력은 G*Power 등으로 확정하세요."
        "</footer>"
    )
    out.append("</body></html>")
    return "\n".join(out)


# Excel/Sheets treat a cell beginning with any of these as a FORMULA, not text.
# Template packs are third-party JSON whose title/hypothesis/analysis/journal
# strings all land in cells, and the whole point of --csv is that a researcher
# opens it in Excel. Prefixing an apostrophe is the standard mitigation: the cell
# still reads as the original text, it just isn't executable.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    """One CSV cell, neutralised against spreadsheet formula injection.

    Numbers are passed through untouched — a leading '-' on a negative number is
    arithmetic, not an injection, and quoting it would break every downstream
    consumer of the numeric columns.
    """
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    return "'" + text if text[:1] in _CSV_FORMULA_LEAD else text


def render_csv(results: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "rank", "idea_id", "title", "modalities", "design", "hypothesis",
            "predictors", "outcomes", "analysis", "required_n", "required_rows",
            "recruit_n", "available_n", "analysis_n", "attained_power",
            "required_events", "expected_events", "within_max_n",
            "detectable_metric", "detectable_value",
            "n_sensitivity", "feasibility", "journal", "novelty", "score",
            "sample_size_justification",
        ]
    )
    for i, r in enumerate(results, 1):
        det_metric = r.detectable["metric"] if r.detectable else ""
        det_value = round(r.detectable["value"], 4) if r.detectable else ""
        sens = "|".join(
            f"{s['label']}:{s['required_n']}@{s.get('metric', '')}"
            f"{s['effect_value']}"
            for s in r.n_sensitivity
        )
        writer.writerow(
            [
                _csv_cell(v) for v in (
                    i, r.idea_id, r.title, "|".join(r.modalities), r.design,
                    r.hypothesis, "|".join(r.predictors), "|".join(r.outcomes),
                    r.analysis, r.required_n if r.required_n is not None else "",
                    r.required_rows if r.required_rows is not None else "",
                    r.recruit_n if r.recruit_n is not None else "",
                    r.available_n if r.available_n is not None else "",
                    r.analysis_n if r.analysis_n is not None else "",
                    round(r.attained_power, 4)
                    if r.attained_power is not None else "",
                    r.required_events if r.required_events is not None else "",
                    r.expected_events if r.expected_events is not None else "",
                    "" if r.within_max_n is None else int(r.within_max_n),
                    det_metric, det_value, sens,
                    r.feasibility_label, r.journal, r.novelty, r.score,
                    r.justification,
                )
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
        # Same collapse the Markdown applies: a 50k-row inventory with one bad
        # modality produced a 5 MB JSON of 50,000 identical warning strings
        # while the .md from the same run correctly showed one line.
        "warnings": _collapse_warnings(manifest.warnings),
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
                "required_events": r.required_events,
                "expected_events": r.expected_events,
                "within_max_n": r.within_max_n,
                "sample_size_justification": r.justification,
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
