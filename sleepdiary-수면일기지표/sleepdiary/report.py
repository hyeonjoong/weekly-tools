"""사람이 읽는 보고서 만들기 (텍스트 / 마크다운).

숫자만 찍지 않는다. **무엇을 몇 건 제외했는지**, **분석 단위가 사람인지 밤인지**,
**시각형 지표는 원형평균으로 계산했는지**를 함께 적는다. 보고서를 그대로
논문 초고에 옮겨도 과장이 섞이지 않도록 하는 것이 목적이다.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .aggregate import (
    ALL_KEYS,
    CIRCULAR_KEYS,
    LINEAR_METRICS,
    METRIC_LABEL,
    METRIC_UNIT,
    GroupSummary,
    PeriodComparison,
    SubjectSummary,
)
from .timeparse import fmt_clock, fmt_hm

RULE = "=" * 74
THIN = "-" * 74


def _num(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def fmt_metric(key: str, value: Optional[float]) -> str:
    """지표 값을 사람이 읽는 문자열로. 시각은 HH:MM, 분은 'Xh YYm'."""
    if value is None:
        return "—"
    if key in CIRCULAR_KEYS:
        return fmt_clock(value)
    unit = METRIC_UNIT.get(key, "")
    if unit == "min":
        return f"{value:.0f}분 ({fmt_hm(value)})"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "회":
        return f"{value:.1f}회"
    return _num(value)


def fmt_delta(key: str, value: Optional[float]) -> str:
    """변화량(차이) 표기 — 시각형 지표도 '분' 단위 차이로 읽는다."""
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    if key in CIRCULAR_KEYS or METRIC_UNIT.get(key) == "min":
        return f"{sign}{value:.1f}분"
    if METRIC_UNIT.get(key) == "%":
        return f"{sign}{value:.1f}%p"
    return f"{sign}{value:.2f}"


def fmt_p(p: Optional[float]) -> str:
    if p is None:
        return "—"
    if p < 0.0001:
        return "< 0.0001"
    return f"{p:.4f}"


def fmt_p_eq(p: Optional[float]) -> str:
    """문장 안에 넣을 때 "p = 0.0031" / "p < 0.0001" 이 되도록 부호까지 만든다."""
    if p is None:
        return "= —"
    return "< 0.0001" if p < 0.0001 else f"= {p:.4f}"


def _pad(text: str, width: int) -> str:
    """한글 폭(2칸)을 고려한 왼쪽 정렬 패딩."""
    return text + " " * max(0, width - _width(text))


def _width(text: str) -> int:
    total = 0
    for ch in text:
        total += 2 if ord(ch) > 0x2FFF else 1
    return total


def _rpad(text: str, width: int) -> str:
    return " " * max(0, width - _width(text)) + text


# ---------------------------------------------------------------------------
# 텍스트 보고서
# ---------------------------------------------------------------------------

def render_header(meta: dict) -> list[str]:
    lines = [RULE, "  sleepdiary — 수면일기 지표 보고서", RULE, ""]
    lines.append(f"  입력 파일  : {meta['path']}")
    lines.append(f"  인코딩     : {meta['encoding']}   (읽은 행 {meta['n_rows']}행)")
    if meta["encoding"] == "latin-1":
        lines.append("    ⚠ latin-1은 어떤 바이트열이든 받아들이는 마지막 수단입니다.")
        lines.append("      한글 열이름이 깨져 보인다면 인코딩을 잘못 판별한 것이니")
        lines.append("      엑셀에서 'CSV UTF-8'로 다시 저장하세요.")
    if meta.get("date_means"):
        how = "기상한 아침" if meta["date_means"] == "morning" else "잠자리에 든 저녁"
        lines.append(f"  날짜 해석  : 일기의 날짜 = {how} (--date-means)")
    lines.append("")
    lines.append("  열 매핑 (자동인식 / 사용자 지정):")
    for field in ("subject", "date", "period", "bedtime", "lights_off", "sol", "waso",
                  "awakenings", "final_awake", "out_of_bed"):
        col = meta["cols"].get(field)
        mark = "" if col else "  (없음)"
        lines.append(f"    {_pad(field, 14)}→ {col or '—'}{mark}")
    lines.append("")
    return lines


def render_quality(nights: Sequence, max_show: int = 12) -> list[str]:
    """제외된 밤과 경고를 반드시 눈에 띄게 적는다."""
    bad = [n for n in nights if not n.valid]
    warned = [n for n in nights if n.valid and n.warnings]
    lines = [THIN, "  자료 품질", THIN]
    lines.append(f"  전체 {len(nights)}박 중 유효 {len(nights) - len(bad)}박, "
                 f"제외 {len(bad)}박, 경고 있으나 포함 {len(warned)}박")

    good = [n for n in nights if n.valid]
    for field, label in (("sol", "입면잠복기 SOL"), ("waso", "중도각성 WASO")):
        filled = [n for n in good if any(m.split("(")[0] == field for m in n.imputed)]
        if not filled:
            continue
        no_column = any(f"{field}(열없음)" in n.imputed for n in filled)
        why = "열이 아예 없어" if no_column else "칸이 비어 있어"
        lines.append(
            f"  ● {label}: 유효한 {len(good)}박 중 {len(filled)}박은 {why} 0으로 계산했습니다.")
        lines.append(f"      → TST와 수면효율이 그만큼 **높게** 나옵니다. "
                     f"{label}의 평균·CI에서는 이 밤들을 뺐습니다.")
    if bad:
        lines.append("")
        lines.append("  ● 제외된 밤 (계산 불가 — 집계에 넣지 않았습니다)")
        for night in bad[:max_show]:
            lines.append(f"      {night.row_no}행 [{night.subject}] "
                         + "; ".join(night.errors))
        if len(bad) > max_show:
            lines.append(f"      … 외 {len(bad) - max_show}건")
    if warned:
        lines.append("")
        lines.append("  ● 경고 (값이 이상하지만 **집계에는 포함**했습니다 — 직접 확인하세요)")
        for night in warned[:max_show]:
            lines.append(f"      {night.row_no}행 [{night.subject}] "
                         + "; ".join(night.warnings))
        if len(warned) > max_show:
            lines.append(f"      … 외 {len(warned) - max_show}건")
    lines.append("")
    return lines


def render_subjects(summaries: Sequence[SubjectSummary], show_period: bool) -> list[str]:
    lines = [THIN, "  대상자별 요약 (한 사람의 여러 밤을 평균한 값)", THIN]
    head = [_pad("대상자", 14)]
    if show_period:
        head.append(_pad("시기", 10))
    head += [_rpad("밤", 4), _rpad("제외", 5), _rpad("TST", 9), _rpad("SE%", 7),
             _rpad("SOL", 6), _rpad("WASO", 6), _rpad("중앙수면", 9), _rpad("규칙성", 8)]
    lines.append("  " + " ".join(head))
    for s in summaries:
        row = [_pad(str(s.subject)[:13], 14)]
        if show_period:
            row.append(_pad(str(s.period or "—")[:9], 10))
        tst = s.value("tst_min")
        row += [
            _rpad(str(s.n_nights), 4),
            _rpad(str(s.n_excluded), 5),
            _rpad(fmt_hm(tst) if tst is not None else "—", 9),
            _rpad(_num(s.value("se_pct")), 7),
            _rpad(_num(s.value("sol_min"), 0), 6),
            _rpad(_num(s.value("waso_min"), 0), 6),
            _rpad(fmt_clock(s.value("midsleep_min")) if s.value("midsleep_min") is not None else "—", 9),
            _rpad(_num(s.regularity, 0) + "분" if s.regularity is not None else "—", 8),
        ]
        lines.append("  " + " ".join(row))
    lines.append("")
    lines.append("  규칙성 = 그 사람의 수면중앙시각 원형 표준편차(분). 작을수록 규칙적.")
    lines.append("")
    return lines


def render_group(group: GroupSummary, conf: float) -> list[str]:
    title = "  집단 요약"
    if group.period:
        title += f" — 시기 '{group.period}'"
    lines = [THIN, title, THIN]
    lines.append(f"  대상자 {group.n_subjects}명 · 유효 {group.n_nights}박 "
                 f"(제외 {group.n_excluded}박)")
    lines.append(f"  ※ 아래 통계의 n은 **사람 수**입니다 (사람별 평균을 다시 평균).")
    lines.append("")
    pct = int(round(conf * 100))
    lines.append("  " + _pad("지표", 24) + _rpad("n", 4) + "  "
                 + _rpad("평균", 14) + "  " + _rpad("SD", 9) + "  "
                 + _rpad("중앙값", 12) + "  " + f"{pct}% CI (평균)")
    for key, label, _unit in LINEAR_METRICS:
        entry = group.metrics.get(key, {})
        if not entry.get("n"):
            continue
        ci = "—"
        if entry.get("ci_low") is not None:
            ci = f"[{entry['ci_low']:.1f}, {entry['ci_high']:.1f}]"
        lines.append("  " + _pad(label, 24)
                     + _rpad(str(entry["n"]), 4) + "  "
                     + _rpad(fmt_metric(key, entry.get("mean")), 14) + "  "
                     + _rpad(_num(entry.get("sd")), 9) + "  "
                     + _rpad(fmt_metric(key, entry.get("median")), 12) + "  " + ci)
    for key in CIRCULAR_KEYS:
        entry = group.metrics.get(key, {})
        if not entry.get("n"):
            continue
        lines.append("  " + _pad(METRIC_LABEL[key], 24)
                     + _rpad(str(entry["n"]), 4) + "  "
                     + _rpad(fmt_clock(entry["mean"]) if entry.get("mean") is not None else "—", 14)
                     + "  " + _rpad((_num(entry.get("sd"), 0) + "분")
                                    if entry.get("sd") is not None else "—", 9)
                     + "  " + _rpad("(원형평균)", 12))
    reg = group.regularity
    if reg.get("n"):
        lines.append("")
        lines.append(f"  수면중앙시각 규칙성 (사람별 원형SD) : 평균 {_num(reg['mean'], 1)}분, "
                     f"중앙값 {_num(reg['median'], 1)}분 (n={reg['n']}명)")
        lines.append("    한 사람만 취침시각이 크게 흔들려도 평균이 끌려가므로 중앙값을 함께 봅니다.")
    lines.append("")
    lines.append("  시각형 지표(소등·입면·최종기상·수면중앙시각)는 원형평균이라 신뢰구간을")
    lines.append("  계산하지 않습니다 — 선형 지표에만 CI가 붙습니다.")
    lines.append("")
    return lines


def render_comparison(comps: Sequence[PeriodComparison], conf: float) -> list[str]:
    if not comps:
        return []
    first = comps[0]
    lines = [THIN,
             f"  시기 비교 — '{first.period_a}' → '{first.period_b}' (대응표본, 차이 = 나중 − 먼저)",
             THIN]
    if first.n_pairs == 0:
        lines.append("  두 시기 모두 기록한 대상자가 없어 비교하지 않았습니다.")
        lines.append("")
        return lines
    if first.n_pairs < 3:
        lines.append(f"  ⚠ 짝 지어진 대상자가 {first.n_pairs}명뿐입니다 — "
                     "p값과 신뢰구간은 참고용으로만 보세요.")
    pct = int(round(conf * 100))
    for comp in comps:
        if comp.n_pairs == 0:
            continue
        lines.append("")
        lines.append(f"  ● {METRIC_LABEL.get(comp.metric, comp.metric)}  (짝 n={comp.n_pairs}명)")
        lines.append(f"      {comp.period_a}: {fmt_metric(comp.metric, comp.mean_a)}"
                     f"   →   {comp.period_b}: {fmt_metric(comp.metric, comp.mean_b)}")
        if comp.wrap_unstable:
            lines.append("      ⚠ 어떤 대상자의 변화가 6시간을 넘습니다. 시각의 차이는")
            lines.append("        ±12시간에서 감기므로 '6시간 앞당김'과 '18시간 늦춤'을")
            lines.append("        구분할 수 없어, 검정을 수행하지 않았습니다. 밤별 값을")
            lines.append("        직접 확인하세요 (--per-night-csv).")
            continue
        tt = comp.ttest
        if tt is None:
            lines.append("      (짝이 2명 미만이라 검정하지 않음)")
            continue
        ci = "—"
        if tt.ci_low is not None:
            ci = f"[{tt.ci_low:.1f}, {tt.ci_high:.1f}]"
        lines.append(f"      변화량 : {fmt_delta(comp.metric, tt.mean_diff)}   {pct}% CI {ci}")
        if tt.t is None:
            lines.append("      대응 t검정 : 모든 대상자의 변화량이 동일해 t를 계산할 수 없습니다")
        else:
            lines.append(f"      대응 t검정 : t({tt.df}) = {tt.t:.3f}, p {fmt_p_eq(tt.p)}"
                         f", Cohen's dz = {tt.dz:.3f}")
        w = comp.wilcoxon
        if w and w.p is not None:
            note = "정확분포" if w.method == "exact" else "정규근사(동점보정)"
            extra = f", r = {w.r:.3f}" if w.r is not None else ""
            lines.append(f"      Wilcoxon   : W = {w.statistic:.1f}, p {fmt_p_eq(w.p)}"
                         f"  [{note}, n={w.n_used}, 0인 차이 {w.n_zero}건 제외{extra}]")
        elif w:
            lines.append("      Wilcoxon   : 모든 차이가 0이라 검정 불가")
    lines.append("")
    lines.append("  ※ 다중비교 보정은 적용하지 않았습니다. 지표를 여러 개 검정했다면")
    lines.append("     주 지표를 사전에 정하거나 보정된 p를 별도로 계산하세요.")
    lines.append("")
    return lines


def render_paragraph(group: GroupSummary, comps: Sequence[PeriodComparison],
                     conf: float, primary: str = "se_pct") -> list[str]:
    """논문 결과 문단 초안 — 그대로 쓰지 말고 확인하라고 명시한다."""
    pct = int(round(conf * 100))
    lines = [THIN, "  논문용 문장 초안 (숫자를 반드시 직접 확인하고 고쳐 쓰세요)", THIN]
    tst = group.metrics.get("tst_min", {})
    se = group.metrics.get("se_pct", {})
    sol = group.metrics.get("sol_min", {})
    waso = group.metrics.get("waso_min", {})
    if not group.n_subjects:
        lines.append("  요약할 대상자가 없습니다.")
        lines.append("")
        return lines

    sent = (f"  대상자 {group.n_subjects}명이 총 {group.n_nights}박의 수면일기를 작성하였다"
            f"(1인당 평균 {group.n_nights / max(group.n_subjects, 1):.1f}박). ")
    if tst.get("mean") is not None:
        sent += (f"총수면시간은 평균 {tst['mean'] / 60:.2f}시간"
                 f"({tst['mean']:.0f}분, SD {_num(tst.get('sd'), 0)}분)이었고, ")
    if se.get("mean") is not None:
        sent += f"수면효율은 {se['mean']:.1f}%(SD {_num(se.get('sd'))})였다. "
    if sol.get("mean") is not None and waso.get("mean") is not None:
        sent += (f"입면잠복기는 {sol['mean']:.0f}분, 중도각성시간(WASO)은 "
                 f"{waso['mean']:.0f}분이었다. ")
    sent += ("모든 값은 각 대상자의 밤별 평균을 구한 뒤 대상자 사이에서 요약한 것이다"
             "(분석 단위 = 대상자).")
    lines += _wrap(sent)

    for comp in comps:
        if comp.metric != primary or comp.ttest is None or comp.ttest.t is None:
            continue
        tt = comp.ttest
        label = METRIC_LABEL.get(comp.metric, comp.metric)
        text = (f"  {label}은(는) {comp.period_a} 대비 {comp.period_b}에서 "
                f"{fmt_delta(comp.metric, tt.mean_diff)} 변화하였다 "
                f"({pct}% CI {tt.ci_low:.1f} ~ {tt.ci_high:.1f}; "
                f"대응표본 t검정 t({tt.df}) = {tt.t:.2f}, p {fmt_p_eq(tt.p)}, "
                f"Cohen's dz = {tt.dz:.2f}; n = {comp.n_pairs}명).")
        lines.append("")
        lines += _wrap(text)
    lines.append("")
    return lines


def _wrap(text: str, width: int = 70) -> list[str]:
    """한글 폭을 고려한 단순 줄바꿈 (앞쪽 들여쓰기는 유지)."""
    indent = text[:len(text) - len(text.lstrip(" "))]
    out, line = [], ""
    for token in text.strip().split(" "):
        candidate = (line + " " + token) if line else token
        if _width(candidate) > width and line:
            out.append(line)
            line = indent + "  " + token.lstrip()
        else:
            line = candidate or (indent + token)
        if not out and not line.startswith(indent):
            line = indent + line
    if line:
        out.append(line)
    return out


def render_report(meta: dict, nights: Sequence, summaries: Sequence[SubjectSummary],
                  groups: Sequence[GroupSummary], comps: Sequence[PeriodComparison],
                  conf: float, *, show_period: bool) -> str:
    lines: list[str] = []
    lines += render_header(meta)
    lines += render_quality(nights)
    lines += render_subjects(summaries, show_period)
    for group in groups:
        lines += render_group(group, conf)
    lines += render_comparison(comps, conf)
    primary_group = groups[0] if groups else GroupSummary(None)
    lines += render_paragraph(primary_group, comps, conf)
    lines.append(RULE)
    lines.append("  정의: TIB=잠자리든→나온 시각, SPT=소등→최종기상(관례적 SPT와 달리 SOL 포함),")
    lines.append("        TST=SPT−SOL−WASO, SE=TST/TIB×100,")
    lines.append("        수면중앙시각=입면~최종기상의 중점 (원형평균).")
    lines.append("        문항은 Consensus Sleep Diary(Carney et al., Sleep 2012)를 따랐고")
    lines.append("        파생지표 산식은 이 도구의 운용적 정의입니다.")
    lines.append("  주의: 수면일기는 자기보고입니다. 실제 수면다원검사(PSG)나 액티그래피와는")
    lines.append("        체계적으로 다를 수 있으며, 이 도구는 그 차이를 보정하지 않습니다.")
    lines.append("        결측 SOL/WASO는 0으로 계산되어 TST·SE를 높입니다(위 '자료 품질' 참조).")
    lines.append("        낮잠은 포함되지 않으며, 시기 비교는 두 시기를 모두 기록한 대상자만")
    lines.append("        쓰는 완전자료 분석입니다 (ITT 아님).")
    lines.append(RULE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 마크다운
# ---------------------------------------------------------------------------

def render_markdown(meta: dict, nights: Sequence, summaries: Sequence[SubjectSummary],
                    groups: Sequence[GroupSummary], comps: Sequence[PeriodComparison],
                    conf: float) -> str:
    pct = int(round(conf * 100))
    out = ["# 수면일기 지표 요약", "",
           f"- 입력: `{meta['path']}` ({meta['encoding']}, {meta['n_rows']}행)",
           f"- 유효 {sum(1 for n in nights if n.valid)}박 / 전체 {len(nights)}박", ""]
    for group in groups:
        title = f"## 집단 요약" + (f" — {group.period}" if group.period else "")
        out += [title, "",
                f"대상자 {group.n_subjects}명, 유효 {group.n_nights}박 (분석단위 = 대상자)", "",
                f"| 지표 | n(명) | 평균 | SD | 중앙값 | {pct}% CI |",
                "|---|---:|---:|---:|---:|---|"]
        for key in ALL_KEYS:
            entry = group.metrics.get(key, {})
            if not entry.get("n"):
                continue
            ci = "—"
            if entry.get("ci_low") is not None:
                ci = f"[{entry['ci_low']:.1f}, {entry['ci_high']:.1f}]"
            out.append(f"| {METRIC_LABEL[key]} | {entry['n']} | "
                       f"{fmt_metric(key, entry.get('mean'))} | {_num(entry.get('sd'))} | "
                       f"{fmt_metric(key, entry.get('median')) if key not in CIRCULAR_KEYS else '—'} | {ci} |")
        out.append("")
    if comps and comps[0].n_pairs:
        out += [f"## 시기 비교 — {comps[0].period_a} → {comps[0].period_b}", "",
                f"| 지표 | 짝 n | 먼저 | 나중 | 변화 | {pct}% CI | t검정 p | Wilcoxon p |",
                "|---|---:|---:|---:|---:|---|---:|---:|"]
        for comp in comps:
            if not comp.n_pairs or comp.ttest is None:
                continue
            if comp.wrap_unstable:
                continue
            tt = comp.ttest
            ci = f"[{tt.ci_low:.1f}, {tt.ci_high:.1f}]" if tt.ci_low is not None else "—"
            wp = fmt_p(comp.wilcoxon.p) if comp.wilcoxon else "—"
            out.append(f"| {METRIC_LABEL.get(comp.metric, comp.metric)} | {comp.n_pairs} | "
                       f"{fmt_metric(comp.metric, comp.mean_a)} | "
                       f"{fmt_metric(comp.metric, comp.mean_b)} | "
                       f"{fmt_delta(comp.metric, tt.mean_diff)} | {ci} | "
                       f"{fmt_p(tt.p)} | {wp} |")
        out += ["", "다중비교 보정 없음. 자기보고 자료이며 PSG와 다를 수 있습니다. "
            "결측 SOL/WASO는 0으로 계산했습니다. 낮잠 미포함. "
            "두 시기를 모두 기록한 대상자만 비교(완전자료 분석).", ""]
    return "\n".join(out)
