"""Machine-readable results: the whole analysis as one JSON document.

Written for the two things a researcher actually does with results: feed them to
a plotting script or a meta-analysis table without re-parsing a Korean text
report, and keep them as the record of what was run. Every number carries its
interval, and the caveats travel with the numbers — ``warnings`` and
``data_chosen`` are part of the payload, not decoration on the printed report.

``NaN``/``Infinity`` are not valid JSON, so non-finite values become ``null``
with the reason recorded in the neighbouring ``*_note`` field where one exists.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from .analyze import Analysis, SelectedPoint
from .roc import Metrics

__all__ = ["analysis_to_dict", "analysis_to_json"]


def _n(x: Optional[float]) -> Optional[float]:
    """A JSON-safe number: NaN and ±inf become null."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _ci(ci: Optional[Tuple[float, float]]) -> Optional[List[Optional[float]]]:
    return None if ci is None else [_n(ci[0]), _n(ci[1])]


def _metrics(m: Metrics) -> Dict[str, Any]:
    pt = m.point
    return {
        "cutoff_oriented": _n(pt.threshold),
        "tp": pt.tp, "fp": pt.fp, "fn": pt.fn, "tn": pt.tn,
        "sensitivity": _n(m.sens), "sensitivity_ci": _ci(m.sens_ci),
        "specificity": _n(m.spec), "specificity_ci": _ci(m.spec_ci),
        "youden_j": _n(pt.youden),
        "ppv": _n(m.ppv), "ppv_ci": _ci(m.ppv_ci),
        "npv": _n(m.npv), "npv_ci": _ci(m.npv_ci),
        "accuracy": _n(m.accuracy), "accuracy_ci": _ci(m.accuracy_ci),
        "balanced_accuracy": _n(m.balanced_accuracy),
        "lr_positive": _n(m.plr), "lr_positive_ci": _ci(m.plr_ci),
        "lr_negative": _n(m.nlr), "lr_negative_ci": _ci(m.nlr_ci),
        "diagnostic_odds_ratio": _n(m.dor), "diagnostic_odds_ratio_ci": _ci(m.dor_ci),
        "lr_ci_haldane_corrected": m.lr_ci_corrected,
        "prevalence": _n(m.prevalence), "prevalence_source": m.prevalence_source,
    }


def _selected(an: Analysis, sp: SelectedPoint) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "key": sp.key, "label": sp.label, "rule": sp.rule,
        "feasible": sp.feasible,
        "data_chosen": sp.data_chosen,
        "note": sp.note,
    }
    if not sp.feasible:
        return d
    value, op = an.cutoff_in_original_units(sp.metrics.point.threshold)
    d["cutoff"] = _n(value)
    d["cutoff_operator"] = op
    d["cutoff_rule"] = f"{an.dataset.score_name} {op} {value:.12g}" \
        if math.isfinite(value) else None
    d["metrics"] = _metrics(sp.metrics)
    b = sp.bootstrap
    if b is not None:
        cut_ci = b.cutoff_ci
        if cut_ci is not None and an.flipped:
            cut_ci = (-cut_ci[1], -cut_ci[0])
        d["bootstrap"] = {
            "n_boot": b.n_boot, "n_effective": b.n_effective, "seed": b.seed,
            "n_cutoff_draws": b.n_cutoff_draws,
            "cutoff_ci": _ci(cut_ci),
            "sensitivity_ci": _ci(b.sens_ci),
            "specificity_ci": _ci(b.spec_ci),
            "youden_ci": _ci(b.youden_ci),
            "sensitivity_optimism_corrected": _n(b.sens_corrected),
            "specificity_optimism_corrected": _n(b.spec_corrected),
            "youden_optimism_corrected": _n(b.youden_corrected),
            "optimism_youden": _n(b.optimism_youden),
            "note": ("이 구간은 절단점 재선택까지 포함한 부트스트랩입니다. 낙관 보정값은 "
                     "부풀림의 크기를 추정한 것이며 제거한 것이 아닙니다 / optimism is "
                     "estimated, not removed"),
        }
    return d


