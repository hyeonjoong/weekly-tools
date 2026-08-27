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
    # 엑셀 오입력(1e308 등)이 그대로 들어오면 309자리 숫자가 찍혀 표가 통째로 어긋난다.
    # 큰 값은 지수표기로 줄여 표의 가독성을 지킨다(값 자체는 숨기지 않는다).
    if abs(x) >= 1e12:
        return f"{x:.2e}"
    return f"{x:.{nd}f}"


def _num(x: Optional[float]) -> str:
    """정수면 정수로, 아니면 소수로 표기(척도 범위 등 사람이 읽는 숫자용)."""
    if x is None:
        return "-"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _ci(pair, nd: int = 2) -> str:
    """[lo, hi] 를 '[lo, hi]' 문자열로. None이거나 비유한값이면 빈 문자열.

    '[nan, nan]' 같은 칸을 그대로 찍으면 사용자는 그것을 '계산된 구간'으로 읽는다.
    산출 불가는 값이 아니라 공백('-')으로 보여야 한다.
    """
    if not pair:
        return ""
    try:
        lo, hi = float(pair[0]), float(pair[1])
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return ""
    return f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def _oneline(s: str) -> str:
    """표 한 칸에 안전하게 들어가는 한 줄 문자열로 정리.

    개행·탭은 공백으로 바꾸고, 제어문자(C0/C1)와 보이지 않는 서식문자(제로폭 공백 등)는
    지운다. 자료에서 온 라벨(집단명·문항명)에 ANSI 이스케이프(`\\x1b[2K`)가 섞이면
    터미널 리포트의 다른 줄을 지우거나 색을 바꿔 화면 내용을 왜곡할 수 있다.
    """
    txt = str(s).replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return "".join(
        ch for ch in txt if unicodedata.category(ch) not in ("Cc", "Cf")
    )


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


def _fmt_p(p: Optional[float]) -> str:
    """p 값 표기. 관례대로 .001 미만은 '<.001', 그 외 소수 셋째 자리까지.

    '0.000' 으로 적으면 '정확히 0'으로 오해되므로 쓰지 않는다.
    """
    if p is None or (isinstance(p, float) and not math.isfinite(p)):
        return "-"
    if p < 0.001:
        return "<.001"
    return f"{p:.3f}".lstrip("0") if p < 1 else "1.000"


def effect_label(g: Optional[float]) -> str:
    """|g| 관례적 해석(Cohen 1988). 어디까지나 관례일 뿐 임상적 의미와 다를 수 있다."""
    if g is None or (isinstance(g, float) and not math.isfinite(g)):
        return "-"
    a = abs(g)
    if a < 0.2:
        return "매우 작음"
    if a < 0.5:
        return "작음"
    if a < 0.8:
        return "중간"
    return "큼"


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


def _nonparam_lines(np_test, indent: str) -> list:
    """순위 기반 검정 결과 한 줄(집단비교·변화량 비교 공용). 없으면 빈 리스트."""
    if not np_test:
        return []
    from .nonparam import rank_effect_label

    if np_test.get("test") == "mannwhitney":
        r = np_test.get("rank_biserial")
        return [
            f"{indent}[비모수] Mann-Whitney U = {_fmt(np_test['U'], 1)}, "
            f"z = {_fmt(np_test['z'])}, p = {_fmt_p(np_test['p'])}   "
            f"rank-biserial r = {_fmt(r)} [{rank_effect_label(r)}]"
        ]
    if np_test.get("test") == "kruskal":
        return [
            f"{indent}[비모수] Kruskal-Wallis H({_fmt(np_test['df'], 0)}) = "
            f"{_fmt(np_test['H'])}, p = {_fmt_p(np_test['p'])}   "
            f"ε² = {_fmt(np_test['epsilon_sq'], 3)}"
        ]
    if np_test.get("test") == "wilcoxon":
        r = np_test.get("rank_biserial")
        how = "정확검정" if np_test.get("exact") else "정규근사(동순위 보정)"
        zero = (
            f", 변화 0인 {np_test['n_zero']}명 제외" if np_test.get("n_zero") else ""
        )
        return [
            f"{indent}[비모수] Wilcoxon 부호순위 W = {_fmt(np_test['W'], 1)}, "
            f"p = {_fmt_p(np_test['p'])}   r = {_fmt(r)} [{rank_effect_label(r)}]",
            f"{indent}          ({how}, 검정에 쓴 쌍 {np_test['n']}쌍{zero})",
        ]
    return []


def _pair_diag_lines(pp: Dict[str, object], indent: str) -> list:
    """짝짓기에서 빠진 인원/행 수. 실패한 경우에도 반드시 보여준다 — '짝이 없다'는 사유만
    보여주면 사용자는 왜 없는지(중복 키인지, ID 표기가 다른지) 알 방법이 없다."""
    notes = []
    if pp.get("n_unpaired"):
        notes.append(f"한 시점에만 있어 제외 {pp['n_unpaired']}명")
    if pp.get("n_dup_excluded"):
        notes.append(f"같은 (ID,시점)이 두 번이라 제외 {pp['n_dup_excluded']}명")
    if pp.get("n_no_id"):
        notes.append(f"ID 가 비어 제외 {pp['n_no_id']}행")
    if pp.get("n_other_time"):
        notes.append(f"비교 대상이 아닌 시점의 행 {pp['n_other_time']}개")
    if not notes:
        return []
    return [f"{indent}짝짓기 제외: " + ", ".join(notes)]


