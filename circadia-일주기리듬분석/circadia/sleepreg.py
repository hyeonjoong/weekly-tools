"""수면 규칙성 — SRI·중간수면·입면/기상 SD·사회적 시차.

공식 출처:
- SRI: Phillips et al. 2017, "Irregular sleep/wake patterns are associated
  with poorer academic performance ...", Sci Rep 7:3216 (정의: 24시간 간격
  두 시점의 수면/각성 상태 일치 확률 → SRI = −100 + 200·P(일치)).
  계산 세부(관측 구간에 실제로 존재하는 상태쌍만 사용, 결측은 쌍에서 제외)는
  Windred et al. 2023, "Sleep regularity is a stronger predictor of mortality
  risk than sleep duration", Sleep 47(1):zsad253 의 구현 관례를 따랐다.
- 사회적 시차: Roenneberg et al. 2012, "Social jetlag and obesity",
  Curr Biol 22(10):939 — 자유일 중간수면(MSF)과 근무일 중간수면(MSW)의 차.
  본 도구는 수면부채 보정판(MSFsc)이 아니라 |MSF − MSW| 원식을 쓴다(README).

여기서 정한 규칙(문서화된 결정론 — 테스트로 고정):
- 밤(night) 배정: 구간 중점이 [D일 12:00, D+1일 12:00) 이면 그 구간은
  'D일 밤'. 자정을 넘는 수면이 자연스럽게 한 밤으로 묶인다.
- 주 수면(main sleep): 한 밤 안에서 구간 사이 갭이 2시간을 넘으면 별개
  덩어리(cluster)로 나누고, 총 수면시간이 가장 긴 덩어리가 주 수면.
  나머지는 낮잠/분리 수면으로 분류해 입면·기상·중간수면 통계에서 제외
  (SRI의 분 단위 상태열에는 포함 — 실제로 잔 시간이므로).
- 중간수면(midsleep) = 주 수면 시작과 끝의 중점.
- 시각 통계 프레임: '전날 정오(12:00) 이후 경과 시간'으로 환산해 산술
  평균·SD를 낸다(Roenneberg 관례). 자정 wrap 문제가 없고, 정오를 걸치는
  수면(교대근무 낮수면)은 이 프레임이 부적합하므로 해당 밤을 표시한다.
- 주말밤 = 금·토요일 밤(그 밤의 배정 날짜 D가 금/토), 나머지는 주중밤.
- SRI 관측 구간 = [첫 구간 시작, 마지막 구간 종료]. 구간 밖은 상태 미상
  이므로 쌍에서 제외된다. 1분 해상도.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MIN_NIGHTS_SRI = 5
MAIN_SLEEP_GAP_HOURS = 2.0


@dataclass
class Night:
    date: dt.date                 # 밤의 배정 날짜(저녁 쪽 날짜 D)
    intervals: List[Tuple[dt.datetime, dt.datetime]]   # 이 밤의 모든 구간
    main: List[Tuple[dt.datetime, dt.datetime]]        # 주 수면 덩어리
    naps: List[Tuple[dt.datetime, dt.datetime]]
    onset: dt.datetime            # 주 수면 시작
    wake: dt.datetime             # 주 수면 종료
    midsleep: dt.datetime
    tst_hours: float              # 주 수면 덩어리 내 구간 합(각성 갭 제외)
    is_weekend: bool              # 금·토 밤


def _noon_anchor_hours(stamp: dt.datetime, night_date: dt.date) -> float:
    """'밤 배정일 정오' 이후 경과 시간(시간 단위). 예: D일 23:00 → 11.0,
    D+1일 03:30 → 15.5."""
    anchor = dt.datetime.combine(night_date, dt.time(12, 0))
    return (stamp - anchor).total_seconds() / 3600.0


def hours_to_clock_from_noon(h: float) -> str:
    """정오 기준 경과시간 → 시계 문자열. 15.5 → '03:30'(다음날)."""
    minutes = int(round(((h + 12.0) % 24.0) * 60.0)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def group_nights(intervals: List[Tuple[dt.datetime, dt.datetime]]) -> List[Night]:
    """정렬·병합된 수면구간 → 밤 목록(문서화된 규칙, 위 참조)."""
    buckets: Dict[dt.date, List[Tuple[dt.datetime, dt.datetime]]] = {}
    for s, e in intervals:
        mid = s + (e - s) / 2
        d = mid.date() if mid.hour >= 12 else mid.date() - dt.timedelta(days=1)
        buckets.setdefault(d, []).append((s, e))
    nights: List[Night] = []
    for d in sorted(buckets):
        ivs = sorted(buckets[d])
        # 갭 > 2h 로 덩어리 나누기
        clusters: List[List[Tuple[dt.datetime, dt.datetime]]] = [[ivs[0]]]
        for s, e in ivs[1:]:
            gap_h = (s - clusters[-1][-1][1]).total_seconds() / 3600.0
            if gap_h > MAIN_SLEEP_GAP_HOURS:
                clusters.append([(s, e)])
            else:
                clusters[-1].append((s, e))
        totals = [sum((e - s).total_seconds() for s, e in c) for c in clusters]
        main_i = max(range(len(clusters)), key=lambda i: (totals[i], -i))
        main = clusters[main_i]
        naps = [iv for i, c in enumerate(clusters) if i != main_i for iv in c]
        onset, wake = main[0][0], main[-1][1]
        nights.append(Night(
            date=d, intervals=ivs, main=main, naps=naps,
            onset=onset, wake=wake,
            midsleep=onset + (wake - onset) / 2,
            tst_hours=totals[main_i] / 3600.0,
            is_weekend=d.weekday() in (4, 5),   # 금=4, 토=5
        ))
    return nights


# ---------------------------------------------------------------------------
# SRI
# ---------------------------------------------------------------------------

@dataclass
class SRIResult:
    sri: Optional[float]
    n_nights: int
    n_pairs: int                 # 비교한 분 단위 상태쌍 수
    insufficient: bool
    note: str = ""


def sri(intervals: List[Tuple[dt.datetime, dt.datetime]],
        min_nights: int = MIN_NIGHTS_SRI) -> SRIResult:
    """Phillips 2017 SRI — 1분 해상도, 관측 구간 [첫 시작, 마지막 종료]."""
    nights = group_nights(intervals)
    n_nights = len(nights)
    if n_nights < min_nights:
        return SRIResult(None, n_nights, 0, True,
                         f"데이터 부족({n_nights}일<{min_nights}일)")
    span_start = intervals[0][0].replace(second=0, microsecond=0)
    span_end = intervals[-1][1]
    total_min = int((span_end - span_start).total_seconds() // 60)
    if total_min <= 24 * 60:
        return SRIResult(None, n_nights, 0, True, "관측 구간이 24시간 이하")
    asleep = bytearray(total_min)
    for s, e in intervals:
        i0 = max(0, int((s - span_start).total_seconds() // 60))
        i1 = min(total_min, int((e - span_start).total_seconds() // 60))
        for i in range(i0, i1):
            asleep[i] = 1
    day = 24 * 60
    agree = 0
    n_pairs = total_min - day
    for i in range(n_pairs):
        if asleep[i] == asleep[i + day]:
            agree += 1
    return SRIResult(200.0 * agree / n_pairs - 100.0, n_nights, n_pairs, False)


# ---------------------------------------------------------------------------
# 요약 통계 (정오 기준 프레임)
# ---------------------------------------------------------------------------

def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


@dataclass
class SleepRegularity:
    nights: List[Night]
    sri: SRIResult
    midsleep_mean_h: Optional[float]      # 정오 기준
    midsleep_sd_h: Optional[float]
    onset_mean_h: Optional[float]
    onset_sd_h: Optional[float]
    wake_mean_h: Optional[float]
    wake_sd_h: Optional[float]
    tst_mean_h: Optional[float]
    tst_sd_h: Optional[float]
    n_naps: int = 0
    # 사회적 시차
    sjl_hours: Optional[float] = None
    msw_h: Optional[float] = None         # 주중밤 평균 중간수면(정오 기준)
    msf_h: Optional[float] = None         # 주말밤(금·토)
    n_work: int = 0
    n_free: int = 0
    sjl_note: str = ""
    notes: List[str] = field(default_factory=list)


def analyze_sleep(intervals: List[Tuple[dt.datetime, dt.datetime]],
                  min_nights: int = MIN_NIGHTS_SRI) -> SleepRegularity:
    nights = group_nights(intervals)
    sri_res = sri(intervals, min_nights=min_nights)
    notes: List[str] = []

    mids = [_noon_anchor_hours(n.midsleep, n.date) for n in nights]
    onsets = [_noon_anchor_hours(n.onset, n.date) for n in nights]
    wakes = [_noon_anchor_hours(n.wake, n.date) for n in nights]
    tsts = [n.tst_hours for n in nights]
    n_naps = sum(len(n.naps) for n in nights)
    if n_naps:
        notes.append(f"낮잠/분리 수면 {n_naps}건 — 입면·기상·중간수면 통계에서 "
                     "제외, SRI에는 포함")
    for n in nights:
        if not (0.0 <= _noon_anchor_hours(n.onset, n.date) < 24.0):
            notes.append(f"{n.date} 밤: 정오 기준 프레임 밖의 수면(교대근무형?) — "
                         "시각 SD 해석에 주의")
            break

    reg = SleepRegularity(
        nights=nights, sri=sri_res,
        midsleep_mean_h=_mean(mids) if mids else None,
        midsleep_sd_h=_sd(mids),
        onset_mean_h=_mean(onsets) if onsets else None,
        onset_sd_h=_sd(onsets),
        wake_mean_h=_mean(wakes) if wakes else None,
        wake_sd_h=_sd(wakes),
        tst_mean_h=_mean(tsts) if tsts else None,
        tst_sd_h=_sd(tsts),
        n_naps=n_naps, notes=notes)

    work = [m for m, n in zip(mids, nights) if not n.is_weekend]
    free = [m for m, n in zip(mids, nights) if n.is_weekend]
    reg.n_work, reg.n_free = len(work), len(free)
    if work and free:
        reg.msw_h = _mean(work)
        reg.msf_h = _mean(free)
        reg.sjl_hours = abs(reg.msf_h - reg.msw_h)
    else:
        reg.sjl_note = ("주중밤/주말밤이 모두 있어야 계산합니다 "
                        f"(주중 {len(work)}밤, 주말 {len(free)}밤)")
    return reg
