"""rsalink.rsa — RSA peak-valley (Grossman, van Beek & Wientjes 1990).

호흡마다: RSA_pv = max RR(호기 창) − min RR(흡기 창) [ms].
- RR 값은 그 **간격의 중점 시각**(t_beat − RR/2)에 배정한다. 끝 박동 시각에 배정하면
  RR/2(≈0.45 s) 의 기계적 지연이 생겨 극값이 창 밖으로 밀린다(라운드 1 A1).
- 흡기 창 = [t_onset + lag, t_peak + lag), 호기 창 = [t_peak + lag, t_end + lag).
  lag(`--rsa-lag`, 기본 1.0 s) = 호흡 → RR 의 생리적 위상 지연 허용치. RR 극값은 흡기 끝·
  호기 끝보다 조금 늦게 나타나므로 창을 그만큼 뒤로 민다. 극값이 창 오른쪽 끝을 넘으면
  박동 이산화 손실이 한쪽으로만 쌓이므로 창은 예상 지연 상한보다 넉넉히 민다.
- 호흡 안(흡기+호기)의 박동이 3개 미만이거나 어느 한 창이 비면 "판정 불가"(값 None).
- 아티팩트 플래그된 호흡은 계산하지 않는다(None, reason="artifact").
- 극값이 놓인 위상(호흡 주기 내 0–1, 0 = 흡기 시작, 1 = 다음 흡기 시작; lag 만큼 1 을
  넘을 수 있음)을 함께 기록한다 → 호흡별.csv 의 RR최소_위상·RR최대_위상.
요약: 유효 값의 중앙값·IQR(25–75%)·개수, 판정불가 개수, 호흡당 박동 수 평균.

인용 수치: Grossman 1990 은 peak-valley 가 스펙트럼·복합 디트렌드 법과 상호상관
>.92(개인 내 평균 .96)이고 호흡과의 상관이 복합 디트렌드법보다 높았다(.91 vs .84)고 보고했다.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .breath import Breath, BreathResult
from .parse import RRSeries, _median

# 기본 1.0 s (패널 제안 0.5 에서 상향): 중점 배정 뒤 창의 오른쪽 여유가 정확히 lag 이고
# 유효 지연 = τ − RR/2 가 τ 1.0 s·RR 900 ms 에서 0.55 s 이므로 0.5 면 τ 1.0 이 창 밖(회복 0.64–0.80).
# 1.0 이면 6–15/분·RR 500–900 에서 τ 0–1.0 s 회복 ≥ 0.93 (HARDENING 라운드 1 A1 표).
DEFAULT_RSA_LAG_S = 1.0


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
    phase_min: Optional[float] = None   # RR 최소(흡기 창)가 놓인 호흡 위상(0–1)
    phase_max: Optional[float] = None   # RR 최대(호기 창)가 놓인 호흡 위상(0–1)


def rsa_per_breath(br: BreathResult, rr: RRSeries, resp_offset: float = 0.0,
                   min_beats: int = 3, lag: float = DEFAULT_RSA_LAG_S) -> List[BreathRSA]:
    """resp_offset: 호흡 시각에 더할 초 — 호흡 기록이 RR 기록보다 늦게 시작했으면 양수
    (= 호흡 시작 절대시각 − RR 시작 절대시각; CLI `--resp-offset` 과 같은 규약, 라운드 2 #6).
    lag: 흡기/호기 창을 뒤로 미는 초(생리적 호흡→RR 위상 지연 허용치)."""
    out: List[BreathRSA] = []
    tm, rms = rr.t_mid, rr.rr_ms
    for b in br.breaths:
        t_on = b.t_onset + resp_offset + lag
        t_pk = b.t_peak + resp_offset + lag
        t_en = b.t_end + resp_offset + lag
        i0 = bisect.bisect_left(tm, t_on)
        i1 = bisect.bisect_left(tm, t_pk)
        i2 = bisect.bisect_left(tm, t_en)
        insp = rms[i0:i1]
        exp = rms[i1:i2]
        n = len(insp) + len(exp)
        mean_rr = (sum(insp) + sum(exp)) / n if n else None
        if not b.valid:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "artifact"))
            continue
        if n < min_beats:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "few_beats"))
            continue
        if not insp or not exp:
            out.append(BreathRSA(b, n, len(insp), len(exp), None, None, None, mean_rr, "empty_window"))
            continue
        k_min = i0 + min(range(len(insp)), key=insp.__getitem__)
        k_max = i1 + max(range(len(exp)), key=exp.__getitem__)
        mn, mx = rms[k_min], rms[k_max]
        period = b.period_s
        ph_min = (tm[k_min] - resp_offset - b.t_onset) / period if period > 0 else None
        ph_max = (tm[k_max] - resp_offset - b.t_onset) / period if period > 0 else None
        out.append(BreathRSA(b, n, len(insp), len(exp), mx, mn, mx - mn, mean_rr, "", ph_min, ph_max))
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
