"""Tests for the 3+-rater statistics (agreestat.multirater)."""

import json
import math

import pytest

from agreestat.multirater import (
    fleiss_kappa,
    fleiss_per_category,
    gwet_ac1_multi,
    icc_family,
    krippendorff_alpha_multi,
    multi_categorical,
    multi_continuous,
    pairwise_kappa,
)
from agreestat.multireport import (
    render_multi_json,
    render_multi_markdown,
    render_multi_text,
    render_multicat_json,
    render_multicat_markdown,
    render_multicat_text,
)

# Shrout & Fleiss (1979) Table 1 — the canonical worked example.
SF_TABLE = [
    [9, 2, 5, 8],
    [6, 1, 3, 2],
    [8, 4, 6, 8],
    [7, 1, 2, 6],
    [10, 5, 6, 9],
    [6, 2, 4, 7],
]

GRADES = [
    ["mild", "mild", "moderate"],
    ["severe", "severe", "severe"],
    ["mild", "moderate", "mild"],
    ["moderate", "moderate", "moderate"],
    ["severe", "moderate", "severe"],
    ["mild", "mild", "mild"],
    ["moderate", "severe", "moderate"],
    ["severe", "severe", "moderate"],
]
CATS = ["mild", "moderate", "severe"]


def _counts(rows, cats=CATS):
    return [[r.count(c) for c in cats] for r in rows]


# --------------------------------------------------------------------------
# ICC family
# --------------------------------------------------------------------------
def test_icc_family_matches_shrout_fleiss_table():
    """Published values: ICC(1,1)=.17, ICC(2,1)=.29, ICC(3,1)=.71."""
    fam = icc_family(SF_TABLE)
    icc11, icc21, icc31 = fam.single
    assert icc11.value == pytest.approx(0.1657, abs=5e-4)
    assert icc21.value == pytest.approx(0.2898, abs=5e-4)
    assert icc31.value == pytest.approx(0.7148, abs=5e-4)
    icc1k, icc2k, icc3k = fam.average
    assert icc1k.value == pytest.approx(0.4428, abs=5e-4)
    assert icc2k.value == pytest.approx(0.6200, abs=5e-4)
    assert icc3k.value == pytest.approx(0.9093, abs=5e-4)


def test_icc_average_is_spearman_brown_of_single():
    fam = icc_family(SF_TABLE)
    k = fam.k
    for single, avg in zip(fam.single, fam.average):
        expect = k * single.value / (1.0 + (k - 1) * single.value)
        assert avg.value == pytest.approx(expect, rel=1e-12)
        assert avg.model.endswith(f",{k})")


def test_icc_family_ci_brackets_point_estimate():
    fam = icc_family(SF_TABLE)
    for r in list(fam.single) + list(fam.average):
        assert r.ci_lower <= r.value + 1e-9 <= r.ci_upper + 2e-9
        assert r.ci_upper <= 1.0 + 1e-12


def test_icc_mean_squares_reproduce_by_hand():
    """MSR/MSC/MSE/MSW recomputed from first principles."""
    fam = icc_family(SF_TABLE)
    n, k = len(SF_TABLE), len(SF_TABLE[0])
    grand = sum(sum(r) for r in SF_TABLE) / (n * k)
    row_m = [sum(r) / k for r in SF_TABLE]
    col_m = [sum(r[j] for r in SF_TABLE) / n for j in range(k)]
    ssr = k * sum((m - grand) ** 2 for m in row_m)
    ssc = n * sum((m - grand) ** 2 for m in col_m)
    sst = sum((v - grand) ** 2 for r in SF_TABLE for v in r)
    sse = sst - ssr - ssc
    ssw = sum((v - row_m[i]) ** 2 for i, r in enumerate(SF_TABLE) for v in r)
    assert fam.ms.msr == pytest.approx(ssr / (n - 1))
    assert fam.ms.msc == pytest.approx(ssc / (k - 1))
    assert fam.ms.mse == pytest.approx(sse / ((n - 1) * (k - 1)))
    assert fam.msw == pytest.approx(ssw / (n * (k - 1)))


