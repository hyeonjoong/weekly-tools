"""CLI wiring for the paired binary (McNemar) mode."""

import json

import pytest

from statwise.cli import main

LONG = """subject,time,responder
S1,pre,N
S1,post,Y
S2,pre,N
S2,post,Y
S3,pre,Y
S3,post,Y
S4,pre,N
S4,post,N
S5,pre,Y
S5,post,N
S6,pre,N
S6,post,Y
S7,pre,N
S7,post,Y
S8,pre,N
S8,post,Y
S9,pre,N
S9,post,Y
S10,pre,N
S10,post,Y
"""

WIDE = """testA,testB
Y,Y
Y,N
Y,N
N,Y
N,N
Y,N
Y,N
N,N
Y,Y
Y,N
"""


@pytest.fixture()
def long_csv(tmp_path):
    p = tmp_path / "paired_binary.csv"
    p.write_text(LONG, encoding="utf-8")
    return str(p)


@pytest.fixture()
def wide_csv(tmp_path):
    p = tmp_path / "paired_binary_wide.csv"
    p.write_text(WIDE, encoding="utf-8")
    return str(p)


def test_long_mode_runs_and_pins_the_direction(long_csv, capsys):
    assert main([long_csv, "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject", "--baseline", "pre"]) == 0
    out = capsys.readouterr().out
    # 7 subjects went N->Y, 1 went Y->N, so post minus pre must be positive
    assert "불일치(discordant) 쌍 = 8개" in out
    assert "(post만 7, pre만 1)" in out
    assert "McNemar exact test" in out
    assert "기준 reference = pre" in out


def test_baseline_flips_the_sign_not_the_p_value(long_csv, capsys):
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--format", "json"])
    a = json.loads(capsys.readouterr().out)
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "post", "--format", "json"])
    b = json.loads(capsys.readouterr().out)
    assert a["test"]["pvalue"] == pytest.approx(b["test"]["pvalue"])
    assert a["estimates"][0]["value"] == pytest.approx(
        -b["estimates"][0]["value"])


def test_wide_mode_uses_row_wise_pairing(wide_csv, capsys):
    assert main([wide_csv, "--binary", "--paired", "--wide",
                 "--columns", "testA,testB"]) == 0
    out = capsys.readouterr().out
    assert "같은 대상 10쌍" in out
    assert "Cohen's kappa" in out


def test_json_and_csv_formats(long_csv, capsys):
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "statwise/paired-binary/1"
    assert doc["table"]["n_pairs"] == 10

    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--format", "csv"])
    csv_out = capsys.readouterr().out
    assert "paired-binary," in csv_out
    assert "Cohen's kappa" in csv_out


def test_forced_test_choices_are_gated_by_design(long_csv, capsys):
    # McNemar-family tests need --paired
    assert main([long_csv, "--binary", "--value", "responder", "--group",
                 "time", "--binary-test", "mcnemar"]) == 2
    assert "--paired" in capsys.readouterr().err
    # and chi-square/Fisher must not be run on matched pairs
    assert main([long_csv, "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject",
                 "--binary-test", "fisher"]) == 2
    assert "독립 2군" in capsys.readouterr().err


def test_forced_exact_and_cc_are_honoured(long_csv, capsys):
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre",
          "--binary-test", "mcnemar-cc", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["test"]["method"] == "asymptotic"
    assert doc["test"]["statistic"] == pytest.approx((abs(7 - 1) - 1) ** 2 / 8)
    assert any("정확검정" in w for w in doc["warnings"])


def test_missing_id_is_a_clear_error(long_csv, capsys):
    assert main([long_csv, "--binary", "--paired", "--value", "responder",
                 "--group", "time"]) == 2
    assert "--id" in capsys.readouterr().err


def test_aggregate_count_columns_are_refused_for_paired(long_csv, capsys):
    assert main([long_csv, "--binary", "--paired", "--events-col", "e",
                 "--n-col", "n", "--group", "time", "--id", "subject"]) == 2
    assert "짝지어진" in capsys.readouterr().err


def test_reference_is_still_refused_under_paired(long_csv, capsys):
    assert main([long_csv, "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject",
                 "--reference", "pre"]) == 2
    assert "--baseline" in capsys.readouterr().err


def test_equivalence_margin_refused_with_a_reason(long_csv, capsys):
    assert main([long_csv, "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject", "--baseline", "pre",
                 "--equivalence-margin", "0.1"]) == 2
    assert "지원하지 않습니다" in capsys.readouterr().err


