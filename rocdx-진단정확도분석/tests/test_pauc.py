"""Partial AUC: recomputed from first principles, plus its honesty guards."""

import math

import pytest

from rocdx.analyze import analyze, load_dataset
from rocdx.cli import main
from rocdx.loader import LoadError, read_table
from rocdx.pauc import partial_auc, partial_auc_from_points, spec_range_to_fpr
from rocdx.roc import auc_from_scores, roc_points


def write_csv(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


# --- the maths ----------------------------------------------------------------

def test_full_range_partial_auc_equals_the_mann_whitney_auc():
    """pAUC(0, 1) must be the AUC itself — including with heavy ties."""
    scores = [1, 2, 2, 3, 3, 3, 4, 5, 5, 6]
    pos = [False, False, True, False, True, True, False, True, True, True]
    pa = partial_auc(scores, pos, 0.0, 1.0)
    assert pa.area == pytest.approx(auc_from_scores(scores, pos))
    assert pa.max_area == pytest.approx(1.0)
    assert pa.chance_area == pytest.approx(0.5)


def test_partial_areas_split_at_any_boundary_and_sum_back_to_the_auc():
    scores = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5]
    pos = [True, False, True, False, True, True, False, True, False, False, True]
    total = auc_from_scores(scores, pos)
    for t in (0.07, 0.25, 0.5, 0.5001, 0.83):
        left = partial_auc(scores, pos, 0.0, t).area
        right = partial_auc(scores, pos, t, 1.0).area
        assert left + right == pytest.approx(total, abs=1e-12)


def test_hand_computed_partial_auc_on_a_curve_with_a_known_shape():
    """4 controls, 2 cases; the empirical curve is checked point by point.

    Scores: cases 10, 6; controls 8, 4, 2, 1. FPR steps are 0, .25, .5, .75, 1.
    Curve: (0,0) -> (0,.5) at 10 -> (.25,.5) at 8 -> (.25,1) at 6 -> (1,1).
    Over FPR in [0, 0.25] the area is 0.25*0.5 = 0.125 (a flat run at TPR .5).
    """
    scores = [10, 6, 8, 4, 2, 1]
    pos = [True, True, False, False, False, False]
    pts = roc_points(scores, pos)
    pa = partial_auc_from_points(pts, 0.0, 0.25)
    assert pa.area == pytest.approx(0.125)
    assert pa.max_area == pytest.approx(0.25)
    assert pa.chance_area == pytest.approx(0.25 ** 2 / 2)
    # McClish: 0.5 * (1 + (area - chance) / (max - chance))
    expected = 0.5 * (1 + (0.125 - 0.03125) / (0.25 - 0.03125))
    assert pa.standardized == pytest.approx(expected)


def test_boundary_inside_a_segment_is_linearly_interpolated():
    """One case, one control: the curve is a single diagonal-free step.

    Scores: case 2, control 1 → curve (0,0) -> (0,1) -> (1,1). Over [0, 0.4]
    the area is a full rectangle 0.4 * 1 = 0.4.
    """
    pa = partial_auc([2, 1], [True, False], 0.0, 0.4)
    assert pa.area == pytest.approx(0.4)
    assert pa.standardized == pytest.approx(1.0)


def test_chance_marker_gives_standardized_pauc_near_one_half():
    """An interleaved (chance) marker standardises to about 0.5 in a sub-range."""
    n = 200
    scores = list(range(n))
    pos = [i % 2 == 0 for i in range(n)]  # perfectly interleaved → AUC ≈ 0.5
    assert auc_from_scores(scores, pos) == pytest.approx(0.5, abs=0.01)
    pa = partial_auc(scores, pos, 0.0, 0.2)
    assert pa.standardized == pytest.approx(0.5, abs=0.05)


def test_perfect_marker_standardizes_to_one_in_the_high_specificity_region():
    scores = [1, 2, 3, 4, 10, 11, 12, 13]
    pos = [False] * 4 + [True] * 4
    assert partial_auc(scores, pos, 0.0, 0.1).standardized == pytest.approx(1.0)


def test_reversed_marker_gives_standardized_pauc_below_one_half():
    scores = [10, 11, 12, 13, 1, 2, 3, 4]
    pos = [False] * 4 + [True] * 4
    assert partial_auc(scores, pos, 0.0, 0.25).standardized < 0.5


def test_single_group_returns_nan_rather_than_a_number():
    pa = partial_auc([1, 2, 3], [True, True, True], 0.0, 0.5)
    assert math.isnan(pa.area) and math.isnan(pa.standardized)


def test_bad_ranges_are_rejected():
    with pytest.raises(ValueError):
        partial_auc_from_points(roc_points([1, 2], [True, False]), 0.5, 0.5)
    with pytest.raises(ValueError):
        partial_auc_from_points(roc_points([1, 2], [True, False]), -0.1, 0.5)
    with pytest.raises(ValueError):
        partial_auc_from_points(roc_points([1, 2], [True, False]), 0.0, 1.5)
    with pytest.raises(ValueError):
        spec_range_to_fpr(0.9, 0.9)


