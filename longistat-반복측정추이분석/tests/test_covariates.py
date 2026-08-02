"""Subject-level covariate adjustment: encoding, loading, ANCOVA, MMRM, CLI.

The backbone here mirrors ``tests/test_mmrm.py``: rather than trusting the
adjusted numbers, they are pinned to answers that hold by construction.

* the encoded block is checked against hand-computed centred columns;
* ANCOVA with covariates is checked against an **independent** normal-equations
  solve written out in this file (no shared code with ``ancova.solve_ols``);
* complete data + ≥2 arms → the MMRM per-visit contrast **is** the covariate
  ANCOVA contrast, standard error and residual df included.  That identity is
  what pins the degrees-of-freedom bookkeeping for the extra columns.
"""

from __future__ import annotations

import math
import random

import pytest

from longistat.ancova import ancova_analysis
from longistat.cli import main
from longistat.covariates import (MAX_COLUMNS, Covariate, complete_subjects,
                                  encode_covariates)
from longistat.dataio import DataError, Panel, load_long, load_wide
from longistat.mmrm import mmrm_analysis


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _panel(values, groups=None, covariates=None, times=None):
    n_t = len(values[0])
    return Panel(subjects=[f"s{i}" for i in range(len(values))],
                 times=times or [f"V{j}" for j in range(n_t)],
                 values=[list(r) for r in values], groups=groups,
                 group_name="군" if groups else None, value_name="점수",
                 covariates=list(covariates or []))


def _num(name, vals):
    return Covariate(name=name, values=[None if v is None else str(v) for v in vals],
                     categorical=False,
                     numeric=[None if v is None else float(v) for v in vals])


def _cat(name, vals):
    return Covariate(name=name, values=list(vals), categorical=True,
                     numeric=[None] * len(vals))


def _ols(x, y):
    """Independent least squares: normal equations by Gaussian elimination.

    Deliberately not ``ancova.solve_ols`` — a shared bug would cancel out.
    Returns ``(beta, xtx_inv, sigma2, df)``.
    """
    n, p = len(y), len(x[0])
    xtx = [[sum(x[i][a] * x[i][b] for i in range(n)) for b in range(p)]
           for a in range(p)]
    xty = [sum(x[i][a] * y[i] for i in range(n)) for a in range(p)]
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(p)] + [xty[i]]
           for i, row in enumerate(xtx)]
    for col in range(p):
        piv = max(range(col, p), key=lambda r: abs(aug[r][col]))
        aug[col], aug[piv] = aug[piv], aug[col]
        d = aug[col][col]
        aug[col] = [v / d for v in aug[col]]
        for r in range(p):
            if r != col:
                f = aug[r][col]
                aug[r] = [v - f * w for v, w in zip(aug[r], aug[col])]
    beta = [aug[i][2 * p] for i in range(p)]
    inv = [[aug[i][p + j] for j in range(p)] for i in range(p)]
    resid = [y[i] - sum(x[i][a] * beta[a] for a in range(p)) for i in range(n)]
    df = n - p
    return beta, inv, sum(r * r for r in resid) / df, df


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------

def test_continuous_covariate_is_centred_on_the_subjects_in_the_fit():
    cov = _num("나이", [30, 40, 50, 200])
    d = encode_covariates([cov], [0, 1, 2])       # subject 3 is not in the fit
    assert d.names == ["나이"]
    assert [r[0] for r in d.columns] == pytest.approx([-10.0, 0.0, 10.0])


def test_categorical_covariate_gets_reference_coding_minus_the_mean():
    cov = _cat("성별", ["남", "여", "여", "남"])
    d = encode_covariates([cov], [0, 1, 2, 3])
    assert d.names == ["성별=여"]                  # 남 is the reference
    assert [r[0] for r in d.columns] == pytest.approx([-0.5, 0.5, 0.5, -0.5])


def test_three_level_factor_costs_two_columns():
    cov = _cat("기관", ["A", "B", "C", "A", "B", "C"])
    d = encode_covariates([cov], list(range(6)))
    assert d.names == ["기관=B", "기관=C"]
    assert d.n_columns == 2


def test_reference_level_does_not_flip_when_the_first_subject_drops_out():
    """ANCOVA refits per visit; a per-fit reference made the tables disagree."""
    cov = _cat("기관", ["A", "B", "A", "B", "C"])
    full = encode_covariates([cov], [0, 1, 2, 3, 4])
    without_first = encode_covariates([cov], [1, 2, 3, 4])
    assert full.names == ["기관=B", "기관=C"]
    assert without_first.names == full.names      # still coded against A


def test_a_level_nobody_in_the_fit_belongs_to_is_dropped_not_zero_coded():
    cov = _cat("기관", ["A", "B", "C", "A"])
    d = encode_covariates([cov], [0, 1, 3])        # nobody is at site C
    assert d.names == ["기관=B"]


def test_constant_covariate_is_dropped_with_a_reason():
    d = encode_covariates([_num("나이", [50, 50, 50, 50])], list(range(4)))
    assert d.names == []
    assert d.dropped and "값이 모두 같아" in d.dropped[0]


