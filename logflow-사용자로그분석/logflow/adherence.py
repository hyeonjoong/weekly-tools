"""프로토콜 준수도(adherence) — "주 N일 이상 사용" 같은 사용 규약의 실제 이행 정도.

디지털치료제(DTx)·앱 기반 중재 연구에서 리텐션·퍼널 다음으로 (때로는 그보다 먼저)
묻는 것은 **"참여자가 정해진 만큼 실제로 썼는가"** 다. 프로토콜에는 보통
"8주간 주 5일 이상 사용" 처럼 *기간 단위의 사용 빈도* 가 적혀 있고, 논문의 결과 표에는
`주차별 준수율`·`준수 참여자 비율`·`연속 준수 주차` 가 들어간다. 이 모듈이 그 표를 만든다.

설계 결정 (모두 편향을 피하기 위한 것):

1. **연구 주차는 달력 주가 아니라 참여자별 첫 활동일(day 0) 기준**으로 끊는다.
   등록 시점이 서로 다른데 달력 주로 끊으면 누군가의 1주차는 3일짜리 부분 주가 되어
   준수율이 근거 없이 낮게 잡힌다.
2. **완전히 관찰된 주만 분모(eligible)에 넣는다.** 관찰 종료일에 걸친 마지막 부분 주를
   넣으면 "아직 다 쓸 시간이 없었다" 는 것이 "안 썼다" 로 기록된다.
3. 관찰 종료일 E 는 호출자가 넘긴 값(군 비교에서는 **전체 데이터의 마지막 날**)을 쓴다.
   군마다 각자의 마지막 활동일을 쓰면 분모가 달라져 군 간 비교가 불공정해진다.
4. 준수 판정은 **활성 일수**(그 주에 이벤트가 하나라도 있던 날의 수) 기준이다. 하루에
   몇 번을 썼든 1일로 센다 — "주 5일 사용" 규약의 문언 그대로다.
5. `max_weeks`(= `--adherence-weeks`)를 **주면 그것이 프로토콜 관찰 창**이 된다. 이때
   '준수 참여자' 의 분모는 그 창을 **끝까지 관찰한 참여자(완주자)** 로 제한한다. 그러지
   않으면 늦게 등록해 1주만 관찰된 사람이 "1주 중 1주 준수 = 100%" 로 분류되어, 8주를
   꼬박 관찰한 사람보다 훨씬 쉬운 시험을 치르게 된다.

한계 (해석 전에 반드시 읽을 것):

- 여기서 말하는 '준수' 는 *앱 로그에 활동이 남았는가* 이지, 중재를 제대로 수행했는지
  (예: 호흡운동을 끝까지 했는지) 가 아니다. 완수 이벤트를 기준으로 보고 싶다면 로그를
  그 이벤트만 남기고 거르거나 `--funnel` 완주율을 함께 보라.
- `max_weeks` 를 주지 않으면 참여자마다 **분모(관찰된 주 수)가 다르다**. 관찰 주가 적은
  사람일수록 '준수 참여자' 판정이 쉬워지므로(1주 중 1주 = 100%), 등록 시점이 다른 코호트
  에서는 이 비율이 행동이 아니라 등록 시점을 반영할 수 있다. 분모가 서로 다르면 경고를
  남기며, 비교를 하려면 `--adherence-weeks` 로 창을 고정하라.
- 주차 1의 시작이 **첫 로그 활동일**이므로, 등록 후 한동안 아예 쓰지 않은 기간은 보이지
  않는다(그 사람의 주차 1이 뒤로 밀린다). 이벤트가 하나도 없는 참여자는 로그에 존재하지
  않으므로 아예 집계에 들어오지 않는다 — ITT 가 아니라 as-observed 분석이다.
- 주차별 표는 **뒤로 갈수록 대상 집합이 앞 주차의 부분집합**이다. 주차 간 준수율 추세에는
  행동 변화뿐 아니라 코호트 구성 변화가 섞여 있다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .dataio import Event
from .stats import median, wilson_interval

# 기본 기간 단위(일)와 '전체 준수' 판정 기준(준수 주 비율).
DEFAULT_PERIOD_DAYS = 7
DEFAULT_TARGET = 0.8

# 주차 표가 무한정 길어지는 것을 막는 기본 상한 (2년). `--adherence-weeks` 로 조정.
MAX_WEEKS_CAP = 104

# 이 인원 미만이 대상인 주차 행은 개인 재식별이 쉬워 경고를 붙인다 (groups.py 와 같은 값).
SMALL_CELL_THRESHOLD = 5


@dataclass
class WeekAdherence:
    """한 연구 주차의 준수 집계."""

    week: int                       # 1부터 시작하는 연구 주차
    eligible: int                   # 이 주차를 온전히 관찰할 기회가 있던 참여자 수
    adherent: int                   # 그중 준수한 참여자 수
    rate: Optional[float]
    ci: Optional[Tuple[float, float]]        # rate 의 Wilson 구간
    median_active_days: Optional[float]      # eligible 참여자의 주간 활성일수 중앙값


@dataclass
class UserAdherence:
    """한 참여자의 준수 요약 (관찰 창 안에서)."""

    user: str
    eligible_weeks: int
    adherent_weeks: int
    rate: Optional[float]           # adherent_weeks / eligible_weeks
    longest_streak: int             # 연속으로 준수한 최대 주차 수
    active_days_in_window: int      # 관찰 창 **안**의 총 활성 일수 (창 밖의 활동은 미포함)


@dataclass
class Adherence:
    """준수도 분석 결과 전체."""

    min_days: int
    period_days: int
    target: float
    confidence: float
    window_weeks: int               # 분석 창(주차 수). max_weeks 를 주면 그 값
    observed_weeks: int             # 데이터가 온전히 관찰한 최대 주차 수
    required_weeks: Optional[int]   # 완주 판정 기준(= max_weeks). None 이면 완주 개념 없음
    observation_end: Optional[date]  # 관찰 종료일 — 모든 '완전히 관찰된 주' 판정의 기준
    weeks: List[WeekAdherence]
    users: List[UserAdherence]
    n_users: int                    # '준수 참여자' 비율의 분모 (아래 규칙)
    n_adherent_users: int           # 그중 준수 주 비율 >= target 인 참여자 수
    adherent_ci: Optional[Tuple[float, float]]
    median_user_rate: Optional[float]
    median_streak: Optional[float]
    n_no_full_week: int             # 완전 관찰 주가 하나도 없어 제외된 참여자 수
    n_incomplete: int               # 창을 끝까지 관찰하지 못해 제외된 참여자 수
    eligible_weeks_range: Optional[Tuple[int, int]] = None  # 분모에 든 참여자의 (min, max)
    notes: List[str] = field(default_factory=list)


def _longest_streak(weeks: Sequence[int]) -> int:
    """정렬된 주차 번호 목록에서 연속 구간의 최대 길이."""
    best = cur = 0
    prev: Optional[int] = None
    for k in weeks:
        cur = cur + 1 if (prev is not None and k == prev + 1) else 1
        prev = k
        if cur > best:
            best = cur
    return best


def adherence(
    events: Sequence[Event],
    *,
    min_days: int,
    period_days: int = DEFAULT_PERIOD_DAYS,
    target: float = DEFAULT_TARGET,
    confidence: float = 0.95,
    max_weeks: Optional[int] = None,
    end: Optional[date] = None,
) -> Adherence:
    """참여자별 연구 주차 준수도를 계산한다.

    min_days   : 한 주(period_days)에 몇 '활성일' 이상이면 준수로 볼지 (1..period_days).
    period_days: 한 '주' 의 길이(일). 기본 7. 격주 규약이면 14 처럼 준다.
    target     : 전체 준수 참여자 판정 기준 — 관찰 주의 이 비율 이상을 준수했는가 (기본 0.8).
    max_weeks  : 프로토콜 관찰 창(주차 수). 주면 **그 창을 끝까지 관찰한 참여자(완주자)**
                 만 '준수 참여자' 분모에 넣는다. 미지정 시 참여자마다 관찰된 만큼
                 (최대 104주)을 쓰고, 분모가 서로 다르다는 경고를 남긴다.
    end        : 관찰 종료일. 미지정 시 이 이벤트들의 마지막 활동일.

    주차별 분모(eligible)에는 **완전히 관찰된 주**만 들어간다 — 관찰 종료일에 걸친 마지막
    부분 주는 "아직 쓸 시간이 없었던" 것이므로 제외한다.
    """
    if period_days < 1:
        raise ValueError(f"period_days 는 1 이상이어야 합니다 (받은 값: {period_days})")
    if not (1 <= min_days <= period_days):
        raise ValueError(
            f"min_days 는 1 이상 period_days({period_days}) 이하여야 합니다 "
            f"(받은 값: {min_days})"
        )
    if not (0.0 < target <= 1.0):
        raise ValueError(f"target 은 0 초과 1 이하여야 합니다 (받은 값: {target})")
    if max_weeks is not None and max_weeks < 1:
        raise ValueError(f"max_weeks 는 1 이상이어야 합니다 (받은 값: {max_weeks})")

    notes: List[str] = []
    by_user: Dict[str, Set[date]] = defaultdict(set)
    for e in events:
        by_user[e.user].add(e.ts.date())
    if not by_user:
        return Adherence(
            min_days=min_days, period_days=period_days, target=target,
            confidence=confidence, window_weeks=0, observed_weeks=0,
            required_weeks=max_weeks, observation_end=None,
            weeks=[], users=[], n_users=0, n_adherent_users=0, adherent_ci=None,
            median_user_rate=None, median_streak=None, n_no_full_week=0,
            n_incomplete=0,
            notes=["준수도를 계산할 이벤트가 없습니다."],
        )

    obs_end = end if end is not None else max(max(ds) for ds in by_user.values())

    # 참여자별 '완전히 관찰된 주' 의 개수.
    full_weeks: Dict[str, int] = {}
    for u, ds in by_user.items():
        first = min(ds)
        observed_days = (obs_end - first).days + 1
        full_weeks[u] = max(0, observed_days // period_days)
    observed_weeks = max(full_weeks.values(), default=0)

    # window = 분석 창. max_weeks 를 주면 그것이 프로토콜 창이자 '완주' 기준이 된다.
    if max_weeks is None:
        window = min(observed_weeks, MAX_WEEKS_CAP)
        required: Optional[int] = None
        if observed_weeks > MAX_WEEKS_CAP:
            notes.append(
                f"데이터에 {observed_weeks}주차까지 있으나 표가 너무 길어지지 않도록 "
                f"{MAX_WEEKS_CAP}주까지만 계산했습니다 (--adherence-weeks 로 조정)."
            )
    else:
        window = max_weeks
        required = max_weeks
        if max_weeks > observed_weeks:
            notes.append(
                f"요청한 관찰 창은 {max_weeks}주이지만 데이터가 온전히 관찰한 것은 "
                f"{observed_weeks}주뿐입니다 — 주차 표는 {observed_weeks}주까지만 나오고, "
                f"창을 끝까지 관찰한 참여자가 없으면 '준수 참여자' 는 계산되지 않습니다."
            )
    # 표 행 수는 창과 무관하게 상한을 둔다 — window 가 크면 표 생성 비용이
    # O(참여자 × 주차) 로 폭발하고 리포트가 수만 줄이 된다.
    table_weeks = min(window, observed_weeks, MAX_WEEKS_CAP)
    if min(window, observed_weeks) > MAX_WEEKS_CAP:
        notes.append(
            f"주차 표는 {MAX_WEEKS_CAP}주까지만 보여줍니다 "
            f"(요청/관찰된 창은 {min(window, observed_weeks)}주)."
        )

    # 참여자 × 주차 활성일수. 주차 수가 아니라 '활성일 수' 에 비례해 도는 루프다.
    per_week: Dict[str, Dict[int, int]] = {}
    for u, ds in by_user.items():
        first = min(ds)
        nw = min(full_weeks[u], window)
        cnt: Dict[int, int] = defaultdict(int)
        if nw:
            for d in ds:
                k = (d - first).days // period_days + 1
                if 1 <= k <= nw:
                    cnt[k] += 1
        per_week[u] = cnt

    users: List[UserAdherence] = []
    for u in sorted(by_user):
        nw = min(full_weeks[u], window)
        cnt = per_week[u]
        adherent_ks = sorted(k for k, v in cnt.items() if v >= min_days)
        users.append(
            UserAdherence(
                user=u,
                eligible_weeks=nw,
                adherent_weeks=len(adherent_ks),
                rate=(len(adherent_ks) / nw) if nw else None,
                longest_streak=_longest_streak(adherent_ks),
                active_days_in_window=sum(cnt.values()),
            )
        )

    weeks: List[WeekAdherence] = []
    for k in range(1, table_weeks + 1):
        vals = [
            per_week[u].get(k, 0)
            for u in by_user
            if min(full_weeks[u], window) >= k
        ]
        eligible = len(vals)
        n_ok = sum(1 for v in vals if v >= min_days)
        weeks.append(
            WeekAdherence(
                week=k,
                eligible=eligible,
                adherent=n_ok,
                rate=(n_ok / eligible) if eligible else None,
                ci=wilson_interval(n_ok, eligible, confidence) if eligible else None,
                median_active_days=median([float(v) for v in vals]) if vals else None,
            )
        )

    # ── '준수 참여자' 의 분석 집단 ────────────────────────────────────────────
    # required(= --adherence-weeks)를 주면 **창을 끝까지 관찰한 완주자**만 분모에 넣는다.
    # 그러지 않으면 1주만 관찰된 사람의 "1/1 = 100%" 와 8주를 다 채운 사람의 "7/8 = 87.5%"
    # 가 같은 저울에 올라가, 비율이 행동이 아니라 등록 시점을 반영하게 된다.
    with_week = [x for x in users if x.eligible_weeks > 0]
    if required is None:
        population = with_week
        n_incomplete = 0
    else:
        population = [x for x in with_week if x.eligible_weeks >= required]
        n_incomplete = len(with_week) - len(population)
    n_users = len(population)
    # target 은 소수(예: 0.8)이고 rate 는 작은 정수비(주차 수 ≤ 104)라, 정확히 기준을
    # 만족하는 4/5·8/10 등은 IEEE-754 에서 target 과 같은 값으로 반올림된다 → 단순 비교로 안전.
    n_adherent = sum(1 for x in population if x.rate is not None and x.rate >= target)
    n_no_full_week = len(users) - len(with_week)

    if n_no_full_week and window > 0:
        notes.append(
            f"참여자 {n_no_full_week}명은 첫 활동일부터 관찰 종료일까지가 {period_days}일보다 "
            f"짧아(늦은 등록이거나 로그 기간 자체가 짧은 경우) 완전히 관찰된 주가 하나도 없어 "
            f"준수도 분모에서 제외했습니다."
        )
    if n_incomplete:
        notes.append(
            f"참여자 {n_incomplete}명은 요청한 {required}주 창을 끝까지 관찰하지 못해"
            f"(추적 미완료) '준수 참여자' 분모에서 제외했습니다 — 주차별 표에는 관찰된 "
            f"주까지 그대로 들어갑니다."
        )
    if required is None and population:
        lo = min(x.eligible_weeks for x in population)
        hi = max(x.eligible_weeks for x in population)
        if lo != hi:
            notes.append(
                f"참여자마다 관찰된 주 수가 다릅니다({lo}~{hi}주). '준수 참여자' 는 각자 "
                f"자기가 관찰된 주를 분모로 쓰므로, 관찰이 짧은 사람일수록 기준을 넘기 "
                f"쉽습니다(1주 중 1주 = 100%). 군 비교나 논문 보고에는 "
                f"--adherence-weeks 로 창을 고정해 완주자만 비교하세요."
            )
    if required is not None and not population:
        notes.append(
            f"{required}주 창을 끝까지 관찰한 참여자가 없어 '준수 참여자' 를 계산하지 "
            f"못했습니다 (주차별 준수율은 그대로 볼 수 있습니다)."
        )
    if window == 0:
        notes.append(
            f"관찰 기간이 {period_days}일보다 짧아 완전히 관찰된 주가 없습니다 — "
            f"--adherence-period 를 줄이거나 더 긴 기간의 로그를 쓰세요."
        )
    small = [w.week for w in weeks if 0 < w.eligible < SMALL_CELL_THRESHOLD]
    if small:
        shown = ", ".join(str(k) for k in small[:8]) + ("…" if len(small) > 8 else "")
        notes.append(
            f"{shown}주차는 대상이 {SMALL_CELL_THRESHOLD}명 미만입니다 — 그 행의 준수율·"
            f"활성일 중앙값은 사실상 개인의 값이라 같은 폴더의 users.csv 와 맞추면 "
            f"재식별될 수 있습니다(외부 공유 시 주의)."
        )

    return Adherence(
        min_days=min_days,
        period_days=period_days,
        target=target,
        confidence=confidence,
        window_weeks=window,
        observed_weeks=observed_weeks,
        required_weeks=required,
        observation_end=obs_end,
        weeks=weeks,
        users=users,
        n_users=n_users,
        n_adherent_users=n_adherent,
        adherent_ci=wilson_interval(n_adherent, n_users, confidence) if n_users else None,
        median_user_rate=median([x.rate for x in population if x.rate is not None]),
        median_streak=median([float(x.longest_streak) for x in population]),
        n_no_full_week=n_no_full_week,
        n_incomplete=n_incomplete,
        eligible_weeks_range=(
            (min(x.eligible_weeks for x in population),
             max(x.eligible_weeks for x in population))
            if population
            else None
        ),
        notes=notes,
    )
