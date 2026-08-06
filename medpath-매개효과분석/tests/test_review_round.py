"""Tests added by the 2026-08-06 adversarial review round.

Every test here pins a defect that was *confirmed* by an independent reviewer,
or closes a coverage gap that let a real mutation of the code survive the
suite. They are grouped by the finding they guard.
"""

import json
import math
from random import Random

import pytest

from helpers import write_csv
from medpath.bootstrap import MIN_BOOT_FOR_CI, ci_from_boots
from medpath.cli import main
from medpath.dataio import DataError, build_design, load_table, parse_float
from medpath.mediation import analyze


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _chain(n=160, seed=7, k=2, xvals=(0.0, 1.0)):
    """X -> M1 -> M2 -> Y with every link genuinely non-zero."""
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = float(xvals[i % len(xvals)])
        m1 = round(10 + 2.0 * x + rng.gauss(0, 1.5), 4)
        m2 = round(4 + 0.6 * m1 + 0.5 * x + rng.gauss(0, 1.2), 4)
        m3 = round(1 + 0.4 * m2 + rng.gauss(0, 1.0), 4)
        y = round(5 + 0.8 * m2 + 0.3 * m1 + 0.5 * m3 + 1.0 * x + rng.gauss(0, 2.0), 4)
        rows.append([x, m1, m2, m3, y])
    header = ["x", "m1", "m2", "m3", "y"]
    return header, rows, ["m1", "m2", "m3"][:k]


def _design(tmp_path, name="c.csv", **kw):
    header, rows, meds = _chain(**{k: v for k, v in kw.items()
                                   if k in ("n", "seed", "k", "xvals")})
    t = load_table(write_csv(tmp_path / name, header, rows))
    return build_design(t, "x", meds, "y", kw.get("covariates", []))


# --------------------------------------------------------------------------
# G7 — the package's own QR-vs-Cholesky safety net must stay silent on clean
# data. This single assertion retroactively kills mutations that break the
# serial chain inside the bootstrap plan (the fast path would then disagree
# with the reported regressions).
# --------------------------------------------------------------------------
@pytest.mark.parametrize("serial,k", [(False, 2), (True, 2), (True, 3)])
def test_clean_data_raises_no_numerical_crosscheck_warning(tmp_path, serial, k):
    d = _design(tmp_path, k=k)
    res = analyze(d, serial=serial, n_boot=200, seed=1)
    assert not any("수치 점검" in w for w in res.warnings), res.warnings


# --------------------------------------------------------------------------
# G1 — the serial branch of EffectPlan was never bootstrapped by any test, so
# deleting the d-path from the chained product went unnoticed.
# --------------------------------------------------------------------------
def test_serial_bootstrap_intervals_bracket_the_reported_estimates(tmp_path):
    d = _design(tmp_path, k=2)
    res = analyze(d, serial=True, n_boot=600, seed=4)
    assert res.boot_ok == 600
    assert not any("수치 점검" in w for w in res.warnings)
    inds = res.indirect_effects
    assert len(inds) == 3                     # a1b1, a2b2, a1*d21*b2
    for e in inds:
        assert e.tested
        assert e.ci_lo < e.estimate < e.ci_hi, e.label
    # The chained path must carry three components, not two: dropping d21
    # would leave a 2-factor product identical to a parallel model.
    chained = [e for e in inds if len(e.components) == 3]
    assert len(chained) == 1
    a1, d21, b2 = [v for _, v, _ in chained[0].components]
    assert chained[0].estimate == pytest.approx(a1 * d21 * b2, rel=1e-12)


def test_serial_bootstrap_differs_from_parallel_on_the_same_columns(tmp_path):
    d = _design(tmp_path, k=2)
    ser = analyze(d, serial=True, n_boot=300, seed=2)
    par = analyze(_design(tmp_path, k=2), serial=False, n_boot=300, seed=2)
    # The *sum* is c - c' either way, so that is not the discriminator. The
    # split is: serial routes part of a1's effect through m2.
    assert len(ser.indirect_effects) == 3
    assert len(par.indirect_effects) == 2
    assert ser.effect("indirect_total").estimate == pytest.approx(
        par.effect("indirect_total").estimate, rel=1e-9)
    # x -> m1 -> y is identical (m1 has no priors), but x -> m2 -> y is not:
    # in the serial model m2 is regressed on x AND m1, so a2 changes.
    assert ser.indirect_effects[0].estimate == pytest.approx(
        par.indirect_effects[0].estimate, rel=1e-9)
    ser_m2 = ser.indirect_effects[1]
    par_m2 = par.indirect_effects[1]
    assert ser_m2.label == par_m2.label
    assert ser_m2.estimate != pytest.approx(par_m2.estimate, rel=1e-6)
    assert (ser_m2.ci_lo, ser_m2.ci_hi) != (par_m2.ci_lo, par_m2.ci_hi)


