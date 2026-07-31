"""Independent reference values and cross-implementation oracles.

Every number asserted here comes from a published table or from a *different*
implementation in this repo — never from the module under test.
"""

import json
import math
import time

import pytest

from agreestat import categorical as Cat
from agreestat.cli import main
from agreestat.dataio import _require_unique_header, label_rater_rows, reshape_long
from agreestat.multirater import (
    fleiss_kappa,
    fleiss_per_category,
    gwet_ac1_multi,
    icc_family,
    krippendorff_alpha_multi,
    multi_categorical,
    multi_continuous,
)
from agreestat.multireport import (
    render_multi_json,
    render_multi_markdown,
    render_multi_text,
    render_multicat_text,
)

# Fleiss (1971) worked example: 10 subjects, 14 raters, 5 categories.
# Published: kappa = 0.210, P_bar = 0.378, P_e_bar = 0.213.
FLEISS_1971 = [
    [0, 0, 0, 0, 14],
    [0, 2, 6, 4, 2],
    [0, 0, 3, 5, 6],
    [0, 3, 9, 2, 0],
    [2, 2, 8, 1, 1],
    [7, 7, 0, 0, 0],
    [3, 2, 6, 3, 0],
    [2, 5, 3, 2, 2],
    [6, 5, 2, 1, 0],
    [0, 2, 2, 3, 7],
]

# Shrout & Fleiss (1979) Table 1.
SF_TABLE = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
            [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]


def test_fleiss_1971_published_example():
    kap, p_bar, pe, _se, m, _p_j = fleiss_kappa(FLEISS_1971)
    assert m == 14.0
    assert p_bar == pytest.approx(0.378, abs=5e-4)
    assert pe == pytest.approx(0.213, abs=5e-4)
    assert kap == pytest.approx(0.210, abs=5e-4)


def test_shrout_fleiss_published_confidence_intervals():
    """Published CIs: ICC(2,1) .019-.761, ICC(3,1) .342-.946."""
    fam = icc_family(SF_TABLE)
    _icc11, icc21, icc31 = fam.single
    assert icc21.ci_lower == pytest.approx(0.019, abs=1e-3)
    assert icc21.ci_upper == pytest.approx(0.761, abs=1e-3)
    assert icc31.ci_lower == pytest.approx(0.342, abs=1e-3)
    assert icc31.ci_upper == pytest.approx(0.946, abs=1e-3)


# --------------------------------------------------------------------------
# At m = 2 the multi-rater coefficients must collapse onto the independent
# two-rater implementations in agreestat.categorical.
# --------------------------------------------------------------------------
PAIR_A = ["a", "a", "b", "c", "b", "a", "c", "c", "a", "b",
          "b", "a", "c", "b", "a", "a", "c", "b", "b", "c"]
PAIR_B = ["a", "b", "b", "c", "a", "a", "c", "b", "a", "b",
          "c", "a", "c", "b", "b", "a", "a", "b", "c", "c"]
PAIR_CATS = ["a", "b", "c"]


def _pair_counts():
    return [[int(x == c) + int(y == c) for c in PAIR_CATS]
            for x, y in zip(PAIR_A, PAIR_B)]


def test_two_rater_fleiss_equals_scott_pi():
    cm = Cat.confusion_matrix(PAIR_A, PAIR_B, PAIR_CATS)
    assert fleiss_kappa(_pair_counts())[0] == pytest.approx(
        Cat.scott_pi(cm), rel=1e-12)


def test_two_rater_gwet_matches_the_pairwise_implementation():
    cm = Cat.confusion_matrix(PAIR_A, PAIR_B, PAIR_CATS)
    assert gwet_ac1_multi(_pair_counts())[0] == pytest.approx(
        Cat.gwet_ac(cm).value, rel=1e-12)


@pytest.mark.parametrize("metric", ["nominal", "ordinal"])
def test_two_rater_krippendorff_matches_the_pairwise_implementation(metric):
    cm = Cat.confusion_matrix(PAIR_A, PAIR_B, PAIR_CATS)
    assert krippendorff_alpha_multi(_pair_counts(), PAIR_CATS, metric) == \
        pytest.approx(Cat.krippendorff_alpha(cm, metric), rel=1e-12)


def test_per_category_kappa_reconstructs_the_overall_kappa():
    """Identity: kappa = sum_j p_j q_j kappa_j / sum_j p_j q_j."""
    cats = [f"c{i}" for i in range(5)]
    entries = fleiss_per_category(FLEISS_1971, cats)
    num = sum(e.proportion * (1 - e.proportion) * e.kappa for e in entries)
    den = sum(e.proportion * (1 - e.proportion) for e in entries)
    assert num / den == pytest.approx(fleiss_kappa(FLEISS_1971)[0], rel=1e-12)


# --------------------------------------------------------------------------
# Renderers must place each number in its own row / field
# --------------------------------------------------------------------------
def _icc_row(txt, model):
    for line in txt.splitlines():
        if line.strip().startswith(model + " "):
            return line
    raise AssertionError(f"no {model} row in report")