def test_rater_effect_f_detects_systematic_bias():
    """A constant +5 offset on one rater must show up as a rater effect."""
    rows = [[float(i), float(i) + 0.1, float(i) + 5.0] for i in range(12)]
    fam = icc_family(rows)
    assert fam.rater_p < 0.001
    # ICC(3,1) (consistency) stays high while ICC(2,1) (absolute) drops.
    assert fam.single[2].value > fam.single[1].value


def test_sem_is_the_absolute_agreement_one():
    """SEM must come from MSW (absolute agreement), not MSE (consistency)."""
    fam = icc_family(SF_TABLE)
    assert fam.sem == pytest.approx(math.sqrt(fam.msw))
    assert fam.sem_consistency == pytest.approx(math.sqrt(fam.ms.mse))
    assert fam.mdc95 == pytest.approx(1.959963984540054 * math.sqrt(2) * fam.sem)
    assert fam.rc == pytest.approx(2.77 * fam.sw)
    # MSW = MSE + (MSC - MSE)/n, so SEM >= SEM_consistency always.
    assert fam.msw == pytest.approx(
        fam.ms.mse + (fam.ms.msc - fam.ms.mse) / fam.n)
    assert fam.sem >= fam.sem_consistency


def test_sem_reflects_a_constant_rater_offset():
    """The bug this guards: sqrt(MSE) reports MDC95=0 for raters 10 units apart."""
    rows = [[float(i) + 0.3, float(i) - 0.3, float(i) + 10.0] for i in range(12)]
    fam = icc_family(rows)
    assert fam.ms.mse == pytest.approx(0.0, abs=1e-9)
    assert fam.sem > 5.0 and fam.mdc95 > 15.0


def test_perfect_agreement_gives_icc_one():
    rows = [[float(i)] * 3 for i in range(8)]
    fam = icc_family(rows)
    for r in fam.single:
        assert r.value == pytest.approx(1.0)


# --------------------------------------------------------------------------
# multi_continuous orchestration
# --------------------------------------------------------------------------
def test_multi_continuous_pairwise_and_descriptives():
    names = ["r1", "r2", "r3", "r4"]
    res = multi_continuous(names, SF_TABLE)
    assert res.n == 6 and res.k == 4
    assert len(res.pairwise) == 6            # C(4,2)
    # rater bias must sum to (approximately) zero across raters
    assert sum(d.bias for d in res.descriptives) == pytest.approx(0.0, abs=1e-12)
    pw = res.pairwise[0]
    a = [r[0] for r in SF_TABLE]
    b = [r[1] for r in SF_TABLE]
    d = [x - y for x, y in zip(a, b)]
    mean_d = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean_d) ** 2 for x in d) / (len(d) - 1))
    assert pw.mean_diff == pytest.approx(mean_d)
    assert pw.sd_diff == pytest.approx(sd)
    assert pw.loa_lower == pytest.approx(mean_d - 1.959963984540054 * sd, rel=1e-9)


def test_multi_continuous_requires_three_raters():
    with pytest.raises(ValueError, match="at least 3"):
        multi_continuous(["a", "b"], [[1.0, 2.0], [3.0, 4.0]])


def test_multi_continuous_rejects_ragged_and_nonfinite():
    with pytest.raises(ValueError):
        multi_continuous(["a", "b", "c"], [[1.0, 2.0, 3.0], [1.0, 2.0]])
    with pytest.raises(ValueError, match="non-finite"):
        multi_continuous(["a", "b", "c"],
                         [[1.0, 2.0, float("nan")], [1.0, 2.0, 3.0]])


def test_multi_continuous_small_sample_warning():
    res = multi_continuous(["a", "b", "c"], [[1.0, 2.0, 3.0], [4.0, 5.0, 7.0],
                                             [2.0, 1.0, 3.0]])
    assert any("피험자 수가 적습니다" in w for w in res.warnings)


