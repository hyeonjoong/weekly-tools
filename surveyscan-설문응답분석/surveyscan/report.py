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


def _id_text(ids) -> str:
    """응답자 ID dict 를 표 한 칸에 넣을 문자열로(여러 ID 컬럼이면 ' / ' 결합)."""
    if not ids:
        return ""
    return " / ".join(str(v) for v in ids.values())


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

    # 숫자로 읽지 못한 값 — '빈칸(무응답)'과 전혀 다른 문제라 반드시 분리해서 알린다.
    unread = result.get("unreadable") or []
    if unread:
        total = sum(int(u["count"]) for u in unread)
        lines.append("[ ⚠ 숫자로 읽지 못해 결측 처리된 값 ]")
        for u in unread[:20]:
            ex = ", ".join(repr(str(v)) for v in u["examples"])
            lines.append(f"  {_oneline(u['item'])}: {u['count']}개 (예: {ex})")
        if len(unread) > 20:
            lines.append(f"  … 외 {len(unread) - 20}개 문항")
        lines.append(
            f"  → 빈칸이 아니라 '값은 있는데 못 읽은' 셀 {total}개입니다. 무응답이 아니므로"
        )
        lines.append("    결측률·N·α가 왜곡됩니다. 흔한 원인: 텍스트 선택지('매우그렇다'),")
        lines.append("    소수점 콤마('3,5'), 단위('3점'), 엑셀 아포스트로피('3), 보이지 않는 공백.")
        lines.append("    원자료를 숫자로 바꾸거나, 결측 코드라면 --na-number 로 지정하세요.")
        lines.append("")

    # 분석 문항에 응답이 하나도 없는 행(설문 플랫폼의 메타데이터 행일 가능성)
    empty_rows = result.get("empty_rows") or []
    if empty_rows:
        shown = ", ".join(str(r) for r in empty_rows[:20])
        more = f" 외 {len(empty_rows) - 20}줄" if len(empty_rows) > 20 else ""
        lines.append("[ ⚠ 모든 문항이 무응답인 행 ]")
        lines.append(f"  {len(empty_rows)}줄 (원본 CSV {shown}{more}행)")
        lines.append("  → 이 행들도 '응답자'로 세어 N과 결측률이 부풀려져 있습니다.")
        lines.append("    Qualtrics·구글폼이 헤더 아래 남기는 문항문구/ImportId 행일 수 있으니")
        lines.append("    원자료에서 지운 뒤 다시 실행하세요.")
        lines.append("")

    # 엑셀이 중간에 남긴 빈 줄 — 건너뛰었음을 알려 원본 행번호 대조가 가능하게 한다.
    skipped = result.get("skipped_blank_lines") or []
    if skipped:
        shown = ", ".join(str(r) for r in skipped[:20])
        more = f" 외 {len(skipped) - 20}줄" if len(skipped) > 20 else ""
        lines.append(
            f"  참고: 완전히 빈 줄 {len(skipped)}개를 건너뛰었습니다(원본 CSV {shown}{more}행). "
        )
        lines.append(
            "        --scores-out 의 '원본CSV행' 열로 원자료와 대조하세요."
        )
        lines.append("")

    # 중복 ID(이중입력·병합오류) 경고
    dups = result.get("duplicate_ids") or []
    if dups:
        n_dup_rows = sum(int(d["count"]) for d in dups)
        lines.append("[ ⚠ 중복된 ID (이중입력·병합오류 점검) ]")
        for d in dups[:20]:
            rows_txt = ", ".join(str(r) for r in d["rows"])
            lines.append(f"  {_oneline(d['id'])}: {d['count']}회 (데이터 행 {rows_txt})")
        if len(dups) > 20:
            lines.append(f"  … 외 {len(dups) - 20}개 ID")
        lines.append(
            f"  → 중복 ID {len(dups)}개 / 해당 행 {n_dup_rows}개. 같은 응답자가 두 번 들어가면"
        )
        lines.append("    N이 부풀려지고 신뢰도·상관이 왜곡됩니다. 원자료에서 확인하세요.")
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
        if a is not None and a < 0:
            lines.append(
                "     ⚠ α가 음수입니다 — 평균 문항간 상관이 음수라는 뜻으로, 거의 항상"
            )
            lines.append(
                "       역문항 재코딩 누락(reverse_items)이 원인입니다. 신뢰도로 해석하지"
            )
            lines.append(
                "       마세요. SEM·MDC₉₅ 는 정의되지 않아 '-' 로 표기합니다."
            )
        extras = []
        if s.get("omega") is not None:
            hey = "  ⚠ Heywood(모형 부적합 — ω 해석 주의)" if s.get("omega_heywood") else ""
            extras.append(f"McDonald ω {_fmt(s['omega'], 3)}{hey}")
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
    # 응답 품질(부주의응답) 선별 — --quality 지정 시에만
    q = result.get("quality")
    if q:
        lines.append("")
        lines.append("[ 응답 품질 선별 (부주의응답 screening) ]")
        lines.append(
            f"  응답자 {q['n_respondents']}명 중 플래그 {q['n_flagged']}명"
            f"  (straightlining {q['n_straightline']}명, 결측>50% {q['n_high_missing']}명)"
        )
        lines.append(
            f"  longstring 기준 ≥{q['longstring_min']} (최대 {q['max_longstring']}, "
            f"중앙 {_fmt(q['median_longstring'], 1)})   IRV 중앙 {_fmt(q['median_irv'], 3)}"
        )
        flagged = [r for r in q["respondents"] if r["flagged"]]
        if flagged:
            has_ids = any(r["ids"] for r in flagged)
            idw = 0
            if has_ids:
                idw = max(
                    [_dwidth(_id_text(r["ids"])) for r in flagged] + [_dwidth("ID")]
                )
            head = f"  {_pad('행', 5, 'right')}"
            if has_ids:
                head += f" {_pad('ID', idw)}"
            head += (
                f" {_pad('longstring', 10, 'right')} {_pad('IRV', 7, 'right')}"
                f" {_pad('결측', 8, 'right')} 사유"
            )
            lines.append(head)
            lines.append("  " + "-" * (idw + 46))
            for r in flagged[:30]:
                why = []
                if r["straightline"]:
                    why.append("전부 동일값")
                if r["long_run"]:
                    why.append("연속 동일값")
                miss_txt = "{}({:g}%)".format(r["n_missing"], r["missing_pct"])
                line = f"  {_pad(r['row'], 5, 'right')}"
                if has_ids:
                    line += f" {_pad(_id_text(r['ids']), idw)}"
                line += (
                    f" {_pad(r['longstring'], 10, 'right')} {_pad(_fmt(r['irv'], 3), 7, 'right')}"
                    f" {_pad(miss_txt, 8, 'right')} " + ", ".join(why)
                )
                lines.append(line)
            if len(flagged) > 30:
                lines.append(f"  … 외 {len(flagged) - 30}명")
        lines.append(
            "      ※ 자동 제외 기준이 아닙니다. 단방향 임상척도(ISI·PHQ-9 등)에서 '모두 0'은"
        )
        lines.append(
            "        증상 없음의 실제 응답일 수 있습니다. 원자료를 눈으로 확인할 대상을"
        )
        lines.append(
            "        좁히는 선별 도구로만 쓰세요(Meade & Craig 2012; Curran 2016)."
        )

    # 하위척도 간 상관(변별타당도)
    sc = result.get("subscale_corr")
    if sc and sc.get("pairs"):
        lines.append("")
        lines.append("[ 하위척도 간 상관 (변별타당도) ]")
        aw = max([_dwidth(str(p["a"])) for p in sc["pairs"]] + [_dwidth("하위척도 A")])
        bw = max([_dwidth(str(p["b"])) for p in sc["pairs"]] + [_dwidth("하위척도 B")])
        lines.append(
            f"  {_pad('하위척도 A', aw)} {_pad('하위척도 B', bw)} "
            f"{_pad('r', 7, 'right')} {_pad('N', 5, 'right')}"
        )
        lines.append("  " + "-" * (aw + bw + 15))
        for p in sc["pairs"]:
            r = p["r"]
            flag = ""
            if r is not None and abs(r) > 0.85:
                flag = "  ⚠ 매우 높음(>.85) — 별개 구성개념인지 검토"
            lines.append(
                f"  {_pad(p['a'], aw)} {_pad(p['b'], bw)} "
                f"{_pad(_fmt(r, 3), 7, 'right')} {_pad(p['n'], 5, 'right')}{flag}"
            )
        lines.append("      (쌍마다 두 점수가 모두 있는 응답자만 사용 — N이 쌍마다 다를 수 있음)")

    lines.append("")
    lines.append("  주: α 해석 — .9우수/.8양호/.7수용/.6의심/<.6낮음.")
    lines.append("      '문항-총점 r'은 수정된 상관(해당 문항 제외 합과의 상관).")
    lines.append("      r<.30 이거나 '제거시 α↑'이면 문항 적합성 재검토 권장 — 단, 문항 제거는")
    lines.append("      척도 '개발' 단계용. 검증된 표준척도(ISI/PHQ-9 등)는 문항을 빼지 마세요.")
    lines.append("      McDonald ω(단일요인 congeneric)는 α의 타우동등성 가정을 완화한 신뢰도로,")
    lines.append("      α와 함께 보고하도록 권고됨(Revelle & Zinbarg 2009; Hayes & Coutts 2020).")
    lines.append("      ω는 문항 3개 이상·요인 적합 수렴 시에만 표기('-'는 산출불가).")
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

    unread = result.get("unreadable") or []
    if unread:
        total = sum(int(u["count"]) for u in unread)
        L.append("## ⚠ 숫자로 읽지 못해 결측 처리된 값")
        L.append("")
        L.append("| 문항 | 개수 | 예시 |")
        L.append("|---|---:|---|")
        for u in unread[:20]:
            ex = ", ".join(f"`{v}`" for v in u["examples"])
            L.append(f"| {_mdcell(u['item'])} | {u['count']} | {_mdcell(ex)} |")
        L.append("")
        L.append(
            f"> 빈칸이 아니라 '값은 있는데 못 읽은' 셀 {total}개 — 무응답이 아니므로 "
            "결측률·N·α가 왜곡됩니다. 흔한 원인: 텍스트 선택지, 소수점 콤마(`3,5`), "
            "단위(`3점`), 엑셀 아포스트로피, 보이지 않는 공백. "
            "결측 코드라면 `--na-number` 로 지정하세요."
        )
        L.append("")

    empty_rows = result.get("empty_rows") or []
    if empty_rows:
        shown = ", ".join(str(r) for r in empty_rows[:20])
        more = f" 외 {len(empty_rows) - 20}줄" if len(empty_rows) > 20 else ""
        L.append("## ⚠ 모든 문항이 무응답인 행")
        L.append("")
        L.append(f"- {len(empty_rows)}줄 (원본 CSV {shown}{more}행)")
        L.append("")
        L.append(
            "> 이 행들도 '응답자'로 세어 N과 결측률이 부풀려져 있습니다. "
            "Qualtrics·구글폼의 문항문구/ImportId 행일 수 있으니 원자료에서 지우고 다시 실행하세요."
        )
        L.append("")

    skipped = result.get("skipped_blank_lines") or []
    if skipped:
        shown = ", ".join(str(r) for r in skipped[:20])
        more = f" 외 {len(skipped) - 20}줄" if len(skipped) > 20 else ""
        L.append(
            f"> 참고: 완전히 빈 줄 {len(skipped)}개를 건너뛰었습니다"
            f"(원본 CSV {shown}{more}행). `--scores-out` 의 `원본CSV행` 열로 대조하세요."
        )
        L.append("")

    dups = result.get("duplicate_ids") or []
    if dups:
        L.append("## ⚠ 중복된 ID (이중입력·병합오류 점검)")
        L.append("")
        L.append("| ID | 횟수 | 데이터 행 |")
        L.append("|---|---:|---|")
        for d in dups[:20]:
            rows_txt = ", ".join(str(r) for r in d["rows"])
            L.append(f"| {_mdcell(d['id'])} | {d['count']} | {_mdcell(rows_txt)} |")
        L.append("")
        if len(dups) > 20:
            L.append(f"… 외 {len(dups) - 20}개 ID")
            L.append("")
        L.append("> 같은 응답자가 두 번 들어가면 N이 부풀려지고 신뢰도·상관이 왜곡됩니다.")
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
        f"| 하위척도 | 문항수 | Cronbach α | {conf_pct}% CI | 해석 | McDonald ω | 표준화 α | SEM | MDC₉₅ "
        f"| 평균 문항간 r | 점수 평균±SD | {conf_pct}% CI | 바닥% | 천장% | 완전응답N | 점수산출N |"
    )
    L.append("|---|---:|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|")
    for s in result["subscales"]:
        a = s["alpha"]
        fl = s.get("floor")
        ce = s.get("ceiling")
        floor_txt = f"{fl['pct']:g}" if fl else "-"
        ceil_txt = f"{ce['pct']:g}" if ce else "-"
        score_txt = f"{_fmt(s['score_mean'])} ± {_fmt(s['score_sd'])}"
        omega_txt = _fmt(s.get("omega"), 3)
        if s.get("omega") is not None and s.get("omega_heywood"):
            omega_txt += " ⚠"
        L.append(
            f"| {_mdcell(s['name'])} | {s['n_items']} | {_fmt(a, 3)} | {_ci(s.get('alpha_ci'),3) or '-'} "
            f"| {alpha_label(a)} | {omega_txt} | {_fmt(s.get('alpha_std'),3)} | {_fmt(s.get('sem'),3)} | {_fmt(s.get('mdc95'),3)} "
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

    q = result.get("quality")
    if q:
        L.append("## 응답 품질 선별 (부주의응답 screening)")
        L.append("")
        L.append(
            f"- 응답자 {q['n_respondents']}명 중 플래그 **{q['n_flagged']}명** "
            f"(straightlining {q['n_straightline']}명, 결측>50% {q['n_high_missing']}명)"
        )
        L.append(
            f"- longstring 기준 ≥{q['longstring_min']} "
            f"(최대 {q['max_longstring']}, 중앙 {_fmt(q['median_longstring'], 1)}) / "
            f"IRV 중앙 {_fmt(q['median_irv'], 3)}"
        )
        L.append("")
        flagged = [r for r in q["respondents"] if r["flagged"]]
        if flagged:
            L.append("| 행 | ID | longstring | IRV | 결측 | 사유 |")
            L.append("|---:|---|---:|---:|---:|---|")
            for r in flagged[:30]:
                why = []
                if r["straightline"]:
                    why.append("전부 동일값")
                if r["long_run"]:
                    why.append("연속 동일값")
                miss_txt = "{} ({:g}%)".format(r["n_missing"], r["missing_pct"])
                L.append(
                    f"| {r['row']} | {_mdcell(_id_text(r['ids']))} | {r['longstring']} "
                    f"| {_fmt(r['irv'], 3)} | {miss_txt} | {', '.join(why)} |"
                )
            L.append("")
            if len(flagged) > 30:
                L.append(f"… 외 {len(flagged) - 30}명")
                L.append("")
        L.append(
            "> ⚠ 자동 제외 기준이 **아닙니다**. 단방향 임상척도(ISI·PHQ-9 등)에서 '모두 0'은 "
            "증상 없음의 실제 응답일 수 있습니다. 원자료를 확인할 대상을 좁히는 "
            "선별 도구로만 쓰세요(Meade & Craig 2012; Curran 2016)."
        )
        L.append("")

    sc = result.get("subscale_corr")
    if sc and sc.get("pairs"):
        L.append("## 하위척도 간 상관 (변별타당도)")
        L.append("")
        L.append("| 하위척도 A | 하위척도 B | r | N | 비고 |")
        L.append("|---|---|---:|---:|---|")
        for p in sc["pairs"]:
            r = p["r"]
            note = "매우 높음(>.85) — 별개 구성개념인지 검토" if (
                r is not None and abs(r) > 0.85) else ""
            L.append(
                f"| {_mdcell(p['a'])} | {_mdcell(p['b'])} | {_fmt(r, 3)} | {p['n']} | {note} |"
            )
        L.append("")
        L.append("> 쌍마다 두 점수가 모두 있는 응답자만 사용(pairwise) — N이 쌍마다 다를 수 있음.")
        L.append("")

    L.append("---")
    L.append("*α 해석: .9 우수 / .8 양호 / .7 수용 / .6 의심 / <.6 낮음. "
             "r<.30 또는 '제거시 α↑'이면 문항 재검토(단, 문항 제거는 척도 개발 단계용 — "
             "ISI/PHQ-9 등 검증된 척도는 문항을 빼지 마세요). "
             "McDonald ω(단일요인 congeneric)는 α의 타우동등성 가정을 완화한 신뢰도 — "
             "α와 함께 보고 권고(Revelle & Zinbarg 2009). 문항 3개 이상·수렴 시에만 표기, "
             "'⚠'는 Heywood case(모형 부적합). "
             "SEM·MDC₉₅(=1.96·√2·SEM)는 listwise 총점 SD와 α로 계산. "
             "α·문항-총점 r은 listwise N, 점수는 가용문항 N 기준.*")
    return "\n".join(L)
