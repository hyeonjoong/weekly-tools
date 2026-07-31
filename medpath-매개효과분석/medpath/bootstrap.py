"""Bootstrap resampling and confidence intervals — pure standard library.

The indirect effect ``a*b`` is a product of estimates, so its sampling
distribution is skewed and the usual "estimate ± 1.96 SE" interval is wrong
in a way that matters (it is why Sobel's test is underpowered). The bootstrap
percentile interval is what Preacher & Hayes recommend and what SPSS PROCESS
reports by default; BC and BCa are offered for the skewed/small-N cases.

Resampling is **case (row) resampling**: one bootstrap replicate draws N rows
with replacement and refits *every* equation of the model on the same drawn
rows, which is what keeps the paths mutually consistent.
"""

from __future__ import annotations

import math
from random import Random
from typing import Dict, List, Optional, Sequence, Tuple

from .linalg import GramCache
from .special import norm_cdf, norm_ppf

__all__ = ["EffectPlan", "BootResult", "run_bootstrap", "quantile",
           "percentile_ci", "bc_ci", "bca_ci", "ci_from_boots",
           "jackknife_acceleration", "jackknife_values", "acceleration_from"]

# Replicates per work unit. Fixed (independent of --jobs) so that results are
# byte-identical no matter how many worker processes are used.
CHUNK = 250


class EffectPlan:
    """How to turn one accumulated Gram matrix into the effect vector.

    Holds only ints/lists so it pickles cheaply to worker processes.
    The produced vector is
    ``[total c, direct c', specific indirect 1..P, total indirect]``.
    """

    __slots__ = ("k", "serial", "m_pred", "m_out", "m_x_pos", "m_prior_pos",
                 "y_pred", "y_out", "y_x_pos", "y_m_pos",
                 "t_pred", "t_out", "t_x_pos", "paths")

    def __init__(self, k: int, serial: bool,
                 m_pred: List[List[int]], m_out: List[int],
                 m_x_pos: List[int], m_prior_pos: List[Dict[int, int]],
                 y_pred: List[int], y_out: int, y_x_pos: int, y_m_pos: List[int],
                 t_pred: List[int], t_out: int, t_x_pos: int,
                 paths: List[Tuple[int, ...]]):
        self.k = k
        self.serial = serial
        self.m_pred = m_pred
        self.m_out = m_out
        self.m_x_pos = m_x_pos
        self.m_prior_pos = m_prior_pos
        self.y_pred = y_pred
        self.y_out = y_out
        self.y_x_pos = y_x_pos
        self.y_m_pos = y_m_pos
        self.t_pred = t_pred
        self.t_out = t_out
        self.t_x_pos = t_x_pos
        self.paths = paths

    @property
    def size(self) -> int:
        return 2 + len(self.paths) + 1

    def compute(self, cache: GramCache, acc: Sequence[float]) -> Optional[List[float]]:
        betas_m = []
        for j in range(self.k):
            b = cache.solve(acc, self.m_pred[j], self.m_out[j])
            if b is None:
                return None
            betas_m.append(b)
        by = cache.solve(acc, self.y_pred, self.y_out)
        if by is None:
            return None
        bt = cache.solve(acc, self.t_pred, self.t_out)
        if bt is None:
            return None
        a = [betas_m[j][self.m_x_pos[j]] for j in range(self.k)]
        b_paths = [by[self.y_m_pos[i]] for i in range(self.k)]
        c_direct = by[self.y_x_pos]
        c_total = bt[self.t_x_pos]
        inds = []
        for path in self.paths:
            val = a[path[0]]
            for t in range(len(path) - 1):
                val *= betas_m[path[t + 1]][self.m_prior_pos[path[t + 1]][path[t]]]
            val *= b_paths[path[-1]]
            inds.append(val)
        return [c_total, c_direct] + inds + [math.fsum(inds)]


class BootResult:
    """Bootstrap replicate matrix, transposed to one list per statistic."""

    def __init__(self, columns: List[List[float]], requested: int, failed: int):
        self.columns = columns
        self.requested = requested
        self.failed = failed
        self.n_ok = len(columns[0]) if columns else 0


