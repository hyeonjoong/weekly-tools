"""Regression tests for the round-2 hardening findings.

Every test here corresponds to a defect that shipped: a traceback escaping the
CLI, a silently-wrong number, an accidentally quadratic loop, or a report that
asserted something the data did not support.
"""

import math
import time

import pytest

from statwise.analyze import analyze
from statwise.binary import BinaryGroup, odds_ratio, risk_ratio
from statwise.cli import main
from statwise.normality import shapiro_wilk
from statwise.special import chi2_sf, t_ppf, t_sf
from statwise.tests_stat import levene, one_way_anova, variance


def _write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


# --------------------------------------------------------------------------
# numeric robustness: extreme magnitudes used to raise out of the CLI
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scale", [1e-300, 1e-150, 1.0, 1e150, 1e300])
def test_variance_survives_extreme_scales(scale):
    x = [1.0 * scale, 2.0 * scale, 3.5 * scale, 4.0 * scale]
    v = variance(x)
    assert v == pytest.approx(variance([1.0, 2.0, 3.5, 4.0]) * scale * scale,
                              rel=1e-9)


@pytest.mark.parametrize("scale", [1e-300, 1e-10, 1.0, 1e10, 1e300])
def test_shapiro_wilk_is_scale_invariant(scale):
    """W is scale-free; computing it on raw values overflowed above ~1e155."""
    base = [1.0, 2.0, 3.5, 4.0, 5.5, 6.0, 7.5, 9.0]
    w, p = shapiro_wilk([v * scale for v in base])
    w0, p0 = shapiro_wilk(base)
    assert w == pytest.approx(w0, rel=1e-9)
    assert p == pytest.approx(p0, rel=1e-9)


def test_anova_is_scale_invariant():
    groups = [[1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0], [8.0, 9.0, 7.0, 6.0]]
    ref = one_way_anova(groups)
    for scale in (1e-200, 1e200):
        r = one_way_anova([[v * scale for v in g] for g in groups])
        assert r.statistic == pytest.approx(ref.statistic, rel=1e-9)
        assert r.pvalue == pytest.approx(ref.pvalue, rel=1e-9)


def test_cli_never_tracebacks_on_extreme_values(tmp_path, capsys):
    for content in ("a,b\n1e300,1\n-1e300,2\n1e300,3\n2e300,4\n",
                    "a,b\n1e-300,1e-310\n2e-300,2e-310\n3e-300,3e-310\n"):
        path = _write(tmp_path, "x.csv", content)
        rc = main([path, "--wide"])
        assert rc in (0, 2)              # a result or a clean error, never a crash
        capsys.readouterr()


def test_nan_result_is_flagged_not_reported_as_null(tmp_path, capsys):
    path = _write(tmp_path, "inf.csv",
                  "a,b\n1e308,1\n-1e308,2\n1e308,3\n-1e308,9\n")
    rc = main([path, "--wide"])
    out = capsys.readouterr().out
    if rc == 0:
        assert "해석할 수 없습니다" in out


# --------------------------------------------------------------------------
# performance: two accidentally quadratic loops made real files look like hangs
# --------------------------------------------------------------------------

def test_levene_and_anova_are_linearithmic():
    import random
    random.seed(1)
    a = [random.gauss(10, 2) for _ in range(20000)]
    b = [random.gauss(10.1, 2) for _ in range(20000)]
    # Deterministic proxy for "not quadratic": count how many times the group
    # median/mean is computed. The quadratic version called them once per
    # observation (40,000 times); the fixed version calls them once per group.
    import statwise.tests_stat as ts
    calls = {"n": 0}
    real_mean = ts.mean

    def counting_mean(x):
        calls["n"] += 1
        return real_mean(x)
    ts.mean = counting_mean
    try:
        levene([a, b])
        one_way_anova([a, b])
    finally:
        ts.mean = real_mean
    assert calls["n"] < 100, f"mean() called {calls['n']} times — quadratic"


