"""Report rendering: text sections, JSON schema, and JSON-safety of NaN/inf."""

import json
import math

import pytest

from statwise.analyze import analyze, analyze_paired
from statwise.report import render_json, render_text, result_to_dict


def _two_group():
    a = [10.5, 12.1, 11.3, 13.0, 9.8, 11.7, 10.9, 12.4, 11.1, 10.2]
    b = [13.1, 14.5, 12.8, 15.0, 11.9, 13.6, 14.1, 12.7, 13.9, 14.3]
    return analyze([("a", a), ("b", b)])


def test_json_roundtrips_and_has_core_fields():
    res = _two_group()
    d = json.loads(render_json(res))
    assert d["schema"] == "statwise/analysis/1"
    assert d["paired"] is False
    assert d["test"]["name"] == "Student's t-test"
    assert d["test"]["pvalue"] < 0.001
    assert len(d["groups"]) == 2
    assert d["groups"][0]["label"] == "a"
    assert d["effects"][0]["name"] == "Hedges' g"
    assert "sentence" in d and d["sentence"]


def test_json_paired_has_paired_fields():
    a = [10.2, 12.5, 14.1, 16.8, 18.3, 11.4, 13.9, 15.2, 9.7, 17.1]
    b = [8.1, 11.3, 12.6, 15.0, 16.2, 9.9, 12.4, 13.1, 8.0, 15.5]
    d = result_to_dict(analyze_paired(("pre", a), ("post", b)))
    assert d["paired"] is True
    assert d["n_pairs"] == 10
    assert "diff_normality" in d
    assert "normality" not in d  # per-group normality not applicable when paired


def _reject_constants(x):
    raise AssertionError(f"non-standard JSON constant emitted: {x!r}")


def test_json_is_nan_safe():
    # perfect separation with zero within-variance -> inf/nan statistics.
    # Force a raw-ANOVA NaN F through the serializer via a direct 3-group call.
    from statwise.tests_stat import one_way_anova
    r = one_way_anova([[5.0, 5.0], [9.0, 9.0]])
    assert r.statistic == float("inf")  # sanity: this path really is non-finite
    # A Mann-Whitney on constant-ish tiny groups yields finite stats, so build a
    # result we know contains non-finite numbers and confirm the JSON is strict.
    res = analyze([("a", [5.0, 5.1]), ("b", [9.0, 9.2])])
    txt = render_json(res)
    # json.loads accepts NaN/Infinity by default, so guard explicitly:
    assert "NaN" not in txt and "Infinity" not in txt
    json.loads(txt, parse_constant=_reject_constants)  # raises if any slipped in


def test_json_nan_safe_with_nonfinite_stats(monkeypatch):
    # Directly exercise the serializer's _jnum on a result carrying inf/nan.
    from statwise import report
    res = analyze([("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0])])
    res.statistic = float("inf")
    res.pvalue = float("nan")
    d = report.result_to_dict(res)
    assert d["test"]["statistic"] is None
    assert d["test"]["pvalue"] is None
    txt = report.render_json(res)
    assert "NaN" not in txt and "Infinity" not in txt
    json.loads(txt, parse_constant=_reject_constants)


def test_text_report_has_all_sections():
    txt = render_text(_two_group())
    for token in ["[1] 기술통계", "[2] 가정 점검", "[3] 선택된 검정",
                  "[4] 효과크기", "논문용 문장"]:
        assert token in txt


def test_text_posthoc_correction_label():
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    txt_bh = render_text(analyze([("a", g1), ("b", g2), ("c", g3)], correction="bh"))
    assert "Benjamini-Hochberg" in txt_bh
    txt_holm = render_text(analyze([("a", g1), ("b", g2), ("c", g3)]))
    assert "Holm-Bonferroni" in txt_holm


def test_sentence_reflects_bh_correction():
    # The paste-ready sentence must not hardcode "Holm" when BH was requested.
    g1 = [3, 5, 4, 6, 2, 5, 4, 3, 7, 5, 4, 6]
    g2 = [6, 8, 7, 9, 5, 8, 7, 6, 10, 8, 7, 9]
    g3 = [9, 11, 10, 12, 8, 11, 10, 9, 13, 11, 10, 12]
    txt = render_text(analyze([("a", g1), ("b", g2), ("c", g3)], correction="bh"))
    assert "Benjamini-Hochberg" in txt.split("Ready-to-paste")[1]
    assert "Holm-corrected" not in txt.split("Ready-to-paste")[1]


def test_welch_anova_sentence():
    g1 = [11.691, 9.534, 10.033, 10.408, 9.211, 10.002, 9.999, 8.245,
          11.018, 10.6, 9.375, 9.828, 10.505, 9.739, 9.757]
    g2 = [10.547, 12.555, 12.124, 12.274, 10.473, 13.651, 12.154, 11.613,
          14.029, 11.955, 10.549, 11.595, 9.712, 13.049, 11.584]
    g3 = [10.287, 19.362, 5.745, 16.677, 3.678, 10.689, 7.979, 21.31,
          22.831, 12.353, 18.204, 13.1, 16.84, 10.236, 5.458]
    txt = render_text(analyze([("A", g1), ("B", g2), ("C", g3)]))
    assert "Welch's ANOVA" in txt