def test_text_report_places_each_icc_value_in_its_own_row():
    res = multi_continuous(["r1", "r2", "r3", "r4"], SF_TABLE)
    txt = render_multi_text(res)
    for r in list(res.icc.single) + list(res.icc.average):
        line = _icc_row(txt, r.model)
        assert f"{r.value:.3f}" in line
        assert f"{r.ci_lower:.3f}" in line and f"{r.ci_upper:.3f}" in line
        # the CI must read low-then-high, not swapped
        assert line.index(f"{r.ci_lower:.3f}") < line.index(f"{r.ci_upper:.3f}") \
            or r.ci_lower == r.ci_upper


def test_json_and_markdown_carry_the_same_icc_numbers_as_the_object():
    res = multi_continuous(["r1", "r2", "r3", "r4"], SF_TABLE)
    data = json.loads(render_multi_json(res))
    for got, want in zip(data["icc"]["single"], res.icc.single):
        assert got["model"] == want.model
        assert got["value"] == pytest.approx(want.value)
        assert got["ci_lower"] == pytest.approx(want.ci_lower)
    md = render_multi_markdown(res)
    icc21 = res.icc.single[1]
    assert f"| ICC(2,1) | {icc21.value:.3f} |" in md


def test_categorical_text_reports_each_coefficient_once_with_its_own_ci():
    rows = [["mild", "mild", "moderate"], ["severe", "severe", "severe"],
            ["mild", "moderate", "mild"], ["moderate", "moderate", "moderate"],
            ["severe", "moderate", "severe"], ["mild", "mild", "mild"]] * 4
    res = multi_categorical(["A", "B", "C"], rows, ["mild", "moderate", "severe"],
                            bootstrap=500)
    txt = render_multicat_text(res)
    for label, value, ci in (("Fleiss' kappa", res.fleiss, res.fleiss_ci),
                             ("Gwet's AC1", res.ac1, res.ac1_ci),
                             ("Krippendorff's alpha", res.kalpha, res.kalpha_ci)):
        line = next(ln for ln in txt.splitlines()
                    if ln.strip().startswith(label))
        assert f"{value:.3f}" in line
        assert f"{ci[0]:.3f}" in line and f"{ci[1]:.3f}" in line


# --------------------------------------------------------------------------
# PII / DoS guardrails
# --------------------------------------------------------------------------
def test_duplicate_key_error_never_echoes_the_identifier():
    header = ["mrn", "rater", "grade"]
    secret = "홍길동-8801011234567"
    data = [[secret, "a", "mild"], [secret, "a", "severe"], [secret, "b", "mild"]]
    with pytest.raises(ValueError) as exc:
        reshape_long(header, data, "mrn", "rater", "grade")
    assert secret not in str(exc.value)
    assert "행" in str(exc.value)


def test_unknown_category_error_truncates_raw_values():
    from agreestat.categorical import order_categories
    observed = [f"환자{i:03d}-01011234567890123456" for i in range(60)]
    with pytest.raises(ValueError) as exc:
        order_categories(observed, ["mild", "severe"])
    msg = str(exc.value)
    assert "외 " in msg and len(msg) < 400
    assert observed[10] not in msg


def test_missing_rater_column_error_truncates_the_header(tmp_path, capsys):
    header = ",".join(["KIM-MRN-0042931", "850101-1234567"]
                      + [f"col{i}" for i in range(40)])
    p = tmp_path / "h.csv"
    p.write_text(header + "\n" + ",".join(["1"] * 42) + "\n", encoding="utf-8")
    assert main([str(p), "--raters", "r1,r2,r3"]) == 2
    err = capsys.readouterr().err
    assert "외 " in err
    assert "col30" not in err


def test_over_long_labels_are_refused(tmp_path):
    from agreestat.dataio import load_rater_table
    long_label = "환자 자유서술 " * 20
    p = tmp_path / "t.csv"
    p.write_text(f"a,b,c\n{long_label},{long_label},x\ny,y,y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="자유서술 텍스트나 식별자"):
        label_rater_rows(load_rater_table(str(p), ["a", "b", "c"]))


def test_too_many_categories_is_refused(tmp_path):
    from agreestat.dataio import load_rater_table
    p = tmp_path / "t.csv"
    rows = "\n".join(f"L{i},L{i},L{i}" for i in range(250))
    p.write_text("a,b,c\n" + rows + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="너무 많습니다"):
        label_rater_rows(load_rater_table(str(p), ["a", "b", "c"]))


def test_pairwise_kappa_table_is_skipped_when_the_work_explodes():
    cats = [f"C{i}" for i in range(200)]
    names = [f"r{i}" for i in range(60)]
    rows = [[cats[(i + j) % 200] for j in range(60)] for i in range(40)]
    res = multi_categorical(names, rows, cats, bootstrap=200)
    assert res.pairwise == []
    assert any("쌍별 kappa 표" in w for w in res.warnings)


def test_reshape_long_is_linear_in_subject_count():
    header = ["id", "m", "v"]
    data = [[f"S{i}", f"r{j}", "1.0"] for i in range(20000) for j in range(3)]
    t0 = time.monotonic()
    names, rows, ids, _n = reshape_long(header, data, "id", "m", "v")
    assert time.monotonic() - t0 < 3.0
    assert len(ids) == 20000 and len(names) == 3 and len(rows) == 20000


def test_duplicate_header_check_is_linear():
    header = [f"c{i}" for i in range(20000)]
    t0 = time.monotonic()
    _require_unique_header(header)
    assert time.monotonic() - t0 < 0.5


def test_too_many_raters_is_refused(tmp_path):
    from agreestat.dataio import load_rater_table
    names = [f"r{i}" for i in range(150)]
    p = tmp_path / "w.csv"
    p.write_text(",".join(names) + "\n" + ",".join(["1"] * 150) + "\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="상한"):
        load_rater_table(str(p), names)


# --------------------------------------------------------------------------
# CSV record integrity
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ch", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
                                "\x85", " ", " "])
