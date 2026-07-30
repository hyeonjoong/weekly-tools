"""Multi-endpoint runs and across-endpoint multiplicity control."""

import json

import pytest

from statwise.analyze import _bh_adjust, _holm_adjust
from statwise.endpoints import run_endpoints
from statwise.report import (multi_to_dict, render_multi_csv, render_multi_json,
                             render_multi_text)

A = [5.1, 4.9, 5.3, 5.0, 5.2, 4.8, 5.4, 5.0]
B = [7.1, 6.9, 7.3, 7.0, 7.2, 6.8, 7.4, 7.0]
C = [5.0, 5.2, 4.7, 5.1, 4.9, 5.3, 5.1, 4.8]


def _cont(name, a, b):
    return (name, [("drug", a), ("placebo", b)])


def test_each_endpoint_is_analysed_independently():
    multi = run_endpoints([_cont("big", A, B), _cont("null", A, C)])
    assert [r.name for r in multi.runs] == ["big", "null"]
    assert all(r.ok for r in multi.runs)
    assert multi.runs[0].result.endpoint == "big"
    assert multi.runs[0].result.pvalue < multi.runs[1].result.pvalue


def test_holm_adjustment_across_endpoints_matches_the_reference():
    multi = run_endpoints([_cont("e1", A, B), _cont("e2", A, C),
                           _cont("e3", B, C)], correction="holm")
    raw = [r.result.pvalue for r in multi.analysed]
    expected = _holm_adjust(list(raw))
    got = [r.result.pvalue_adj for r in multi.analysed]
    assert got == pytest.approx(expected, rel=1e-12)
    assert all(a >= r - 1e-12 for a, r in zip(got, raw))


def test_bh_adjustment_across_endpoints():
    ds = [_cont("e1", A, B), _cont("e2", A, C), _cont("e3", B, C)]
    multi = run_endpoints(ds, correction="bh")
    raw = [r.result.pvalue for r in multi.analysed]
    assert [r.result.pvalue_adj for r in multi.analysed] == pytest.approx(
        _bh_adjust(list(raw)), rel=1e-12)


def test_no_correction_leaves_p_values_alone_but_warns():
    multi = run_endpoints([_cont("e1", A, B), _cont("e2", A, C)],
                          correction="none")
    for run in multi.analysed:
        assert run.result.pvalue_adj == run.result.pvalue
    assert any("보정" in w for w in multi.warnings)


def test_single_endpoint_needs_no_warning():
    multi = run_endpoints([_cont("only", A, B)], correction="none")
    assert multi.warnings == []


def test_correction_never_lowers_a_p_value():
    multi = run_endpoints([_cont(f"e{i}", A, B if i else C) for i in range(5)])
    for run in multi.analysed:
        assert run.result.pvalue_adj >= run.result.pvalue - 1e-12
        assert run.result.pvalue_adj <= 1.0


def test_one_broken_endpoint_does_not_lose_the_others():
    """A dead column must not take the whole batch down with it."""
    ds = [_cont("good", A, B), ("dead", [("drug", []), ("placebo", [])]),
          _cont("also_good", A, C)]
    multi = run_endpoints(ds)
    assert [r.name for r in multi.analysed] == ["good", "also_good"]
    assert [r.name for r in multi.failed] == ["dead"]
    assert multi.failed[0].error
    # the multiplicity family is the endpoints that were actually tested
    raw = [r.result.pvalue for r in multi.analysed]
    assert [r.result.pvalue_adj for r in multi.analysed] == pytest.approx(
        _holm_adjust(list(raw)), rel=1e-12)


def test_binary_endpoints_run_through_the_same_machinery():
    ds = [("resp", [("drug", (22, 52)), ("placebo", (10, 50))]),
          ("ae", [("drug", (5, 52)), ("placebo", (4, 50))])]
    multi = run_endpoints(ds, binary=True)
    assert multi.binary
    assert len(multi.analysed) == 2
    assert multi.analysed[0].result.test_name.startswith("Chi-square")
    assert multi.analysed[0].result.pvalue_adj is not None


def test_unknown_correction_is_rejected():
    with pytest.raises(ValueError):
        run_endpoints([_cont("e1", A, B)], correction="bonferroni")


def test_posthoc_correction_is_independent_of_endpoint_correction():
    three = [("drug", A), ("placebo", B), ("other", C)]
    multi = run_endpoints([("e1", three), ("e2", three)],
                          correction="bh", posthoc_correction="holm")
    res = multi.analysed[0].result
    assert res.correction == "holm"
    assert multi.correction == "bh"


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def test_summary_table_lists_every_endpoint():
    multi = run_endpoints([_cont("isi", A, B), _cont("psqi", A, C)])
    text = render_multi_text(multi, detail=False)
    assert "isi" in text and "psqi" in text
    assert "p(adj)" in text
    assert "### 엔드포인트" not in text        # detail suppressed


def test_detail_mode_appends_the_full_reports():
    multi = run_endpoints([_cont("isi", A, B), _cont("psqi", A, C)])
    text = render_multi_text(multi, detail=True)
    assert text.count("### 엔드포인트") == 2
    assert "[1] 기술통계" in text


def test_failed_endpoints_are_reported_not_hidden():
    ds = [_cont("good", A, B), ("dead", [("a", []), ("b", [])])]
    text = render_multi_text(run_endpoints(ds), detail=False)
    assert "분석 불가" in text
    assert "dead" in text


def test_multi_json_round_trips():
    multi = run_endpoints([_cont("isi", A, B), _cont("psqi", A, C)])
    d = json.loads(render_multi_json(multi))
    assert d["schema"] == "statwise/multi/1"
    assert [e["endpoint"] for e in d["endpoints"]] == ["isi", "psqi"]
    assert all(e["pvalue_adj"] is not None for e in d["endpoints"])
    assert d["endpoint_correction"] == "holm"


def test_multi_csv_has_one_row_per_endpoint_with_names():
    multi = run_endpoints([_cont("isi", A, B), _cont("psqi", A, C)])
    csv_text = render_multi_csv(multi)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("endpoint,kind,comparison")
    assert len(lines) == 3
    assert lines[1].startswith("isi,continuous")
    assert lines[2].startswith("psqi,continuous")


def test_multi_dict_records_failures():
    ds = [_cont("good", A, B), ("dead", [("a", []), ("b", [])])]
    d = multi_to_dict(run_endpoints(ds))
    assert d["failed"][0]["endpoint"] == "dead"
    assert d["failed"][0]["error"]