def test_exact_pmf_is_memoised():
    from statwise.exact import mannwhitney_u_pmf
    mannwhitney_u_pmf.cache_clear()
    mannwhitney_u_pmf(20, 20)
    info = mannwhitney_u_pmf.cache_info()
    mannwhitney_u_pmf(20, 20)
    assert mannwhitney_u_pmf.cache_info().hits == info.hits + 1


def test_exact_pmf_result_is_immutable():
    """A mutable cached null distribution could be corrupted by any caller."""
    from statwise.exact import mannwhitney_u_pmf
    assert isinstance(mannwhitney_u_pmf(4, 4), tuple)


def test_many_groups_posthoc_completes(tmp_path, capsys):
    """Deterministic stand-in for 'this must not take minutes': the exact null
    distribution and the t quantile are both memoised, so a 60-group run does a
    bounded number of expensive evaluations regardless of machine load."""
    from statwise.exact import mannwhitney_u_pmf
    from statwise.special import t_ppf
    rows = ["v,g"]
    for gi in range(60):
        for k in range(12):
            rows.append(f"{gi + k * 0.1:.3f},G{gi}")
    path = _write(tmp_path, "many.csv", "\n".join(rows) + "\n")
    t_ppf.cache_clear()
    mannwhitney_u_pmf.cache_clear()
    assert main([path, "--value", "v", "--group", "g", "--format", "csv"]) == 0
    # 1770 comparisons must not mean 1770 continued-fraction inversions
    assert t_ppf.cache_info().misses <= 20
    capsys.readouterr()


# --------------------------------------------------------------------------
# silently-wrong input handling
# --------------------------------------------------------------------------

def test_newline_inside_a_quoted_cell_is_not_fused_into_a_number(tmp_path,
                                                                 capsys):
    """'1\\n2' used to be read as the number 12."""
    path = _write(tmp_path, "nl.csv", 'a,b\n"1\n2",3\n4,5\n6,7\n')
    assert main([path, "--wide"]) == 0
    out = capsys.readouterr().out
    row = [ln for ln in out.splitlines() if ln.strip().startswith("a ")][0]
    assert "12.000" not in row
    assert row.split()[1] == "2"          # n = 2, the multi-line cell dropped


