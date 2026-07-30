"""statwise — 그룹 비교 통계 (auto-selected group comparison with assumption checks).

Public API
----------
Continuous outcomes::

    analyze(named_groups, alpha=..., equivalence=...) -> AnalysisResult
    analyze_paired((label_a, values_a), (label_b, values_b), ...) -> AnalysisResult

Binary (yes/no) outcomes::

    compare_binary([(label, (events, n)), ...], ...) -> BinaryResult

Several endpoints at once, corrected across the family::

    run_endpoints([(name, named_groups), ...], correction="holm") -> MultiEndpointResult

Rendering (text / JSON-safe dict / JSON / tidy CSV)::

    render_text(result) / render_binary_text(result) / render_multi_text(multi)
    result_to_dict(result) / binary_to_dict(result) / multi_to_dict(multi)
    render_json(result) / render_binary_json(result) / render_multi_json(multi)
    render_csv(result) / render_multi_csv(multi)
"""

from .analyze import (AnalysisResult, EquivalenceSpec, Group, PairwiseResult,
                      analyze, analyze_paired)
from .binary import BinaryGroup, BinaryResult, compare_binary
from .endpoints import EndpointRun, MultiEndpointResult, run_endpoints
from .report import (binary_to_dict, multi_to_dict, render_binary_json,
                     render_binary_text, render_csv, render_json,
                     render_multi_csv, render_multi_json, render_multi_text,
                     render_text, result_to_dict)

__version__ = "0.3.0"

__all__ = [
    # analysis
    "analyze", "analyze_paired", "compare_binary", "run_endpoints",
    # rendering
    "render_text", "render_json", "result_to_dict", "render_csv",
    "render_binary_text", "render_binary_json", "binary_to_dict",
    "render_multi_text", "render_multi_json", "multi_to_dict",
    "render_multi_csv",
    # result types
    "AnalysisResult", "Group", "PairwiseResult", "EquivalenceSpec",
    "BinaryResult", "BinaryGroup", "MultiEndpointResult", "EndpointRun",
]
