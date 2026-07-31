"""table1 — publication-ready baseline-characteristics ("Table 1") generator.

Reads a clinical CSV or .xlsx workbook with one grouping column (e.g. treatment
arm) and any number of variables, auto-classifies each variable as continuous
or categorical, chooses an appropriate summary and hypothesis test per
variable, computes standardized mean differences (SMD, incl. the multivariate
SMD for multi-level categoricals), accounts for missing data, and renders a
Table 1 in Markdown / CSV / TSV / JSON / HTML / LaTeX.

Beyond the basic table it also produces between-group effect sizes with 95%
confidence intervals, multiple-comparison adjustment of the per-variable
p-values, and a weighted (IPTW / propensity-score / survey) Table 1 with
weighted SMDs and Kish effective sample sizes, an explicit group-column order
(--group-order) and, for ordered arms such as dose levels or exposure
quartiles, a "p for trend" column (ANOVA linear contrast / Jonckheere-Terpstra
/ Cochran-Armitage).

Everything is pure standard library — the distribution CDFs, Shapiro-Wilk
normality test, and group-comparison tests are implemented from first
principles so the tool runs anywhere Python 3.9+ is installed.
"""

from __future__ import annotations

__version__ = "0.3.0"

__all__ = ["__version__"]
