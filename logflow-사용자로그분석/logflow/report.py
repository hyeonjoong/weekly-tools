"""분석 결과(Analysis)를 사람이 읽는 텍스트 리포트로 렌더링한다."""

from __future__ import annotations

import unicodedata
from typing import List, Optional, Sequence

from .analyze import Analysis, analyze
from .dataio import Event
from .metrics import ActiveUsers, EventStat, FunnelStep, Retention, UserStat

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# 폭 0으로 취급할 제로폭 문자(제로폭 공백/조이너/BOM 등).
_ZERO_WIDTH = {"​", "‌", "‍", "﻿", "⁠"}


def _dw(s: str) -> int:
    """터미널 표시 폭 (한글 등 전각 문자는 2칸, 결합/제로폭 문자는 0칸)."""
    w = 0
    for c in s:
        if unicodedata.combining(c) or c in _ZERO_WIDTH:
            continue  # 결합 악센트·제로폭 문자는 폭 0
        w += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    return w


def _rj(s: str, width: int) -> str:
    """표시 폭 기준 오른쪽 정렬 (전각 문자 고려)."""
    return " " * max(0, width - _dw(s)) + s


def _lj(s: str, width: int) -> str:
    """표시 폭 기준 왼쪽 정렬 (전각 문자 고려)."""
    return s + " " * max(0, width - _dw(s))


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_ci(pair, confidence: float) -> str:
    if pair is None:
        return ""
    lo, hi = pair
    return f", {int(round(confidence * 100))}%CI {lo * 100:.1f}–{hi * 100:.1f}%"


def _fmt_secs(seconds: Optional[float]) -> str:
    """초 단위를 사람이 읽기 좋은 문자열로 (분/시간 자동)."""
    if seconds is None:
        return "n/a"
    if seconds < 90:
        return f"{seconds:.0f}초"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.1f}분"
    return f"{minutes / 60.0:.1f}시간"


def build_report(
    events: List[Event],
    *,
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    top: int = 10,
    confidence: float = 0.95,
) -> str:
    a = analyze(
        events,
        gap_seconds=gap_seconds,
        retention_days=retention_days,
        funnel_steps=funnel_steps,
        confidence=confidence,
    )
    return render_text(a, top=top)


def render_text(a: Analysis, *, top: int = 10) -> str:
    lines: List[str] = []
    add = lines.append

    add("=" * 64)
    add("  logflow — 사용자 이벤트 로그 분석 리포트")
    add("=" * 64)
    _overview(add, a)
    _events_section(add, a.event_stats, top)
    _active_section(add, a.active, a.stickiness)
    _retention_section(add, a.retention, a.confidence, a.retention_mode)
    if a.adherence is not None:
        _adherence_section(add, a.adherence)
    if a.funnel:
        _funnel_section(add, a.funnel, a.confidence)
    _activity_section(add, a)
    if a.groups is not None:
        _groups_section(add, a.groups)
    _top_users_section(add, a.user_stats, top)
    add("")
    return "\n".join(lines)


