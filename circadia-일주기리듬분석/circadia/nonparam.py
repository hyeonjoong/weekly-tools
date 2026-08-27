"""비모수 일주기 지표 — IS·IV·L5/M10·RA (시간당 빈).

공식 출처: Van Someren et al. 1999, "Bright light therapy: improved sensitivity
to its effects on rest-activity rhythms of demented persons ...", Chronobiol Int
16(4):505-518 — IS/IV 정의는 원래 Witting et al. 1990 (Biol Psychiatry 27:563)
에서 나왔고 Van Someren 1999 이 시간당 빈 관례를 굳혔다.

시간당 빈 x_i (i=1..n, 하루 p=24 빈, 시간대 h의 전일 평균 x̄_h, 전체 평균 x̄):

    IS = ( n · Σ_{h=1..p} (x̄_h − x̄)² ) / ( p · Σ_{i=1..n} (x_i − x̄)² )
    IV = ( n · Σ_{i=2..n} (x_i − x_{i−1})² ) / ( (n−1) · Σ_{i=1..n} (x_i − x̄)² )

- IS ∈ [0,1]: 매일 같은 24h 패턴이면 정확히 1 (테스트로 고정).
- IV: 매시간 값이 ±d 로 교대하는 극단 패턴에서 정확히 4 (테스트로 고정).
  IV 의 연속차 합은 '시간상 연속인' 빈 사이에서만 유효하므로, 유효일이
  달력에서 연속인 구간(run) 안에서만 차분을 취한다(비연속 유효일 사이의
  차분은 하룻밤 건너뛴 갭이지 '시간 전이'가 아니다 — README 한계 참조).

L5/M10 (같은 논문): 유효일 평균 24h 프로파일에서 연속 5시간 최저 평균(L5)·
연속 10시간 최고 평균(M10)과 그 구간의 중앙 시각. 구간은 자정을 넘어
감을 수 있다(wrap). 동률이면 이른 시작 시각을 취한다(결정론).
RA = (M10 − L5) / (M10 + L5).

유효일 규칙 (보간 금지 원칙의 결과):
- 시간당 빈은 그 시간에 표본이 1개라도 있으면 채워진 것으로 본다
  (심박=평균, 걸음=합).
- '유효일' = 24개 빈이 전부 채워진 달력일. 빈 빈을 평균으로 메우는 것은
  보간이므로 하지 않는다 — 부족하면 그 날을 버리고 자백한다.
- IS/IV 는 유효일 ≥ MIN_DAYS(기본 5)일 때만 계산. 미만이면 값 대신
  '데이터 부족'(report 층에서 처리).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MIN_DAYS_IS_IV = 5      # 권장 7일 — Van Someren 계열 관례
P_BINS = 24


# ---------------------------------------------------------------------------
# 시간당 빈
# ---------------------------------------------------------------------------

@dataclass
class HourlyBinned:
    # 날짜 -> [24개 값 또는 None]
    days: "Dict[dt.date, List[Optional[float]]]"
    valid_days: List[dt.date]          # 24/24 빈이 채워진 날(정렬)
    dropped_days: List[Tuple[dt.date, int]]   # (날짜, 채워진 빈 수)
    agg: str                            # "mean" | "sum"


def hourly_bin(samples: Sequence[Tuple[dt.datetime, float]],
               agg: str) -> HourlyBinned:
    """(datetime, 값) 시계열 → 달력일×24 시간당 빈. agg: 'mean'(심박)/'sum'(걸음)."""
    assert agg in ("mean", "sum")
    acc: Dict[dt.date, List[List[float]]] = {}
    for stamp, v in samples:
        d = stamp.date()
        if d not in acc:
            acc[d] = [[] for _ in range(P_BINS)]
        acc[d][stamp.hour].append(v)
    days: Dict[dt.date, List[Optional[float]]] = {}
    valid: List[dt.date] = []
    dropped: List[Tuple[dt.date, int]] = []
    for d in sorted(acc):
        row: List[Optional[float]] = []
        for bucket in acc[d]:
            if not bucket:
                row.append(None)
            elif agg == "mean":
                row.append(sum(bucket) / len(bucket))
            else:
                row.append(sum(bucket))
        days[d] = row
        n_filled = sum(1 for v in row if v is not None)
        if n_filled == P_BINS:
            valid.append(d)
        else:
            dropped.append((d, n_filled))
    return HourlyBinned(days, valid, dropped, agg)


def mean_profile(binned: HourlyBinned,
                 days: Optional[Sequence[dt.date]] = None) -> Optional[List[float]]:
    """유효일들의 평균 24h 프로파일. 유효일이 없으면 None."""
    use = list(days) if days is not None else binned.valid_days
    if not use:
        return None
    prof = []
    for h in range(P_BINS):
        vals = [binned.days[d][h] for d in use]
        prof.append(sum(vals) / len(vals))  # 유효일이므로 None 없음
    return prof


# ---------------------------------------------------------------------------
# IS / IV
# ---------------------------------------------------------------------------

@dataclass
class ISIVResult:
    is_: Optional[float]
    iv: Optional[float]
    n_days: int
    insufficient: bool          # 유효일 < MIN_DAYS_IS_IV
    note: str = ""


def is_iv(binned: HourlyBinned, min_days: int = MIN_DAYS_IS_IV) -> ISIVResult:
    days = binned.valid_days
    n_days = len(days)
    if n_days < min_days:
        return ISIVResult(None, None, n_days, True,
                          f"데이터 부족({n_days}일<{min_days}일)")
    series: List[float] = []
    for d in days:
        series.extend(binned.days[d])          # type: ignore[arg-type]
    n = len(series)
    mean_all = sum(series) / n
    denom = sum((x - mean_all) ** 2 for x in series)
    if denom <= 1e-12:
        # 상수 시계열: 리듬도 잡음도 없음 — IS/IV 정의 불가(0/0)
        return ISIVResult(None, None, n_days, False,
                          "시계열이 상수라 IS/IV가 정의되지 않습니다")
    prof = mean_profile(binned, days)
    assert prof is not None
    between = sum((ph - mean_all) ** 2 for ph in prof)
    is_val = (n * between) / (P_BINS * denom)

    # IV: 달력상 연속인 유효일 run 안의 연속 시간 빈 차분만 사용
    sq_diff = 0.0
    n_diff = 0
    prev_day: Optional[dt.date] = None
    prev_val: Optional[float] = None
    for d in days:
        contiguous = prev_day is not None and (d - prev_day) == dt.timedelta(days=1)
        for h in range(P_BINS):
            v = binned.days[d][h]
            assert v is not None
            if prev_val is not None and (h > 0 or contiguous):
                sq_diff += (v - prev_val) ** 2
                n_diff += 1
            prev_val = v
        prev_day = d
    note = ""
    if n_diff < n - 1:
        note = "유효일이 달력에서 비연속 — IV 차분은 연속 구간 안에서만 취함"
    # 문헌 공식은 연속 기록 기준 n·Σdiff²/((n−1)·Σdev²)이며, 여기서
    # (n−1) 자리에 실제 사용한 차분 개수 n_diff 를 쓴다(연속 기록이면 동일).
    iv_val = (n * sq_diff) / (n_diff * denom) if n_diff else None
    return ISIVResult(is_val, iv_val, n_days, False, note)


# ---------------------------------------------------------------------------
# L5 / M10 / RA
# ---------------------------------------------------------------------------

@dataclass
class L5M10Result:
    l5: float
    l5_onset_hour: int
    l5_mid_hours: float          # 중앙 시각(시간) — 예: 시작 01시, 5h → 3.5
    m10: float
    m10_onset_hour: int
    m10_mid_hours: float
    ra: Optional[float]          # L5+M10=0 이면 None
    n_days: int


def _window_means(profile: List[float], width: int) -> List[float]:
    """자정 wrap 허용, 시작 시각 h(0..23)별 연속 width시간 평균."""
    return [sum(profile[(h + k) % P_BINS] for k in range(width)) / width
            for h in range(P_BINS)]


def l5m10(binned: HourlyBinned) -> Optional[L5M10Result]:
    prof = mean_profile(binned)
    if prof is None:
        return None
    w5 = _window_means(prof, 5)
    w10 = _window_means(prof, 10)
    l5_h = min(range(P_BINS), key=lambda h: (w5[h], h))    # 동률→이른 시작
    m10_h = min(range(P_BINS), key=lambda h: (-w10[h], h))
    l5_v, m10_v = w5[l5_h], w10[m10_h]
    ra = None
    if (m10_v + l5_v) > 1e-12:
        ra = (m10_v - l5_v) / (m10_v + l5_v)
    return L5M10Result(
        l5=l5_v, l5_onset_hour=l5_h, l5_mid_hours=(l5_h + 5 / 2.0) % 24,
        m10=m10_v, m10_onset_hour=m10_h, m10_mid_hours=(m10_h + 10 / 2.0) % 24,
        ra=ra, n_days=len(binned.valid_days))


# ---------------------------------------------------------------------------
# 커버리지(착용률)와 갭 — 자백용
# ---------------------------------------------------------------------------

@dataclass
class Coverage:
    span_start: dt.datetime
    span_end: dt.datetime
    n_hour_bins: int             # 구간 안 총 시간 빈 수
    n_covered: int               # 표본이 있는 시간 빈 수
    wear_rate: float             # n_covered / n_hour_bins
    gaps: List[Tuple[dt.datetime, dt.datetime]] = field(default_factory=list)


def coverage(samples: Sequence[Tuple[dt.datetime, float]],
             gap_min_hours: float = 3.0) -> Coverage:
    """첫~마지막 표본 사이 시간 빈 커버율과 갭(기본 3h 이상) 목록."""
    stamps = [s for s, _ in samples]
    start, end = stamps[0], stamps[-1]
    first_bin = start.replace(minute=0, second=0, microsecond=0)
    n_bins = int((end - first_bin).total_seconds() // 3600) + 1
    covered = set()
    for s in stamps:
        covered.add(int((s.replace(minute=0, second=0, microsecond=0)
                         - first_bin).total_seconds() // 3600))
    gaps = []
    thr = dt.timedelta(hours=gap_min_hours)
    for a, b in zip(stamps, stamps[1:]):
        if (b - a) >= thr:
            gaps.append((a, b))
    return Coverage(start, end, n_bins, len(covered),
                    len(covered) / n_bins if n_bins else 0.0, gaps)
