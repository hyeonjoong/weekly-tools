"""rsalink.pace — 페이싱 동조 (--pace N 회/분).

- 동조 호흡 = 유효 호흡이고 호흡수가 목표 ±tol(기본 10%) 안.
- 동조율 = 동조 호흡 수 / 구간 안 전체 호흡 수(아티팩트 포함 — 분모를 줄이지 않는다).
- 첫 동조 시각 = 연속 run_len(기본 5) 호흡이 처음 동조한 그 첫 호흡의 흡기 시작 시각.
  아티팩트 호흡은 run 을 끊는다(호흡수를 모르는 호흡을 동조로 치지 않는다).
- 동조 중 호흡수 SD = 길이 ≥ run_len 인 동조 run 에 속한 호흡들의 호흡수 표본 SD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .breath import Breath


@dataclass
class PaceResult:
    target_bpm: float
    tol_frac: float
    run_len: int
    n_breaths: int
    n_within: int
    first_entrain_t: Optional[float]
    entrained_rates: List[float] = field(default_factory=list)

    @property
    def pct_within(self) -> float:
        return 100.0 * self.n_within / self.n_breaths if self.n_breaths else float("nan")

    @property
    def n_entrained(self) -> int:
        return len(self.entrained_rates)

    @property
    def mean_entrained(self) -> float:
        r = self.entrained_rates
        return sum(r) / len(r) if r else float("nan")

    @property
    def sd_entrained(self) -> float:
        r = self.entrained_rates
        if len(r) < 2:
            return float("nan")
        m = self.mean_entrained
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def pace_analysis(breaths: Sequence[Breath], target_bpm: float, tol_frac: float = 0.10,
                  run_len: int = 5, t0: Optional[float] = None, t1: Optional[float] = None) -> PaceResult:
    sel = [b for b in breaths
           if (t0 is None or b.t_onset >= t0) and (t1 is None or b.t_onset < t1)]
    lo, hi = target_bpm * (1 - tol_frac), target_bpm * (1 + tol_frac)
    within = [b.valid and lo <= b.rate_bpm <= hi for b in sel]
    n_within = sum(1 for w in within if w)
    first_t: Optional[float] = None
    entrained: List[float] = []
    i = 0
    n = len(sel)
    while i < n:
        if within[i]:
            j = i
            while j < n and within[j]:
                j += 1
            if j - i >= run_len:
                if first_t is None:
                    first_t = sel[i].t_onset
                entrained.extend(b.rate_bpm for b in sel[i:j])
            i = j
        else:
            i += 1
    return PaceResult(target_bpm=target_bpm, tol_frac=tol_frac, run_len=run_len, n_breaths=n,
                      n_within=n_within, first_entrain_t=first_t, entrained_rates=entrained)