# --------------------------------------------------------------------------
# G3 / A4 — BCa must actually use a non-zero acceleration for *effects*, not
# just for contrasts. Degrading to BC under a "BCa" label is silent.
# --------------------------------------------------------------------------
def test_bca_effect_interval_uses_a_real_acceleration(tmp_path):
    d = _design(tmp_path, k=1)
    bca = analyze(d, n_boot=400, seed=9, ci_method="bca").indirect_effects[0]
    bc = analyze(d, n_boot=400, seed=9, ci_method="bc").indirect_effects[0]
    assert not bca.warnings, bca.warnings          # no "가속 계산 실패" fallback
    assert (bca.ci_lo, bca.ci_hi) != (bc.ci_lo, bc.ci_hi)
    assert "BCa" in bca.ci_method


# --------------------------------------------------------------------------
# Degenerate bootstrap: a handful of surviving resamples must NOT be rendered
# as a tested, significant interval.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("n_ok", [1, 2, 3, MIN_BOOT_FOR_CI - 1])
def test_too_few_resamples_yield_no_interval_rather_than_a_fake_one(n_ok):
    boots = [1.0 + 0.001 * i for i in range(n_ok)]
    lo, hi, warns = ci_from_boots(boots, 1.0, 0.95, "percentile")
    assert math.isnan(lo) and math.isnan(hi)
    assert warns and "재표본" in warns[0]


def test_enough_resamples_still_produce_an_interval():
    boots = [0.5 + 0.01 * i for i in range(MIN_BOOT_FOR_CI)]
    lo, hi, warns = ci_from_boots(boots, 1.0, 0.95, "percentile")
    assert math.isfinite(lo) and math.isfinite(hi) and lo < hi
    assert not warns


def test_one_bootstrap_replicate_is_reported_as_untested_not_significant(tmp_path):
    d = _design(tmp_path, k=1)
    res = analyze(d, n_boot=1, seed=3)
    eff = res.indirect_effects[0]
    assert not eff.tested
    assert not eff.significant
    assert eff.estimate != 0.0          # the point estimate survives


def test_unknown_ci_method_still_raises_even_for_a_tiny_sample():
    with pytest.raises(ValueError):
        ci_from_boots([1.0, 2.0], 1.0, 0.95, "nope")


# --------------------------------------------------------------------------
# Standardization: a numeric two-level X is per *unit*, so the partially
# standardized effect must carry the distance between the two levels.
# --------------------------------------------------------------------------
def _two_level_rows(hi, n=120, seed=5):
    rng = Random(seed)
    rows = []
    for i in range(n):
        x = float(hi) if i % 2 else 0.0
        m = round(10 + (2.0 / hi) * x + rng.gauss(0, 1.5), 4)
        y = round(5 + 0.8 * m + (1.0 / hi) * x + rng.gauss(0, 2.0), 4)
        rows.append([x, m, y])
    return rows


def test_numeric_two_level_x_records_its_span(tmp_path):
    t = load_table(write_csv(tmp_path / "s.csv", ["x", "m", "y"],
                             _two_level_rows(5)))
    d = build_design(t, "x", ["m"], "y", [])
    assert d.x_kind == "binary"
    assert d.x_span == pytest.approx(5.0)


def test_partially_standardized_effect_is_invariant_to_the_group_coding(tmp_path):
    """0/1 and 0/5 codings of the same contrast must standardize alike.

    The regression this pins: dividing a per-unit coefficient by SD(Y) made the
    0/5 version report an effect five times too small.
    """
    res = {}
    for hi in (1, 5):
        rows = _two_level_rows(hi)
        t = load_table(write_csv(tmp_path / ("s%d.csv" % hi), ["x", "m", "y"], rows))
        d = build_design(t, "x", ["m"], "y", [])
        r = analyze(d, n_boot=0)
        res[hi] = (r.effect("total").standardized,
                   r.indirect_effects[0].standardized)
    assert res[1][0] == pytest.approx(res[5][0], rel=1e-9)
    assert res[1][1] == pytest.approx(res[5][1], rel=1e-9)


