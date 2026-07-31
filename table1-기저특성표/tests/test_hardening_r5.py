"""Round-5 hardening regressions.

Focus of this round: patient re-identification through the rendered table.
Everything here pins behaviour that protects a *small* cohort — the regime the
earlier absolute-threshold guards structurally could not reach.
"""

from __future__ import annotations

import pytest

from table1.build import Options, build_table1
from table1.dataio import Frame
from table1.render import render


def _frame(header, rows):
    return Frame(list(header), [list(r) for r in rows])


def _ids_frame(n=8):
    """A pilot-sized cohort carrying direct identifiers."""
    header = ["mrn", "name", "dob", "arm", "age"]
    rows = []
    for i in range(n):
        rows.append([
            f"{1000 + i}",
            f"Patient-{i}",
            f"19{60 + i}-0{i % 9 + 1}-1{i % 9}",
            "A" if i % 2 == 0 else "B",
            str(40 + i),
        ])
    return _frame(header, rows)


# --------------------------------------------------------------------------- #
# near-unique identifier guard (relative criterion)
# --------------------------------------------------------------------------- #
def test_near_unique_column_is_skipped_in_small_cohort():
    """A name/DOB column must not render one row per patient just because the
    cohort is smaller than --max-levels."""
    tbl = build_table1(_ids_frame(8), Options(group_col="arm"))
    names = [r.name for r in tbl.rows]
    assert "name" not in names
    assert "dob" not in names
    # ...and the real characteristic survives.
    assert "age" in names


def test_near_unique_skip_holds_even_with_generous_max_levels():
    """--max-levels cannot be raised past the cohort size to defeat the guard."""
    opt = Options(group_col="arm", max_display_levels=1000)
    tbl = build_table1(_ids_frame(8), opt)
    assert "name" not in [r.name for r in tbl.rows]


def test_near_unique_values_never_reach_any_rendered_format():
    """The identifiers must not appear in the table OR in the warning text —
    the warning is written into the same file the researcher shares."""
    tbl = build_table1(_ids_frame(8), Options(group_col="arm"))
    for fmt in ("md", "csv", "tsv", "json", "html"):
        text = render(tbl, Options(group_col="arm"), fmt=fmt)
        assert "Patient-0" not in text, fmt
        assert "1960-01-10" not in text, fmt


def test_near_unique_warning_names_the_column_and_counts_only():
    tbl = build_table1(_ids_frame(8), Options(group_col="arm"))
    warn = " ".join(tbl.warnings)
    assert "name" in warn
    assert "8" in warn  # the count is reported, the values are not


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_near_unique_warning_is_localized(lang):
    tbl = build_table1(_ids_frame(8), Options(group_col="arm", lang=lang))
    warn = " ".join(tbl.warnings)
    assert warn.strip()
    if lang == "en":
        # An English table must not leak Korean text.
        assert not any("가" <= ch <= "힣" for ch in warn)


# --------------------------------------------------------------------------- #
# the guard must not swallow legitimate variables
# --------------------------------------------------------------------------- #
def test_genuine_categorical_is_not_mistaken_for_an_identifier():
    """Three sites over 12 patients is a characteristic, not an ID."""
    rows = [[["A", "B", "C"][i % 3], "A" if i % 2 == 0 else "B", str(40 + i)]
            for i in range(12)]
    tbl = build_table1(_frame(["site", "arm", "age"], rows),
                       Options(group_col="arm"))
    assert "site" in [r.name for r in tbl.rows]


def test_tiny_cohort_below_min_obs_is_not_treated_as_an_identifier():
    """With only 4 observations, 4 distinct levels is not evidence of an ID —
    the guard needs enough rows to be confident."""
    rows = [["lvl%d" % i, "A" if i % 2 == 0 else "B"] for i in range(4)]
    tbl = build_table1(_frame(["v", "arm"], rows), Options(group_col="arm"))
    assert "v" in [r.name for r in tbl.rows]


def test_forced_categorical_still_overrides_the_guard():
    """The documented escape hatch keeps working for a user who means it."""
    opt = Options(group_col="arm", categorical=["name"], var_cols=["name"])
    tbl = build_table1(_ids_frame(8), opt)
    assert "name" in [r.name for r in tbl.rows]


def test_forced_categorical_identifier_is_warned_not_silent():
    """The override is honoured, but never silently."""
    opt = Options(group_col="arm", categorical=["name"], var_cols=["name"])
    tbl = build_table1(_ids_frame(8), opt)
    assert any("name" in w for w in tbl.warnings)


