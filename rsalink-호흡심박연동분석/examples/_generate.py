#!/usr/bin/env python3
"""examples/_generate.py — 100% 합성 예시 2벌 생성 (고정 seed, stdlib 전용).

paced_6bpm/ : 기준선 0–5 분 호흡 12/분 → 자극 5–15 분 6/분 페이싱. 느린 호흡에서 RSA 진폭이
              커지도록(Hirsch & Bishop 1981 방향) A 25 → 45 ms.
sham/       : 호흡 12/분 고정, 5 분부터 RSA 진폭만 25 → 50 ms (호흡 불변·HF만 상승).

호흡 파형: 10 Hz, 값이 클수록 흡기(흉곽 확장). 위상 연속(순간 주파수 적분) + 3% 주기 지터
+ 진폭 변조 + 가우스 잡음. RR: 평균 900 ms, RSA 항 A·sin(θ+π) (흡기 중 RR 감소),
0.08 Hz LF 성분 8 ms, 백색 잡음 6 ms. RR 값은 그 RR 이 끝나는 박동 시각 기준.
실측 데이터가 아니며 어떤 사람의 기록도 아니다.
"""
from __future__ import annotations

import math
import os
import random
import sys
from typing import Callable, List, Tuple

FS_RESP = 10.0
SEED = 20260909


def _rate_profile_paced(t: float) -> float:
    return 12.0 if t < 300.0 else 6.0


def _rate_profile_sham(t: float) -> float:
    return 12.0


def _amp_profile_paced(t: float) -> float:
    return 25.0 if t < 300.0 else 45.0


def _amp_profile_sham(t: float) -> float:
    return 25.0 if t < 300.0 else 50.0


def synth(rate_bpm: Callable[[float], float], rsa_amp: Callable[[float], float],
          dur_s: float = 900.0, seed: int = SEED, rr_mean: float = 900.0,
          resp_noise: float = 0.08, rr_noise: float = 6.0):
    """→ (resp_t, resp_v, rr_ms 리스트). 호흡 위상 θ(t) 를 10 Hz 로 적분해 두 신호가 공유."""
    rnd = random.Random(seed)
    n = int(dur_s * FS_RESP)
    theta = [0.0] * n
    jitter = 1.0
    th = 0.0
    for i in range(1, n):
        t = i / FS_RESP
        # 호흡마다(위상이 2π 를 넘을 때) 주기 지터를 새로 뽑는다
        if int(theta[i - 1] / (2 * math.pi)) != int(th / (2 * math.pi)) or i == 1:
            jitter = 1.0 + 0.03 * rnd.gauss(0, 1)
        f = rate_bpm(t) / 60.0 * jitter
        th = theta[i - 1] + 2 * math.pi * f / FS_RESP
        theta[i] = th
    resp_t = [i / FS_RESP for i in range(n)]
    resp_v = []
    amp_mod = 1.0
    for i in range(n):
        if i % 600 == 0:
            amp_mod = 1.0 + 0.15 * rnd.gauss(0, 1)
        # 골(흡기 시작)이 뾰족하지 않도록 sin 을 그대로 쓰되 약간의 2차 조화 추가
        v = amp_mod * (math.sin(theta[i]) + 0.1 * math.sin(2 * theta[i]))
        resp_v.append(v + resp_noise * rnd.gauss(0, 1))

    def theta_at(t: float) -> float:
        pos = t * FS_RESP
        k = int(pos)
        if k >= n - 1:
            return theta[-1]
        f = pos - k
        return theta[k] * (1 - f) + theta[k + 1] * f

    rr: List[float] = []
    t = 0.0
    while t < dur_s:
        x = rr_mean
        for _ in range(3):
            te = t + x / 1000.0
            x = (rr_mean + rsa_amp(te) * math.sin(theta_at(te) + math.pi)
                 + 8.0 * math.sin(2 * math.pi * 0.08 * te))
        x += rr_noise * rnd.gauss(0, 1)
        t += x / 1000.0
        rr.append(x)
    return resp_t, resp_v, rr


SCENARIOS = {
    "paced_6bpm": (_rate_profile_paced, _amp_profile_paced),
    "sham": (_rate_profile_sham, _amp_profile_sham),
}


def write_scenario(name: str, out_dir: str) -> Tuple[str, str]:
    rate_fn, amp_fn = SCENARIOS[name]
    resp_t, resp_v, rr = synth(rate_fn, amp_fn)
    os.makedirs(out_dir, exist_ok=True)
    p_resp = os.path.join(out_dir, "호흡.csv")
    p_rr = os.path.join(out_dir, "RR.csv")
    with open(p_resp, "w", encoding="utf-8", newline="") as fh:
        fh.write("timestamp,value\n")
        for t, v in zip(resp_t, resp_v):
            fh.write(f"{t:.1f},{v:.4f}\n")
    with open(p_rr, "w", encoding="utf-8", newline="") as fh:
        fh.write("rr_ms\n")
        for x in rr:
            fh.write(f"{x:.1f}\n")
    return p_resp, p_rr


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    for name in SCENARIOS:
        p_resp, p_rr = write_scenario(name, os.path.join(here, name))
        print(f"{name}: {os.path.basename(p_resp)}, {os.path.basename(p_rr)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
