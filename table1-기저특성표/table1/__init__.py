"""table1 — publication-ready baseline-characteristics ("Table 1") generator.

Reads a clinical CSV with one grouping column (e.g. treatment arm) and any
number of variables, auto-classifies each variable as continuous or
categorical, chooses an appropriate summary and hypothesis test per variable,
computes standardized mean differences (SMD, incl. the multivariate SMD for
multi-level categoricals), accounts for missing data, and renders a Table 1
in Markdown / CSV / TSV / JSON.

Everything is pure standard library — the distribution CDFs, Shapiro-Wilk
normality test, and group-comparison tests are implemented from first
principles so the tool runs anywhere Python 3.9+ is installed.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