def test_zero_one_dummy_standardization_is_unchanged(tmp_path):
    rows = _two_level_rows(1)
    t = load_table(write_csv(tmp_path / "d1.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])
    r = analyze(d, n_boot=0)
    sd_y = (sum((v - sum(d.y) / len(d.y)) ** 2 for v in d.y) / (len(d.y) - 1)) ** 0.5
    assert r.effect("total").standardized == pytest.approx(
        r.effect("total").estimate / sd_y, rel=1e-9)


# --------------------------------------------------------------------------
# Proportion mediated must not be printed when the denominator's own interval
# covers zero.
# --------------------------------------------------------------------------
def test_proportion_is_withheld_when_the_total_effect_ci_covers_zero(tmp_path):
    """a and b are real but of opposite sign to c', so c lands near zero."""
    rng = Random(11)
    rows = []
    for i in range(120):
        x = float(i % 2)
        m = round(10 + 1.2 * x + rng.gauss(0, 1.5), 4)
        y = round(5 + 0.5 * m - 0.6 * x + rng.gauss(0, 3.0), 4)
        rows.append([x, m, y])
    t = load_table(write_csv(tmp_path / "p.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])
    res = analyze(d, n_boot=0)
    total = res.effect("total")
    assert total.ci_lo <= 0.0 <= total.ci_hi          # precondition
    assert math.isnan(res.proportion_mediated)
    assert "0을 포함" in res.proportion_note


def test_proportion_is_reported_for_a_clearly_non_zero_total(tmp_path):
    d = _design(tmp_path, k=1)
    res = analyze(d, n_boot=0)
    assert res.effect("total").ci_lo > 0.0
    assert math.isfinite(res.proportion_mediated)


# --------------------------------------------------------------------------
# Decimal-comma detection: the highest-consequence parsing path in the package
# (a wrong call is a silent factor of 1000) had no test at all.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("token,expected", [
    ("3,142", 3.142),
    ("1.234,5", 1234.5),
    ("1.234", 1234.0),      # EU thousands
    ("12.5", 12.5),         # dot-decimal escape hatch: not 3 digits, unambiguous
    ("-0,5", -0.5),
])
def test_parse_float_in_comma_mode(token, expected):
    assert parse_float(token, ",") == pytest.approx(expected)


@pytest.mark.parametrize("token,expected", [
    ("3.142", 3.142),
    ("1,234", 1234.0),
    ("1,024.0", 1024.0),
])
def test_parse_float_in_point_mode(token, expected):
    assert parse_float(token, ".") == pytest.approx(expected)


def test_genuine_european_export_is_detected(tmp_path):
    path = tmp_path / "eu.csv"
    lines = ["x;m;y"] + ["%d;%s;%s" % (i % 2, "10,%d" % (i % 9), "20,%d" % (i % 7))
                         for i in range(40)]
    path.write_text("\n".join(lines), encoding="utf-8")
    t = load_table(str(path))
    assert t.decimal == ","


def test_one_stray_multiselect_cell_does_not_flip_the_file_to_comma_decimal(tmp_path):
    """Regression: a single '1,2' cell in an unused column re-read every
    three-decimal value as thousands, multiplying it by 1000 and reversing the
    reported conclusion."""
    rng = Random(3)
    rows = []
    for i in range(50):
        rows.append([i % 2, "1,2" if i == 11 else "1",
                     round(0.7 + 0.3 * (i % 2) + rng.gauss(0, 0.05), 3),
                     round(90 - 20 * (i % 2) + rng.gauss(0, 3), 3)])
    t = load_table(write_csv(tmp_path / "k.csv", ["arm", "code", "cr", "egfr"], rows))
    assert t.decimal == "."
    d = build_design(t, "arm", ["cr"], "egfr", [])
    # 0.9-ish creatinine values must stay sub-unit, not become ~900.
    assert max(d.mediators[0][1]) < 5.0


def test_decimal_flag_overrides_the_sniffer(tmp_path):
    rows = [[i % 2, "1.500", "2.250"] for i in range(20)]
    path = write_csv(tmp_path / "f.csv", ["x", "m", "y"], rows)
    assert load_table(path, decimal="point").decimal == "."
    assert load_table(path, decimal="comma").decimal == ","
    t_pt = load_table(path, decimal="point")
    t_cm = load_table(path, decimal="comma")
    assert parse_float(t_pt.column("m")[0], t_pt.decimal) == pytest.approx(1.5)
    assert parse_float(t_cm.column("m")[0], t_cm.decimal) == pytest.approx(1500.0)


# --------------------------------------------------------------------------
# Categorical covariate dummies: the existing test checked names and counts but
# never a single cell value, so inverting every dummy survived.
# --------------------------------------------------------------------------
def test_categorical_covariate_dummy_values_are_correct(tmp_path):
    sexes = ["남", "여", "남", "여", "기타", "남", "여", "기타"]
    rng = Random(2)
    rows = [[i % 2, sexes[i], round(10 + rng.gauss(0, 1), 3),
             round(20 + rng.gauss(0, 1), 3)] for i in range(8)]
    t = load_table(write_csv(tmp_path / "cc.csv", ["x", "sex", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", ["sex"])
    cov = dict(d.covariates)
    assert "sex=남" not in cov                       # most frequent -> reference
    assert cov["sex=여"] == [0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    assert cov["sex=기타"] == [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]


def test_one_bad_cell_keeps_a_continuous_covariate_continuous(tmp_path):
    """Regression: '45 세' in an age column silently demoted it to a
    dummy-coded factor with one level per distinct age."""
    rng = Random(6)
    rows = []
    for i in range(60):
        age = "45 세" if i == 17 else str(30 + (i % 25))
        rows.append([i % 2, age, round(10 + rng.gauss(0, 1), 3),
                     round(20 + rng.gauss(0, 1), 3)])
    t = load_table(write_csv(tmp_path / "cov.csv", ["x", "age", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", ["age"])
    assert [nm for nm, _ in d.covariates] == ["age"]
    assert any("age" in w and "숫자로 해석되지 않아" in w for w in d.warnings)
    assert d.n_used == 59            # the one bad row is dropped, not recoded


# --------------------------------------------------------------------------
# PII: an error message fired by a mistyped --x must not dump an ID column.
# --------------------------------------------------------------------------
def test_level_listing_in_errors_is_capped(tmp_path):
    rows = [["MRN%05d" % i, round(1.0 * i, 2), round(2.0 * i, 2)] for i in range(60)]
    t = load_table(write_csv(tmp_path / "id.csv", ["mrn", "m", "y"], rows))
    with pytest.raises(DataError) as exc:
        build_design(t, "mrn", ["m"], "y", [], reference="없는값")
    msg = str(exc.value)
    assert msg.count("MRN") <= 8
    assert "총 60개" in msg


# --------------------------------------------------------------------------
# Report honesty: "no resamples were drawn" is a false statement when they were
# drawn and all failed.
# --------------------------------------------------------------------------
def test_all_resamples_failed_is_not_reported_as_never_run(tmp_path):
    from medpath.report import render
    d = _design(tmp_path, k=1)
    res = analyze(d, n_boot=200, seed=1)
    for e in res.indirect_effects:          # force the "untested" branch
        e.ci_lo = e.ci_hi = float("nan")
    res.boot_ok = 0
    text = render(res, "x.csv", mode="text")
    assert "부트스트랩을 실행하지 않아" not in text
    assert "재표본" in text


def test_zero_bootstrap_still_says_it_was_never_run(tmp_path):
    from medpath.report import render
    d = _design(tmp_path, k=1)
    res = analyze(d, n_boot=0)
    text = render(res, "x.csv", mode="text")
    assert "부트스트랩을 실행하지 않아" in text


# --------------------------------------------------------------------------
# G4 — CLI flags must actually reach build_design/analyze. Each of these
# mutations (dropping robust=, reference=, x_levels=) survived the old suite.
# --------------------------------------------------------------------------
def _cli_csv(tmp_path, name="cli.csv", arms=("sham", "device")):
    rng = Random(8)
    rows = []
    for i in range(90):
        arm = arms[i % len(arms)]
        xi = 1.0 if arm == arms[-1] else 0.0
        m = round(10 + 2.0 * xi + rng.gauss(0, 1.5), 4)
        y = round(5 + 0.8 * m + 1.0 * xi + rng.gauss(0, 2.0), 4)
        rows.append([arm, m, y])
    return write_csv(tmp_path / name, ["arm", "m", "y"], rows)


def _run_json(tmp_path, path, extra, out_name):
    out = tmp_path / out_name
    rc = main([path, "--x", "arm", "--m", "m", "--y", "y",
               "--bootstrap", "0", "--json", "--out", str(out)] + extra)
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_robust_flag_reaches_the_model(tmp_path):
    path = _cli_csv(tmp_path)
    base = _run_json(tmp_path, path, [], "a.json")
    hc3 = _run_json(tmp_path, path, ["--robust", "hc3"], "b.json")
    assert base["settings"]["se_type"] == "classical"
    assert hc3["settings"]["se_type"] == "hc3"
    b_se = base["regressions"][0]["coefficients"][1]["se"]
    h_se = hc3["regressions"][0]["coefficients"][1]["se"]
    assert b_se != pytest.approx(h_se, rel=1e-9)


def test_reference_flag_reaches_the_design(tmp_path):
    path = _cli_csv(tmp_path)
    flipped = _run_json(tmp_path, path, ["--reference", "device"], "c.json")
    assert "0=device" in flipped["model"]["x_coding"]
    default = _run_json(tmp_path, path, [], "d.json")
    assert "0=sham" in default["model"]["x_coding"]
    # Flipping the reference flips the sign of every effect.
    a = {e["kind"]: e["estimate"] for e in default["effects"]}
    b = {e["kind"]: e["estimate"] for e in flipped["effects"]}
    assert b["total"] == pytest.approx(-a["total"], rel=1e-9)


def test_x_levels_flag_reaches_the_design(tmp_path):
    path = _cli_csv(tmp_path, "three.csv", arms=("placebo", "low", "high"))
    both = _run_json(tmp_path, path, ["--x-levels", "placebo,high"], "e.json")
    assert both["sample"]["n_analysed"] == 60          # the 'low' arm is excluded
    assert "0=placebo" in both["model"]["x_coding"]


# --------------------------------------------------------------------------
# G6 — diagnostics are computed and tested; their escalation into user-facing
# warnings was not.
# --------------------------------------------------------------------------
def test_strong_influence_point_is_warned_about(tmp_path):
    rng = Random(4)
    rows = [[i % 2, round(10 + 2.0 * (i % 2) + rng.gauss(0, 1.0), 4),
             round(5 + 0.8 * (10 + 2.0 * (i % 2)) + rng.gauss(0, 1.0), 4)]
            for i in range(40)]
    rows[0] = [1, 60.0, -400.0]          # a wildly influential point
    t = load_table(write_csv(tmp_path / "inf.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])
    res = analyze(d, n_boot=0)
    assert any("Cook" in w for w in res.warnings), res.warnings


def test_heteroscedastic_residuals_are_warned_about(tmp_path):
    rng = Random(12)
    rows = []
    for i in range(200):
        x = float(i % 2)
        m = round(10 + 2.0 * x + rng.gauss(0, 1.0), 4)
        # error variance grows steeply with m
        y = round(5 + 0.8 * m + rng.gauss(0, 0.15 * abs(m) ** 2), 4)
        rows.append([x, m, y])
    t = load_table(write_csv(tmp_path / "het.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])
    res = analyze(d, n_boot=0)
    assert res.bp_test is not None and res.bp_test[2] < 0.05
    assert any("Breusch" in w for w in res.warnings), res.warnings


# --------------------------------------------------------------------------
# Misc guards from the same review.
# --------------------------------------------------------------------------
def test_tiny_magnitude_column_is_not_mistaken_for_a_constant(tmp_path):
    rng = Random(1)
    rows = [[i % 2,
             "%.6e" % (1.6e-139 + 1e-140 * (i % 40)),
             "%.6e" % (2.0e-139 + rng.gauss(0, 1e-141))] for i in range(40)]
    t = load_table(write_csv(tmp_path / "tiny.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])     # must not raise "상수"
    assert d.n_used == 40


def test_truly_constant_column_is_still_rejected(tmp_path):
    rows = [[i % 2, 3.0, round(1.0 * i, 3)] for i in range(20)]
    t = load_table(write_csv(tmp_path / "const.csv", ["x", "m", "y"], rows))
    with pytest.raises(DataError, match="상수"):
        build_design(t, "x", ["m"], "y", [])


@pytest.mark.parametrize("token", ["미측정", "측정안함", "무응답", "해당없음"])
def test_korean_missing_labels_are_treated_as_missing(tmp_path, token):
    rng = Random(1)
    rows = [[i % 2, token if i == 3 else round(10 + rng.gauss(0, 1), 3),
             round(20 + rng.gauss(0, 1), 3)] for i in range(30)]
    t = load_table(write_csv(tmp_path / "na.csv", ["x", "m", "y"], rows))
    d = build_design(t, "x", ["m"], "y", [])
    assert d.n_used == 29
    # A missing cell is not an "unparseable value" — no scolding warning.
    assert not any("숫자로 해석되지 않아" in w for w in d.warnings)


def test_bad_delimiter_is_a_friendly_error_not_a_raw_valueerror(tmp_path):
    path = write_csv(tmp_path / "q.csv", ["x", "m", "y"], [[0, 1, 2], [1, 2, 3]])
    with pytest.raises(DataError):
        load_table(path, delimiter='"')