def test_forced_categorical_of_a_normal_variable_is_not_warned():
    """No false alarm on an ordinary forced categorical."""
    rows = [[["A", "B", "C"][i % 3], "A" if i % 2 == 0 else "B"]
            for i in range(12)]
    opt = Options(group_col="arm", categorical=["site"], var_cols=["site"])
    tbl = build_table1(_frame(["site", "arm"], rows), opt)
    assert not tbl.warnings


# --------------------------------------------------------------------------- #
# --group must not be pointed at an identifier column
# --------------------------------------------------------------------------- #
def test_group_by_identifier_is_rejected_in_a_small_cohort():
    """--group mrn would emit one COLUMN per patient — a line listing."""
    with pytest.raises(ValueError) as exc:
        build_table1(_ids_frame(8), Options(group_col="mrn"))
    assert "mrn" in str(exc.value)


def test_group_by_identifier_error_does_not_echo_the_identifiers():
    with pytest.raises(ValueError) as exc:
        build_table1(_ids_frame(8), Options(group_col="mrn"))
    msg = str(exc.value)
    for i in range(8):
        assert str(1000 + i) not in msg


def test_group_cardinality_cap_applies_in_a_large_cohort_too():
    """Above the absolute cap, reject even though groups are far from unique."""
    rows = [[str(i % 40), str(40 + i % 20)] for i in range(400)]
    with pytest.raises(ValueError):
        build_table1(_frame(["gid", "age"], rows), Options(group_col="gid"))


def test_realistic_multi_arm_trial_is_still_accepted():
    """A 4-arm dose-finding trial must keep working."""
    rows = [[["p", "low", "mid", "high"][i % 4], str(40 + i % 20)]
            for i in range(80)]
    tbl = build_table1(_frame(["arm", "age"], rows), Options(group_col="arm"))
    assert len(tbl.groups) == 4


def test_examples_are_not_quoted_for_a_skipped_identifier_column():
    """A skipped column must not have had its raw cells quoted by an earlier
    'looks numeric' warning — the skip decision comes first."""
    rows = [[f"196{i}-01-0{i + 1}", "A" if i % 2 == 0 else "B", str(40 + i)]
            for i in range(8)]
    tbl = build_table1(_frame(["dob", "arm", "age"], rows),
                       Options(group_col="arm"))
    warn = " ".join(tbl.warnings)
    assert "dob" in warn
    assert "1960-01-01" not in warn


# --------------------------------------------------------------------------- #
# --vars hygiene: duplicates and the group column corrupt the multiplicity family
# --------------------------------------------------------------------------- #
def _padjust_frame():
    rows = [["A" if i % 2 == 0 else "B", str(40 + i), str(20 + i % 7)]
            for i in range(30)]
    return _frame(["arm", "age", "isi"], rows)


def test_duplicate_vars_are_summarized_once():
    opt = Options(group_col="arm", var_cols=["age", "age", "isi"])
    tbl = build_table1(_padjust_frame(), opt)
    assert [r.name for r in tbl.rows] == ["age", "isi"]


def test_duplicate_vars_do_not_inflate_the_multiplicity_family():
    """The bug: a repeated column counted twice toward m, changing every
    adjusted p-value in the table."""
    clean = build_table1(_padjust_frame(),
                         Options(group_col="arm", var_cols=["age", "isi"],
                                 padjust="holm"))
    dupe = build_table1(_padjust_frame(),
                        Options(group_col="arm",
                                var_cols=["age", "age", "isi"],
                                padjust="holm"))
    assert ([r.p_adjusted for r in clean.rows]
            == [r.p_adjusted for r in dupe.rows])


def test_duplicate_vars_are_warned():
    opt = Options(group_col="arm", var_cols=["age", "age"])
    tbl = build_table1(_padjust_frame(), opt)
    assert any("age" in w for w in tbl.warnings)


def test_group_column_in_vars_is_dropped():
    """arm vs arm is p<0.001 and SMD=inf by construction — never a finding."""
    opt = Options(group_col="arm", var_cols=["age", "arm"])
    tbl = build_table1(_padjust_frame(), opt)
    assert [r.name for r in tbl.rows] == ["age"]
    assert any("arm" in w for w in tbl.warnings)


def test_group_column_in_vars_does_not_enter_the_multiplicity_family():
    clean = build_table1(_padjust_frame(),
                         Options(group_col="arm", var_cols=["age", "isi"],
                                 padjust="holm"))
    polluted = build_table1(_padjust_frame(),
                            Options(group_col="arm",
                                    var_cols=["age", "isi", "arm"],
                                    padjust="holm"))
    assert ([r.p_adjusted for r in clean.rows]
            == [r.p_adjusted for r in polluted.rows])