def test_collinear_covariate_is_dropped_with_a_reason():
    a = _num("a", [1, 2, 3, 4])
    b = _num("b", [3, 5, 7, 9])                    # b = 2a + 1
    d = encode_covariates([a, b], list(range(4)))
    assert d.names == ["a"]
    assert any("공선성" in m for m in d.dropped)


def test_single_level_factor_is_dropped_with_the_level_named():
    d = encode_covariates([_cat("기관", ["A", "A", "A"])], [0, 1, 2])
    assert d.names == []
    assert "'A'" in d.dropped[0]


def test_too_many_covariate_columns_is_refused():
    rng = random.Random(99)
    covs = [_cat(f"c{k}", [rng.choice("abcdef") for _ in range(60)])
            for k in range(5)]      # 5 factors × 5 dummy columns = 25 > 20
    with pytest.raises(ValueError) as exc:
        encode_covariates(covs, list(range(30)))
    assert str(MAX_COLUMNS) in str(exc.value)


def test_complete_subjects_counts_the_ones_it_drops():
    keep, n = complete_subjects([_num("나이", [1, None, 3])], [0, 1, 2])
    assert keep == [0, 2] and n == 1


def test_encode_with_no_covariates_or_no_rows_is_empty_not_an_error():
    assert encode_covariates([], [0, 1]).n_columns == 0
    assert encode_covariates([_num("a", [1, 2])], []).n_columns == 0


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

LONG_CSV = """대상,군,방문,점수,나이,성별,기관
P1,A,기저,20,30,남,1
P1,A,4주,15,30,남,1
P2,B,기저,22,40,여,2
P2,B,4주,21,40,여,2
P3,A,기저,25,50,여,1
P3,A,4주,18,50,여,1
"""


def test_long_loader_reads_subject_level_covariates(tmp_path):
    notes = []
    p = load_long(_write(tmp_path, "a.csv", LONG_CSV), "대상", "방문", "점수",
                  group_col="군", covariate_cols=["나이", "성별"], notes=notes)
    assert [c.name for c in p.covariates] == ["나이", "성별"]
    age, sex = p.covariates
    assert age.numeric == [30.0, 40.0, 50.0] and not age.categorical
    assert sex.categorical and sex.values == ["남", "여", "여"]
    assert any("연속형으로 사용" in n for n in notes)
    assert any("범주형으로 사용" in n for n in notes)


def test_numeric_looking_factor_can_be_forced_categorical(tmp_path):
    path = _write(tmp_path, "a.csv", LONG_CSV)
    auto = load_long(path, "대상", "방문", "점수", covariate_cols=["기관"])
    forced = load_long(path, "대상", "방문", "점수", covariate_cols=["기관"],
                       categorical_cols=["기관"])
    assert not auto.covariates[0].categorical      # 1/2 parses as a number
    assert forced.covariates[0].categorical


def test_covariate_that_changes_within_a_subject_is_refused(tmp_path):
    bad = LONG_CSV.replace("P1,A,4주,15,30,남,1", "P1,A,4주,15,31,남,1")
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "b.csv", bad), "대상", "방문", "점수",
                  covariate_cols=["나이"])
    assert "나이" in str(exc.value) and "대상마다 값이 하나" in str(exc.value)


def test_covariate_may_not_reuse_the_id_or_value_column(tmp_path):
    path = _write(tmp_path, "a.csv", LONG_CSV)
    with pytest.raises(DataError) as exc:
        load_long(path, "대상", "방문", "점수", covariate_cols=["대상"])
    assert "이미" in str(exc.value)


def test_unknown_covariate_column_names_the_column(tmp_path):
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "a.csv", LONG_CSV), "대상", "방문", "점수",
                  covariate_cols=["체중"])
    assert "체중" in str(exc.value)


def test_missing_covariate_cells_are_kept_as_none_and_counted(tmp_path):
    csv_text = LONG_CSV.replace("P2,B,기저,22,40,여,2", "P2,B,기저,22,,여,2") \
                       .replace("P2,B,4주,21,40,여,2", "P2,B,4주,21,,여,2")
    notes = []
    p = load_long(_write(tmp_path, "c.csv", csv_text), "대상", "방문", "점수",
                  covariate_cols=["나이"], notes=notes)
    assert p.covariates[0].numeric == [30.0, None, 50.0]
    assert any("1명" in n and "제외" in n for n in notes)


def test_all_missing_covariate_column_is_refused(tmp_path):
    csv_text = "\n".join(
        ",".join(part if k != 4 else "" for k, part in enumerate(line.split(",")))
        if i else line
        for i, line in enumerate(LONG_CSV.strip().split("\n")))
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "d.csv", csv_text + "\n"), "대상", "방문",
                  "점수", covariate_cols=["나이"])
    assert "값이 하나도 없습니다" in str(exc.value)


WIDE_CSV = """환자,군,기저,4주,나이,성별
W1,A,20,15,30,남
W2,B,22,21,40,여
W3,A,25,18,50,여
"""


