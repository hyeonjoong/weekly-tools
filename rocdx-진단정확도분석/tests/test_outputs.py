"""The machine-readable JSON document and the SVG figure."""

import json
import math
import os
import re
import xml.dom.minidom as minidom

import pytest

from rocdx import __version__
from rocdx.analyze import add_comparison, analyze, finalize_comparisons, load_dataset
from rocdx.cli import main
from rocdx.jsonout import analysis_to_dict, analysis_to_json
from rocdx.loader import read_table
from rocdx.svgplot import roc_svg

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


def write_csv(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def svg_text(svg):
    """All text content of the SVG joined with spaces.

    The footer is wrapped across several <text> elements, so a phrase that a
    reader sees as one sentence is not one string in the markup.
    """
    doc = minidom.parseString(svg)
    parts = [t.firstChild.nodeValue for t in doc.getElementsByTagName("text")
             if t.firstChild is not None]
    return " ".join(parts)


def demo(tmp_path, name="d.csv"):
    rows = ["id,marker,rival,dx"]
    for i in range(30):
        rows.append(f"c{i},{i},{29 - i},No")
    for i in range(30):
        rows.append(f"p{i},{i + 12},{i},Yes")
    return write_csv(tmp_path, name, "\n".join(rows) + "\n")


def full_analysis(tmp_path, **kw):
    ds = load_dataset(read_table(demo(tmp_path)), "marker", "dx",
                      compare_cols=["rival"])
    an = analyze(ds, min_spec=0.9, cutoffs=[20.0], n_boot=120, seed=4,
                 pauc_min_spec=0.8, **kw)
    add_comparison(an, "rival")
    finalize_comparisons(an, ni_margin=0.05)
    return an


# --- JSON ---------------------------------------------------------------------

def test_json_is_valid_and_carries_the_key_numbers(tmp_path):
    an = full_analysis(tmp_path)
    doc = json.loads(analysis_to_json(an, __version__))
    assert doc["tool"] == "rocdx" and doc["tool_version"] == __version__
    assert doc["schema_version"] == 1
    assert doc["auc"]["estimate"] == pytest.approx(an.auc.auc)
    assert doc["auc"]["ci"] == pytest.approx(list(an.auc.ci))
    assert doc["sample"]["n_positive"] == an.dataset.n_pos
    assert doc["sample"]["analysed"] == len(an.dataset.scores)
    assert doc["settings"]["alpha"] == an.alpha
    assert doc["partial_auc"]["standardized_mcclish"] == pytest.approx(
        an.pauc.standardized)
    assert doc["comparisons"][0]["difference"] == pytest.approx(
        an.comparisons[0].diff)
    assert doc["comparisons"][0]["noninferiority"]["margin"] == pytest.approx(0.05)


def test_json_never_emits_nan_or_infinity(tmp_path):
    """A tiny sample produces inf LR+ and NaN intervals — those must be null."""
    path = write_csv(tmp_path, "tiny.csv", "\n".join([
        "id,marker,dx", "1,1,No", "2,2,No", "3,9,Yes", "4,9,Yes",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), cutoffs=[5.0])
    text = analysis_to_json(an)                 # allow_nan=False would raise
    assert "NaN" not in text and "Infinity" not in text
    doc = json.loads(text)
    y = next(p for p in doc["operating_points"] if p["key"] == "youden")
    assert y["metrics"]["lr_positive"] is None   # was +inf
    json.loads(json.dumps(doc, allow_nan=False))


def test_json_operating_points_report_cutoffs_in_the_users_own_units(tmp_path):
    """With --direction lower the JSON cut-off must be the un-negated value."""
    path = write_csv(tmp_path, "low.csv", "\n".join(
        ["id,marker,dx"] + [f"c{i},{50 + i},No" for i in range(10)]
        + [f"p{i},{i},Yes" for i in range(10)]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="lower")
    doc = analysis_to_dict(an)
    y = next(p for p in doc["operating_points"] if p["key"] == "youden")
    assert y["cutoff_operator"] == "<="
    assert 0 < y["cutoff"] < 50
    assert doc["settings"]["direction"] == "lower"


def test_json_keeps_the_caveats_with_the_numbers(tmp_path):
    an = full_analysis(tmp_path)
    doc = analysis_to_dict(an)
    y = next(p for p in doc["operating_points"] if p["key"] == "youden")
    assert y["data_chosen"] is True
    assert "optimism" in y["bootstrap"]["note"]
    fixed = next(p for p in doc["operating_points"] if p["key"].startswith("cutoff:"))
    assert fixed["data_chosen"] is False
    assert isinstance(doc["warnings"], list)


def test_json_records_drop_reasons_and_notes(tmp_path):
    path = write_csv(tmp_path, "messy.csv", "\n".join([
        "id,marker,dx", "1,1,No", "2,,No", "3,3,", "4,9,Yes", "5,10,Yes",
        "6,11,Yes", "7,2,No",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"))
    doc = analysis_to_dict(an)
    assert doc["sample"]["dropped"] == 2
    assert sum(doc["sample"]["drop_reasons"].values()) == 2


def test_cli_writes_json_to_a_file_and_still_prints_the_report(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--json", str(out), "--no-curve"]) == 0
    printed = capsys.readouterr().out
    assert "rocdx — 진단정확도 분석" in printed
    assert f"→ {out}" in printed
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["auc"]["estimate"] > 0.5


def test_cli_json_dash_emits_only_json_on_stdout(tmp_path, capsys):
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--json", "-"]) == 0
    out = capsys.readouterr().out
    doc = json.loads(out)          # the whole stream must parse
    assert doc["tool"] == "rocdx"
    assert "진단정확도 분석" not in out


def test_cli_json_dash_suppresses_even_the_saved_file_chatter(tmp_path, capsys):
    pts = tmp_path / "p.csv"
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--json", "-", "--points-csv", str(pts)]) == 0
    json.loads(capsys.readouterr().out)
    assert pts.exists()


def test_cli_reports_an_unwritable_json_path(tmp_path, capsys):
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--json", str(tmp_path / "nope" / "x.json")]) == 2
    assert "JSON" in capsys.readouterr().err


# --- SVG ----------------------------------------------------------------------

def test_svg_is_well_formed_xml(tmp_path):
    svg = roc_svg(full_analysis(tmp_path))
    minidom.parseString(svg)          # raises on malformed output
    assert svg.startswith("<?xml")
    assert svg.rstrip().endswith("</svg>")


def test_svg_escapes_column_names_that_would_break_the_xml(tmp_path):
    path = write_csv(tmp_path, "x.csv", "\n".join(
        ['id,"a<b>&c",dx'] + [f"c{i},{i},No" for i in range(8)]
        + [f"p{i},{i + 5},Yes" for i in range(8)]) + "\n")
    an = analyze(load_dataset(read_table(path), "a<b>&c", "dx"))
    svg = roc_svg(an)
    minidom.parseString(svg)
    assert "a&lt;b&gt;&amp;c" in svg
    assert "a<b>&c" not in svg


def test_svg_contains_the_curve_the_comparators_and_the_caveat(tmp_path):
    an = full_analysis(tmp_path)
    svg = roc_svg(an)
    assert svg.count("<polyline") == 2          # index test + one comparator
    assert "AUC" in svg and "rival" in svg
    assert "optimistic" in svg_text(svg)        # the caveat travels with the figure
    assert "analysable of" in svg_text(svg)
    assert "Sensitivity" in svg


def test_svg_marks_the_pauc_region_only_when_one_was_requested(tmp_path):
    an = full_analysis(tmp_path)
    assert "<polygon" in roc_svg(an) and "pAUC" in svg_text(roc_svg(an))
    ds = load_dataset(read_table(demo(tmp_path)), "marker", "dx")
    assert "<polygon" not in roc_svg(analyze(ds))


def test_svg_youden_marker_uses_the_original_units(tmp_path):
    path = write_csv(tmp_path, "low.csv", "\n".join(
        ["id,marker,dx"] + [f"c{i},{50 + i},No" for i in range(10)]
        + [f"p{i},{i},Yes" for i in range(10)]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), direction="lower")
    svg = roc_svg(an)
    assert "marker &lt;=" in svg
    assert "lower values indicate disease" in svg_text(svg)


def test_svg_survives_a_degenerate_analysis(tmp_path):
    path = write_csv(tmp_path, "t.csv",
                     "id,marker,dx\n1,1,No\n2,1,Yes\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"))
    minidom.parseString(roc_svg(an))


def test_cli_writes_the_svg_file(tmp_path, capsys):
    out = tmp_path / "roc.svg"
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--plot-svg", str(out), "--no-curve"]) == 0
    assert f"→ {out}" in capsys.readouterr().out
    minidom.parseString(out.read_text(encoding="utf-8"))


def test_cli_reports_an_unwritable_svg_path(tmp_path, capsys):
    assert main([demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--plot-svg", str(tmp_path / "nope" / "x.svg")]) == 2
    assert "SVG" in capsys.readouterr().err


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# --- regression: value checks the substring tests could not make --------------

def test_json_partial_auc_fields_are_not_cross_wired(tmp_path):
    """`area` and `standardized_mcclish` are different numbers on this fixture."""
    an = full_analysis(tmp_path)
    doc = analysis_to_dict(an)
    pa = doc["partial_auc"]
    assert pa["area"] == pytest.approx(an.pauc.area)
    assert pa["area"] != pytest.approx(an.pauc.standardized)
    assert pa["specificity_range"] == pytest.approx([0.8, 1.0])
    assert pa["specificity_range"][0] < pa["specificity_range"][1]
    assert pa["fpr_range"] == pytest.approx([0.0, 0.2])
    assert pa["chance_area"] == pytest.approx(0.02)
    assert pa["max_area"] == pytest.approx(0.2)
    assert pa["ci_source"] == "bootstrap"
    assert "NOT to a full AUC" in pa["note"]


def test_json_carries_the_warnings_verbatim_not_an_empty_list(tmp_path):
    # pAUC without --bootstrap always produces the "no analytic CI" warning.
    ds = load_dataset(read_table(demo(tmp_path)), "marker", "dx")
    an = analyze(ds, pauc_min_spec=0.9)
    doc = analysis_to_dict(an)
    assert an.warnings, "fixture should produce at least one warning"
    assert doc["warnings"] == an.warnings
    assert doc["operating_points_assume_independent_rows"] is True
    assert analysis_to_dict(full_analysis(tmp_path))["warnings"] == \
        full_analysis(tmp_path).warnings


def test_json_noninferiority_booleans_are_not_swapped(tmp_path):
    """Non-inferior without being superior — the case that separates the two."""
    path = write_csv(tmp_path, "ni.csv", "\n".join(
        ["id,a,b,dx"]
        + [f"c{i},{i},{i + 0.3},No" for i in range(60)]
        + [f"p{i},{i + 30},{i + 30.4},Yes" for i in range(60)]) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds)
    add_comparison(an, "b")
    finalize_comparisons(an, ni_margin=0.05)
    ni = analysis_to_dict(an)["comparisons"][0]["noninferiority"]
    assert ni["noninferior"] is True and ni["superior"] is False
    assert ni["ci_lower_limit"] == pytest.approx(an.comparisons[0].ci[0], rel=1e-12)
    assert ni["alpha_one_sided"] == pytest.approx(0.025)
    assert "pre-specified" in ni["note"]


def test_json_cluster_fields_are_populated(tmp_path):
    rows = ["pid,marker,dx"]
    for i in range(20):
        for _ in range(3):
            rows.append(f"C{i},{i},No")
            rows.append(f"P{i},{i + 5},Yes")
    path = write_csv(tmp_path, "cl.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    an = analyze(ds, n_boot=200, seed=2, cluster=True)
    doc = analysis_to_dict(an)
    assert doc["input"]["cluster_column"] == "pid"
    assert doc["sample"]["n_clusters"] == 40
    assert doc["sample"]["max_cluster_size"] == 3
    assert doc["curve_bootstrap"]["kind"] == "cluster"
    assert doc["curve_bootstrap"]["n_clusters"] == 40
    assert doc["curve_bootstrap"]["max_cluster_size"] == 3


def test_json_bootstrap_cutoff_interval_is_un_negated_for_a_lower_marker(tmp_path):
    path = write_csv(tmp_path, "low.csv", "\n".join(
        ["id,marker,dx"] + [f"c{i},{50 + i},No" for i in range(25)]
        + [f"p{i},{20 + i},Yes" for i in range(25)]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"),
                 direction="lower", n_boot=300, seed=5)
    y = next(p for p in analysis_to_dict(an)["operating_points"]
             if p["key"] == "youden")
    lo, hi = y["bootstrap"]["cutoff_ci"]
    assert lo <= hi                      # a negated interval would be reversed
    assert 20 <= lo <= 75 and 20 <= hi <= 75      # inside the observed range
    assert y["cutoff"] > 0


def test_json_notes_do_not_carry_raw_cell_text(tmp_path):
    """The unparsed-value note lands in a file that gets emailed around."""
    path = write_csv(tmp_path, "pii.csv", "\n".join([
        "id,marker,dx",
        "1,홍길동 010-1234-5678,No",
        "2,901231-1234567,No",
        "3,NOT_DONE_patient_smith,Yes",
        "4,5,No", "5,6,No", "6,20,Yes", "7,21,Yes",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"))
    text = analysis_to_json(an)
    for leak in ("홍길동", "901231", "1234567", "smith", "patient_smith"):
        assert leak not in text, leak
    # No real digit groups survive — only the 9-masked shape does.
    assert "010-1234" not in text and "1234-5678" not in text
    assert not re.search(r"[0-8]{4}", text.split('"notes"')[1])
    assert "가가가 999-9999-999" in text           # the shape, which is safe
    assert "999999-9999999" in text               # an RRN-shaped cell, digits masked
    assert "숫자로 읽을 수 없는 값" in text          # still tells the user what happened


def test_json_reports_the_file_name_without_its_directory(tmp_path):
    an = analyze(load_dataset(read_table(demo(tmp_path, "환자_export.csv")),
                             "marker", "dx"))
    doc = analysis_to_dict(an)
    assert doc["input"]["file_name"] == "환자_export.csv"
    assert str(tmp_path) not in analysis_to_json(an)


def test_svg_youden_dot_sits_at_the_operating_point(tmp_path):
    """Plotting specificity instead of 1 − specificity survived every test."""
    an = full_analysis(tmp_path)
    pt = next(sp.metrics.point for sp in an.selected if sp.key == "youden")
    doc = minidom.parseString(roc_svg(an))
    circles = doc.getElementsByTagName("circle")
    assert len(circles) == 1
    cx = float(circles[0].getAttribute("cx"))
    cy = float(circles[0].getAttribute("cy"))
    assert cx == pytest.approx(68 + (1.0 - pt.spec) * 420, abs=0.5)
    assert cy == pytest.approx(52 + (1.0 - pt.sens) * 420, abs=0.5)


def test_svg_pauc_band_spans_exactly_the_requested_region(tmp_path):
    an = full_analysis(tmp_path)          # pauc over specificity 0.8-1.0
    doc = minidom.parseString(roc_svg(an))
    poly = doc.getElementsByTagName("polygon")[0]
    xs = [float(p.split(",")[0]) for p in poly.getAttribute("points").split()]
    assert min(xs) == pytest.approx(68.0, abs=0.5)             # fpr 0.0
    assert max(xs) == pytest.approx(68 + 0.2 * 420, abs=0.5)   # fpr 0.2
    assert max(xs) < 68 + 420 - 1                              # not the whole plot


def test_svg_comparator_curve_is_drawn_with_the_orientation_it_was_tested_with(tmp_path):
    """An un-flipped comparator would be drawn below the diagonal while its
    legend advertised an AUC above 0.5."""
    rows = ["id,a,b,dx"]
    for i in range(20):
        rows.append(f"c{i},{i},{100 - i},No")     # b is reversed: low = disease
    for i in range(20):
        rows.append(f"p{i},{i + 25},{50 - i},Yes")
    path = write_csv(tmp_path, "flip.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "a", "dx", compare_cols=["b"])
    an = analyze(ds, direction="higher")
    add_comparison(an, "b")                       # auto-flips the comparator
    finalize_comparisons(an)
    assert an.comparison_flipped["b"] is True
    assert an.comparisons[0].auc_b > 0.5
    doc = minidom.parseString(roc_svg(an))
    poly = doc.getElementsByTagName("polyline")[1]     # the comparator
    pts = [tuple(float(v) for v in p.split(","))
           for p in poly.getAttribute("points").split()]
    # The diagonal runs from (68, 472) to (488, 52); above it means a smaller y.
    for x, y in pts:
        assert y <= 472.0 - (x - 68.0) + 1.0, (x, y)


def test_svg_text_stays_inside_the_canvas_with_long_korean_names(tmp_path):
    """The clipped label used to be the "cut-off is optimistic" caveat itself."""
    from rocdx.svgplot import _text_w
    long_score = "혈청 C-반응성단백_정량검사결과_밀리그램퍼리터"
    rows = [f"환자식별번호,{long_score},비교검사_프로칼시토닌_정량,최종임상진단_패혈증여부"]
    for i in range(30):
        rows.append(f"c{i},{i},{40 - i},아니오")
    for i in range(30):
        rows.append(f"p{i},{i + 15},{i + 3},예")
    path = write_csv(tmp_path, "long.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), long_score, "최종임상진단_패혈증여부",
                      positive_label="예", compare_cols=["비교검사_프로칼시토닌_정량"])
    an = analyze(ds, pauc_min_spec=0.9, n_boot=200, seed=1)
    add_comparison(an, "비교검사_프로칼시토닌_정량")
    finalize_comparisons(an, ni_margin=0.05)
    svg = roc_svg(an)
    doc = minidom.parseString(svg)
    w = float(doc.documentElement.getAttribute("width"))
    h = float(doc.documentElement.getAttribute("height"))
    for t in doc.getElementsByTagName("text"):
        if t.getAttribute("transform"):        # the rotated y-axis title
            continue
        txt = t.firstChild.nodeValue if t.firstChild else ""
        size = float(t.getAttribute("font-size"))
        x, y = float(t.getAttribute("x")), float(t.getAttribute("y"))
        width = _text_w(txt, size)
        anchor = t.getAttribute("text-anchor") or "start"
        x0 = x if anchor == "start" else x - width
        assert x0 >= -1, (txt, x0)
        assert x0 + width <= w + 1, (txt, x0 + width, w)
        assert y <= h - 2, (txt, y, h)
    # and the caveat is present in full, not truncated away
    assert "optimistic." in svg_text(svg)


def test_svg_footer_does_not_overlap_the_axis_title(tmp_path):
    an = full_analysis(tmp_path)
    doc = minidom.parseString(roc_svg(an))
    ys = {}
    for t in doc.getElementsByTagName("text"):
        txt = t.firstChild.nodeValue if t.firstChild else ""
        if "false positive rate" in txt:
            ys["axis"] = float(t.getAttribute("y"))
        if txt.startswith("n = "):
            ys["foot"] = float(t.getAttribute("y"))
    assert ys["foot"] > ys["axis"] + 8


def test_svg_states_whether_the_interval_was_cluster_corrected(tmp_path):
    rows = ["pid,marker,dx"]
    for i in range(20):
        for _ in range(3):
            rows.append(f"C{i},{i},No")
            rows.append(f"P{i},{i + 5},Yes")
    path = write_csv(tmp_path, "cl.csv", "\n".join(rows) + "\n")
    ds = load_dataset(read_table(path), "marker", "dx", cluster_col="pid")
    corrected = svg_text(roc_svg(analyze(ds, n_boot=200, seed=1, cluster=True)))
    assert "cluster bootstrap" in corrected
    plain = svg_text(roc_svg(analyze(ds)))
    assert "NOT corrected for clustering" in plain


def test_json_to_stdout_is_utf8_whatever_the_locale_says(tmp_path):
    """Under LC_ALL=ISO8859-1 the Korean column names became "???" at rc=0."""
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = ["환자ID,검사값,진단결과"]
    for i in range(20):
        rows.append(f"P{i},{i},아니오")
    for i in range(20):
        rows.append(f"Q{i},{i + 25},예")
    path = write_csv(tmp_path, "kor.csv", "\n".join(rows) + "\n")
    env = dict(os.environ, LC_ALL="en_US.ISO8859-1", LANG="en_US.ISO8859-1",
               PYTHONPATH=root)
    env.pop("PYTHONIOENCODING", None)
    out = subprocess.run(
        [sys.executable, "-m", "rocdx.cli", path, "--score", "검사값",
         "--truth", "진단결과", "--positive-label", "예", "--json", "-"],
        capture_output=True, env=env, cwd=root)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    doc = json.loads(out.stdout.decode("utf-8"))     # must be valid UTF-8 JSON
    assert doc["input"]["score_column"] == "검사값"
    assert doc["input"]["positive_label"] == "예"
    assert "?" not in doc["input"]["score_column"]
