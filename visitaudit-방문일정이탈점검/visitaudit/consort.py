"""CONSORT 흐름 숫자 + PP 집합 후보.

CONSORT 는 피험자.csv 에서 계산한다. 없으면 방문기록으로 계산 가능한 부분만
내고, 못 낸 부분을 자백한다. 내적 정합성(스크리닝 = 제외 + 무작위배정 등)을
검사해 안 맞으면 경고를 낸다 — 고치지 않는다.

PP 는 어디까지나 '후보'다. 최종 PP 확정은 사람(눈가림 해제 전 데이터 검토
회의)의 몫이며, 리포트에 그렇게 명시한다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .criteria import CriteriaResult
from .judge import JudgeResult, V_IN, V_MISSING, V_OUT, V_UNJUDGEABLE
from .protocol import Protocol
from .tables import Subject

R_DAYS = "창이탈 {n}일초과"
R_MISSING = "필수방문결측"
R_ELIG = "선정기준위반"
R_DROP = "탈락"


@dataclass
class PPEntry:
    subject: str
    status: str                    # "후보" | "제외" | "판정불가"
    reasons: List[str] = field(default_factory=list)
    caveat: str = ""               # "기준판정불가"·"방문판정불가"(·으로 연결) — 후보인데 판정 못 한 항목이 남은 경우


@dataclass
class PPResult:
    entries: Dict[str, PPEntry] = field(default_factory=dict)
    reason_counts: List[Tuple[str, int]] = field(default_factory=list)
    n_candidates: int = 0
    n_excluded: int = 0
    n_unjudgeable: int = 0
    n_caveat_candidates: int = 0   # 후보 중 '판정 못 한 항목 있음' 표시가 붙은 수
    n_dedup_removed: int = 0       # 사유 합계 - 제외 인원수
    skipped: Optional[str] = None


def _dropped_asof(s: Subject, as_of) -> bool:
    """이 기준시점(as-of)에서 탈락으로 볼 것인가.

    탈락일이 as-of 이후면 아직 탈락하지 않았다(C2 — as-of 재현성).
    탈락일이 파싱 불가면 기재는 있으므로 탈락으로 세되, 그 피험자는
    judge 쪽에서 이미 판정불가로 강등되어 있다.
    """
    if s.dropout is not None:
        return s.dropout <= as_of
    return bool(s.dropout_raw)


@dataclass
class Consort:
    available: bool = False
    n_screened: int = 0
    n_excluded: int = 0
    excluded_reasons: List[Tuple[str, int]] = field(default_factory=list)
    n_randomized: int = 0
    arms: List[str] = field(default_factory=list)
    arm_counts: Dict[str, int] = field(default_factory=dict)
    arm_completed: Dict[str, int] = field(default_factory=dict)
    arm_ongoing: Dict[str, int] = field(default_factory=dict)
    arm_dropout: Dict[str, int] = field(default_factory=dict)
    arm_dropout_reasons: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    n_itt: int = 0
    # (설명, 통과여부, 독립검증인가)
    # 독립검증이 아닌 항목은 같은 목록을 갈라 센 '합계 확인'이라 정의상 어긋날 수
    # 없다. 어긋날 수 없는 검사를 '✓ 정합성'으로 나란히 보여 주면 실제로는 아무것도
    # 확인해 주지 않으면서 확인해 준 것처럼 읽히므로, 둘을 구분해서 표시한다.
    checks: List[Tuple[str, bool, bool]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # 피험자.csv 없을 때의 축소판
    fallback_subjects: int = 0
    fallback_last_visit_done: int = 0


def _wide_len(text: str) -> int:
    """터미널 표시 폭. 한글·전각 문자는 두 칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _wide_pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _wide_len(text))


def _tally(pairs: List[str]) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    order: List[str] = []
    for p in pairs:
        label = p.strip() or "사유 미기재"
        if label not in counts:
            counts[label] = 0
            order.append(label)
        counts[label] += 1
    return [(label, counts[label]) for label in order]


