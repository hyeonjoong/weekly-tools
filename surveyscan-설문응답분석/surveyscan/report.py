"""분석 결과를 사람이 읽는 텍스트 리포트로 렌더링.

알파 해석 기준(통상): >=.9 우수, >=.8 양호, >=.7 수용가능, >=.6 의심, <.6 낮음.
"""
from __future__ import annotations

import math
import unicodedata
from typing import Dict, Optional


def _fmt(x: Optional[float], nd: int = 2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "  -  "
    return f"{x:.{nd}f}"


def _num(x: Optional[float]) -> str:
    """정수면 정수로, 아니면 소수로 표기(척도 범위 등 사람이 읽는 숫자용)."""
    if x is None:
        return "-"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _ci(pair, nd: int = 2) -> str:
    """[lo, hi] 를 '[lo, hi]' 문자열로. None이면 빈 문자열."""
    if not pair:
        return ""
    return f"[{pair[0]:.{nd}f}, {pair[1]:.{nd}f}]"


def _oneline(s: str) -> str:
    """이름 안의 개행·탭을 공백으로 치환(표 구조가 깨지지 않게)."""
    return str(s).replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")


def _dwidth(s: str) -> int:
    """문자열의 터미널 표시 폭(한글 등 동아시아 폭 문자는 2칸으로 계산)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int, align: str = "left") -> str:
    """표시 폭 기준 패딩(한글 컬럼명이 들어가도 표 정렬이 깨지지 않게)."""
    s = _oneline(s)
    gap = max(0, width - _dwidth(s))
    if align == "right":
        return " " * gap + s
    return s + " " * gap


def alpha_label(a: Optional[float]) -> str:
    if a is None or (isinstance(a, float) and not math.isfinite(a)):
        return "계산불가"
    if a >= 0.9:
        return "우수"
    if a >= 0.8:
        return "양호"
    if a >= 0.7:
        return "수용가능"
    if a >= 0.6:
        return "의심"
    return "낮음"


def render(result: Dict[str, object]) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append("  설문 응답 분석 리포트 (surveyscan)")
    lines.append("=" * 64)
    lines.append(f"  응답자 수 : {result['n_respondents']}")
    lines.append(f"  문항 수   : {result['n_items']}")
    rev = result["reverse_items"]
    if rev:
        lines.append(f"  역문항    : {', '.join(rev)} (재코딩 적용됨)")
    if result["scale_min"] is not None:
        lines.append(f"  척도 범위 : {_num(result['scale_min'])} ~ {_num(result['scale_max'])}")
    method = result.get("score_method", "mean")
    lines.append(f"  점수 방식 : {'총합(sum)' if method == 'sum' else '평균(mean)'}")
    conf = result.get("conf_level", 0.95)
    lines.append(f"  신뢰수준  : {int(round(conf * 100))}% CI")
    lines.append("")

    # 범위 이탈(입력 오류 가능) 경고
    oor = result.get("out_of_range") or []
    if oor:
        lines.append("[ ⚠ 척도 범위를 벗어난 값 (입력 오류 점검) ]")
        for o in oor:
            ex = ", ".join(str(v) for v in o["examples"])
            lines.append(f"  {o['item']}: {o['count']}개 (예: {ex})")
        lines.append("")

    # 결측 요약
    m = result["missing"]
    lines.append("[ 결측 요약 ]")
    lines.append(
        f"  전체 셀 {m['total_cells']}개 중 결측 {m['missing_cells']}개 "
        f"({m['missing_pct']}%)"
    )
    lines.append(
        f"  완전응답자(모든 문항 응답) {m['complete_respondents']}명 "
        f"({m['complete_pct']}%)"
    )
    lines.append("")

    # 문항별 기술통계
    lines.append("[ 문항별 기술통계 ]")
    # 문항명이 한글이어도 정렬이 유지되도록 표시폭 기준 패딩(_pad) 사용.
    iw = max([_dwidth(str(d["item"])) for d in result["descriptives"]] + [_dwidth("문항")])
    lines.append(
        f"  {_pad('문항', iw)} {_pad('N', 4, 'right')} {_pad('결측', 8, 'right')} "
        f"{_pad('평균', 7, 'right')} {_pad('표준편차', 8, 'right')} "
        f"{_pad('중앙', 6, 'right')} {_pad('최소', 5, 'right')} {_pad('최대', 5, 'right')} "
        f"{_pad('왜도', 6, 'right')} {_pad('첨도', 6, 'right')}"
    )
    lines.append("  " + "-" * (iw + 62))
    for d in result["descriptives"]:
        miss = f"{d['n_missing']}({d['missing_pct']:g}%)"
        lines.append(
            f"  {_pad(d['item'], iw)} {_pad(d['n'], 4, 'right')} {_pad(miss, 8, 'right')} "
            f"{_pad(_fmt(d['mean']), 7, 'right')} {_pad(_fmt(d['sd']), 8, 'right')} "
            f"{_pad(_fmt(d['median'],1), 6, 'right')} {_pad(_fmt(d['min'],1), 5, 'right')} "
            f"{_pad(_fmt(d['max'],1), 5, 'right')} {_pad(_fmt(d.get('skew')), 6, 'right')} "
            f"{_pad(_fmt(d.get('kurtosis')), 6, 'right')}"
        )
    lines.append("")

    # 문항별 응답 선택지 빈도(옵션)
    freq = result.get("item_freq")
    if freq:
        lines.append("[ 문항별 응답 선택지 빈도 ]")
        levels = freq["levels"]
        fiw = max([_dwidth(str(r["item"])) for r in freq["items"]] + [_dwidth("문항")])
        head = f"  {_pad('문항', fiw)}"
        for lv in levels:
            head += f" {_pad(str(lv), 6, 'right')}"
        head += f" {_pad('기타', 6, 'right')}"
        lines.append(head)
        lines.append("  " + "-" * (fiw + 7 * (len(levels) + 1)))
        for r in freq["items"]:
            line = f"  {_pad(r['item'], fiw)}"
            n = r["n"]
            for lv in levels:
                c = r["counts"][lv]
                cell = f"{c}({100.0*c/n:.0f}%)" if n else "0"
                line += f" {_pad(cell, 6, 'right')}"
            oc = r["other"]
            ocell = f"{oc}" if oc else "-"
            line += f" {_pad(ocell, 6, 'right')}"
            lines.append(line)
        lines.append("      (칸 = 응답수(해당문항 응답자 대비 %); '기타'=비정수/범위밖)")
        lines.append("")

    # 하위척도별 신뢰도
    lines.append("[ 하위척도별 신뢰도 · 점수 ]")
    for s in result["subscales"]:
        lines.append("")
        lines.append(f"  ▶ {_oneline(s['name'])}  (문항 {s['n_items']}개)")
        a = s["alpha"]
        ci_txt = ""
        if s.get("alpha_ci"):
            ci_txt = f"  {int(round(result.get('conf_level', 0.95)*100))}% CI {_ci(s['alpha_ci'], 3)}"
        lines.append(
            f"     Cronbach α = {_fmt(a, 3)}  [{alpha_label(a)}]{ci_txt}"
            f"   (완전응답 {s['n_complete']}명; "
            f"listwise 제외 {s['n_excluded_listwise']}명)"
        )
        extras = []
        if s.get("sem") is not None:
            extras.append(f"SEM {_fmt(s['sem'], 3)}")
        if s.get("mdc95") is not None:
            extras.append(f"MDC₉₅ {_fmt(s['mdc95'], 3)}")
        if s.get("alpha_std") is not None:
            extras.append(f"표준화 α {_fmt(s['alpha_std'], 3)}")
        if extras:
            lines.append("     " + "   ".join(extras))
        mii = s.get("mean_inter_item_r")
        if mii is not None:
            iiflag = ""
            if mii > 0.70:
                iiflag = "  ⚠ 문항 중복 의심(>.70)"
            elif mii < 0.15:
                iiflag = "  ⚠ 이질적 구성 의심(<.15)"
            lines.append(f"     평균 문항간 r {_fmt(mii, 3)} (범위 {_fmt(s.get('min_inter_item_r'),2)}~{_fmt(s.get('max_inter_item_r'),2)}){iiflag}")
        method_lbl = "총합" if s.get("score_method") == "sum" else "평균"
        score_ci_txt = f"  {int(round(result.get('conf_level', 0.95)*100))}% CI {_ci(s['score_ci'])}" if s.get("score_ci") else ""
        lines.append(
            f"     하위척도 점수({method_lbl}): {_fmt(s['score_mean'])} "
            f"± {_fmt(s['score_sd'])}{score_ci_txt}  (점수산출 {s['n_scored']}명)"
        )
        # 바닥/천장 효과(척도 범위 선언 시). 15% 초과면 주의 플래그.
        fl, ce = s.get("floor"), s.get("ceiling")
        if fl is not None and ce is not None:
            fflag = "  ⚠" if fl["pct"] > 15.0 else ""
            cflag = "  ⚠" if ce["pct"] > 15.0 else ""
            lines.append(
                f"     바닥효과 {fl['n']}명({fl['pct']:g}%, ={_num(s['possible_min'])}){fflag}"
                f"   천장효과 {ce['n']}명({ce['pct']:g}%, ={_num(s['possible_max'])}){cflag}"
            )
        no_data = s.get("items_no_data") or []
        if no_data:
            lines.append(
                f"     ⚠ 전부 결측인 문항 {len(no_data)}개({', '.join(no_data)})는 "
                f"점수에 기여하지 못함 — 실제로는 더 적은 문항으로 계산됨."
            )
        if s["n_items"] >= 2 and s["alpha"] is not None:
            iw2 = max([_dwidth(str(it)) for it in s["items"]] + [_dwidth("문항")])
            lines.append(
                f"     {_pad('문항', iw2)} {_pad('문항-총점 r', 12, 'right')} "
                f"{_pad('α(문항제거시)', 14, 'right')}"
            )
            lines.append("     " + "-" * (iw2 + 28))
            for it in s["items"]:
                itc = s["item_total_corr"].get(it)
                aid = s["alpha_if_deleted"].get(it)
                # 우선순위: 음수 r(역코딩 오류 신호) > 제거시 α↑ > 낮은 r.
                flag = ""
                if itc is not None and itc < 0:
                    flag = "  ← 음수 r(역코딩 확인)"
                elif aid is not None and a is not None and aid > a + 1e-9:
                    flag = "  ← 제거시 α↑(검토)"
                elif itc is not None and itc < 0.3:
                    flag = "  ← 낮음(검토)"
                lines.append(
                    f"     {_pad(it, iw2)} {_pad(_fmt(itc, 3), 12, 'right')} "
                    f"{_pad(_fmt(aid, 3), 14, 'right')}{flag}"
                )
    lines.append("")
    lines.append("  주: α 해석 — .9우수/.8양호/.7수용/.6의심/<.6낮음.")
    lines.append("      '문항-총점 r'은 수정된 상관(해당 문항 제외 합과의 상관).")
    lines.append("      r<.30 이거나 '제거시 α↑'이면 문항 적합성 재검토 권장 — 단, 문항 제거는")
    lines.append("      척도 '개발' 단계용. 검증된 표준척도(ISI/PHQ-9 등)는 문항을 빼지 마세요.")
    lines.append("      SEM·MDC₉₅ 는 완전응답자(listwise) 총점 SD와 α 로 계산(점수 지표 단위).")
    lines.append("      MDC₉₅=1.96·√2·SEM: 이 값 이상 변해야 측정오차를 넘는 실질적 변화.")
    lines.append("      α·문항-총점 r 은 '완전응답자(listwise)' N 기준, 하위척도 점수는")
    lines.append("      가용문항(min_valid_ratio 충족) N 기준 — 두 N이 다를 수 있음.")
    lines.append("=" * 64)
    return "\n".join(lines)


def _mdcell(x) -> str:
    """마크다운 표 셀 값에서 파이프(|)를 이스케이프하고 개행을 공백으로 치환."""
    return _oneline(x).replace("|", "\\|")


def render_markdown(result: Dict[str, object]) -> str:
    """분석 결과를 Markdown 으로 렌더링(논문 초안·GitHub·노션에 붙여넣기 용)."""
    conf_pct = int(round(result.get("conf_level", 0.95) * 100))
    L = []
    L.append("# 설문 응답 분석 리포트 (surveyscan)")
    L.append("")
    L.append(f"- 응답자 수: **{result['n_respondents']}**")
    L.append(f"- 문항 수: **{result['n_items']}**")
    if result["reverse_items"]:
        L.append(f"- 역문항(재코딩): {', '.join(_mdcell(r) for r in result['reverse_items'])}")
    if result["scale_min"] is not None:
        L.append(f"- 척도 범위: {_num(result['scale_min'])} ~ {_num(result['scale_max'])}")
    method = result.get("score_method", "mean")
    L.append(f"- 점수 방식: {'총합(sum)' if method == 'sum' else '평균(mean)'}")
    L.append(f"- 신뢰수준: {conf_pct}% CI")
    L.append("")

    oor = result.get("out_of_range") or []
    if oor:
        L.append("## ⚠ 척도 범위를 벗어난 값")
        L.append("")
        L.append("| 문항 | 개수 | 예시 |")
        L.append("|---|---:|---|")
        for o in oor:
            ex = ", ".join(str(v) for v in o["examples"])
            L.append(f"| {_mdcell(o['item'])} | {o['count']} | {_mdcell(ex)} |")
        L.append("")

    m = result["missing"]
    L.append("## 결측 요약")
    L.append("")
    L.append(f"- 전체 셀 {m['total_cells']}개 중 결측 {m['missing_cells']}개 ({m['missing_pct']}%)")
    L.append(f"- 완전응답자 {m['complete_respondents']}명 ({m['complete_pct']}%)")
    L.append("")

    L.append("## 문항별 기술통계")
    L.append("")
    L.append("| 문항 | N | 결측 | 평균 | 표준편차 | 중앙 | Q1 | Q3 | 최소 | 최대 | 왜도 | 첨도 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for d in result["descriptives"]:
        miss = f"{d['n_missing']} ({d['missing_pct']:g}%)"
        L.append(
            f"| {_mdcell(d['item'])} | {d['n']} | {miss} | {_fmt(d['mean'])} | {_fmt(d['sd'])} "
            f"| {_fmt(d['median'],1)} | {_fmt(d.get('q1'),1)} | {_fmt(d.get('q3'),1)} "
            f"| {_fmt(d['min'],1)} | {_fmt(d['max'],1)} | {_fmt(d.get('skew'))} | {_fmt(d.get('kurtosis'))} |"
        )
    L.append("")

    freq = result.get("item_freq")
    if freq:
        L.append("## 문항별 응답 선택지 빈도")
        L.append("")
        levels = freq["levels"]
        L.append("| 문항 | " + " | ".join(str(lv) for lv in levels) + " | 기타 |")
        L.append("|---|" + "---:|" * (len(levels) + 1))
        for r in freq["items"]:
            n = r["n"]
            cells = []
            for lv in levels:
                c = r["counts"][lv]
                cells.append(f"{c} ({100.0*c/n:.0f}%)" if n else "0")
            oc = r["other"]
            L.append(f"| {_mdcell(r['item'])} | " + " | ".join(cells) + f" | {oc if oc else '-'} |")
        L.append("")

    L.append("## 하위척도별 신뢰도 · 점수")
    L.append("")
    L.append(
        f"| 하위척도 | 문항수 | Cronbach α | {conf_pct}% CI | 해석 | 표준화 α | SEM | MDC₉₅ "
        f"| 평균 문항간 r | 점수 평균±SD | {conf_pct}% CI | 바닥% | 천장% | 완전응답N | 점수산출N |"
    )
    L.append("|---|---:|---:|---|---|---:|---:|---:|---:|---|---|---:|---:|---:|---:|")
    for s in result["subscales"]:
        a = s["alpha"]
        fl = s.get("floor")
        ce = s.get("ceiling")
        floor_txt = f"{fl['pct']:g}" if fl else "-"
        ceil_txt = f"{ce['pct']:g}" if ce else "-"
        score_txt = f"{_fmt(s['score_mean'])} ± {_fmt(s['score_sd'])}"
        L.append(
            f"| {_mdcell(s['name'])} | {s['n_items']} | {_fmt(a, 3)} | {_ci(s.get('alpha_ci'),3) or '-'} "
            f"| {alpha_label(a)} | {_fmt(s.get('alpha_std'),3)} | {_fmt(s.get('sem'),3)} | {_fmt(s.get('mdc95'),3)} "
            f"| {_fmt(s.get('mean_inter_item_r'),3)} "
            f"| {score_txt} | {_ci(s.get('score_ci')) or '-'} | {floor_txt} | {ceil_txt} "
            f"| {s['n_complete']} | {s['n_scored']} |"
        )
    L.append("")

    for s in result["subscales"]:
        if not (s["n_items"] >= 2 and s["alpha"] is not None):
            continue
        L.append(f"### {_oneline(s['name'])} — 문항 진단")
        L.append("")
        L.append("| 문항 | 문항-총점 r | α(문항제거시) | 비고 |")
        L.append("|---|---:|---:|---|")
        a = s["alpha"]
        for it in s["items"]:
            itc = s["item_total_corr"].get(it)
            aid = s["alpha_if_deleted"].get(it)
            note = ""
            if itc is not None and itc < 0:
                note = "음수 r(역코딩 확인)"
            elif aid is not None and a is not None and aid > a + 1e-9:
                note = "제거시 α↑(검토)"
            elif itc is not None and itc < 0.3:
                note = "낮음(검토)"
            L.append(f"| {_mdcell(it)} | {_fmt(itc,3)} | {_fmt(aid,3)} | {note} |")
        no_data = s.get("items_no_data") or []
        if no_data:
            L.append("")
            L.append(f"> ⚠ 전부 결측인 문항: {', '.join(_mdcell(x) for x in no_data)}")
        L.append("")

    L.append("---")
    L.append("*α 해석: .9 우수 / .8 양호 / .7 수용 / .6 의심 / <.6 낮음. "
             "r<.30 또는 '제거시 α↑'이면 문항 재검토(단, 문항 제거는 척도 개발 단계용 — "
             "ISI/PHQ-9 등 검증된 척도는 문항을 빼지 마세요). "
             "SEM·MDC₉₅(=1.96·√2·SEM)는 listwise 총점 SD와 α로 계산. "
             "α·문항-총점 r은 listwise N, 점수는 가용문항 N 기준.*")
    return "\n".join(L)
