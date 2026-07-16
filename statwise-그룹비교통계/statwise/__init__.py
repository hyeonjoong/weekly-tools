"""statwise — 그룹 비교 통계 (auto-selected group comparison with assumption checks).

Public API:
    analyze(named_groups, ...) -> AnalysisResult
    render_text(result) -> str
"""

from .analyze import (AnalysisResult, Group, PairwiseResult, analyze,
                      analyze_paired)
from .report import render_json, render_text, result_to_dict

__version__ = "0.2.0"

__all__ = ["analyze", "analyze_paired", "render_text", "render_json",
           "result_to_dict", "AnalysisResult", "Group", "PairwiseResult"]
