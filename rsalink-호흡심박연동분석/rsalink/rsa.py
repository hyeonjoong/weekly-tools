"""rsalink.rsa — RSA peak-valley (Grossman, van Beek & Wientjes 1990).

호흡마다: RSA_pv = max RR(호기 창) − min RR(흡기 창) [ms].
- 흡기 창 = [t_onset, t_peak), 호기 창 = [t_peak, t_end).
- RR 값은 그 RR 이 끝나는 박동 시각(t_beat)으로 창에 배정한다.
- 호흡 안(흡기+호기)의 박동이 3개 미만이거나 어느 한 창이 비면 "판정 불가"(값 None).
- 아티팩트 플래그된 호흡은 계산하지 않는다(None, reason="artifact").
요약: 유효 값의 중앙값·IQR(25–75%)·개수, 판정불가 개수, 호흡당 박동 수 평균.

인용 수치: Grossman 1990 은 peak-valley 가 스펙트럼·복합 디트렌드 법과 상호상관
>.92(개인 내 평균 .96)이고 호흡과의 상관이 가장 높았다(.91 vs .84)고 보고했다.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .breath import Breath, BreathResult
from .parse import RRSeries, _median


@dataclass
class BreathRSA:
    breath: Breath
    n_beats: int
    n_insp: int
    n_exp: int
    rr_max_exp: Optional[float]
    rr_min_insp: Optional[float]
    rsa_pv: Optional[float]         # ms, None = 판정 불가
    mean_rr: Optional[float]
    reason: str = ""                # "" | "artifact" | "few_beats" | "empty_window"


def _beats_in(t_beat: Sequence[float], rr_ms: Sequence[float], t0: float, t1: float) -> List[float]:
    i0 = bisect.bisect_left(t_beat, t0)
    i1 = bisect.bisect_left(t_beat, t1)
    return list(rr_ms[i0:i1])


def rsa_per_breath(br: BreathResult, rr: RRSeries, resp_offset: float = 0.0,
                   min_beats: int = 3) -> List[BreathRSA]:
    """resp_offset: 호흡 시각에 더할 초(호흡 시계가 RR 시계보다 앞서면 양수)."""
    out: List[BreathRSA] = []
    tb, rms = rr.t_beat, rr.rr_ms
    for b in br.breaths:
        t_on, t_pk, t_en = b.t_onset + resp_offset, b.t_peak + resp_offset, b.t_end + resp_offset
        insp = _beats_in(tb, rms, t_on, t_pk)
        exp = _beats_in(tb, rms, t_pk, t_en)
        n = len(insp) + len(exp)
        allb = insp + exp
        mean_rr = sum(allb) / n if n else None
        if not b.valid:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "artifact"))
            continue
        if n < min_beats:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "few_beats"))
            continue
        if not insp or not exp:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "empty_window"))
            continue
        mx, mn = max(exp), min(insp)
        out.append(BreathRSA(b, n, len(insp), len(exp), mx, mn, mx - mn, mean_rr, ""))
    return out


def summarize(rows: Sequence[BreathRSA], t0: Optional[float] = None, t1: Optional[float] = None) -> dict:
    sel = [r for r in rows if (t0 is None or r.breath.t_onset >= t0) and (t1 is None or r.breath.t_onset < t1)]
    vals = [r.rsa_pv for r in sel if r.rsa_pv is not None]
    und = [r for r in sel if r.rsa_pv is None and r.reason in ("few_beats", "empty_window")]
    beats = [r.n_beats for r in sel if r.reason != "artifact"]
    s = sorted(vals)
    n = len(s)

    def q(p):
        if n == 0:
            return float("nan")
        pos = p * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return {
        "n": n,
        "median": _median(vals) if vals else float("nan"),
        "q1": q(0.25), "q3": q(0.75),
        "mean": sum(vals) / n if n else float("nan"),
        "n_undetermined": len(und),
        "n_breaths": len(sel),
        "beats_per_breath": sum(beats) / len(beats) if beats else float("nan"),
        "mean_rr": (sum(r.mean_rr for r in sel if r.mean_rr is not None)
                    / max(1, sum(1 for r in sel if r.mean_rr is not None))) if sel else float("nan"),
    }