def analysis_to_dict(an: Analysis, tool_version: str = "") -> Dict[str, Any]:
    """The whole analysis as plain dicts/lists, ready for ``json.dump``."""
    ds = an.dataset
    a = an.auc
    doc: Dict[str, Any] = {
        "tool": "rocdx",
        "tool_version": tool_version,
        "schema_version": 1,
        "input": {
            # Basename only: a full path like /Users/…/환자_홍길동_export.csv would
            # put a patient name into a file that gets emailed around.
            "file_name": os.path.basename(ds.path),
            "encoding": ds.encoding,
            "delimiter": ds.delimiter,
            "score_column": ds.score_name,
            "truth_column": ds.truth_name,
            "positive_label": ds.positive_label,
            "negative_label": ds.negative_label,
            "cluster_column": ds.cluster_name or None,
            # Insertion order, so it lines up with `comparisons` below.
            "comparator_columns": list(ds.extra.keys()),
        },
        "sample": {
            "rows_in": ds.n_rows_in,
            "analysed": len(ds.scores),
            "dropped": ds.n_dropped,
            "drop_reasons": dict(ds.drop_reasons),
            "n_positive": ds.n_pos,
            "n_negative": ds.n_neg,
            "sample_prevalence": _n(ds.n_pos / len(ds.scores)) if ds.scores else None,
            "n_clusters": ds.n_clusters or None,
            "max_cluster_size": ds.max_cluster_size or None,
        },
        "settings": {
            "alpha": an.alpha,
            "confidence_level": _n(1.0 - an.alpha),
            "direction": "lower" if an.flipped else "higher",
            "direction_source": an.direction_source,
            "auc_ci_method": a.ci_method,
            "prevalence_assumed": _n(an.prevalence_user),
        },
        "auc": {
            "estimate": _n(a.auc),
            "se_delong": _n(a.se),
            "ci": _ci(a.ci),
            "ci_method": a.ci_method,
            "p_value": _n(a.p_value),
            "p_value_test": "Mann-Whitney U, tie-corrected, H0: AUC = 0.5",
        },
        "operating_points": [_selected(an, sp) for sp in an.selected],
        # Stated per-document because it is easy to forget: the cluster bootstrap
        # widens the AUC/pAUC intervals only. Everything under operating_points
        # still assumes one independent row per subject.
        "operating_points_assume_independent_rows": True,
        "notes": list(ds.notes),
        "warnings": list(an.warnings),
    }
    if an.pauc is not None:
        pa = an.pauc
        doc["partial_auc"] = {
            "specificity_range": [_n(pa.spec_low), _n(pa.spec_high)],
            "fpr_range": [_n(pa.fpr_low), _n(pa.fpr_high)],
            "area": _n(pa.area),
            "area_ci": _ci(pa.area_ci),
            "chance_area": _n(pa.chance_area),
            "max_area": _n(pa.max_area),
            "standardized_mcclish": _n(pa.standardized),
            "standardized_ci": _ci(pa.ci),
            "ci_source": pa.ci_source or None,
            "n_boot_effective": pa.n_effective or None,
            "note": ("standardized_mcclish is comparable across ranges and markers "
                     "but NOT to a full AUC; it is bounded above by 1 and unbounded "
                     "below. Any CI here is a bootstrap percentile interval, not an "
                     "analytic one."),
        }
    if an.curve_boot is not None:
        b = an.curve_boot
        doc["curve_bootstrap"] = {
            "kind": b.kind, "n_boot": b.n_boot, "n_effective": b.n_effective,
            "seed": b.seed, "auc_ci": _ci(b.auc_ci), "auc_se": _n(b.auc_se),
            "n_clusters": b.n_clusters or None,
            "max_cluster_size": b.max_cluster_size or None,
        }
    if an.comparisons:
        out: List[Dict[str, Any]] = []
        for c in an.comparisons:
            item: Dict[str, Any] = {
                "reference_marker": c.label_a,
                "comparator": c.label_b,
                "auc_reference": _n(c.auc_a),
                "auc_comparator": _n(c.auc_b),
                "difference": _n(c.diff),
                "se_difference": _n(c.se_diff),
                "ci": _ci(c.ci),
                "z": _n(c.z),
                "p_value": _n(c.p_value),
                "paired": c.paired,
                "n_used": c.n_used,
                "comparator_direction": ("lower" if an.comparison_flipped.get(c.label_b)
                                         else "higher"),
                "test": "DeLong",
            }
            if an.comparison_p_adjusted:
                item["p_value_holm"] = _n(an.comparison_p_adjusted.get(c.label_b))
                item["holm_family_size"] = an.holm_family_size
            ni = an.noninferiority.get(c.label_b)
            if ni is not None:
                item["noninferiority"] = {
                    "margin": _n(ni.margin),
                    "z": _n(ni.z),
                    "p_value_one_sided": _n(ni.p_value),
                    "alpha_one_sided": _n(ni.alpha_one_sided),
                    "ci_lower_limit": _n(ni.lower_limit),
                    "noninferior": ni.noninferior,
                    "superior": ni.superior,
                    "note": ("the margin must be pre-specified on clinical grounds; "
                             "this one-sided p is NOT multiplicity-corrected even when "
                             "several comparators were tested"),
                }
            out.append(item)
        doc["comparisons"] = out
    return doc


def analysis_to_json(an: Analysis, tool_version: str = "", indent: int = 2) -> str:
    """Serialise the analysis. ``allow_nan=False`` guards the null conversion."""
    return json.dumps(analysis_to_dict(an, tool_version), ensure_ascii=False,
                      indent=indent, allow_nan=False) + "\n"
