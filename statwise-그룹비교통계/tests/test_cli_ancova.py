"""End-to-end CLI coverage for the covariate-adjusted (ANCOVA) mode."""

import json

import pytest

from statwise.cli import main


def _write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


#: A small two-arm trial with a predictive baseline, a site factor, one row
#: whose outcome is missing, and one whose site is missing.
_ROWS = [
    "subject,arm,site,age,base,post",
    "S01,placebo,seoul,45,15,16.1",
    "S02,drug,busan,52,14,11.8",
    "S03,placebo,busan,38,18,19.4",
    "S04,drug,seoul,61,17,14.2",
    "S05,placebo,seoul,49,12,12.9",
    "S06,drug,busan,44,20,17.1",
    "S07,placebo,busan,57,16,17.2",
    "S08,drug,seoul,35,13,10.4",
    "S09,placebo,seoul,50,19,20.3",
    "S10,drug,busan,42,15,12.2",
    "S11,placebo,busan,48,17,18.1",
    "S12,drug,seoul,55,18,15.0",
    "S13,placebo,seoul,41,14,15.2",
    "S14,drug,busan,39,16,13.1",
    "S15,placebo,busan,46,20,21.0",
    "S16,drug,seoul,53,12,9.5",
    "S17,placebo,seoul,44,,17.4",     # covariate missing -> dropped
    "S18,drug,,47,15,12.0",           # factor missing -> dropped when used
]


@pytest.fixture
def trial_csv(tmp_path):
    return _write(tmp_path, "trial.csv", "\n".join(_ROWS) + "\n")