def test_wide_loader_reads_covariates(tmp_path):
    p = load_wide(_write(tmp_path, "w.csv", WIDE_CSV), ["기저", "4주"],
                  id_col="환자", group_col="군", covariate_cols=["나이", "성별"])
    assert [c.name for c in p.covariates] == ["나이", "성별"]
    assert p.covariates[0].numeric == [30.0, 40.0, 50.0]


def test_wide_covariate_may_not_be_a_timepoint_column(tmp_path):
    with pytest.raises(DataError) as exc:
        load_wide(_write(tmp_path, "w.csv", WIDE_CSV), ["기저", "4주"],
                  id_col="환자", covariate_cols=["4주"])
    assert "이미" in str(exc.value)


def test_wide_duplicate_row_with_a_different_covariate_is_refused(tmp_path):
    text = WIDE_CSV + "W1,A,20,15,31,남\n"
    with pytest.raises(DataError) as exc:
        load_wide(_write(tmp_path, "w2.csv", text), ["기저", "4주"],
                  id_col="환자", group_col="군", covariate_cols=["나이"],
                  duplicates="mean")
    assert "나이" in str(exc.value)


def test_covariates_survive_subsetting_the_panel(tmp_path):
    p = load_long(_write(tmp_path, "a.csv", LONG_CSV), "대상", "방문", "점수",
                  group_col="군", covariate_cols=["나이"])
    sub = p.subset_times([0, 1])
    assert sub.covariates[0].numeric == [30.0, 40.0, 50.0]


# --------------------------------------------------------------------------
# ANCOVA with covariates — against an independent OLS
# --------------------------------------------------------------------------

def _cov_panel(rng, n=40, n_times=3, missing=0.0):
    groups, rows, ages, sites = [], [], [], []
    for i in range(n):
        arm = "A" if i % 2 else "B"
        age = rng.randint(25, 70)
        site = "S1" if i % 3 == 0 else ("S2" if i % 3 == 1 else "S3")
        base = rng.gauss(20, 4) + 0.08 * age
        row = [base]
        for j in range(1, n_times):
            eff = (-3.0 if arm == "A" else -1.0) * j
            row.append(base + eff + 0.02 * age + rng.gauss(0, 2.0))
        for j in range(1, n_times):
            if missing and rng.random() < missing:
                for k in range(j, n_times):
                    row[k] = None
                break
        groups.append(arm)
        rows.append(row)
        ages.append(age)
        sites.append(site)
    covs = [_num("나이", ages), _cat("기관", sites)]
    return _panel(rows, groups, covs)


def test_ancova_with_covariates_matches_an_independent_ols_fit():
    p = _cov_panel(random.Random(11), n=48)
    res = ancova_analysis(p, 0)
    assert res is not None
    assert res.covariates == ["나이", "기관=S2", "기관=S3"]

    n = p.n_subjects
    age = p.covariates[0].numeric
    site = p.covariates[1].values
    age_mean = sum(age) / n
    for lev in ("S2", "S3"):
        pass
    for j in (1, 2):
        want_time = p.times[j]
        con = [c for c in res.contrasts if c.time == want_time][0]
        # arm code +1 for the first present arm (A appears first at index 1),
        # -1 for the last: exactly the sum-to-zero coding ancova uses.
        arms = []
        for i in range(n):
            if p.groups[i] not in arms:
                arms.append(p.groups[i])
        x, y = [], []
        s2 = [1.0 if s == "S2" else 0.0 for s in site]
        s3 = [1.0 if s == "S3" else 0.0 for s in site]
        m2, m3 = sum(s2) / n, sum(s3) / n
        for i in range(n):
            code = 1.0 if p.groups[i] == arms[0] else -1.0
            x.append([1.0, code, p.values[i][0], age[i] - age_mean,
                      s2[i] - m2, s3[i] - m3])
            y.append(p.values[i][j])
        beta, inv, sigma2, df = _ols(x, y)
        est = 2.0 * beta[1]                     # arms[0] − arms[1]
        se = math.sqrt(4.0 * inv[1][1] * sigma2)
        assert con.adjusted_diff == pytest.approx(est, abs=1e-9)
        assert con.df == pytest.approx(df, abs=1e-9)
        assert con.t == pytest.approx(est / se, rel=1e-9)
        assert con.slope == pytest.approx(beta[2], abs=1e-9)


def test_covariate_adjustment_changes_the_estimate_and_narrows_nothing_silently():
    """A covariate must actually enter the model — not be accepted and ignored."""
    p = _cov_panel(random.Random(3), n=44)
    plain = ancova_analysis(p, 0, covariates=[])
    adj = ancova_analysis(p, 0)
    assert plain.covariates == []
    a = [c for c in plain.contrasts if c.time == "V1"][0]
    b = [c for c in adj.contrasts if c.time == "V1"][0]
    assert a.adjusted_diff != pytest.approx(b.adjusted_diff, abs=1e-9)
    assert b.df == a.df - 3                     # 나이 + 2 site columns