def test_multi_continuous_renderers_run():
    res = multi_continuous(["r1", "r2", "r3", "r4"], SF_TABLE)
    txt = render_multi_text(res)
    assert "ICC(2,1)" in txt and "MDC95" in txt
    # section numbers must be consecutive
    nums = [int(line[1]) for line in txt.splitlines()
            if line.startswith("[") and line[1].isdigit()]
    assert nums == list(range(1, len(nums) + 1))
    data = json.loads(render_multi_json(res))
    assert data["analysis"] == "multi_rater_continuous"
    assert len(data["icc"]["single"]) == 3
    assert len(data["pairwise"]) == 6
    assert "| ICC(1,1) |" in render_multi_markdown(res)


# --------------------------------------------------------------------------
# Fleiss' kappa
# --------------------------------------------------------------------------
def test_fleiss_kappa_components_are_probabilities():
    """Structural invariants only — published values live in the reference file."""
    counts = _counts(GRADES)
    kap, p_bar, pe, _se, m, p_j = fleiss_kappa(counts)
    assert m == 3.0
    assert sum(p_j) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in p_j)
    assert 0.0 <= p_bar <= 1.0 and 0.0 <= pe <= 1.0
    assert -1.0 <= kap <= 1.0
    # kappa must sit strictly between the observed and chance agreement scales
    assert (kap > 0) == (p_bar > pe)


def test_fleiss_kappa_perfect_and_zero():
    perfect = [[3, 0, 0], [0, 3, 0], [0, 0, 3], [3, 0, 0]]
    assert fleiss_kappa(perfect)[0] == pytest.approx(1.0)
    # Every subject split 1/1/1 -> observed agreement 0, kappa negative.
    split = [[1, 1, 1]] * 6
    assert fleiss_kappa(split)[0] < 0


def test_fleiss_kappa_requires_balanced_raters():
    with pytest.raises(ValueError, match="same number of raters"):
        fleiss_kappa([[3, 0], [2, 0]])
    with pytest.raises(ValueError, match="at least 2 raters"):
        fleiss_kappa([[1, 0], [0, 1]])


def test_fleiss_per_category_sums_sensibly():
    counts = _counts(GRADES)
    entries = fleiss_per_category(counts, CATS)
    assert [e.category for e in entries] == CATS
    assert sum(e.proportion for e in entries) == pytest.approx(1.0)
    # category-specific kappa recomputed from the definition
    n, m = len(counts), 3
    for j, e in enumerate(entries):
        pj = sum(c[j] for c in counts) / (n * m)
        num = sum(c[j] * (m - c[j]) for c in counts)
        assert e.kappa == pytest.approx(1 - num / (n * m * (m - 1) * pj * (1 - pj)))


# --------------------------------------------------------------------------
# Gwet AC1 / Krippendorff alpha
# --------------------------------------------------------------------------
def test_gwet_ac1_shares_observed_agreement_with_fleiss():
    """AC1 and Fleiss differ only in the chance term, never in p_a."""
    counts = _counts(GRADES)
    ac1, pa, pe = gwet_ac1_multi(counts)
    assert pa == pytest.approx(fleiss_kappa(counts)[1])   # same P_bar
    assert 0.0 <= pe <= 1.0
    assert ac1 == pytest.approx((pa - pe) / (1 - pe))
    # Gwet's chance term is bounded above by 1/2 for any category count >= 2
    assert pe <= 0.5 + 1e-12


def test_gwet_ac1_beats_kappa_under_extreme_prevalence():
    """The classic paradox: near-total agreement on one category."""
    counts = [[3, 0]] * 48 + [[2, 1], [1, 2]]
    kap = fleiss_kappa(counts)[0]
    ac1 = gwet_ac1_multi(counts)[0]
    assert ac1 > kap


def test_krippendorff_alpha_ordinal_exceeds_nominal_here():
    counts = _counts(GRADES)
    nom = krippendorff_alpha_multi(counts, CATS, "nominal")
    ordi = krippendorff_alpha_multi(counts, CATS, "ordinal")
    assert -1.0 <= nom <= 1.0
    assert ordi > nom          # disagreements are all adjacent grades