def test_basic_ancova_runs_and_reports(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--reference", "placebo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "공변량 보정 그룹 비교 (ANCOVA)" in out
    assert "보정평균" in out
    assert "drug − placebo" in out
    assert "기울기 동질성" in out
    assert "무작위배정 전에 측정된" in out


def test_adjust_factor_alone_is_allowed(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--adjust-factor", "site", "--reference", "placebo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "site=" in out
    # no numeric covariate -> the slope check is skipped, and says so
    assert "기울기 동질성: (건너뜀)" in out


def test_multiple_covariates_and_factors(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base,age", "--adjust-factor", "site",
               "--reference", "placebo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "base" in out and "age" in out and "site=" in out


def test_missing_rows_are_counted_not_silently_dropped(trial_csv, capsys):
    main([trial_csv, "--value", "post", "--group", "arm",
          "--covariate", "base", "--reference", "placebo"])
    out = capsys.readouterr().out
    assert "분석 n = 17 (결측으로 제외 1행)" in out
    assert "완전자료(complete-case)" in out


def test_factor_missing_drops_the_row_only_when_the_factor_is_used(trial_csv,
                                                                   capsys):
    main([trial_csv, "--value", "post", "--group", "arm",
          "--adjust-factor", "site"])
    with_factor = capsys.readouterr().out
    assert "분석 n = 17" in with_factor        # S18 (no site) dropped
    main([trial_csv, "--value", "post", "--group", "arm",
          "--covariate", "base"])
    with_cov = capsys.readouterr().out
    assert "분석 n = 17" in with_cov           # S17 (no base) dropped


def test_json_output_is_valid_and_machine_readable(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--reference", "placebo",
               "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["analysis"] == "ancova"
    assert payload["reference"] == "placebo"
    assert payload["contrasts"][0]["b"] == "placebo"
    assert payload["group_effect"]["df1"] == 1.0


def test_csv_output_has_one_row_per_estimate(trial_csv, capsys):
    main([trial_csv, "--value", "post", "--group", "arm",
          "--covariate", "base", "--reference", "placebo", "--format", "csv"])
    lines = capsys.readouterr().out.strip().splitlines()
    kinds = [line.split(",")[1] for line in lines[1:]]
    assert kinds[0] == "ancova"
    assert kinds.count("adjusted-mean") == 2
    assert kinds.count("adjusted-contrast") == 1
    assert kinds.count("covariate") == 1


def test_equivalence_margin_applies_to_the_adjusted_difference(trial_csv,
                                                               capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--reference", "placebo",
               "--ni-margin", "2", "--ni-direction", "lower_is_better"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "비열등성 검정" in out
    assert "보정된 평균차" in out
    assert "ANCOVA (공변량 보정 최소제곱)" in out


def test_alpha_flows_into_the_intervals(trial_csv, capsys):
    main([trial_csv, "--value", "post", "--group", "arm",
          "--covariate", "base", "--alpha", "0.01", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["alpha"] == 0.01
    wide = payload["contrasts"][0]
    assert wide["ci_high"] - wide["ci_low"] > 0


@pytest.mark.parametrize("extra", [
    ["--wide"],
    ["--paired", "--id", "subject"],
    ["--binary"],
    ["--columns", "a,b"],
])
def test_incompatible_modes_are_refused(trial_csv, capsys, extra):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base"] + extra)
    assert rc == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_covariate_with_multi_endpoint_mode_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--values", "post,base", "--group", "arm",
               "--covariate", "age"])
    assert rc == 2
    assert "--values" in capsys.readouterr().err


def test_covariate_without_value_or_group_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--covariate", "base"])
    assert rc == 2
    assert "--value" in capsys.readouterr().err


def test_prespecified_test_flag_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--test", "welch"])
    assert rc == 2
    assert "--test" in capsys.readouterr().err


def test_no_posthoc_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--no-posthoc"])
    assert rc == 2
    assert "--no-posthoc" in capsys.readouterr().err


def test_unknown_covariate_column_names_the_header(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "bsae"])
    assert rc == 2
    assert "bsae" in capsys.readouterr().err


def test_covariate_may_not_double_as_the_outcome(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "post"])
    assert rc == 2
    assert "두 가지 역할" in capsys.readouterr().err


def test_empty_covariate_value_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", ""])
    assert rc == 2
    assert "빈 값" in capsys.readouterr().err


def test_unknown_reference_is_refused(trial_csv, capsys):
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--reference", "vehicle"])
    assert rc == 2
    assert "vehicle" in capsys.readouterr().err


def test_all_rows_incomplete_gives_a_clear_error(tmp_path, capsys):
    path = _write(tmp_path, "holes.csv",
                  "arm,base,post\na,,1\nb,,2\na,,3\nb,,4\n")
    rc = main([path, "--value", "post", "--group", "arm",
               "--covariate", "base"])
    assert rc == 2
    assert "완전한 행이 없습니다" in capsys.readouterr().err


def test_single_group_after_loading_is_refused(tmp_path, capsys):
    path = _write(tmp_path, "one.csv",
                  "arm,base,post\na,1,2\na,2,3\na,3,5\na,4,6\n")
    rc = main([path, "--value", "post", "--group", "arm",
               "--covariate", "base"])
    assert rc == 2
    assert "그룹이 2개 이상" in capsys.readouterr().err


def test_covariate_constant_within_arm_is_refused_with_a_hint(tmp_path, capsys):
    path = _write(tmp_path, "alias.csv",
                  "arm,code,post\n" + "\n".join(
                      f"{'a' if i % 2 else 'b'},{0 if i % 2 else 1},{i}"
                      for i in range(1, 11)) + "\n")
    rc = main([path, "--value", "post", "--group", "arm",
               "--covariate", "code"])
    assert rc == 2
    assert "선형종속" in capsys.readouterr().err


def test_semicolon_delimiter_and_cp949_encoding(tmp_path, capsys):
    rows = ["군;기저;사후"] + [
        f"{'약물' if i % 2 else '위약'};{10 + i};{12 + i * 0.9}"
        for i in range(1, 13)]
    path = tmp_path / "kr.csv"
    path.write_text("\n".join(rows) + "\n", encoding="cp949")
    rc = main([str(path), "--value", "사후", "--group", "군",
               "--covariate", "기저", "--delimiter", ";"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "약물" in out and "위약" in out


def test_sentinel_missing_codes_in_a_covariate_are_flagged(tmp_path, capsys):
    rows = ["arm,base,post"]
    for i in range(1, 9):
        rows.append(f"{'a' if i % 2 else 'b'},{10 + i},{12 + i * 0.8}")
    rows.append("a,-999,14.0")
    path = _write(tmp_path, "sentinel.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--covariate", "base"])
    out = capsys.readouterr().out
    assert "결측 코드로 흔히 쓰이는 값" in out
    # and the paste-ready sentence is withheld while the input is suspect
    assert "논문용 문장을 생성하지 않았습니다" in out


def test_output_file_is_written_with_tight_permissions(trial_csv, tmp_path,
                                                       capsys):
    import os
    dest = tmp_path / "ancova.json"
    rc = main([trial_csv, "--value", "post", "--group", "arm",
               "--covariate", "base", "--format", "json",
               "-o", str(dest)])
    assert rc == 0
    assert json.loads(dest.read_text(encoding="utf-8"))["analysis"] == "ancova"
    assert os.stat(dest).st_mode & 0o777 == 0o600


def test_literal_na_in_a_factor_column_is_missing_not_a_level(tmp_path, capsys):
    """`NA` / `.` in a stratification column must not become a real site."""
    rows = ["arm,site,base,post"]
    for i in range(1, 13):
        rows.append(f"{'a' if i % 2 else 'b'},{'s1' if i % 3 else 's2'},"
                    f"{10 + i},{12 + i * 0.9}")
    rows.append("a,NA,15,17.0")
    rows.append("b,.,16,18.0")
    path = _write(tmp_path, "na_factor.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--adjust-factor", "site",
          "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_dropped"] == 2
    assert payload["n_used"] == 12
    names = [e["name"] for e in payload["covariate_effects"]]
    assert names == ["site=s2 (vs s1)"]        # no phantom "NA" / "." level


def test_group_labels_in_the_json_missing_map_are_sanitised(tmp_path, capsys):
    """The `missing` map is the one place raw group cells reach the report.

    A one-character slip pointing --group at a subject-id column must not put
    300-character identifiers with control bytes into a file that gets emailed.
    """
    long_label = "PT-" + "9" * 300
    rows = ["arm,base,post",
            f"{long_label}\x07,10,12", f"{long_label}\x07,11,13",
            f"{long_label}\x07,12,",           # missing outcome -> counted
            "ctrl,10,11", "ctrl,11,12", "ctrl,12,13"]
    path = _write(tmp_path, "wild.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--covariate", "base",
          "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    for key in payload["missing"]:
        assert len(key) <= 41
        assert not any(ord(ch) < 32 for ch in key)
    assert sum(payload["missing"].values()) == 1


def test_equivalence_json_and_csv_rows_are_emitted(trial_csv, capsys):
    main([trial_csv, "--value", "post", "--group", "arm", "--covariate", "base",
          "--reference", "placebo", "--ni-margin", "2",
          "--ni-direction", "lower_is_better", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["equivalence"]["kind"] == "noninferiority"
    assert payload["equivalence"]["direction"] == "lower_is_better"
    main([trial_csv, "--value", "post", "--group", "arm", "--covariate", "base",
          "--reference", "placebo", "--ni-margin", "2",
          "--ni-direction", "lower_is_better", "--format", "csv"])
    kinds = [line.split(",")[1]
             for line in capsys.readouterr().out.strip().splitlines()[1:]]
    assert "noninferiority" in kinds


def test_too_many_arms_is_refused_at_the_cli(tmp_path, capsys):
    from statwise.ancova import MAX_ANCOVA_GROUPS
    rows = ["arm,base,post"]
    for g in range(MAX_ANCOVA_GROUPS + 1):
        for j in range(3):
            rows.append(f"g{g},{j},{g + j}")
    path = _write(tmp_path, "many.csv", "\n".join(rows) + "\n")
    rc = main([path, "--value", "post", "--group", "arm", "--covariate", "base"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "상한" in err and "--group" in err


def test_long_arm_names_are_not_merged_by_a_display_truncation(tmp_path,
                                                               capsys):
    """Two arms agreeing in their first 39 characters are two arms.

    Keying group identity on the 40-char display label silently averaged a
    high-dose and a low-dose arm together and reported one confident adjusted
    difference against control.
    """
    stem = "Investigational_Product_XYZ_Arm_Cohort_20"   # 40 chars
    rows = ["arm,base,post"]
    for i in range(1, 6):
        rows.append(f"{stem}HIGH,{i},{i + 0.1}")
        rows.append(f"{stem}LOW,{i},{i + 3.7}")
        rows.append(f"placebo,{i},{i + 6.2}")
    path = _write(tmp_path, "long_arms.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--covariate", "base",
          "--reference", "placebo", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["adjusted_means"]) == 3
    assert [m["n"] for m in payload["adjusted_means"]] == [5, 5, 5]
    labels = [m["label"] for m in payload["adjusted_means"]]
    assert len(set(labels)) == 3               # printable names stay distinct
    assert any("이름이 같아집니다" in w for w in payload["warnings"])


def test_constant_outcome_is_refused_not_given_a_plausible_p(tmp_path, capsys):
    path = _write(tmp_path, "flat.csv", "arm,base,post\n" + "\n".join(
        f"{'a' if i % 2 else 'b'},{i},4" for i in range(1, 9)) + "\n")
    rc = main([path, "--value", "post", "--group", "arm", "--covariate", "base"])
    assert rc == 2
    assert "분산 0" in capsys.readouterr().err


def test_an_arm_wiped_out_by_complete_case_filtering_is_named(tmp_path, capsys):
    rows = ["arm,base,post", "A,1,2", "A,2,3", "A,3,5",
            "B,1,3", "B,2,4", "B,3,6", "C,,7", "C,,8", "C,,9"]
    path = _write(tmp_path, "gone.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--covariate", "base"])
    out = capsys.readouterr().out
    assert "통째로 빠진 군: C" in out
    assert "군별 제외 행수" in out and "C=3" in out


def test_factor_levels_differing_only_by_case_are_flagged(tmp_path, capsys):
    rows = ["arm,site,base,post"]
    for i in range(1, 16):
        site = ["Seoul", "seoul ", " SEOUL"][i % 3]
        rows.append(f"{'a' if i % 2 else 'b'},{site},{i},{i * 1.1 + (2 if i % 2 else 0)}")
    path = _write(tmp_path, "case_site.csv", "\n".join(rows) + "\n")
    main([path, "--value", "post", "--group", "arm", "--covariate", "base",
          "--adjust-factor", "site"])
    out = capsys.readouterr().out
    assert "보정인자 'site'의 수준" in out


def test_million_scale_values_keep_their_column_separators(tmp_path, capsys):
    """Viral load in copies/mL used to fuse n, mean, adjusted and SE together."""
    rows = ["arm,vl_base,vl_post"]
    for i in range(1, 21):
        base = 2_600_000 + i * 40_000
        post = base * 0.5 + (-280_000 if i % 2 else 0) + i * 900
        rows.append(f"{'drug' if i % 2 else 'placebo'},{base},{post:.1f}")
    path = _write(tmp_path, "viral.csv", "\n".join(rows) + "\n")
    main([path, "--value", "vl_post", "--group", "arm",
          "--covariate", "vl_base", "--reference", "placebo"])
    out = capsys.readouterr().out
    body = [l for l in out.splitlines()
            if l.startswith("    drug ") or l.startswith("    placebo ")]
    assert body
    for line in body:
        # every numeric cell must still be separated by whitespace
        assert "  " in line.strip()
        assert not any(len(tok) > 24 for tok in line.split())


def test_tiny_covariate_is_not_misdiagnosed_as_collinear(tmp_path, capsys):
    """A 1e-300 covariate is a unit choice, not a constant column."""
    rows = ["arm,base,post"]
    for i in range(1, 17):
        rows.append(f"{'a' if i % 2 else 'b'},{i}e-300,"
                    f"{i * 1.1 + (2 if i % 2 else 0)}")
    path = _write(tmp_path, "tiny.csv", "\n".join(rows) + "\n")
    rc = main([path, "--value", "post", "--group", "arm", "--covariate", "base"])
    out = capsys.readouterr().out
    assert rc == 0                                   # no ZeroDivisionError
    assert "선형종속" not in out
    # infinite SEs are called out, and the paste-ready sentence is withheld
    assert "표준오차가 유한하지 않아" in out
    assert "논문용 문장을 생성하지 않았습니다" in out
