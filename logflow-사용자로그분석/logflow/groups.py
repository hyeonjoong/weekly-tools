"""군(arm/그룹) 간 비교 분석 — 중재군 vs 대조군처럼 두 집단의 사용 로그를 비교한다.

임상/유저테스트 로그는 보통 "어느 군에 배정됐는가" 열을 함께 갖는다. 그런데 전체를
뭉뚱그린 DAU·리텐션·퍼널만 보면 정작 알고 싶은 것 — *군 사이에 차이가 있는가, 그 차이가
얼마나 불확실한가* — 를 답할 수 없다. 이 모듈은 사용자 단위로 군을 확정한 뒤 다음을 낸다:

  1. 군별 기술통계 (사용자 수·이벤트·세션·사용시간·활성일수)
  2. **비율 비교**: day-N 리텐션, 퍼널 완주율
     → 위험차(risk difference) + Newcombe hybrid-score 신뢰구간 + Fisher 정확검정
  3. **분포 비교**: 사용자당 이벤트·세션·총 사용시간·활성일수
     → Mann-Whitney U (동점 보정) + rank-biserial 효과크기
  4. **이탈까지의 시간**: Kaplan-Meier 곡선(군별) + log-rank 검정
  5. 위 모든 검정에 **Holm–Bonferroni 다중비교 보정** p 값

설계 원칙 — *분석 단위는 사용자*다. 세션이나 이벤트를 독립 관측치로 세면 한 사용자가
여러 번 기여해 표준오차가 과소평가되므로, 모든 검정은 사용자당 하나의 값(또는 하나의
0/1 결과)만 쓴다.

추론 검정은 **군이 정확히 2개일 때만** 수행한다. 3개 이상이면 기술통계만 내고, 어떤
쌍을 비교할지는 연구자가 `--ref-group` 과 필터로 직접 정하도록 둔다 (모든 쌍을 말없이
검정해 다중비교를 부풀리지 않기 위함).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .adherence import Adherence, adherence
from .dataio import Event
from .metrics import funnel
from .sessionize import Session, sessionize
from .stats import (
    DiffResult,
    KMCurve,
    LogRankResult,
    MannWhitneyResult,
    fisher_exact_two_sided,
    holm_adjust,
    kaplan_meier,
    logrank_test,
    mann_whitney_u,
    median,
    newcombe_diff_interval,
)

# 이 인원 미만의 군은 개인 재식별이 쉬워 경고를 붙인다 (역학/통계 공개의 관행값).
SMALL_CELL_THRESHOLD = 5

# 안내문에 군 라벨을 넣을 때의 최대 길이 (원본은 JSON/CSV 에 그대로 남는다).
_NOTE_LABEL_MAX = 24


def _short(label: str) -> str:
    """안내문용으로 라벨을 한 줄·적당한 길이로 (표·문장이 깨지지 않도록)."""
    flat = " ".join(str(label).split())
    return flat if len(flat) <= _NOTE_LABEL_MAX else flat[:_NOTE_LABEL_MAX - 1] + "…"

# 사용자당 분포 비교 지표의 (키, 사람이 읽는 이름, 단위).
_USER_METRICS = (
    ("events_per_user", "사용자당 이벤트 수", "건"),
    ("sessions_per_user", "사용자당 세션 수", "회"),
    ("minutes_per_user", "사용자당 총 사용시간", "분"),
    ("active_days_per_user", "사용자당 활성 일수", "일"),
)


@dataclass
class ArmSummary:
    """한 군의 기술통계 (모두 사용자 단위)."""

    group: str
    n_users: int
    n_events: int
    n_sessions: int
    median_events_per_user: Optional[float]
    median_sessions_per_user: Optional[float]
    median_minutes_per_user: Optional[float]
    median_active_days: Optional[float]
    retention: Dict[int, Tuple[int, int]] = field(default_factory=dict)  # day-N -> (retained, eligible)
    funnel_completion: Optional[Tuple[int, int]] = None  # (완주자, 1단계 도달자)
    adherence: Optional[Tuple[int, int]] = None  # (준수 참여자, 분모가 있는 참여자)
    median_adherence_rate: Optional[float] = None  # 사용자당 준수 주 비율의 중앙값


@dataclass
class ProportionTest:
    """두 군의 비율 비교 (위험차 + Newcombe 구간 + Fisher 정확검정)."""

    label: str
    group_a: str          # 비교군
    group_b: str          # 기준군(reference)
    successes_a: int
    n_a: int
    successes_b: int
    n_b: int
    diff: DiffResult      # p_a - p_b 와 그 신뢰구간
    p_value: float        # Fisher exact (양측)
    p_adjusted: Optional[float] = None


@dataclass
class DistributionTest:
    """두 군의 사용자당 연속형 지표 비교 (Mann-Whitney U)."""

    label: str
    unit: str
    group_a: str
    group_b: str
    result: MannWhitneyResult
    p_adjusted: Optional[float] = None


@dataclass
class SurvivalComparison:
    """이탈까지의 시간 — 군별 KM 곡선과 log-rank 검정."""

    churn_days: int
    curves: Dict[str, KMCurve]
    n_churned: Dict[str, int]
    logrank: Optional[LogRankResult]
    p_adjusted: Optional[float] = None


@dataclass
class GroupComparison:
    group_col: str
    groups: List[str]
    reference: Optional[str]
    arms: List[ArmSummary]
    proportions: List[ProportionTest]
    distributions: List[DistributionTest]
    survival: Optional[SurvivalComparison]
    confidence: float
    n_tests: int
    ungrouped_users: int      # 군 라벨이 하나도 없던 사용자 수 (분석 제외)
    conflicting_users: int    # 한 사용자에게 서로 다른 군 라벨이 붙어 있던 수
    notes: List[str] = field(default_factory=list)
    compare_a: Optional[str] = None   # 검정에서 '비교군' 으로 쓴 라벨 (2군일 때)
    compare_b: Optional[str] = None   # 검정에서 '기준군' 으로 쓴 라벨 (= reference)


# ---------------------------------------------------------------- 군 배정

def assign_groups(events: Sequence[Event]) -> Tuple[Dict[str, str], int, int]:
    """사용자 → 군 라벨을 확정한다.

    실데이터에서는 한 사용자에게 서로 다른 군 라벨이 붙는 경우가 있다(로그 병합 실수,
    재배정 등). 여기서는 **가장 이른 이벤트의 라벨**을 그 사용자의 군으로 확정하고,
    충돌한 사용자 수를 따로 세어 리포트에서 경고한다 — 조용히 마지막 값으로 덮어쓰면
    배정이 실행마다 달라 보일 수 있기 때문이다.

    반환: (user→group, 군 라벨이 전혀 없는 사용자 수, 라벨이 충돌한 사용자 수)
    """
    first_label: Dict[str, str] = {}
    first_ts: Dict[str, object] = {}
    labels_seen: Dict[str, Set[str]] = defaultdict(set)
    all_users: Set[str] = set()
    for e in events:
        all_users.add(e.user)
        if e.group is None:
            continue
        labels_seen[e.user].add(e.group)
        # 이벤트는 (ts, user) 정렬이지만 호출자가 정렬을 보장하지 않을 수 있으므로 직접 비교.
        if e.user not in first_ts or e.ts < first_ts[e.user]:
            first_ts[e.user] = e.ts
            first_label[e.user] = e.group
    conflicting = sum(1 for u, labs in labels_seen.items() if len(labs) > 1)
    ungrouped = len(all_users - set(first_label))
    return first_label, ungrouped, conflicting


def filter_to_groups(
    events: Sequence[Event], wanted: Sequence[str]
) -> Tuple[List[Event], int]:
    """지정한 군에 배정된 사용자의 이벤트만 남긴다 (군 배정은 `assign_groups` 규칙).

    군이 3개 이상이면 검정을 생략하므로, 비교하고 싶은 두 군만 골라 다시 돌릴 수 있어야
    한다. 날짜 필터(--from/--to)로는 군을 고를 수 없으므로 이 함수가 그 역할을 한다.

    반환: (남은 이벤트, 제외된 사용자 수). 요청한 라벨이 데이터에 없으면 오류.
    """
    if not wanted:
        raise ValueError("남길 군 목록이 비어 있습니다")
    mapping, _, _ = assign_groups(events)
    available = sorted(set(mapping.values()))
    missing = [w for w in wanted if w not in available]
    if missing:
        raise ValueError(
            f"데이터에 없는 군입니다: {missing} (있는 군: {available})"
        )
    keep = set(wanted)
    kept_users = {u for u, g in mapping.items() if g in keep}
    out = [e for e in events if e.user in kept_users]
    dropped = len({e.user for e in events}) - len(kept_users)
    if not out:
        raise ValueError(f"선택한 군에 해당하는 이벤트가 없습니다: {sorted(keep)}")
    return out, dropped


# ---------------------------------------------------------------- 사용자 단위 지표

def _user_metrics(
    events: Sequence[Event], sessions: Sequence[Session]
) -> Dict[str, Dict[str, float]]:
    """사용자별 {이벤트 수, 세션 수, 총 사용시간(분), 활성 일수}."""
    ev_count: Dict[str, int] = defaultdict(int)
    days: Dict[str, Set[date]] = defaultdict(set)
    for e in events:
        ev_count[e.user] += 1
        days[e.user].add(e.ts.date())
    sess_count: Dict[str, int] = defaultdict(int)
    minutes: Dict[str, float] = defaultdict(float)
    for s in sessions:
        sess_count[s.user] += 1
        minutes[s.user] += s.duration_seconds / 60.0
    return {
        u: {
            "events_per_user": float(ev_count[u]),
            "sessions_per_user": float(sess_count.get(u, 0)),
            "minutes_per_user": float(minutes.get(u, 0.0)),
            "active_days_per_user": float(len(days[u])),
        }
        for u in ev_count
    }


def _retention_counts_from_days(
    by_user: Dict[str, Set[date]], n: int, mode: str, max_day: date
) -> Tuple[int, int]:
    """day-N 리텐션의 (retained, eligible) — `metrics.retention` 과 같은 정의.

    관찰 지평 `max_day` 는 **전체 데이터 기준**을 호출자가 넘긴다. 군마다 마지막 활동일이
    다른데 각자의 max_day 를 쓰면 eligible 집합이 달라져 비교가 불공정해지기 때문이다.
    """
    retained = eligible = 0
    for u, ds in by_user.items():
        c = min(ds)
        if n > (max_day - c).days:
            continue
        eligible += 1
        if mode == "exact":
            if (c + timedelta(days=n)) in ds:
                retained += 1
        else:
            if (max(ds) - c).days >= n:
                retained += 1
    return (retained, eligible)


# ---------------------------------------------------------------- 이탈(생존)

def churn_survival(
    events: Sequence[Event], churn_days: int, end: Optional[date] = None
) -> Tuple[List[float], List[bool]]:
    """사용자별 (관찰시간[일], 이탈 관찰 여부) — Kaplan-Meier 입력.

    관심 사건 T 는 **참여 지속 기간**(첫 활동 → 마지막 활동까지의 일수)이다.
    관찰 종료일 `end`(주지 않으면 이 이벤트들의 마지막 활동일), 사용자의 첫 활동일 C,
    마지막 활동일 L 이라 할 때 — **두 경우 모두 시간은 (L - C) 일**이고 사건 여부만 다르다:

      - `end - L >= churn_days` → **이탈로 관찰**(event=True). 마지막 활동 이후
        churn_days 이상 기록이 없으므로 L 이 참여의 끝이라고 확정한다. T = L - C.
      - 그렇지 않으면 → **우측 절단**(censored). 아직 이탈로 확정할 만큼 침묵을 관찰하지
        못했으므로, 우리가 아는 것은 `T >= L - C` 뿐이다. 그래서 L - C 에서 절단한다.

    절단 시간에 (end - C) 를 쓰지 **않는** 이유: 그 값은 "관찰 종료일까지 참여가 이어졌다"
    는 주장인데, 우리는 L 이후의 활동을 본 적이 없으므로 그렇게 말할 근거가 없다.
    두 경우가 같은 시계(L - C)를 쓰므로 사건군과 절단군의 시간이 서로 다른 척도로
    섞이지 않는다.

    주의(해석상의 한계): 절단이 마지막 활동 시점에 걸리므로 절단은 사건 과정과 무관하지
    않다(informative censoring). 표본이 작을수록 KM 곡선을 낙관적으로 볼 위험이 있으니
    p 값만이 아니라 절단 비율과 곡선을 함께 보라. `churn_days` 가 데이터 기간에 비해
    크면 대부분이 절단된다 — 정의상 당연한 결과이며 리포트에 절단 수를 함께 표시한다.
    """
    if churn_days < 1:
        raise ValueError("churn_days 는 1 이상이어야 합니다")
    first: Dict[str, date] = {}
    last: Dict[str, date] = {}
    for e in events:
        d = e.ts.date()
        if e.user not in first or d < first[e.user]:
            first[e.user] = d
        if e.user not in last or d > last[e.user]:
            last[e.user] = d
    if not first:
        return ([], [])
    if end is None:
        end = max(last.values())
    times: List[float] = []
    observed: List[bool] = []
    for u in sorted(first):
        c, l = first[u], last[u]
        # 시간은 두 경우 모두 (L - C); 침묵을 충분히 관찰했는지가 사건/절단을 가른다.
        times.append(float((l - c).days))
        observed.append((end - l).days >= churn_days)
    return (times, observed)


# ---------------------------------------------------------------- 메인

def compare_groups(
    events: Sequence[Event],
    *,
    group_col: str = "group",
    gap_seconds: float = 1800.0,
    retention_days: Sequence[int] = (1, 7),
    funnel_steps: Optional[Sequence[str]] = None,
    confidence: float = 0.95,
    retention_mode: str = "exact",
    churn_days: int = 7,
    reference: Optional[str] = None,
    adherence_min_days: Optional[int] = None,
    adherence_period: int = 7,
    adherence_target: float = 0.8,
    adherence_weeks: Optional[int] = None,
) -> GroupComparison:
    """군 간 비교 분석을 수행한다 (군이 2개일 때 추론 검정 포함).

    reference: 기준군(대조군) 라벨. 비율 차이는 (비교군 − 기준군) 으로 낸다.
    지정하지 않으면 사전순 첫 라벨이 기준군이 된다.

    adherence_min_days 를 주면 프로토콜 준수도(주 N일 이상 사용)도 군별로 계산하고,
    준수 참여자 비율(비율 비교)과 사용자당 준수 주 비율(분포 비교)을 검정에 추가한다.
    관찰 종료일은 전체 데이터의 마지막 날로 통일한다 — 군마다 다른 종료일을 쓰면
    '완전히 관찰된 주' 의 수가 달라져 분모가 불공정해진다.
    """
    if not events:
        raise ValueError("분석할 이벤트가 없습니다 (빈 입력)")
    # 중복 day-N 은 같은 가설을 두 번 검정해 Holm family 를 부풀리므로 여기서도 막는다.
    retention_days = list(dict.fromkeys(retention_days))
    user_group, ungrouped, conflicting = assign_groups(events)
    groups = sorted(set(user_group.values()))
    notes: List[str] = []
    if ungrouped:
        notes.append(f"군 라벨이 없는 사용자 {ungrouped}명은 군 비교에서 제외했습니다.")
    if conflicting:
        notes.append(
            f"한 사용자에게 서로 다른 군 라벨이 붙은 경우 {conflicting}명 — "
            f"가장 이른 이벤트의 라벨로 확정했습니다(배정 데이터를 확인하세요)."
        )
    if reference is not None and reference not in groups:
        raise ValueError(
            f"기준군 {reference!r} 이 데이터에 없습니다 (있는 군: {groups})"
        )
    ref = reference if reference is not None else (groups[0] if groups else None)

    # 군별 이벤트 분할 (군 미상 사용자는 제외).
    by_group: Dict[str, List[Event]] = {g: [] for g in groups}
    for e in events:
        g = user_group.get(e.user)
        if g is not None:
            by_group[g].append(e)

    # 관찰 지평은 전체 데이터 기준으로 통일 — 군마다 마지막 활동일이 다르면
    # eligible 집합이 달라져 리텐션 비교가 불공정해진다.
    max_day = max(e.ts.date() for e in events)

    arms: List[ArmSummary] = []
    per_user: Dict[str, Dict[str, Dict[str, float]]] = {}
    adh_by_group: Dict[str, Adherence] = {}
    for g in groups:
        evs = by_group[g]
        sessions = sessionize(evs, gap_seconds=gap_seconds)
        metrics = _user_metrics(evs, sessions)
        per_user[g] = metrics
        by_user_days: Dict[str, Set[date]] = defaultdict(set)
        for e in evs:
            by_user_days[e.user].add(e.ts.date())
        ret = {
            n: _retention_counts_from_days(by_user_days, n, retention_mode, max_day)
            for n in retention_days
        }
        fc = None
        if funnel_steps:
            steps = funnel(evs, funnel_steps, confidence=confidence)
            fc = (steps[-1].reached, steps[0].reached)
        adh_counts = None
        adh_median = None
        if adherence_min_days is not None:
            adh = adherence(
                evs,
                min_days=adherence_min_days,
                period_days=adherence_period,
                target=adherence_target,
                confidence=confidence,
                max_weeks=adherence_weeks,
                end=max_day,
            )
            adh_by_group[g] = adh
            adh_counts = (adh.n_adherent_users, adh.n_users)
            adh_median = adh.median_user_rate
        arms.append(
            ArmSummary(
                group=g,
                n_users=len(metrics),
                n_events=len(evs),
                n_sessions=len(sessions),
                median_events_per_user=median([m["events_per_user"] for m in metrics.values()]),
                median_sessions_per_user=median([m["sessions_per_user"] for m in metrics.values()]),
                median_minutes_per_user=median([m["minutes_per_user"] for m in metrics.values()]),
                median_active_days=median([m["active_days_per_user"] for m in metrics.values()]),
                retention=ret,
                funnel_completion=fc,
                adherence=adh_counts,
                median_adherence_rate=adh_median,
            )
        )

    proportions: List[ProportionTest] = []
    distributions: List[DistributionTest] = []
    survival: Optional[SurvivalComparison] = None
    a_label = b_label = None

    if len(groups) == 2:
        a_label = groups[0] if groups[1] == ref else groups[1]
        b_label = ref
        arm_a = next(x for x in arms if x.group == a_label)
        arm_b = next(x for x in arms if x.group == b_label)

        for n in retention_days:
            ra, ea = arm_a.retention.get(n, (0, 0))
            rb, eb = arm_b.retention.get(n, (0, 0))
            d = newcombe_diff_interval(ra, ea, rb, eb, confidence)
            if d is None:
                # 한쪽 군의 관찰 기회(eligible)가 0 이면 비교 자체가 불가능하다.
                # 조용히 행을 빼면 사용자는 요청한 비교가 없어진 걸 알아채지 못한다.
                notes.append(
                    f"day-{n} 리텐션 비교 불가: 관찰 기회가 있는 사용자가 "
                    f"{_short(a_label)} {ea}명 / {_short(b_label)} {eb}명 (한쪽이 0명)."
                )
                continue
            proportions.append(
                ProportionTest(
                    label=f"day-{n} 리텐션",
                    group_a=a_label, group_b=b_label,
                    successes_a=ra, n_a=ea, successes_b=rb, n_b=eb,
                    diff=d,
                    p_value=fisher_exact_two_sided(ra, ea - ra, rb, eb - rb),
                )
            )
        if funnel_steps and arm_a.funnel_completion and arm_b.funnel_completion:
            sa, na = arm_a.funnel_completion
            sb, nb = arm_b.funnel_completion
            d = newcombe_diff_interval(sa, na, sb, nb, confidence)
            if d is None:
                notes.append(
                    f"퍼널 완주 비교 불가: 1단계({_short(funnel_steps[0])}) 도달자가 "
                    f"{_short(a_label)} {na}명 / {_short(b_label)} {nb}명 (한쪽이 0명)."
                )
            else:
                proportions.append(
                    ProportionTest(
                        label=f"퍼널 완주({funnel_steps[-1]})",
                        group_a=a_label, group_b=b_label,
                        successes_a=sa, n_a=na, successes_b=sb, n_b=nb,
                        diff=d,
                        p_value=fisher_exact_two_sided(sa, na - sa, sb, nb - sb),
                    )
                )

        if adherence_min_days is not None:
            sa, na = arm_a.adherence or (0, 0)
            sb, nb = arm_b.adherence or (0, 0)
            per = "주" if adherence_period == 7 else f"{adherence_period}일당"
            adh_label = (
                f"프로토콜 준수({per}{adherence_min_days}일↑"
                f"·{adherence_target * 100:g}%↑)"
            )
            d = newcombe_diff_interval(sa, na, sb, nb, confidence)
            if d is None:
                notes.append(
                    f"프로토콜 준수 비교 불가: 완전히 관찰된 주가 1개 이상인 참여자가 "
                    f"{_short(a_label)} {na}명 / {_short(b_label)} {nb}명 (한쪽이 0명)."
                )
            else:
                proportions.append(
                    ProportionTest(
                        label=adh_label,
                        group_a=a_label, group_b=b_label,
                        successes_a=sa, n_a=na, successes_b=sb, n_b=nb,
                        diff=d,
                        p_value=fisher_exact_two_sided(sa, na - sa, sb, nb - sb),
                    )
                )

        for key, label, unit in _USER_METRICS:
            xs = [m[key] for m in per_user[a_label].values()]
            ys = [m[key] for m in per_user[b_label].values()]
            res = mann_whitney_u(xs, ys)
            if res is not None:
                distributions.append(
                    DistributionTest(
                        label=label, unit=unit,
                        group_a=a_label, group_b=b_label, result=res,
                    )
                )

        if adherence_min_days is not None:
            # 사용자당 '준수 주 비율(%)' — 완전 관찰 주가 없는 참여자는 값이 없어 제외.
            xs = [u.rate * 100.0 for u in adh_by_group[a_label].users if u.rate is not None]
            ys = [u.rate * 100.0 for u in adh_by_group[b_label].users if u.rate is not None]
            res = mann_whitney_u(xs, ys)
            if res is None:
                # 조용히 빠지면 Holm family 크기까지 함께 줄어 다른 p 값이 바뀐다.
                notes.append(
                    f"사용자당 준수 주 비율 비교 불가: 분모가 있는 참여자가 "
                    f"{_short(a_label)} {len(xs)}명 / {_short(b_label)} {len(ys)}명 "
                    f"(한쪽이 0명)."
                )
            else:
                distributions.append(
                    DistributionTest(
                        label="사용자당 준수 주 비율", unit="%",
                        group_a=a_label, group_b=b_label, result=res,
                    )
                )

        t1, e1 = churn_survival(by_group[a_label], churn_days, end=max_day)
        t2, e2 = churn_survival(by_group[b_label], churn_days, end=max_day)
        km1 = kaplan_meier(t1, e1, confidence)
        km2 = kaplan_meier(t2, e2, confidence)
        curves = {}
        if km1 is not None:
            curves[a_label] = km1
        if km2 is not None:
            curves[b_label] = km2
        lr = logrank_test(t1, e1, t2, e2)
        if curves:
            survival = SurvivalComparison(
                churn_days=churn_days,
                curves=curves,
                n_churned={a_label: sum(e1), b_label: sum(e2)},
                logrank=lr,
            )
    elif len(groups) > 2:
        notes.append(
            f"군이 {len(groups)}개라 통계 검정은 생략하고 기술통계만 냈습니다 "
            f"(2개 군일 때만 검정을 수행합니다 — 모든 쌍을 자동 검정해 "
            f"다중비교를 부풀리지 않기 위함)."
        )
    elif len(groups) == 1:
        notes.append("군이 1개뿐이라 비교할 대상이 없습니다 (기술통계만).")
    else:
        notes.append("군 라벨이 있는 사용자가 없습니다 — 군 열 이름/값을 확인하세요.")

    small_arms = [a.group for a in arms if 0 < a.n_users < SMALL_CELL_THRESHOLD]
    if small_arms:
        notes.append(
            f"인원이 {SMALL_CELL_THRESHOLD}명 미만인 군이 있습니다"
            f"({', '.join(_short(a) for a in small_arms)}). "
            f"이 군의 중앙값·생존곡선은 사실상 개인의 값이라 재식별될 수 있으니 "
            f"외부 공유 시 주의하세요(통계적으로도 해석하지 마세요)."
        )

    # Holm 보정 — 이번 비교에서 나온 모든 p 값을 하나의 family 로 본다.
    pvals: List[float] = [t.p_value for t in proportions]
    pvals += [t.result.p for t in distributions]
    has_lr = survival is not None and survival.logrank is not None
    if has_lr:
        pvals.append(survival.logrank.p)
    adjusted = holm_adjust(pvals)
    i = 0
    for t in proportions:
        t.p_adjusted = adjusted[i]
        i += 1
    for t in distributions:
        t.p_adjusted = adjusted[i]
        i += 1
    if has_lr:
        survival.p_adjusted = adjusted[i]
        i += 1

    return GroupComparison(
        group_col=group_col,
        groups=groups,
        reference=ref,
        arms=arms,
        proportions=proportions,
        distributions=distributions,
        survival=survival,
        confidence=confidence,
        n_tests=len(pvals),
        ungrouped_users=ungrouped,
        conflicting_users=conflicting,
        notes=notes,
        compare_a=a_label,
        compare_b=b_label,
    )
