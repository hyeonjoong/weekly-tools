"""Run the same group comparison across several outcome columns at once.

A protocol almost never has one endpoint.  A sleep study measures ISI, PSQI,
sleep efficiency and HRV on the same two arms; a safety table has a dozen event
rates.  Analysing those one CLI call at a time is not just tedious — it hides
the multiplicity: eight endpoints at alpha = 0.05 give a ~34% chance of at
least one spurious "significant" result.

This module runs one analysis per endpoint and then adjusts the *omnibus*
p-values across endpoints (Holm by default, Benjamini-Hochberg or none on
request), so the family-wise or false-discovery statement covers the whole
table the reader is looking at.  Post-hoc corrections *within* an endpoint are
unchanged and stay separate — the two families are different questions and are
reported separately.

The adjusted p-value lands on ``AnalysisResult.pvalue_adj`` /
``BinaryResult.pvalue_adj``, and ``.endpoint`` records which column it came
from; both fields already existed on those dataclasses for exactly this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from .analyze import EquivalenceSpec, _correct, analyze
from .binary import compare_binary

__all__ = ["EndpointRun", "MultiEndpointResult", "run_endpoints",
           "CORRECTION_LABELS"]

CORRECTION_LABELS = {
    "holm": "Holm-Bonferroni (family-wise)",
    "bh": "Benjamini-Hochberg (FDR)",
    "none": "보정 없음 (uncorrected)",
}

Result = Union[Any]  # AnalysisResult | BinaryResult


#: Errors are shown to the user *and* persisted into --output reports, so an
#: unbounded str(exc) built from cell contents would ship data to whoever
#: receives the file. Loader messages are already length-bounded, but cap again
#: here so a future message can't quietly widen the exposure.
_MAX_ERROR_LEN = 200


@dataclass
class EndpointRun:
    """One endpoint's outcome, or the reason it could not be analysed."""

    name: str
    result: Optional[Result] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.error is not None and len(self.error) > _MAX_ERROR_LEN:
            self.error = self.error[:_MAX_ERROR_LEN - 1] + "…"

    @property
    def ok(self) -> bool:
        return self.result is not None


@dataclass
class MultiEndpointResult:
    runs: List[EndpointRun]
    alpha: float
    correction: str
    binary: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def analysed(self) -> List[EndpointRun]:
        return [r for r in self.runs if r.ok]

    @property
    def failed(self) -> List[EndpointRun]:
        return [r for r in self.runs if not r.ok]


def _adjust_across(results: Sequence[Any], correction: str,
                   alpha: float, warnings: Optional[List[str]] = None) -> None:
    """Set ``pvalue_adj`` on each result and re-decide significance from it.

    Leaving ``significant`` on the raw p-value would make the whole module a
    lie: the detail report, the JSON and the CSV would each keep asserting a
    significance that the correction has just withdrawn, while only the summary
    table showed the corrected verdict.  Whatever family the caller chose, the
    verdict reported everywhere is the verdict for that family.
    """
    if not results:
        return
    pvals = [r.pvalue for r in results]
    if correction == "none" or any(p != p for p in pvals):
        # A NaN omnibus p-value (a degenerate endpoint) makes any step-down or
        # step-up ordering meaningless, so we leave the raw values alone rather
        # than inventing an order.
        if correction != "none" and warnings is not None:
            warnings.append(
                "엔드포인트 중 p값을 계산할 수 없는(NaN) 것이 있어 엔드포인트 간 "
                "다중비교 보정을 적용하지 못했습니다 — 표시된 p값은 보정 전 "
                "값입니다. 문제가 되는 엔드포인트를 빼고 다시 실행하세요.")
        for r in results:
            r.pvalue_adj = r.pvalue
        return
    for r, adj in zip(results, _correct(list(pvals), correction)):
        r.pvalue_adj = adj
        was = bool(r.significant)
        r.significant = (adj == adj and adj < alpha)
        if was and not r.significant:
            r.warnings.append(
                "이 엔드포인트는 보정 전 p={:.4g} 로 유의했지만, 엔드포인트 "
                "{}개에 대한 다중비교 보정 후 p={:.4g} 가 되어 더 이상 "
                "유의하지 않습니다.".format(r.pvalue, len(results), adj))


def run_endpoints(datasets: Sequence[Any], alpha: float = 0.05,
                  alpha_norm: float = 0.05, correction: str = "holm",
                  posthoc_correction: str = "holm", posthoc: bool = True,
                  binary: bool = False, binary_test: str = "auto",
                  equivalence: Optional[EquivalenceSpec] = None,
                  missing: Optional[Dict[str, Dict[str, int]]] = None,
                  test: str = "auto", event_is: str = "unspecified"
                  ) -> MultiEndpointResult:
    """Analyse several endpoints and adjust the omnibus p-values across them.

    ``datasets`` is ``[(endpoint_name, named_groups), ...]`` where
    ``named_groups`` has the shape the underlying analyser expects:
    ``[(label, [values...])]`` for continuous, ``[(label, (events, n))]`` for
    binary.  An endpoint that cannot be analysed at all (all-missing column,
    a single usable arm) is recorded with its error instead of aborting the
    whole run — losing seven good endpoints because the eighth was empty is
    not acceptable behaviour for a batch tool.
    """
    if correction not in ("holm", "bh", "none"):
        raise ValueError("endpoint correction must be 'holm', 'bh' or 'none'")
    miss = missing or {}
    runs: List[EndpointRun] = []
    for name, named_groups in datasets:
        try:
            if binary:
                res = compare_binary(
                    named_groups, alpha=alpha, correction=posthoc_correction,
                    posthoc=posthoc, test=binary_test,
                    missing=miss.get(name), event_is=event_is)
            else:
                res = analyze(
                    named_groups, alpha=alpha, alpha_norm=alpha_norm,
                    posthoc=posthoc, correction=posthoc_correction,
                    equivalence=equivalence, missing=miss.get(name),
                    test=test)
            res.endpoint = name
            runs.append(EndpointRun(name, res))
        except (ValueError, ArithmeticError) as exc:
            runs.append(EndpointRun(name, None, str(exc)))

    ok = [r.result for r in runs if r.ok]
    warnings: List[str] = []
    _adjust_across(ok, correction, alpha, warnings)
    if correction == "none" and len(ok) > 1:
        warnings.append(
            f"엔드포인트 {len(ok)}개를 동시에 검정했지만 다중비교 보정을 "
            f"하지 않았습니다(--endpoint-correction none) — 위양성 위험이 "
            f"커집니다.")
    if binary and correction == "holm" and len(ok) > 1:
        # FWER control on a safety table hides harm signals, which is the
        # opposite of what an AE table is for (ICH E9 / CIOMS).
        warnings.append(
            "이상반응(안전성) 표라면 엔드포인트 간 family-wise 보정이 오히려 "
            "위해 신호를 가릴 수 있습니다 — 안전성 결과는 관례적으로 보정 없이 "
            "보고합니다(--endpoint-correction none 고려). 유효성 엔드포인트에는 "
            "지금의 Holm 보정이 적절합니다.")
    if len(ok) > 1:
        warnings.append(
            f"보정 대상 패밀리 = 이번 실행에 넘긴 엔드포인트 {len(ok)}개입니다. "
            f"같은 연구의 엔드포인트를 여러 번에 나눠 실행하면 보정이 그만큼 "
            f"약해지며, 주평가변수/부평가변수 구분(계층적 검정)은 지원하지 "
            f"않습니다.")
    return MultiEndpointResult(runs=runs, alpha=alpha, correction=correction,
                               binary=binary, warnings=warnings)