def test_unpairable_data_fails_loudly(tmp_path, capsys):
    p = tmp_path / "solo.csv"
    p.write_text("subject,time,responder\nS1,pre,Y\nS2,post,N\n",
                 encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject"]) == 2
    assert "짝을 이루는" in capsys.readouterr().err


def test_three_condition_levels_are_refused(tmp_path, capsys):
    p = tmp_path / "three.csv"
    p.write_text("subject,time,responder\nS1,pre,Y\nS1,mid,N\nS1,post,Y\n",
                 encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject"]) == 2
    assert "2개 수준" in capsys.readouterr().err


def test_event_value_maps_arbitrary_codes(tmp_path, capsys):
    p = tmp_path / "coded.csv"
    p.write_text(
        "subject,time,status\n"
        "S1,pre,관해없음\nS1,post,관해\n"
        "S2,pre,관해없음\nS2,post,관해\n"
        "S3,pre,관해\nS3,post,관해\n"
        "S4,pre,관해없음\nS4,post,관해없음\n", encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "status",
                 "--group", "time", "--id", "subject", "--baseline", "pre",
                 "--event-value", "관해"]) == 0
    out = capsys.readouterr().out
    assert "사건(event) = {관해}" in out


def test_blank_cells_drop_the_whole_pair(tmp_path, capsys):
    p = tmp_path / "blank.csv"
    p.write_text("subject,time,responder\n"
                 "S1,pre,N\nS1,post,Y\n"
                 "S2,pre,N\nS2,post,\n"
                 "S3,pre,Y\nS3,post,Y\n", encoding="utf-8")
    main([str(p), "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["table"]["n_pairs"] == 2
    assert doc["missing"]["pre"] == 1


def test_no_discordant_pairs_reports_but_warns(tmp_path, capsys):
    p = tmp_path / "same.csv"
    rows = ["subject,time,responder"]
    for i in range(8):
        rows += [f"S{i},pre,Y", f"S{i},post,Y"]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "responder",
                 "--group", "time", "--id", "subject", "--baseline", "pre"]) == 0
    out = capsys.readouterr().out
    assert "판단 불가" in out


# --------------------------------------------------------------------------
# regression tests from the 2026-08-06 adversarial review round
# --------------------------------------------------------------------------

def test_wide_baseline_pins_the_sign(wide_csv, capsys):
    main([wide_csv, "--binary", "--paired", "--wide", "--columns",
          "testA,testB", "--format", "json"])
    default = json.loads(capsys.readouterr().out)
    main([wide_csv, "--binary", "--paired", "--wide", "--columns",
          "testA,testB", "--baseline", "testA", "--format", "json"])
    flipped = json.loads(capsys.readouterr().out)
    assert default["conditions"] == {"a": "testA", "b": "testB"}
    assert flipped["conditions"] == {"a": "testB", "b": "testA"}
    assert flipped["estimates"][0]["value"] == pytest.approx(
        -default["estimates"][0]["value"])
    assert flipped["test"]["pvalue"] == pytest.approx(
        default["test"]["pvalue"])


def test_pairs_missing_in_both_conditions_are_still_counted(tmp_path, capsys):
    p = tmp_path / "bothna.csv"
    p.write_text("a,b\nY,Y\nNA,NA\nN,Y\nNA,NA\nY,N\n", encoding="utf-8")
    main([str(p), "--binary", "--paired", "--wide", "--columns", "a,b",
          "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["table"]["n_pairs"] == 3
    # both rows were dropped; neither may vanish from the accounting
    assert doc["missing"]["a"] == 2 and doc["missing"]["b"] == 2
    assert any("결측/해석 불가로 제외" in w for w in doc["warnings"])


def test_long_mode_counts_unpaired_and_unusable_separately(tmp_path, capsys):
    p = tmp_path / "mix.csv"
    p.write_text("subject,time,resp\n"
                 "S1,pre,N\nS1,post,Y\n"
                 "S2,pre,NA\nS2,post,NA\n"      # pair present, both unusable
                 "S3,pre,Y\nS3,post,NA\n"       # pair present, one unusable
                 "S4,pre,Y\n",                  # no partner row at all
                 encoding="utf-8")
    main([str(p), "--binary", "--paired", "--value", "resp", "--group", "time",
          "--id", "subject", "--baseline", "pre", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["table"]["n_pairs"] == 1
    # S2 and S3 lost a pair each; S4 was never paired
    assert doc["missing"]["pre"] == 3
    assert doc["missing"]["post"] == 2


def test_duplicate_row_with_a_blank_does_not_erase_the_real_value(tmp_path,
                                                                  capsys):
    p = tmp_path / "lastblank.csv"
    p.write_text("subject,time,resp\n"
                 "S1,pre,Y\nS1,pre,\nS1,post,N\n"
                 "S2,pre,N\nS2,post,Y\n"
                 "S3,pre,N\nS3,post,Y\n", encoding="utf-8")
    main([str(p), "--binary", "--paired", "--value", "resp", "--group", "time",
          "--id", "subject", "--baseline", "pre", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["table"]["n_pairs"] == 3        # S1 must survive
    assert doc["missing"]["pre"] == 0 and doc["missing"]["post"] == 0


def test_flags_that_would_be_silently_ignored_are_refused(long_csv, capsys):
    base = [long_csv, "--binary", "--paired", "--value", "responder",
            "--group", "time", "--id", "subject"]
    for extra, needle in ((["--no-posthoc"], "--no-posthoc"),
                          (["--correction", "bh"], "--correction"),
                          (["--alpha-norm", "0.1"], "--alpha-norm")):
        assert main(base + extra) == 2
        assert needle in capsys.readouterr().err


def test_alpha_reaches_the_paired_binary_path(long_csv, capsys):
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--alpha", "0.01",
          "--format", "json"])
    strict = json.loads(capsys.readouterr().out)
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre", "--format", "json"])
    normal = json.loads(capsys.readouterr().out)
    assert strict["alpha"] == 0.01
    assert strict["estimates"][0]["ci_low"] < normal["estimates"][0]["ci_low"]
    assert strict["estimates"][0]["ci_high"] > normal["estimates"][0]["ci_high"]


def test_event_is_reaches_the_paired_binary_path(long_csv, capsys):
    main([long_csv, "--binary", "--paired", "--value", "responder", "--group",
          "time", "--id", "subject", "--baseline", "pre",
          "--event-is", "harm", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert any("needed to harm" in e["name"] for e in doc["estimates"])


def test_case_only_label_difference_is_screened(tmp_path, capsys):
    p = tmp_path / "case.csv"
    p.write_text("subject,time,resp\nS1,Pre,Y\nS1,pre,N\n", encoding="utf-8")
    rc = main([str(p), "--binary", "--paired", "--value", "resp", "--group",
               "time", "--id", "subject"])
    combined = capsys.readouterr()
    assert "대소문자/공백만 다른" in (combined.out + combined.err) or rc == 2


def test_duplicate_subject_note_is_emitted(tmp_path, capsys):
    p = tmp_path / "dup.csv"
    p.write_text("subject,time,resp\n"
                 "S1,pre,N\nS1,pre,Y\nS1,post,Y\n"
                 "S2,pre,N\nS2,post,Y\n", encoding="utf-8")
    main([str(p), "--binary", "--paired", "--value", "resp", "--group", "time",
          "--id", "subject", "--baseline", "pre", "--format", "json"])
    doc = json.loads(capsys.readouterr().out)
    assert any("마지막 값만" in w for w in doc["warnings"])


def test_control_characters_in_a_condition_label_do_not_escape(tmp_path,
                                                               capsys):
    p = tmp_path / "evil.csv"
    p.write_text('subject,time,resp\n'
                 'S1,"\x1b[31mRED\x1b[0m",Y\nS1,"A\rB",N\n'
                 'S2,"\x1b[31mRED\x1b[0m",N\nS2,"A\rB",Y\n', encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "resp", "--group",
                 "time", "--id", "subject"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out


def test_output_file_is_written_safely(long_csv, tmp_path, capsys):
    dest = tmp_path / "out.json"
    args = [long_csv, "--binary", "--paired", "--value", "responder",
            "--group", "time", "--id", "subject", "--baseline", "pre",
            "--format", "json", "-o", str(dest)]
    assert main(args) == 0
    assert json.loads(dest.read_text(encoding="utf-8"))["table"]["n_pairs"] == 10
    assert oct(dest.stat().st_mode)[-3:] == "600"
    capsys.readouterr()
    assert main(args) == 2                    # refuses to overwrite
    assert "--overwrite" in capsys.readouterr().err
    assert main(args + ["--overwrite"]) == 0
    # and never onto the input CSV
    capsys.readouterr()
    assert main([a if a != str(dest) else long_csv for a in args]
                + ["--overwrite"]) == 2
    assert "입력 CSV" in capsys.readouterr().err


def test_literal_tab_delimiter_is_accepted(tmp_path, capsys):
    p = tmp_path / "tabs.csv"
    p.write_text("subject\ttime\tresp\nS1\tpre\tN\nS1\tpost\tY\n"
                 "S2\tpre\tN\nS2\tpost\tY\n", encoding="utf-8")
    assert main([str(p), "--binary", "--paired", "--value", "resp", "--group",
                 "time", "--id", "subject", "--delimiter", "\t"]) == 0
