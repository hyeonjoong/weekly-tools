#!/usr/bin/env python3
"""[개발 전용] numcheck 자체 구현 분포를 scipy 와 대조한다.

이 스크립트는 **런타임 의존성이 아니다.** numcheck 자체는 scipy 없이 돌아간다.
저장소 기준(statwise 가 세운 것)인 "scipy 대비 p 값 ≤1e-9 일치"를 확인할 때만
개발자가 손으로 돌린다.

    python3 dev/verify_against_scipy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from scipy import stats  # type: ignore
except ImportError:  # pragma: no cover - 개발 도구
    sys.exit("scipy 가 없습니다. 이 스크립트는 개발용입니다: pip install scipy")

from numcheck import dists

TOL_REL = 1e-9
TOL_ABS = 1e-300

worst = {"t": 0.0, "F": 0.0, "chi2": 0.0, "z": 0.0, "r": 0.0}
fails = []


def compare(kind, mine, theirs, label):
    if theirs == 0.0 and mine == 0.0:
        return
    denom = max(abs(theirs), TOL_ABS)
    rel = abs(mine - theirs) / denom
    worst[kind] = max(worst[kind], rel)
    if rel > TOL_REL:
        fails.append(f"{kind} {label}: mine={mine!r} scipy={theirs!r} rel={rel:.3g}")


# **큰 자유도까지 반드시 훑는다.** 예전 격자는 df ≤ 5000 에서 멈춰,
# _gser 가 조용히 잘려 χ²(df ≳ 13,500) 에서 48% 틀리던 것을 놓쳤다.
DFS = [1, 2, 3, 5, 8, 12, 20, 45, 88, 200, 1000, 5000,
       13_500, 33_333, 49_999, 50_000]
STATS = [0.0, 0.1, 0.5, 1.0, 1.96, 2.31, 3.0, 5.0, 8.0, 12.0, 20.0, 40.0]

for df in DFS:
    for t in STATS:
        compare("t", dists.t_two_tailed(t, df), 2 * stats.t.sf(abs(t), df), f"t={t} df={df}")
        compare("t", dists.t_sf(t, df), stats.t.sf(t, df), f"sf t={t} df={df}")
        compare("t", dists.t_sf(-t, df), stats.t.sf(-t, df), f"sf t={-t} df={df}")

for df in DFS:
    for x in [0.001, 0.5, 1.0, 3.84, 6.44, 10.0, 25.0, 60.0, 150.0, 400.0,
              df * 0.5, df * 0.9, float(df), df * 1.1, df * 2.0]:
        compare("chi2", dists.chi2_sf(x, df), stats.chi2.sf(x, df), f"x={x} df={df}")

for df1 in [1, 2, 3, 5, 10, 30, 1000, 49_999]:
    for df2 in [1, 2, 5, 12, 45, 88, 500, 49_999]:
        for f in [0.01, 0.5, 1.0, 2.5, 4.12, 8.0, 20.0, 100.0]:
            compare("F", dists.f_sf(f, df1, df2), stats.f.sf(f, df1, df2), f"F={f} ({df1},{df2})")

for z in [0.0, 0.5, 1.0, 1.96, 2.05, 3.0, 5.0, 8.0, 12.0, 20.0, 35.0]:
    compare("z", dists.z_two_tailed(z), 2 * stats.norm.sf(abs(z)), f"z={z}")
    compare("z", dists.z_sf(z), stats.norm.sf(z), f"sf z={z}")

# 촘촘한 무작위 격자 — 격자점 사이에서 기준을 넘는 곳이 없는지 본다
import random  # noqa: E402

rng = random.Random(20260813)
for _ in range(4000):
    df = rng.uniform(1.0, 50_000.0)
    t = rng.uniform(0.0, 12.0)
    compare("t", dists.t_two_tailed(t, df), 2 * stats.t.sf(t, df), f"rand t={t} df={df}")
    x = rng.uniform(0.1, 3.0) * df
    compare("chi2", dists.chi2_sf(x, df), stats.chi2.sf(x, df), f"rand x={x} df={df}")
    d1 = rng.uniform(1.0, 2000.0)
    f = rng.uniform(0.1, 12.0)
    compare("F", dists.f_sf(f, d1, df), stats.f.sf(f, d1, df), f"rand F={f} ({d1},{df})")

for df in [3, 8, 38, 100, 998]:
    for r in [0.0, 0.05, 0.21, 0.41, 0.6, 0.85, 0.97, 0.999]:
        t = abs(r) * (df / (1 - r * r)) ** 0.5
        compare("r", dists.r_two_tailed(r, df), 2 * stats.t.sf(t, df), f"r={r} df={df}")

# 비정수 자유도(Welch, Greenhouse–Geisser)
for df in [1.63, 4.7, 23.41, 87.9]:
    for t in [0.4, 2.31, 6.0]:
        compare("t", dists.t_two_tailed(t, df), 2 * stats.t.sf(t, df), f"t={t} df={df}")

print("최대 상대오차")
for kind, value in worst.items():
    print(f"  {kind:5s} {value:.3e}")
if fails:
    print(f"\n실패 {len(fails)}건")
    for line in fails[:20]:
        print("  " + line)
    sys.exit(1)
print(f"\n모두 통과 — 상대오차 ≤ {TOL_REL:g}")