def _weights(rng: Random, n: int) -> List[Tuple[int, float]]:
    counts: Dict[int, int] = {}
    randrange = rng.randrange
    for _ in range(n):
        i = randrange(n)
        counts[i] = counts.get(i, 0) + 1
    return [(i, float(c)) for i, c in counts.items()]


def _run_chunk(cache: GramCache, plan: EffectPlan, seed: int,
               chunk_index: int, reps: int) -> Tuple[List[List[float]], int]:
    rng = Random("medpath-%d-%d" % (seed, chunk_index))
    n = cache.n
    out: List[List[float]] = []
    failed = 0
    for _ in range(reps):
        acc = cache.weighted_acc(_weights(rng, n))
        vec = plan.compute(cache, acc)
        if vec is None:
            failed += 1
        else:
            out.append(vec)
    return out, failed


# --- multiprocessing plumbing (workers get the data once, via initializer) --
_G_CACHE: Optional[GramCache] = None
_G_PLAN: Optional[EffectPlan] = None
_G_SEED: int = 0


def _init_worker(cache: GramCache, plan: EffectPlan, seed: int) -> None:  # pragma: no cover
    global _G_CACHE, _G_PLAN, _G_SEED
    _G_CACHE, _G_PLAN, _G_SEED = cache, plan, seed


def _worker(task: Tuple[int, int]) -> Tuple[List[List[float]], int]:  # pragma: no cover
    chunk_index, reps = task
    assert _G_CACHE is not None and _G_PLAN is not None
    return _run_chunk(_G_CACHE, _G_PLAN, _G_SEED, chunk_index, reps)


def run_bootstrap(cache: GramCache, plan: EffectPlan, n_boot: int, seed: int,
                  jobs: int = 1) -> BootResult:
    """Draw ``n_boot`` case-resampled replicates of the effect vector.

    Deterministic for a given ``seed`` and ``n_boot`` **regardless of
    ``jobs``** — work is split into fixed-size chunks with chunk-derived
    seeds, and results are reassembled in chunk order.
    """
    if n_boot <= 0:
        return BootResult([[] for _ in range(plan.size)], 0, 0)
    tasks = []
    remaining = n_boot
    idx = 0
    while remaining > 0:
        reps = min(CHUNK, remaining)
        tasks.append((idx, reps))
        remaining -= reps
        idx += 1

    results: List[Tuple[List[List[float]], int]] = []
    used_parallel = False
    if jobs > 1 and len(tasks) > 1:
        try:  # pragma: no cover - exercised opportunistically
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=min(jobs, len(tasks)),
                                     initializer=_init_worker,
                                     initargs=(cache, plan, seed)) as pool:
                results = list(pool.map(_worker, tasks))
            used_parallel = True
        except Exception:
            results = []
            used_parallel = False
    if not used_parallel:
        results = [_run_chunk(cache, plan, seed, ci, reps) for ci, reps in tasks]

    size = plan.size
    columns: List[List[float]] = [[] for _ in range(size)]
    failed = 0
    for vecs, nfail in results:
        failed += nfail
        for vec in vecs:
            for s in range(size):
                columns[s].append(vec[s])
    return BootResult(columns, n_boot, failed)


def jackknife_values(cache: GramCache, plan: EffectPlan) -> List[List[float]]:
    """Leave-one-out effect vectors, one per usable row.

    Cheap because the full accumulator is linear in rows: leaving out row i is
    a single vector subtraction, not a refit over N-1 rows. Computed **once**
    per model — every BCa acceleration (each indirect effect and each pairwise
    contrast) is then a reduction over this matrix rather than its own O(N)
    sweep of Cholesky solves.
    """
    full = cache.full_acc()
    out: List[List[float]] = []
    for i in range(cache.n):
        vec = plan.compute(cache, cache.acc_minus_row(full, i))
        if vec is not None:
            out.append(vec)
    return out


def acceleration_from(values: Sequence[float]) -> Optional[float]:
    """BCa acceleration constant from a set of leave-one-out estimates."""
    if len(values) < 3:
        return None
    mbar = math.fsum(values) / len(values)
    num = math.fsum((mbar - v) ** 3 for v in values)
    den = math.fsum((mbar - v) ** 2 for v in values)
    if den <= 0:
        return None
    return num / (6.0 * den ** 1.5)


