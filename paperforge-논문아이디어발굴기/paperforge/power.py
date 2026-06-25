"""Closed-form sample-size approximations (stdlib only).

These are deliberately simple, well-known closed forms so every number can be
hand-checked against published tables. They are *planning* approximations, not a
substitute for a full power analysis (e.g. G*Power), and we say so in the report.

References
---------
- Correlation: Fisher z-transform approximation. For r=0.30, alpha=.05 (two
  sided), power=.80 this yields n≈85, matching standard tables.
- Two-group mean comparison (Cohen's d): the normal approximation
  n_per_group = 2 (z_{1-a/2} + z_{1-b})^2 / d^2. For d=0.5 this yields 63/group.

Note: the normal approximation can land ~1 subject/group below exact
non-central-t tools (e.g. G*Power, which gives 64/group for d=0.5). These are
*planning* estimates only; the report says so and recommends confirming final
power in a dedicated tool. Because the danger for a feasibility check is
*under*-stating the required N, we always round up (ceil) and pair every number
with conservative (small-to-medium) effect-size priors.
"""
from __future__ import annotations

import math

# Standard normal quantiles for the alpha/power values we actually use.
# Hard-coded (rather than inverting the normal CDF) so the constants are
# transparent and the results are exactly reproducible across machines.
_Z_ALPHA_TWO_SIDED = {0.05: 1.959963985, 0.01: 2.575829304, 0.10: 1.644853627}
_Z_POWER = {0.80: 0.841621234, 0.90: 1.281551566, 0.95: 1.644853627}


def _z(table: dict, key: float, what: str) -> float:
    # Exact membership only — we advertise a fixed set of supported values, so a
    # near-miss (e.g. alpha=0.0501) should be rejected, not silently snapped.
    if key in table:
        return table[key]
    raise ValueError(f"Unsupported {what}={key}. Supported: {sorted(table)}")


def n_for_correlation(r: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Total N to detect a Pearson correlation of magnitude ``r``.

    Uses the Fisher z approximation::

        C = 0.5 * ln((1+|r|)/(1-|r|))
        N = ((z_{1-a/2} + z_{1-b}) / C)^2 + 3
    """
    r = abs(float(r))
    if not 0 < r < 1:
        raise ValueError("r must be strictly between 0 and 1")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    c = 0.5 * math.log((1 + r) / (1 - r))
    n = ((za + zb) / c) ** 2 + 3
    return math.ceil(n)


def n_per_group_two_means(d: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Per-group N to detect a standardized mean difference ``d`` (Cohen's d).

    Normal approximation::

        n_per_group = 2 (z_{1-a/2} + z_{1-b})^2 / d^2
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    n = 2 * (za + zb) ** 2 / d ** 2
    return math.ceil(n)


def n_for_paired(d: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Number of *subjects* (pairs) for a within-subject / paired comparison.

    Normal approximation on difference scores::

        n_pairs = (z_{1-a/2} + z_{1-b})^2 / d_z^2 + 1

    where ``d_z`` is the standardized mean of the difference scores. For d_z=0.5
    this gives 33 subjects — far fewer than the 126 a between-groups design would
    need, which is the whole point of a repeated-measures design.
    """
    d = abs(float(d))
    if d <= 0:
        raise ValueError("d must be > 0")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    n = (za + zb) ** 2 / d ** 2 + 1
    return math.ceil(n)


def required_total_n(effect: dict, alpha: float = 0.05, power: float = 0.80):
    """Required *total* N for an effect spec, or ``None`` when not applicable.

    ``effect`` is one of::

        {"type": "correlation", "r": 0.3}
        {"type": "two_group", "d": 0.5}        # total = 2 * per-group
        {"type": "paired", "d": 0.5}           # within-subject; total = n pairs
        {"type": "regression", "f2": 0.15, "k": 3}
        {"type": "exploratory"}                # no closed-form target -> None
    """
    etype = effect.get("type")
    if etype == "correlation":
        return n_for_correlation(effect["r"], alpha, power)
    if etype == "two_group":
        return 2 * n_per_group_two_means(effect["d"], alpha, power)
    if etype == "paired":
        return n_for_paired(effect["d"], alpha, power)
    if etype == "regression":
        return n_for_regression(effect["f2"], effect.get("k", 1), alpha, power)
    if etype == "exploratory":
        return None
    raise ValueError(f"Unknown effect type: {etype!r}")


def n_for_regression(f2: float, k: int, alpha: float = 0.05, power: float = 0.80) -> int:
    """Total N for a multiple-regression R^2 / coefficient test.

    Uses the standard non-central-F normal approximation::

        N = (z_{1-a/2} + z_{1-b})^2 / f2 + k + 1

    where ``f2`` is Cohen's f-squared and ``k`` the number of predictors. For
    f2=0.15 (medium), k=3 this gives N≈57, in line with common rules of thumb.
    """
    f2 = float(f2)
    k = int(k)
    if f2 <= 0:
        raise ValueError("f2 must be > 0")
    if k < 1:
        raise ValueError("k must be >= 1")
    za = _z(_Z_ALPHA_TWO_SIDED, alpha, "alpha")
    zb = _z(_Z_POWER, power, "power")
    n = (za + zb) ** 2 / f2 + k + 1
    return math.ceil(n)