def build_consort(subjects: Optional[List[Subject]], judged: JudgeResult,
                  protocol: Protocol, as_of) -> Consort:
    """CONSORT 흐름도 숫자(스크리닝→제외→배정→군별 완료/진행중/탈락→ITT).

    피험자.csv 가 없으면 계산 가능한 부분만 내고 무엇을 건너뛰었는지 남긴다.
    정합성 검사는 '합계 확인'(같은 목록을 갈라 센 항등식)과 '교차 검증'(judge 가
    독립적으로 센 숫자와 대조 — 실제로 어긋날 수 있는 것)을 구분해 담는다.
    """
    c = Consort()
    last_visit = protocol.visits[-1].name

    if subjects is None:
        c.available = False
        c.notes.append("피험자.csv 없음 — 스크리닝/제외/군별/탈락 숫자는 계산할 수 없습니다")
        c.fallback_subjects = len(judged.universe)
        done = set()
        for s in judged.slots:
            if s.visit.name == last_visit and s.verdict in (V_IN, V_OUT):
                done.add(s.subject)
        c.fallback_last_visit_done = len(done)
        c.n_itt = len(judged.universe)
        return c

    c.available = True
    unique: List[Subject] = []
    seen = set()
    for s in subjects:
        if s.sid in seen:
            continue
        seen.add(s.sid)
        unique.append(s)
    if len(unique) != len(subjects):
        c.notes.append(f"피험자.csv 중복 기재 {len(subjects) - len(unique)}행 — 첫 행 기준으로 세되, 해당 피험자는 판정불가")

    # as-of 시점에 아직 등록되지 않은 사람은 이 기준시점의 CONSORT 에 없다.
    # judge 가 판정 대상에서 뺀 사람을 CONSORT 만 세면, 한 페이지 안에서
    # 무작위배정 N 과 등록곡선 N 이 어긋난다(교차 검증도 같이 깨진다).
    not_yet = set(judged.not_yet_enrolled)
    if not_yet:
        unique = [s for s in unique if s.sid not in not_yet]
        c.notes.append(f"as-of 시점 미등록 {len(not_yet)}명 — 이 기준시점의 CONSORT 에서 제외")

    c.n_screened = len(unique)
    randomized = [s for s in unique if s.randomized]
    screenfail = [s for s in unique if not s.randomized]
    c.n_excluded = len(screenfail)
    c.excluded_reasons = _tally([s.screenfail_reason for s in screenfail])
    c.n_randomized = len(randomized)
    c.n_itt = len(randomized)

    # 마지막 프로토콜 방문의 기록(창내/창이탈)이 있으면 '완료'
    done_last = set()
    for s in judged.slots:
        if s.visit.name == last_visit and s.verdict in (V_IN, V_OUT):
            done_last.add(s.subject)

    for s in randomized:
        arm = s.arm.strip()
        if arm not in c.arm_counts:
            c.arms.append(arm)
            c.arm_counts[arm] = 0
            c.arm_completed[arm] = 0
            c.arm_ongoing[arm] = 0
            c.arm_dropout[arm] = 0
            c.arm_dropout_reasons[arm] = []
        c.arm_counts[arm] += 1
        if _dropped_asof(s, as_of):
            c.arm_dropout[arm] += 1
        elif s.sid in done_last:
            c.arm_completed[arm] += 1
        else:
            c.arm_ongoing[arm] += 1
    for arm in c.arms:
        reasons = [s.dropout_reason for s in randomized
                   if s.arm.strip() == arm and _dropped_asof(s, as_of)]
        c.arm_dropout_reasons[arm] = _tally(reasons) if reasons else []
    n_future = sum(1 for s in randomized
                   if s.dropout is not None and s.dropout > as_of)
    if n_future:
        c.notes.append(f"as-of 이후 탈락일 {n_future}명 — 이번 기준시점에서는 미탈락(완료/진행중)으로 분류")

    # ── 내적 정합성 ────────────────────────────────────────────────
    c.checks.append((f"스크리닝({c.n_screened}) = 제외({c.n_excluded}) + 무작위배정({c.n_randomized})",
                     c.n_screened == c.n_excluded + c.n_randomized, False))
    arm_sum = sum(c.arm_counts.values())
    c.checks.append((f"무작위배정({c.n_randomized}) = 군 합계({arm_sum})",
                     c.n_randomized == arm_sum, False))
    for arm in c.arms:
        total = c.arm_completed[arm] + c.arm_ongoing[arm] + c.arm_dropout[arm]
        c.checks.append((f"{arm or '(군 미기재)'}: 완료+진행중+탈락({total}) = 배정({c.arm_counts[arm]})",
                         total == c.arm_counts[arm], False))
    # 서로 다른 모듈이 독립적으로 센 숫자의 교차 검증 — 어긋나면 데이터가 애매하다는 뜻.
    # 위의 합계 확인들과 달리 이것만 실제로 실패할 수 있다.
    c.checks.append((f"ITT({c.n_itt}) = 판정 대상 피험자({len(judged.universe)})",
                     c.n_itt == len(judged.universe), True))
    return c


