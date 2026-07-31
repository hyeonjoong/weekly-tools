"""agreestat — 측정 방법 일치도 (method-comparison / agreement analysis).

Validate a new measurement method against a reference, for both kinds of
measurement scale:

* **Continuous** — Bland–Altman limits of agreement, ICC(2,1)/ICC(3,1), Lin's
  concordance correlation coefficient (CCC), repeatability, Deming and
  Passing–Bablok regression (CLSI EP09).
* **Categorical / ordinal** — Cohen's kappa and weighted kappa, Gwet's AC1/AC2,
  Scott's pi, Krippendorff's alpha, per-category (PPA/NPA) agreement,
  kappa-paradox diagnostics, and marginal-homogeneity tests.
* **Three or more raters** — the full ICC family ICC(1,1)...ICC(3,k) with SEM /
  MDC95 and pairwise LoA, or Fleiss' kappa, Gwet's AC1 and Krippendorff's alpha
  with bootstrap CIs.

Input may be wide (one column per method) or long/tidy (one row per
measurement). Pure standard library.

Public API:
    analyze(a, b, ...) -> AnalysisResult                  # continuous
    analyze_categorical(a, b, ...) -> CategoricalResult   # categorical
    render_text/render_json(result) -> str
    render_cat_text/render_cat_json(result) -> str
    multi_continuous(names, rows) -> MultiContinuous       # 3+ raters, numeric
    multi_categorical(names, rows, cats) -> MultiCategorical  # 3+ raters, labels
    render_multi_text/render_multicat_text(result) -> str
"""

from .analyze import AnalysisResult, analyze
from .catanalyze import CategoricalResult, analyze_categorical
from .catreport import render_cat_json, render_cat_markdown, render_cat_text
from .multirater import (
    MultiCategorical,
    MultiContinuous,
    fleiss_kappa,
    gwet_ac1_multi,
    icc_family,
    krippendorff_alpha_multi,
    multi_categorical,
    multi_continuous,
)
from .multireport import (
    render_multi_json,
    render_multi_markdown,
    render_multi_text,
    render_multicat_json,
    render_multicat_markdown,
    render_multicat_text,
)
from .regression import deming, passing_bablok
from .report import render_json, render_text

__version__ = "0.4.0"

__all__ = ["analyze", "render_text", "render_json", "AnalysisResult",
           "deming", "passing_bablok",
           "analyze_categorical", "CategoricalResult",
           "render_cat_text", "render_cat_json", "render_cat_markdown",
           "multi_continuous", "MultiContinuous", "multi_categorical",
           "MultiCategorical", "icc_family", "fleiss_kappa", "gwet_ac1_multi",
           "krippendorff_alpha_multi",
           "render_multi_text", "render_multi_json", "render_multi_markdown",
           "render_multicat_text", "render_multicat_json",
           "render_multicat_markdown"]