def test_krippendorff_alpha_uses_partially_rated_units():
    """A unit rated by 2 of 3 raters still contributes; a 1-rating unit does not."""
    full = [[2, 0, 0], [0, 2, 0], [2, 0, 0], [0, 0, 2]]
    with_singleton = full + [[1, 0, 0]]
    assert krippendorff_alpha_multi(full, CATS) == pytest.approx(
        krippendorff_alpha_multi(with_singleton, CATS))


def test_krippendorff_alpha_perfect_is_one():
    counts = [[3, 0, 0], [0, 3, 0], [0, 0, 3], [3, 0, 0], [0, 3, 0]]
    assert krippendorff_alpha_multi(counts, CATS) == pytest.approx(1.0)


def test_pairwise_kappa_skips_missing_cells():
    rows = [["a", "a", ""], ["b", "b", "b"], ["a", "b", "a"], ["b", "a", "b"]]
    pw = pairwise_kappa(["r1", "r2", "r3"], rows, ["a", "b"])
    assert len(pw) == 3
    by_pair = {(a, b): (v, n) for a, b, v, n in pw}
    assert by_pair[("r1", "r3")][1] == 3     # one cell missing
    assert by_pair[("r1", "r2")][1] == 4


# --------------------------------------------------------------------------
# multi_categorical orchestration
# --------------------------------------------------------------------------
def test_multi_categorical_end_to_end():
    res = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=300)
    assert res.n == 8 and res.m == 3 and res.n_alpha == 8
    # Fleiss kappa recomputed from the confusion of the three rater pairs is
    # bounded by the smallest and largest pairwise Scott's pi.
    assert res.fleiss_ci[0] <= res.fleiss <= res.fleiss_ci[1]
    assert len(res.per_category) == 3
    assert len(res.pairwise) == 3
    assert res.light_kappa == pytest.approx(
        sum(v for _a, _b, v, _n in res.pairwise) / 3)
    assert sum(res.category_counts) == 24
    # Hand count of agreeing rater pairs: 1+3+1+3+1+3+1+1 = 14 of 24.
    assert res.percent_agreement == pytest.approx(14 / 24)


def test_multi_categorical_bootstrap_is_reproducible():
    a = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=300, seed=7)
    b = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=300, seed=7)
    c = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=300, seed=8)
    assert a.fleiss_ci == b.fleiss_ci
    assert a.fleiss_ci != c.fleiss_ci


def test_multi_categorical_incomplete_rows_are_reported():
    rows = GRADES + [["mild", "", "mild"]]
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=200)
    assert res.n == 8                     # Fleiss uses complete cases only
    assert res.n_alpha == 9               # alpha uses the 2-rating unit too
    assert any("일부만 평가한" in w for w in res.warnings)


def test_multi_categorical_threshold_uses_ci_lower():
    res = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=400,
                            min_kappa=0.9)
    assert res.meets_threshold is False
    res2 = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=400,
                             min_kappa=-1.0)
    assert res2.meets_threshold is True


def test_multi_categorical_rejects_unknown_label():
    with pytest.raises(ValueError, match="범주 목록"):
        multi_categorical(["A", "B", "C"], [["x", "y", "z"]] * 3, CATS)


def test_multi_categorical_requires_three_raters():
    with pytest.raises(ValueError, match="at least 3"):
        multi_categorical(["A", "B"], [["mild", "mild"]] * 3, CATS)


def test_multi_categorical_needs_two_complete_subjects():
    rows = [["mild", "mild", "mild"], ["mild", "", ""], ["", "", "severe"]]
    with pytest.raises(ValueError, match="Fleiss"):
        multi_categorical(["A", "B", "C"], rows, CATS)


def test_multi_categorical_paradox_warning():
    rows = [["neg", "neg", "neg"]] * 45 + [["neg", "neg", "pos"]] * 5
    res = multi_categorical(["A", "B", "C"], rows, ["neg", "pos"],
                            bootstrap=200)
    assert any("역설" in w for w in res.warnings)