def build_pp(judged: JudgeResult, crit: CriteriaResult,
             subjects: Optional[List[Subject]], protocol: Protocol, as_of) -> PPResult:
    """PP(per-protocol) 집합 **후보**와 제외 사유. 확정이 아니다.

    프로토콜의 PP제외규칙을 기계적으로 적용하고, 한 사람이 여러 사유에 걸리면
    1명으로 센다("중복 N명 제거"). 판정하지 못한 항목이 남은 후보에는 표시를
    붙인다 — 표시 없이 후보에 오르면 PP 숫자가 조용히 틀릴 수 있기 때문.
    """
    pp = PPResult()
    rules = protocol.pp_rules
    if rules is None:
        pp.skipped = "프로토콜에 PP제외규칙이 없음 — PP 후보 산출 생략"
        return pp

    subj_map: Dict[str, Subject] = {}
    if subjects is not None:
        for s in subjects:
            subj_map.setdefault(s.sid, s)
    violators = set(crit.violators()) if crit is not None else set()
    # 선정/제외기준을 판정 못 한 피험자(값 없음·해석 불가) — 후보로 두되 표시한다
    crit_unjudgeable = ({f.subject for f in crit.unjudgeable}
                        if crit is not None and crit.skipped is None else set())
    slots_map = judged.slots_by_subject()

    # 사유 라벨 → 등장 순서 유지 카운트
    reason_order: List[str] = []
    reason_counts: Dict[str, int] = {}

    def add_reason(entry: PPEntry, label: str) -> None:
        entry.reasons.append(label)
        if label not in reason_counts:
            reason_counts[label] = 0
            reason_order.append(label)
        reason_counts[label] += 1

    for sid in judged.universe:
        entry = PPEntry(subject=sid, status="후보")
        if sid in judged.subject_unjudgeable:
            entry.status = "판정불가"
            entry.reasons.append(judged.subject_unjudgeable[sid])
            pp.entries[sid] = entry
            pp.n_unjudgeable += 1
            continue

        slots = slots_map.get(sid, [])
        if rules.max_days_out is not None:
            worst = max((abs(s.days_out) for s in slots
                         if s.verdict == V_OUT and s.days_out is not None), default=0)
            if worst > rules.max_days_out:
                add_reason(entry, R_DAYS.format(n=rules.max_days_out))
        if rules.missing_required and any(s.verdict == V_MISSING for s in slots):
            add_reason(entry, R_MISSING)
        if rules.eligibility_violation and sid in violators:
            add_reason(entry, R_ELIG)
        subj = subj_map.get(sid)
        if rules.dropout and subj is not None and _dropped_asof(subj, as_of):
            add_reason(entry, R_DROP)

        if entry.reasons:
            entry.status = "제외"
            pp.n_excluded += 1
        else:
            pp.n_candidates += 1
            # 후보로 남았지만 판정하지 못한 구석이 있으면 표시해 둔다. 방문 하나가
            # 판정불가인 채로 아무 표시 없이 후보에 오르면, 그 방문이 실은 창을
            # 크게 벗어났을 경우 PP 숫자가 조용히 틀린다.
            marks = []
            if sid in crit_unjudgeable:
                marks.append("기준판정불가")
            if any(s.verdict == V_UNJUDGEABLE for s in slots):
                marks.append("방문판정불가")
            if marks:
                entry.caveat = "·".join(marks)
                pp.n_caveat_candidates += 1
        pp.entries[sid] = entry

    total_reason_hits = sum(reason_counts.values())
    pp.n_dedup_removed = total_reason_hits - pp.n_excluded
    pp.reason_counts = [(label, reason_counts[label]) for label in reason_order]
    return pp