def test_spec_range_maps_to_the_fpr_range_the_maths_uses():
    assert spec_range_to_fpr(0.9) == pytest.approx((0.0, 0.1))
    assert spec_range_to_fpr(0.8, 0.95) == pytest.approx((0.05, 0.2))


# --- wiring through analyze() and the CLI -------------------------------------

def _demo(tmp_path):
    rows = ["id,marker,dx"]
    for i in range(40):
        rows.append(f"c{i},{i * 0.5:.2f},No")
    for i in range(40):
        rows.append(f"p{i},{10 + i * 0.5:.2f},Yes")
    return write_csv(tmp_path, "d.csv", "\n".join(rows) + "\n")


def test_analyze_attaches_the_partial_auc_only_when_asked(tmp_path):
    ds = load_dataset(read_table(_demo(tmp_path)), "marker", "dx")
    assert analyze(ds).pauc is None
    an = analyze(ds, pauc_min_spec=0.9)
    assert an.pauc is not None
    assert an.pauc.spec_low == pytest.approx(0.9)
    assert an.pauc.spec_high == pytest.approx(1.0)


def test_partial_auc_uses_the_oriented_scores_not_the_raw_ones(tmp_path):
    """A low-is-bad marker must give the same pAUC after --direction lower."""
    path = write_csv(tmp_path, "low.csv", "\n".join(
        ["id,marker,dx"]
        + [f"c{i},{100 + i}" + ",No" for i in range(20)]     # controls score high
        + [f"p{i},{i}" + ",Yes" for i in range(20)]) + "\n")  # cases score low
    ds = load_dataset(read_table(path), "marker", "dx")
    low = analyze(ds, direction="lower", pauc_min_spec=0.8).pauc
    assert low.standardized == pytest.approx(1.0)
    # Told the wrong direction, the same data must look bad, not good.
    high = analyze(ds, direction="higher", pauc_min_spec=0.8).pauc
    assert high.standardized < 0.5


def test_bootstrap_gives_the_partial_auc_an_interval_that_brackets_it(tmp_path):
    ds = load_dataset(read_table(_demo(tmp_path)), "marker", "dx")
    an = analyze(ds, pauc_min_spec=0.8, n_boot=300, seed=7)
    pa = an.pauc
    assert pa.ci is not None and pa.area_ci is not None
    assert pa.ci_source == "bootstrap"
    assert pa.ci[0] <= pa.standardized <= pa.ci[1] + 1e-12
    assert pa.n_effective > 0


def test_without_bootstrap_the_missing_interval_is_stated_not_hidden(tmp_path):
    an = analyze(load_dataset(read_table(_demo(tmp_path)), "marker", "dx"),
                 pauc_min_spec=0.9)
    assert an.pauc.ci is None
    assert any("부분 AUC" in w and "bootstrap" in w for w in an.warnings)


def test_sparse_pauc_region_is_warned_about(tmp_path):
    """With 4 controls, the region spec >= 0.99 contains no observed point."""
    path = write_csv(tmp_path, "s.csv", "\n".join([
        "id,marker,dx", "1,1,No", "2,2,No", "3,3,No", "4,4,No",
        "5,5,Yes", "6,6,Yes", "7,7,Yes", "8,8,Yes",
    ]) + "\n")
    an = analyze(load_dataset(read_table(path), "marker", "dx"), pauc_min_spec=0.99)
    assert any("보간" in w for w in an.warnings)


def test_invalid_pauc_bounds_are_rejected_with_a_clear_error(tmp_path):
    ds = load_dataset(read_table(_demo(tmp_path)), "marker", "dx")
    for kwargs in ({"pauc_min_spec": 1.0}, {"pauc_min_spec": -0.1},
                   {"pauc_min_spec": 0.9, "pauc_max_spec": 0.8},
                   {"pauc_min_spec": 0.9, "pauc_max_spec": 1.5}):
        with pytest.raises(LoadError):
            analyze(ds, **kwargs)


