"""statwise — 그룹 비교 통계 (auto-selected group comparison with assumption checks).

Public API:
    analyze(named_groups, ...) -> AnalysisResult
    render_text(result) -> str
"""

from .analyze import AnalysisResult, Group, PairwiseResult, analyze
from .report import render_text

__version__ = "0.1.0"

__all__ = ["analyze", "render_text", "AnalysisResult", "Group", "PairwiseResult"]