def test_ancova_drops_subjects_with_a_missing_covariate_and_says_so():
    p = _cov_panel(random.Random(4), n=40)
    p.covariates[0].numeric[0] = None
    p.covariates[0].values[0] = None
    res = ancova_analysis(p, 0)
    con = [c for c in res.contrasts if c.time == "V1"][0]
    assert con.n_a + con.n_b == 39
    assert any("공변량 값이 없는 대상" in n for n in res.notes)


def test_ancova_notes_name_the_covariates_that_were_used():
    res = ancova_analysis(_cov_panel(random.Random(5), n=40), 0)
    assert any("보정 공변량: 기저값 + 나이" in n for n in res.notes)


def test_ancova_still_runs_when_every_covariate_is_dropped():
    p = _cov_panel(random.Random(6), n=30)
    p.covariates = [_num("상수", [7.0] * p.n_subjects)]
    res = ancova_analysis(p, 0)
    assert res is not None and res.covariates == []
    assert any("값이 모두 같아" in n for n in res.notes)


# --------------------------------------------------------------------------
# MMRM with covariates
# --------------------------------------------------------------------------

def test_mmrm_with_covariates_reproduces_the_covariate_ancova_on_complete_data():
    p = _cov_panel(random.Random(21), n=48, n_times=4)
    res = mmrm_analysis(p, 0)
    anc = ancova_analysis(p, 0)
    assert res is not None and res.converged
    assert res.covariates == ["나이", "기관=S2", "기관=S3"]
    assert len(res.contrasts) == len(anc.contrasts) == 3
    for c in res.contrasts:
        m = [x for x in anc.contrasts if x.time == c.time][0]
        assert c.estimate == pytest.approx(m.adjusted_diff, abs=1e-8)
        assert c.df == pytest.approx(m.df, abs=1e-9)
        assert c.se == pytest.approx(
            abs(m.adjusted_diff / m.t) if m.t else float("nan"), rel=1e-7)
        assert c.p_raw == pytest.approx(m.p_raw, rel=1e-7)


def test_mmrm_covariate_columns_cost_degrees_of_freedom():
    p = _cov_panel(random.Random(22), n=40, n_times=3)
    with_cov = mmrm_analysis(p, 0)
    p2 = _panel([list(r) for r in p.values], list(p.groups))
    without = mmrm_analysis(p2, 0)
    for a, b in zip(with_cov.contrasts, without.contrasts):
        assert a.df == b.df - 3


