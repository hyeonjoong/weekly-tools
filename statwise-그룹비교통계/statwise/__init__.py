"""statwise — 그룹 비교 통계 (auto-selected group comparison with assumption checks).

Public API
----------
Continuous outcomes::

    analyze(named_groups, alpha=..., equivalence=...) -> AnalysisResult
    analyze_paired((label_a, values_a), (label_b, values_b), ...) -> AnalysisResult

Binary (yes/no) outcomes::

    compare_binary([(label, (events, n)), ...], ...) -> BinaryResult

Paired binary outcomes — the same subjects measured twice (McNemar)::

    compare_paired_binary(("post", [1, 0, ...]), ("pre", [0, 0, ...]))
        -> PairedBinaryResult

Covariate-adjusted (ANCOVA) comparison — the usual RCT primary analysis::

    run_ancova(records, covariate_names=["baseline"], factor_names=["site"])

Several endpoints at once, corrected across the family::

    run_endpoints([(name, named_groups), ...], correction="holm") -> MultiEndpointResult

Rendering (text / JSON-safe dict / JSON / tidy CSV)::

    render_text(result) / render_binary_text(result) / render_multi_text(multi)
    render_mcnemar_text(result) / render_mcnemar_json(result)
    result_to_dict(result) / binary_to_dict(result) / multi_to_dict(multi)
    render_json(result) / render_binary_json(result) / render_multi_json(multi)
    render_csv(result) / render_multi_csv(multi)
"""

from .analyze import (AnalysisResult, EquivalenceSpec, Group, PairwiseResult,
                      analyze, analyze_paired)
from .ancova import (AdjustedMean, AncovaContrast, AncovaRecord, AncovaResult,
                     CovariateEffect, run_ancova)
from .binary import BinaryGroup, BinaryResult, compare_binary
from .endpoints import EndpointRun, MultiEndpointResult, run_endpoints
from .mcnemar import (PairedBinaryResult, PairedTable, cohens_kappa,
                      compare_paired_binary, conditional_odds_ratio,
                      mcnemar_chi_square, mcnemar_exact_p,
                      paired_risk_difference)
from .report import (ancova_to_dict, binary_to_dict, multi_to_dict,
                     render_ancova_json, render_ancova_text,
                     render_binary_json,
                     render_binary_text, render_csv, render_json,
                     render_multi_csv, render_multi_json, render_multi_text,
                     render_text, result_to_dict)

__version__ = "0.5.0"

__all__ = [
    # analysis
    "analyze", "analyze_paired", "compare_binary", "run_endpoints",
    "run_ancova", "compare_paired_binary", "mcnemar_exact_p",
    "mcnemar_chi_square", "paired_risk_difference", "conditional_odds_ratio",
    "cohens_kappa",
    # rendering
    "render_text", "render_json", "result_to_dict", "render_csv",
    "render_binary_text", "render_binary_json", "binary_to_dict",
    "render_multi_text", "render_multi_json", "multi_to_dict",
    "render_multi_csv",
    "render_mcnemar_text", "render_mcnemar_json", "mcnemar_to_dict",
    "render_ancova_text", "render_ancova_json", "ancova_to_dict",
    # result types
    "AnalysisResult", "Group", "PairwiseResult", "EquivalenceSpec",
    "BinaryResult", "BinaryGroup", "MultiEndpointResult", "EndpointRun",
    "PairedBinaryResult", "PairedTable",
    "AncovaResult", "AncovaRecord", "AdjustedMean", "AncovaContrast",
    "CovariateEffect",
]