def jackknife_acceleration(cache: GramCache, plan: EffectPlan,
                           stat_index: int,
                           minus_index: Optional[int] = None,
                           jack: Optional[Sequence[Sequence[float]]] = None
                           ) -> Optional[float]:
    """BCa acceleration for one statistic, or for a difference of two.

    ``minus_index`` makes the statistic ``vec[stat_index] - vec[minus_index]``,
    which is what a contrast between two specific indirect effects is — so
    contrasts get a genuine BCa interval instead of silently degrading to BC.
    Pass ``jack`` (from :func:`jackknife_values`) to reuse one sweep.
    """
    rows = jack if jack is not None else jackknife_values(cache, plan)
    if minus_index is None:
        vals = [v[stat_index] for v in rows]
    else:
        vals = [v[stat_index] - v[minus_index] for v in rows]
    return acceleration_from(vals)


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation (type 7 / numpy default) quantile."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_vals[0]
    h = (n - 1) * min(max(q, 0.0), 1.0)
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    frac = h - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def percentile_ci(boots: Sequence[float], conf: float) -> Tuple[float, float]:
    s = sorted(boots)
    alpha = 1.0 - conf
    return quantile(s, alpha / 2.0), quantile(s, 1.0 - alpha / 2.0)


def _z0(boots: Sequence[float], observed: float) -> Tuple[float, bool]:
    """Bias-correction constant, with a mid-p tie correction and clamping."""
    b = len(boots)
    less = sum(1 for v in boots if v < observed)
    ties = sum(1 for v in boots if v == observed)
    p0 = (less + 0.5 * ties) / b
    clamped = False
    lo, hi = 1.0 / (b + 1.0), b / (b + 1.0)
    if p0 <= 0.0 or p0 >= 1.0 or p0 < lo or p0 > hi:
        p0 = min(max(p0, lo), hi)
        clamped = True
    return norm_ppf(p0), clamped


def _adjusted(z0: float, acc: float, z: float) -> float:
    denom = 1.0 - acc * (z0 + z)
    if abs(denom) < 1e-12:
        denom = math.copysign(1e-12, denom if denom != 0 else 1.0)
    return norm_cdf(z0 + (z0 + z) / denom)


def bc_ci(boots: Sequence[float], observed: float, conf: float
          ) -> Tuple[float, float, bool]:
    return bca_ci(boots, observed, conf, 0.0)


def bca_ci(boots: Sequence[float], observed: float, conf: float, acc: float
           ) -> Tuple[float, float, bool]:
    s = sorted(boots)
    z0, clamped = _z0(s, observed)
    alpha = 1.0 - conf
    z_lo = norm_ppf(alpha / 2.0)
    z_hi = norm_ppf(1.0 - alpha / 2.0)
    a1 = _adjusted(z0, acc, z_lo)
    a2 = _adjusted(z0, acc, z_hi)
    return quantile(s, a1), quantile(s, a2), clamped


def ci_from_boots(boots: Sequence[float], observed: float, conf: float,
                  method: str, acc: Optional[float] = None
                  ) -> Tuple[float, float, List[str]]:
    """Dispatch to the requested interval, returning any warnings raised."""
    warns: List[str] = []
    if not boots:
        return float("nan"), float("nan"), ["부트스트랩 표본이 없습니다."]
    if method == "percentile":
        lo, hi = percentile_ci(boots, conf)
        return lo, hi, warns
    if method == "bc":
        lo, hi, clamped = bc_ci(boots, observed, conf)
    elif method == "bca":
        if acc is None:
            acc = 0.0
            warns.append("가속(acceleration) 계산에 실패해 BC 구간으로 대체했습니다.")
        lo, hi, clamped = bca_ci(boots, observed, conf, acc)
    else:
        raise ValueError("unknown CI method: %r" % (method,))
    if clamped:
        warns.append(
            "부트스트랩 분포가 한쪽으로 완전히 치우쳐 편향보정 상수를 절단했습니다 "
            "— 구간을 신뢰하기 어렵습니다(표본이 작거나 효과 추정이 불안정).")
    return lo, hi, warns