def test_mmrm_lsmeans_are_at_the_mean_covariate_value():
    """Centred main effects ⇒ the LS-mean equals the covariate-free cell mean
    of the residualised response, so the arm difference is unchanged by a
    covariate that is orthogonal to arm by construction."""
    rng = random.Random(23)
    n = 40
    groups = ["A" if i % 2 else "B" for i in range(n)]
    # covariate perfectly balanced across arms and unrelated to the outcome
    ages = [30 + (i // 2) for i in range(n)]
    rows = [[rng.gauss(20, 3), rng.gauss(17, 3)] for _ in range(n)]
    with_cov = mmrm_analysis(_panel(rows, groups, [_num("나이", ages)]), 0)
    plain = mmrm_analysis(_panel(rows, groups), 0)
    a = with_cov.contrasts[0].estimate
    b = plain.contrasts[0].estimate
    assert a == pytest.approx(b, abs=0.35)      # same story, tiny reshuffle


def test_mmrm_keeps_partially_observed_subjects_when_covariates_are_present():
    p = _cov_panel(random.Random(24), n=50, n_times=3, missing=0.3)
    res = mmrm_analysis(p, 0)
    assert res is not None
    assert res.n_subjects > len(p.complete_rows())
    assert res.covariates


def test_mmrm_drops_subjects_with_a_missing_covariate_and_reports_the_count():
    p = _cov_panel(random.Random(25), n=44, n_times=3)
    for i in (0, 1, 2):
        p.covariates[0].numeric[i] = None
        p.covariates[0].values[i] = None
    res = mmrm_analysis(p, 0)
    assert res.n_subjects == 41
    assert any("3명은 공변량 값이 없어" in n for n in res.notes)
    assert res.n_dropped >= 3


def test_mmrm_reports_a_dropped_collinear_covariate():
    p = _cov_panel(random.Random(26), n=40, n_times=3)
    p.covariates.append(_num("나이복사", list(p.covariates[0].numeric)))
    res = mmrm_analysis(p, 0)
    assert "나이복사" not in res.covariates
    assert any("공선성" in n for n in res.notes)


def test_mmrm_covariate_note_explains_what_the_contrast_means():
    res = mmrm_analysis(_cov_panel(random.Random(27), n=40), 0)
    note = [n for n in res.notes if "보정 공변량" in n][0]
    assert "평균 중심화" in note and "나이" in note


def test_single_arm_mmrm_accepts_covariates_without_a_baseline_adjustment():
    rng = random.Random(28)
    rows = [[rng.gauss(20, 3), rng.gauss(18, 3), rng.gauss(16, 3)]
            for _ in range(30)]
    ages = [30 + i for i in range(30)]
    res = mmrm_analysis(_panel(rows, None, [_num("나이", ages)]), 0)
    assert res is not None and not res.adjusted
    assert res.covariates == ["나이"]
    # visit means are still LS-means at the mean age: centring guarantees it
    for k, ls in enumerate(res.lsmeans):
        want = sum(r[k] for r in rows) / len(rows)
        assert ls.estimate == pytest.approx(want, abs=1e-8)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

CLI_CSV_HEAD = "대상,군,방문,점수,나이,성별\n"


def _cli_csv(n=36, seed=31):
    rng = random.Random(seed)
    out = [CLI_CSV_HEAD.rstrip()]
    for i in range(n):
        arm = "능동" if i % 2 else "위약"
        age, sex = rng.randint(25, 70), rng.choice(["남", "여"])
        base = rng.gauss(22, 3)
        for t, eff in (("기저", 0.0), ("4주", -3.0), ("8주", -6.0)):
            v = base + eff * (1.5 if arm == "능동" else 1.0) + rng.gauss(0, 2)
            out.append(f"P{i:02d},{arm},{t},{v:.1f},{age},{sex}")
    return "\n".join(out) + "\n"


def test_cli_covariate_flag_end_to_end(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--group", "군", "--covariate", "나이,성별"])
    out = capsys.readouterr().out
    assert code == 0
    assert "보정 공변량" in out and "나이" in out and "성별=" in out


def test_cli_covariate_reaches_json(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "나이", "--format", "json"])
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["mmrm"]["covariates"] == ["나이"]
    assert data["ancova"]["covariates"] == ["나이"]


def test_cli_categorical_without_covariate_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--categorical", "성별"])
    assert code == 1
    assert "--covariate 와 함께" in capsys.readouterr().err


def test_cli_categorical_must_name_a_listed_covariate(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--covariate", "나이", "--categorical", "성별"])
    assert code == 1
    assert "목록에 없습니다" in capsys.readouterr().err


def test_cli_repeated_covariate_name_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--covariate", "나이,나이"])
    assert code == 1
    assert "두 번" in capsys.readouterr().err


def test_cli_blank_covariate_argument_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--covariate", " , "])
    assert code == 1
    assert "비어 있습니다" in capsys.readouterr().err


def test_cli_covariate_name_is_nfc_normalised(tmp_path, capsys):
    """A decomposed-Hangul argument from a macOS shell must still match."""
    import unicodedata
    path = _write(tmp_path, "cli.csv", _cli_csv())
    decomposed = unicodedata.normalize("NFD", "나이")
    assert decomposed != "나이"
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--group", "군", "--covariate", decomposed])
    assert code == 0
    assert "보정 공변량" in capsys.readouterr().out


def test_over_adjustment_leaves_a_warning_instead_of_confident_numbers():
    """14 subjects and four 3-level covariates still *fits* — and the intervals
    it produces are worthless.  The report has to say so."""
    rng = random.Random(77)
    n = 14
    groups = ["A" if i % 2 else "B" for i in range(n)]
    rows = [[rng.gauss(20, 3), rng.gauss(19, 3), rng.gauss(18, 3)]
            for _ in range(n)]
    covs = [_cat(f"c{k}", [rng.choice("xyz") for _ in range(n)])
            for k in range(4)]
    p = _panel(rows, groups, covs)
    res = mmrm_analysis(p, 0)
    anc = ancova_analysis(p, 0)
    assert res is not None
    assert any("잔차 자유도가" in note for note in res.notes)
    assert any("잔차 자유도가" in note for note in anc.notes)


def test_no_over_adjustment_warning_when_there_is_room():
    p = _cov_panel(random.Random(78), n=60, n_times=3)
    res = mmrm_analysis(p, 0)
    anc = ancova_analysis(p, 0)
    assert not any("잔차 자유도가" in note for note in res.notes)
    assert not any("잔차 자유도가" in note for note in anc.notes)


# --------------------------------------------------------------------------
# hardening round 2026-08-02 — regressions for every reviewer finding
# --------------------------------------------------------------------------

def test_single_arm_contrast_df_pays_for_the_covariate_columns():
    """The contrast used n − 1 while the LS-means one line above already
    subtracted the covariate columns — the same table disagreed with itself,
    and the contrast's p and CI were both too narrow."""
    rng = random.Random(401)
    n = 24
    rows = [[rng.gauss(20, 3), rng.gauss(17, 3)] for _ in range(n)]
    ages = [30 + i for i in range(n)]
    sites = [["S1", "S2", "S3", "S4", "S5"][i % 5] for i in range(n)]
    covs = [_num("나이", ages), _cat("기관", sites)]      # 1 + 4 = 5 columns
    res = mmrm_analysis(_panel(rows, None, covs), 0)
    assert res is not None and len(res.covariates) == 5
    ls_df = {x.df for x in res.lsmeans}
    assert ls_df == {float(n - 1 - 5)}
    assert res.contrasts[0].df == float(n - 1 - 5)


def test_mmrm_fits_when_the_reference_level_is_absent_at_a_later_visit():
    """The reference level is fixed panel-wide, so if its subjects have all
    dropped out by week 8 the remaining dummies sum to a constant and are
    exactly collinear with that visit's cells.  This used to abort the whole
    section with a Cholesky error blaming the sample size."""
    rng = random.Random(402)
    rows, groups, sites = [], [], []
    for i in range(48):
        site = ["S1", "S2", "S3"][i % 3]
        base = rng.gauss(20, 3)
        row = [base, base - 2 + rng.gauss(0, 2), base - 4 + rng.gauss(0, 2)]
        if site == "S1":
            row[2] = None                      # every S1 subject leaves early
        rows.append(row)
        groups.append("A" if i % 2 else "B")
        sites.append(site)
    p = _panel(rows, groups, [_cat("기관", sites)])
    skipped = []
    res = mmrm_analysis(p, 0, skipped=skipped)
    assert res is not None and res.converged, skipped
    assert any("완전히 겹쳐" in n for n in res.notes)
    assert any("V2" in n for n in res.notes if "완전히 겹쳐" in n)


def test_covariate_confounded_with_arm_is_dropped_not_fatal():
    """Site nested in arm is the single most likely real-world trigger."""
    rng = random.Random(403)
    rows, groups, sites = [], [], []
    for i in range(40):
        arm = "A" if i % 2 else "B"
        base = rng.gauss(20, 3)
        rows.append([base, base - 2 + rng.gauss(0, 2)])
        groups.append(arm)
        sites.append("서울" if arm == "A" else "부산")
    p = _panel(rows, groups, [_cat("기관", sites)])
    skipped = []
    res = mmrm_analysis(p, 0, skipped=skipped)
    assert res is not None and res.converged, skipped
    assert res.contrasts, "the between-arm contrast must survive"
    anc = ancova_analysis(p, 0)
    assert anc is not None and anc.contrasts
    assert any("완전히 겹쳐" in n for n in anc.notes)


def test_encoding_is_invariant_to_the_units_of_a_covariate():
    """mol/L instead of nmol/L silently dropped the covariate as 'collinear'
    and reported the unadjusted answer."""
    vals = [2.5, 3.1, 2.8, 3.4, 2.2, 3.0, 2.9, 3.3]
    for scale in (1.0, 1e-4, 1e-9, 1e6):
        d = encode_covariates([_num("농도", [v * scale for v in vals])],
                              list(range(len(vals))))
        assert d.names == ["농도"], f"dropped at scale {scale}"
        first = [r[0] / scale for r in d.columns]
        assert first == pytest.approx([r[0] for r in encode_covariates(
            [_num("농도", vals)], list(range(len(vals)))).columns], rel=1e-9)


def test_ancova_is_invariant_to_the_units_of_a_covariate():
    p = _cov_panel(random.Random(404), n=40)
    base_age = list(p.covariates[0].numeric)
    got = []
    for scale in (1.0, 1e-9):
        p.covariates[0] = _num("나이", [v * scale for v in base_age])
        res = ancova_analysis(p, 0)
        con = [c for c in res.contrasts if c.time == "V1"][0]
        got.append((con.adjusted_diff, con.df))
    assert got[0][0] == pytest.approx(got[1][0], rel=1e-7)
    assert got[0][1] == got[1][1]


def test_a_nearly_collinear_covariate_is_kept_not_dropped():
    """Pins the loose direction of the tolerance: only *exact* aliasing goes."""
    rng = random.Random(405)
    a = [float(i) for i in range(30)]
    b = [2 * v + rng.gauss(0, 0.05) for v in a]      # residual ~1e-3 of the norm
    d = encode_covariates([_num("a", a), _num("b", b)], list(range(30)))
    assert d.names == ["a", "b"]


def test_lone_constant_covariate_is_not_blamed_on_collinearity():
    d = encode_covariates([_num("용량", [5.0] * 8)], list(range(8)))
    assert d.names == []
    assert "겹쳐" not in d.dropped[0]


def test_covariate_missing_for_a_whole_arm_says_the_arm_is_gone():
    rng = random.Random(406)
    rows, groups, ages = [], [], []
    for i in range(40):
        arm = "A" if i % 2 else "B"
        base = rng.gauss(20, 3)
        rows.append([base, base - 2 + rng.gauss(0, 2)])
        groups.append(arm)
        ages.append(30 + i if arm == "A" else None)   # B has no covariate at all
    p = _panel(rows, groups, [_num("나이", ages)])
    res = mmrm_analysis(p, 0)
    assert res is not None
    assert any("군간 비교를 수행하지 못했습니다" in n for n in res.notes)


def test_report_does_not_claim_the_same_people_when_covariates_excluded_some():
    from longistat.analyze import Options, analyze
    from longistat.report import render_text
    rng = random.Random(407)
    rows, groups, ages = [], [], []
    for i in range(40):
        base = rng.gauss(20, 3)
        rows.append([base, base - 2 + rng.gauss(0, 2)])
        groups.append("A" if i % 2 else "B")
        ages.append(None if i < 6 else 30 + i)
    p = _panel(rows, groups, [_num("나이", ages)])
    out = render_text(analyze(p, Options()))
    assert "완전자료 대상과 같은 인원을 씁니다" not in out
    assert "명만 모형에 들어갔습니다" in out


def test_ancova_reports_why_it_could_not_fit_instead_of_vanishing():
    """Every visit failing used to delete [5b] together with its reasons."""
    from longistat.analyze import Options, analyze
    from longistat.report import render_text
    rng = random.Random(408)
    rows, groups = [], []
    for i in range(12):
        rows.append([5.0, rng.gauss(10, 2)])          # constant baseline
        groups.append("A" if i % 2 else "B")
    p = _panel(rows, groups)
    res = ancova_analysis(p, 0)
    assert res is not None and not res.contrasts and res.notes
    assert "[5b] 기저값 보정 (ANCOVA) 미수행" in render_text(analyze(p, Options()))


def test_conflicting_covariate_error_redacts_both_values(tmp_path):
    """The old value was printed raw while the new one was redacted — a
    free-text covariate cell put full PII into a pasteable error."""
    secret = "김철수 1978-03-14 010-2345-6789 재발성우울장애"
    text = ("대상,방문,점수,메모\n"
            f"P1,기저,20,{secret}\n"
            "P1,4주,15,다른값이다 아주 길게 적은 문자열\n")
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "pii.csv", text), "대상", "방문", "점수",
                  covariate_cols=["메모"])
    msg = str(exc.value)
    assert secret not in msg
    assert "자)" in msg           # both sides truncated by _redact


def test_reference_level_is_redacted_in_the_note(tmp_path):
    long_name = "가" * 200
    text = ("대상,방문,점수,기관\n"
            f"P1,기저,20,{long_name}\nP1,4주,15,{long_name}\n"
            "P2,기저,22,다른기관\nP2,4주,19,다른기관\n")
    notes = []
    load_long(_write(tmp_path, "long.csv", text), "대상", "방문", "점수",
              covariate_cols=["기관"], notes=notes)
    joined = " ".join(notes)
    assert long_name not in joined
    assert "(200자)" in joined


def test_long_category_labels_do_not_reach_coefficient_names():
    long_level = "환자정보 " + "나" * 300
    d = encode_covariates(
        [_cat("담당의", [long_level, "김", long_level, "김"])], [0, 1, 2, 3])
    assert d.names, "the factor itself must still be usable"
    assert all(len(n) < 60 for n in d.names), d.names
    assert long_level not in " ".join(d.names)


def test_too_many_levels_names_the_value_that_forced_the_categorical_reading(
        tmp_path):
    """One '45세' in an age column used to yield 'is this an ID or a date?'."""
    lines = ["대상,방문,점수,나이"]
    for i in range(20):
        age = "45세" if i == 0 else str(30 + i)
        lines.append(f"P{i},기저,20,{age}")
        lines.append(f"P{i},4주,18,{age}")
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "age.csv", "\n".join(lines) + "\n"),
                  "대상", "방문", "점수", covariate_cols=["나이"])
    msg = str(exc.value)
    assert "45세" in msg and "12" in msg