def _prepost_lines(result: Dict[str, object]) -> list:
    """사전-사후(반복측정) 비교 절 — 텍스트 리포트."""
    pp = result.get("prepost")
    if not pp:
        return []
    conf_pct = int(round(result.get("conf_level", 0.95) * 100))
    lines = [""]
    lines.append(f"[ 사전-사후 비교 (시점 컬럼: {_oneline(str(pp['column']))}) ]")
    if not pp.get("usable"):
        lines.append(f"  ⚠ {_oneline(str(pp.get('reason')))}")
        lines.extend(_pair_diag_lines(pp, "  "))
        return lines
    rule = {
        "numeric": "시점 라벨이 숫자여서 숫자 순",
        "appearance": "자료에 먼저 나온 시점을 사전으로 봄",
        "explicit": "--time-pre/--time-post 로 직접 지정",
    }.get(str(pp.get("order_rule")), "")
    lines.append(
        f"  사전 '{_oneline(str(pp['pre']))}' → 사후 '{_oneline(str(pp['post']))}'"
        f"   ({rule})"
    )
    if str(pp.get("order_rule")) == "appearance":
        lines.append(
            "  ⚠ 시점 순서는 파일에 나온 순서로 **추정**한 것입니다 — 자료를 시점 기준으로"
        )
        lines.append(
            "    정렬했거나 첫 사람의 기저 행이 없으면 사전/사후가 뒤바뀝니다."
        )
        lines.append(
            "    변화량 부호가 통째로 뒤집히므로 --time-pre/--time-post 로 명시하세요."
        )
    if pp.get("id_label"):
        lines.append(f"  짝짓기 기준 ID: {_oneline(str(pp['id_label']))}")
    notes = []
    if pp.get("n_unpaired"):
        notes.append(f"한 시점에만 있어 제외 {pp['n_unpaired']}명")
    if pp.get("n_dup_excluded"):
        notes.append(f"같은 (ID,시점)이 두 번이라 제외 {pp['n_dup_excluded']}명")
    if pp.get("n_no_id"):
        notes.append(f"ID 가 비어 제외 {pp['n_no_id']}행")
    if pp.get("n_other_time"):
        notes.append(f"비교 대상이 아닌 시점의 행 {pp['n_other_time']}개")
    lines.append(
        f"  짝지은 응답자 {pp.get('n_pairs_total', 0)}명"
        + (f"  ({', '.join(notes)})" if notes else "")
    )
    for row in pp["subscales"]:
        lines.append("")
        method = "총합" if row.get("score_method") == "sum" else "평균"
        lines.append(f"  ▶ {_oneline(str(row['name']))}  (점수: {method})")
        if row.get("n_missing_score"):
            lines.append(
                f"     ※ 두 시점 중 한쪽 점수가 없어 빠진 사람 {row['n_missing_score']}명"
            )
        if int(row.get("n_pairs", 0)) < 1:
            lines.append(f"     ⚠ {_oneline(str(row.get('reason')))}")
            continue
        lines.append(
            f"     {_pad('시점', 10)} {_pad('N', 5, 'right')} {_pad('평균', 8, 'right')} "
            f"{_pad('SD', 8, 'right')} {_pad('중앙', 7, 'right')} {_pad('α', 6, 'right')}"
        )
        lines.append("     " + "-" * 48)
        for key, lab, al in (
            ("pre", pp["pre"], row.get("alpha_pre")),
            ("post", pp["post"], row.get("alpha_post")),
        ):
            d = row[key]
            lines.append(
                f"     {_pad(_oneline(str(lab)), 10)} {_pad(d['n'], 5, 'right')} "
                f"{_pad(_fmt(d['mean']), 8, 'right')} {_pad(_fmt(d['sd']), 8, 'right')} "
                f"{_pad(_fmt(d['median'], 1), 7, 'right')} {_pad(_fmt(al, 2), 6, 'right')}"
            )
        ch = row["change"]
        who = f"({_oneline(str(pp['post']))} − {_oneline(str(pp['pre']))})"
        lines.append(
            f"     변화량{who} {_fmt(ch['mean'])} ± {_fmt(ch['sd'])}"
            + (f"  {conf_pct}% CI {_ci(row['change_ci'])}" if row.get("change_ci") else "")
        )
        t = row.get("test")
        if t:
            lines.append(
                f"     대응표본 t({_fmt(t['df'], 0)}) = {_fmt(t['t'])}, p = {_fmt_p(t['p'])}"
                + (
                    f"   (Holm 보정 p = {_fmt_p(row['p_holm'])})"
                    if int(pp.get("n_tests", 0)) > 1 else ""
                )
            )
        e = row.get("effect")
        if e:
            lines.append(
                f"     Cohen dz = {_fmt(e['dz'])} {_ci(e['ci'])} [{effect_label(e['dz'])}]"
                "  (변화량 SD 기준 — 독립표본 g와 직접 비교 불가)"
            )
        lines.extend(_nonparam_lines(row.get("wilcoxon"), "     "))
        icc = row.get("icc")
        if row.get("r_prepost") is not None or icc:
            parts = []
            if row.get("r_prepost") is not None:
                parts.append(f"사전-사후 r = {_fmt(row['r_prepost'], 3)}")
            if icc:
                parts.append(
                    f"ICC(2,1) = {_fmt(icc['icc'], 3)}"
                    + (f" {_ci(icc['ci'], 3)}" if icc.get("ci") else "")
                )
                parts.append(f"SEM(재측정, √MSE) = {_fmt(icc['sem'])}")
            lines.append("     " + "   ".join(parts))
        rsp = row.get("responders")
        if rsp:
            src = {
                "mcid": "MCID(config)",
                "mdc95_retest": "MDC₉₅(재측정 기반)",
                "mdc95_alpha": "MDC₉₅(α 기반·전체 시점 합산)",
            }.get(str(rsp.get("source")), "MDC₉₅")
            lines.append(
                f"     반응자(임계값 {_fmt(rsp['threshold'])}, {src}): "
                f"감소 {rsp['decreased']}명({rsp['decreased_pct']}%) / "
                f"변화없음 {rsp['unchanged']}명({rsp['unchanged_pct']}%) / "
                f"증가 {rsp['increased']}명({rsp['increased_pct']}%)"
            )
        gc = row.get("group_change")
        if gc:
            lines.append(
                f"     [ 집단별 변화량 비교 — 기준 컬럼: {_oneline(str(pp.get('group_column')))} ]"
            )
            lw = max([_dwidth(str(g["label"])) for g in gc["groups"]] + [4])
            lines.append(
                f"       {_pad('집단', lw)} {_pad('N', 5, 'right')} "
                f"{_pad('변화 평균', 10, 'right')} {_pad('SD', 8, 'right')}"
            )
            for g in gc["groups"]:
                lines.append(
                    f"       {_pad(_oneline(str(g['label'])), lw)} {_pad(g['n'], 5, 'right')} "
                    f"{_pad(_fmt(g['mean']), 10, 'right')} {_pad(_fmt(g['sd']), 8, 'right')}"
                )
            gt = gc.get("test")
            dl = gc.get("diff_labels") or []
            gwho = f"({_oneline(str(dl[0]))} − {_oneline(str(dl[1]))})" if len(dl) == 2 else ""
            if gt and gt.get("test") == "welch_t":
                lines.append(
                    f"       Welch t({_fmt(gt['df'], 1)}) = {_fmt(gt['t'])}, "
                    f"p = {_fmt_p(gt['p'])}"
                )
                lines.append(
                    f"       변화량 차이{gwho} {_fmt(gt['mean_diff'])}  "
                    f"{conf_pct}% CI {_ci(gt['diff_ci'])}"
                )
                ge = gc.get("effect")
                if ge:
                    lines.append(
                        f"       Hedges g{gwho} = {_fmt(ge['g'])} {_ci(ge['ci'])} "
                        f"[{effect_label(ge['g'])}]"
                    )
            elif gt and gt.get("test") == "welch_anova":
                lines.append(
                    f"       Welch ANOVA F({_fmt(gt['df1'], 0)}, {_fmt(gt['df2'], 1)}) = "
                    f"{_fmt(gt['F'])}, p = {_fmt_p(gt['p'])}"
                )
            lines.extend(_nonparam_lines(gc.get("nonparam"), "       "))
            if gc.get("excluded_groups"):
                lines.append(
                    "       ※ 변화량이 2명 미만이라 빠진 집단: "
                    + ", ".join(_oneline(str(x)) for x in gc["excluded_groups"])
                )
        if row.get("n_group_conflict"):
            lines.append(
                f"     ⚠ 같은 ID 인데 시점마다 집단 라벨이 다른 사람 {row['n_group_conflict']}명은"
                " 집단별 비교에서 제외했습니다(원자료 확인 필요)."
            )
        if row.get("reason"):
            lines.append(f"     ⚠ {_oneline(str(row['reason']))}")
    lines.append("")
    lines.append("      ※ 변화량 = 사후 − 사전. 부호는 척도 방향에 따라 해석하세요")
    lines.append("        (증상척도는 감소가 호전). 반응자 표는 방향 판단 없이 감소/증가로만 셉니다.")
    lines.append("        대응표본 t 는 짝이 모두 있는 사람만 씁니다(완전자료 분석) — 중도탈락이")
    lines.append("        많으면 결과가 낙관적으로 치우칩니다(ITT·혼합모형을 대신하지 않음).")
    lines.append("        Cohen dz 는 변화량 SD로 표준화한 값이라 독립표본 g와 스케일이 다르고,")
    lines.append("        CI 는 대표본(Wald) 근사이며 g와 달리 소표본 편향보정이 없습니다.")
    lines.append("        임계값이 MDC₉₅ 인 경우 그 α·SD 는 '모든 시점을 합친' 자료에서 온 값이라")
    lines.append("        개입으로 점수가 이동했으면 실제보다 큽니다 — config 의 mcid 사용을 권합니다.")
    lines.append("        SEM(재측정)=√MSE 는 두 시점의 평균 이동을 제외한 일치(consistency)형입니다.")
    lines.append("        ICC(2,1)은 두 시점 사이에 개입이 없을 때만 '검사-재검사 신뢰도'입니다 —")
    lines.append("        치료 전후라면 낮은 ICC 는 신뢰도가 아니라 반응의 개인차를 뜻합니다.")
    lines.append("        집단별 변화량 비교는 공변량(기저값) 보정을 하지 않은 탐색적 계산입니다.")
    return lines


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
    if result.get("time_column"):
        lines.append(f"  시점 컬럼 : {_oneline(str(result['time_column']))} (반복측정 자료)")
        lines.append(
            "    ※ 위의 문항 기술통계·α·하위척도 점수는 파일에 있는 **모든 시점을 합친**"
        )
        lines.append("      값입니다(같은 사람이 시점 수만큼 들어감). 비교하지 않는 시점의 행도")
        lines.append("      여기에는 포함됩니다. 시점별 α와 변화량은 아래 사전-사후 절 참고.")
    enc = str(result.get("encoding_used") or "utf-8-sig")
    if enc != "utf-8-sig" or result.get("encoding_forced"):
        how = (
            "--encoding 으로 지정" if result.get("encoding_forced")
            else "UTF-8 로 읽히지 않아 자동 재시도"
        )
        lines.append(f"  파일 인코딩: {enc} ({how})")
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
        # 임상 심각도 구간 분포(config 의 severity_bands 지정 시)
        bands = s.get("bands") or []
        if bands:
            pror = int(s.get("n_prorated") or 0)
            pror_txt = f"; 그중 일부문항 결측으로 비례배분된 {pror}명" if pror else ""
            lines.append(
                f"     [ 심각도 구간 분포 ]  (점수산출 {s['n_scored']}명 기준{pror_txt})"
            )
            bw = max([_dwidth(str(b["label"])) for b in bands] + [4])
            for b in bands:
                rng = f"{_num(b['min'])}~{_num(b['max'])}"
                lines.append(
                    f"       {_pad(b['label'], bw)} {_pad(rng, 11, 'right')} : "
                    f"{_pad(str(b['n']) + '명', 6, 'right')} ({b['pct']:g}%)"
                )
            nub = int(s.get("n_unbanded") or 0)
            if nub:
                lines.append(
                    f"       {_pad('미분류', bw)} {_pad('구간 밖', 11, 'right')} : "
                    f"{_pad(str(nub) + '명', 6, 'right')}  ⚠ 어느 구간에도 속하지 않음"
                )
                lines.append(
                    "         (구간 사이 빈틈에 떨어진 점수 — 결측 비례배분으로 소수점"
                )
                lines.append(
                    "          점수가 생겼거나 구간이 점수 범위를 다 덮지 않는 경우)"
                )
            if s.get("bands_out_of_range"):
                lines.append(
                    "       ⚠ 구간 경계가 가능한 점수 범위"
                    f"({_num(s.get('possible_min'))}~{_num(s.get('possible_max'))})를 벗어납니다"
                )
                lines.append(
                    "         — severity_bands 는 지금의 점수 방식"
                    f"({'총합' if s.get('score_method') == 'sum' else '평균'}) 단위로 적으세요."
                )
                lines.append(
                    "         (단위가 어긋나면 전원이 최하위 구간으로 몰려 '정상적인 표'처럼"
                )
                lines.append("          보일 수 있으니 이 경고를 반드시 확인하세요)")
            if s.get("bands_range_unknown"):
                lines.append(
                    "       ⚠ config에 scale_min/scale_max 가 없어 구간 단위(평균/총합)가"
                )
                lines.append(
                    "         맞는지 점검할 수 없었습니다 — 척도 범위를 넣으면 자동으로 확인합니다."
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

    # 집단 비교(--group-col)
    gc = result.get("group_compare")
    if gc:
        conf_pct = int(round(result.get("conf_level", 0.95) * 100))
        lines.append("")
        lines.append(f"[ 집단 비교 (기준 컬럼: {_oneline(gc['column'])}) ]")
        if not gc.get("usable"):
            lines.append(f"  ⚠ {_oneline(str(gc.get('reason')))}")
        else:
            lines.append("  집단: " + ", ".join(_oneline(x) for x in gc["labels"]))
            if result.get("time_column") and result.get("time_column") == gc["column"]:
                lines.append(
                    "  ⚠ 집단 컬럼과 시점 컬럼이 같습니다 — 여기서는 같은 사람이 두 집단에"
                )
                lines.append(
                    "    들어가 독립성 가정이 깨집니다. 시점 비교는 아래 '사전-사후' 절을 보세요."
                )
            if gc.get("n_no_label"):
                lines.append(
                    f"  ※ 집단 라벨이 비어 있는 응답자 {gc['n_no_label']}명은 비교에서 제외했습니다."
                )
            for row in gc["subscales"]:
                lines.append("")
                lines.append(f"  ▶ {_oneline(row['name'])}")
                lw = max([_dwidth(str(g["label"])) for g in row["groups"]] + [4])
                lines.append(
                    f"     {_pad('집단', lw)} {_pad('N', 5, 'right')} "
                    f"{_pad('평균', 8, 'right')} {_pad('SD', 8, 'right')} "
                    f"{_pad('중앙', 7, 'right')} {_pad('α', 6, 'right')}"
                )
                lines.append("     " + "-" * (lw + 38))
                for g in row["groups"]:
                    lines.append(
                        f"     {_pad(g['label'], lw)} {_pad(g['n'], 5, 'right')} "
                        f"{_pad(_fmt(g['mean']), 8, 'right')} {_pad(_fmt(g['sd']), 8, 'right')} "
                        f"{_pad(_fmt(g['median'], 1), 7, 'right')} "
                        f"{_pad(_fmt(g.get('alpha'), 2), 6, 'right')}"
                    )
                t = row.get("test")
                if t and t.get("test") == "welch_t":
                    lines.append(
                        f"     Welch t({_fmt(t['df'], 1)}) = {_fmt(t['t'], 2)}, "
                        f"p = {_fmt_p(t['p'])}"
                        + (
                            f"   (Holm 보정 p = {_fmt_p(row['p_holm'])})"
                            if gc.get("n_tests", 0) > 1 else ""
                        )
                    )
                    dl = row.get("diff_labels") or []
                    who = f"({_oneline(dl[0])} − {_oneline(dl[1])})" if len(dl) == 2 else ""
                    lines.append(
                        f"     평균차{who} {_fmt(t['mean_diff'])}  "
                        f"{conf_pct}% CI {_ci(t['diff_ci'])}"
                    )
                    e = row.get("effect")
                    if e:
                        lines.append(
                            f"     Hedges g{who} = {_fmt(e['g'])} {_ci(e['ci'])} "
                            f"[{effect_label(e['g'])}]"
                        )
                elif t and t.get("test") == "welch_anova":
                    lines.append(
                        f"     Welch ANOVA F({_fmt(t['df1'], 0)}, {_fmt(t['df2'], 1)}) = "
                        f"{_fmt(t['F'], 2)}, p = {_fmt_p(t['p'])}"
                        + (
                            f"   (Holm 보정 p = {_fmt_p(row['p_holm'])})"
                            if gc.get("n_tests", 0) > 1 else ""
                        )
                    )
                    lines.append(
                        "     (집단 3개 이상 → 전체 차이 검정. 어느 쌍이 다른지는 사후검정 필요)"
                    )
                lines.extend(_nonparam_lines(row.get("nonparam"), "     "))
                exc = row.get("excluded_groups") or []
                if exc:
                    lines.append(
                        "     ※ 점수가 2명 미만이라 검정에서 빠진 집단: "
                        + ", ".join(_oneline(x) for x in exc)
                    )
                if row.get("reason"):
                    lines.append(f"     ⚠ {row['reason']}")
            lines.append("")
            lines.append(
                "      ※ 등분산을 가정하지 않는 Welch 검정을 씁니다(집단 크기·분산이 다른"
            )
            lines.append(
                "        임상자료의 기본값; Delacre et al. 2017). 하위척도가 여러 개면 검정도"
            )
            lines.append(
                "        여러 번이므로 Holm 보정 p 를 함께 봅니다. 이 비교는 자료 점검·기술"
            )
            lines.append(
                "        목적의 탐색적 분석이며, 사전 정의된 1차 분석을 대신하지 않습니다."
            )
            lines.append(
                "        Hedges g: |0.2| 작음 / |0.5| 중간 / |0.8| 큼 (관례, Cohen 1988)."
            )
            lines.append(
                "        g의 CI는 대표본 근사이므로 집단이 작으면 실제보다 좁습니다."
            )
            lines.append(
                "        표의 N은 '점수가 산출된 인원', α는 그 집단의 '완전응답자' 기준이라"
            )
            lines.append(
                "        분모가 다를 수 있고, 집단이 작으면 α가 음수로도 나옵니다(참고용)."
            )

    lines.extend(_prepost_lines(result))

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
    """마크다운 표 셀 값을 안전하게 만든다.

    파이프(|)는 표 구조를, `<`/`>` 는 원시 HTML(<img onerror=...>)을, 대괄호는 링크
    (`[클릭](javascript:...)`)를 만든다. 자료에서 온 라벨이 그대로 들어가면 붙여넣은
    문서에서 실행 가능한 내용이 되므로 모두 무해한 표기로 바꾼다.
    """
    return (
        _oneline(x)
        .replace("|", "\\|")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("`", "\\`")
    )


def _nonparam_md(np_test) -> list:
    """순위 기반 검정 한 줄(Markdown)."""
    if not np_test:
        return []
    from .nonparam import rank_effect_label

    if np_test.get("test") == "mannwhitney":
        r = np_test.get("rank_biserial")
        return [
            f"- (비모수) Mann-Whitney U = {_fmt(np_test['U'], 1)}, z = {_fmt(np_test['z'])}, "
            f"p = {_fmt_p(np_test['p'])}, rank-biserial r = {_fmt(r)} ({rank_effect_label(r)})"
        ]
    if np_test.get("test") == "kruskal":
        return [
            f"- (비모수) Kruskal-Wallis H({_fmt(np_test['df'], 0)}) = {_fmt(np_test['H'])}, "
            f"p = {_fmt_p(np_test['p'])}, ε² = {_fmt(np_test['epsilon_sq'], 3)}"
        ]
    if np_test.get("test") == "wilcoxon":
        r = np_test.get("rank_biserial")
        how = "정확검정" if np_test.get("exact") else "정규근사(동순위 보정)"
        zero = f", 변화 0인 {np_test['n_zero']}명 제외" if np_test.get("n_zero") else ""
        return [
            f"- (비모수) Wilcoxon 부호순위 W = {_fmt(np_test['W'], 1)}, "
            f"p = {_fmt_p(np_test['p'])}, r = {_fmt(r)} ({rank_effect_label(r)}) "
            f"— {how}, 쌍 {np_test['n']}개{zero}"
        ]
    return []


def _prepost_md(result: Dict[str, object]) -> list:
    """사전-사후(반복측정) 비교 절 — Markdown."""
    pp = result.get("prepost")
    if not pp:
        return []
    conf_pct = int(round(result.get("conf_level", 0.95) * 100))
    L = [f"## 사전-사후 비교 (시점 컬럼: {_mdcell(pp['column'])})", ""]
    if not pp.get("usable"):
        L.append(f"> ⚠ {_mdcell(pp.get('reason'))}")
        for line in _pair_diag_lines(pp, ""):
            L.append(f"> {line}")
        L.append("")
        return L
    rule = {
        "numeric": "시점 라벨이 숫자여서 숫자 순",
        "appearance": "자료에 먼저 나온 시점을 사전으로 봄",
        "explicit": "`--time-pre`/`--time-post` 로 직접 지정",
    }.get(str(pp.get("order_rule")), "")
    L.append(
        f"- 사전 **{_mdcell(pp['pre'])}** → 사후 **{_mdcell(pp['post'])}** ({rule})"
    )
    if str(pp.get("order_rule")) == "appearance":
        L.append(
            "- ⚠ 시점 순서는 파일에 나온 순서로 **추정**한 것입니다(정렬 순서가 바뀌면 사전/사후가"
            " 뒤집혀 변화량 부호가 반대가 됩니다) — `--time-pre`/`--time-post` 로 명시하세요."
        )
    notes = []
    if pp.get("n_unpaired"):
        notes.append(f"한 시점에만 있어 제외 {pp['n_unpaired']}명")
    if pp.get("n_dup_excluded"):
        notes.append(f"같은 (ID,시점) 중복으로 제외 {pp['n_dup_excluded']}명")
    if pp.get("n_no_id"):
        notes.append(f"ID 가 비어 제외 {pp['n_no_id']}행")
    if pp.get("n_other_time"):
        notes.append(f"비교 대상이 아닌 시점의 행 {pp['n_other_time']}개")
    L.append(
        f"- 짝지은 응답자 **{pp.get('n_pairs_total', 0)}명**"
        + (f" ({', '.join(notes)})" if notes else "")
    )
    L.append("")
    for row in pp["subscales"]:
        L.append(f"### {_oneline(str(row['name']))}")
        L.append("")
        if int(row.get("n_pairs", 0)) < 1:
            L.append(f"> ⚠ {_mdcell(row.get('reason'))}")
            L.append("")
            continue
        L.append("| 시점 | N | 평균 | SD | 중앙 | α |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for key, lab, al in (
            ("pre", pp["pre"], row.get("alpha_pre")),
            ("post", pp["post"], row.get("alpha_post")),
        ):
            d = row[key]
            L.append(
                f"| {_mdcell(lab)} | {d['n']} | {_fmt(d['mean'])} | {_fmt(d['sd'])} "
                f"| {_fmt(d['median'], 1)} | {_fmt(al, 2)} |"
            )
        L.append("")
        ch = row["change"]
        who = f"({_mdcell(pp['post'])} − {_mdcell(pp['pre'])})"
        L.append(
            f"- 변화량{who} **{_fmt(ch['mean'])} ± {_fmt(ch['sd'])}**"
            + (f", {conf_pct}% CI {_ci(row['change_ci'])}" if row.get("change_ci") else "")
        )
        t = row.get("test")
        if t:
            holm = (
                f", Holm 보정 p = {_fmt_p(row['p_holm'])}"
                if int(pp.get("n_tests", 0)) > 1 else ""
            )
            L.append(
                f"- 대응표본 t({_fmt(t['df'], 0)}) = {_fmt(t['t'])}, "
                f"**p = {_fmt_p(t['p'])}**{holm}"
            )
        e = row.get("effect")
        if e:
            L.append(
                f"- Cohen dz = {_fmt(e['dz'])} {_ci(e['ci'])} ({effect_label(e['dz'])}) "
                "— 변화량 SD 기준이라 독립표본 g와 직접 비교 불가"
            )
        L.extend(_nonparam_md(row.get("wilcoxon")))
        icc = row.get("icc")
        if icc:
            L.append(
                f"- 사전-사후 r = {_fmt(row.get('r_prepost'), 3)}, "
                f"ICC(2,1) = {_fmt(icc['icc'], 3)}"
                + (f" {_ci(icc['ci'], 3)}" if icc.get("ci") else "")
                + f", SEM(재측정, √MSE) = {_fmt(icc['sem'])}"
            )
        rsp = row.get("responders")
        if rsp:
            src = {
                "mcid": "MCID(config)",
                "mdc95_retest": "MDC₉₅(재측정 기반)",
                "mdc95_alpha": "MDC₉₅(α 기반·전체 시점 합산)",
            }.get(str(rsp.get("source")), "MDC₉₅")
            L.append(
                f"- 반응자(임계값 {_fmt(rsp['threshold'])}, {src}): "
                f"감소 {rsp['decreased']}명({rsp['decreased_pct']}%) / "
                f"변화없음 {rsp['unchanged']}명({rsp['unchanged_pct']}%) / "
                f"증가 {rsp['increased']}명({rsp['increased_pct']}%)"
            )
        gc = row.get("group_change")
        if gc:
            L.append("")
            L.append(f"**집단별 변화량 비교 (기준 컬럼: {_mdcell(pp.get('group_column'))})**")
            L.append("")
            L.append("| 집단 | N | 변화 평균 | SD |")
            L.append("|---|---:|---:|---:|")
            for g in gc["groups"]:
                L.append(
                    f"| {_mdcell(g['label'])} | {g['n']} | {_fmt(g['mean'])} | {_fmt(g['sd'])} |"
                )
            L.append("")
            gt = gc.get("test")
            dl = gc.get("diff_labels") or []
            gwho = f"({_mdcell(dl[0])} − {_mdcell(dl[1])})" if len(dl) == 2 else ""
            if gt and gt.get("test") == "welch_t":
                L.append(
                    f"- Welch t({_fmt(gt['df'], 1)}) = {_fmt(gt['t'])}, "
                    f"**p = {_fmt_p(gt['p'])}**"
                )
                L.append(
                    f"- 변화량 차이{gwho} {_fmt(gt['mean_diff'])}, "
                    f"{conf_pct}% CI {_ci(gt['diff_ci'])}"
                )
                ge = gc.get("effect")
                if ge:
                    L.append(
                        f"- Hedges g{gwho} = {_fmt(ge['g'])} {_ci(ge['ci'])} "
                        f"({effect_label(ge['g'])})"
                    )
            elif gt and gt.get("test") == "welch_anova":
                L.append(
                    f"- Welch ANOVA F({_fmt(gt['df1'], 0)}, {_fmt(gt['df2'], 1)}) = "
                    f"{_fmt(gt['F'])}, **p = {_fmt_p(gt['p'])}**"
                )
            L.extend(_nonparam_md(gc.get("nonparam")))
            if gc.get("excluded_groups"):
                L.append(
                    "- 변화량이 2명 미만이라 빠진 집단: "
                    + ", ".join(_mdcell(x) for x in gc["excluded_groups"])
                )
        if row.get("n_group_conflict"):
            L.append(
                f"- ⚠ 같은 ID 인데 시점마다 집단 라벨이 다른 {row['n_group_conflict']}명은 "
                "집단별 비교에서 제외(원자료 확인 필요)"
            )
        if row.get("reason"):
            L.append(f"- ⚠ {_mdcell(row['reason'])}")
        L.append("")
    L.append(
        "> 변화량 = **사후 − 사전**(부호는 척도 방향에 따라 해석 — 증상척도는 감소가 호전). "
        "짝이 모두 있는 사람만 쓰는 완전자료 분석이라 중도탈락이 많으면 낙관적으로 치우칩니다"
        "(ITT·혼합모형을 대신하지 않음). Cohen dz 는 변화량 SD 기준이라 독립표본 g와 스케일이 "
        "다르고, 그 CI 는 대표본(Wald) 근사이며 소표본 편향보정이 없습니다. 임계값이 MDC₉₅ 인 "
        "경우 그 α·SD 는 '모든 시점을 합친' 자료에서 온 값이라 개입으로 점수가 이동했으면 실제보다 "
        "큽니다(config 의 `mcid` 권장). ICC(2,1)은 두 시점 사이에 **개입이 없을 때만** 검사-재검사 "
        "신뢰도이며, 함께 나오는 SEM(재측정)=√MSE 는 평균 이동을 제외한 일치(consistency)형입니다. "
        "집단별 변화량 비교는 기저값 보정을 하지 않은 탐색적 계산입니다."
    )
    L.append("")
    return L


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
    if result.get("time_column"):
        L.append(f"- 시점 컬럼: {_mdcell(result['time_column'])} (반복측정 자료)")
        L.append(
            "  - ⚠ 아래 문항 기술통계·α·하위척도 점수는 파일의 **모든 시점을 합친** 값입니다"
            "(같은 사람이 시점 수만큼 들어가며, 비교하지 않는 시점의 행도 포함). "
            "시점별 α와 변화량은 '사전-사후 비교' 절 참고."
        )
    enc = str(result.get("encoding_used") or "utf-8-sig")
    if enc != "utf-8-sig" or result.get("encoding_forced"):
        how = (
            "`--encoding` 으로 지정" if result.get("encoding_forced")
            else "UTF-8 로 읽히지 않아 자동 재시도"
        )
        L.append(f"- 파일 인코딩: `{enc}` ({how})")
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

    banded = [s for s in result["subscales"] if s.get("bands")]
    if banded:
        L.append("## 임상 심각도 구간 분포")
        L.append("")
        for s in banded:
            pror = int(s.get("n_prorated") or 0)
            pror_txt = f", 그중 비례배분 {pror}명" if pror else ""
            L.append(f"### {_oneline(s['name'])} (점수산출 {s['n_scored']}명{pror_txt})")
            L.append("")
            L.append("| 심각도 | 점수 구간 | N | % |")
            L.append("|---|---|---:|---:|")
            for b in s["bands"]:
                L.append(
                    f"| {_mdcell(b['label'])} | {_num(b['min'])}~{_num(b['max'])} "
                    f"| {b['n']} | {b['pct']:g} |"
                )
            nub = int(s.get("n_unbanded") or 0)
            if nub:
                L.append(f"| 미분류(구간 밖) | - | {nub} | - |")
            L.append("")
            if s.get("bands_out_of_range"):
                L.append(
                    f"> ⚠ 구간 경계가 가능한 점수 범위({_num(s.get('possible_min'))}~"
                    f"{_num(s.get('possible_max'))})를 벗어납니다 — `severity_bands` 는 "
                    f"현재 점수 방식({'총합' if s.get('score_method') == 'sum' else '평균'}) "
                    "단위로 지정하세요. 단위가 어긋나면 전원이 최하위 구간으로 몰려 "
                    "정상적인 표처럼 보일 수 있습니다."
                )
                L.append("")
            if s.get("bands_range_unknown"):
                L.append(
                    "> ⚠ config에 `scale_min`/`scale_max` 가 없어 구간 단위(평균/총합)가 맞는지 "
                    "점검할 수 없었습니다 — 척도 범위를 넣으면 자동으로 확인합니다."
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

    gc = result.get("group_compare")
    if gc:
        L.append(f"## 집단 비교 (기준 컬럼: {_mdcell(gc['column'])})")
        L.append("")
        if not gc.get("usable"):
            L.append(f"> ⚠ {_mdcell(gc.get('reason'))}")
            L.append("")
        else:
            if gc.get("n_no_label"):
                L.append(
                    f"> 집단 라벨이 비어 있는 응답자 {gc['n_no_label']}명은 비교에서 제외했습니다."
                )
                L.append("")
            if result.get("time_column") and result.get("time_column") == gc["column"]:
                L.append(
                    "> ⚠ 집단 컬럼과 시점 컬럼이 같습니다 — 같은 사람이 두 집단에 들어가 독립성 "
                    "가정이 깨집니다. 시점 비교는 아래 '사전-사후 비교' 절을 보세요."
                )
                L.append("")
            for row in gc["subscales"]:
                L.append(f"### {_oneline(row['name'])}")
                L.append("")
                L.append("| 집단 | N | 평균 | SD | 중앙 | α |")
                L.append("|---|---:|---:|---:|---:|---:|")
                for g in row["groups"]:
                    L.append(
                        f"| {_mdcell(g['label'])} | {g['n']} | {_fmt(g['mean'])} "
                        f"| {_fmt(g['sd'])} | {_fmt(g['median'], 1)} | {_fmt(g.get('alpha'), 2)} |"
                    )
                L.append("")
                t = row.get("test")
                e = row.get("effect")
                holm = (
                    f", Holm 보정 p = {_fmt_p(row['p_holm'])}"
                    if gc.get("n_tests", 0) > 1 else ""
                )
                if t and t.get("test") == "welch_t":
                    L.append(
                        f"- Welch t({_fmt(t['df'], 1)}) = {_fmt(t['t'], 2)}, "
                        f"**p = {_fmt_p(t['p'])}**{holm}"
                    )
                    dl = row.get("diff_labels") or []
                    who = f"({_mdcell(dl[0])} − {_mdcell(dl[1])})" if len(dl) == 2 else ""
                    L.append(
                        f"- 평균차{who} {_fmt(t['mean_diff'])}, {conf_pct}% CI {_ci(t['diff_ci'])}"
                    )
                    if e:
                        L.append(
                            f"- Hedges g{who} = {_fmt(e['g'])} {_ci(e['ci'])} "
                            f"({effect_label(e['g'])})"
                        )
                elif t and t.get("test") == "welch_anova":
                    L.append(
                        f"- Welch ANOVA F({_fmt(t['df1'], 0)}, {_fmt(t['df2'], 1)}) = "
                        f"{_fmt(t['F'], 2)}, **p = {_fmt_p(t['p'])}**{holm}"
                    )
                    L.append("- 집단 3개 이상 → 전체 차이 검정(어느 쌍이 다른지는 사후검정 필요)")
                L.extend(_nonparam_md(row.get("nonparam")))
                exc = row.get("excluded_groups") or []
                if exc:
                    L.append(
                        "- 점수가 2명 미만이라 검정에서 빠진 집단: "
                        + ", ".join(_mdcell(x) for x in exc)
                    )
                if row.get("reason"):
                    L.append(f"- ⚠ {row['reason']}")
                L.append("")
            L.append(
                "> 등분산을 가정하지 않는 **Welch** 검정(Delacre et al. 2017). 하위척도가 여러 개면 "
                "Holm 보정 p 를 함께 보세요. 이 비교는 자료 점검·기술 목적의 **탐색적** 분석이며 "
                "사전 정의된 1차 분석을 대신하지 않습니다. Hedges g(2집단 전용) 관례: |0.2| 작음 / "
                "|0.5| 중간 / |0.8| 큼(Cohen 1988) — CI는 대표본 근사라 소표본에서 좁습니다. "
                "표의 N은 점수 산출 인원, α는 그 집단의 완전응답자 기준이라 분모가 다를 수 있으며 "
                "집단이 작으면 α가 음수로도 나옵니다(참고용)."
            )
            L.append("")

    L.extend(_prepost_md(result))

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