def _fmt_p(p: Optional[float]) -> str:
    """p 값 표기 — 아주 작으면 '<0.001', 그 외는 3자리."""
    if p is None:
        return "n/a"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _fmt_num(x: Optional[float], nd: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


# 리포트 본문의 목표 표시 폭 (터미널 기본 80칸보다 약간 넓게).
_WRAP_WIDTH = 96


def _label(s: str, limit: int = 24) -> str:
    """군 라벨 등 사용자 데이터를 표에 넣기 안전한 한 줄로.

    줄바꿈/제어문자가 든 라벨은 표를 깨뜨리고, 아주 긴 라벨은 줄을 무한정 늘린다.
    (JSON·CSV 출력은 원본을 그대로 담으므로 여기서만 표시용으로 다듬는다.)
    """
    flat = " ".join(str(s).split())
    if _dw(flat) <= limit:
        return flat
    out = ""
    for ch in flat:
        if _dw(out + ch) > limit - 1:
            break
        out += ch
    return out + "…"


def _split_long(word: str, width: int) -> List[str]:
    """공백이 없는 초장문 토큰(예: 300자 군 라벨)을 표시폭 기준으로 강제 분할."""
    chunks, cur = [], ""
    for ch in word:
        if _dw(cur + ch) > width:
            chunks.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _wrap(text: str, width: int = _WRAP_WIDTH, indent: str = "    ") -> List[str]:
    """표시 폭 기준 줄바꿈 (한글 전각 2칸 반영). 공백 없는 긴 토큰도 잘라 넘긴다."""
    words = []
    for w in text.split():
        words.extend(_split_long(w, width) if _dw(w) > width else [w])
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        if _dw(cur) + 1 + _dw(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = indent + w
    lines.append(cur)
    return lines


def _groups_section(add, g):
    """군(arm) 비교 — 기술통계 · 비율 검정 · 분포 검정 · 이탈 생존."""
    add("")
    has_tests = bool(g.proportions or g.distributions or g.survival)
    label = f"[ 군 비교 ] (열: {_label(g.group_col)}"
    # 기준군은 실제로 비교에 쓰였을 때만 표시한다 (3군 이상이면 쓰이지 않는다).
    if has_tests and g.compare_b:
        label += f", 기준군: {_label(g.compare_b)}"
    add(label + ")")

    add("  " + _lj("군", 14) + _rj("사용자", 8) + _rj("이벤트", 8) + _rj("세션", 8)
        + _rj("이벤트/명", 12) + _rj("세션/명", 10) + _rj("사용시간/명", 14)
        + _rj("활성일/명", 12))
    for s in g.arms:
        add("  " + _lj(_label(s.group, 14), 14) + _rj(str(s.n_users), 8) + _rj(str(s.n_events), 8)
            + _rj(str(s.n_sessions), 8)
            + _rj(_fmt_num(s.median_events_per_user), 12)
            + _rj(_fmt_num(s.median_sessions_per_user), 10)
            + _rj(_fmt_num(s.median_minutes_per_user) + "분", 14)
            + _rj(_fmt_num(s.median_active_days), 12))
    add("  * 군별 값은 모두 사용자당 중앙값입니다 (한 사용자가 여러 번 세어지지 않도록).")
    add("  * 사용시간 = 각 세션의 (첫→마지막 이벤트) 시간의 합. 이벤트가 하나뿐인 세션은")
    add("    0분으로 잡히며, 세션과 세션 사이의 시간은 포함하지 않습니다.")
    # 군이 3개 이상이면 검정 표가 없으므로, 군별 준수도는 여기에 한 줄로 남긴다.
    adh_arms = [s for s in g.arms if s.adherence is not None]
    if adh_arms:
        parts = []
        for s in adh_arms:
            ok, n = s.adherence
            share = f"{ok}/{n}" + (f" ({_pct(ok / n)})" if n else "")
            parts.append(f"{_label(s.group, 14)} {share}")
        for i, line in enumerate(_wrap("* 프로토콜 준수 참여자: " + " · ".join(parts),
                                       _WRAP_WIDTH - 2, indent="  ")):
            add("  " + line)

    if g.proportions:
        add("")
        add(f"  ── 비율 비교 ({_label(g.compare_a)} − {_label(g.compare_b)}, "
            f"위험차 {int(round(g.confidence * 100))}%CI: Newcombe / p: Fisher exact) ──")
        add("  " + _lj("지표", 26) + _rj(_label(g.compare_a, 12), 12)
            + _rj(_label(g.compare_b, 12), 12)
            + _rj("차이", 9) + _lj(f"  {int(round(g.confidence * 100))}%CI", 20)
            + _rj("p", 8) + _rj("p(Holm)", 10))
        for t in g.proportions:
            ci = f"  [{t.diff.ci[0] * 100:+.1f}, {t.diff.ci[1] * 100:+.1f}]%p"
            add("  " + _lj(t.label, 26)
                + _rj(f"{t.successes_a}/{t.n_a}", 12)
                + _rj(f"{t.successes_b}/{t.n_b}", 12)
                + _rj(f"{t.diff.diff * 100:+.1f}%p", 9)
                + _lj(ci, 20)
                + _rj(_fmt_p(t.p_value), 8) + _rj(_fmt_p(t.p_adjusted), 10))

    if g.distributions:
        add("")
        add("  ── 분포 비교 (사용자당 값, Mann-Whitney U · 효과크기 rank-biserial) ──")
        add("  " + _lj("지표", 26) + _rj(_label(g.compare_a, 12), 12)
            + _rj(_label(g.compare_b, 12), 12)
            + _rj("효과크기", 10) + _rj("p", 8) + _rj("p(Holm)", 10))
        for t in g.distributions:
            add("  " + _lj(f"{t.label}({t.unit})", 26)
                + _rj(_fmt_num(t.result.median1), 12)
                + _rj(_fmt_num(t.result.median2), 12)
                + _rj(f"{t.result.rank_biserial:+.2f}", 10)
                + _rj(_fmt_p(t.result.p), 8) + _rj(_fmt_p(t.p_adjusted), 10))
        add("  * 표시값은 중앙값. 효과크기 +는 첫 군이 큼, −는 작음 (0=차이 없음).")
        small = [t for t in g.distributions if min(t.result.n1, t.result.n2) < 8]
        if small:
            add("  * 주의: 한 군의 n<8 이라 정규근사 p 값은 대략적입니다 — 참고용으로만.")

    if g.survival is not None:
        s = g.survival
        add("")
        add(f"  ── 이탈까지의 시간 (마지막 활동 후 {s.churn_days}일 이상 무활동 = 이탈) ──")
        for name, c in s.curves.items():
            med = "도달 안 함" if c.median_survival is None else f"{c.median_survival:.0f}일"
            add(f"    {_label(name)}: n={c.n}, 이탈 관찰 {c.n_events}명 "
                f"(절단 {c.n - c.n_events}명), 생존중앙값 {med}")
        if s.logrank is not None:
            add(f"    log-rank: chi2={s.logrank.chi2:.2f}, p={_fmt_p(s.logrank.p)}, "
                f"p(Holm)={_fmt_p(s.p_adjusted)}")
        else:
            add("    log-rank: n/a (이탈 사건이 없거나 한 군이 비어 검정 불가)")

    add("")
    if g.n_tests:
        add(f"  * 다중비교: 이 절의 검정 {g.n_tests}개를 하나의 family 로 보고")
        add("    Holm–Bonferroni 로 보정했습니다 — 판단은 p(Holm) 으로 하세요.")
        add("    (검정 개수는 --retention/--funnel/--adherence-days 에 따라 달라지므로, 보정된 p 값도")
        add("     함께 바뀝니다. 비교 설계를 먼저 정하고 돌리세요.)")
    add("  * 이 비교는 사후(post-hoc) 관찰 분석입니다. 사전에 정한 주요 평가변수가")
    add("    아니라면 확증이 아닌 탐색적 근거로 다루세요.")
    for note in g.notes:
        for i, line in enumerate(_wrap(note, _WRAP_WIDTH - 4, indent="")):
            add(("  ! " if i == 0 else "    ") + line)


def _overview(add, a: Analysis):
    events = a.events
    days = sorted({e.ts.date() for e in events})
    span = (days[-1] - days[0]).days + 1
    dur = a.session_summary.get("duration_seconds")
    eps = a.session_summary.get("events_per_session")
    add("")
    add("[ 개요 ]")
    add(f"  총 이벤트       : {len(events):,}")
    add(f"  고유 사용자     : {len(a.user_stats):,}")
    add(f"  고유 이벤트종류 : {len(a.event_stats):,}")
    add(f"  기간            : {days[0]} ~ {days[-1]}  (달력 {span}일, 활성 {len(days)}일)")
    spu = len(a.sessions) / len(a.user_stats) if a.user_stats else 0.0
    add(f"  세션 수         : {len(a.sessions):,}  (비활동 기준 {int(a.gap_seconds // 60)}분)")
    add(f"  사용자당 세션   : 평균 {spu:.1f}")
    if eps:
        add(f"  세션당 이벤트   : 평균 {eps['mean']:.1f} · 중앙값 {eps['median']:.0f}")
    if dur:
        add(f"  세션 길이       : 평균 {dur['mean'] / 60:.1f}분 · 중앙값 {dur['median'] / 60:.1f}분 "
            f"(단일이벤트 세션 제외 n={dur['n']})")
    else:
        add("  세션 길이       : n/a (모든 세션이 단일 이벤트)")


def _events_section(add, evs: List[EventStat], top: int):
    add("")
    add(f"[ 이벤트별 집계 ] (상위 {min(top, len(evs))})")
    add("  " + _lj("이벤트", 24) + _rj("발생수", 10) + _rj("고유사용자", 12))
    for s in evs[:top]:
        add("  " + _lj(s.name, 24) + _rj(f"{s.count:,}", 10) + _rj(f"{s.unique_users:,}", 12))


def _active_section(add, active: List[ActiveUsers], st: Optional[float]):
    add("")
    add("[ 활성 사용자 (DAU / WAU / MAU) ]  (WAU=7일 롤링, MAU=28일 롤링)")
    add("  " + _lj("날짜", 12) + _rj("DAU", 8) + _rj("WAU", 8) + _rj("MAU", 8))
    for a in active:
        add("  " + _lj(str(a.day), 12) + _rj(str(a.dau), 8)
            + _rj(str(a.wau), 8) + _rj(str(a.mau), 8))
    add(f"  점착도(평균DAU/평균MAU): {_pct(st)}")


def _retention_section(add, ret: List[Retention], confidence: float, mode: str = "exact"):
    add("")
    desc = "정확히 day-N 재방문" if mode == "exact" else "day-N 이후(포함) 재방문 [rolling]"
    add(f"[ 리텐션 ] (코호트=첫 활성일, {desc})")
    for r in ret:
        add(f"  day-{r.n:<3} : {_pct(r.rate):>7}  "
            f"(retained {r.retained}/{r.eligible}{_fmt_ci(r.ci, confidence)})")


def _josa(word: str, with_batchim: str, without: str) -> str:
    """한글 조사 선택 — 앞말의 받침 유무에 따라 (이/가, 을/를, 은/는).

    단위 이름이 옵션에 따라 '주'/'기간' 으로 바뀌므로, 문장이 "그 기간를" 처럼
    깨지지 않도록 조사를 계산한다.
    """
    if not word:
        return without
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return without
    return with_batchim if (ord(last) - 0xAC00) % 28 else without


def _adherence_section(add, ad):
    """프로토콜 준수도 — 참여자별 연구 주차 기준."""
    add("")
    wu = "주" if ad.period_days == 7 else "기간"
    wu_i, wu_eul, wu_neun = (_josa(wu, "이", "가"), _josa(wu, "을", "를"),
                             _josa(wu, "은", "는"))
    span = "주" if ad.period_days == 7 else f"{ad.period_days}일 기간"
    add(f"[ 프로토콜 준수도 ] (한 {span}에 {ad.min_days}일 이상 사용 = 준수, "
        f"참여자별 첫 활동일 기준)")
    def row(label: str, value: str):
        add("  " + _lj(label, 16) + ": " + value)

    if ad.observation_end is not None:
        row("관찰 종료일", f"{ad.observation_end} "
                           f"(로그 전체의 마지막 활동일 — 모든 '완전히 관찰된 "
                           f"{wu}' 판정의 기준)")
    if ad.required_weeks is not None:
        row("관찰 창", f"{ad.window_weeks}{wu} 고정(--adherence-weeks) · "
                       f"데이터가 온전히 관찰한 최대 {ad.observed_weeks}{wu}")
    else:
        row("관찰 창", f"{ad.window_weeks}{wu} "
                       f"(참여자마다 완전히 관찰된 {wu}만 · 최대 {ad.observed_weeks}{wu})")
    if ad.n_users:
        rate = ad.n_adherent_users / ad.n_users
        who = (f"{ad.required_weeks}{wu} 완주자"
               if ad.required_weeks is not None
               else f"관찰 {wu}{wu_i} 1개 이상인 참여자")
        row("준수 참여자",
            f"{ad.n_adherent_users}/{ad.n_users}  "
            f"({_pct(rate)}{_fmt_ci(ad.adherent_ci, ad.confidence)})"
            f"  ← 자기 관찰 {wu}의 {ad.target * 100:g}% 이상 준수")
        rng = ad.eligible_weeks_range
        denom = f"{who} (분모가 된 관찰 {wu} 수: "
        denom += f"{rng[0]}{wu}" if rng and rng[0] == rng[1] else f"{rng[0]}~{rng[1]}{wu}"
        row("  └ 분석 집단", denom + ")")
        row("사용자당 준수율", f"중앙값 {_pct(ad.median_user_rate)} "
                               f"(참여자별 준수 {wu}÷관찰 {wu})")
        row(f"연속 준수 {wu}", f"중앙값 {_fmt_num(ad.median_streak)}{wu} "
                               f"(참여자별 '가장 긴 연속 준수' 의 중앙값)")
    else:
        row("준수 참여자", "n/a (분모가 될 참여자가 없습니다 — 아래 ! 안내 참고)")
    if ad.weeks:
        head = "주차" if ad.period_days == 7 else "기간"
        add("  " + _lj(head, 8) + _rj("대상", 8) + _rj("준수", 8) + _rj("준수율", 10)
            + _lj(f"  {int(round(ad.confidence * 100))}%CI", 22) + _rj("활성일(중앙)", 14))
        for w in ad.weeks:
            ci = "" if w.ci is None else f"  [{w.ci[0] * 100:.1f}, {w.ci[1] * 100:.1f}]%"
            add("  " + _lj(f"{w.week}", 8) + _rj(str(w.eligible), 8)
                + _rj(str(w.adherent), 8) + _rj(_pct(w.rate), 10)
                + _lj(ci, 22) + _rj(_fmt_num(w.median_active_days), 14))
    add(f"  * 대상 = 그 {wu}{wu_eul} 온전히 관찰할 기회가 있던 참여자 (관찰 종료일에 걸친 부분")
    add(f"    {wu}{wu_neun} 분모에서 제외 — 아직 쓸 시간이 없었던 것을 미준수로 세지 않기 위함).")
    add("  * 활성일 = 이벤트가 하나라도 있던 날의 수 (하루에 몇 번 썼는지는 세지 않음).")
    add(f"    '활성일(중앙)' 은 대상 전원(그 {wu} 사용 0일인 사람 포함)의 중앙값입니다.")
    add(f"  * 뒤 {wu}의 대상은 앞 {wu} 대상의 부분집합이라, {wu} 간 준수율 추세에는")
    add("    행동 변화뿐 아니라 코호트 구성 변화가 섞여 있습니다.")
    for note in ad.notes:
        for i, line in enumerate(_wrap(note, _WRAP_WIDTH - 4, indent="")):
            add(("  ! " if i == 0 else "    ") + line)


def _funnel_section(add, steps: List[FunnelStep], confidence: float):
    add("")
    add("[ 퍼널 전환 ] (시간순 진행)")
    add("  " + _lj("단계", 22) + _rj("도달", 8) + _rj("직전대비", 10)
        + _rj("1단계대비", 12) + _rj("소요(중앙)", 14))
    for s in steps:
        t = "-" if s.median_seconds_from_prev is None else _fmt_secs(s.median_seconds_from_prev)
        add("  " + _lj(s.name, 22) + _rj(str(s.reached), 8)
            + _rj(_pct(s.step_conversion), 10) + _rj(_pct(s.overall_conversion), 12)
            + _rj(t, 14))
    add("  * 소요(중앙): 직전 단계 도달자 중 이 단계 도달자의 소요시간 중앙값(조건부).")
    # 신뢰구간은 폭이 넓어 표 아래에 별도로 (2단계부터)
    ci_lines = [
        f"    {s.name}: 직전대비 {_pct(s.step_conversion)}{_fmt_ci(s.step_ci, confidence)}"
        for s in steps[1:]
        if s.step_ci is not None
    ]
    if ci_lines:
        add(f"  ── 전환율 {int(round(confidence * 100))}% 신뢰구간 ──")
        for line in ci_lines:
            add(line)


def _activity_section(add, a: Analysis):
    add("")
    add("[ 활동 시간대 ]")
    total_h = sum(a.hour_hist)
    if a.peak_hour is not None:
        share = a.hour_hist[a.peak_hour] / total_h if total_h else 0.0
        add(f"  피크 시간대     : {a.peak_hour:02d}시 "
            f"({a.hour_hist[a.peak_hour]:,}건, 전체의 {share * 100:.0f}%)")
    add("  요일별 이벤트   : " + _bar_row(a.weekday_hist, _WEEKDAY_KO))
    add("  시간대 분포     : " + _hour_sparkline(a.hour_hist))


def _bar_row(counts: List[int], labels: List[str]) -> str:
    mx = max(counts) if counts else 0
    parts = []
    blocks = "▁▂▃▄▅▆▇█"
    for c, lab in zip(counts, labels):
        if c == 0:
            b = "·"   # 0 은 낮은 막대(▁)와 구분되도록 점으로
        elif mx == 0:
            b = "▁"
        else:
            idx = int(round(c / mx * (len(blocks) - 1)))
            b = blocks[idx]
        parts.append(f"{lab}{b}{c}")
    return "  ".join(parts)


def _hour_sparkline(counts: List[int]) -> str:
    mx = max(counts) if counts else 0
    blocks = "▁▂▃▄▅▆▇█"
    if mx == 0:
        return "0…23  " + "▁" * 24
    out = []
    for c in counts:
        out.append(blocks[int(round(c / mx * (len(blocks) - 1)))])
    return "0시 " + "".join(out) + " 23시"


def _top_users_section(add, users: List[UserStat], top: int):
    add("")
    add(f"[ 상위 사용자 ] (이벤트 수 기준 상위 {min(top, len(users))})")
    add("  " + _lj("사용자", 16) + _rj("이벤트", 8) + _rj("활성일", 8)
        + "  " + _lj("첫활동", 18) + "마지막활동")
    for u in users[:top]:
        add("  " + _lj(u.user, 16) + _rj(str(u.event_count), 8) + _rj(str(u.active_days), 8)
            + "  " + _lj(_fmt_dt(u.first_seen), 18) + _fmt_dt(u.last_seen))
