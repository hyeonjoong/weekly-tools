"""agreestat — 측정 방법 일치도 (method-comparison / agreement analysis).

Validate a new measurement method against a reference: Bland–Altman limits of
agreement, ICC(2,1)/ICC(3,1), Lin's concordance correlation coefficient (CCC),
repeatability, and correlation/difference context — pure standard library.

Public API:
    analyze(a, b, ...) -> AnalysisResult
    render_text(result) -> str
    render_json(result) -> str
"""

from .analyze import AnalysisResult, analyze
from .report import render_json, render_text

__version__ = "0.1.0"

__all__ = ["analyze", "render_text", "render_json", "AnalysisResult"]
