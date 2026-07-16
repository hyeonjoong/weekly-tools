"""End-to-end builder tests with hand-computed expectations."""

import math

import pytest

from table1.build import CategoricalRow, ContinuousRow, Options, build_table1
from table1.dataio import Frame


def _frame(header, rows):
    return Frame(list(header), [list(map(str, r)) for r in rows])


def _row(table, name):
    return next(r for r in table.rows if r.name == name)


def test_continuous_summary_handcomputed():
    # group A: 1..5 (mean 3, sd 1.5811, median 3, q1 2, q3 4)
    # group B: 6..10 (mean 8, sd 1.5811)
    rows = [("A", v) for v in (1, 2, 3, 4, 5)] + [("B", v) for v in (6, 7, 8, 9, 10)]
    fr = _frame(["g", "x"], rows)
    t = build_table1(fr, Options(group_col="g", display="mean"))
    r = _row(t, "x")
    assert isinstance(r, ContinuousRow)
    a = r.per_group[0]
    assert a.n == 5
    assert abs(a.mean - 3.0) < 1e-12
    assert abs(a.sd - math.sqrt(2.5)) < 1e-12
    assert abs(a.median - 3.0) < 1e-12
    assert abs(a.q1 - 2.0) < 1e-12
    assert abs(a.q3 - 4.0) < 1e-12
    # overall mean of 1..10 = 5.5
    assert abs(r.overall.mean - 5.5) < 1e-12


def test_quantile_interpolation():
    rows = [("A", v) for v in (1, 2, 3, 4)] + [("B", v) for v in (10, 20, 30, 40)]
    fr = _frame(["g", "x"], rows)
    t = build_table1(fr, Options(group_col="g", display="median"))
    a = _row(t, "x").per_group[0]
    assert abs(a.median - 2.5) < 1e-12   # (2+3)/2
    assert abs(a.q1 - 1.75) < 1e-12
    assert abs(a.q3 - 3.25) < 1e-12


def test_test_selection_normal_equalvar_uses_student():
    rows = [("A", v) for v in (10, 11, 12, 13, 14, 15, 16, 17)] + \
           [("B", v) for v in (12, 13, 14, 15, 16, 17, 18, 19)]
    fr = _frame(["g", "x"], rows)
    r = _row(build_table1(fr, Options(group_col="g")), "x")
    # Both groups normal, equal variance (Levene median p high) -> Student t.
    assert r.test_name == "Student t"


def test_missing_counts_continuous():
    rows = [("A", "1"), ("A", ""), ("A", "NA"), ("B", "5"), ("B", "6")]
    fr = _frame(["g", "x"], rows)
    r = _row(build_table1(fr, Options(group_col="g", display="mean")), "x")
    assert r.n_missing_total == 2
    assert r.per_group[0].n == 1
    assert r.per_group[0].n_missing == 2


def test_categorical_counts_and_levels():
    rows = [("A", "F"), ("A", "M"), ("A", "F"), ("B", "M"), ("B", "M")]
    fr = _frame(["g", "sex"], rows)
    r = _row(build_table1(fr, Options(group_col="g")), "sex")
    assert isinstance(r, CategoricalRow)
    labels = [l.label for l in r.levels]
    assert labels == ["F", "M"]
    frow = r.levels[labels.index("F")]
    assert frow.counts == [2, 0]
    assert frow.overall == 2


def test_id_column_skipped():
    rows = [(f"S{i}", "A" if i % 2 else "B", i) for i in range(30)]
    fr = _frame(["id", "g", "x"], rows)
    t = build_table1(fr, Options(group_col="g"))
    names = [r.name for r in t.rows]
    assert "id" not in names
    assert any("id" in w for w in t.warnings)


def test_missing_group_rows_dropped():
    rows = [("A", 1), ("", 2), ("NA", 3), ("B", 4), ("B", 5)]
    fr = _frame(["g", "x"], rows)
    t = build_table1(fr, Options(group_col="g", display="mean"))
    assert t.overall_size == 3
    assert any("결측" in w for w in t.warnings)


def test_requires_two_groups():
    fr = _frame(["g", "x"], [("A", 1), ("A", 2)])
    with pytest.raises(ValueError, match="그룹"):
        build_table1(fr, Options(group_col="g"))


def test_missing_group_column():
    fr = _frame(["g", "x"], [("A", 1), ("B", 2)])
    with pytest.raises(ValueError, match="찾을 수 없"):
        build_table1(fr, Options(group_col="nope"))


def test_three_group_anova_or_kruskal():
    rows = ([("A", v) for v in (1, 2, 3, 4, 5)] +
            [("B", v) for v in (3, 4, 5, 6, 7)] +
            [("C", v) for v in (5, 6, 7, 8, 9)])
    fr = _frame(["g", "x"], rows)
    r = _row(build_table1(fr, Options(group_col="g")), "x")
    # All three groups pass normality -> parametric ANOVA.
    assert r.test_name == "One-way ANOVA"
    assert r.smd is None  # SMD only for two groups


def test_forced_categorical_overrides_numeric():
    rows = [("A", 1), ("A", 2), ("A", 3), ("B", 1), ("B", 2), ("B", 3)]
    fr = _frame(["g", "code"], rows)
    r = _row(build_table1(fr, Options(group_col="g", categorical=["code"])), "code")
    assert isinstance(r, CategoricalRow)


def test_constant_continuous_no_crash():
    rows = [("A", 5), ("A", 5), ("A", 5), ("B", 5), ("B", 5), ("B", 5)]
    fr = _frame(["g", "x"], rows)
    r = _row(build_table1(fr, Options(group_col="g", display="mean",
                                      continuous=["x"])), "x")
    # identical everywhere -> test undefined, p suppressed, no exception
    assert r.per_group[0].sd == 0.0
    assert r.pvalue is None