def test_multi_categorical_renderers_run():
    res = multi_categorical(["A", "B", "C"], GRADES, CATS, bootstrap=200,
                            min_kappa=0.4)
    txt = render_multicat_text(res)
    assert "Fleiss' kappa" in txt and "Krippendorff" in txt
    nums = [int(line[1]) for line in txt.splitlines()
            if line.startswith("[") and line[1].isdigit()]
    assert nums == list(range(1, len(nums) + 1))
    data = json.loads(render_multicat_json(res))
    assert data["analysis"] == "multi_rater_categorical"
    assert data["ci_method"]["resamples"] == 200
    assert data["threshold"]["min_kappa"] == 0.4
    assert "Fleiss' kappa" in render_multicat_markdown(res)


def test_bootstrap_work_cap_reduces_resamples():
    """Many categories make each resample expensive -> B is scaled down."""
    cats = [f"C{i}" for i in range(60)]
    rows = [[cats[i % 60], cats[(i + 1) % 60], cats[(i + 2) % 60]]
            for i in range(100)]
    res = multi_categorical(["A", "B", "C"], rows, cats, bootstrap=50_000)
    assert res.bootstrap < 50_000
    assert any("부트스트랩 반복" in w for w in res.warnings)


def test_large_input_still_produces_every_ci():
    """Pattern grouping keeps the bootstrap cheap on repetitive large inputs."""
    rows = [["mild", "mild", "moderate"]] * 1500 + [["severe"] * 3] * 1500
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=2000)
    assert math.isfinite(res.fleiss_ci[0])
    assert math.isfinite(res.ac1_ci[0])
    assert math.isfinite(res.kalpha_ci[0])


def test_unanimous_data_uses_one_consistent_convention():
    """All three coefficients agree that unanimity is perfect agreement."""
    rows = [["mild", "mild", "mild"]] * 30
    res = multi_categorical(["A", "B", "C"], rows, CATS, bootstrap=200)
    assert res.fleiss == pytest.approx(1.0)
    assert res.ac1 == pytest.approx(1.0)
    assert res.kalpha == pytest.approx(1.0)
    assert res.fleiss_ci == pytest.approx((1.0, 1.0))
    assert res.ac1_ci == pytest.approx((1.0, 1.0))


def test_single_observed_category_is_refused():
    """One category means no chance-corrected coefficient is defined."""
    rows = [["neg", "neg", "neg"]] * 8
    with pytest.raises(ValueError, match="1종"):
        multi_categorical(["A", "B", "C"], rows, ["neg"], bootstrap=200)


def test_pattern_grouped_bootstrap_matches_naive_recomputation():
    """The fast (pattern-grouped) resampler must reproduce the naive one."""
    import random as _random

    from agreestat.multirater import _bootstrap_cis, _quantile

    counts = _counts(GRADES)
    fast = _bootstrap_cis(counts, CATS, "nominal", 300, 4242, 0.05, 3)

    rng = _random.Random(4242)
    n = len(counts)
    fk, ac, ka = [], [], []
    for _ in range(300):
        sample = [counts[rng.randrange(n)] for _ in range(n)]
        v = fleiss_kappa(sample)[0]
        if math.isfinite(v):
            fk.append(v)
        v = gwet_ac1_multi(sample)[0]
        if math.isfinite(v):
            ac.append(v)
        v = krippendorff_alpha_multi(sample, CATS, "nominal")
        if math.isfinite(v):
            ka.append(v)

    def pct(vals):
        vals = sorted(vals)
        return (_quantile(vals, 0.025), _quantile(vals, 0.975))

    for got, want in zip(fast[:3], (pct(fk), pct(ac), pct(ka))):
        assert got[0] == pytest.approx(want[0], abs=1e-12)
        assert got[1] == pytest.approx(want[1], abs=1e-12)
    assert fast[3] == 300