def test_cli_prints_the_partial_auc_and_labels_the_range(tmp_path, capsys):
    assert main([_demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--pauc-min-spec", "0.9", "--no-curve"]) == 0
    out = capsys.readouterr().out
    assert "부분 AUC" in out and "McClish" in out
    assert "0.9" in out


def test_cli_rejects_an_impossible_pauc_range(tmp_path, capsys):
    assert main([_demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--pauc-min-spec", "0.9", "--pauc-max-spec", "0.5"]) == 2
    assert "pauc" in capsys.readouterr().err.lower()


def test_pauc_max_spec_alone_is_refused_instead_of_being_ignored(tmp_path, capsys):
    """Silently ignoring it would report a range the user did not ask for."""
    assert main([_demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--pauc-max-spec", "0.95"]) == 2
    assert "--pauc-min-spec" in capsys.readouterr().err


def test_non_finite_pauc_bounds_are_refused(tmp_path, capsys):
    for bad in ("nan", "inf"):
        assert main([_demo(tmp_path), "--score", "marker", "--truth", "dx",
                     "--pauc-min-spec", bad]) == 2
        assert "유한한" in capsys.readouterr().err
    # "-inf" has to be passed with "=" or argparse reads it as an option name.
    assert main([_demo(tmp_path), "--score", "marker", "--truth", "dx",
                 "--pauc-min-spec=-inf"]) == 2
    assert "유한한" in capsys.readouterr().err


def test_markdown_and_paper_sentence_carry_the_partial_auc(tmp_path, capsys):
    from rocdx.report import markdown_report, paper_sentence
    an = analyze(load_dataset(read_table(_demo(tmp_path)), "marker", "dx"),
                 pauc_min_spec=0.9, n_boot=200, seed=3)
    md = markdown_report(an)
    assert "부분 AUC" in md and "표준화" in md
    assert "직접 비교하지 마세요" in md
    assert "부분 AUC" in paper_sentence(an)


# --- regression: gaps a mutation run walked straight through ------------------

def test_bounded_range_chance_and_max_areas_are_hand_computed():
    """With fpr_low > 0 the chance area is (hi²−lo²)/2, not (hi−lo)²/2.

    Both wrong formulas survived the suite because every earlier numeric test
    used fpr_low == 0, where they happen to agree.
    """
    n = 200
    scores = list(range(n))
    pos = [i % 2 == 0 for i in range(n)]        # chance marker
    pa = partial_auc(scores, pos, 0.05, 0.20)
    assert pa.chance_area == pytest.approx((0.20 ** 2 - 0.05 ** 2) / 2.0)
    assert pa.max_area == pytest.approx(0.15)
    assert pa.standardized == pytest.approx(0.5, abs=0.05)
    # the two wrong formulas would land outside this band
    assert pa.standardized != pytest.approx(0.5175, abs=1e-3)


def test_range_boundary_inside_a_sloping_segment_is_interpolated():
    """A tie creates a diagonal segment; clipping it must not take the whole trapezoid.

    Scores: case 3, control 3 (tied), case 2, control 1.
    Curve: (0,0) → (0.5,0.5) via the tie → (0.5,1) at 2 → (1,1) at 1.
    Over FPR [0, 0.25] the curve is the diagonal y = x, so the area is
    0.25²/2 = 0.03125. Averaging the segment *endpoints* instead would give
    0.25 * (0 + 0.5)/2 = 0.0625 — exactly double.
    """
    pa = partial_auc([3, 3, 2, 1], [True, False, True, False], 0.0, 0.25)
    assert pa.area == pytest.approx(0.03125)
    assert pa.area != pytest.approx(0.0625)


def test_below_chance_partial_auc_is_warned_and_kept_out_of_the_paper_draft(tmp_path):
    """A window where the marker is worse than chance must not read as performance."""
    from rocdx.report import paper_sentence
    # 40 controls, so the region is resolved by several observed FPRs and the
    # below-chance branch (not the unresolved-region branch) is what fires.
    rows = ["x,y"] + [f"{i},1" for i in range(40)] + [f"{100 + i},0" for i in range(40)]
    path = write_csv(tmp_path, "neg.csv", "\n".join(rows) + "\n")
    an = analyze(load_dataset(read_table(path), "x", "y"), direction="higher",
                 pauc_min_spec=0.7, pauc_max_spec=1.0)
    assert an.pauc.standardized < 0.5
    assert an.pauc.n_observed_fprs >= 3
    assert any("우연(0.5)보다 낮습니다" in w for w in an.warnings)
    draft = paper_sentence(an)
    assert "판별력이 확인되지 않았으며" in draft
    assert "부분 AUC는 표준화 값" not in draft


def test_unresolved_pauc_region_is_refused_in_the_paper_draft(tmp_path):
    """A band containing one observed FPR is interpolation, not measurement."""
    from rocdx.report import paper_sentence
    path = write_csv(tmp_path, "thin.csv", "\n".join(
        ["x,y"] + [f"{i},1" for i in range(1, 6)]
        + [f"{i},0" for i in range(6, 11)]) + "\n")
    an = analyze(load_dataset(read_table(path), "x", "y"), direction="higher",
                 pauc_min_spec=0.0, pauc_max_spec=0.1)
    assert an.pauc.n_observed_fprs < 3
    draft = paper_sentence(an)
    assert "사실상 보간값" in draft
    assert "부분 AUC는 표준화 값" not in draft