def test_duplicate_columns_are_rejected(tmp_path, capsys):
    path = _write(tmp_path, "w.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    assert main([path, "--wide", "--columns", "x,x"]) == 2
    assert "여러 번" in capsys.readouterr().err


def test_duplicate_columns_rejected_in_paired_and_binary(tmp_path, capsys):
    path = _write(tmp_path, "p.csv", "pre,post\n5,3\n6,4\n7,5\n8,6\n")
    assert main([path, "--paired", "--wide", "--columns", "pre,pre"]) == 2
    capsys.readouterr()
    b = _write(tmp_path, "b.csv", "A,B\nyes,no\nno,yes\nyes,yes\n")
    assert main([b, "--binary", "--wide", "--columns", "A,A"]) == 2


def test_duplicate_header_names_are_reported(tmp_path, capsys):
    path = _write(tmp_path, "d.csv", "v,g,v\n1,a,9\n2,a,9\n3,b,9\n4,b,9\n")
    assert main([path, "--value", "v", "--group", "g"]) == 0
    assert "고유하게" in capsys.readouterr().out


@pytest.mark.parametrize("delim", [";;", "", "abc"])
def test_bad_delimiter_is_an_input_error(tmp_path, delim, capsys):
    path = _write(tmp_path, "w.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    assert main([path, "--wide", "--delimiter", delim]) == 2
    capsys.readouterr()


@pytest.mark.parametrize("flag", ["--equivalence-margin", "--reference",
                                  "--columns", "--values"])
def test_empty_flag_values_are_refused(tmp_path, flag, capsys):
    path = _write(tmp_path, "w.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    assert main([path, "--wide", flag, ""]) == 2
    assert "빈 값" in capsys.readouterr().err


def test_infinite_ni_margin_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "l.csv",
                  "v,g\n1,a\n2,a\n3,a\n4,a\n5,b\n6,b\n7,b\n8,b\n")
    rc = main([path, "--value", "v", "--group", "g", "--ni-margin", "inf",
               "--reference", "a", "--ni-direction", "higher_is_better"])
    assert rc == 2
    assert "유한한" in capsys.readouterr().err


def test_oversized_csv_field_is_an_input_error(tmp_path, capsys):
    path = tmp_path / "big.csv"
    path.write_text('note,a,b\n"' + "z" * 200000 + '",1,2\nok,3,4\n',
                    encoding="utf-8")
    assert main([str(path), "--wide", "--columns", "a,b"]) == 2
    assert "입력 오류" in capsys.readouterr().err


def test_output_never_overwrites_the_input_even_with_overwrite(tmp_path,
                                                              capsys):
    path = _write(tmp_path, "in.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    rc = main([path, "--wide", "-o", path, "--overwrite"])
    assert rc == 2
    assert "입력 CSV와 같은 파일" in capsys.readouterr().err
    assert open(path, encoding="utf-8").read().startswith("x,y")


# --------------------------------------------------------------------------
# reporting: columns must not fuse, undefined estimates must not carry CIs
# --------------------------------------------------------------------------

def test_descriptives_columns_do_not_fuse_for_large_values(tmp_path, capsys):
    path = _write(tmp_path, "m6.csv",
                  "a,b\n1000000.5,1000006.5\n1000001.5,1000007.5\n"
                  "1000002.5,1000008.5\n1000003.5,1000009.5\n")
    assert main([path, "--wide"]) == 0
    out = capsys.readouterr().out
    row = [ln for ln in out.splitlines() if ln.strip().startswith("a ")][0]
    # every cell must still be a separate whitespace-delimited token
    assert len(row.split()) == 9         # label + n + 7 statistics
    assert "1000002.000" in row


def test_undefined_ratios_carry_no_interval():
    for maker in (risk_ratio, odds_ratio):
        est = maker(BinaryGroup("a", 0, 20), BinaryGroup("b", 0, 20))
        assert est.value != est.value             # NaN
        assert est.ci_low is None and est.ci_high is None


def test_small_n_normal_approximation_is_warned():
    res = analyze([("a", [1.0, 1.0, 1.0, 1.0]), ("b", [2.0, 2.0, 2.0, 2.0])])
    assert res.test_name == "Mann-Whitney U test"
    assert any("정규근사" in w for w in res.warnings)


# --------------------------------------------------------------------------
# special functions at the boundaries
# --------------------------------------------------------------------------

@pytest.mark.parametrize("p", [1e-300, 1e-20, 1e-17, 5e-17, 1e-10])
def test_t_ppf_handles_tiny_probabilities(p):
    """1 - p rounded to 1.0 below ~5.6e-17 and raised ValueError."""
    q = t_ppf(p, 10)
    assert math.isfinite(q) and q < 0
    assert t_sf(q, 10) == pytest.approx(1.0 - p, rel=1e-6)


def test_t_ppf_does_not_saturate_at_the_bracket():
    """df=1 quantiles exceed 1e6; the old fixed bracket silently returned it."""
    q = t_ppf(1 - 5e-8, 1)
    assert q > 6e6
    assert t_sf(q, 1) == pytest.approx(5e-8, rel=1e-6)


def test_t_ppf_symmetry_and_median():
    assert t_ppf(0.5, 30) == 0.0
    for p in (0.001, 0.01, 0.05, 0.2):
        assert t_ppf(p, 12) == pytest.approx(-t_ppf(1 - p, 12), rel=1e-12)


def test_chi2_sf_far_tail_is_not_zero():
    """1 - gammainc_lower destroyed the whole tail below ~1e-16."""
    assert chi2_sf(125.393, 1) == pytest.approx(4.1750573329841757e-29,
                                                rel=1e-9)
    assert chi2_sf(300.0, 100.0) == pytest.approx(7.412100857323e-22, rel=1e-9)


def test_chi2_sf_large_df_series_converges():
    """The 1000-term series cap returned a 16%-wrong tail at df = 1e6."""
    assert chi2_sf(1e6, 1e6) == pytest.approx(0.4998119368033945, rel=1e-6)


# --------------------------------------------------------------------------
# round-3: finite-but-meaningless results, and flags a mode would ignore
# --------------------------------------------------------------------------

def test_infinite_sd_is_refused_not_reported_as_null(tmp_path, capsys):
    """t = diff/inf = -0.0 and p = 1.000 are finite, so a NaN check missed them."""
    path = _write(tmp_path, "e155.csv",
                  "a,b\n1.0e155,2.0e155\n1.1e155,2.1e155\n1.2e155,2.2e155\n"
                  "1.3e155,2.3e155\n1.5e155,2.5e155\n")
    rc = main([path, "--wide"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "배정밀도" in err
    assert "p=1.000" in err          # the message names the failure it prevents


def test_infinite_sd_refused_in_every_continuous_mode(tmp_path, capsys):
    content = ("a,b\n1.0e155,2.0e155\n1.1e155,2.1e155\n1.2e155,2.2e155\n"
               "1.3e155,2.3e155\n1.5e155,2.5e155\n")
    path = _write(tmp_path, "e2.csv", content)
    for extra in ([], ["--test", "welch"], ["--test", "student"],
                  ["--paired", "--wide", "--columns", "a,b"]):
        assert main([path, "--wide"] + extra if "--paired" not in extra
                    else [path] + extra) == 2
        capsys.readouterr()


def test_three_group_extreme_does_not_self_contradict(tmp_path, capsys):
    path = _write(tmp_path, "e3.csv",
                  "a,b,c\n1e300,2e300,3e300\n1.1e300,2.1e300,3.1e300\n"
                  "1.2e300,2.2e300,3.2e300\n1.3e300,2.3e300,3.3e300\n"
                  "1.5e300,2.5e300,3.5e300\n")
    rc = main([path, "--wide"])
    out = capsys.readouterr()
    assert rc == 2
    # never a significant omnibus beside all-nonsignificant post-hocs
    assert "통계적으로 유의함" not in out.out


def test_absurd_event_counts_are_refused(tmp_path, capsys):
    path = _write(tmp_path, "xl.csv", "arm,ev,n\nA,1e300,2e300\nB,10,52\n")
    assert main([path, "--binary", "--events-col", "ev", "--n-col", "n",
                 "--group", "arm"]) == 2
    assert "비현실적" in capsys.readouterr().err


def test_overwrite_still_applies_restrictive_permissions(tmp_path):
    import os
    import stat
    dest = tmp_path / "loose.txt"
    dest.write_text("old", encoding="utf-8")
    os.chmod(dest, 0o666)
    path = _write(tmp_path, "d.csv", "x,y\n1,4\n2,5\n3,6\n4,7\n")
    assert main([path, "--wide", "-o", str(dest), "--overwrite"]) == 0
    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH
                   | stat.S_IWOTH) == 0


def test_huge_group_count_skips_posthoc_with_a_warning(tmp_path, capsys):
    rows = ["v,g"]
    for gi in range(80):
        for k in range(4):
            rows.append(f"{gi + k * 0.1:.2f},G{gi}")
    path = _write(tmp_path, "many.csv", "\n".join(rows) + "\n")
    import time
    start = time.time()
    assert main([path, "--value", "v", "--group", "g"]) == 0
    assert time.time() - start < 10.0
    out = capsys.readouterr().out
    assert "사후검정" not in out.split("[!]")[0] or "생략했습니다" in out


def test_columns_of_only_separators_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "abc.csv", "a,b,c\n1,4,7\n2,5,8\n3,6,9\n4,7,10\n")
    assert main([path, "--wide", "--columns", ",,,"]) == 2
    assert "쉼표만" in capsys.readouterr().err


def test_duplicate_values_names_the_values_flag(tmp_path, capsys):
    rows = ["subject,arm,x,y"]
    for i in range(10):
        rows.append(f"S{i},a,{i},{i * 2}")
        rows.append(f"T{i},b,{i + 4},{i * 2 + 5}")
    path = _write(tmp_path, "m.csv", "\n".join(rows) + "\n")
    assert main([path, "--values", "x,x", "--group", "arm"]) == 2
    assert "--values" in capsys.readouterr().err


def test_reference_group_dropped_for_small_n_is_an_error(tmp_path, capsys):
    rows = ["v,g", "1,X"] + [f"{i},A" for i in range(2, 8)] \
        + [f"{i},B" for i in range(5, 11)]
    path = _write(tmp_path, "rd.csv", "\n".join(rows) + "\n")
    assert main([path, "--value", "v", "--group", "g", "--reference", "X"]) == 2
    assert "기준" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [
    ["--id", "subject"],
    ["--binary-test", "fisher"],
    ["--endpoint-correction", "bh"],
])
def test_flags_without_their_mode_are_refused(tmp_path, extra, capsys):
    path = _write(tmp_path, "l.csv",
                  "v,g\n1,a\n2,a\n3,a\n4,a\n5,b\n6,b\n7,b\n8,b\n")
    assert main([path, "--value", "v", "--group", "g"] + extra) == 2
    capsys.readouterr()


def test_values_rejects_wide_and_columns(tmp_path, capsys):
    rows = ["subject,arm,x,y"]
    for i in range(10):
        rows.append(f"S{i},a,{i},{i * 2}")
        rows.append(f"T{i},b,{i + 4},{i * 2 + 5}")
    path = _write(tmp_path, "m2.csv", "\n".join(rows) + "\n")
    assert main([path, "--values", "x,y", "--group", "arm", "--wide"]) == 2
    capsys.readouterr()


def test_control_characters_in_labels_are_sanitised():
    from statwise.dataio import sanitize_label
    assert "\n" not in sanitize_label("A\nB")
    assert "\x00" not in sanitize_label("A\x00B")
    assert len(sanitize_label("z" * 200)) <= 40


def test_long_and_odd_labels_do_not_break_the_report():
    res = analyze([("x" * 200, [1.0, 2.0, 3.0, 4.0]),
                   ("b\nc", [5.0, 6.0, 7.0, 8.0])])
    from statwise.report import render_text
    text = render_text(res)
    assert "\n" not in res.groups[1].label
    assert len(res.groups[0].label) <= 40
    # table rows must stay aligned; the prose sentence may legitimately be long
    table = [ln for ln in text.split("[2]")[0].splitlines()
             if ln.startswith("    ")]
    assert table and max(len(ln) for ln in table) < 140


def test_rank_test_reason_distinguishes_undetermined_from_rejected():
    res = analyze([("a", [1.0, 2.0]), ("b", [8.0, 9.0, 10.0, 11.0])])
    assert res.test_name == "Mann-Whitney U test"
    assert "undetermined" in res.reason
    assert "normality rejected" not in res.reason


def test_zero_variance_message_is_localised_and_explains_underflow():
    from statwise.tests_stat import students_t
    with pytest.raises(ValueError) as exc:
        students_t([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert "단위" in str(exc.value)


def test_zero_cell_risk_ratio_explains_a_point_outside_its_interval():
    from statwise.binary import risk_ratio
    est = risk_ratio(BinaryGroup("a", 0, 3), BinaryGroup("b", 3, 3))
    assert est.ci_low is not None
    if not (est.ci_low <= est.value <= est.ci_high):
        assert "포함하지 않습니다" in est.note