def consort_text(c: Consort, pp: PPResult, protocol: Protocol, as_of_iso: str) -> str:
    """CONSORT.txt 용 텍스트 흐름도."""
    lines: List[str] = []
    lines.append(f"CONSORT 흐름 — {protocol.study}   기준시점(as-of): {as_of_iso}")
    lines.append("(visitaudit 이 계산한 판정치이며, 확정 숫자가 아닙니다)")
    lines.append("")
    if not c.available:
        lines.append("피험자.csv 없음 — 계산 가능한 부분만 표시합니다.")
        lines.append(f"  방문기록 기준 피험자: {c.fallback_subjects}명")
        lines.append(f"  마지막 프로토콜 방문({protocol.visits[-1].name}) 기록 보유: {c.fallback_last_visit_done}명")
        if pp.skipped is None:
            lines.append(f"  PP 후보 {pp.n_candidates}명 (제외 {pp.n_excluded} / 판정불가 {pp.n_unjudgeable})")
        return "\n".join(lines) + "\n"

    lines.append(f"        스크리닝 {c.n_screened}")
    lines.append("            │")
    reason_txt = " / ".join(f"{r} {n}" for r, n in c.excluded_reasons) or "-"
    lines.append(f"            ├─ 제외 {c.n_excluded}  ({reason_txt})")
    lines.append("            ▼")
    lines.append(f"        무작위배정 {c.n_randomized}")
    if c.arms:
        if len(c.arms) == 2:
            lines.append("      ┌" + "─" * 9 + "┴" + "─" * 9 + "┐")
        # 회의록에 붙여 넣는 그림이라 열을 맞춘다. 한글은 터미널에서 두 칸을
        # 차지하므로 len() 이 아니라 표시 폭으로 채워야 줄이 맞는다.
        cols = [[f"{arm or '(군 미기재)'} {c.arm_counts[arm]}",
                 f"완료 {c.arm_completed[arm]}",
                 f"진행중 {c.arm_ongoing[arm]}"] for arm in c.arms]
        widths = [max(_wide_len(cell) for cell in col) for col in cols]
        for row in range(3):
            bits = [_wide_pad(cols[i][row], widths[i]) for i in range(len(cols))]
            lines.append("   " + "   ".join(bits).rstrip())
        drop_bits = []
        for arm in c.arms:
            r = c.arm_dropout_reasons.get(arm) or []
            detail = (" (" + ", ".join(f"{lbl} {n}" for lbl, n in r) + ")") if r else ""
            drop_bits.append(f"탈락 {c.arm_dropout[arm]}{detail}")
        # 탈락 줄만 사유가 붙어 길이가 들쭉날쭉하므로, 마지막 칸은 채우지 않는다
        padded = [_wide_pad(b, widths[i]) if i < len(drop_bits) - 1 else b
                  for i, b in enumerate(drop_bits)]
        lines.append("   " + "   ".join(padded).rstrip())
    lines.append("")
    lines.append(f"        ITT {c.n_itt}")
    if pp.skipped is None:
        detail = ", ".join(f"{lbl} {n}" for lbl, n in pp.reason_counts)
        dedup = f" — 중복 {pp.n_dedup_removed}명 제거" if pp.n_dedup_removed > 0 else ""
        unj = f" / 판정불가 {pp.n_unjudgeable}" if pp.n_unjudgeable else ""
        why = f": {detail}{dedup}" if detail else ""
        lines.append(f"        PP 후보 {pp.n_candidates}  (제외 {pp.n_excluded}{why}{unj})")
        if pp.n_caveat_candidates:
            lines.append(f"        ※ 후보 {pp.n_candidates}명 중 {pp.n_caveat_candidates}명은 "
                         f"판정하지 못한 항목이 있음 — '후보(사유)'로 표기")
    else:
        lines.append(f"        PP: {pp.skipped}")
    lines.append("")
    lines.append("정합성 검사:")
    for desc, ok, indep in c.checks:
        tag = "교차검증" if indep else "합계확인"
        lines.append(f"  {'✓' if ok else '✗ 경고'}  [{tag}] {desc}")
    itt_ok = pp.skipped is not None or c.n_itt >= pp.n_candidates
    lines.append(f"  {'✓' if itt_ok else '✗ 경고'}  ITT({c.n_itt}) ≥ PP 후보({pp.n_candidates if pp.skipped is None else '-'})")
    for note in c.notes:
        lines.append(f"  ※ {note}")
    return "\n".join(line for line in lines if line is not None) + "\n"