def test_line_break_lookalikes_do_not_split_records(tmp_path, ch):
    """str.splitlines() would tear these cells into two rows."""
    from agreestat.dataio import _read_rows
    p = tmp_path / "t.csv"
    p.write_text(f"id,r1,r2\nS1,a{ch}b,c\nS2,d,e\n", encoding="utf-8",
                 newline="")
    header, data, _notes = _read_rows(str(p))
    assert header == ["id", "r1", "r2"]
    assert len(data) == 2
    assert data[0] == ["S1", f"a{ch}b", "c"]


def test_quoted_newline_inside_a_label_is_preserved(tmp_path):
    from agreestat.dataio import _read_rows
    p = tmp_path / "t.csv"
    p.write_text('id,r1,r2\nS1,"a\nb","ab"\nS2,c,c\n', encoding="utf-8",
                 newline="")
    _h, data, _n = _read_rows(str(p))
    assert len(data) == 2
    assert data[0][1] == "a\nb" and data[0][2] == "ab"


def test_blank_first_line_is_reported(tmp_path):
    from agreestat.dataio import _read_rows
    p = tmp_path / "t.csv"
    p.write_text(",,,\nid,a,b,c\nS1,1,2,3\n", encoding="utf-8", newline="")
    header, data, notes = _read_rows(str(p))
    assert header == ["id", "a", "b", "c"] or any("첫 줄" in n for n in notes)


# --------------------------------------------------------------------------
# CLI branches the coverage report flagged as untested
# --------------------------------------------------------------------------
def test_empty_raters_string_is_rejected(tmp_path, capsys):
    p = tmp_path / "w.csv"
    p.write_text("id,r1,r2,r3\n1,1,2,3\n2,4,5,6\n", encoding="utf-8")
    assert main([str(p), "--raters", ""]) == 2
    assert "2개 이상" in capsys.readouterr().err


def test_categorical_multi_markdown_to_file(tmp_path, capsys):
    p = tmp_path / "c.csv"
    p.write_text("a,b,c\n" + "\n".join(
        ["mild,mild,moderate", "severe,severe,severe", "mild,moderate,mild",
         "moderate,moderate,moderate"] * 3) + "\n", encoding="utf-8")
    out = tmp_path / "o.md"
    assert main([str(p), "--raters", "a,b,c", "--categorical",
                 "--bootstrap", "200", "--markdown", str(out)]) == 0
    assert "Fleiss' kappa" in out.read_text(encoding="utf-8")
    assert "저장했습니다" in capsys.readouterr().err


def test_non_integer_confidence_level_renders(tmp_path, capsys):
    p = tmp_path / "w.csv"
    p.write_text("id,r1,r2,r3\n" + "\n".join(
        f"{i},{i},{i + 0.2},{i - 0.1}" for i in range(10)) + "\n",
        encoding="utf-8")
    assert main([str(p), "--raters", "r1,r2,r3", "--alpha", "0.033"]) == 0
    out = capsys.readouterr().out
    assert "96.7% CI" in out and "NaN%" not in out


def test_min_kappa_out_of_range_is_rejected(tmp_path, capsys):
    p = tmp_path / "c.csv"
    p.write_text("a,b,c\nmild,mild,severe\nsevere,severe,severe\n",
                 encoding="utf-8")
    assert main([str(p), "--raters", "a,b,c", "--categorical",
                 "--min-kappa", "99"]) == 2
    assert "--min-kappa" in capsys.readouterr().err


def test_threshold_met_branch_renders(tmp_path, capsys):
    p = tmp_path / "c.csv"
    p.write_text("a,b,c\n" + "\n".join(
        ["mild,mild,mild", "severe,severe,severe"] * 15) + "\n",
        encoding="utf-8")
    assert main([str(p), "--raters", "a,b,c", "--categorical",
                 "--bootstrap", "300", "--min-kappa", "0.6"]) == 0
    assert "판정: 충족" in capsys.readouterr().out