def test_max_levels_boundary_is_enforced_exactly(tmp_path):
    """The guard that stops a patient-ID column becoming a covariate."""
    def build(n_levels):
        lines = ["대상,방문,점수,코드"]
        for i in range(n_levels * 2):
            lines.append(f"P{i},기저,20,L{i % n_levels}")
            lines.append(f"P{i},4주,18,L{i % n_levels}")
        return "\n".join(lines) + "\n"

    ok = load_long(_write(tmp_path, "ok.csv", build(12)), "대상", "방문", "점수",
                   covariate_cols=["코드"], categorical_cols=["코드"])
    assert len(ok.covariates[0].level_labels()) == 12
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "bad.csv", build(13)), "대상", "방문", "점수",
                  covariate_cols=["코드"], categorical_cols=["코드"])
    assert "12" in str(exc.value)


def test_ancova_turns_a_column_overflow_into_a_note_not_a_crash():
    rng = random.Random(409)
    n = 80
    rows = [[rng.gauss(20, 3), rng.gauss(18, 3)] for _ in range(n)]
    groups = ["A" if i % 2 else "B" for i in range(n)]
    covs = [_cat(f"c{k}", [rng.choice("abcdef") for _ in range(n)])
            for k in range(5)]                      # 25 columns > MAX_COLUMNS
    res = ancova_analysis(_panel(rows, groups, covs), 0)
    assert res is not None
    assert not res.contrasts
    assert any(str(MAX_COLUMNS) in note for note in res.notes)


