"""agreestat — 측정 방법 일치도 (method-comparison / agreement analysis).

Validate a new measurement method against a reference, for both kinds of
measurement scale:

* **Continuous** — Bland–Altman limits of agreement, ICC(2,1)/ICC(3,1), Lin's
  concordance correlation coefficient (CCC), repeatability, Deming and
  Passing–Bablok regression (CLSI EP09).
* **Categorical / ordinal** — Cohen's kappa and weighted kappa, Gwet's AC1/AC2,
  Scott's pi, Krippendorff's alpha, per-category (PPA/NPA) agreement,
  kappa-paradox diagnostics, and marginal-homogeneity tests.

Pure standard library.

Public API:
    analyze(a, b, ...) -> AnalysisResult                  # continuous
    analyze_categorical(a, b, ...) -> CategoricalResult   # categorical
    render_text/render_json(result) -> str
    render_cat_text/render_cat_json(result) -> str
"""

from .analyze import AnalysisResult, analyze
from .catanalyze import CategoricalResult, analyze_categorical
from .catreport import render_cat_json, render_cat_markdown, render_cat_text
from .regression import deming, passing_bablok
from .report import render_json, render_text

__version__ = "0.3.0"

__all__ = ["analyze", "render_text", "render_json", "AnalysisResult",
           "deming", "passing_bablok",
           "analyze_categorical", "CategoricalResult",
           "render_cat_text", "render_cat_json", "render_cat_markdown"]
