"""방문창 판정 엔진.

원칙 (스펙의 '절대 버리면 안 되는 2가지'):
  1. 미도래(창 미마감)·탈락후(해당없음)는 절대 이탈로 세지 않는다.
  2. 판정 못 한 것은 조용히 통과시키지 않는다 — 전부 사유별로 센다.

규칙은 좁게: 애매하면 이탈이 아니라 판정불가.

창 경계 포함 규칙: 창 [-3, 3] 이면 예정일-3일과 예정일+3일은 **창 안**이다.
미도래 규칙: 창 종료일이 as-of **이후이거나 같으면**(당일 방문이 아직 가능)
미도래다. 즉 창 종료일 < as-of 인 방문만 '창이 닫혔다'고 본다.

판정률(coverage rate) 정의:
  판정률 = 판정완료 / (판정완료 + 판정불가)
  미도래·해당없음(탈락/선택방문)은 *정당한* 판정 제외이므로 분모에 넣지 않는다.
  (넣으면 등록 초기의 젊은 시험이 항상 임계 미달 exit 3 이 되어 툴이 죽는다.)
  분모가 0이면(전부 미도래 등) 판정률은 계산 불가 — 임계 검사를 통과로 두고 자백한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .protocol import Protocol, VisitDef
from .tables import Subject, VisitRecord

# slot verdict
V_IN = "창내"
V_OUT = "창이탈"
V_MISSING = "결측"
V_PENDING = "미도래"
V_NA_DROPOUT = "해당없음(탈락)"
V_NA_OPTIONAL = "해당없음(선택방문)"
V_UNJUDGEABLE = "판정불가"

COMPLETED = {V_IN, V_OUT, V_MISSING}
EXCLUDED = {V_PENDING, V_NA_DROPOUT, V_NA_OPTIONAL}

# 데이터 오류 종류
E_DUP = "중복 방문행"
E_PARSE = "날짜 파싱 실패"
E_FUTURE = "미래 날짜"
E_PREENROLL = "등록일 이전"
E_SUBJECT = "피험자 판정불가"


@dataclass
class Slot:
    subject: str
    visit: VisitDef
    scheduled: Optional[dt.date] = None
    win_start: Optional[dt.date] = None
    win_end: Optional[dt.date] = None
    verdict: str = V_UNJUDGEABLE
    actual: Optional[dt.date] = None
    days_out: Optional[int] = None   # 창밖일수: 늦으면 +, 이르면 -
    error_kind: Optional[str] = None
    detail: str = ""


@dataclass
class Deviation:
    subject: str
    visit_name: str
    kind: str                       # "창이탈" | "필수방문결측" | "순서위반"
    scheduled: Optional[dt.date]
    win_start: Optional[dt.date]
    win_end: Optional[dt.date]
    actual: Optional[dt.date]
    days_out: Optional[int]
    severity: str                   # "경미" | "중대" | "미정"(임계 미정의)
    evidence: str


@dataclass
class DataError:
    subject: str
    visit_name: str
    kind: str
    detail: str


@dataclass
class JudgeResult:
    slots: List[Slot] = field(default_factory=list)
    deviations: List[Deviation] = field(default_factory=list)
    data_errors: List[DataError] = field(default_factory=list)
    subject_unjudgeable: Dict[str, str] = field(default_factory=dict)  # sid -> 사유
    universe: List[str] = field(default_factory=list)   # 판정 대상 피험자 (순서 유지)
    notes: List[str] = field(default_factory=list)      # 자백 각주
    warnings: List[str] = field(default_factory=list)   # 정합성 경고
    n_rows_seen: int = 0
    n_time_rows: int = 0
    n_planned_rows: int = 0
    n_notdone_rows: int = 0
    n_extra_visit_rows: int = 0
    n_nonuniverse_rows: int = 0
    n_future_booked: int = 0    # 창 마감 전 미래 날짜(예약 기재) — 미도래로 처리한 건수
    not_yet_enrolled: List[str] = field(default_factory=list)  # as-of 시점 미등록 — 판정 대상 제외

    _slots_cache: Optional[Dict[str, List[Slot]]] = field(
        default=None, repr=False, compare=False)
    _devs_cache: Optional[Dict[str, List["Deviation"]]] = field(
        default=None, repr=False, compare=False)

    def slots_by_subject(self) -> Dict[str, List[Slot]]:
        """피험자 → 슬롯 목록. 한 번만 만들어 재사용 (피험자별 재스캔은 O(n²))."""
        if self._slots_cache is None:
            cache: Dict[str, List[Slot]] = {}
            for s in self.slots:
                cache.setdefault(s.subject, []).append(s)
            self._slots_cache = cache
        return self._slots_cache

    def deviations_by_subject(self) -> Dict[str, List["Deviation"]]:
        if self._devs_cache is None:
            cache: Dict[str, List[Deviation]] = {}
            for d in self.deviations:
                cache.setdefault(d.subject, []).append(d)
            self._devs_cache = cache
        return self._devs_cache

    # ── 커버리지 산술 ──────────────────────────────────────────────
    def count(self, verdict: str) -> int:
        return sum(1 for s in self.slots if s.verdict == verdict)

    @property
    def n_slots(self) -> int:
        return len(self.slots)

    @property
    def n_completed(self) -> int:
        return sum(1 for s in self.slots if s.verdict in COMPLETED)

    @property
    def n_unjudgeable(self) -> int:
        return self.count(V_UNJUDGEABLE)

    @property
    def n_excluded(self) -> int:
        return self.n_slots - self.n_completed

    def error_count(self, kind: str) -> int:
        return sum(1 for s in self.slots
                   if s.verdict == V_UNJUDGEABLE and s.error_kind == kind)

    @property
    def coverage_rate(self) -> Optional[float]:
        """판정완료 / (판정완료 + 판정불가). 분모 0 이면 None."""
        denom = self.n_completed + self.n_unjudgeable
        if denom == 0:
            return None
        return 100.0 * self.n_completed / denom

    def deviations_of(self, kind: str) -> List[Deviation]:
        return [d for d in self.deviations if d.kind == kind]


def _severity(kind: str, days_out: Optional[int], protocol: Protocol) -> str:
    """창이탈은 PP 제외 기준(창이탈일수초과)을 넘으면 중대, 아니면 경미.
    결측·순서위반은 중대.

    임계가 정의돼 있지 않으면 '경미'가 아니라 '미정'이다 — 기준이 없는데 경미로
    적으면 86일 지각도 가벼운 일로 읽힌다.
    """
    if kind != "창이탈":
        return "중대"
    threshold = None
    if protocol.pp_rules is not None:
        threshold = protocol.pp_rules.max_days_out
    if threshold is None:
        return "미정"
    if days_out is not None and abs(days_out) > threshold:
        return "중대"
    return "경미"


def judge(records: List[VisitRecord], subjects: Optional[List[Subject]],
          protocol: Protocol, as_of: dt.date) -> JudgeResult:
    """방문 기록을 프로토콜에 대조해 슬롯마다 판정한다 — 이 툴의 심장.

    각 피험자 × 프로토콜 방문마다 슬롯을 하나 만들고, 기준방문일에서 예정일과
    창을 계산해 창내/창이탈/미도래/해당없음/결측/판정불가 중 하나로 가른다.
    미도래(창 미마감)와 탈락 이후는 **이탈로 세지 않는다** — 이 규칙이 무너지면
    등록 단계 시험에서 첫 실행에 이탈이 수십 건 뜨고 툴은 두 번 다시 안 열린다.
    애매하면(중복 행·깨진 날짜·기준방문 결측) 추측하지 않고 판정불가로 보낸다.
    """
    res = JudgeResult()
    res.n_rows_seen = len(records)
    res.n_time_rows = sum(1 for r in records if r.had_time)

    subj_map: Dict[str, Subject] = {}
    if subjects is not None:
        for s in subjects:
            subj_map.setdefault(s.sid, s)

    # ── 판정 대상 피험자(universe) 결정 ─────────────────────────────
    # 순서는 유지하되 중복 판정은 집합으로 — 리스트 membership 은 O(n²) 라
    # 수만 행짜리 트래커에서 그대로 체감된다.
    visit_sids: List[str] = []
    seen_sids = set()
    for r in records:
        if r.subject not in seen_sids:
            seen_sids.add(r.subject)
            visit_sids.append(r.subject)
    if subjects is None:
        res.universe = list(visit_sids)
        res.notes.append("피험자.csv 없음 — 방문기록의 모든 피험자를 무작위배정된 것으로 간주하고 판정 "
                         "(군·탈락·등록일 정보 없음: 결측 중 일부는 실제로는 중도탈락일 수 있음)")
    else:
        res.universe = [s.sid for s in subjects if s.randomized and not s.duplicated]
        # 중복 기재 피험자: 무작위배정 여부가 불확실할 때만 판정불가로 강등한다.
        # 중복된 행이 모두 군 미기재(= 스크린 실패)로 일치하면 불확실하지 않다 —
        # 그런 사람까지 ITT 판정 대상에 넣으면 판정률만 깎여 애먼 exit 3 이 뜨고,
        # CONSORT(첫 행 기준)와 어긋나 제 손으로 만든 정합성 경고까지 뜬다.
        any_arm: Dict[str, bool] = {}
        for s in subjects:
            if s.duplicated:
                any_arm[s.sid] = any_arm.get(s.sid, False) or s.randomized
        for sid, has_arm in any_arm.items():
            if has_arm and sid not in res.subject_unjudgeable:
                res.subject_unjudgeable[sid] = "피험자.csv 중복 기재"
                if sid not in res.universe:
                    res.universe.append(sid)
        known_sids = {s.sid for s in subjects}
        outside = [sid for sid in visit_sids if sid not in known_sids]
        if outside:
            outside_set = set(outside)
            n_rows = sum(1 for r in records if r.subject in outside_set)
            res.n_nonuniverse_rows += n_rows
            res.warnings.append(
                f"피험자.csv 에 없는 피험자 {len(outside)}명({', '.join(outside[:5])}"
                + (" …" if len(outside) > 5 else "") + f")의 방문 {n_rows}건 — 판정 제외")
        not_rand = [s.sid for s in subjects
                    if not s.randomized and not s.duplicated and s.sid in seen_sids]
        if not_rand:
            not_rand_set = set(not_rand)
            n_rows = sum(1 for r in records if r.subject in not_rand_set)
            res.n_nonuniverse_rows += n_rows
            res.warnings.append(
                f"무작위배정 기록이 없는(군 미기재) 피험자 {len(not_rand)}명의 방문 {n_rows}건 — 판정 제외")

    # ── 행 분류 자백 ────────────────────────────────────────────────
    res.n_planned_rows = sum(1 for r in records if r.status_kind == "planned")
    res.n_notdone_rows = sum(1 for r in records if r.status_kind == "notdone")
    visit_names = set(protocol.visit_names())
    universe_set = set(res.universe)   # 행마다 set() 을 다시 만들면 O(n·m) 이 된다
    extra_rows = [r for r in records if r.visit not in visit_names and r.subject in universe_set]
    res.n_extra_visit_rows = len(extra_rows)
    if extra_rows:
        names = sorted({r.visit for r in extra_rows})
        res.notes.append(f"프로토콜에 없는 방문명 {len(extra_rows)}건({', '.join(names[:5])}"
                         + (" …" if len(names) > 5 else "") + ") — 판정 대상 아님")
    if res.n_planned_rows:
        res.notes.append(f"'예정' 상태 행 {res.n_planned_rows}건 — 기록으로 세지 않음")
    if res.n_notdone_rows:
        res.notes.append(f"'미실시/취소' 상태 행 {res.n_notdone_rows}건 — 기록으로 세지 않고, 창이 닫혔으면 결측으로 판정")
    if res.n_time_rows:
        res.notes.append(f"시각 정보 {res.n_time_rows}건 발견 — 날짜 단위로만 판정함")

    # ── 피험자별 기록 묶기 (record 상태만) ──────────────────────────
    groups: Dict[str, Dict[str, List[VisitRecord]]] = {}
    for r in records:
        if r.status_kind != "record":
            continue
        groups.setdefault(r.subject, {}).setdefault(r.visit, []).append(r)

    # ── as-of 격리·데이터 모순 자백 (C2/C3/B6) ──────────────────────
    # 탈락일이 as-of 이후 → 이번 기준시점에서는 '아직 탈락하지 않음'.
    # 등록일이 as-of 이후 → 등록일-이전 검사에 쓰지 않음(등록 곡선 쪽에서 별도 자백).
    n_future_dropout = sum(
        1 for sid in res.universe
        if (s := subj_map.get(sid)) is not None
        and s.dropout is not None and s.dropout > as_of)
    if n_future_dropout:
        res.notes.append(f"as-of 이후 탈락일 {n_future_dropout}명 — 이번 기준시점(as-of)에서는 미탈락으로 처리")
    enroll_err_sids = [sid for sid in res.universe
                       if (s := subj_map.get(sid)) is not None and s.enroll_error is not None]
    if enroll_err_sids:
        shown = ", ".join(enroll_err_sids[:5]) + (" …" if len(enroll_err_sids) > 5 else "")
        res.notes.append(f"등록일 해석 불가 {len(enroll_err_sids)}명({shown}) — '등록일 이전 방문' 검사를 건너뜀")

    # ── 피험자 단위 판정 ────────────────────────────────────────────
    # as-of 시점에 아직 등록되지 않은 피험자는 판정 대상이 아니다. 과거 기준일로
    # 다시 돌려 그때 보고한 숫자를 재현하는 것이 이 툴의 용도인데, 그때 아직
    # 들어오지도 않은 사람을 '판정불가'로 세면 판정률이 무너져 exit 3 이 뜨고
    # CONSORT 의 N 과 등록곡선의 N 이 한 페이지에서 어긋난다.
    not_yet: List[str] = []
    for sid in res.universe:
        subj = subj_map.get(sid)
        anchor_recs = groups.get(sid, {}).get(protocol.anchor, [])
        anchor_after = (len(anchor_recs) == 1 and anchor_recs[0].date is not None
                        and anchor_recs[0].date > as_of)
        enroll_after = (subj is not None and subj.enroll is not None and subj.enroll > as_of)
        if anchor_after or enroll_after:
            not_yet.append(sid)
    if not_yet:
        skip = set(not_yet)
        res.universe = [s for s in res.universe if s not in skip]
        for sid in not_yet:
            res.subject_unjudgeable.pop(sid, None)
        res.not_yet_enrolled = list(not_yet)
        res.notes.append(
            f"as-of 시점 미등록 {len(not_yet)}명 — 이 기준시점에는 아직 시험에 들어오지 "
            f"않아 판정 대상에서 제외({', '.join(not_yet[:6])}"
            + (" …" if len(not_yet) > 6 else "") + ")")

    for sid in res.universe:
        subj = subj_map.get(sid)
        recs = groups.get(sid, {})
        reason = res.subject_unjudgeable.get(sid)

        if reason is None and subj is not None and subj.dropout_error is not None:
            reason = f"탈락일 해석 불가({subj.dropout_raw!r})"
        if reason is None:
            reason = _anchor_problem(recs.get(protocol.anchor, []), protocol, as_of)

        anchor_date = None
        if reason is None:
            anchor_date = recs[protocol.anchor][0].date
            # C3: 탈락일이 등록일(없으면 기준방문일)보다 앞서면 모순 — 실제 방문을
            # '탈락 후 해당없음'으로 조용히 지워 버리므로, 판정불가로 크게 강등한다.
            if subj is not None and subj.dropout is not None:
                ref, refname = ((subj.enroll, "등록일") if subj.enroll is not None
                                else (anchor_date, f"기준방문({protocol.anchor})일"))
                if ref is not None and subj.dropout < ref:
                    reason = (f"탈락일({subj.dropout.isoformat()})이 {refname}"
                              f"({ref.isoformat()})보다 앞섬 — 모순 데이터")

        # 기준방문일이 날짜 범위 끝(9999-12-31 같은 구형 EDC 의 '미정' 표기)에
        # 붙어 있으면 예정일 계산이 넘친다. 넘치면 죽지 말고 그 피험자만
        # 판정불가로 내린다 — 추측하지 않는다는 원칙 그대로.
        if reason is None and anchor_date is not None:
            try:
                for vdef in protocol.visits:
                    sched = anchor_date + dt.timedelta(days=vdef.offset)
                    sched + dt.timedelta(days=vdef.win_lo)
                    sched + dt.timedelta(days=vdef.win_hi)
            except (OverflowError, OSError):
                reason = (f"기준방문({protocol.anchor})일 {anchor_date.isoformat()} 에서 "
                          f"예정일을 계산하면 날짜 범위를 벗어남 — 미정 표기(예: 9999-12-31)인지 확인하세요")

        if reason is not None:
            res.subject_unjudgeable[sid] = reason
            for vdef in protocol.visits:
                res.slots.append(Slot(subject=sid, visit=vdef, verdict=V_UNJUDGEABLE,
                                      error_kind=E_SUBJECT, detail=reason))
            continue

        # C2: as-of 이후의 탈락일·등록일은 이 기준시점의 판정에 쓰지 않는다
        dropout = None
        if subj is not None and subj.dropout is not None and subj.dropout <= as_of:
            dropout = subj.dropout
        enroll = None
        if subj is not None and subj.enroll is not None and subj.enroll <= as_of:
            enroll = subj.enroll
        judged_seq: List[Slot] = []

        for vdef in protocol.visits:
            slot = Slot(subject=sid, visit=vdef)
            slot.scheduled = anchor_date + dt.timedelta(days=vdef.offset)
            slot.win_start = slot.scheduled + dt.timedelta(days=vdef.win_lo)
            slot.win_end = slot.scheduled + dt.timedelta(days=vdef.win_hi)
            group = recs.get(vdef.name, [])

            if len(group) >= 2:
                rows = ", ".join(f"{g.row_no}행 {g.raw_date!r}" for g in group)
                slot.verdict, slot.error_kind = V_UNJUDGEABLE, E_DUP
                slot.detail = f"같은 방문 기록 {len(group)}행({rows}) — 어느 행이 맞는지 추측하지 않음"
                res.data_errors.append(DataError(sid, vdef.name, E_DUP, slot.detail))
            elif len(group) == 1:
                r = group[0]
                if r.date is None:
                    slot.verdict, slot.error_kind = V_UNJUDGEABLE, E_PARSE
                    slot.detail = f"{r.row_no}행 {r.parse_error}"
                    res.data_errors.append(DataError(sid, vdef.name, E_PARSE, slot.detail))
                elif r.date > as_of and slot.win_end >= as_of:
                    # 창이 아직 안 닫혔는데 날짜가 미래 — 코디네이터가 다음 예약일을
                    # 미리 적어 둔 정상적인 트래커다(상태 열이 없는 시트에서 흔하다).
                    # 이걸 '원본 확인 필요'로 띄우면 멀쩡한 예약 수십 건이 잡음이 된다.
                    slot.verdict = V_PENDING
                    slot.detail = (f"{r.row_no}행 방문일 {r.date.isoformat()} — 예약된 미래 날짜"
                                   f"(창 {slot.win_end.isoformat()} 마감 전)로 보고 미도래 처리")
                    res.n_future_booked += 1
                elif r.date > as_of:
                    # 창은 이미 닫혔는데 날짜가 미래 — 이건 진짜 모순이다.
                    slot.verdict, slot.error_kind = V_UNJUDGEABLE, E_FUTURE
                    slot.detail = (f"{r.row_no}행 방문일 {r.date.isoformat()} 이 기준시점(as-of) "
                                   f"{as_of.isoformat()} 이후인데 창은 "
                                   f"{slot.win_end.isoformat()} 에 이미 마감 — 완료 기록일 수 없음")
                    res.data_errors.append(DataError(sid, vdef.name, E_FUTURE, slot.detail))
                # 기준방문 자신은 이 검사에서 뺀다(offset > 0). 기준방문일이 곧 0일을
                # 정의하므로 등록일과의 앞뒤는 프로토콜 위반이 아니라 기관의 기재
                # 관행이다 — 등록일을 무작위배정일로 적어 기저방문 다음 날이 되는
                # 곳이 흔한데, 그때마다 기저방문을 데이터 오류로 떨어뜨리면 정상
                # 트래커에서 판정불가가 무더기로 생긴다.
                elif enroll is not None and vdef.offset > 0 and r.date < enroll:
                    slot.verdict, slot.error_kind = V_UNJUDGEABLE, E_PREENROLL
                    slot.detail = (f"{r.row_no}행 방문일 {r.date.isoformat()} 이 등록일 "
                                   f"{enroll.isoformat()} 이전")
                    res.data_errors.append(DataError(sid, vdef.name, E_PREENROLL, slot.detail))
                elif dropout is not None and r.date > dropout:
                    slot.verdict = V_NA_DROPOUT
                    slot.actual = r.date
                    slot.detail = f"탈락일({dropout.isoformat()}) 이후의 기록 — 판정 제외"
                else:
                    slot.actual = r.date
                    if slot.win_start <= r.date <= slot.win_end:
                        slot.verdict = V_IN
                        judged_seq.append(slot)
                    else:
                        slot.verdict = V_OUT
                        if r.date > slot.win_end:
                            slot.days_out = (r.date - slot.win_end).days
                        else:
                            slot.days_out = -(slot.win_start - r.date).days
                        judged_seq.append(slot)
                        sev = _severity("창이탈", slot.days_out, protocol)
                        sign = f"+{slot.days_out}" if slot.days_out > 0 else str(slot.days_out)
                        res.deviations.append(Deviation(
                            subject=sid, visit_name=vdef.name, kind="창이탈",
                            scheduled=slot.scheduled, win_start=slot.win_start,
                            win_end=slot.win_end, actual=r.date, days_out=slot.days_out,
                            severity=sev,
                            evidence=(f"예정 {slot.scheduled.isoformat()} "
                                      f"(창 {slot.win_start.isoformat()}~{slot.win_end.isoformat()}), "
                                      f"실제 {r.date.isoformat()} → {sign}일"),
                        ))
            else:  # 기록 없음
                if dropout is not None and dropout <= slot.win_end:
                    slot.verdict = V_NA_DROPOUT
                    slot.detail = f"창 마감({slot.win_end.isoformat()}) 전 탈락({dropout.isoformat()})"
                elif slot.win_end >= as_of:
                    slot.verdict = V_PENDING
                elif not vdef.required:
                    slot.verdict = V_NA_OPTIONAL
                else:
                    slot.verdict = V_MISSING
                    res.deviations.append(Deviation(
                        subject=sid, visit_name=vdef.name, kind="필수방문결측",
                        scheduled=slot.scheduled, win_start=slot.win_start,
                        win_end=slot.win_end, actual=None, days_out=None,
                        severity="중대",
                        evidence=(f"창 {slot.win_start.isoformat()}~{slot.win_end.isoformat()} 마감, "
                                  f"기록 없음, 탈락 기록도 없음"),
                    ))
            res.slots.append(slot)

        # ── 순서 위반: 창내/창이탈로 판정된 기록끼리, 프로토콜 순서대로 인접 비교 ──
        for a, b in zip(judged_seq, judged_seq[1:]):
            if b.actual < a.actual:
                res.deviations.append(Deviation(
                    subject=sid, visit_name=b.visit.name, kind="순서위반",
                    scheduled=b.scheduled, win_start=b.win_start, win_end=b.win_end,
                    actual=b.actual, days_out=None, severity="중대",
                    evidence=(f"{b.visit.name}({b.actual.isoformat()}) 가 "
                              f"{a.visit.name}({a.actual.isoformat()}) 보다 앞섬"),
                ))

    if res.n_future_booked:
        res.notes.append(
            f"창 마감 전 미래 날짜 {res.n_future_booked}건 — 예약 기재로 보고 미도래로 처리"
            "(창이 이미 닫혔는데 미래 날짜면 데이터 오류로 잡습니다)")
    return res


def _anchor_problem(anchor_group: List[VisitRecord], protocol: Protocol,
                    as_of: dt.date) -> Optional[str]:
    """기준방문을 신뢰할 수 없으면 사유 문자열, 정상이면 None."""
    a = protocol.anchor
    if not anchor_group:
        return f"기준방문({a}) 기록 없음"
    if len(anchor_group) >= 2:
        rows = ", ".join(str(g.row_no) for g in anchor_group)
        return f"기준방문({a}) 중복 기록({rows}행)"
    r = anchor_group[0]
    if r.date is None:
        # 사유 문자열에 값을 박으면 리포트의 '같은 사유끼리 묶기'가 영영 안 걸려
        # 한 줄짜리 요약이 N 줄로 늘어난다. 값은 사유가 아니라 목록 쪽에 둔다.
        return f"기준방문({a}) 날짜 해석 불가"
    # 기준방문일이 as-of 이후인 경우는 여기서 다루지 않는다 — 데이터가 나쁜 게
    # 아니라 그 시점에 아직 이 시험에 들어오지 않은 사람이라, 판정불가가 아니라
    # 판정 대상 제외다(호출부에서 처리).
    return None