def test_library_loader_rejects_a_repeated_covariate_name(tmp_path):
    """cli._split_names catches this first; the library API must too."""
    with pytest.raises(DataError) as exc:
        load_long(_write(tmp_path, "a.csv", LONG_CSV), "대상", "방문", "점수",
                  covariate_cols=["나이", "나이"])
    assert "두 번" in str(exc.value)


def _thin_csv(n=14):
    rng = random.Random(303)
    out = ["대상,군,방문,점수,c1,c2,c3,c4"]
    for i in range(n):
        arm = "A" if i % 2 else "B"
        cs = ",".join(rng.choice("xyz") for _ in range(4))
        for t in ("기저", "4주", "8주"):
            out.append(f"S{i},{arm},{t},{rng.gauss(20, 3):.1f},{cs}")
    return "\n".join(out) + "\n"


def test_thin_df_warning_reaches_the_top_level_notice_and_the_sentences(
        tmp_path, capsys):
    """The [4c]/[5b] footnote is easy to scroll past; the 주의 list and the
    paste-into-manuscript sentences are not."""
    path = _write(tmp_path, "thin.csv", _thin_csv())
    code = main([path, "--id", "대상", "--time", "방문", "--value", "점수",
                 "--group", "군", "--covariate", "c1,c2,c3,c4"])
    out = capsys.readouterr().out
    assert code == 0
    notice = out.split("[!] 주의", 1)[1]
    assert "잔차 자유도가 5 미만" in notice
    sentences = out.split("[10]", 1)[1]
    assert "잔차 자유도가 5 미만" in sentences
    assert "should not be quoted as they stand" in sentences


