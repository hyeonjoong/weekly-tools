"""분석 결과를 사람이 읽는 텍스트 리포트로 렌더링.

알파 해석 기준(통상): >=.9 우수, >=.8 양호, >=.7 수용가능, >=.6 의심, <.6 낮음.
"""
from __future__ import annotations

import unicodedata
from typing import Dict, Optional


def _fmt(x: Optional[float], nd: int = 2) -> str:
    if x is None:
        return "  -  "
    return f"{x:.{nd}f}"


def _dwidth(s: str) -> int:
    """문자열의 터미널 표시 폭(한글 등 동아시아 폭 문자는 2칸으로 계산)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int, align: str = "left") -> str:
    """표시 폭 기준 패딩(한글 컬럼명이 들어가도 표 정렬이 깨지지 않게)."""
    s = str(s)
    gap = max(0, width - _dwidth(s))
    if align == "right":
        return " " * gap + s
    return s + " " * gap


def alpha_label(a: Optional[float]) -> str:
    if a is None:
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
        lines.append(f"  척도 범위 : {result['scale_min']} ~ {result['scale_max']}")
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
        f"{_pad('중앙', 6, 'right')} {_pad('최소', 5, 'right')} {_pad('최대', 5, 'right')}"
    )
    lines.append("  " + "-" * (iw + 48))
    for d in result["descriptives"]:
        miss = f"{d['n_missing']}({d['missing_pct']:g}%)"
        lines.append(
            f"  {_pad(d['item'], iw)} {_pad(d['n'], 4, 'right')} {_pad(miss, 8, 'right')} "
            f"{_pad(_fmt(d['mean']), 7, 'right')} {_pad(_fmt(d['sd']), 8, 'right')} "
            f"{_pad(_fmt(d['median'],1), 6, 'right')} {_pad(_fmt(d['min'],1), 5, 'right')} "
            f"{_pad(_fmt(d['max'],1), 5, 'right')}"
        )
    lines.append("")

    # 하위척도별 신뢰도
    lines.append("[ 하위척도별 신뢰도 · 점수 ]")
    for s in result["subscales"]:
        lines.append("")
        lines.append(f"  ▶ {s['name']}  (문항 {s['n_items']}개)")
        a = s["alpha"]
        lines.append(
            f"     Cronbach α = {_fmt(a, 3)}  [{alpha_label(a)}]"
            f"   (완전응답 {s['n_complete']}명 기준; "
            f"listwise 제외 {s['n_excluded_listwise']}명)"
        )
        lines.append(
            f"     하위척도 점수: 평균 {_fmt(s['score_mean'])} "
            f"± {_fmt(s['score_sd'])}  (점수산출 {s['n_scored']}명)"
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
                flag = ""
                if itc is not None and itc < 0.3:
                    flag = "  ← 낮음(검토)"
                if aid is not None and a is not None and aid > a + 1e-9:
                    flag = "  ← 제거시 α↑(검토)"
                lines.append(
                    f"     {_pad(it, iw2)} {_pad(_fmt(itc, 3), 12, 'right')} "
                    f"{_pad(_fmt(aid, 3), 14, 'right')}{flag}"
                )
    lines.append("")
    lines.append("  주: α 해석 — .9우수/.8양호/.7수용/.6의심/<.6낮음.")
    lines.append("      '문항-총점 r'은 수정된 상관(해당 문항 제외 합과의 상관).")
    lines.append("      r<.30 이거나 '제거시 α↑'이면 문항 적합성 재검토 권장.")
    lines.append("      α·문항-총점 r 은 '완전응답자(listwise)' N 기준, 하위척도 점수는")
    lines.append("      가용문항(min_valid_ratio 충족) N 기준 — 두 N이 다를 수 있음.")
    lines.append("=" * 64)
    return "\n".join(lines)
