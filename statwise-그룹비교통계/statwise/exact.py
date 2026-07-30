"""Exact null distributions for rank tests — pure standard library.

Small clinical samples are common (n < 10 per arm), and there the asymptotic
normal approximation used by Mann-Whitney U / Wilcoxon signed-rank can be
noticeably off.  When the data contain **no ties** (and the sample is small
enough that enumeration is cheap) we compute the *exact* permutation p-value,
matching ``scipy.stats`` with ``method='exact'``.

Both distributions are built with a simple counting recurrence:

* Mann-Whitney U — ``count(m, n, u)`` = number of orderings of ``m`` + ``n``
  items giving statistic ``u``.  Recurrence (Mann & Whitney, 1947):
      count(m, n, u) = count(m-1, n, u-n) + count(m, n-1, u)
* Wilcoxon signed-rank W+ — number of subsets of ``{1..n}`` summing to ``w``:
      count(n, w) = count(n-1, w) + count(n-1, w-n)

The two-sided p-value convention mirrors SciPy: ``min(1, 2 * min(cdf, sf))``
with the observed statistic included in both tails.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Tuple

__all__ = [
    "mannwhitney_u_pmf",
    "mannwhitney_exact_p",
    "signed_rank_pmf",
    "signed_rank_exact_p",
    "MWU_EXACT_MAX_N",
    "SIGNED_RANK_EXACT_MAX_N",
]

# Enumeration stays cheap well past these bounds; the caps keep worst-case work
# and memory bounded and match the regime where an exact test is worthwhile.
MWU_EXACT_MAX_N = 30          # applies to each group size
SIGNED_RANK_EXACT_MAX_N = 50  # number of non-zero paired differences


@lru_cache(maxsize=512)
def mannwhitney_u_pmf(n1: int, n2: int) -> Tuple[float, ...]:
    """Probability mass function of Mann-Whitney U for sizes ``n1``/``n2``.

    Returns a list ``pmf`` of length ``n1*n2 + 1`` where ``pmf[u] = P(U = u)``
    under the null (no ties).  The counts are the Gaussian-binomial
    coefficients, built with the classic Mann & Whitney (1947) recurrence

        c(m, n, u) = c(m-1, n, u-n) + c(m, n-1, u)

    iterated bottom-up (no recursion) so it is safe for larger samples.
    """
    if n1 < 0 or n2 < 0:
        raise ValueError("group sizes must be non-negative")
    max_u = n1 * n2
    if n1 == 0 or n2 == 0:
        pmf = [0.0] * (max_u + 1)
        pmf[0] = 1.0
        return tuple(pmf)

    # prev_row[n] holds c(m-1, n, .) as a list over u for every n in 0..n2.
    # Base row m = 0: c(0, n, u) = 1 if u == 0 else 0.
    prev_row = [[0.0] * (max_u + 1) for _ in range(n2 + 1)]
    for n in range(n2 + 1):
        prev_row[n][0] = 1.0

    for m in range(1, n1 + 1):
        cur_row = [[0.0] * (max_u + 1) for _ in range(n2 + 1)]
        # c(m, 0, u) = 1 if u == 0 else 0
        cur_row[0][0] = 1.0
        for n in range(1, n2 + 1):
            up = prev_row[n]      # c(m-1, n, .)
            left = cur_row[n - 1]  # c(m, n-1, .)
            cur = cur_row[n]
            for u in range(max_u + 1):
                val = left[u]
                if u - n >= 0:
                    val += up[u - n]
                cur[u] = val
        prev_row = cur_row

    counts = prev_row[n2]
    total = sum(counts)
    # A tuple, not a list: the result is memoized and must not be mutable, or a
    # caller could corrupt the cached null distribution for every later pair.
    return tuple(c / total for c in counts)


def mannwhitney_exact_p(u: float, n1: int, n2: int) -> float:
    """Two-sided exact Mann-Whitney p-value for observed statistic ``u``."""
    pmf = mannwhitney_u_pmf(n1, n2)
    ui = int(round(u))
    ui = max(0, min(ui, n1 * n2))
    cdf = sum(pmf[:ui + 1])
    sf = sum(pmf[ui:])
    return min(1.0, 2.0 * min(cdf, sf))


@lru_cache(maxsize=512)
def signed_rank_pmf(n: int) -> Tuple[float, ...]:
    """PMF of the Wilcoxon signed-rank W+ statistic for ``n`` ranks (no ties).

    ``pmf[w] = P(W+ = w)`` for ``w`` in ``0 .. n(n+1)/2``.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    max_w = n * (n + 1) // 2
    counts = [0.0] * (max_w + 1)
    counts[0] = 1.0
    running_max = 0
    for k in range(1, n + 1):
        running_max += k
        # subset-sum: include rank k or not (iterate downward to avoid reuse)
        for w in range(running_max, k - 1, -1):
            counts[w] += counts[w - k]
    total = 2.0 ** n
    return tuple(c / total for c in counts)


def signed_rank_exact_p(w: float, n: int) -> float:
    """Two-sided exact Wilcoxon signed-rank p-value.

    ``w`` is the test statistic ``min(W+, W-)`` and ``n`` the number of
    non-zero differences.
    """
    if n == 0:
        return 1.0
    pmf = signed_rank_pmf(n)
    wi = int(round(w))
    max_w = n * (n + 1) // 2
    wi = max(0, min(wi, max_w))
    cdf = sum(pmf[:wi + 1])
    sf = sum(pmf[wi:])
    return min(1.0, 2.0 * min(cdf, sf))