def test_ancova_block_is_labelled_5b_as_the_docs_promise():
    """README/사용법/--help all send the reader to [5b]; it has to exist."""
    from longistat.analyze import Options, analyze
    from longistat.report import render_markdown, render_text
    p = _cov_panel(random.Random(304), n=40)
    a = analyze(p, Options())
    assert "[5b] 기저값 보정 (ANCOVA)" in render_text(a)
    assert "[5b] 기저값 보정 (ANCOVA)" in render_markdown(a)


def test_english_mmrm_sentence_says_covariate_adjusted_when_it_is(
        tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "나이"])
    with_cov = capsys.readouterr().out
    assert "baseline- and covariate-adjusted between-arm difference" in with_cov
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군"])
    plain = capsys.readouterr().out
    assert "baseline-adjusted between-arm difference" in plain
    assert "covariate-adjusted" not in plain


def test_paper_sentences_name_the_covariates_that_were_fitted(tmp_path, capsys):
    """A covariate-adjusted model described as unadjusted is a misreported
    analysis — the [10] sentences are pasted into manuscripts verbatim."""
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "나이,성별",
          "--labels-en", "나이=age,성별=sex"])
    out = capsys.readouterr().out
    assert "그리고 나이, 성별(각각 시점과의 교호작용 포함)이었다" in out
    assert "together with age, sex, each interacted with visit" in out
    assert "기저값과 나이, 성별을 공변량으로 보정" in out
    assert "Adjusting for baseline and age, sex (ANCOVA)" in out


def test_paper_sentences_are_unchanged_without_covariates(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군"])
    out = capsys.readouterr().out
    assert "기저값(시점과의 교호작용 포함)이었다" in out
    assert "baseline with its visit interaction." in out
    assert "기저값을 공변량으로 보정했을 때" in out


def test_dummy_columns_collapse_back_to_the_variable_name_in_sentences(
        tmp_path, capsys):
    text = ("대상,군,방문,점수,기관\n" + "".join(
        f"P{i:02d},{'A' if i % 2 else 'B'},{t},{20 + (i * 7 + k) % 9}.0,"
        f"{'S1' if i % 3 == 0 else ('S2' if i % 3 == 1 else 'S3')}\n"
        for i in range(30) for k, t in enumerate(("기저", "4주", "8주"))))
    path = _write(tmp_path, "s.csv", text)
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "기관"])
    out = capsys.readouterr().out
    assert "그리고 기관(각각" in out            # not "기관=S2, 기관=S3"
    assert "기관=S2, 기관=S3" not in out.split("[10]")[-1]


def test_markdown_output_carries_the_covariate_note_with_the_ancova_table(
        tmp_path, capsys):
    """Markdown is what gets pasted into a manuscript — the adjustment set has
    to travel with the table, not stay behind in the text report."""
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "나이", "--format", "md"])
    out = capsys.readouterr().out
    after_table = out.split("## [5b] 기저값 보정 (ANCOVA)", 1)[1]
    assert "> 보정 공변량: 기저값 + 나이" in after_table


def test_csv_output_records_what_the_ancova_was_adjusted_for(tmp_path, capsys):
    path = _write(tmp_path, "cli.csv", _cli_csv())
    main([path, "--id", "대상", "--time", "방문", "--value", "점수",
          "--group", "군", "--covariate", "나이", "--format", "csv"])
    out = capsys.readouterr().out
    note_rows = [ln for ln in out.splitlines() if ln.startswith("ancova_note")]
    assert note_rows and any("나이" in ln for ln in note_rows)


def test_cli_covariate_works_in_wide_format(tmp_path, capsys):
    text = ("환자,군,기저,4주,나이\n"
            + "".join(f"W{i:02d},{'A' if i % 2 else 'B'},"
                      f"{20 + i % 5}.0,{15 + i % 4}.0,{30 + i}\n"
                      for i in range(24)))
    path = _write(tmp_path, "w.csv", text)
    code = main([path, "--wide", "--id", "환자", "--columns", "기저,4주",
                 "--group", "군", "--covariate", "나이"])
    assert code == 0
    assert "보정 공변량" in capsys.readouterr().out
